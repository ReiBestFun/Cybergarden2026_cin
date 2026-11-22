import json
import os
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from secrets import randbits
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.db.models import ScanRun
from backend.app.queue_worker import run_next
from backend.tests.test_analyzers import matches
from backend.tests.test_api import ORIGIN, analysis_graph, api, ci_graph, create_project, create_workflow, edge, login, node


def zipped(name, content):
    data = BytesIO()
    with ZipFile(data, "w", ZIP_DEFLATED) as archive:
        archive.writestr(name, content)
    return data.getvalue()


def test_project_archive_runs_without_git_and_preserves_queued_input(api, monkeypatch):
    client, sessions = api
    project = create_project(client)
    url = f"/api/projects/{project}/archives"
    headers = {"content-type": "application/zip"}
    content = zipped("src/Sample.kt", 'val servicePassword = "fixture-pass-98"')
    digest = sha256(content).hexdigest()
    assert client.post(url, content=content, headers=headers).json() == {"sha256": digest}
    assert client.post(url, content=content, headers=headers).json() == {"sha256": digest}
    with sessions() as db:
        [stored] = db.scalars(text("select content from project_archives where project_id = :id"), {"id": project}).all()
        assert bytes(stored).startswith(b"cf1:") and bytes(stored) != content

    graph = analysis_graph()
    graph["nodes"][1] = node("source", "local_archive", {"archive_sha256": digest, "filename": "source.zip"})
    workflow = create_workflow(client, project, graph)
    run = client.post(f"/api/workflows/{workflow}/runs").raise_for_status().json()["id"]
    template = client.post("/api/templates", json={"name": "ZIP", "workflow_id": workflow}).raise_for_status().json()
    assert template["graph"]["nodes"][1]["config"] == {}

    other = client.post(url, content=zipped("README.md", "another revision"), headers=headers).json()["sha256"]
    graph["nodes"][1]["config"] = {"archive_sha256": other}
    client.patch(f"/api/workflows/{workflow}", json={"graph": graph}).raise_for_status()
    with sessions.begin() as db:
        db.get(ScanRun, UUID(run)).created_at = datetime(1990, 1, 1, tzinfo=UTC)
    def no_git(*args, **kwargs):
        pytest.fail("An archive run must use its uploaded bytes")

    monkeypatch.setattr("backend.app.workflows.execution.prepare_repository", no_git)
    assert run_next(sessions, client.app.state.settings, frozenset())
    result = client.get(f"/api/runs/{run}").raise_for_status().json()
    assert (result["status"], result["commit_sha"], result["source_snapshot"]) == ("succeeded", None, {"archive_sha256": digest})
    findings = client.get(f"/api/runs/{run}/findings").json()["items"]
    assert [(item["path"], item["evidence"]) for item in findings] == [("src/Sample.kt", "***")]
    assert digest in client.get(f"/api/runs/{run}/export?format=markdown").text


def test_archive_validation_and_project_scope(api, monkeypatch):
    client, _ = api
    project = create_project(client)
    url = f"/api/projects/{project}/archives"
    headers = {"content-type": "application/zip"}
    assert client.post(url, content=b"invalid", headers=headers).status_code == 422
    assert client.post(url, content=zipped("../escape.txt", ""), headers=headers).status_code == 422
    assert client.post(url, content=zipped("large.txt", " " * (33 * 1024 * 1024)), headers=headers).status_code == 422
    assert client.post(url, content=b"0" * (8 * 1024 * 1024 + 1), headers=headers).status_code == 413
    digest = client.post(url, content=zipped("README.md", "example"), headers=headers).json()["sha256"]

    team = client.post("/api/workspaces", json={"name": "Другая команда"}).json()["id"]
    target = client.post("/api/projects", json={"name": "Другой проект", "workspace_id": team,
                         "repository_url": "https://github.com/TBagirov/training"}).json()["id"]
    invitation = client.post(f"/api/workspaces/{team}/invitations", json={"role": "member"}).json()["token"]
    with TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as reader:
        login(reader, monkeypatch, randbits(48) + 1)
        upload = f"/api/projects/{target}/archives"
        assert reader.post(upload, content=b"zip", headers=headers).status_code == 404
        reader.post(f"/api/workspaces/{team}/join", json={"token": invitation}).raise_for_status()
        assert reader.post(upload, content=b"zip", headers=headers).status_code == 403

    graph = ci_graph()
    graph["nodes"][1] = node("source", "local_archive", {"archive_sha256": digest})
    rejected = client.post(f"/api/projects/{target}/workflows", json={"name": "ZIP", "graph": graph})
    assert rejected.json()["error"]["code"] == "ARCHIVE_NOT_FOUND"
    workflow = create_workflow(client, project, graph)
    assert client.post(f"/api/workflows/{workflow}/runs").json()["error"]["code"] == "CI_REPOSITORY_REQUIRED"
    graph["nodes"][1] = node("source", "local_archive")
    workflow = create_workflow(client, project, graph)
    assert client.post(f"/api/workflows/{workflow}/runs").json()["error"]["code"] == "ARCHIVE_REQUIRED"


@pytest.mark.skipif(not os.environ.get("TRAINING_DATASET"), reason="set TRAINING_DATASET to the training checkout")
def test_training_zip_matches_reference_without_fetch(api):
    client, sessions = api
    root = Path(os.environ["TRAINING_DATASET"])
    data = BytesIO()
    with ZipFile(data, "w", ZIP_DEFLATED) as archive:
        for folder in ("training-project", "vulnerability-database"):
            for path in (root / folder).rglob("*"):
                if path.is_file():
                    archive.write(path, "training-main/" + path.relative_to(root).as_posix())
    project = create_project(client)
    digest = client.post(f"/api/projects/{project}/archives", content=data.getvalue(),
                         headers={"content-type": "application/zip"}).raise_for_status().json()["sha256"]
    graph = analysis_graph()
    graph["nodes"][1] = node("source", "local_archive", {"archive_sha256": digest})
    for kind in ("sca", "sast"):
        graph["nodes"].append(node(kind, "scan_" + kind))
        graph["edges"].extend([edge("to-" + kind, "source", "snapshot", kind, "snapshot"),
                               edge("from-" + kind, kind, "result", "merge", "results")])
    workflow = create_workflow(client, project, graph)
    run = client.post(f"/api/workflows/{workflow}/runs").raise_for_status().json()["id"]
    with sessions.begin() as db:
        db.get(ScanRun, UUID(run)).created_at = datetime(1990, 1, 1, tzinfo=UTC)
    settings = client.app.state.settings.model_copy(update={"sca_catalog_path": None})
    assert run_next(sessions, settings, frozenset())
    actual = client.get(f"/api/runs/{run}/export").raise_for_status().json()["findings"]
    expected = json.loads((root / "ground-truth/training-findings.json").read_text())
    assert Counter(item["category"] for item in actual) == {"SECRET": 5, "SCA": 5, "SAST": 5}
    assert all(sum(matches(item, target) for item in actual) == 1 for target in expected["findings"])
    assert not any(matches(item, control) for item in actual for control in expected["controls"])
