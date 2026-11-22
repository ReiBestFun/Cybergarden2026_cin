import json
from datetime import UTC, datetime
from secrets import randbits
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import llm
from backend.app.db.models import ScanRun
from backend.app.integrations.repository import allowed_repository_urls
from backend.app.queue_worker import run_next
from backend.tests.test_api import ORIGIN, analysis_graph, api, create_workflow, edge, login, node
from backend.tests.test_notifications import _prepare_sources


@pytest.mark.parametrize("provider_id,endpoint,model", [
    ("openai", "https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-flash-latest"),
    ("groq", "https://api.groq.com/openai/v1/chat/completions", "openai/gpt-oss-20b"),
])
def test_owner_enables_verified_model_only_for_their_workspace(api, monkeypatch, provider_id, endpoint, model):
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
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "mistral:test"}]})
        payload = json.loads(request.content)
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": draft}})
        assert str(request.url) == endpoint
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
    catalog = client.get(path + "/models").json()
    assert model in catalog[provider_id] and "mistral:test" in catalog["ollama"]
    profile = {"provider": provider_id, "model": model, "api_key": "fixture-llm-secret"}
    assert client.put(path, json=profile).status_code == 502
    assert client.get(path).json()["provider"] == "ollama"
    available = True
    enabled = client.put(path, json=profile)
    assert enabled.status_code == 200 and enabled.json()["provider"] == provider_id
    assert client.get(path).json() == {"provider": provider_id, "model": model}
    other_provider = "gemini" if provider_id != "gemini" else "groq"
    assert client.put(path, json={"provider": other_provider, "model": catalog[other_provider][0]}).status_code == 422
    replacement = catalog[provider_id][1]
    assert client.put(path, json={"provider": provider_id, "model": replacement}).json()["model"] == replacement
    assert "fixture-llm-secret" not in enabled.text + client.get(path).text
    task = {"description": description, "timezone": "UTC"}
    generate = "/api/workflows/generate"
    assert client.post(generate, params={"workspace_id": team}, json=task).json()["graph"]["nodes"]
    assert calls[-1] == httpx.URL(endpoint).host
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


def test_selected_ollama_model_is_used_for_generation(api, monkeypatch):
    client, _ = api
    team = client.post("/api/workspaces", json={"name": "Локальные модели"}).json()["id"]
    path = f"/api/workspaces/{team}/llm"
    model = "library/mistral:test"
    used = []

    def provider(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": model}]})
        assert request.url.path == "/api/chat"
        used.append(json.loads(request.content)["model"])
        content = '{"ok":true}' if len(used) == 1 else '{"plan":{"name":"Проверка","trigger":"manual","scanners":["secrets"]}}'
        return httpx.Response(200, json={"message": {"content": content}})

    transport_client = httpx.Client
    monkeypatch.setattr(llm.httpx, "Client", lambda **kw: transport_client(transport=httpx.MockTransport(provider), **kw))
    profile = {"provider": "ollama", "model": model}
    assert model in client.get(path + "/models").json()["ollama"]
    assert client.put(path, json=profile).json() == profile
    assert client.put(path, json={"provider": "ollama", "model": "missing"}).status_code == 422
    assert client.get(path).json() == profile
    generated = client.post("/api/workflows/generate", params={"workspace_id": team},
                            json={"description": "Проверить секреты проекта", "timezone": "UTC"})
    assert generated.json()["graph"]["nodes"]
    assert used == [model, model]


def test_ai_report_uses_project_profile_and_keeps_findings_after_model_error(api, monkeypatch):
    client, sessions = api
    settings = client.app.state.settings
    team = client.post("/api/workspaces", json={"name": "Команда проверки"}).json()["id"]
    project = client.post("/api/projects", json={
        "workspace_id": team, "name": "Проверка", "repository_url": "https://github.com/TBagirov/training",
    }).json()["id"]
    calls = []

    def provider(request):
        assert request.url.host == "generativelanguage.googleapis.com"
        user_message = json.loads(request.content)["messages"][-1]["content"]
        calls.append(user_message)
        content = '{"ok":true}' if len(calls) == 1 else '{"items":[]}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    transport_client = httpx.Client
    monkeypatch.setattr(llm.httpx, "Client", lambda **kw: transport_client(transport=httpx.MockTransport(provider), **kw))
    client.put(f"/api/workspaces/{team}/llm", json={
        "provider": "gemini", "model": "gemini-flash-latest", "api_key": "fixture-key",
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
