"""Compare statically declared Gradle dependencies with a trusted catalog."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from tree_sitter import Node

from . import kotlin
from .models import Finding, Scan, ScanError, ScanResult, Severity, Snapshot, Warning

_COORDINATE = re.compile(r"[A-Za-z0-9_.-]+")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*")
_GHSA = re.compile(r"GHSA-(?:[a-z0-9]{4}-){2}[a-z0-9]{4}")
CATALOG_HINT = (
    "Возьмите vulnerability-database/vulnerabilities.json из TBagirov/training или составьте JSON по "
    "vulnerability-database/schema.json и задайте SCA_CATALOG_PATH в окружении воркера."
)


def catalog_missing() -> ScanError:
    return ScanError(Warning("SCA_CATALOG_MISSING", "Для SCA требуется справочник Maven.", hint=CATALOG_HINT))


def _catalog_invalid(*, field: str | None = None, line: int | None = None, message: str | None = None) -> ScanError:
    return ScanError(Warning(
        "SCA_CATALOG_INVALID", message or "Справочник Maven не соответствует формату версии 1.",
        line=line, field=field, hint=CATALOG_HINT,
    ))


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
        try:
            lower = None if item["lower"] is None else _version(item["lower"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("lower") from None
        try:
            upper = _version(item["upper"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("upper") from None
        lower_inclusive, upper_inclusive = item.get("lowerInclusive"), item.get("upperInclusive")
        if type(lower_inclusive) is not bool:
            raise ValueError("lowerInclusive")
        if type(upper_inclusive) is not bool:
            raise ValueError("upperInclusive")
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
        except FileNotFoundError:
            raise ScanError(Warning(
                "SCA_CATALOG_NOT_FOUND", f"Справочник {path.name} не найден.",
                hint="Проверьте путь и наличие файла в файловой системе воркера. " + CATALOG_HINT,
            )) from None
        except OSError:
            raise ScanError(Warning(
                "SCA_CATALOG_UNREADABLE", f"Воркер не смог прочитать справочник {path.name}.",
                hint="Укажите обычный JSON-файл и проверьте права процесса воркера.",
            )) from None
        if len(raw) > 8 * 1024 * 1024:
            raise ScanError(Warning("SCA_CATALOG_TOO_LARGE", "Размер справочника превышает 8 МиБ.", hint=CATALOG_HINT))
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            raise _catalog_invalid(
                line=error.lineno,
                message=f"Ошибка синтаксиса JSON, строка {error.lineno}, столбец {error.colno}.",
            ) from None
        except (ValueError, RecursionError):
            raise _catalog_invalid(message="Не удалось декодировать JSON справочника.") from None
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: object) -> VulnerabilityCatalog:
        index: dict[tuple[str, str], list[Advisory]] = {}
        seen: set[tuple[str, str, str]] = set()
        if not isinstance(data, dict):
            raise _catalog_invalid(field="$")
        for field, expected in (
            ("formatVersion", 1), ("ecosystem", "Maven"), ("versionFormat", "numeric-dot-separated"),
        ):
            value = data.get(field)
            if type(value) is not type(expected) or value != expected:
                raise _catalog_invalid(field=field)
        entries = data.get("vulnerabilities")
        if not isinstance(entries, list) or not entries:
            raise _catalog_invalid(field="vulnerabilities")
        field = "vulnerabilities"
        try:
            for position, entry in enumerate(entries):
                field = f"vulnerabilities[{position}]"
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
                field = f"vulnerabilities[{position}].fixedVersions"
                for fixed in fixes:
                    _version(fixed)
                ranges = []
                for range_index, bound in enumerate(bounds):
                    field = f"vulnerabilities[{position}].ranges[{range_index}]"
                    if not isinstance(bound, dict):
                        raise ValueError
                    try:
                        ranges.append(VersionRange.parse(bound))
                    except ValueError as error:
                        if error.args:
                            field += f".{error.args[0]}"
                        raise
                advisory = Advisory(
                    advisory_id, cast(Severity, severity), tuple(ranges), tuple(fixes),
                )
                index.setdefault((group, artifact), []).append(advisory)
        except (AttributeError, KeyError, TypeError, ValueError):
            raise _catalog_invalid(field=field) from None
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


def _coordinates(
    source: kotlin.KotlinFile, node: Node, properties: dict[str, str], libraries: dict[str, str]
) -> str | None:
    target = source.resolve(node)
    wrapper = kotlin.call(target)
    if wrapper and wrapper.name in {"platform", "enforcedPlatform"} and len(wrapper.args) == 1:
        target = source.resolve(wrapper.args[0])
    value = kotlin.string_content(target)
    if value is None:
        reference = kotlin.text(target)
        return libraries.get(reference.removeprefix("libs.")) if reference.startswith("libs.") else None

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


def accepts_sca_source(path: PurePosixPath) -> bool:
    return path.name in {"build.gradle.kts", "gradle.properties", "gradle.lockfile"} or (
        path.name == "libs.versions.toml" and path.parent.name == "gradle"
    )


def _read_libraries(scan: Scan, path: str, content: str) -> dict[str, str]:
    try:
        catalog = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        scan.skip(path, "GRADLE_CATALOG_PARSE_ERROR", "Не удалось прочитать каталог версий Gradle в формате TOML.")
        return {}
    libraries, versions = catalog.get("libraries", {}), catalog.get("versions", {})
    if not isinstance(libraries, dict) or not isinstance(versions, dict):
        scan.skip(path, "GRADLE_CATALOG_PARSE_ERROR", "В каталоге Gradle ожидаются таблицы libraries и versions.")
        return {}
    aliases = {}
    for name, entry in libraries.items():
        alias = re.sub(r"[-_.]+", ".", name)
        if isinstance(entry, str):
            aliases[alias] = entry
        elif isinstance(entry, dict):
            module = entry.get("module")
            group, artifact = entry.get("group"), entry.get("name")
            if not isinstance(module, str) and isinstance(group, str) and isinstance(artifact, str):
                module = f"{group}:{artifact}"
            if not isinstance(module, str):
                continue
            version = entry.get("version")
            if isinstance(version, dict) and isinstance(version.get("ref"), str):
                version = versions.get(version["ref"])
            aliases[alias] = f"{module}:{version}" if isinstance(version, str) else module
    return aliases


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
        raise catalog_missing()
    scan = Scan(snapshot, accepts_sca_source)
    if not any(PurePosixPath(path).name == "build.gradle.kts" for path in scan.files):
        skipped = next((w for w in scan.warnings if w.path and PurePosixPath(w.path).name == "build.gradle.kts"), None)
        if skipped:
            raise ScanError(Warning(
                "SCA_BUILD_FILE_UNAVAILABLE", skipped.message, skipped.path,
                hint="Проверьте права и лимиты чтения исходного снимка.",
            ))
        raise ScanError(Warning(
            "SCA_BUILD_FILE_MISSING", "SCA не нашёл build.gradle.kts.",
            hint="Проверьте ветку и наличие Kotlin DSL-файла build.gradle.kts в проекте или модуле.",
        ))
    properties = {path: _read_properties(data) for path, data in scan.files.items() if path.endswith("gradle.properties")}
    locks = {
        path: _lock_entries(scan, path, data)
        for path, data in scan.files.items() if path.endswith("gradle.lockfile")
    }
    libraries = {
        path: _read_libraries(scan, path, data)
        for path, data in scan.files.items() if path.endswith("libs.versions.toml")
    }
    findings: list[Finding] = []
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
        aliases = libraries.get("gradle/libs.versions.toml", {}) | libraries.get(
            (script.parent / "gradle/libs.versions.toml").as_posix(), {}
        )
        for node, configuration, notation in _dependencies(source):
            line = kotlin.line(node)
            coordinates = _coordinates(source, notation, settings, aliases) if notation is not None else None
            parts = coordinates.split(":") if coordinates else []
            if (
                configuration is None or len(parts) not in {2, 3}
                or any(not _COORDINATE.fullmatch(part) for part in parts[:2])
            ):
                scan.warn(Warning("UNSUPPORTED_DEPENDENCY", "Форма объявления зависимости не поддерживается.", path, line), incomplete=True)
                continue
            group, artifact = parts[:2]
            declared = parts[2] if len(parts) == 3 else ""
            configurations = _configuration_names(configuration)
            locked: dict[str, set[str]] = {}
            for version, contexts in lock.get((group, artifact), ()):
                if matched := contexts & configurations:
                    locked.setdefault(version, set()).update(matched)
            conflicting = {
                context for context in configurations
                if sum(context in contexts for contexts in locked.values()) > 1
            }
            if conflicting:
                affected = ", ".join(sorted(conflicting))
                scan.warn(Warning(
                    "SCA_LOCK_CONFLICT", f"В lockfile несколько версий {group}:{artifact} для {affected}.",
                    path, line, hint="Для одной конфигурации оставьте одну точно разрешённую версию.",
                ), incomplete=True)
                locked = {version: contexts - conflicting for version, contexts in locked.items() if contexts - conflicting}
                if not locked:
                    continue
            versions = locked or {declared: {configuration}}
            for version, contexts in sorted(versions.items()):
                context = ", ".join(sorted(contexts))
                try:
                    parsed = _version(version)
                except ValueError:
                    scan.warn(Warning(
                        "UNRESOLVED_VERSION", f"У {group}:{artifact} нет точной числовой версии ({configuration}).",
                        path, line, hint="Укажите точную версию в Gradle, каталоге версий или lockfile.",
                    ), incomplete=True)
                    continue
                if locked and declared and version != declared:
                    scan.warn(Warning("LOCK_VERSION_OVERRIDE", f"Для {group}:{artifact} использована версия lockfile ({context}).", path, line))
                for advisory in catalog.index.get((group, artifact), ()):
                    if not any(bounds.contains(parsed) for bounds in advisory.ranges):
                        continue
                    fixed = ", ".join(advisory.fixed_versions)
                    remediation = (
                        f"Обновите зависимость до подходящей исправленной версии из справочника ({fixed})."
                        if fixed else "Обновите или замените зависимость согласно advisory."
                    )
                    findings.append(Finding(
                        "sca.maven-advisory", "sca", advisory.severity, "Уязвимая зависимость Maven",
                        f"Версия {version} соответствует диапазону {advisory.id}.", remediation,
                        path, line, kotlin.end_line(node), "Совпадение с диапазоном справочника.",
                        {"group": group, "artifact": artifact, "version": version, "configurations": context,
                         "advisory_id": advisory.id, "version_source": "lockfile" if locked else "declaration"},
                    ))
    return scan.result("sca", "Прямые зависимости Gradle Kotlin DSL по учебному справочнику Maven", findings)
