import os
import shutil
from pathlib import Path

import pytest

from backend.app.integrations import repository


@pytest.mark.parametrize("change", ["original", "edited", "configured"])
def test_ci_snapshot_uses_trusted_advisories(monkeypatch, tmp_path, change):
    source = Path(os.environ.get("TRAINING_DATASET", "../training")) / "vulnerability-database/vulnerabilities.json"
    if change == "original" and not source.is_file():
        pytest.skip("Set TRAINING_DATASET to the training fixture for the original catalog.")
    sha = "a" * 40
    configured = tmp_path / "worker-catalog.json" if change == "configured" else None

    def git(directory, deadline, command, *arguments):
        if command == "checkout":
            target = directory / "vulnerability-database/vulnerabilities.json"
            target.parent.mkdir()
            if change == "original":
                shutil.copyfile(source, target)
            else:
                target.write_text("{}", encoding="utf-8")
        return sha if command == "rev-parse" else ""

    monkeypatch.setattr(repository, "_git", git)
    with repository.prepare_repository(
        repository.TRAINING_REPOSITORY, sha, allowed=repository.ci_repository_allowlist([repository.TRAINING_REPOSITORY]),
        timeout=1, catalog_path=configured, pin_training_catalog=True,
    ) as prepared:
        if change == "original":
            assert prepared.catalog_path.read_bytes() == source.read_bytes()
        else:
            assert prepared.catalog_path == configured
