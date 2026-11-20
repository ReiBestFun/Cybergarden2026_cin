"""Compare statically declared Gradle dependencies with a trusted catalog."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from tree_sitter import Node

from . import kotlin
from .models import Finding, Scan, ScanError, ScanResult, Severity, Snapshot, Warning

_COORDINATE = re.compile(r"[A-Za-z0-9_.-]+")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*")
_GHSA = re.compile(r"GHSA-(?:[a-z0-9]{4}-){2}[a-z0-9]{4}")
_CATALOG_ERROR = Warning("INVALID_CATALOG", "Справочник Maven недоступен или имеет неверный формат.")


def _version(value: object) -> tuple[int, ...]:
    if not isinstance(value, str) or len(value) > 128 or not _VERSION.fullmatch(value):
        raise ValueError
    parts = [int(part) for part in value.split(".")]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


@dataclass(frozen=True)
class VersionRange:
    lower: tuple[int, ...] | None
    lower_inclusive: bool
    upper: tuple[int, ...]
    upper_inclusive: bool

    @classmethod
    def parse(cls, item: dict) -> VersionRange:
        lower_value = item["lower"]
        lower = None if lower_value is None else _version(lower_value)
        upper = _version(item["upper"])
        lower_inclusive, upper_inclusive = item["lowerInclusive"], item["upperInclusive"]
        if type(lower_inclusive) is not bool or type(upper_inclusive) is not bool:
            raise ValueError
        if lower is not None and (lower > upper or (lower == upper and not (lower_inclusive and upper_inclusive))):
            raise ValueError
        return cls(lower, lower_inclusive, upper, upper_inclusive)

    def contains(self, version: tuple[int, ...]) -> bool:
        return (
            (self.lower is None or version > self.lower or (version == self.lower and self.lower_inclusive))
            and (version < self.upper or (version == self.upper and self.upper_inclusive))
        )


@dataclass(frozen=True)
class Advisory:
    id: str
    severity: Severity
    ranges: tuple[VersionRange, ...]
    fixed_versions: tuple[str, ...]


@dataclass(frozen=True)
class VulnerabilityCatalog:
    index: dict[tuple[str, str], tuple[Advisory, ...]]

    @classmethod
    def from_file(cls, path: Path) -> VulnerabilityCatalog:
        try:
            with path.open("rb") as stream:
                raw = stream.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError
            data = json.loads(raw)
        except (OSError, ValueError, RecursionError):
            raise ScanError(_CATALOG_ERROR) from None
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: object) -> VulnerabilityCatalog:
        index: dict[tuple[str, str], list[Advisory]] = {}
        seen: set[tuple[str, str, str]] = set()
        try:
            if not isinstance(data, dict) or (
                type(data.get("formatVersion")) is not int or data["formatVersion"] != 1
                or data.get("ecosystem") != "Maven"
                or data.get("versionFormat") != "numeric-dot-separated"
            ):
                raise ValueError
            entries = data["vulnerabilities"]
            if not isinstance(entries, list) or not entries:
                raise ValueError
            for entry in entries:
                group, artifact, advisory_id = (entry[key] for key in ("group", "artifact", "id"))
                severity = entry["severity"].lower()
                if not _COORDINATE.fullmatch(group) or not _COORDINATE.fullmatch(artifact):
                    raise ValueError
                if not _GHSA.fullmatch(advisory_id) or severity not in {"low", "medium", "high", "critical"}:
                    raise ValueError
                key = group, artifact, advisory_id
                if key in seen:
                    raise ValueError
                seen.add(key)
                bounds, fixes = entry["ranges"], entry["fixedVersions"]
                if not isinstance(bounds, list) or not bounds or not isinstance(fixes, list):
                    raise ValueError
                for fixed in fixes:
                    _version(fixed)
                advisory = Advisory(
                    advisory_id, cast(Severity, severity),
                    tuple(VersionRange.parse(bound) for bound in bounds), tuple(fixes),
                )
                index.setdefault((group, artifact), []).append(advisory)
        except (AttributeError, KeyError, TypeError, ValueError):
            raise ScanError(_CATALOG_ERROR) from None
        return cls({key: tuple(entries) for key, entries in index.items()})


def _version_reference(
    source: kotlin.KotlinFile, node: Node, properties: dict[str, str]
) -> str | None:
    target = source.resolve(node)
    value = kotlin.literal(target)
    if value is not None:
        return value
    getter = kotlin.call(target)
    if getter is None or getter.name not in {"get", "getOrNull"} or getter.receiver is None:
        return None
    provider = kotlin.call(getter.receiver)
    if (
        provider and provider.name == "gradleProperty" and provider.receiver is not None
        and kotlin.text(provider.receiver) == "providers" and len(provider.args) == 1
    ):
        return properties.get(kotlin.literal(provider.args[0]) or "")
    return None


def _coordinates(source: kotlin.KotlinFile, node: Node, properties: dict[str, str]) -> str | None:
    target = source.resolve(node)
    value = kotlin.string_content(target)
    if value is None:
        return None

    def replace(match: re.Match[str]) -> str:
        name = match["name"] or match["braced"]
        if name is None:
            return match[0]
        binding = source.binding(name.strip(), target)
        return (_version_reference(source, binding, properties) or match[0]) if binding is not None else match[0]

    return kotlin.TEMPLATE.sub(replace, value)


_PROPERTY = re.compile(r"\s*([\w.-]+)\s*(?:[=:]\s*|\s+)(\S.*?)\s*")
_LOCK_ENTRY = re.compile(r"([^:\s]+):([^:\s]+):([^=\s]+)=(\S+)")
_CONFIGURATION_SUFFIX = {
    "implementation": ("compileClasspath", "runtimeClasspath"),
    "api": ("compileClasspath", "runtimeClasspath"),
    "runtimeOnly": ("runtimeClasspath",),
    "compileOnly": ("compileClasspath",),
    "annotationProcessor": ("annotationProcessor",),
}


def _configuration_names(name: str) -> set[str]:
    names = {name}
    for suffix, endpoints in _CONFIGURATION_SUFFIX.items():
        prefix = name[:-len(suffix)] if name.lower().endswith(suffix.lower()) else ""
        if name == suffix or prefix:
            names.update(prefix + endpoint[0].upper() + endpoint[1:] if prefix else endpoint for endpoint in endpoints)
    return names


def _read_properties(content: str) -> dict[str, str]:
    return {
        match[1]: match[2]
        for line in content.splitlines() if (match := _PROPERTY.fullmatch(line))
    }


def _lock_entries(scan: Scan, path: str, content: str) -> dict[tuple[str, str], list[tuple[str, set[str]]]]:
    entries: dict[tuple[str, str], list[tuple[str, set[str]]]] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "empty=")):
            continue
        match = _LOCK_ENTRY.fullmatch(line)
        if match is None:
            scan.skip(path, "LOCK_PARSE_ERROR", "Файл блокировки разобран частично.")
            continue
        group, artifact, version, configurations = match.groups()
        entries.setdefault((group, artifact), []).append((version, set(configurations.split(","))))
    return entries


def _dependencies(source: kotlin.KotlinFile):
    for block in kotlin.children(source.root):
        call = kotlin.call(block)
        if call is None or call.name != "dependencies" or call.receiver is not None or call.block is None:
            continue
        for node in kotlin.children(call.block):
            declaration = kotlin.call(node)
            if declaration is None or not declaration.args:
                yield node, None, None
            elif declaration.name == "add":
                if len(declaration.args) >= 2:
                    yield node, kotlin.literal(declaration.args[0]), declaration.args[1]
                else:
                    yield node, None, None
            else:
                yield node, declaration.name, declaration.args[0]


def scan_sca(snapshot: Snapshot, catalog: VulnerabilityCatalog) -> ScanResult:
    if not isinstance(catalog, VulnerabilityCatalog):
        raise ScanError(_CATALOG_ERROR)
    scan = Scan(snapshot, lambda path: path.name in {"build.gradle.kts", "gradle.properties", "gradle.lockfile"})
    properties = {path: _read_properties(data) for path, data in scan.files.items() if path.endswith("gradle.properties")}
    locks = {
        path: _lock_entries(scan, path, data)
        for path, data in scan.files.items() if path.endswith("gradle.lockfile")
    }
    findings: list[Finding] = []
    if not any(PurePosixPath(path).name == "build.gradle.kts" for path in scan.files):
        scan.warn(Warning("NO_BUILD_FILE", "Файл build.gradle.kts не найден."), incomplete=True)
    for path, data in scan.files.items():
        script = PurePosixPath(path)
        if script.name != "build.gradle.kts":
            continue
        source = kotlin.KotlinFile(data)
        if source.root.has_error:
            scan.skip(path, "GRADLE_PARSE_ERROR", "Файл Gradle разобран частично.")
        module_properties = properties.get(script.with_name("gradle.properties").as_posix(), {})
        settings = properties.get("gradle.properties", {}) | module_properties
        lock = locks.get(script.with_name("gradle.lockfile").as_posix(), {})
        for node, configuration, notation in _dependencies(source):
            coordinates = _coordinates(source, notation, settings) if notation is not None else None
            parts = coordinates.split(":") if coordinates else []
            if (
                configuration is None or len(parts) not in {2, 3}
                or any(not _COORDINATE.fullmatch(part) for part in parts[:2])
            ):
                scan.warn(Warning("UNSUPPORTED_DEPENDENCY", "Форма объявления зависимости не поддерживается.", path), incomplete=True)
                continue
            group, artifact = parts[:2]
            declared = parts[2] if len(parts) == 3 else ""
            configurations = _configuration_names(configuration)
            locked = {value for value, contexts in lock.get((group, artifact), ()) if contexts & configurations}
            if len(locked) > 1:
                scan.warn(Warning("AMBIGUOUS_LOCK", "В lockfile несколько версий для объявления.", path), incomplete=True)
                continue
            version = next(iter(locked), declared)
            try:
                parsed = _version(version)
            except ValueError:
                scan.warn(Warning("UNRESOLVED_VERSION", "Есть зависимости без поддерживаемой точной версии.", path), incomplete=True)
                continue
            if locked and declared and version != declared:
                scan.warn(Warning("LOCK_VERSION_OVERRIDE", "Использована версия из lockfile.", path))
            for advisory in catalog.index.get((group, artifact), ()):
                if not any(bounds.contains(parsed) for bounds in advisory.ranges):
                    continue
                fixed = ", ".join(advisory.fixed_versions)
                remediation = (
                    f"Обновите зависимость до исправленной версии из справочника ({fixed}) или новее."
                    if fixed else "Обновите или замените зависимость согласно advisory."
                )
                findings.append(Finding(
                    "sca.maven-advisory", "sca", advisory.severity, "Уязвимая зависимость Maven",
                    f"Версия {version} соответствует диапазону {advisory.id}.", remediation,
                    path, kotlin.line(node), kotlin.end_line(node), "Совпадение с диапазоном справочника.",
                    {"group": group, "artifact": artifact, "version": version,
                     "advisory_id": advisory.id, "version_source": "lockfile" if locked else "declaration"},
                ))
    return scan.result("sca", "Прямые зависимости Gradle Kotlin DSL по учебному справочнику Maven", findings)
