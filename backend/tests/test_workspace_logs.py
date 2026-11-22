import json
from datetime import UTC, datetime, timedelta
from secrets import randbits
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from backend.app.log_maintenance import maintain_logs
from backend.tests.test_api import ORIGIN, TEST_KEY, analysis_graph, api, create_project, create_workflow, login


def test_team_audit_records_actors_and_denied_actions(api, monkeypatch):
    owner, _ = api
    team = owner.post("/api/workspaces", json={"name": "Аудит"}).raise_for_status().json()["id"]
    base = f"/api/workspaces/{team}"
    invitation = owner.post(base + "/invitations", json={"role": "member"}).raise_for_status().json()
    with TestClient(owner.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as member:
        login(member, monkeypatch, randbits(48) + 1)
        assert member.get(base + "/logs").status_code == 404
        member.post(base + "/join", json={"token": invitation["token"]}).raise_for_status()
        assert member.post(base + "/invitations", json={"role": "owner"}).status_code == 403
        assert member.get(base + "/logs").status_code == 403
        actor = member.get("/api/me").json()["id"]
    response = owner.get(base + "/logs").raise_for_status()
    assert invitation["token"] not in response.text
    rows = response.json()["items"]
    assert [(r["action"], r["result"]) for r in rows] == [
        ("invitation.issue", "denied"), ("invitation.redeem", "succeeded"), ("invitation.issue", "succeeded"),
    ]
    assert rows[0]["actor_id"] == actor
    assert all(r["workspace_id"] == team and r["at"] for r in rows)


def test_destinations_and_retention_are_scoped_by_kind_and_workspace(api, tmp_path):

    client, sessions = api
    settings = client.app.state.settings
    (tmp_path / ".cyberflow-destination").write_text("archive\n", encoding="ascii")
    settings.log_destinations = {"archive": tmp_path}
    teams = [client.post("/api/workspaces", json={"name": name}).json()["id"] for name in ("A", "B")]
    paths = [f"/api/workspaces/{team}/logs" for team in teams]
    first = client.get(paths[0] + "/policy").json()
    assert first["destinations"] == ["postgresql", "archive"] and str(tmp_path) not in str(first)
    assert (first["policy"]["diagnostic_days"], first["policy"]["audit_days"]) == (14, 90)
    policy = {**first["policy"], "destination": "archive", "audit_days": 3, "diagnostic_days": 1}
    client.put(paths[0] + "/policy", json=policy).raise_for_status()
    saved = client.get(paths[0] + "/policy").json()["policy"]
    assert saved == {**policy, "version": 1}
    assert client.put(paths[0] + "/policy", json=policy).status_code == 409
    assert client.put(paths[0] + "/policy", json={**policy, "destination": "https://invalid.test"}).status_code == 422

    for team in teams:
        project = client.post("/api/projects", json={"name": "Проект", "workspace_id": team,
                              "repository_url": "https://github.com/TBagirov/training"}).json()["id"]
        wf = create_workflow(client, project, analysis_graph())
        client.post(f"/api/workflows/{wf}/runs").raise_for_status()
        client.get(f"/api/workspaces/{team}/logs/export").raise_for_status()
    assert client.get(paths[0], params={"kind": "diagnostic"}).json()["total"] == 1
    now = datetime.now(UTC)
    maintain_logs(sessions, settings, now)
    archived = [json.loads(path.read_text()) for path in tmp_path.rglob("*.json")]
    assert {row["workspace_id"] for row in archived} == {teams[0]}
    assert any(row["action"] == "run.enqueue" for row in archived)
    maintain_logs(sessions, settings, now + timedelta(days=2))
    assert client.get(paths[0], params={"kind": "diagnostic"}).json()["total"] == 0
    assert client.get(paths[0], params={"kind": "audit"}).json()["total"] > 0
    assert client.get(paths[1], params={"kind": "diagnostic"}).json()["total"] == 1
    maintain_logs(sessions, settings, now + timedelta(days=4))
    assert client.get(paths[0]).json()["total"] == 0
    assert not list(tmp_path.rglob("*.json"))
    assert client.get(paths[1], params={"kind": "audit"}).json()["total"] > 0


def test_independent_permissions_and_redaction(api, monkeypatch, caplog, capfd):
    client, sessions = api
    team = client.post("/api/workspaces", json={"name": "Команда"}).json()["id"]
    team_path = f"/api/workspaces/{team}"
    path = team_path + "/logs"
    separate = client.post("/api/workspaces", json={"name": "Соседняя команда"}).json()["id"]
    other = f"/api/workspaces/{separate}/logs"
    private = client.get("/api/me").json()["id"]
    project = create_project(client)
    workflow = create_workflow(client, project, analysis_graph())
    run = client.post(f"/api/workflows/{workflow}/runs").json()["id"]
    private_path = f"/api/workspaces/{private}/logs"
    assert client.get(private_path + "?run_id=" + run).json()["total"] == 1
    client.post(f"/api/projects/{project}/move", json={"workspace_id": team}).raise_for_status()
    assert client.get(private_path + "/export?run_id=" + run).status_code == 404
    assert all(row["run_id"] != run for row in client.get(private_path + "/export").json())
    secret = "synthetic-marker-" + str(randbits(64))
    client.app.state.settings.credentials_encryption_key = SecretStr(TEST_KEY)
    credentials = f"/api/projects/{project}/credentials"
    credential = client.post(credentials, json={"kind": "llm", "name": "Ключ модели", "value": {"secret": secret}}).raise_for_status().json()
    client.patch(credentials + "/" + credential["id"], json={"active": False}).raise_for_status()
    client.put(team_path + "/llm", json={"provider": "ollama"}).raise_for_status()
    client.post("/api/templates", json={"name": "Общий шаблон", "workspace_id": team, "workflow_id": workflow}).raise_for_status()
    issued = client.post(team_path + "/mcp-tokens", json={"name": secret}).raise_for_status().json()
    for _ in range(2):
        client.delete(team_path + "/mcp-tokens/" + issued["id"]).raise_for_status()
    invitation = client.post(team_path + "/invitations", json={"role": "member"}).json()
    with TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as member:
        login(member, monkeypatch, randbits(48) + 1)
        member.post(team_path + "/join", json={"token": invitation["token"]}).raise_for_status()
        member_id = member.get("/api/me").json()["id"]
        client.patch(team_path + "/members/" + member_id, json={"role": "member"}).raise_for_status()
        for permission in ("view", "export", "manage"):
            current = client.get(path + "/policy").raise_for_status().json()["policy"]
            rights = {"member_" + right: right == permission for right in ("view", "export", "manage")}
            policy = client.put(path + "/policy", json={**current, **rights}).raise_for_status().json()["policy"]
            assert member.get(path).status_code == (200 if permission == "view" else 403)
            assert member.get(path + "/export").status_code == (200 if permission == "export" else 403)
            assert member.put(path + "/policy", json=policy).status_code == (200 if permission == "manage" else 403)
            assert all(member.get(other + suffix).status_code == 404 for suffix in ("", "/export", "/policy"))
            assert member.put(other + "/policy", json=policy).status_code == 404
        assert member.put(path + "/policy", json={**policy, "member_export": True}).status_code == 403
        assert member.get(path + "/export").status_code == 403
        own_project = create_project(member)
        foreign_wf = create_workflow(member, own_project, analysis_graph())
        foreign_run = member.post(f"/api/workflows/{foreign_wf}/runs").json()["id"]
        assert client.get(path + "?run_id=" + foreign_run).status_code == 404
        assert client.get(path + "/export?run_id=" + foreign_run).status_code == 404
    exported = client.get(path + "/export").raise_for_status()
    assert {"credential.create", "credential.update", "llm.configure", "member.role", "template.publish",
            "mcp.issue", "mcp.revoke", "logs.policy", "logs.export"} <= {
        row["action"] for row in exported.json() if row["result"] == "succeeded"
    }
    assert any(row["action"] == "mcp.revoke" and row["result"] == "unchanged" for row in exported.json())
    with sessions() as db:
        stored = str(db.scalars(text("SELECT row_to_json(entry)::text FROM workspace_logs AS entry WHERE workspace_id=:team"),
                                {"team": UUID(team)}).all())
    captured = capfd.readouterr()
    console = caplog.text + captured.out + captured.err
    for marker in (secret, issued["token"], invitation["token"]):
        assert marker not in stored and marker not in console and marker not in exported.text


def test_unmounted_archive_and_one_broken_workspace_do_not_lose_records(api, tmp_path):
    client, sessions = api
    mount = tmp_path / "mount"
    mount.mkdir()
    (mount / ".cyberflow-destination").write_text("shared", encoding="ascii")
    settings = client.app.state.settings
    settings.log_destinations = {"shared": mount}
    teams = [client.post("/api/workspaces", json={"name": name}).json()["id"] for name in ("Первая", "Вторая")]
    for team in teams:
        policy = f"/api/workspaces/{team}/logs/policy"
        defaults = client.get(policy).json()["policy"]
        client.put(policy, json={**defaults, "destination": "shared", "audit_days": 1}).raise_for_status()

    # A non-directory at the first team's path must not block the next team.
    obstacle = mount / teams[0]
    obstacle.touch()
    now = datetime.now(UTC)
    maintain_logs(sessions, settings, now)
    assert len(list((mount / teams[1]).glob("*.json"))) == 1
    obstacle.unlink()
    maintain_logs(sessions, settings, now + timedelta(minutes=6))
    assert len(list((mount / teams[0]).glob("*.json"))) == 1

    offline = tmp_path / "detached"
    mount.rename(offline)
    mount.mkdir()
    (mount / ".cyberflow-destination").write_text("wrong-volume", encoding="ascii")
    expiry = now + timedelta(days=2)
    maintain_logs(sessions, settings, expiry)
    assert len(list(offline.rglob("*.json"))) == 2
    assert not list(mount.rglob("*.json"))
    with sessions() as db:
        assert db.scalar(text("SELECT count(*) FROM workspace_logs WHERE destination='shared'")) == 2
    mount.rename(tmp_path / "empty-mountpoint")
    offline.rename(mount)
    maintain_logs(sessions, settings, expiry)
    assert not list(mount.rglob("*.json"))
    with sessions() as db:
        assert db.scalar(text("SELECT count(*) FROM workspace_logs WHERE destination='shared'")) == 0
