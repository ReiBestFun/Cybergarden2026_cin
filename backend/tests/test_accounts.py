from base64 import urlsafe_b64encode
from contextlib import contextmanager
from hashlib import sha256
from secrets import randbits
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.analyzers import Snapshot
from backend.app.api import auth
from backend.app.config import Settings
from backend.app.integrations import github_oauth
from backend.app.integrations.github_oauth import GitHubIdentity
from backend.app.integrations.repository import RepositorySnapshot
from backend.app.main import create_app
from backend.app.queue_worker import run_next
from backend.app.workflows import execution
from backend.tests.test_api import ORIGIN, analysis_graph, api, create_project, create_workflow, edge, login, node


def test_github_adapter_exchanges_the_code_and_reads_only_the_authenticated_profile(monkeypatch):
    requests = []

    def reply(request):
        requests.append(request)
        if len(requests) == 1:
            return {"access_token": "fixture-access"}
        return {"id": 42, "login": "example", "name": " Display name ", "avatar_url": "https://avatars.githubusercontent.com/u/42"}

    monkeypatch.setattr(github_oauth, "_read_json", reply)
    callback = ORIGIN + "/api/auth/github/callback"
    identity = github_oauth.fetch_identity("fixture-app", "fixture-secret", callback, "fixture-code", "fixture-verifier")
    token, profile = requests
    assert token.full_url == "https://github.com/login/oauth/access_token"
    assert parse_qs(token.data.decode()) == {
        "client_id": ["fixture-app"], "client_secret": ["fixture-secret"], "redirect_uri": [callback],
        "code": ["fixture-code"], "code_verifier": ["fixture-verifier"],
    }
    assert profile.full_url == "https://api.github.com/user" and profile.headers["Authorization"] == "Bearer fixture-access"
    assert identity == GitHubIdentity(42, "example", "Display name", "https://avatars.githubusercontent.com/u/42")


def test_github_callback_is_browser_bound_single_use_and_uses_pkce(api, monkeypatch):
    client, _ = api
    app = client.app
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as browser, TestClient(app, base_url=ORIGIN) as other:
        for path in ("/api/me", "/api/projects", "/api/check-types", "/api/templates", "/api/telegram-destinations"):
            assert browser.get(path).status_code == 401
        start = browser.get("/api/auth/github/start", params={"next": "/results?run=synthetic"}, follow_redirects=False)
        query = parse_qs(urlsplit(start.headers["location"]).query)
        assert query["redirect_uri"] == [ORIGIN + "/api/auth/github/callback"]
        assert start.headers["cache-control"] == "no-store"
        assert "HttpOnly" in start.headers["set-cookie"]
        assert "scope" not in query
        provider_calls = []
        github_id = randbits(48) + 1

        def provider(client_id, secret, redirect_uri, code, verifier):
            challenge = urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            assert query["code_challenge"] == [challenge]
            assert redirect_uri == query["redirect_uri"][0] and code == "fixture-oauth-code"
            provider_calls.append(code)
            return GitHubIdentity(github_id, "sample-login", "Sample User", None)

        monkeypatch.setattr(auth, "fetch_identity", provider)
        callback = "/api/auth/github/callback"
        params = {"code": "fixture-oauth-code", "state": query["state"][0]}
        rejected = other.get(callback, params=params, follow_redirects=False)
        assert rejected.headers["location"] == ORIGIN + "/?auth_error=expired"
        assert not provider_calls
        accepted = browser.get(callback, params=params, follow_redirects=False)
        assert accepted.headers["location"] == ORIGIN + "/results?run=synthetic"
        assert "HttpOnly" in accepted.headers["set-cookie"] and "SameSite=lax" in accepted.headers["set-cookie"]
        original_id = browser.get("/api/me").json()["id"]
        old_cookie = browser.cookies.get("cyberflow_session")
        browser.get(callback, params=params, follow_redirects=False)
        assert len(provider_calls) == 1
        assert other.get("/api/me").status_code == 401
        assert browser.patch("/api/me", json={"display_name": "Private Name", "timezone": "Europe/Moscow"},
                             headers={"Origin": "https://outside.example"}).status_code == 403
        browser.patch("/api/me", json={"display_name": "Private Name", "timezone": "Europe/Moscow"}).raise_for_status()

        response = login(browser, monkeypatch, github_id, login_name="renamed-login", next_path="//outside.example")
        assert response.headers["location"] == ORIGIN + "/"
        profile = browser.get("/api/me").json()
        assert profile["id"] == original_id and profile["github_login"] == "renamed-login"
        assert profile["display_name"] == "Private Name" and profile["timezone"] == "Europe/Moscow"
        other.cookies.set("cyberflow_session", old_cookie)
        assert other.get("/api/me").status_code == 401
        active_cookie = browser.cookies.get("cyberflow_session")
        assert browser.post("/api/auth/logout").status_code == 204
        other.cookies.set("cyberflow_session", active_cookie)
        assert other.get("/api/me").status_code == 401


def test_https_session_cookie_and_invalid_storage_key(api, monkeypatch):
    client, _ = api
    origin = "https://cyberflow.example.test"
    config = client.app.state.settings
    app = create_app(config.model_copy(update={"app_base_url": origin}))
    with TestClient(app, base_url=origin, headers={"Origin": origin}) as secure_browser:
        callback = login(secure_browser, monkeypatch, randbits(48) + 1)
        assert "Secure" in callback.headers["set-cookie"]
        assert secure_browser.get("/api/me").status_code == 200
    mismatch = create_app(Settings(
        database_url=config.database_url, app_base_url=ORIGIN,
        data_encryption_key=urlsafe_b64encode(b"\xff" * 32).decode(),
        github_client_id="fixture-app", github_client_secret="fixture-secret",
    ))
    with TestClient(mismatch, base_url=ORIGIN) as browser:
        result = browser.get("/api/auth/github/start", follow_redirects=False)
        assert result.status_code == 503 and result.json()["error"]["code"] == "STORAGE_KEY_MISMATCH"


def test_personal_projects_templates_and_reports_are_isolated_and_encrypted(api, monkeypatch):
    owner, sessions = api
    project_id = create_project(owner)
    duplicate = owner.post("/api/projects", json={"name": "duplicate", "repository_url": "https://github.com/tbagirov/TRAINING"})
    assert duplicate.status_code == 200 and duplicate.json()["id"] == project_id
    graph = analysis_graph()
    graph["nodes"].append(node("telegram", "notify_telegram", {"recipients": ["-1003456789"]}))
    graph["edges"].append(edge("merge-telegram", "merge", "bundle", "telegram", "report"))
    workflow_id = create_workflow(owner, project_id, graph)
    run_id = owner.post(f"/api/workflows/{workflow_id}/runs").json()["id"]

    @contextmanager
    def prepare(*args, **kwargs):
        yield RepositorySnapshot(Snapshot("a" * 40, {"PrivateSource.kt": 'val servicePassword = "fixture-test-928"'}), None)

    monkeypatch.setattr(execution, "prepare_repository", prepare)
    assert run_next(sessions, owner.app.state.settings, owner.app.state.allowed_repositories)
    report = owner.get(f"/api/runs/{run_id}/findings").json()
    assert report["items"][0]["path"] == "PrivateSource.kt" and report["items"][0]["evidence"] == "***"
    created = owner.post("/api/templates", json={
        "workflow_id": workflow_id, "name": "Personal template", "description": "Personal template memo",
    })
    assert created.status_code == 201, created.text
    template = created.json()
    assert "recipients" not in template["graph"]["nodes"][-1]["config"]
    template_id = template["id"]

    with TestClient(owner.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as other:
        login(other, monkeypatch, randbits(48) + 1)
        assert other.get("/api/projects").json()["total"] == 0
        other_project = create_project(other)
        assert other_project != project_id
        assert other.get(f"/api/projects/{project_id}").status_code == 404
        assert other.get(f"/api/projects/{project_id}/workflows").status_code == 404
        assert other.get(f"/api/projects/{project_id}/runs").status_code == 404
        assert other.get(f"/api/workflows/{workflow_id}").status_code == 404
        assert other.get(f"/api/workflows/{workflow_id}/schedule").status_code == 404
        assert other.post(f"/api/workflows/{workflow_id}/ci-token", json={}).status_code == 404
        assert other.post(f"/api/workflows/{workflow_id}/runs").status_code == 404
        for suffix in ("", "/findings", "/report", "/export", "/events"):
            assert other.get(f"/api/runs/{run_id}{suffix}").status_code == 404
        assert other.get("/api/templates").json()["total"] == 0
        assert other.get(f"/api/templates/{template_id}").status_code == 404
        assert other.delete(f"/api/templates/{template_id}").status_code == 404
        assert owner.post(f"/api/templates/{template_id}/instantiate", json={"project_id": other_project}).status_code == 404
        assert other.post("/api/templates", json={"workflow_id": workflow_id, "name": "copy"}).status_code == 404

    instantiated = owner.post(f"/api/templates/{template_id}/instantiate", json={"project_id": project_id})
    assert instantiated.status_code == 201 and instantiated.json()["graph"] == template["graph"]
    assert instantiated.json()["id"] != workflow_id

    with sessions() as db:
        queries = (
            ("SELECT github_login, display_name, timezone FROM users WHERE id = :id", owner.get("/api/me").json()["id"]),
            ("SELECT name, repository_url, default_ref FROM projects WHERE id = :id", project_id),
            ("SELECT name, graph_json FROM workflows WHERE id = :id", workflow_id),
            ("SELECT name, description, graph_json FROM workflow_templates WHERE id = :id", template_id),
            ("SELECT source_snapshot, graph_snapshot FROM scan_runs WHERE id = :id", run_id),
            ("SELECT title, path, metadata FROM findings WHERE run_id = :id", run_id),
        )
        stored = [db.execute(text(statement), {"id": UUID(ident)}).one() for statement, ident in queries]
        assert all(isinstance(value, str) and value.startswith("Y2Yx") for row in stored for value in row)
        assert all(marker not in repr(stored) for marker in (
            "test-user", "github.com", "-1003456789", "Personal template", "PrivateSource.kt",
        ))

    assert owner.delete(f"/api/templates/{template_id}").status_code == 204
    assert owner.get(f"/api/workflows/{instantiated.json()['id']}").status_code == 200
