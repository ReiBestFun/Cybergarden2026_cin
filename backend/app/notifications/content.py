"""What a notification contains: a short summary plus the report files."""

from collections import Counter
from urllib.parse import urlsplit

from ..analyzers.models import ScanError, Warning
from ..reports import RunReport, submission_json
from .base import Attachment, Message

_SEVERITIES = ("critical", "high", "medium", "low", "info")
_CATEGORIES = {"secrets": "SECRET", "sca": "SCA", "sast": "SAST"}
_STATUS = {"succeeded": "проверка завершена", "partial": "проверка завершена частично", "failed": "проверка не выполнена"}


def build_slack_message(report: RunReport, *, run_id: str, app_base_url: str) -> Message:
    try:
        base = urlsplit(app_base_url)
        if (base.scheme not in {"http", "https"} or not base.hostname
                or base.username is not None or base.password is not None or base.query or base.fragment
                or any(character.isspace() or character in "<>" for character in app_base_url)):
            raise ValueError
    except ValueError:
        raise ScanError(Warning("SLACK_REPORT_URL_INVALID", "Проверьте адрес APP_BASE_URL для ссылки Slack.")) from None
    subject = "CyberFlow: результаты проверки безопасности"
    summary = report.summary
    text = "\n".join((
        subject,
        f"Статус: {_STATUS[report.status]}",
        f"Находок: {summary.total} · критических: {summary.critical_count} · высоких: {summary.high_count}",
        f"Отчёт: {app_base_url.rstrip('/')}/results?run={run_id}",
    ))
    return Message(subject, text)


def readable_summary(report: RunReport, *, repository: str, results_url: str, attached: bool = False) -> str:
    severities = Counter(finding.severity for finding in report.findings)
    categories = Counter(finding.category for finding in report.findings)
    lines = [
        "CyberFlow: результаты проверки безопасности",
        f"Репозиторий: {repository}" + (f" @ {report.sha[:7]}" if report.sha else ""),
        f"Статус: {_STATUS[report.status]}",
        f"Находок: {len(report.findings)}",
    ]
    if report.findings:
        lines.append("По критичности: " + ", ".join(
            f"{severity.upper()} {severities[severity]}" for severity in _SEVERITIES if severities[severity]
        ))
        lines.append("По категориям: " + ", ".join(
            f"{label} {categories[category]}" for category, label in _CATEGORIES.items() if categories[category]
        ))
    if report.status == "partial":
        for node in report.nodes:
            if node.warning:
                lines.append(f"{_CATEGORIES[node.category]} — {node.warning.message}")
            elif node.result and node.result.coverage.status == "partial":
                lines.append(f"{_CATEGORIES[node.category]} — частичное покрытие.")
    lines.extend(("", f"Отчёт: {results_url}"))
    if attached:
        lines.append("JSON с находками приложен.")
    return "\n".join(lines)


def build_message(
    report: RunReport, *, run_id: str, repository: str, app_base_url: str, attach_json: bool = True,
    max_attachment_bytes: int | None = None, summary_only: bool = False,
) -> Message:
    short_sha = report.sha[:7] if report.sha else "run"
    results_url = f"{app_base_url.rstrip('/')}/results?run={run_id}"
    if summary_only:
        summary = report.summary
        title = "CyberFlow: сводка проверки"
        text = "\n".join((
            title, f"Результат анализа: {_STATUS[report.status]}",
            f"Находок: {summary.total} · HIGH: {summary.high_count} · CRITICAL: {summary.critical_count}",
            f"Отчёт: {results_url}",
        ))
        return Message(title, text)
    total = len(report.findings)
    subject = f"CyberFlow: {total} находок в {repository.rstrip('/').rsplit('/', 1)[-1]} ({short_sha})"
    attachments: tuple[Attachment, ...] = ()
    if attach_json and report.status != "failed":
        content = submission_json(report).encode("utf-8")
        if max_attachment_bytes is None or len(content) <= max_attachment_bytes:
            attachments = (Attachment(f"findings-{short_sha}.json", content, "application/json"),)
    body = readable_summary(report, repository=repository, results_url=results_url, attached=bool(attachments))
    return Message(subject, body, attachments)
