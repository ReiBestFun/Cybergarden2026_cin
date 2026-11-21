"""Shared input and output for the static analyzers."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Literal

Category = Literal["secrets", "sca", "sast"]
Severity = Literal["info", "low", "medium", "high", "critical"]


@dataclass(frozen=True)
class Warning:
    code: str
    message: str
    path: str | None = None
    line: int | None = None
    field: str | None = None
    hint: str | None = None


class ScanError(Exception):
    def __init__(self, warning: Warning):
        super().__init__(warning.message)
        self.warning = warning


@dataclass(frozen=True)
class Finding:
    rule_id: str
    category: Category
    severity: Severity
    title: str
    description: str
    remediation: str
    path: str
    line: int | None
    end_line: int | None
    evidence: str
    metadata: dict[str, str]


@dataclass(frozen=True)
class Coverage:
    status: Literal["complete", "partial"]
    scope: str
    files_scanned: int
    files_skipped: int


@dataclass(frozen=True)
class ScanResult:
    category: Category
    findings: tuple[Finding, ...]
    warnings: tuple[Warning, ...]
    coverage: Coverage


def finding_key(finding: Finding) -> tuple:
    metadata = finding.metadata
    if finding.category == "sca":
        return (
            finding.category, finding.path, metadata["group"], metadata["artifact"],
            metadata["version"], metadata["advisory_id"],
        )
    return finding.category, finding.path, finding.line, metadata["class"]


def safe_path(path: str) -> bool:
    parts = path.split("/")
    return all(part not in {"", ".", ".."} for part in parts) and not re.search(
        r"[\\:\x00-\x1f\x7f]", path
    )


_IGNORED_DIRS = {".git", ".gradle", ".idea", ".venv", "build", "node_modules", "__pycache__"}


@dataclass(frozen=True)
class Snapshot:
    sha: str
    files: dict[str, str] = field(repr=False)
    skipped: tuple[Warning, ...] = ()

    def select(self, accepts: Callable[[PurePosixPath], bool]) -> Snapshot:
        return Snapshot(
            self.sha,
            {path: data for path, data in self.files.items() if accepts(PurePosixPath(path))},
            tuple(warning for warning in self.skipped if warning.path is None or accepts(PurePosixPath(warning.path))),
        )

    @classmethod
    def from_directory(
        cls,
        directory: Path,
        sha: str,
        *,
        max_file_bytes: int = 1024 * 1024,
        max_total_bytes: int = 32 * 1024 * 1024,
        max_files: int = 10_000,
    ) -> Snapshot:
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
            raise ScanError(Warning("INVALID_SHA", "Ожидался полный SHA коммита."))
        if directory.is_symlink() or not directory.is_dir():
            raise ScanError(Warning("SNAPSHOT_UNAVAILABLE", "Каталог снимка недоступен."))

        files: dict[str, str] = {}
        skipped: list[Warning] = []
        remaining = max_total_bytes
        visited = 0

        def on_error(error: OSError) -> None:
            if error.filename == str(directory):
                raise ScanError(Warning("SNAPSHOT_UNAVAILABLE", "Каталог снимка недоступен."))
            skipped.append(Warning("UNREADABLE_DIRECTORY", "Не удалось прочитать каталог."))

        for parent, directories, names in os.walk(directory, onerror=on_error, followlinks=False):
            for name in sorted(set(directories) - _IGNORED_DIRS):
                path = Path(parent, name)
                if path.is_symlink():
                    skipped.append(Warning("SYMLINK_DIRECTORY", "Пропущена ссылка на каталог."))
            directories[:] = sorted(
                name for name in directories
                if name not in _IGNORED_DIRS and not Path(parent, name).is_symlink()
            )
            for name in sorted(names):
                if visited >= max_files:
                    skipped.append(Warning("FILE_LIMIT", "Достигнут лимит количества файлов."))
                    return cls(sha, files, tuple(skipped))
                visited += 1
                path = Path(parent, name)
                relative = path.relative_to(directory).as_posix()
                if not safe_path(relative):
                    skipped.append(Warning("INVALID_PATH", "Пропущен недопустимый путь."))
                    continue
                try:
                    mode = path.lstat().st_mode
                    if not stat.S_ISREG(mode):
                        skipped.append(Warning("NON_REGULAR_FILE", "Пропущен специальный файл.", relative))
                        continue
                    limit = min(max_file_bytes, remaining)
                    if path.stat().st_size > limit:
                        skipped.append(Warning("SIZE_LIMIT", "Файл превышает лимит чтения.", relative))
                        continue
                    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                    with os.fdopen(os.open(path, flags), "rb") as stream:
                        data = stream.read(limit + 1)
                    if len(data) > limit:
                        skipped.append(Warning("SIZE_LIMIT", "Файл превышает лимит чтения.", relative))
                        continue
                    remaining -= len(data)
                    if b"\0" in data:
                        raise UnicodeError
                    files[relative] = data.decode("utf-8-sig")
                except UnicodeError:
                    skipped.append(Warning("NON_TEXT_FILE", "Файл не является текстом UTF-8.", relative))
                except OSError:
                    skipped.append(Warning("UNREADABLE_FILE", "Не удалось прочитать файл.", relative))
        return cls(sha, files, tuple(skipped))


class Scan:
    """Collect findings and diagnostics for one analyzer invocation."""

    def __init__(self, snapshot: Snapshot, accepts: Callable[[PurePosixPath], bool]):
        self.files = {
            path: text for path, text in snapshot.files.items() if accepts(PurePosixPath(path))
        }
        self.warnings: list[Warning] = []
        self.skipped: set[str] = set()
        self.partial = False
        for warning in snapshot.skipped:
            if warning.path is None or accepts(PurePosixPath(warning.path)):
                self.warn(warning, incomplete=True)
                if warning.path is not None:
                    self.skipped.add(warning.path)
        if not self.files:
            self.warn(Warning("NO_SUPPORTED_FILES", "Поддерживаемые файлы не найдены."), incomplete=True)

    def warn(self, warning: Warning, *, incomplete: bool = False) -> None:
        if warning not in self.warnings:
            self.warnings.append(warning)
        self.partial |= incomplete

    def skip(self, path: str, code: str, message: str) -> None:
        self.skipped.add(path)
        self.warn(Warning(code, message, path), incomplete=True)

    def result(self, category: Category, scope: str, findings: list[Finding]) -> ScanResult:
        ordered = sorted(findings, key=lambda item: (item.path, item.line or 0, item.rule_id))
        unique: dict[tuple, Finding] = {}
        for finding in ordered:
            unique.setdefault(finding_key(finding), finding)
        return ScanResult(
            category, tuple(unique.values()), tuple(self.warnings),
            Coverage(
                "partial" if self.partial else "complete", scope,
                len(self.files.keys() - self.skipped), len(self.skipped),
            ),
        )
