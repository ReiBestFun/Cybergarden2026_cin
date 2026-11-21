"""Check the evidence for individual findings against the scanned snapshot."""

from __future__ import annotations

import re
from collections import defaultdict
from functools import cache
from pathlib import PurePosixPath

import yaml
from tree_sitter import Node

from . import kotlin as kt, sast, sca, secrets
from .models import Finding, FindingVerification, Scan, Snapshot

_MISSING_SOURCE = FindingVerification("needs_review", "SOURCE_UNAVAILABLE", "Исходный фрагмент недоступен.")
_UNSUPPORTED = FindingVerification("needs_review", "RULE_NOT_VERIFIED", "Для этого правила пока нет дополнительной проверки.")
_SQL_REVIEW = FindingVerification("needs_review", "SQL_FLOW_UNVERIFIED", "Не удалось независимо подтвердить локальный поток данных в SQL.")
_SECRET_REVIEW = FindingVerification("needs_review", "SECRET_CONTEXT_UNVERIFIED", "Не удалось однозначно определить источник значения.")
_SCA_REVIEW = FindingVerification("needs_review", "SCA_LOCK_UNVERIFIED", "Lock-файл не подтверждает однозначную версию для всех указанных конфигураций.")
_JDBC = {"org.springframework.jdbc.core.JdbcTemplate", "org.springframework.jdbc.core.JdbcOperations"}
_COMPLEX_SQL_LITERAL = re.compile(r"""['"\\`$;]|--|/\*""")
_CLASS_LITERAL = re.compile(r"[\w.]+::class\.java")
_EXPLICIT_PLACEHOLDER = re.compile(r"<(?:your|replace|insert|placeholder)[-_ ][\w -]+>|\*{3,}", re.IGNORECASE)
_SINGLE_QUOTED_ENV = re.compile(r"\s*(?:export\s+)?[\w.-]+\s*[:=]\s*'")


def _direct_sql_input(sql: Node, inputs: frozenset[str]) -> bool:
    if sql.type == "identifier":
        return kt.text(sql) in inputs
    if sql.type == "binary_expression" and kt.operator(sql) == "+":
        return any(_direct_sql_input(part, inputs) for part in kt.children(sql))
    text = kt.string_content(sql)
    return text is not None and any(
        (match["name"] or (match["braced"] or "").strip()) in inputs for match in kt.TEMPLATE.finditer(text)
    )


def _verify_sql(function: sast.Function, call: kt.Call, sql: Node) -> FindingVerification:
    if call.receiver is None or function.receiver_type(call.receiver) not in _JDBC:
        return _SQL_REVIEW
    literal = kt.literal(call.args[0])
    value_start = None
    if call.name in {"update", "queryForMap"}:
        value_start = 1
    elif call.name in {"queryForObject", "queryForList"} and len(call.args) > 1:
        if _CLASS_LITERAL.fullmatch(kt.text(call.args[1])):
            value_start = 2
    if (
        value_start is not None and len(call.args) > value_start
        and literal is not None and not _COMPLEX_SQL_LITERAL.search(literal)
        and literal.count("?") == len(call.args) - value_start
    ):
        return FindingVerification(
            "false_positive", "SQL_PARAMETERS_BOUND",
            "JdbcTemplate получает фиксированный SQL-шаблон со связанными параметрами.",
        )
    inputs = (function.inputs & function.request_parameters) - function.scalar_parameters
    if sql == call.args[0] and _direct_sql_input(sql, inputs):
        return FindingVerification(
            "confirmed", "SQL_REQUEST_INPUT_IN_TEXT",
            "Параметр Spring-обработчика напрямую участвует в формировании текста SQL.",
        )
    return _SQL_REVIEW


def _verify_lock(
    finding: Finding, catalog: sca.VulnerabilityCatalog | None,
    entries: list[tuple[str, set[str]]],
) -> FindingVerification:
    if catalog is None:
        return _SCA_REVIEW
    meta = finding.metadata
    advisory = next((
        item for item in catalog.index.get((meta["group"], meta["artifact"]), ())
        if item.id == meta["advisory_id"]
    ), None)
    if advisory is None:
        return _SCA_REVIEW
    contexts = meta["configurations"].split(", ")
    versions = [{version for version, names in entries if context in names} for context in contexts]
    if any(len(matches) != 1 for matches in versions):
        return _SCA_REVIEW
    resolved = set().union(*versions)
    try:
        affected = {version for version in resolved if any(bounds.contains(sca._version(version)) for bounds in advisory.ranges)}
    except ValueError:
        return _SCA_REVIEW
    if not affected:
        return FindingVerification(
            "false_positive", "SCA_VERSION_OUTSIDE_RANGE",
            "Версии из lock-файла для указанных конфигураций вне диапазона advisory.",
        )
    if affected == resolved == {meta["version"]}:
        return FindingVerification(
            "confirmed", "SCA_LOCK_VERSION_AFFECTED",
            "Lock-файл подтверждает версию в диапазоне advisory для указанных конфигураций.",
        )
    return _SCA_REVIEW


def _verify_secret_value(
    value: str, *, reference: re.Pattern[str] | None = None, expandable: bool = True,
) -> FindingVerification:
    clean = secrets._CREDENTIAL_PREFIX.sub("", value).strip()
    match = reference.fullmatch(clean) if reference is not None else None
    if match is not None and not expandable:
        return _SECRET_REVIEW
    if match is not None and not match["fallback"]:
        return FindingVerification("false_positive", "SECRET_ENV_REFERENCE", "Указана ссылка на переменную без встроенного значения.")
    if match is not None:
        clean = match["fallback"].strip()
    if _EXPLICIT_PLACEHOLDER.fullmatch(clean):
        return FindingVerification("false_positive", "SECRET_PLACEHOLDER", "Найдена явная заглушка.")
    if secrets._concrete(clean):
        return FindingVerification(
            "confirmed", "SECRET_FALLBACK" if match is not None else "SECRET_LITERAL",
            "В исходниках записано значение по умолчанию." if match is not None else "В исходниках записано конкретное значение.",
        )
    return _SECRET_REVIEW


def _verify_kotlin_secret(source: kt.KotlinFile, name: str, node: Node) -> FindingVerification:
    role = secrets._role(name)
    fallback = secrets._fallback(source, node, role)
    if fallback is not None:
        verdict = _verify_secret_value(fallback)
        if verdict.status == "confirmed":
            return FindingVerification("confirmed", "SECRET_FALLBACK", "В исходниках записано значение по умолчанию.")
        return verdict
    if role is None:
        return _SECRET_REVIEW
    call = kt.call(node)
    if (
        call is not None and call.name == "getenv" and call.receiver is not None
        and kt.text(call.receiver) == "java.lang.System"
    ):
        return FindingVerification("false_positive", "SECRET_ENV_REFERENCE", "Значение читается из окружения без встроенного значения.")
    if kt.string_content(node) is not None or (node.type == "binary_expression" and kt.operator(node) == "+"):
        value = secrets._constant(source, node)
        if value is not None:
            return _verify_secret_value(value)
    return _SECRET_REVIEW


def verify_findings(
    snapshot: Snapshot, findings: tuple[Finding, ...], catalog: sca.VulnerabilityCatalog | None = None,
) -> tuple[FindingVerification, ...]:
    secret_locations = defaultdict(set)
    for finding in findings:
        if finding.category == "secrets" and PurePosixPath(finding.path).suffix not in {".kt", ".kts"}:
            secret_locations[finding.path].add((finding.line, finding.end_line or finding.line))

    @cache
    def sql_calls(path: str):
        source = kt.KotlinFile(snapshot.files[path])
        if source.root.has_error:
            return {}
        functions, _, _ = sast._index(path, source)
        return {
            (str(kt.line(node)), str(node.start_point.column)): (function, call, sql)
            for function in functions for node, call, sql, _ in sast._sql_calls(function, all_sinks=True)
        }

    @cache
    def lock_entries(path: str):
        content = snapshot.files.get(path)
        if content is None:
            return {}
        scan = Scan(Snapshot(snapshot.sha, {path: content}), sca.accepts_sca_source)
        entries = sca._lock_entries(scan, path, content)
        return {} if scan.partial else entries

    @cache
    def kotlin_properties(path: str):
        source = kt.KotlinFile(snapshot.files[path])
        properties = {} if source.root.has_error else {
            str(node.start_byte): assignment
            for node in kt.walk(source.root, "property_declaration")
            if (assignment := kt.property_value(node)) is not None
        }
        return source, properties

    @cache
    def secret_spans(path: str):
        name = PurePosixPath(path)
        content = snapshot.files[path]
        locations = secret_locations[path]
        spans = defaultdict(list)
        dotenv = secrets._is_dotenv(name)
        reference = secrets._DOTENV_REFERENCE if dotenv else secrets._ENV_REFERENCE
        lines = content.splitlines() if dotenv else ()
        try:
            for keys, value, start, end in secrets._configuration_entries(name, content):
                if (start, end) not in locations:
                    continue
                if not (
                    keys and secrets._role(keys[-1])
                    or secrets._configuration_finding(path, keys, value, start, end, dotenv=dotenv)
                ):
                    continue
                expandable = not dotenv or not _SINGLE_QUOTED_ENV.match(lines[start - 1])
                spans[start, end].append(_verify_secret_value(value, reference=reference, expandable=expandable))
        except (ValueError, RecursionError, yaml.YAMLError):
            return {}
        return spans

    def verify(finding: Finding) -> FindingVerification:
        if finding.path not in snapshot.files:
            return _MISSING_SOURCE
        if finding.rule_id == "sast.sql-injection":
            anchor = finding.metadata.get("sink_line"), finding.metadata.get("sink_column")
            call = sql_calls(finding.path).get(anchor)
            return _verify_sql(*call) if call is not None else _SQL_REVIEW
        if finding.rule_id == "sca.maven-advisory":
            if finding.metadata.get("version_source") != "lockfile":
                return FindingVerification(
                    "needs_review", "SCA_DECLARED_VERSION",
                    "В отчёте указана объявленная версия. Итоговая версия Gradle для этой конфигурации не подтверждена.",
                )
            path = PurePosixPath(finding.path).with_name("gradle.lockfile").as_posix()
            coordinate = finding.metadata["group"], finding.metadata["artifact"]
            return _verify_lock(finding, catalog, lock_entries(path).get(coordinate, []))
        if finding.category == "secrets":
            if PurePosixPath(finding.path).suffix in {".kt", ".kts"}:
                if (offset := finding.metadata.get("property_offset")) is not None:
                    source, properties = kotlin_properties(finding.path)
                    if assignment := properties.get(offset):
                        return _verify_kotlin_secret(source, *assignment)
                return _SECRET_REVIEW
            matches = secret_spans(finding.path).get((finding.line, finding.end_line or finding.line), ())
            return matches[0] if len(matches) == 1 else _SECRET_REVIEW
        return _UNSUPPORTED

    return tuple(verify(finding) for finding in findings)
