"""What a notification contains: a short summary plus the report files."""

from collections import Counter

from ..reports import RunReport, submission_json
from .base import Attachment, Message

_SEVERITIES = ("critical", "high", "medium", "low", "info")
_CATEGORIES = {"secrets": "SECRET", "sca": "SCA", "sast": "SAST"}
_STATUS = {"succeeded": "проверка завершена", "partial": "проверка завершена частично", "failed": "проверка не выполнена"}


def readable_summary(report: RunReport, *, repository: str, results_url: str) -> str:
    """Human-readable report sent in the message body.

    TODO: placeholder for the readable report; it will be replaced by a full report
    (findings with explanations and fixes) once its format is decided.
    """
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
    lines.extend((
        "",
        "Подробный читаемый отчёт пока в разработке. Полный список находок — во вложенном JSON",
        f"и на странице результатов: {results_url}",
    ))
    return "\n".join(lines)


def build_message(
    report: RunReport, *, run_id: str, repository: str, app_base_url: str, attach_json: bool = True,
) -> Message:
    short_sha = report.sha[:7] if report.sha else "run"
    results_url = f"{app_base_url.rstrip('/')}/results?run={run_id}"
    total = len(report.findings)
    subject = f"CyberFlow: {total} находок в {repository.rstrip('/').rsplit('/', 1)[-1]} ({short_sha})"
    attachments: tuple[Attachment, ...] = ()
    if attach_json and report.status != "failed":
        # Same document as GET /api/runs/{id}/export?format=json: secrets are already masked.
        attachments = (Attachment(f"findings-{short_sha}.json", submission_json(report).encode("utf-8"), "application/json"),)
    return Message(subject, readable_summary(report, repository=repository, results_url=results_url), attachments)
