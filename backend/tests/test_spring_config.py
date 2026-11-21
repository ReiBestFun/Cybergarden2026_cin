import pytest

from backend.app.analyzers import Snapshot, scan_spring_config


@pytest.mark.parametrize(("name", "content", "line"), [
    ("application.yml", "server:\n  error:\n    include-stacktrace: always\n", 3),
    ("application-prod.properties", "# Error responses\nserver.error.include-stacktrace=ALWAYS\n", 2),
    ("application.yaml", "server.error.include-stacktrace: never\n", None),
    ("application.properties", "server.error.include-stacktrace=always\nserver.error.include-stacktrace=never\n", None),
])
def test_stacktrace_setting_without_kotlin(name, content, line):
    path = f"src/main/resources/{name}"
    result = scan_spring_config(Snapshot("8" * 40, {path: content}))
    assert result.coverage.status == "complete"
    assert [(item.rule_id, item.severity, item.path, item.line) for item in result.findings] == (
        [("sast.error-stacktrace", "medium", path, line)] if line else []
    )
