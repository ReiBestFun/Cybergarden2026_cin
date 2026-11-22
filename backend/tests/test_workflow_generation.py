import json

import httpx
from fastapi.testclient import TestClient

from backend.app.api.auth import _current_user

from backend.app import llm
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.schemas.contracts import Graph
from backend.app.workflows.generation import ModelAnswer
from backend.app.workflows.validation import validate_graph


def _client() -> TestClient:
    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[_current_user] = lambda: None
    return TestClient(app)


def test_generation_returns_editable_graph_with_schedule_and_conditional_delivery(monkeypatch):
    def provider(request):
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        assert payload["format"] == "json"
        assert "WorkflowPlan" in payload["messages"][0]["content"]
        answer = ModelAnswer(plan={
            "name": "Отчёт по будням", "trigger": "schedule", "scanners": ["secrets", "sca"],
            "schedule": {"frequency": "weekly", "time": "14:00", "weekdays": [0, 1, 2, 3, 4]},
            "condition": {"field": "summary.total", "operator": "gt", "value": 0},
            "notifications": [
                {"channel": "email", "when": "true", "recipients": ["dev@example.org"]},
                {"channel": "telegram", "when": "false", "recipients": ["@invented"]},
            ],
        })
        return httpx.Response(200, json={"message": {"content": answer.model_dump_json()}})

    client_class = httpx.Client
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: client_class(
        transport=httpx.MockTransport(provider), **kwargs,
    ))
    with _client() as client:
        response = client.post("/api/workflows/generate", json={
            "description": "По будням в 14:00 проверь секреты и зависимости в https://github.com/TBagirov/training. "
                           "При находках пиши на dev@example.org, иначе в Telegram.",
            "timezone": "Europe/Moscow",
        })
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["repository_url"] == "https://github.com/TBagirov/training"
    assert draft["schedule"]["time"] == "14:00" and draft["schedule"]["timezone"] == "Europe/Moscow"
    graph = Graph.model_validate(draft["graph"])
    nodes = {node.type: node for node in graph.nodes}
    assert nodes["notify_email"].config["recipients"] == ["dev@example.org"]
    assert nodes["notify_telegram"].config == {}
    assert [(issue["code"], issue["node_id"]) for issue in draft["issues"]] == [
        ("RECIPIENTS_REQUIRED", nodes["notify_telegram"].id),
    ]
    branches = {edge.target: edge.source_port for edge in graph.edges if edge.source == nodes["condition"].id}
    assert branches == {nodes["notify_email"].id: "true", nodes["notify_telegram"].id: "false"}
    nodes["notify_telegram"].config["recipients"] = ["@confirmed"]
    assert validate_graph(graph, expected_trigger="schedule_trigger") == []


def test_generation_connects_all_catalog_analyzers(monkeypatch):
    scanners = ["secrets", "sca", "sast", "spring_config", "password_hashing", "sensitive_logging"]
    description = ("Вручную запусти все анализаторы, включая конфигурацию Spring Boot, "
                   "хеширование паролей и утечки в логи.")

    def provider(request):
        prompt = json.loads(request.content)["messages"][0]["content"]
        guide, schema = prompt.split("Схема JSON-ответа:\n")
        assert all(f"- {scanner}:" in guide for scanner in scanners)
        field = json.loads(schema)["$defs"]["WorkflowPlan"]["properties"]["scanners"]
        assert set(field["items"]["enum"]) == set(scanners) and field["maxItems"] == len(scanners)
        answer = {"plan": {"name": "Все анализаторы", "trigger": "manual", "scanners": scanners}}
        return httpx.Response(200, json={"message": {"content": json.dumps(answer)}})

    client_class = httpx.Client
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: client_class(
        transport=httpx.MockTransport(provider), **kwargs,
    ))
    with _client() as client:
        response = client.post("/api/workflows/generate", json={"description": description, "timezone": "UTC"})
    assert response.status_code == 200, response.text
    graph = Graph.model_validate(response.json()["graph"])
    nodes = {node.id: node.type for node in graph.nodes}
    scans = {kind for kind in nodes.values() if kind.startswith("scan_")}
    assert scans == {f"scan_{scanner}" for scanner in scanners}
    assert {(nodes[edge.source], nodes[edge.target]) for edge in graph.edges} == {
        ("manual_trigger", "repository"),
        *(("repository", scan) for scan in scans),
        *((scan, "merge_findings") for scan in scans),
    }
    assert validate_graph(graph, expected_trigger="manual_trigger") == []


def test_generation_rejects_repository_outside_allowlist_before_calling_model(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Provider should not be called")

    monkeypatch.setattr(llm.httpx, "Client", unexpected)
    with _client() as client:
        response = client.post("/api/workflows/generate", json={
            "description": "Проверь https://github.com/example/out-of-scope.",
            "timezone": "UTC",
        })
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REPOSITORY_NOT_ALLOWED"
