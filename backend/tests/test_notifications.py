import json
import threading
from email import message_from_bytes, policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.app.analyzers.models import Finding
from backend.app.devtools.mail_sink import SmtpSink
from backend.app.notifications import EmailChannel, NotifierError, TelegramChannel, build_message
from backend.app.reports import RunReport
from backend.app.schemas.contracts import Graph
from backend.app.workflows.validation import validate_draft, validate_graph

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
        else:
            status, payload = 200, {"ok": True, "result": {}}
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
