import json
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

from backend.app.analyzers.models import Finding, Snapshot
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
from backend.app.workflows import execution
from backend.app.workflows.validation import validate_draft, validate_graph
from backend.tests.test_api import ORIGIN, api, enqueue_for_worker, login

TOKEN = "123456:TEST-token-value"


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


def test_delivery_requires_finished_report_and_cancels_failed_run(api, monkeypatch):
    client, sessions = api
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
