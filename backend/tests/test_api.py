import os
from contextlib import contextmanager
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.analyzers.models import ScanError, Snapshot, Warning
from backend.app.config import Settings
from backend.app.db.models import FindingRow, NodeRun, ScanRun
from backend.app.integrations.repository import RepositorySnapshot, allowed_repository_urls, prepare_repository
from backend.app.main import create_app
from backend.app.queue_worker import run_next
from backend.app.schemas.contracts import Graph
from backend.app.workflows import execution
from backend.app.workflows.persistence import ClaimLost, RunProgress
from backend.app.workflows.queue import claim_next, expire_stale_runs
from backend.app.workflows.scheduling import next_daily_slot
from backend.app.workflows.validation import validate_graph


def node(node_id, kind, config=None):
    return {"id": node_id, "type": kind, "position": {"x": 0, "y": 0}, "config": config or {}}


def edge(edge_id, source, source_port, target, target_port):
    return {"id": edge_id, "source": source, "source_port": source_port, "target": target, "target_port": target_port}


def analysis_graph(trigger="manual_trigger"):
    return {
        "nodes": [
            node("entry", trigger), node("source", "repository"),
            node("scan", "scan_secrets"), node("merge", "merge_findings"),
        ],
        "edges": [
            edge("trigger-source", "entry", "params", "source", "params"),
            edge("source-scan", "source", "snapshot", "scan", "snapshot"),
            edge("scan-merge", "scan", "result", "merge", "results"),
        ],
    }


def test_graph_validation_types_ports_and_cycles():
    graph = analysis_graph()
    assert validate_graph(Graph.model_validate(graph), expected_trigger="manual_trigger") == []
    graph["edges"][0]["source_port"] = "missing"
    graph["nodes"].append(node("check", "condition", {"field": "summary.total", "operator": "gt", "value": "3"}))
    graph["edges"].append(edge("cycle", "check", "true", "check", "bundle"))
    issues = validate_graph(Graph.model_validate(graph), expected_trigger="schedule_trigger")
    codes = {issue.code for issue in issues}
    assert {"UNKNOWN_PORT", "WRONG_TRIGGER", "GRAPH_CYCLE", "MISSING_INPUT", "UNREACHABLE_NODE", "INVALID_CONFIG"} <= codes


def test_daily_schedule_skips_nonexistent_local_time():
    now = datetime(2026, 3, 28, 15, 0, tzinfo=UTC)
    assert next_daily_slot(time(2, 30), ZoneInfo("Europe/Berlin"), now) == datetime(2026, 3, 30, 0, 30, tzinfo=UTC)


@pytest.fixture
def api(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    app = create_app(Settings(database_url=url))
    with TestClient(app) as client:
        yield client, app.state.session_factory


def create_project(client):
    response = client.post("/api/projects", json={
        "name": "Учебный проект", "repository_url": "https://github.com/TBagirov/training.git",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_workflow(client, project, graph=None):
    payload = {"name": "Проверка"}
    if graph is not None:
        payload["graph"] = graph
    response = client.post(f"/api/projects/{project}/workflows", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_project_workflow_and_run_are_persisted(api):
    client, _ = api
    assert client.get("/api/health").json() == {"status": "ok", "db": "ok"}
    assert client.post("/api/projects", json={
        "name": "Unknown", "repository_url": "https://github.com/invalid/unknown",
    }).json()["error"]["code"] == "REPOSITORY_NOT_ALLOWED"
    project = create_project(client)
    assert client.get(f"/api/projects/{project}").json()["repository_url"] == "https://github.com/TBagirov/training"
    assert client.get("/api/projects?limit=1").json()["limit"] == 1

    workflow = create_workflow(client, project)
    assert client.get(f"/api/projects/{project}/workflows").json()["total"] == 1
    prefix = f"/api/workflows/{workflow}"
    assert client.post(f"{prefix}/validate", json={}).json()["valid"] is False
    graph = analysis_graph()
    saved = client.patch(prefix, json={"graph": graph})
    assert saved.status_code == 200, saved.text
    assert saved.json()["graph"] == graph
    assert client.post(f"{prefix}/validate").json() == {"valid": True, "issues": []}
    queued = client.post(f"{prefix}/runs", json={"ref": "main"})
    assert queued.status_code == 202, queued.text
    run_id = queued.json()["id"]
    client.patch(prefix, json={"graph": {"nodes": [], "edges": []}}).raise_for_status()
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["graph_snapshot"] == graph
    assert detail["source_snapshot"]["ref"] == "main"
    assert [item["status"] for item in detail["nodes"]] == ["pending"] * 4
    assert client.get(f"/api/projects/{project}/runs?status=queued").json()["total"] == 1
    assert client.get(f"/api/projects/{project}/runs?status=failed").json()["total"] == 0
    assert client.get(f"/api/runs/{run_id}/export").json()["error"]["code"] == "REPORT_NOT_READY"


def test_schedule_and_catalog_for_frontend(api):
    client, _ = api
    catalog = client.get("/api/check-types").json()["items"]
    condition = next(item for item in catalog if item["type"] == "condition")
    assert [port["id"] for port in condition["outputs"]] == ["true", "false"]
    cors = client.options("/api/projects", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
    })
    assert cors.headers["access-control-allow-origin"] == "http://localhost:5173"
    project = create_project(client)
    workflow = create_workflow(client, project, analysis_graph("schedule_trigger"))
    prefix = f"/api/workflows/{workflow}"
    assert not client.get(f"{prefix}/schedule").json()["enabled"]
    assert client.post(f"{prefix}/runs").json()["error"]["code"] == "WRONG_TRIGGER"
    first = client.put(f"{prefix}/schedule", json={"enabled": True, "time": "09:00", "timezone": "Europe/Moscow"})
    assert first.status_code == 200, first.text
    settings = first.json()
    assert settings["time"] == "09:00" and settings["next_run_at"]
    assert client.get(f"{prefix}/schedule").json() == settings
    assert client.put(f"{prefix}/schedule", json={"enabled": False}).json()["next_run_at"] is None
    client.put(f"{prefix}/schedule", json={"enabled": True, "time": "09:00", "timezone": "Europe/Moscow"}).raise_for_status()
    client.patch(prefix, json={"graph": {"nodes": [], "edges": []}}).raise_for_status()
    result = client.get(f"{prefix}/schedule").json()
    assert not result["enabled"] and result["last_error"]["code"] == "TRIGGER_COUNT"


def test_persisted_findings_are_filtered_and_exported(api):
    client, sessions = api
    project = create_project(client)
    workflow = create_workflow(client, project, analysis_graph())
    run_id = client.post(f"/api/workflows/{workflow}/runs").json()["id"]
    with sessions() as db:
        run = db.get(ScanRun, run_id)
        scan = db.scalar(select(NodeRun).where(NodeRun.run_id == run.id, NodeRun.node_id == "scan"))
        scan.status = "succeeded"
        run.status = "succeeded"
        run.commit_sha = "a" * 40
        db.add(FindingRow(
            run_id=run.id, node_run_id=scan.id, rule_id="secret.password", category="secrets", severity="high",
            title="Пароль", description="Обнаружено встроенное значение.", remediation="Вынесите значение из файла.",
            path="Sample.kt", line=1, evidence="***", details={"class": "password"},
        ))
        db.commit()
    base = f"/api/runs/{run_id}"
    page = client.get(f"{base}/findings?category=secrets&severity=high").json()
    assert page["total"] == 1
    assert page["items"][0]["metadata"] == {"class": "password"}
    assert page["items"][0]["evidence"] == "***"
    assert client.get(f"{base}/findings?category=sca").json()["total"] == 0
    assert client.get(f"{base}/report").json()["summary"]["high_count"] == 1
    export = client.get(f"{base}/export")
    assert export.json()["findings"][0]["category"] == "SECRET"
    assert "attachment" in export.headers["content-disposition"]
    assert "| SECRET | HIGH |" in client.get(f"{base}/export?format=markdown").text


def enqueue_for_worker(api, graph, *, priority=0):
    client, sessions = api
    project = create_project(client)
    workflow = create_workflow(client, project, graph)
    response = client.post(f"/api/workflows/{workflow}/runs")
    assert response.status_code == 202, response.text
    run_id = UUID(response.json()["id"])
    with sessions.begin() as db:
        db.get(ScanRun, run_id).created_at = datetime(2000, 1, 1, tzinfo=UTC) + timedelta(seconds=priority)
    return run_id


def test_queue_worker_runs_analysis_and_keeps_results_on_failed_sca(api, monkeypatch, tmp_path):
    client, sessions = api
    graph = analysis_graph()
    graph["nodes"] += [
        node("sca", "scan_sca"),
        node("if", "condition", {"field": "coverage.status", "operator": "matches", "value": "^part"}),
        node("branch", "condition", {"field": "summary.total", "operator": "eq", "value": 0}),
    ]
    graph["edges"] += [
        edge("snapshot-sca", "source", "snapshot", "sca", "snapshot"),
        edge("sca-results", "sca", "result", "merge", "results"),
        edge("bundle", "merge", "bundle", "if", "bundle"),
        edge("false-branch", "if", "false", "branch", "bundle"),
    ]
    (tmp_path / "Sample.kt").write_text('val servicePassword = "fixture-pass-98"\n', encoding="utf-8")
    (tmp_path / "build.gradle.kts").write_text("dependencies {}", encoding="utf-8")
    snapshot = Snapshot.from_directory(tmp_path, "b" * 40)

    @contextmanager
    def prepared(*args, **kwargs):
        yield RepositorySnapshot(snapshot, None)

    monkeypatch.setattr(execution, "prepare_repository", prepared)
    run_id = enqueue_for_worker(api, graph)
    settings = Settings()
    assert run_next(sessions, settings, allowed_repository_urls(settings.allowed_repositories))

    base = f"/api/runs/{run_id}"
    detail = client.get(base).json()
    assert detail["status"] == "partial"
    assert detail["commit_sha"] == "b" * 40
    steps = {step["node_id"]: step for step in detail["nodes"]}
    assert steps["scan"]["status"] == "succeeded"
    assert steps["sca"]["error"]["code"] == "INVALID_CATALOG"
    assert steps["if"]["active_output_ports"] == ["true"]
    assert steps["branch"]["status"] == "skipped" and steps["branch"]["error"]["code"] == "INACTIVE_BRANCH"
    findings = client.get(f"{base}/findings").json()["items"]
    assert [(item["category"], item["evidence"]) for item in findings] == [("secrets", "***")]
    report = client.get(f"{base}/report").json()
    assert report["summary"]["total"] == 1
    assert report["coverage"]["status"] == "partial"
    markdown = client.get(f"{base}/export?format=markdown").text
    assert "**SECRET** — полное покрытие" in markdown
    assert "**SCA** — Для SCA требуется справочник Maven." in markdown
    for url in (base, f"{base}/findings", f"{base}/report", f"{base}/export?format=markdown"):
        assert "fixture-pass-98" not in client.get(url).text


def test_queue_worker_reports_repository_failure(api, monkeypatch):
    client, sessions = api

    def unavailable(*args, **kwargs):
        raise ScanError(Warning("REPOSITORY_FETCH_FAILED", "Учебный источник недоступен."))

    monkeypatch.setattr(execution, "prepare_repository", unavailable)
    run_id = enqueue_for_worker(api, analysis_graph())
    settings = Settings()
    assert run_next(sessions, settings, allowed_repository_urls(settings.allowed_repositories))
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "failed"
    assert detail["error"]["code"] == "REPOSITORY_FETCH_FAILED"
    statuses = {node["node_id"]: node["status"] for node in detail["nodes"]}
    assert statuses == {"entry": "succeeded", "source": "failed", "scan": "skipped", "merge": "failed"}


def test_queue_claims_are_exclusive_and_expired_workers_cannot_write(api):
    client, sessions = api
    first = enqueue_for_worker(api, analysis_graph(), priority=1)
    second = enqueue_for_worker(api, analysis_graph(), priority=2)
    with sessions.begin() as first_transaction:
        claim = claim_next(first_transaction)
        assert claim.id == first
        with sessions.begin() as second_transaction:
            second_claim = claim_next(second_transaction)
            assert second_claim.id == second
    stale = datetime.now(UTC) - timedelta(minutes=10)
    with sessions.begin() as db:
        db.get(ScanRun, first).heartbeat_at = stale
    with sessions.begin() as db:
        assert expire_stale_runs(db, stale + timedelta(seconds=1)) == 1
    detail = client.get(f"/api/runs/{first}").json()
    assert detail["status"] == "failed" and detail["error"]["code"] == "WORKER_EXPIRED"
    progress = RunProgress(sessions, claim)
    assert progress.heartbeat() is False
    with pytest.raises(ClaimLost):
        progress.finish_node("entry", "succeeded", ports=["params"])
    RunProgress(sessions, second_claim).fail(Warning("TEST_FINISHED", "Проверка захвата задания завершена."))


def test_repository_loader_rejects_sources_outside_allowlist():
    allowed = allowed_repository_urls(["https://github.com/TBagirov/training.git"])
    with pytest.raises(ScanError) as rejected, prepare_repository(
        "https://github.com/other/training", "main", allowed=allowed, timeout=1, catalog_path=None,
    ):
        pytest.fail("An unapproved source must be rejected before Git runs")
    assert rejected.value.warning.code == "REPOSITORY_NOT_ALLOWED"
