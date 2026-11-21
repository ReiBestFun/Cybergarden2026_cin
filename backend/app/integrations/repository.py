"""Prepare a local snapshot from an allowed GitHub source."""

import os
import re
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Iterator
from urllib.parse import urlsplit

from ..analyzers.models import ScanError, Snapshot, Warning

TRAINING_REPOSITORY = "https://github.com/TBagirov/training"
REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._/-]*$"


def canonical_repository_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.rstrip("/").removesuffix(".git")
    if (
        parts.scheme != "https" or parts.netloc.lower() != "github.com"
        or parts.query or parts.fragment
        or not re.fullmatch(r"/[a-zA-Z0-9-]+/[a-zA-Z0-9_.-]+", path)
        or path.rsplit("/", 1)[-1] in {".", ".."}
    ):
        raise ValueError("Ожидалась HTTPS-ссылка на репозиторий GitHub.")
    return f"https://github.com{path}"


def allowed_repository_urls(urls: list[str]) -> frozenset[str]:
    return frozenset(canonical_repository_url(url).casefold() for url in urls)


def ci_repository_allowlist(urls: list[str]) -> frozenset[str]:
    return allowed_repository_urls(urls).intersection({TRAINING_REPOSITORY.casefold()})


# None means "any public GitHub repository" (ALLOW_ANY_REPOSITORY=true).
Allowlist = frozenset[str] | None


def repository_allowlist(urls: list[str], allow_any: bool) -> Allowlist:
    return None if allow_any else allowed_repository_urls(urls)


def is_repository_allowed(canonical_url: str, allowed: Allowlist) -> bool:
    return allowed is None or canonical_url.casefold() in allowed


@dataclass(frozen=True)
class RepositorySnapshot:
    snapshot: Snapshot
    catalog_path: Path | None


def _git(directory: Path, deadline: float, *arguments: str) -> str:
    environment = os.environ | {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
    }
    try:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError
        result = subprocess.run(
            ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
             "-c", "http.followRedirects=false", "-c", f"core.hooksPath={os.devnull}", *arguments],
            cwd=directory, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=remaining, check=True, text=True,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, TimeoutError):
        raise ScanError(Warning("REPOSITORY_TIMEOUT", "Получение исходников превысило лимит времени.")) from None
    except (subprocess.CalledProcessError, OSError, UnicodeError):
        raise ScanError(Warning("REPOSITORY_FETCH_FAILED", "Не удалось получить исходники для указанной ветки, тега или коммита.")) from None


@contextmanager
def prepare_repository(
    url: str,
    ref: str,
    *,
    allowed: Allowlist,
    timeout: float,
    catalog_path: Path | None,
) -> Iterator[RepositorySnapshot]:
    try:
        canonical = canonical_repository_url(url)
    except ValueError:
        raise ScanError(Warning("INVALID_REPOSITORY", "Указана некорректная ссылка на GitHub.")) from None
    if not is_repository_allowed(canonical, allowed):
        raise ScanError(Warning("REPOSITORY_NOT_ALLOWED", "Репозиторий отсутствует в списке учебных источников."))
    if len(ref) > 255 or not re.fullmatch(REF_PATTERN, ref):
        raise ScanError(Warning("INVALID_REF", "Указана некорректная ветка, тег или коммит."))

    with TemporaryDirectory(prefix="workflow-source-") as temporary:
        directory = Path(temporary)
        deadline = monotonic() + timeout
        _git(directory, deadline, "init", "--quiet", "--template=")
        _git(directory, deadline, "fetch", "--quiet", "--no-tags", "--no-recurse-submodules", "--depth=1",
             "--", canonical, ref)
        sha = _git(directory, deadline, "rev-parse", "--verify", "FETCH_HEAD^{commit}")
        _git(directory, deadline, "checkout", "--quiet", "--detach", sha)
        training_project = directory / "training-project"
        project = training_project if training_project.is_dir() else directory
        if catalog_path is None and canonical.casefold() == TRAINING_REPOSITORY.casefold():
            advisory_directory = directory / "vulnerability-database"
            advisory_file = advisory_directory / "vulnerabilities.json"
            if not advisory_directory.is_symlink() and not advisory_file.is_symlink() and advisory_file.is_file():
                catalog_path = advisory_file
        yield RepositorySnapshot(Snapshot.from_directory(project, sha), catalog_path)
