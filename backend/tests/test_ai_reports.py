import json
from contextlib import ExitStack
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from backend.app import ai_reports, llm
from backend.app.analyzers.models import Finding
from backend.app.config import Settings
from backend.app.reports import RunReport
from backend.app.schemas.contracts import Edge, Node
from backend.app.workflows import execution


@pytest.mark.parametrize("failure", [None, "reference", "group", "schema", "unavailable"])
def test_provider_contract_and_grounding(monkeypatch, failure):
    finding = Finding("secret.password", "secrets", "high", "Встроенный пароль", "", "",
                      "private/path.kt", 13, 13, "secret-value", {"class": "password"})
    bundle = RunReport("", "partial", (), (finding,))
    settings = Settings(_env_file=None, llm_provider="openai_compatible", llm_base_url="https://provider.example/v1",
                        llm_api_key="test-key", llm_model="test-model")

    def provider(request):
        assert str(request.url) == "https://provider.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert finding.path not in str(body) and finding.evidence not in str(body)
        if failure == "unavailable":
            return httpx.Response(503)
        advice = {"group": 2 if failure == "group" else 1, "risk": "Пароль раскрыт.",
                  "fix": "CVE-2026-12345" if failure == "reference" else "Смените пароль."}
        content = "{}" if failure == "schema" else json.dumps({"items": [advice]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.Client
    monkeypatch.setattr(llm.httpx, "Client",
                        lambda **kwargs: client(transport=httpx.MockTransport(provider), **kwargs))
    if failure:
        with pytest.raises(ai_reports.ReportError):
            ai_reports.explain_report(bundle, settings)
    else:
        result = ai_reports.explain_report(bundle, settings)
        assert "private/path.kt:13" in result and "Смените пароль." in result


@pytest.mark.parametrize("available", [True, False])
def test_ai_node_passes_original_bundle_to_notification(monkeypatch, available):
    def generate(bundle, settings):
        if available:
            return "AI объяснение"
        raise ai_reports.ReportError("Ошибка провайдера")

    monkeypatch.setattr(execution, "explain_report", generate)
    settings = Settings(_env_file=None, notify_email_recipients=["dev@example.org"])
    claim = SimpleNamespace(id=uuid4(), trigger="manual", source={"repository_url": "demo"})
    progress = SimpleNamespace(claim=claim, llm_settings=lambda defaults: defaults)
    worker = execution.GraphExecution(progress, settings, set())
    bundle = RunReport("f" * 40, "succeeded", (), ())
    worker.steps["merge"] = execution.Step(ports=("bundle",), bundle=bundle)
    ai = Node(id="ai", type="ai_report", position={"x": 0, "y": 0})
    mail = Node(id="mail", type="notify_email", position=ai.position, config={"recipients": ["dev@example.org"]})
    first = Edge(id="in", source="merge", source_port="bundle", target="ai", target_port="bundle")
    second = Edge(id="out", source="ai", source_port="report", target="mail", target_port="report")
    with ExitStack() as stack:
        step = worker._execute_node(ai, [first], stack)
        worker.steps["ai"] = step
        result = worker._execute_node(mail, [second], stack)
    assert step.status == ("succeeded" if available else "failed")
    assert step.bundle is bundle and step.ports == ("report",)
    message = result.notification.message
    assert ("AI объяснение" if available else "AI-рекомендации недоступны") in message.text
    assert json.loads(message.attachments[0].content) == {"findings": []}

    telegram = Node(id="team", type="notify_telegram", position=ai.position,
                    config={"recipients": ["@team_alerts"], "summary_only": True})
    team_edge = Edge(id="team-out", source="ai", source_port="report", target="team", target_port="report")
    with ExitStack() as stack:
        summary = worker._execute_node(telegram, [team_edge], stack).notification.message
    assert summary.text.splitlines() == [
        "CyberFlow: сводка проверки",
        "Результат анализа: проверка завершена",
        "Находок: 0 · HIGH: 0 · CRITICAL: 0",
        f"Отчёт: {settings.app_base_url}/results?run={claim.id}",
    ]
    assert summary.attachments == ()
