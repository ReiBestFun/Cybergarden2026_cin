"""Build machine-readable and human-readable reports from the same findings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from typing import Literal

from .analyzers.models import Category, Finding, ScanResult, Warning, finding_key, safe_path

_CATEGORIES = {"secrets": "SECRET", "sca": "SCA", "sast": "SAST"}


@dataclass(frozen=True)
class Summary:
    total: int
    high_count: int
    critical_count: int


@dataclass(frozen=True)
class NodeResult:
    category: Category
    status: Literal["succeeded", "failed", "skipped"]
    result: ScanResult | None = None
    warning: Warning | None = None


@dataclass(frozen=True)
class RunReport:
    sha: str
    status: Literal["succeeded", "partial", "failed"]
    nodes: tuple[NodeResult, ...]
    findings: tuple[Finding, ...]

    @property
    def summary(self) -> Summary:
        return Summary(
            len(self.findings),
            sum(finding.severity == "high" for finding in self.findings),
            sum(finding.severity == "critical" for finding in self.findings),
        )


def merge_findings(sha: str, nodes: tuple[NodeResult, ...]) -> RunReport:
    results = [
        node.result for node in nodes if node.status == "succeeded" and node.result is not None
    ]
    if not results:
        return RunReport(sha, "failed", nodes, ())
    partial = any(result.coverage.status == "partial" for result in results) or any(
        node.status == "failed" or (
            node.status == "skipped" and (node.warning is None or node.warning.code != "INACTIVE_BRANCH")
        ) for node in nodes
    )
    findings: dict[tuple, Finding] = {}
    for result in results:
        for finding in result.findings:
            findings.setdefault(finding_key(finding), finding)
    ordered = tuple(sorted(
        findings.values(), key=lambda item: (item.category, item.path, item.line or 0, item.rule_id)
    ))
    return RunReport(sha, "partial" if partial else "succeeded", nodes, ordered)


def submission_json(report: RunReport) -> str:
    if report.status == "failed":
        raise ValueError("Выгрузка находок требует хотя бы одной завершившейся проверки.")
    findings = []
    for finding in report.findings:
        if not safe_path(finding.path):
            raise ValueError("Недопустимый путь находки.")
        entry = {
            "category": _CATEGORIES[finding.category],
            "path": finding.path,
            "description": finding.description,
            "remediation": finding.remediation,
        }
        if finding.severity != "info":
            entry["severity"] = finding.severity.upper()
        metadata = finding.metadata
        if finding.category == "sca":
            entry.update(
                group=metadata["group"], artifact=metadata["artifact"],
                version=metadata["version"], advisoryId=metadata["advisory_id"],
            )
        else:
            if finding.line is None or finding.line < 1:
                raise ValueError("Для SECRET и SAST требуется номер строки.")
            entry.update({"class": metadata["class"], "line": finding.line})
            if finding.end_line is not None and finding.end_line > finding.line:
                entry["endLine"] = min(finding.end_line, finding.line + 14)
        findings.append(entry)
    return json.dumps({"findings": findings}, ensure_ascii=False, indent=2) + "\n"


def _cell(value: str) -> str:
    value = escape(value.replace("\n", " ").replace("\r", " "))
    return value.replace("|", "&#124;").replace("[", "&#91;").replace("]", "&#93;").replace(chr(96), "&#96;")


def _warning_text(warning: Warning) -> str:
    location = warning.path or warning.field or ""
    if warning.path and warning.line is not None:
        location += f":{warning.line}"
    text = warning.message + (f" ({location})" if location else "")
    return _cell(f"{text} {warning.hint}" if warning.hint else text)


def readable_report(report: RunReport) -> str:
    status = {"succeeded": "Проверка завершена", "partial": "Проверка завершена частично", "failed": "Проверка не выполнена"}
    lines = ["# Отчёт статического анализа", ""]
    if report.sha:
        lines.extend((f"Коммит {report.sha}", ""))
    lines.extend((status[report.status], ""))
    for node in report.nodes:
        category = _CATEGORIES[node.category]
        if node.result is not None:
            result = node.result
            coverage = result.coverage
            state = "полное покрытие" if coverage.status == "complete" else "неполное покрытие"
            lines.append(
                f"- **{category}** — {state}, находок {len(result.findings)}, "
                f"файлов просмотрено {coverage.files_scanned}, пропущено {coverage.files_skipped}. {coverage.scope}."
            )
            for warning in result.warnings:
                lines.append(f"  - {_warning_text(warning)}")
        else:
            message = _warning_text(node.warning) if node.warning else "Проверка пропущена."
            lines.append(f"- **{category}** — {message}")
    if not report.findings:
        if report.status == "succeeded":
            lines.extend(("", "Находок нет."))
        if report.status == "partial":
            lines.extend(("", "В просмотренной части находок нет."))
        return "\n".join(lines) + "\n"

    lines.extend((
        "", "| Категория | Критичность | Находка | Место | Исправление |",
        "|---|---|---|---|---|",
    ))
    for finding in report.findings:
        location = finding.path
        detail = f"{finding.title}. {finding.description}"
        if finding.category == "sca":
            meta = finding.metadata
            location += f" · {meta['group']}:{meta['artifact']}:{meta['version']} · {meta['advisory_id']}"
        else:
            location += f":{finding.line}"
            if finding.end_line and finding.line and finding.end_line > finding.line:
                location += f"–{min(finding.end_line, finding.line + 14)}"
            if finding.category == "secrets":
                detail += " Значение скрыто."
        cells = (_CATEGORIES[finding.category], finding.severity.upper(), detail, location, finding.remediation)
        lines.append("| " + " | ".join(map(_cell, cells)) + " |")
    return "\n".join(lines) + "\n"
