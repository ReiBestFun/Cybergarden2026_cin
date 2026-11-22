"""Run explicitly with pytest against a dedicated, empty local acceptance database."""

import json
import os
from collections import Counter
from email import message_from_bytes, policy
from html import unescape
from pathlib import Path
from secrets import randbits

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.analyzers import Snapshot, scan_password_hashing, scan_spring_config
from backend.app.analyzers.sensitive_logging import scan_sensitive_logging
from backend.app.db.models import ScanRun
from backend.app.devtools.mail_sink import SmtpSink
from backend.app.integrations.repository import TRAINING_REPOSITORY
from backend.app.notification_worker import process_next
from backend.app.queue_worker import run_next
from backend.tests.test_api import ORIGIN, api, create_workflow, edge, login, node

SHA = "fe1fcbe212669783480666b9b60147c8a8c6eef4"
RECIPIENT = "review@example.test"


def _graph():
    scanners = ["scan_secrets", "scan_sca", "scan_sast"]
    graph = {
        "nodes": [node("entry", "manual_trigger"), node("repo", "repository"),
                  *[node(name, name) for name in scanners], node("merge", "merge_findings"),
                  node("advice", "ai_report"), node("mail", "notify_email")],
        "edges": [edge("start", "entry", "params", "repo", "params"),
                  *[edge("input-" + name, "repo", "snapshot", name, "snapshot") for name in scanners],
                  *[edge("result-" + name, name, "result", "merge", "results") for name in scanners],
                  edge("advise", "merge", "bundle", "advice", "bundle"),
                  edge("send", "advice", "report", "mail", "report")],
    }
    positions = {"entry": (0, 160), "repo": (240, 160), "merge": (720, 160),
                 "advice": (960, 160), "mail": (1200, 160)}
    positions.update({name: (480, index * 160) for index, name in enumerate(scanners)})
    for item in graph["nodes"]:
        x, y = positions[item["id"]]
        item["position"] = {"x": x, "y": y}
    return graph


def _synthetic():
    files = {"application.properties": "server.error.include-stacktrace=always\n",
             "Demo.kt": "fun fingerprint(password: String) = DigestUtils.md5Hex(password)\n"
                        "fun trace(token: String) { logger.info(token) }\n"}
    snapshot = Snapshot("a" * 40, files)
    findings = [finding for scan in (scan_spring_config, scan_password_hashing, scan_sensitive_logging)
                for finding in scan(snapshot).findings]
    assert {row.rule_id for row in findings} == {
        "sast.error-stacktrace", "sast.weak-password-hash", "sast.sensitive-logging",
    }
    return {row.rule_id: f"{row.path}:{row.line}" for row in findings}


def _verify_exports(machine, readable):
    rows = machine["findings"]
    expected = Counter()
    for item in rows:
        if item["category"] == "SCA":
            place = f"{item['path']} · {item['group']}:{item['artifact']}:{item['version']} · {item['advisoryId']}"
        else:
            place = f"{item['path']}:{item['line']}"
            if "endLine" in item:
                place += f"–{item['endLine']}"
        expected[item["category"], item.get("severity", "INFO"), place, item["remediation"]] += 1
    actual = Counter()
    for line in readable.splitlines():
        if line.startswith(("| SECRET |", "| SAST |", "| SCA |")):
            category, severity, _, place, _, fix = map(unescape, line[2:-2].split(" | "))
            actual[category, severity, place, fix] += 1
    assert expected and actual == expected


def test_local_acceptance(api, monkeypatch, tmp_path):
    owner, sessions = api
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(ScanRun)) == 0, "Use an empty acceptance database"
    team = owner.post("/api/workspaces", json={"name": "Демонстрационная команда"}).raise_for_status().json()["id"]
    other_team = owner.post("/api/workspaces", json={"name": "Вторая команда"}).raise_for_status().json()["id"]
    base = f"/api/workspaces/{team}"
    base_other = f"/api/workspaces/{other_team}/logs"
    project = owner.post("/api/projects", json={"name": "Учебный проект", "workspace_id": team,
                                                "repository_url": TRAINING_REPOSITORY}).raise_for_status().json()["id"]
    source = create_workflow(owner, project, _graph())
    template = owner.post("/api/templates", json={"name": "Проверка репозитория", "workflow_id": source,
                                                 "workspace_id": team}).raise_for_status().json()["id"]
    invite = owner.post(base + "/invitations", json={"role": "owner"}).raise_for_status().json()
    previous = owner.get(base + "/logs/policy").json()["policy"]
    owner.put(base + "/logs/policy", json={**previous, "audit_days": 45, "member_view": True}).raise_for_status()
    messages = []
    with TestClient(owner.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as colleague, \
            SmtpSink(0, lambda sender, recipients, data: messages.append((recipients, data))).start() as smtp:
        login(colleague, monkeypatch, randbits(48) + 1, login_name="demo-colleague")
        assert owner.get("/api/me").json()["id"] != colleague.get("/api/me").json()["id"]
        colleague.post(base + "/join", json={"token": invite["token"]}).raise_for_status()
        assert colleague.get(base + "/logs/policy").json()["policy"]["audit_days"] == 45
        assert all(colleague.get(base_other + path).status_code == 404 for path in ("", "/policy", "/export"))
        copy = colleague.post(f"/api/templates/{template}/copy", json={"project_id": project}).raise_for_status().json()
        graph = copy["graph"]
        next(n for n in graph["nodes"] if n["id"] == "mail")["config"] = {"recipients": [RECIPIENT], "attach_json": True}
        wf = "/api/workflows/" + copy["id"]
        colleague.patch(wf, json={"graph": graph}).raise_for_status()
        settings = owner.app.state.settings.model_copy(update={
            "smtp_host": "127.0.0.1", "smtp_port": smtp.server_address[1], "smtp_security": "none",
            "smtp_username": None, "smtp_password": None, "smtp_from": "demo@example.test",
            "notify_email_recipients": [RECIPIENT], "llm_provider": "ollama", "llm_api_key": None,
            "llm_base_url": "http://127.0.0.1:11434", "llm_model": os.getenv("DEMO_MODEL", "mistral:latest"),
            "llm_timeout_seconds": 120, "sca_catalog_path": None,
        })
        run_id = colleague.post(wf + "/runs", json={"ref": SHA}).raise_for_status().json()["id"]
        path = "/api/runs/" + run_id
        assert run_next(sessions, settings, owner.app.state.allowed_repositories)
        detail = colleague.get(path).raise_for_status().json()
        states = {n["type"]: n["status"] for n in detail["nodes"]}
        assert all(state == "succeeded" for state in states.values()), [(n["type"], n["error"]) for n in detail["nodes"]]
        assert detail["commit_sha"] == SHA
        assert process_next(sessions, settings)
        detail = colleague.get(path).json()
        report = colleague.get(path + "/report").raise_for_status().json()
        assert report["llm"]["status"] == "succeeded" and report["llm"]["text"]
        assert report["delivery"]["mail"]["status"] == "accepted"
        [(recipients, raw)] = messages
        message = message_from_bytes(raw, policy=policy.default)
        assert recipients == [RECIPIENT] and run_id in message.get_body().get_content()
        machine = colleague.get(path + "/export?format=json").raise_for_status().content.decode("utf-8")
        readable = colleague.get(path + "/export?format=markdown").raise_for_status().content.decode("utf-8")
        _verify_exports(json.loads(machine), readable)
        [attachment] = message.iter_attachments()
        def finding_set(document):
            return Counter(json.dumps(item, sort_keys=True) for item in document["findings"])

        assert finding_set(json.loads(attachment.get_payload(decode=True))) == finding_set(json.loads(machine))
        assert run_id in {row["run_id"] for row in owner.get(base + "/logs?kind=diagnostic").json()["items"]}
        summary = {"run": run_id, "commit": SHA, "status": detail["status"], "nodes": states, "findings": report["summary"],
                   "model": report["llm"]["model"], "smtp": {"subject": str(message["Subject"]),
                   "to": recipients, "text": message.get_body().get_content(), "attachment": attachment.get_filename()},
                   "synthetic": _synthetic()}
        output = Path(os.environ.get("DEMO_OUTPUT", tmp_path))
        output.mkdir(parents=True, exist_ok=True)
        for filename, contents in [("findings.json", machine), ("report.md", readable),
                                   ("result.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")]:
            (output / filename).write_text(contents, encoding="utf-8")
        smtp.shutdown()
