"""Find Kotlin logger calls that directly expose credentials."""

import re
from pathlib import PurePosixPath

from tree_sitter import Node

from . import kotlin as kt
from .models import Finding, Scan, ScanResult, Snapshot

_LEVELS = {"trace", "debug", "info", "warn", "error", "fatal"}
_CREDENTIAL = re.compile(r"(?:^|_)(?:password|passwd|pwd|token|authorization)(?:_header)?$")


def accepts_logging_source(path: PurePosixPath) -> bool:
    return path.suffix == ".kt"


def _words(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


def _sensitive_name(name: str) -> bool:
    words = _words(name)
    return bool(_CREDENTIAL.search(words)) and not {"masked", "redacted"} & set(words.split("_"))


def _logger(source: kt.KotlinFile, receiver: Node) -> bool:
    name = kt.text(receiver).rsplit(".", 1)[-1]
    if _words(name).split("_")[-1] in {"log", "logger"}:
        return True
    factory = kt.call(source.resolve(receiver))
    return bool(factory and factory.receiver and (
        kt.text(factory.receiver).rsplit(".", 1)[-1], factory.name
    ) in {("LoggerFactory", "getLogger"), ("KotlinLogging", "logger"), ("LogManager", "getLogger")})


def _header_constant(node: Node) -> bool:
    return kt.text(node).rsplit(".", 2)[-2:] in (
        ["HttpHeaders", "AUTHORIZATION"], ["HttpHeaders", "Authorization"],
    )


def _authorization_key(source: kt.KotlinFile, key: Node) -> bool:
    key = source.resolve(key)
    return (kt.literal(key) or "").lower() == "authorization" or _header_constant(key)


def _exposes_credential(source: kt.KotlinFile, arguments: list[Node]) -> bool:
    pending = arguments.copy()
    seen: set[Node] = set()

    def identifier(name: str, use: Node) -> bool:
        value = source.binding(name, use)
        if value is not None:
            pending.append(value)
            origin = source.resolve(value)
            if _header_constant(origin):
                return False
            transform = kt.call(origin)
            if transform and _words(transform.name).split("_")[0] in {"mask", "masked", "redact", "redacted", "sanitize"}:
                return False
            literal = kt.literal(origin)
            if literal is not None and (not literal.strip("*") or literal.lower() in {"redacted", "[redacted]"}):
                return False
        return _sensitive_name(name)

    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        parts = kt.children(node)
        if node.type == "identifier":
            if identifier(kt.text(node), node):
                return True
        elif node.type == "navigation_expression" and parts:
            if not _header_constant(node) and _sensitive_name(kt.text(parts[-1])):
                return True
        elif node.type in kt.STRING_TYPES:
            for index, part in enumerate(parts):
                if part.type == "interpolation":
                    pending.extend(kt.children(part))
                elif part.type == "string_content" and kt.text(part) == "$" and index + 1 < len(parts):
                    name = re.match(r"\w+", kt.text(parts[index + 1]))
                    if name and identifier(name.group(), part):
                        return True
        elif node.type == "parenthesized_expression" or (node.type == "binary_expression" and kt.operator(node) in {"+", "?:"}):
            pending.extend(parts)
        elif node.type == "index_expression" and len(parts) == 2:
            if _authorization_key(source, parts[1]):
                return True
        elif call := kt.call(node):
            if call.name in {"getHeader", "getFirst", "get", "header"} and call.args and _authorization_key(source, call.args[0]):
                return True
            if call.name.startswith("get") and not call.args and _sensitive_name(call.name):
                return True
            if call.name == "toString" and call.receiver is not None:
                pending.append(call.receiver)
    return False


def scan_sensitive_logging(snapshot: Snapshot) -> ScanResult:
    scan = Scan(snapshot, accepts_logging_source)
    findings = []
    for path, data in scan.files.items():
        source = kt.KotlinFile(data)
        if source.root.has_error:
            scan.skip(path, "KOTLIN_PARSE_ERROR", "Файл Kotlin разобран частично.")
        for node in kt.walk(source.root, "call_expression"):
            call = kt.call(node)
            if not call or call.name not in _LEVELS or not call.receiver or not _logger(source, call.receiver):
                continue
            arguments = list(call.args)
            if call.block:
                arguments.extend(kt.children(call.block)[-1:])
            if _exposes_credential(source, arguments):
                findings.append(Finding(
                    rule_id="sast.sensitive-logging", category="sast", severity="high",
                    title="Чувствительные данные в журнале",
                    description="Вызов логгера передаёт пароль, токен или значение заголовка Authorization.",
                    remediation="Удалите чувствительное значение из сообщения или замаскируйте его до вызова логгера.",
                    path=path, line=kt.line(node), end_line=kt.end_line(node), evidence="***",
                    metadata={"class": "sensitive-logging"},
                ))
    return scan.result("sast", "Проверка вызовов логгера в Kotlin", findings)
