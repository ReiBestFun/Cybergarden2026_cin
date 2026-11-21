import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from threading import Barrier, Event, Lock
from typing import get_args
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import make_url

from backend.app.analyzers.models import Coverage, ScanError, ScanResult, Snapshot, Warning
from backend.app.api import runs as runs_api
from backend.app.config import Settings
from backend.app.credentials import resolve_credential
from backend.app.db.models import Credential, FindingRow, NodeRun, ScanRun, Schedule, Workflow
from backend.app.integrations.github_actions import security_workflow
from backend.app.integrations.repository import (
    RepositorySnapshot, allowed_repository_urls, is_repository_allowed, prepare_repository, repository_allowlist,
)
from backend.app.main import create_app
from backend.app import notification_worker
from backend.app.queue_worker import run_next
from backend.app.reports import NodeResult
from backend.app.schemas.contracts import Graph
from backend.app.schemas.credentials import CredentialKind
from backend.app.workflows import execution
from backend.app.workflows.persistence import ClaimLost, RunProgress
from backend.app.workflows.queue import claim_next, enqueue_due_schedules, expire_stale_runs
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


def ci_graph(threshold="CRITICAL"):
    graph = analysis_graph("ci_trigger")
    graph["nodes"].append(node("gate", "ci_gate", {"threshold": threshold}))
    graph["edges"].append(edge("merge-gate", "merge", "bundle", "gate", "bundle"))
    return graph


def test_github_actions_template_and_gate_config():
    import yaml

    workflow_id = UUID(int=1)
    for mode, runner in [("local", ["self-hosted", "Windows", "cyberflow"]), ("hosted", "ubuntu-latest")]:
        workflow = yaml.load(security_workflow(workflow_id, "release/demo", mode), Loader=yaml.BaseLoader)
        assert workflow["on"]["push"]["branches"] == ["release/demo"]
        assert workflow["permissions"] == {}
        job = workflow["jobs"]["cyberflow"]
        assert job["runs-on"] == runner
        assert job["steps"][0]["env"]["CYBERFLOW_TOKEN"] == "${{ secrets.CYBERFLOW_TOKEN }}"
    assert validate_graph(Graph.model_validate(ci_graph()), expected_trigger="ci_trigger") == []
    assert validate_graph(Graph.model_validate(ci_graph("LOW")))[0].code == "INVALID_CONFIG"
    assert validate_graph(Graph.model_validate(analysis_graph("ci_trigger")))[0].code == "CI_GATE_REQUIRED"


@pytest.mark.parametrize(("threshold", "coverage", "outcome"), [
    ("CRITICAL", "complete", "passed"), ("HIGH", "complete", "blocked"),
    ("CRITICAL", "partial", "error"), ("HIGH", "partial", "blocked"), ("HIGH", "failed", "error"),
])
def test_ci_webhook_exact_sha_and_verdict(api, monkeypatch, tmp_path, threshold, coverage, outcome):
    client, sessions = api
    graph = ci_graph(threshold)
    graph["nodes"].append(node("if", "condition", {"field": "ci.decision", "operator": "eq", "value": "blocked"}))
    graph["edges"].append(edge("gate-if", "gate", "bundle", "if", "bundle"))
    workflow_id = create_workflow(client, create_project(client), graph)
    settings = client.app.state.settings
    settings.ci_workflow_tokens = Settings(ci_workflow_tokens={workflow_id: "ci-test-token"}).ci_workflow_tokens
    headers = {"Authorization": "Bearer ci-test-token"}
    endpoint = f"/api/hooks/{workflow_id}"
    sha = "a" * 40
    payload = {"repository_url": "https://github.com/TBagirov/training", "commit_sha": sha, "request_id": "123-1"}
    assert client.get(f"/api/workflows/{workflow_id}/security.yml").status_code == 200
    assert client.post(endpoint, json=payload).status_code == 401
    assert client.post(endpoint, headers=headers, json={**payload, "commit_sha": "main"}).status_code == 422
    assert client.post(endpoint, headers=headers, json={**payload, "repository_url": "https://github.com/other/source"}).status_code == 403

    response = client.post(endpoint, headers=headers, json=payload)
    assert response.status_code == 202, response.text
    assert client.post(endpoint, headers=headers, json=payload).json() == response.json()
    assert client.post(endpoint, headers=headers, json={**payload, "commit_sha": "b" * 40}).status_code == 409
    status_url = f"{endpoint}/runs/{response.json()['id']}"
    assert client.get(status_url).status_code == 401
    assert client.get(status_url, headers=headers).json()["gate"] is None

    (tmp_path / "Service.kt").write_text('val importPassword = "synthetic-ci-not-a-real-password"\n', encoding="utf-8")

    @contextmanager
    def prepare(url, ref, **kwargs):
        assert url == payload["repository_url"] and ref == sha
        if coverage == "failed":
            raise ScanError(Warning("REPOSITORY_FETCH_FAILED", "Тестовый сбой."))
        yield RepositorySnapshot(Snapshot.from_directory(tmp_path, sha), None)

    monkeypatch.setattr(execution, "prepare_repository", prepare)
    if coverage == "partial":
        run_scan = execution.run_scan

        def partial_scan(*args, **kwargs):
            scanned = run_scan(*args, **kwargs)
            result = scanned.result
            return replace(scanned, result=replace(result, coverage=replace(result.coverage, status="partial")))

        monkeypatch.setattr(execution, "run_scan", partial_scan)
    assert run_next(sessions, settings, None)
    status = client.get(status_url, headers=headers).json()
    assert status["gate"]["decision"] == outcome
    execution_nodes = {item["node_id"]: item for item in client.get(f"/api/runs/{response.json()['id']}").json()["nodes"]}
    if coverage != "failed":
        assert status["commit_sha"] == sha
        assert status["report_url"].endswith(response.json()["id"])
        assert execution_nodes["gate"]["active_output_ports"] == ["bundle"]
        assert execution_nodes["if"]["active_output_ports"] == ["true" if outcome == "blocked" else "false"]
        incoming = execution_nodes["if"]["input_preview"]["inputs"][0]
        assert incoming["edge_id"] == "gate-if" and incoming["data"]["decision"] == outcome
    else:
        assert execution_nodes["if"]["status"] == "skipped"


def test_allow_any_repository_switch():
    strict = repository_allowlist(["https://github.com/TBagirov/training"], allow_any=False)
    assert is_repository_allowed("https://github.com/tbagirov/TRAINING", strict)
    assert not is_repository_allowed("https://github.com/other/project", strict)
    assert is_repository_allowed("https://github.com/other/project", repository_allowlist([], allow_any=True))


def test_template_is_portable_and_schedule_stays_disabled(api):
    client, _ = api
    project_id, target_id = create_project(client), create_project(client)
    graph = analysis_graph("schedule_trigger")
    graph["nodes"].append(node("email", "notify_email", {
        "credential_id": str(UUID(int=5)), "recipients": ["user@example.org"], "subject": "Private subject", "attach_json": False,
    }))
    graph["edges"].append(edge("deliver", "merge", "bundle", "email", "report"))
    workflow_id = create_workflow(client, project_id, graph)
    schedule_url = f"/api/workflows/{workflow_id}/schedule"
    assert client.put(schedule_url, json={"enabled": True, "time": "08:30", "timezone": "UTC"}).status_code == 200
    published = client.post("/api/templates", json={"workflow_id": workflow_id, "name": "Daily checks", "description": "Example"})
    assert published.status_code == 201, published.text
    template = published.json()
    assert template["graph"]["nodes"][-1]["config"] == {"attach_json": False}
    assert template["schedule"]["time"] == "08:30"
    assert any(item["id"] == template["id"] for item in client.get("/api/templates").json()["items"])
    assert client.get(f"/api/workflows/{workflow_id}").json()["graph"] == graph
    copy = client.post(f"/api/templates/{template['id']}/copy", json={"project_id": target_id, "name": "My copy"})
    assert copy.status_code == 201, copy.text
    workflow = copy.json()
    assert workflow["id"] != workflow_id and workflow["project_id"] == target_id
    assert workflow["name"] == "My copy" and workflow["graph"] == template["graph"]
    schedule = client.get(f"/api/workflows/{workflow['id']}/schedule").json()
    assert schedule["enabled"] is False and schedule["time"] == "08:30" and schedule["next_run_at"] is None


def test_run_event_snapshot_updates_after_postgres_notification(api, monkeypatch):
    client, sessions = api
    workflow_id = create_workflow(client, create_project(client), analysis_graph())
    run_id = UUID(client.post(f"/api/workflows/{workflow_id}/runs", json={}).json()["id"])
    with sessions() as db:
        initial = runs_api.get_run(run_id, db)
    dsn = make_url(client.app.state.settings.database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    listening = Event()
    get_run = runs_api.get_run

    def observed_read(*args):
        run = get_run(*args)
        listening.set()
        return run

    monkeypatch.setattr(runs_api, "get_run", observed_read)
    events = runs_api._run_events(initial, sessions, dsn)
    assert '"status":"queued"' in next(events)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(next, events)
        assert listening.wait(4)
        with sessions.begin() as db:
            db.get(ScanRun, run_id).status = "running"
        runtime = json.loads(pending.result(timeout=4).split("data: ", 1)[1])
        assert runtime["status"] == "running"
    events.close()
    with sessions.begin() as db:
        db.get(ScanRun, run_id).status = "succeeded"
    terminal = client.get(f"/api/runs/{run_id}/events")
    assert terminal.headers["content-type"].startswith("text/event-stream")
    assert '"status":"succeeded"' in terminal.text


def test_ci_editor_run_and_token_rotation(api, monkeypatch, tmp_path):
    client, sessions = api
    workflow_id = create_workflow(client, create_project(client), ci_graph())
    editor = f"/api/workflows/{workflow_id}"
    hook = f"/api/hooks/{workflow_id}"
    sha = "a" * 40
    response = client.post(f"{editor}/ci-test", json={"commit_sha": sha})
    assert response.status_code == 202
    run_id = response.json()["id"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["trigger"] == "ci" and run["source_snapshot"]["ref"] == sha
    (tmp_path / "Service.kt").write_text('val serviceName = "demo"\n', encoding="utf-8")

    @contextmanager
    def prepare(url, ref, **kwargs):
        yield RepositorySnapshot(Snapshot.from_directory(tmp_path, ref), None)

    monkeypatch.setattr(execution, "prepare_repository", prepare)
    assert run_next(sessions, client.app.state.settings, None)
    token_response = client.post(f"{editor}/ci-token", json={})
    assert token_response.headers["cache-control"] == "no-store"
    old_headers = {"Authorization": f"Bearer {token_response.json()['token']}"}
    status_endpoint = f"{hook}/runs/{run_id}"
    assert client.get(status_endpoint, headers=old_headers).json()["gate"]["decision"] == "passed"
    token = client.post(f"{editor}/ci-token", json={}).json()["token"]
    assert client.get(status_endpoint, headers=old_headers).status_code == 401
    assert client.get(status_endpoint, headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_project_credentials_are_encrypted_and_scoped(api):
    client, sessions = api
    project_id = UUID(create_project(client))
    other_project_id = UUID(create_project(client))
    route = f"/api/projects/{project_id}/credentials"
    settings = client.app.state.settings
    settings.credentials_encryption_key = None
    body = {"kind": "telegram", "name": "Example", "value": {"secret": "synthetic-credential"}}
    assert client.post(route, json=body).status_code == 503
    settings.credentials_encryption_key = Settings(credentials_encryption_key=Fernet.generate_key().decode()).credentials_encryption_key

    for kind in get_args(CredentialKind):
        value = {"secret": "synthetic-credential"}
        if kind == "ssh_key":
            value = {"secret": "synthetic-credential\nkey-body\n", "passphrase": "synthetic-passphrase"}
        response = client.post(route, json={**body, "kind": kind, "value": value})
        assert response.status_code == 201, response.text
        assert "synthetic-credential" not in response.text and "ciphertext" not in response.json()
        credential_id = UUID(response.json()["id"])
        with sessions() as db:
            row = db.get(Credential, credential_id)
            assert "synthetic-credential" not in row.ciphertext
            resolved = resolve_credential(db, settings, project_id, credential_id, kind)
            assert resolved.secret.get_secret_value() == value["secret"]
            if kind == "ssh_key":
                assert resolved.passphrase.get_secret_value() == value["passphrase"]
            with pytest.raises(ScanError, match="недоступны"):
                resolve_credential(db, settings, other_project_id, credential_id, kind)
            with pytest.raises(ScanError, match="расшифровать"):
                resolve_credential(db, Settings(credentials_encryption_key=Fernet.generate_key().decode()), project_id, credential_id, kind)
    assert "synthetic-credential" not in client.get(route).text
    assert client.patch(f"/api/projects/{other_project_id}/credentials/{credential_id}", json={"active": False}).status_code == 404
    assert client.patch(f"{route}/{credential_id}", json={"active": False}).json()["active"] is False
    with sessions() as db, pytest.raises(ScanError, match="недоступны"):
        resolve_credential(db, settings, project_id, credential_id, kind)


@pytest.mark.parametrize(("kind", "node_type", "recipient"), [
    ("telegram", "notify_telegram", "@test_channel"), ("smtp", "notify_email", "user@example.org"),
])
def test_worker_reads_current_credential_without_exposing_it(api, monkeypatch, tmp_path, kind, node_type, recipient):
    client, sessions = api
    project_id = create_project(client)
    settings = client.app.state.settings
    settings.credentials_encryption_key = Settings(credentials_encryption_key=Fernet.generate_key().decode()).credentials_encryption_key
    settings.notify_telegram_recipients = [recipient] if kind == "telegram" else []
    settings.notify_email_recipients = [recipient] if kind == "smtp" else []
    route = f"/api/projects/{project_id}/credentials"
    value = {"secret": "synthetic-old"}
    if kind == "smtp":
        value["username"] = "demo-user"
    credential = client.post(route, json={"name": "Test credential", "kind": kind, "value": value}).json()
    credential_id = credential["id"]
    graph = analysis_graph()
    graph["nodes"].append(node("notify", node_type, {"recipients": [recipient], "credential_id": credential_id}))
    graph["edges"].append(edge("merge-notify", "merge", "bundle", "notify", "report"))
    workflow = create_workflow(client, project_id, graph)
    run_id = client.post(f"/api/workflows/{workflow}/runs", json={}).json()["id"]
    assert client.patch(f"{route}/{credential_id}", json={"value": {**value, "secret": "synthetic-new"}}).status_code == 200
    (tmp_path / "Service.kt").write_text('val serviceName = "demo"\n', encoding="utf-8")

    @contextmanager
    def prepare(*args, **kwargs):
        yield RepositorySnapshot(Snapshot.from_directory(tmp_path, "a" * 40), None)

    class ChannelStub:
        def send_text(self, actual, text):
            assert actual == recipient
            return "message-id"

        def send_attachment(self, actual, attachment):
            assert actual == recipient
            return "attachment-id"

        def send_one(self, actual, message, **kwargs):
            assert actual == recipient
            return "email-id"

    def telegram(token, **kwargs):
        assert token == "synthetic-new"
        return ChannelStub()

    def smtp(host, *, username, password, **kwargs):
        assert (username, password) == ("demo-user", "synthetic-new")
        return ChannelStub()

    monkeypatch.setattr(execution, "prepare_repository", prepare)
    monkeypatch.setattr(notification_worker, "TelegramChannel", telegram)
    monkeypatch.setattr(notification_worker, "EmailChannel", smtp)
    assert run_next(sessions, settings, None)
    assert client.get(f"/api/runs/{run_id}").json()["delivery"]["notify"]["status"] == "queued"
    assert notification_worker.process_next(sessions, settings)
    if kind == "telegram":
        assert notification_worker.process_next(sessions, settings)
    result = client.get(f"/api/runs/{run_id}")
    assert result.json()["status"] == "succeeded"
    assert result.json()["delivery"]["notify"]["status"] == "accepted"
    assert '"status":"accepted"' in client.get(f"/api/runs/{run_id}/events?wait_for_delivery=true").text
    assert "synthetic-" not in result.text and "demo-user" not in result.text


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
    # Tests use the strict allowlist regardless of a developer's local .env.
    app = create_app(Settings(database_url=url, allow_any_repository=False))
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
    draft_schedule = client.put(f"{prefix}/schedule", json={
        "enabled": False, "time": "14:00", "timezone": "Europe/Moscow",
    })
    assert draft_schedule.status_code == 200, draft_schedule.text
    saved_schedule = client.get(f"{prefix}/schedule").json()
    assert saved_schedule["time"] == "14:00" and saved_schedule["next_run_at"] is None
    assert saved_schedule["enabled"] is False
    # A scheduled workflow can also be started by hand ("run now").
    assert client.post(f"{prefix}/runs").status_code == 202
    first = client.put(f"{prefix}/schedule", json={"enabled": True, "time": "09:00", "timezone": "Europe/Moscow"})
    assert first.status_code == 200, first.text
    settings = first.json()
    assert settings["time"] == "09:00" and settings["frequency"] == "daily" and settings["next_run_at"]
    assert len(settings["upcoming"]) == 3
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
    settings = Settings(allow_any_repository=False)
    assert run_next(sessions, settings, allowed_repository_urls(settings.allowed_repositories))

    base = f"/api/runs/{run_id}"
    detail = client.get(base).json()
    assert detail["status"] == "partial"
    assert detail["commit_sha"] == "b" * 40
    steps = {step["node_id"]: step for step in detail["nodes"]}
    assert steps["scan"]["status"] == "succeeded"
    assert steps["sca"]["error"]["code"] == "SCA_CATALOG_MISSING"
    assert "SCA_CATALOG_PATH" in steps["sca"]["error"]["hint"]
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


def test_password_hashing_node_persists_only_fast_password_digests(api, monkeypatch):
    client, sessions = api
    catalog = client.get("/api/check-types").json()["items"]
    scanner = next(item for item in catalog if item["type"] == "scan_password_hashing")
    graph = analysis_graph()
    graph["nodes"][2] = node("scan", scanner["type"])
    source = """fun store(password: String) = DigestUtils.md5Hex(password)
fun verify(rawPassword: String, stored: String) = DigestUtils.sha1Hex(rawPassword) == stored
fun hash(password: String): ByteArray {
    val digest = MessageDigest.getInstance("SHA-256")
    return digest.digest(password.toByteArray())
}
fun checksum(file: File) = MessageDigest.getInstance("SHA-256").digest(file.readBytes())
fun secure(password: String) = BCryptPasswordEncoder().encode(password)
fun matches(password: String, stored: String) = Argon2PasswordEncoder.defaultsForSpringSecurity_v5_8().matches(password, stored)
fun salted(password: String, salt: String) = DigestUtils.sha256Hex(salt + password)
fun storeInParts(password: String, file: File) {
    val md = MessageDigest.getInstance("SHA-1")
    md.update(password.toByteArray())
    save(md.digest())
    md.update(file.readBytes())
    checksum(md.digest())
}
"""

    @contextmanager
    def prepared(*args, **kwargs):
        yield RepositorySnapshot(Snapshot("b" * 40, {"Auth.kt": source}), None)

    monkeypatch.setattr(execution, "prepare_repository", prepared)
    run_id = enqueue_for_worker(api, graph)
    assert run_next(sessions, client.app.state.settings, None)

    base = f"/api/runs/{run_id}"
    history = client.get(base).json()
    assert history["status"] == "succeeded"
    step = next(item for item in history["nodes"] if item["node_id"] == "scan")
    assert step["type"] == scanner["type"]
    assert step["output_preview"]["finding_count"] == 5
    findings = client.get(f"{base}/findings").json()["items"]
    assert [(f["metadata"]["algorithm"], f["line"]) for f in findings] == [
        ("MD5", 1), ("SHA-1", 2), ("SHA-256", 5), ("SHA-256", 10), ("SHA-1", 14),
    ]
    exported = client.get(f"{base}/export").json()["findings"]
    assert [(f["path"], f["line"]) for f in exported] == [
        ("Auth.kt", 1), ("Auth.kt", 2), ("Auth.kt", 5), ("Auth.kt", 10), ("Auth.kt", 14),
    ]
    assert all("Argon2id" in f["remediation"] for f in exported)


def test_sensitive_logging_node_reports_only_exposed_values(api, monkeypatch):
    client, sessions = api
    catalog = client.get("/api/check-types").json()["items"]
    scanner = next(item for item in catalog if item["type"] == "scan_sensitive_logging")
    graph = analysis_graph()
    graph["nodes"][2] = node("scan", scanner["type"])
    source = '''fun audit(request: Request, tokenId: String) {
    val password = "synthetic-private-canary"
    logger.warn("password={}", password)
    logger.debug { "session=${request.accessToken}" }
    val header = request.getHeader("Authorization")
    log.error("header=" + header)
    logger.info("password={}", mask(password))
    logger.info("token id=$tokenId")
    logger.info("header name={}", HttpHeaders.AUTHORIZATION)
    logger.info { "redacted=${redact(request.accessToken)}" }
    auditService.info(password)
    val accessToken = issueJwt()
    logger.info("received $accessToken")
    val masked = mask(accessToken)
    logger.info("received $masked")
    log.warn("header {}", request.headers[HttpHeaders.AUTHORIZATION])
    val sink = LoggerFactory.getLogger("audit")
    sink.error("credential {}", request.password)
    log.warn("{}", request.headers[HttpHeaders.Authorization] ?: "missing")
    log.info("{}", HttpHeaders.Authorization)
    val sessionToken = "prefix-$tokenId"
    log.trace("{}", sessionToken)
    val userPassword = "***"
    log.info("{}", userPassword)
}'''

    @contextmanager
    def prepared(*args, **kwargs):
        yield RepositorySnapshot(Snapshot("c" * 40, {"src/Auth.kt": source}), None)

    monkeypatch.setattr(execution, "prepare_repository", prepared)
    run_id = enqueue_for_worker(api, graph)
    assert run_next(sessions, client.app.state.settings, None)
    base = f"/api/runs/{run_id}"
    assert client.get(base).json()["status"] == "succeeded"
    findings = client.get(f"{base}/findings").json()["items"]
    assert [(f["path"], f["line"]) for f in findings] == [
        ("src/Auth.kt", 3), ("src/Auth.kt", 4), ("src/Auth.kt", 6),
        ("src/Auth.kt", 13), ("src/Auth.kt", 16), ("src/Auth.kt", 18),
        ("src/Auth.kt", 19), ("src/Auth.kt", 22),
    ]
    assert all(f["rule_id"] == "sast.sensitive-logging" and f["severity"] == "high" for f in findings)
    report = client.get(f"{base}/export").json()["findings"]
    assert [f["line"] for f in report] == [3, 4, 6, 13, 16, 18, 19, 22]
    assert all(f["remediation"] for f in report)
    for url in (base, f"{base}/findings", f"{base}/export", f"{base}/export?format=markdown"):
        assert "synthetic-private-canary" not in client.get(url).text


@pytest.mark.parametrize(("mode", "total", "coverage", "branch"), [
    ("AND", 0, "complete", "true"), ("AND", 0, "partial", "false"),
    ("AND", 1, "complete", "false"), ("AND", 1, "partial", "false"),
    ("OR", 0, "complete", "true"), ("OR", 0, "partial", "true"),
    ("OR", 1, "complete", "true"), ("OR", 1, "partial", "false"),
    (None, 0, "complete", "true"), (None, 1, "complete", "false"),
])
def test_condition_routes_only_the_selected_branch(api, monkeypatch, mode, total, coverage, branch):
    client, sessions = api
    graph = analysis_graph()
    count = {"field": "summary.total", "operator": "eq", "value": total}
    status = {"field": "coverage.status", "operator": "matches", "value": coverage}
    config = {"mode": mode, "conditions": [count, status]} if mode else count
    graph["nodes"].append(node("decision", "condition", config))
    graph["edges"].append(edge("input", "merge", "bundle", "decision", "bundle"))
    for port in ("true", "false"):
        graph["nodes"].append(node(port, "condition", count))
        graph["edges"].append(edge(port, "decision", port, port, "bundle"))

    @contextmanager
    def prepared(*args, **kwargs):
        yield RepositorySnapshot(Snapshot("d" * 40, {"Sample.kt": "val count = 1"}), None)

    monkeypatch.setattr(execution, "prepare_repository", prepared)
    run_id = enqueue_for_worker(api, graph)
    assert run_next(sessions, client.app.state.settings, None)
    detail = client.get(f"/api/runs/{run_id}").json()
    steps = {step["node_id"]: step for step in detail["nodes"]}
    assert steps["decision"]["output_preview"]["branch"] == branch
    assert steps["decision"]["active_output_ports"] == [branch]
    assert steps[branch]["status"] == "succeeded"
    other = "false" if branch == "true" else "true"
    assert steps[other]["error"]["code"] == "INACTIVE_BRANCH"
    assert steps[other]["status"] == "skipped"


@pytest.mark.parametrize(("change", "code"), [
    ({"mode": "XOR"}, "INVALID_CONFIG"),
    ({"conditions": []}, "INVALID_CONFIG"),
    ({"conditions": [{"field": "summary.total", "operator": "eq", "value": 0}]}, "INVALID_CONFIG"),
    ({"field": "summary.total"}, "INVALID_CONFIG"),
    ({"second": {"field": "summary.total", "operator": "gt", "value": "3"}}, "INVALID_CONFIG"),
    ({"second": {"field": "ci.decision", "operator": "matches", "value": "passed"}}, "INVALID_CONFIG"),
    ({"second": {"field": "coverage.status", "operator": "matches", "value": "["}}, "INVALID_REGEX"),
])
def test_api_rejects_invalid_condition_groups(api, change, code):
    client, _ = api
    workflow = create_workflow(client, create_project(client))
    graph = analysis_graph()
    count = {"field": "summary.total", "operator": "eq", "value": 0}
    config = {"mode": "OR", "conditions": [count, count]}
    if "second" in change:
        config["conditions"][1] = change["second"]
    else:
        config.update(change)
    graph["nodes"].append(node("decision", "condition", config))
    graph["edges"].append(edge("input", "merge", "bundle", "decision", "bundle"))
    url = f"/api/workflows/{workflow}"
    result = client.post(f"{url}/validate", json={"graph": graph}).json()
    assert result["valid"] is False
    assert [(issue["node_id"], issue["code"]) for issue in result["issues"]] == [("decision", code)]
    assert client.patch(url, json={"graph": graph}).status_code == 422


@pytest.mark.parametrize("with_sast", [False, True])
def test_spring_config_node_exports_each_finding_once(api, monkeypatch, with_sast):
    client, sessions = api
    catalog = client.get("/api/check-types").json()["items"]
    scanner = next(item for item in catalog if item["type"] == "scan_spring_config")
    graph = analysis_graph()
    graph["nodes"][2] = node("scan", scanner["type"])
    if with_sast:
        graph["nodes"].append(node("sast", "scan_sast"))
        graph["edges"] += [
            edge("source-sast", "source", "snapshot", "sast", "snapshot"),
            edge("sast-merge", "sast", "result", "merge", "results"),
        ]
    snapshot = Snapshot("8" * 40, {
        "Security.kt": """class Security {
    fun chain(http: HttpSecurity) = http.authorizeHttpRequests { rules ->
        rules.requestMatchers("/actuator/**").permitAll()
        rules.anyRequest().authenticated()
    }
}""",
        "application.yml": "management.endpoints.web.exposure.include: [health, env]\nserver.error.include-stacktrace: always",
    })

    @contextmanager
    def prepared(*args, **kwargs):
        yield RepositorySnapshot(snapshot, None)

    monkeypatch.setattr(execution, "prepare_repository", prepared)
    run_id = enqueue_for_worker(api, graph)
    assert run_next(sessions, client.app.state.settings, None)
    base = f"/api/runs/{run_id}"
    assert client.get(base).json()["status"] == "succeeded"
    assert client.get(f"{base}/report").json()["summary"]["total"] == 2
    findings = client.get(f"{base}/export").json()["findings"]
    assert sorted((f["class"], f["severity"], f["path"], f["line"]) for f in findings) == [
        ("actuator-exposure", "HIGH", "Security.kt", 3),
        ("error-stacktrace", "MEDIUM", "application.yml", 2),
    ]


def test_queue_worker_reports_repository_failure(api, monkeypatch):
    client, sessions = api

    def unavailable(*args, **kwargs):
        raise ScanError(Warning("REPOSITORY_FETCH_FAILED", "Учебный источник недоступен."))

    monkeypatch.setattr(execution, "prepare_repository", unavailable)
    run_id = enqueue_for_worker(api, analysis_graph())
    settings = Settings(allow_any_repository=False)
    assert run_next(sessions, settings, allowed_repository_urls(settings.allowed_repositories))
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "failed"
    assert detail["error"]["code"] == "REPOSITORY_FETCH_FAILED"
    statuses = {node["node_id"]: node["status"] for node in detail["nodes"]}
    assert statuses == {"entry": "succeeded", "source": "failed", "scan": "skipped", "merge": "failed"}


def test_queue_worker_runs_ready_scans_in_parallel_and_waits_for_them(api, monkeypatch):
    client, sessions = api
    graph = analysis_graph()
    graph["nodes"] += [node("sca", "scan_sca"), node("sast", "scan_sast")]
    graph["edges"] += [
        edge("snapshot-sca", "source", "snapshot", "sca", "snapshot"),
        edge("snapshot-sast", "source", "snapshot", "sast", "snapshot"),
        edge("sca-results", "sca", "result", "merge", "results"),
        edge("sast-results", "sast", "result", "merge", "results"),
    ]
    started = []
    active = 0
    peak = 0
    mutex = Lock()
    first_two = Barrier(2)

    @contextmanager
    def prepared(*args, **kwargs):
        yield RepositorySnapshot(Snapshot("b" * 40, {}), None)

    def inspect(category, snapshot, catalog, timeout, **kwargs):
        nonlocal active, peak
        with mutex:
            started.append(category)
            ordinal = len(started)
            active += 1
            peak = max(peak, active)
        if ordinal <= 2:
            first_two.wait(timeout=5)
        with mutex:
            active -= 1
        return NodeResult(category, "succeeded", ScanResult(category, (), (), Coverage("complete", category, 1, 0)))

    monkeypatch.setattr(execution, "prepare_repository", prepared)
    monkeypatch.setattr(execution, "run_scan", inspect)
    run_id = enqueue_for_worker(api, graph)
    settings = Settings(scan_parallelism=2)
    assert run_next(sessions, settings, allowed_repository_urls(settings.allowed_repositories))
    assert set(started) == {"secrets", "sca", "sast"}
    assert peak == 2 and active == 0
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "succeeded"
    checks = client.get(f"/api/runs/{run_id}/report").json()["coverage"]["checks"]
    assert set(checks) == {"scan", "sca", "sast"}


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


def test_schedule_frequencies_and_preview(api):
    client, _ = api
    project = create_project(client)
    workflow = create_workflow(client, project, analysis_graph("schedule_trigger"))
    prefix = f"/api/workflows/{workflow}"
    weekly = client.put(f"{prefix}/schedule", json={
        "enabled": True, "frequency": "weekly", "time": "18:00", "weekdays": [4], "timezone": "Europe/Moscow",
    }).json()
    assert weekly["weekdays"] == [4] and weekly["day_of_month"] is None
    assert all(datetime.fromisoformat(slot).astimezone(ZoneInfo("Europe/Moscow")).weekday() == 4 for slot in weekly["upcoming"])
    cron = client.put(f"{prefix}/schedule", json={
        "enabled": True, "frequency": "cron", "cron": "0 9 * * 1-5", "timezone": "UTC",
    }).json()
    assert cron["cron"] == "0 9 * * 1-5" and cron["time"] is None and cron["weekdays"] is None
    bad = client.put(f"{prefix}/schedule", json={"enabled": True, "frequency": "weekly", "time": "09:00", "timezone": "UTC"})
    assert bad.status_code == 422 and bad.json()["error"]["field"] == "weekdays"
    preview = client.post("/api/schedules/preview", json={"frequency": "monthly", "time": "03:00", "day_of_month": 1, "timezone": "UTC"})
    assert preview.status_code == 200 and len(preview.json()["next_runs"]) == 5
    assert client.post("/api/schedules/preview", json={"frequency": "cron", "cron": "x", "timezone": "UTC"}).json()["error"]["code"] == "INVALID_CRON"


def test_worker_enqueues_due_schedule_once(api):
    client, sessions = api
    project = create_project(client)
    workflow = create_workflow(client, project, analysis_graph("schedule_trigger"))
    client.put(f"/api/workflows/{workflow}/schedule", json={
        "enabled": True, "time": "09:00", "timezone": "UTC",
    }).raise_for_status()
    with sessions.begin() as db:
        schedule = db.scalar(select(Schedule).where(Schedule.workflow_id == UUID(workflow)))
        slot = schedule.next_run_at
    later = slot + timedelta(days=3, minutes=1)  # the worker was down for several slots
    # The test database is shared, so count only this workflow's runs.
    for _ in range(2):
        with sessions.begin() as db:
            enqueue_due_schedules(db, later)
    with sessions.begin() as db:
        schedule = db.scalar(select(Schedule).where(Schedule.workflow_id == UUID(workflow)))
        assert schedule.next_run_at > later and schedule.last_enqueued_at == later
        runs = db.scalars(select(ScanRun).where(ScanRun.workflow_id == UUID(workflow))).all()
        assert [(run.trigger, run.scheduled_for, run.status) for run in runs] == [("schedule", slot, "queued")]

    # A broken graph is reported on the schedule instead of creating a failing run.
    with sessions.begin() as db:
        schedule = db.scalar(select(Schedule).where(Schedule.workflow_id == UUID(workflow)))
        schedule.next_run_at = later
        db.get(Workflow, UUID(workflow)).graph_json = analysis_graph("manual_trigger")
    with sessions.begin() as db:
        enqueue_due_schedules(db, later + timedelta(minutes=1))
        assert len(db.scalars(select(ScanRun).where(ScanRun.workflow_id == UUID(workflow))).all()) == 1
        schedule = db.scalar(select(Schedule).where(Schedule.workflow_id == UUID(workflow)))
        assert schedule.last_error["code"] == "WRONG_TRIGGER"
