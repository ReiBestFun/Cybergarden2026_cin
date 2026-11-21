"""Analyzers available to workflow nodes."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .models import Category, ScanResult, Snapshot
from .password_hashing import accepts_password_source, scan_password_hashing
from .sast import accepts_sast_source, scan_sast
from .sca import VulnerabilityCatalog, accepts_sca_source, catalog_missing, scan_sca
from .secrets import accepts_secrets_source, scan_secrets


@dataclass(frozen=True)
class Scanner:
    category: Category
    accepts: Callable[[PurePosixPath], bool]
    analyze: Callable[[Snapshot, Path | None], ScanResult]


def _scan_dependencies(snapshot: Snapshot, catalog: Path | None) -> ScanResult:
    if catalog is None:
        raise catalog_missing()
    return scan_sca(snapshot, VulnerabilityCatalog.from_file(catalog))


SCANNERS = {
    "secrets": Scanner("secrets", accepts_secrets_source, lambda snapshot, _: scan_secrets(snapshot)),
    "sca": Scanner("sca", accepts_sca_source, _scan_dependencies),
    "sast": Scanner("sast", accepts_sast_source, lambda snapshot, _: scan_sast(snapshot)),
    "password_hashing": Scanner("sast", accepts_password_source, lambda snapshot, _: scan_password_hashing(snapshot)),
}
SCANNER_NODES = {f"scan_{name}": name for name in SCANNERS}
