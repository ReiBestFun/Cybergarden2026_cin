import json
from datetime import UTC, datetime
from secrets import randbits
from uuid import UUID

import httpx
from fastapi.testclient import TestClient

from backend.app import llm
from backend.app.db.models import ScanRun
from backend.app.integrations.repository import allowed_repository_urls
from backend.app.queue_worker import run_next
from backend.tests.test_api import ORIGIN, analysis_graph, api, create_workflow, edge, login, node
from backend.tests.test_notifications import _prepare_sources


def test_owner_enables_verified_model_only_for_their_workspace(api, monkeypatch):
    client, _ = api
    team = client.post("/api/workspaces", json={"name": "Модели команды"}).json()["id"]
    path = f"/api/workspaces/{team}/llm"
    # Existing server-wide external settings must not enable sharing for a new workspace.
    client.app.state.settings.llm_provider = "openai_compatible"
    client.app.state.settings.llm_base_url = "https://legacy-provider.example/v1"
    assert client.get(path).json()["provider"] == "ollama"
    calls = []
    available = False

    def provider(request):
        calls.append(request.url.host)
        payload = json.loads(request.content)
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": draft}})
        assert str(request.url) == "https://api.openai.com/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer fixture-llm-secret"
        assert payload["response_format"]["type"] == "json_object"
        if not available:
            return httpx.Response(401)
        content = draft if payload["messages"][-1]["content"] == description else '{"ok":true}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    description = "Проверь проект на секреты"
    draft = '{"plan":{"name":"Проверка","trigger":"manual","scanners":["secrets"]}}'
    transport_client = httpx.Client
    monkeypatch.setattr(llm.httpx, "Client", lambda **kw: transport_client(transport=httpx.MockTransport(provider), **kw))
    profile = {"provider": "openai", "model": "gpt-4o-mini", "api_key": "fixture-llm-secret"}
    assert client.put(path, json=profile).status_code == 502
    assert client.get(path).json()["provider"] == "ollama"
    available = True
    enabled = client.put(path, json=profile)
    assert enabled.status_code == 200 and enabled.json()["provider"] == "openai"
    assert "fixture-llm-secret" not in enabled.text + client.get(path).text
    task = {"description": description, "timezone": "UTC"}
    generate = "/api/workflows/generate"
    assert client.post(generate, params={"workspace_id": team}, json=task).json()["graph"]["nodes"]
    assert calls[-1] == "api.openai.com"
    second = client.post("/api/workspaces", json={"name": "Другая команда"}).json()["id"]
    assert client.post(generate, params={"workspace_id": second}, json=task).status_code == 200
    assert calls[-1] == "127.0.0.1"

    invitation = client.post(f"/api/workspaces/{team}/invitations", json={"role": "member"}).json()["token"]
    with TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as other:
        login(other, monkeypatch, randbits(48) + 1)
        count = len(calls)
        assert other.post(generate, params={"workspace_id": team}, json=task).status_code == 404
        other.post(f"/api/workspaces/{team}/join", json={"token": invitation}).raise_for_status()
        assert other.get(path).status_code == 403
        assert other.put(path, json=profile).status_code == 403
        assert len(calls) == count
        assert other.post(generate, params={"workspace_id": team}, json=task).status_code == 200
    assert client.put(path, json={"provider": "ollama"}).json()["provider"] == "ollama"
    assert client.post(generate, params={"workspace_id": team}, json=task).status_code == 200
    assert calls[-1] == "127.0.0.1"


def test_ai_report_uses_project_profile_and_keeps_findings_after_model_error(api, monkeypatch):
    client, sessions = api
    settings = client.app.state.settings
    team = client.post("/api/workspaces", json={"name": "Команда проверки"}).json()["id"]
    project = client.post("/api/projects", json={
        "workspace_id": team, "name": "Проверка", "repository_url": "https://github.com/TBagirov/training",
    }).json()["id"]
    calls = []

    def provider(request):
        assert request.url.host == "api.openai.com"
        user_message = json.loads(request.content)["messages"][-1]["content"]
        calls.append(user_message)
        content = '{"ok":true}' if len(calls) == 1 else '{"items":[]}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    transport_client = httpx.Client
    monkeypatch.setattr(llm.httpx, "Client", lambda **kw: transport_client(transport=httpx.MockTransport(provider), **kw))
    client.put(f"/api/workspaces/{team}/llm", json={
        "provider": "openai", "model": "gpt-4o-mini", "api_key": "fixture-key",
    }).raise_for_status()
    graph = analysis_graph()
    graph["nodes"].append(node("ai", "ai_report"))
    graph["edges"].append(edge("explain", "merge", "bundle", "ai", "bundle"))
    workflow = create_workflow(client, project, graph)
    run_id = client.post(f"/api/workflows/{workflow}/runs").json()["id"]
    with sessions.begin() as db:
        db.get(ScanRun, UUID(run_id)).created_at = datetime(1980, 1, 1, tzinfo=UTC)
    _prepare_sources(monkeypatch)
    assert run_next(sessions, settings, allowed_repository_urls(settings.allowed_repositories))
    assert len(calls) == 2 and "fixture-auth-83" not in calls[1] and "App.kt" not in calls[1]
    report = client.get(f"/api/runs/{run_id}/report").json()
    assert report["llm"]["status"] == "failed"
    assert report["summary"]["total"] == 1
    assert client.get(f"/api/runs/{run_id}/export").json()["findings"]
