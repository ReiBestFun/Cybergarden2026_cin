"""Generate explanations and resolve their references from the original scan."""

import json
import re
from collections import defaultdict

from pydantic import BaseModel, ConfigDict, Field

from .analyzers.models import Finding
from .config import Settings
from .llm import ModelError, complete_json
from .reports import RunReport


class ReportError(Exception):
    pass


class Advice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    group: int
    risk: str = Field(min_length=1, max_length=350)
    fix: str = Field(min_length=1, max_length=350)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Advice] = Field(max_length=6)


_SEVERITIES = ("critical", "high", "medium", "low", "info")
_PROMPT = """Объясни разработчику результаты статического анализа по-русски.
Для каждой группы дай одно краткое объяснение риска и одно конкретное исправление.
Верни только JSON вида {"items":[{"group":1,"risk":"...","fix":"..."}]}.
Используй только факты из входа. Не добавляй пути, имена файлов, номера строк,
версии, ссылки и идентификаторы уязвимостей. Приложение само подставит ссылки."""
_REFERENCE = re.compile(
    r"CVE\s*[-–:]?\s*\d|GHSA-|https?://|[/\\]|\b[\w-]+\.(?:kt|kts|java|properties|ya?ml|xml|json)\b"
    r"|(?:строк\w*|line)\s*[#:№]?\s*\d", re.I
)


def explain_report(bundle: RunReport, settings: Settings) -> str:
    if not bundle.findings:
        return "В проверенной части проекта находок нет."
    grouped: dict[tuple, list[Finding]] = defaultdict(list)
    for item in bundle.findings:
        grouped[item.severity, item.rule_id, item.metadata.get("advisory_id")].append(item)
    ordered = sorted(grouped.values(), key=lambda items: _SEVERITIES.index(items[0].severity))
    selected = ordered[:6]
    facts = [
        {"group": i, "rule": group[0].rule_id, "severity": group[0].severity,
         "title": group[0].title, "occurrences": len(group)}
        for i, group in enumerate(selected, 1)
    ]
    try:
        answer = complete_json(settings, Answer, system=_PROMPT, user=json.dumps(facts, ensure_ascii=False))
    except ModelError as error:
        raise ReportError(str(error)) from None
    if sorted(item.group for item in answer.items) != list(range(1, len(selected) + 1)):
        raise ReportError("Ответ модели не соответствует группам находок.")
    lines = []
    for advice in sorted(answer.items, key=lambda item: item.group):
        if (not advice.risk.strip() or not advice.fix.strip()
                or _REFERENCE.search(advice.risk + " " + advice.fix)):
            raise ReportError("Ответ модели содержит пустой текст или непроверенную ссылку.")
        group = selected[advice.group - 1]
        finding = group[0]
        location = finding.path + (f":{finding.line}" if finding.line else "")
        if advisory := finding.metadata.get("advisory_id"):
            location += f" · {advisory}"
        lines.append(f"{finding.severity.upper()} · {finding.title} ({len(group)}) · {location}\n"
                     f"{advice.risk.strip()} {advice.fix.strip()}")
    if len(ordered) > len(selected):
        lines.append(f"Ещё групп в полном отчёте: {len(ordered) - len(selected)}.")
    return "\n\n".join(lines)
