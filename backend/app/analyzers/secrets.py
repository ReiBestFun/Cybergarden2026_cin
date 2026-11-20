"""Find embedded secrets and redact them before constructing a result."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Iterator

import yaml
from tree_sitter import Node
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from . import kotlin
from .models import Finding, Scan, ScanResult, Severity, Snapshot

_RULES: dict[str, tuple[Severity, str]] = {
    "password": ("high", "Встроенный пароль"),
    "api-key": ("medium", "Встроенный API-ключ"),
    "configuration-token": ("high", "Встроенный токен"),
    "secret-fallback": ("low", "Встроенный секрет по умолчанию"),
    "recoverable-token": ("medium", "Секрет собирается из строковых литералов"),
}
_CREDENTIAL_PREFIX = re.compile(r"^(?:Bearer|Basic)\s+", re.IGNORECASE)
_ENV_REFERENCE = re.compile(r"^\$(?:\{(?P<name>[A-Za-z_]\w*)(?::(?P<fallback>[^}]*))?\}|[A-Za-z_]\w*)$")
_PLACEHOLDER = re.compile(
    r"(?:<[^>]+>|\*+|x{3,}|(?:your|replace|insert|put|set)[-_ ].*|"
    r"(?:placeholder|example|dummy|sample)(?:[-_ ].*)?|changeme|change[-_ ]me|redacted|todo)",
    re.IGNORECASE,
)
_KEY_FORMAT = re.compile(
    r"(?:AKIA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{30,}|"
    r"github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----)"
)
_KEY_VALUE = re.compile(r"^\s*(?:export\s+)?([\w.-]+)\s*[:=]\s*(.*?)\s*$")


def _accepts(path: PurePosixPath) -> bool:
    return path.suffix in {".kt", ".kts", ".yaml", ".yml", ".properties", ".env"} or (
        path.name == ".env" or path.name.startswith(".env.")
    )


def _role(name: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    if normalized.endswith(("password", "passwd")):
        return "password"
    if normalized.endswith(("apikey", "accesskey")):
        return "api-key"
    if normalized.endswith(("token", "authorization", "clientsecret")) or normalized == "secret":
        return "configuration-token"
    return None


def _concrete(value: str) -> bool:
    value = _CREDENTIAL_PREFIX.sub("", value).strip()
    return bool(value) and not (
        _ENV_REFERENCE.fullmatch(value) or _PLACEHOLDER.fullmatch(value)
        or value.startswith("$(") or value.startswith("$env:")
    )


def _finding(kind: str, path: str, start: int, end: int) -> Finding:
    severity, title = _RULES[kind]
    return Finding(
        f"secret.{kind}", "secrets", severity, title,
        "Значение доступно любому, кто может читать репозиторий.",
        "Удалите значение из исходников, замените его и загружайте из хранилища секретов.",
        path, start, end, "***", {"class": kind},
    )


def _constant(
    source: kotlin.KotlinFile, node: Node, depth: int = 0
) -> str | None:
    if depth >= 12:
        return None
    node = source.resolve(node)
    value = kotlin.string_content(node)
    if value is not None:
        replacements = []
        start = 0
        for match in kotlin.TEMPLATE.finditer(value):
            name = match["name"] or match["braced"]
            if name is None:
                continue
            target = source.binding(name.strip(), node)
            resolved = _constant(source, target, depth + 1) if target is not None else None
            if resolved is None:
                return None
            replacements.extend((value[start:match.start()], resolved))
            start = match.end()
        replacements.append(value[start:])
        return "".join(replacements) if sum(map(len, replacements)) <= 8192 else None
    if node.type == "binary_expression" and kotlin.operator(node) == "+":
        left, right = (node.child_by_field_name(side) for side in ("left", "right"))
        if left is not None and right is not None:
            prefix = _constant(source, left, depth + 1)
            suffix = _constant(source, right, depth + 1)
            if prefix is not None and suffix is not None and len(prefix) + len(suffix) <= 8192:
                return prefix + suffix
    return None


def _fallback(source: kotlin.KotlinFile, node: Node, role: str | None) -> str | None:
    if node.type != "binary_expression" or kotlin.operator(node) != "?:":
        return None
    left, right = (node.child_by_field_name(side) for side in ("left", "right"))
    if left is None or right is None:
        return None
    call = kotlin.call(left)
    if call is None or call.name != "getenv" or call.receiver is None or not call.args:
        return None
    if kotlin.text(call.receiver) not in {"System", "java.lang.System"}:
        return None
    key = kotlin.literal(call.args[0])
    return _constant(source, right) if role or (key and _role(key)) else None


def _kotlin_findings(path: str, source: kotlin.KotlinFile) -> Iterator[Finding]:
    def check(node: Node, role: str | None, context: Node) -> Finding | None:
        origin = source.resolve(node)
        fallback = _fallback(source, origin, role)
        if fallback is not None and _concrete(fallback):
            return _finding("secret-fallback", path, kotlin.line(origin), kotlin.end_line(origin))
        value = _constant(source, origin)
        if value is None or not _concrete(value):
            return None
        if role is None and _KEY_FORMAT.search(value):
            role = "api-key"
        if role is None:
            return None
        anchor = context if origin == node else origin
        if kotlin.literal(origin) is None:
            role = "recoverable-token"
        return _finding(role, path, kotlin.line(anchor), kotlin.end_line(anchor))

    for node in kotlin.walk(source.root, "property_declaration"):
        value = kotlin.property_value(node)
        if value is not None and (finding := check(value[1], _role(value[0]), node)):
            yield finding

    for node in kotlin.walk(source.root, "assignment"):
        left, right = (node.child_by_field_name(side) for side in ("left", "right"))
        if left is not None and right is not None:
            role = _role(kotlin.text(left).rsplit(".", 1)[-1])
            if role and (finding := check(right, role, node)):
                yield finding
    for node in kotlin.walk(source.root, "infix_expression"):
        parts = kotlin.children(node)
        if len(parts) == 3 and kotlin.text(parts[1]) == "to":
            key = kotlin.literal(parts[0])
            if key and (role := _role(key)) and (finding := check(parts[2], role, node)):
                yield finding

    for node in kotlin.walk(source.root, "call_expression"):
        call = kotlin.call(node)
        if call is None:
            continue
        if call.name in {"header", "addHeader", "setHeader", "set", "add"} and len(call.args) >= 2:
            header = kotlin.literal(call.args[0])
            if header and (role := _role(header)):
                if finding := check(call.args[1], role, node):
                    yield finding
        if call.name in {"password", "setPassword", "setBearerAuth", "setApiKey"} and call.args:
            role = "configuration-token" if call.name == "setBearerAuth" else _role(call.name)
            if finding := check(call.args[0], role, node):
                yield finding
        arguments = kotlin.child(node, "value_arguments")
        if arguments is not None:
            for argument in kotlin.children(arguments):
                values = kotlin.children(argument)
                if len(values) == 2 and values[0].type == "identifier":
                    role = _role(kotlin.text(values[0]))
                    if role and (finding := check(values[1], role, argument)):
                        yield finding


def _configuration_finding(
    path: str, keys: tuple[str, ...], value: str, start: int, end: int
) -> Finding | None:
    value = value.strip()
    role = _role(keys[-1]) if keys else None
    if len(keys) >= 2 and keys[-1].lower() in {"value", "default"}:
        role = _role(keys[-2])
    stripped = _CREDENTIAL_PREFIX.sub("", value).strip()
    reference = _ENV_REFERENCE.fullmatch(stripped)
    if reference:
        fallback = reference["fallback"]
        name = reference["name"]
        if fallback and (role or (name and _role(name))) and _concrete(fallback):
            return _finding("secret-fallback", path, start, end)
        return None
    if role is None and _KEY_FORMAT.search(value):
        role = "api-key"
    if role is not None and _concrete(value):
        return _finding(role, path, start, end)
    return None


def _yaml_entries(content: str) -> Iterator[tuple[tuple[str, ...], str, int, int]]:
    for document in yaml.compose_all(content, Loader=yaml.SafeLoader):
        seen: set[tuple[int, tuple[str, ...]]] = set()
        nodes = [(document, ())]
        while nodes:
            node, keys = nodes.pop()
            identity = id(node), keys
            if identity in seen:
                continue
            seen.add(identity)
            if isinstance(node, MappingNode):
                nodes.extend(
                    (value, (*keys[-1:], name.value))
                    for name, value in node.value if isinstance(name, ScalarNode)
                )
            elif isinstance(node, SequenceNode):
                nodes.extend((value, keys) for value in node.value)
            elif isinstance(node, ScalarNode) and node.tag in {
                "tag:yaml.org,2002:str", "tag:yaml.org,2002:int",
            }:
                start = node.start_mark.line + 1
                yield keys, node.value, start, max(start, node.end_mark.line + bool(node.end_mark.column))


def _properties_entries(content: str, *, dotenv: bool) -> Iterator[tuple[tuple[str, ...], str, int, int]]:
    for index, entry in enumerate(content.splitlines(), 1):
        if match := _KEY_VALUE.fullmatch(entry):
            value = match[2]
            if dotenv:
                quoted = re.fullmatch(r"""("[^"]*"|'[^']*')\s*(?:#.*)?""", value)
                value = quoted[1] if quoted else re.split(r"\s+#", value, maxsplit=1)[0]
            if value[:1] in {"'", '"'} and value[:1] == value[-1:]:
                value = value[1:-1]
            yield tuple(match[1].split(".")), value, index, index


def scan_secrets(snapshot: Snapshot) -> ScanResult:
    scan = Scan(snapshot, _accepts)
    findings = []
    for path, content in scan.files.items():
        extension = PurePosixPath(path).suffix
        if extension in {".kt", ".kts"}:
            source = kotlin.KotlinFile(content)
            if source.root.has_error:
                scan.skip(path, "KOTLIN_PARSE_ERROR", "Файл Kotlin разобран частично.")
            findings.extend(_kotlin_findings(path, source))
            continue
        try:
            entries = (
                _yaml_entries(content) if extension in {".yaml", ".yml"}
                else _properties_entries(content, dotenv=PurePosixPath(path).name.startswith(".env") or extension == ".env")
            )
            for keys, value, start, end in entries:
                if finding := _configuration_finding(path, keys, value, start, end):
                    findings.append(finding)
        except (yaml.YAMLError, RecursionError):
            scan.skip(path, "YAML_PARSE_ERROR", "Не удалось полностью разобрать YAML.")
    return scan.result("secrets", "Исходники Kotlin и текстовые конфигурации", findings)
