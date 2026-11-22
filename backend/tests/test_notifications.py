import json
import logging
import re
import smtplib
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from secrets import randbits
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, text

from backend.app.analyzers.models import Finding, ScanError, Snapshot, Warning
from backend.app.config import Settings
from backend.app.db.models import NotificationDelivery, ScanRun
from backend.app.devtools.mail_sink import SmtpSink
from backend.app.integrations.repository import RepositorySnapshot, allowed_repository_urls
from backend.app.notification_worker import process_next
from backend.app.notifications import EmailChannel, NotifierError, TelegramChannel, build_message
from backend.app.notifications.base import DeliveryError
from backend.app.notifications.queue import claim_delivery, expire_deliveries, finish_delivery
from backend.app.queue_worker import run_next
from backend.app.reports import RunReport
from backend.app.schemas.contracts import Graph
from backend.app.telegram_link_worker import consume_start
from backend.app.workflows import execution
from backend.app.workflows.validation import validate_draft, validate_graph
from backend.tests.test_api import ORIGIN, api, create_project, create_workflow, enqueue_for_worker, login

TOKEN = "123456:TEST-token-value"


def test_workspace_delivery_settings_hide_secrets_and_enforce_membership(api, monkeypatch):
    client, _ = api
    workspace = client.post("/api/workspaces", json={"name": "Команда доставки"}).json()["id"]
    path = f"/api/workspaces/{workspace}/delivery"
    client.app.state.settings.notify_email_recipients = ["team@example.org", "second@example.org"]
    assert client.get(path).json() == {"email": None, "telegram": None, "slack": None}
    value = {"version": 0, "recipients": ["team@example.org"], "credentials": {"username": "smtp-user", "secret": "fixture-smtp-secret"}}
    changed = client.patch(path + "/email", json=value)
    assert changed.status_code == 200 and changed.json() == {"version": 1, "recipients": ["team@example.org"], "has_credentials": True}
    assert "fixture-smtp-secret" not in client.get(path).text and "smtp-user" not in client.get(path).text
    assert client.patch(path + "/email", json=value).status_code == 409
    assert client.patch(path + "/email", json={"version": 1, "recipients": ["outside@example.org"]}).status_code == 422
    assert client.patch(path + "/email", json={"version": 1, "recipients": ["team@example.org"]}).json()["has_credentials"]
    second = client.post("/api/workspaces", json={"name": "Другая команда"}).json()["id"]
    other_path = f"/api/workspaces/{second}/delivery"
    other_settings = client.patch(other_path + "/email", json={
        "version": 0, "recipients": ["second@example.org"],
        "credentials": {"username": "other-smtp-user", "secret": "other-smtp-secret"},
    })
    assert other_settings.json() == {"version": 1, "recipients": ["second@example.org"], "has_credentials": True}
    assert client.get(path).json()["email"]["recipients"] == ["team@example.org"]
    invitation = client.post(f"/api/workspaces/{workspace}/invitations", json={"role": "member"}).json()["token"]
    with TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as other:
        login(other, monkeypatch, randbits(48) + 1)
        assert other.get(path).status_code == 404
        assert other.post(f"/api/workspaces/{workspace}/join", json={"token": invitation}).status_code == 200
        assert other.get(path).status_code == 403
        assert other.patch(path + "/email", json=value).status_code == 403
        assert other.get(other_path).status_code == 404


def report() -> RunReport:
    return RunReport("f" * 40, "succeeded", (), (
        Finding("secret.password", "secrets", "high", "Встроенный пароль", "Пароль в коде.", "Вынесите в окружение.",
                "src/App.kt", 4, 4, "***", {"class": "password"}),
        Finding("sast.sql-injection", "sast", "critical", "SQL", "Ввод в SQL.", "Параметризуйте.",
                "src/Repo.kt", 10, 12, "", {"class": "sql-injection"}),
    ))


def test_message_has_summary_and_masked_json():
    message = build_message(report(), run_id="run-1", repository="https://github.com/TBagirov/training",
                            app_base_url="http://localhost:5173/")
    assert "Находок: 2" in message.text and "CRITICAL 1, HIGH 1" in message.text
    assert "http://localhost:5173/results?run=run-1" in message.text
    assert message.subject == "CyberFlow: 2 находок в training (fffffff)"
    [attachment] = message.attachments
    assert attachment.filename == "findings-fffffff.json"
    findings = json.loads(attachment.content)["findings"]
    assert [item["class"] for item in findings] == ["password", "sql-injection"]
    assert build_message(report(), run_id="r", repository="x", app_base_url="", attach_json=False).attachments == ()
    link_only = build_message(report(), run_id="r", repository="x", app_base_url="https://reports.example.org", max_attachment_bytes=1)
    assert not link_only.attachments and "приложен" not in link_only.text and "https://reports.example.org/results?run=r" in link_only.text


class _FakeTelegram(BaseHTTPRequestHandler):
    calls: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        method = self.path.rsplit("/", 1)[-1]
        _FakeTelegram.calls.append((self.path, method, body))
        if TOKEN not in self.path:
            status, payload = 401, {"ok": False, "description": "Unauthorized"}
        elif method in {"getMe", "getChat", "getChatMember", "getChatAdministrators"}:
            answers = {
                "getMe": {"id": 111, "username": "cyberflow_bot"},
                "getChat": {"id": -1008765, "type": "channel"},
                "getChatMember": {"status": "administrator", "can_post_messages": True},
                "getChatAdministrators": [{"user": {"id": 1234567}}],
            }
            status, payload = 200, {"ok": True, "result": answers[method]}
        elif b"-100404" in body:
            status, payload = 400, {"ok": False, "description": "Bad Request: chat not found"}
        elif method == "sendDocument" and b"-100429" in body and sum(
            entry[1] == "sendDocument" and b"-100429" in entry[2] for entry in _FakeTelegram.calls
        ) == 1:
            status, payload = 429, {"ok": False, "parameters": {"retry_after": 42}}
        else:
            status, payload = 200, {"ok": True, "result": {"message_id": len(_FakeTelegram.calls)}}
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def telegram_api():
    _FakeTelegram.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeTelegram)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_telegram_sends_text_and_document_to_each_chat(telegram_api):
    message = build_message(report(), run_id="run-1", repository="repo", app_base_url="http://app")
    deliveries = TelegramChannel(TOKEN, api_base=telegram_api).send(["-1001", "-100404", "@team_alerts"], message)
    assert [(item.recipient, item.status) for item in deliveries] == [
        ("-1001", "sent"), ("-100404", "failed"), ("@team_alerts", "sent"),
    ]
    assert deliveries[1].error == "Bad Request: chat not found"
    methods = [method for _, method, _ in _FakeTelegram.calls]
    assert methods == ["sendMessage", "sendDocument", "sendMessage", "sendMessage", "sendDocument"]
    document = _FakeTelegram.calls[1][2]
    assert b'filename="findings-fffffff.json"' in document and b'"sql-injection"' in document


def test_telegram_rejects_missing_or_bad_token(telegram_api):
    with pytest.raises(NotifierError) as missing:
        TelegramChannel(None)
    assert missing.value.code == "TELEGRAM_NOT_CONFIGURED"
    message = build_message(report(), run_id="r", repository="repo", app_base_url="")
    with pytest.raises(NotifierError) as bad:
        TelegramChannel("999:wrong", api_base=telegram_api).send(["-1001"], message)
    assert bad.value.code == "TELEGRAM_UNAUTHORIZED" and "999:wrong" not in bad.value.message


@pytest.mark.parametrize("group", [False, True])
def test_telegram_link_and_verification(api, telegram_api, group):
    client, sessions = api
    settings = client.app.state.settings
    settings.telegram_bot_token = SecretStr(TOKEN)
    settings.telegram_api_base = telegram_api
    base = "/api/telegram-destinations"
    stale = client.post(base + "/links", json={"group_chat": group}).raise_for_status().json()
    link = client.post(base + "/links", json={"group_chat": group}).raise_for_status().json()
    assert client.get(base + "/links/" + stale["id"]).status_code == 404
    prefix = "https://t.me/cyberflow_bot?" + ("startgroup" if group else "start") + "="
    assert link["url"].startswith(prefix)
    status = base + "/links/" + link["id"]
    assert client.get(status).raise_for_status().json() is None
    chat = {"id": -43210, "type": "group", "title": "Команда"} if group else {
        "id": 1234567, "type": "private", "username": "test_user",
    }
    message = {"chat": chat, "from": {"id": 1234567, "is_bot": False}, "text": "/start " + link["url"][len(prefix):]}
    with sessions.begin() as db:
        consume_start(db, {**message, "text": "/start " + stale["url"][len(prefix):]}, settings, "cyberflow_bot")
        consume_start(db, {**message, "forward_origin": {"type": "user"}}, settings, "cyberflow_bot")
    assert client.get(status).json() is None
    for _ in range(2):
        with sessions.begin() as db:
            consume_start(db, message, settings, "cyberflow_bot")
    result = client.get(status).raise_for_status().json()
    assert result["chat_id"] == str(chat["id"]) and result["verified_at"] is None
    assert len(client.get(base).json()["items"]) == 1
    dest = base + "/" + result["id"]
    client.post(dest + "/challenge").raise_for_status()
    sent = json.loads(_FakeTelegram.calls[-1][2])
    code = re.search(r"\d{8}", sent["text"]).group()
    assert client.post(dest + "/verify", json={"code": code}).raise_for_status().json()["verified_at"]
    channel = client.post(base + "/public", json={"username": "@team_alerts"})
    if group:
        assert channel.status_code == 409
    else:
        assert channel.raise_for_status().json()["chat_id"] == "-1008765"
        assert channel.json()["verified_at"]


def test_telegram_link_expiry_owner_and_chat_type(api, telegram_api, monkeypatch):
    from backend.app.db.models import TelegramLink
    client, sessions = api
    settings = client.app.state.settings
    settings.telegram_bot_token = SecretStr(TOKEN)
    settings.telegram_api_base = telegram_api
    base = "/api/telegram-destinations"
    link = client.post(base + "/links", json={}).raise_for_status().json()
    status = base + "/links/" + link["id"]
    with TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as other:
        login(other, monkeypatch, randbits(48) + 1)
        assert other.get(status).status_code == 404
    message = {"text": "/start " + link["url"].split("=")[1], "from": {"is_bot": False, "id": 5}}
    with sessions.begin() as db:
        consume_start(db, {**message, "chat": {"type": "group", "id": -123}}, settings, "cyberflow_bot")
        attempt = db.scalar(select(TelegramLink).where(TelegramLink.id == UUID(link["id"])))
        assert attempt.token_hash and attempt.destination_id is None
        attempt.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    with sessions.begin() as db:
        consume_start(db, {**message, "chat": {"type": "private", "id": 5}}, settings, "cyberflow_bot")
    assert client.get(status).status_code == 410
    assert client.get(base).json()["items"] == []


def test_email_reaches_all_recipients_privately():
    received = []
    with SmtpSink(0, lambda sender, rcpts, data: received.append((sender, rcpts, data)), reject_domain="invalid.test") as sink:
        sink.start()
        channel = EmailChannel("127.0.0.1", port=sink.server_address[1], security="none", sender="cyberflow@localhost")
        message = build_message(report(), run_id="run-1", repository="repo", app_base_url="http://app")
        deliveries = channel.send(["dev1@example.org", "nobody@invalid.test", "dev2@example.org"], message)
        sink.shutdown()
    assert [(item.recipient, item.status) for item in deliveries] == [
        ("dev1@example.org", "sent"), ("nobody@invalid.test", "failed"), ("dev2@example.org", "sent"),
    ]
    [(sender, recipients, data)] = received
    assert sender == "cyberflow@localhost" and recipients == ["dev1@example.org", "dev2@example.org"]
    email = message_from_bytes(data, policy=policy.default)
    assert email["To"] == "undisclosed-recipients:;" and "dev1@example.org" not in str(email["To"])
    assert [part.get_filename() for part in email.iter_attachments()] == ["findings-fffffff.json"]
    assert "Находок: 2" in email.get_body(("plain",)).get_content()


def test_email_requires_server_settings():
    with pytest.raises(NotifierError) as error:
        EmailChannel(None, sender="a@b.c")
    assert error.value.code == "SMTP_NOT_CONFIGURED"


@pytest.mark.parametrize(("failure", "outcome"), [
    (smtplib.SMTPDataError(451, b"temporary"), "retry"),
    (smtplib.SMTPDataError(554, b"permanent"), "failed"),
    (smtplib.SMTPServerDisconnected("confirmation missing"), "unknown"),
])
def test_email_worker_distinguishes_data_rejection_from_missing_confirmation(monkeypatch, failure, outcome):
    class Connection:
        def send_message(self, *args, **kwargs):
            raise failure

        def close(self):
            pass

    channel = EmailChannel("127.0.0.1", security="none", sender="cyberflow@localhost")
    monkeypatch.setattr(channel, "_connect", Connection)
    message = build_message(report(), run_id="run-id", repository="repo", app_base_url="http://app")
    with pytest.raises(DeliveryError) as error:
        channel.send_one("dev@example.org", message, delivery_id="notification-id")
    assert error.value.outcome == outcome


def _graph(*notifiers):
    nodes = [
        {"id": "t", "type": "manual_trigger", "position": {"x": 0, "y": 0}},
        {"id": "r", "type": "repository", "position": {"x": 0, "y": 0}},
        {"id": "s", "type": "scan_secrets", "position": {"x": 0, "y": 0}},
        {"id": "m", "type": "merge_findings", "position": {"x": 0, "y": 0}},
    ]
    edges = [
        {"id": "e1", "source": "t", "source_port": "params", "target": "r", "target_port": "params"},
        {"id": "e2", "source": "r", "source_port": "snapshot", "target": "s", "target_port": "snapshot"},
        {"id": "e3", "source": "s", "source_port": "result", "target": "m", "target_port": "results"},
    ]
    for index, (kind, config) in enumerate(notifiers):
        nodes.append({"id": f"n{index}", "type": kind, "config": config, "position": {"x": 0, "y": 0}})
        edges.append({"id": f"en{index}", "source": "m", "source_port": "bundle", "target": f"n{index}", "target_port": "report"})
    return Graph.model_validate({"nodes": nodes, "edges": edges})


def test_notifiers_connect_to_merged_findings_and_validate_recipients():
    good = _graph(
        ("notify_telegram", {"recipients": ["-1001", "@team_alerts"]}),
        ("notify_email", {"recipients": ["dev@example.org"], "subject": "Отчёт", "attach_json": False}),
        ("notify_email", {"recipients": ["lead@example.org"]}),
    )
    assert validate_graph(good) == []
    empty = _graph(("notify_email", {}))
    assert validate_draft(empty) == []  # a draft may be saved before recipients are filled in
    assert [issue.field for issue in validate_graph(empty)] == ["config.recipients"]
    bad = _graph(("notify_email", {"recipients": ["not-an-address"]}), ("notify_telegram", {"recipients": ["hello world"]}))
    assert {issue.code for issue in validate_draft(bad)} == {"INVALID_CONFIG"} and len(validate_draft(bad)) == 2


def _prepare_sources(monkeypatch):
    @contextmanager
    def prepared(*args, **kwargs):
        yield RepositorySnapshot(Snapshot("b" * 40, {"App.kt": 'val password = "fixture-auth-83"'}), None)

    monkeypatch.setattr(execution, "prepare_repository", prepared)


@pytest.mark.parametrize("removed", [False, True])
def test_workspace_delivery_uses_latest_credentials_and_rechecks_queued_recipient(api, monkeypatch, removed):
    client, sessions = api
    settings = client.app.state.settings
    settings.smtp_host = "127.0.0.1"
    settings.smtp_from = "cyberflow@localhost"
    settings.notify_email_recipients = ["team@example.org", "new@example.org"]
    team = client.post("/api/workspaces", json={"name": "Командная почта"}).json()["id"]
    path = f"/api/workspaces/{team}/delivery/email"
    client.patch(path, json={"version": 0, "recipients": ["team@example.org"], "credentials": {
        "username": "old", "secret": "old-password",
    }}).raise_for_status()
    second = client.post("/api/workspaces", json={"name": "Вторая командная почта"}).json()["id"]
    client.patch(f"/api/workspaces/{second}/delivery/email", json={
        "version": 0, "recipients": ["new@example.org"], "credentials": {"username": "other", "secret": "other-password"},
    }).raise_for_status()
    project = client.post("/api/projects", json={
        "name": "Командный проект", "repository_url": "https://github.com/TBagirov/training", "workspace_id": team,
    }).json()["id"]
    graph = _graph(("notify_email", {"use_workspace_settings": True, "recipients": ["unused-invalid-address"]}))
    workflow = create_workflow(client, project, graph.model_dump())
    response = client.post(f"/api/workflows/{workflow}/runs")
    assert response.status_code == 202
    run_id = response.json()["id"]
    with sessions.begin() as db:
        db.get(ScanRun, UUID(run_id)).created_at = datetime(1980, 1, 1, tzinfo=UTC)
    _prepare_sources(monkeypatch)
    assert run_next(sessions, settings, allowed_repository_urls(settings.allowed_repositories))
    client.patch(path, json={"version": 1, "recipients": ["new@example.org" if removed else "team@example.org"], "credentials": {
        "username": "new", "secret": "new-password",
    }}).raise_for_status()
    sent = []

    def simulate_send(channel, recipient, message, **kwargs):
        sent.append((channel._username, channel._password, recipient))
        return "fixture-id"

    monkeypatch.setattr(EmailChannel, "send_one", simulate_send)
    assert process_next(sessions, settings)
    [delivery] = client.get(f"/api/runs/{run_id}/report").json()["delivery"]["n0"]["recipients"]
    if removed:
        assert not sent and delivery["error_code"] == "WORKSPACE_RECIPIENT_REMOVED"
    else:
        assert sent == [("new", "new-password", "team@example.org")] and delivery["status"] == "accepted"


@pytest.mark.parametrize("reject_delivery", [False, True])
def test_team_telegram_summary_and_provider_status(api, telegram_api, monkeypatch, reject_delivery):
    client, sessions = api
    settings = client.app.state.settings
    settings.telegram_bot_token = SecretStr(TOKEN)
    settings.telegram_api_base = telegram_api
    settings.notify_telegram_recipients = []
    team = client.post("/api/workspaces", json={"name": "Командный Telegram"}).json()["id"]
    path = f"/api/workspaces/{team}/delivery/telegram"
    chat_id = "-1001002" if reject_delivery else "-1001001"
    config = {"version": 0, "recipients": [chat_id], "credentials": {"secret": TOKEN}}
    rejected = client.patch(path, json=config)
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "TELEGRAM_DESTINATION_UNVERIFIED"
    destination = client.post("/api/telegram-destinations", json={"label": "Канал команды", "chat_id": chat_id}).json()
    destination_path = f"/api/telegram-destinations/{destination['id']}"
    client.post(destination_path + "/challenge").raise_for_status()
    code = re.search(r"\b[0-9]{8}\b", json.loads(_FakeTelegram.calls[-1][2])["text"])[0]
    client.post(destination_path + "/verify", json={"code": code}).raise_for_status()
    assert client.patch(path, json=config).json() == {"version": 1, "recipients": [chat_id], "has_credentials": True}
    assert TOKEN not in client.get(f"/api/workspaces/{team}/delivery").text

    project = client.post("/api/projects", json={
        "name": "Командный проект", "repository_url": "https://github.com/TBagirov/training", "workspace_id": team,
    }).json()["id"]
    graph = _graph(("notify_telegram", {"use_workspace_settings": True, "summary_only": True}))
    workflow = create_workflow(client, project, graph.model_dump())
    run_id = client.post(f"/api/workflows/{workflow}/runs").json()["id"]
    with sessions.begin() as db:
        db.get(ScanRun, UUID(run_id)).created_at = datetime(1980, 1, 1, tzinfo=UTC)
    if reject_delivery:
        client.patch(path, json={"version": 1, "recipients": [chat_id], "credentials": {
            "secret": "987:fixture-invalid-token",
        }}).raise_for_status()
    _FakeTelegram.calls.clear()
    settings.telegram_bot_token = None
    _prepare_sources(monkeypatch)
    assert run_next(sessions, settings, allowed_repository_urls(settings.allowed_repositories))
    assert process_next(sessions, settings)
    [delivery] = client.get(f"/api/runs/{run_id}/report").json()["delivery"]["n0"]["recipients"]
    if reject_delivery:
        assert (delivery["status"], delivery["error_code"]) == ("failed", "TELEGRAM_UNAUTHORIZED")
    else:
        assert delivery["status"] == "accepted"
    [(_, method, body)] = _FakeTelegram.calls
    assert method == "sendMessage"
    sent = json.loads(body)
    assert sent["chat_id"] == chat_id
    assert f"{settings.app_base_url}/results?run={run_id}" in sent["text"]
    assert "Находок: 1" in sent["text"]
    assert all(value not in sent["text"] for value in (TOKEN, "987:fixture-invalid-token", "fixture-auth-83", "App.kt"))


@pytest.mark.parametrize("provider_status, rotated", [(200, False), (403, False), (302, False), (200, True)])
def test_slack_workspace_workflow_sends_only_summary_and_preserves_destination(api, monkeypatch, provider_status, rotated):
    client, sessions = api
    settings = client.app.state.settings
    team = client.post("/api/workspaces", json={"name": "Команда Slack"}).json()["id"]
    path = f"/api/workspaces/{team}/delivery/slack"
    webhook = "https://hooks.slack.com/services/T000FIXTURE/B000FIXTURE/fake-webhook-secret"
    rejected = client.patch(path, json={"version": 0, "recipients": [], "credentials": {"secret": "https://example.org/services/x/y/z"}})
    assert rejected.status_code == 422
    configured = client.patch(path, json={"version": 0, "recipients": [], "credentials": {"secret": webhook}})
    assert configured.status_code == 200 and configured.json()["has_credentials"]
    assert "fake-webhook-secret" not in client.get(path.rsplit("/", 1)[0]).text
    project = client.post("/api/projects", json={
        "name": "Учебный проект Slack", "repository_url": "https://github.com/TBagirov/training", "workspace_id": team,
    }).json()["id"]
    workflow = create_workflow(client, project, _graph(("notify_slack", {})).model_dump())
    response = client.post(f"/api/workflows/{workflow}/runs")
    assert response.status_code == 202
    run_id = response.json()["id"]
    with sessions.begin() as db:
        db.get(ScanRun, UUID(run_id)).created_at = datetime(1980, 1, 1, tzinfo=UTC)
    _prepare_sources(monkeypatch)
    assert run_next(sessions, settings, allowed_repository_urls(settings.allowed_repositories))
    if rotated:
        client.patch(path, json={"version": 1, "recipients": [], "credentials": {
            "secret": "https://hooks.slack.com/services/T000FIXTURE/B000OTHER/fake-other-secret",
        }}).raise_for_status()
    else:
        assert client.patch(path, json={"version": 1, "recipients": []}).json()["version"] == 1

    [queued] = client.get(f"/api/runs/{run_id}/report").json()["delivery"]["n0"]["recipients"]
    with sessions.begin() as db:
        db.get(NotificationDelivery, UUID(queued["id"])).next_attempt_at = datetime(1980, 1, 1, tzinfo=UTC)

    requests = []

    import io
    from types import SimpleNamespace
    from urllib.error import HTTPError
    from backend.app.notifications import slack

    class FixtureOpener:
        @contextmanager
        def open(self, request, timeout):
            assert request.get_method() == "POST"
            requests.append((request.full_url, json.loads(request.data)))
            if provider_status != 200:
                raise HTTPError(request.full_url, provider_status, "fake-provider-secret", {}, io.BytesIO())
            yield SimpleNamespace(status=200, read=lambda size: b"ok")

    def fixture_opener(proxy, redirect_handler):
        assert redirect_handler.redirect_request(None, None, 302, "", {}, "https://example.org") is None
        return FixtureOpener()

    monkeypatch.setattr(slack.urllib.request, "build_opener", fixture_opener)
    assert process_next(sessions, settings)
    response = client.get(f"/api/runs/{run_id}/report")
    [delivery] = response.json()["delivery"]["n0"]["recipients"]
    assert "fake-webhook-secret" not in response.text and "fake-provider-secret" not in response.text
    if rotated:
        assert requests == [] and delivery["error_code"] == "SLACK_DESTINATION_CHANGED"
    else:
        [(destination, payload)] = requests
        assert destination == webhook
        assert f"/results?run={run_id}" in payload["text"] and "Находок: 1" in payload["text"]
        assert "fixture-auth-83" not in payload["text"] and "App.kt" not in payload["text"]
        assert delivery["status"] == ("accepted" if provider_status == 200 else "failed")


@pytest.mark.parametrize("failure", [None, "repository", "delivery"])
def test_technical_logs_link_request_run_nodes_and_delivery_without_sensitive_data(api, caplog, monkeypatch, failure):
    client, sessions = api
    logger = logging.getLogger("cyberflow.events")
    monkeypatch.setattr(logger, "handlers", [caplog.handler])
    caplog.set_level(logging.INFO, logger=logger.name)

    @contextmanager
    def prepared(*args, **kwargs):
        if failure == "repository":
            raise ScanError(Warning("TEST_SOURCE_FAILED", "private-repository-error"))
        yield RepositorySnapshot(Snapshot("b" * 40, {"App.kt": 'val password = "fixture-source-password"'}), None)

    def send_email(*args, **kwargs):
        if failure == "delivery":
            raise RuntimeError("private-provider-response")
        return "private-provider-message-id"

    monkeypatch.setattr(execution, "prepare_repository", prepared)
    monkeypatch.setattr(EmailChannel, "send_one", send_email)
    recipient = "fixture-recipient@example.org"
    graph = _graph(("notify_email", {"recipients": [recipient], "subject": "private-message-subject"}))
    workflow = create_workflow(client, create_project(client), graph.model_dump())
    response = client.post(
        f"/api/workflows/{workflow}/runs", params={"key": "private-query-token"},
        headers={"Authorization": "Bearer private-header-token", "X-Request-ID": "private-client-id"},
        json={"ref": "private-body-ref"},
    )
    assert response.status_code == 202
    request_id = response.headers["X-Request-ID"]
    UUID(request_id)
    run_id = response.json()["id"]
    # The PostgreSQL fixture is shared with tests that leave runs queued.
    with sessions.begin() as db:
        db.get(ScanRun, UUID(run_id)).created_at = datetime(2000, 1, 1, tzinfo=UTC)
    settings = Settings(_env_file=None, smtp_host="localhost", smtp_from="cyberflow@localhost",
                        notify_email_recipients=[recipient])
    assert run_next(sessions, settings, allowed_repository_urls(settings.allowed_repositories))
    assert process_next(sessions, settings) is (failure != "repository")

    records = [json.loads(record.getMessage()) for record in caplog.records if record.name == logger.name]
    run_events = [event for event in records if event.get("run_id") == run_id]
    [http_event] = [event for event in run_events if event["component"] == "api"]
    assert http_event["request_id"] == request_id and http_event["http_status"] == 202
    outcomes = {(event["action"], event["result"]) for event in run_events}
    assert ("run.claim", "running") in outcomes
    assert ("run.finish", "failed" if failure == "repository" else "succeeded") in outcomes
    node_events = [event for event in run_events if event["action"] == "node.finish"]
    assert node_events and all(UUID(event["node_run_id"]) for event in node_events)
    assert {"manual_trigger", "repository", "scan_secrets", "merge_findings", "notify_email"} == {
        event["node_type"] for event in node_events
    }
    delivery_events = [event for event in run_events if event["component"] == "notification_worker"]
    if failure == "repository":
        assert ("node.finish", "failed") in outcomes and not delivery_events
    else:
        assert ("delivery.finish", "unknown" if failure == "delivery" else "accepted") in outcomes
        assert len({UUID(event["delivery_id"]) for event in delivery_events}) == 1
        [queued] = [event for event in run_events if event["action"] == "delivery.enqueue"]
        assert all((event["delivery_id"], event["node_run_id"]) == (queued["delivery_id"], queued["node_run_id"])
                   for event in delivery_events)
    assert all(event["timestamp"].endswith("+00:00") and event["level"] for event in run_events)
    cookie = client.cookies.get("cyberflow_session")
    assert cookie
    for sensitive in (cookie, recipient, "fixture-source-password", "private-"):
        assert sensitive not in caplog.text


def test_worker_delivers_email_and_retries_only_telegram_attachment(api, telegram_api, monkeypatch):
    client, sessions = api
    client.app.state.settings.telegram_bot_token = SecretStr(TOKEN)
    client.app.state.settings.telegram_api_base = telegram_api
    destination = client.post("/api/telegram-destinations", json={"label": "Тест", "chat_id": "-100429"}).json()
    path_to_destination = f"/api/telegram-destinations/{destination['id']}"
    client.post(path_to_destination + "/challenge").raise_for_status()
    code = re.search(r"\b[0-9]{8}\b", json.loads(_FakeTelegram.calls[-1][2])["text"])[0]
    client.post(path_to_destination + "/verify", json={"code": code}).raise_for_status()
    _FakeTelegram.calls.clear()
    _prepare_sources(monkeypatch)
    graph = _graph(
        ("notify_email", {"recipients": ["dev1@example.org", "outside@example.org", "dev1@EXAMPLE.ORG"]}),
        ("notify_telegram", {"recipients": ["-100429"]}),
    )
    run_id = enqueue_for_worker(api, graph.model_dump())
    path = f"/api/runs/{run_id}"
    received = []
    with SmtpSink(0, lambda sender, recipients, data: received.append((sender, recipients, data))) as sink:
        sink.start()
        settings = Settings(
            smtp_host="127.0.0.1", smtp_port=sink.server_address[1], smtp_security="none",
            smtp_from="cyberflow@localhost", notify_email_recipients=["dev1@example.org"],
            telegram_bot_token=TOKEN, telegram_api_base=telegram_api, notify_telegram_recipients=["-100429"],
        )
        assert run_next(sessions, settings, allowed_repository_urls(settings.allowed_repositories))
        assert client.get(path).json()["status"] == "succeeded"
        assert not received and not _FakeTelegram.calls
        for _ in range(3):
            assert process_next(sessions, settings)
        assert not process_next(sessions, settings)
        delivery = client.get(path + "/report").json()["delivery"]
        assert delivery["n0"]["status"] == "partial"
        assert {item["status"] for item in delivery["n0"]["recipients"]} == {"accepted", "failed"}
        blocked = next(item for item in delivery["n0"]["recipients"] if item["status"] == "failed")
        assert blocked["error_code"] == "RECIPIENT_NOT_ALLOWED"
        pending, = delivery["n1"]["recipients"]
        assert pending["phase"] == "attachment" and pending["status"] == "queued"
        assert pending["error_code"] == "TELEGRAM_RATE_LIMIT" and pending["message_id"]
        with sessions.begin() as db:
            row = db.get(NotificationDelivery, UUID(pending["id"]))
            assert row.next_attempt_at > row.updated_at + timedelta(seconds=40)
            row.next_attempt_at = datetime.now(UTC)
        assert process_next(sessions, settings)
        assert not process_next(sessions, settings)
        sink.shutdown()

    [(_, recipients, message)] = received
    assert recipients == ["dev1@example.org"]
    assert b"fixture-auth-83" not in message
    email = message_from_bytes(message, policy=policy.default)
    assert list(email.iter_attachments())
    assert [method for _, method, _ in _FakeTelegram.calls] == ["sendMessage", "sendDocument", "sendDocument"]
    report_response = client.get(path + "/report")
    assert "dev1@example.org" not in report_response.text and "-100429" not in report_response.text
    delivered, = report_response.json()["delivery"]["n1"]["recipients"]
    assert delivered["status"] == "accepted" and delivered["attachment_id"] != delivered["message_id"]
    assert client.get(path).json()["status"] == "succeeded"


def test_telegram_confirmation_is_owner_scoped_and_removed_chat_cannot_receive_report(api, telegram_api, monkeypatch):
    client, sessions = api
    client.app.state.settings.telegram_bot_token = SecretStr(TOKEN)
    client.app.state.settings.telegram_api_base = telegram_api
    client.app.state.settings.notify_telegram_recipients = []
    chat = "-100575321"
    response = client.post("/api/telegram-destinations", json={"label": "Private chat", "chat_id": chat})
    assert response.status_code == 201
    destination_id = response.json()["id"]
    path = f"/api/telegram-destinations/{destination_id}"
    assert not _FakeTelegram.calls
    challenge = client.post(path + "/challenge")
    assert challenge.status_code == 200 and challenge.json()["verified_at"] is None
    code = re.search(r"\b[0-9]{8}\b", json.loads(_FakeTelegram.calls[-1][2])["text"])[0]
    assert code not in challenge.text
    assert client.post(path + "/challenge").status_code == 429
    wrong = "00000000" if code != "00000000" else "00000001"
    assert client.post(path + "/verify", json={"code": wrong}).status_code == 422

    with TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as other:
        login(other, monkeypatch, randbits(48) + 1)
        assert other.get("/api/telegram-destinations").json()["items"] == []
        assert other.post(path + "/verify", json={"code": code}).status_code == 404
        assert other.delete(path).status_code == 404
        _prepare_sources(monkeypatch)
        graph = _graph(("notify_telegram", {"recipients": [chat]}))
        settings = Settings(telegram_bot_token=TOKEN, telegram_api_base=telegram_api, notify_telegram_recipients=[])
        foreign_run = enqueue_for_worker((other, sessions), graph.model_dump())
        assert run_next(sessions, settings, allowed_repository_urls(settings.allowed_repositories))
        delivery = other.get(f"/api/runs/{foreign_run}/report").json()["delivery"]["n0"]["recipients"][0]
        assert delivery["error_code"] == "TELEGRAM_DESTINATION_UNVERIFIED"

    with sessions() as db:
        stored = db.execute(text(
            "SELECT chat_id, label, challenge_hash FROM telegram_destinations WHERE id = :id",
        ), {"id": UUID(destination_id)}).one()
        assert all(value not in repr(stored) for value in (chat, "Private chat", code))
    confirmed = client.post(path + "/verify", json={"code": code})
    assert confirmed.json()["verified_at"] is not None

    run_id = enqueue_for_worker(api, graph.model_dump(), priority=-1)
    assert run_next(sessions, settings, allowed_repository_urls(settings.allowed_repositories))
    assert client.delete(path).status_code == 204
    assert process_next(sessions, settings)
    delivery = client.get(f"/api/runs/{run_id}/report").json()["delivery"]["n0"]["recipients"][0]
    assert delivery["error_code"] == "TELEGRAM_DESTINATION_UNVERIFIED"
    assert len(_FakeTelegram.calls) == 1  # only the fake verification message was sent


def test_expired_delivery_stays_unknown_and_cannot_be_taken_again(api, monkeypatch):
    client, sessions = api
    _prepare_sources(monkeypatch)
    graph = _graph(("notify_email", {"recipients": ["dev@example.org"]}))
    run_id = enqueue_for_worker(api, graph.model_dump())
    settings = Settings(notify_email_recipients=["dev@example.org"])
    assert run_next(sessions, settings, allowed_repository_urls(settings.allowed_repositories))
    now = datetime.now(UTC)
    with sessions.begin() as db:
        claim = claim_delivery(db, now, lease_seconds=1)
        assert claim is not None
        with sessions.begin() as other:
            assert claim_delivery(other, now, lease_seconds=1) is None
    with sessions.begin() as db:
        expire_deliveries(db, now + timedelta(seconds=2))
        assert claim_delivery(db, now + timedelta(seconds=2), lease_seconds=1) is None
        assert not finish_delivery(db, claim, provider_id="late", max_attempts=4)
    report = client.get(f"/api/runs/{run_id}/report").json()
    assert report["status"] == "succeeded" and report["delivery"]["n0"]["status"] == "unknown"


def test_delivery_requires_finished_report_and_cancels_failed_run(api, caplog, monkeypatch):
    client, sessions = api
    logger = logging.getLogger("cyberflow.events")
    monkeypatch.setattr(logger, "handlers", [caplog.handler])
    caplog.set_level(logging.INFO, logger=logger.name)
    _prepare_sources(monkeypatch)
    graph = _graph(("notify_email", {"recipients": ["dev@example.org"]}))
    run_id = enqueue_for_worker(api, graph.model_dump())
    settings = Settings(notify_email_recipients=["dev@example.org"])
    assert run_next(sessions, settings, allowed_repository_urls(settings.allowed_repositories))
    with sessions.begin() as db:
        db.get(ScanRun, run_id).status = "running"
    assert not process_next(sessions, settings)
    with sessions.begin() as db:
        db.get(ScanRun, run_id).status = "failed"
    assert not process_next(sessions, settings)
    report = client.get(f"/api/runs/{run_id}/report").json()
    assert report["delivery"]["n0"]["status"] == "cancelled"
    events = [json.loads(record.getMessage()) for record in caplog.records if record.name == logger.name]
    [recovered] = [event for event in events if event["action"] == "delivery.recover" and event["run_id"] == str(run_id)]
    assert recovered["result"] == "cancelled"
    assert recovered["delivery_id"] == report["delivery"]["n0"]["recipients"][0]["id"]
    workspace = client.get("/api/me").raise_for_status().json()["id"]
    rows = client.get(f"/api/workspaces/{workspace}/logs", params={"run_id": str(run_id)}).raise_for_status().json()["items"]
    recovery = next(row for row in rows if row["action"] == "delivery.recover")
    assert recovery["result"] == "cancelled" and recovery["delivery_id"] == recovered["delivery_id"]
