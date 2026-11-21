import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import pytest

from backend.app.analyzers import Snapshot, VulnerabilityCatalog, scan_sast, scan_sca, scan_secrets
from backend.app.analyzers.models import ScanError, Warning
from backend.app.analyzers.sca import VersionRange
from backend.app.reports import NodeResult, merge_findings, readable_report, submission_json
from backend.app.worker import run_analysis

SHA = "a" * 40
ADVISORY = "GHSA-aaaa-bbbb-cccc"


def catalog():
    return VulnerabilityCatalog.from_dict({
        "formatVersion": 1,
        "ecosystem": "Maven",
        "versionFormat": "numeric-dot-separated",
        "vulnerabilities": [{
            "id": ADVISORY,
            "group": "demo",
            "artifact": "asset",
            "severity": "HIGH",
            "ranges": [
                {"lower": "1.2", "lowerInclusive": True, "upper": "1.5", "upperInclusive": False},
                {"lower": "1.4", "lowerInclusive": False, "upper": "1.6", "upperInclusive": True},
            ],
            "fixedVersions": ["1.7"],
        }],
    })


def test_secrets_classifies_literals_and_excludes_controls():
    result = scan_secrets(Snapshot(SHA, {
        "src/Service.kt": """
            class Service {
                val servicePassword = "fixture-pass-41"
                fun configure() {
                    header("X-Api-Key", "fixture-key-42")
                    val fallback = System.getenv("SERVICE_TOKEN") ?: "fixture-fallback-43"
                    val left = "fixture-"
                    val right = "split-44"
                    val credential = left + right
                    setBearerAuth(credential)
                    header("Authorization", System.getenv("SERVICE_TOKEN"))
                }
            }
        """,
        "application.yml": """
            upstream:
              authorization:
                value: Bearer fixture-yaml-45
              token: @{UPSTREAM_TOKEN}
        """.replace("@", "$"),
        ".env.example": 'API_KEY="your-key-here" # Replace at runtime\n',
    }))
    assert {item.metadata["class"] for item in result.findings} == {
        "password", "api-key", "secret-fallback", "recoverable-token", "configuration-token",
    }
    assert len(result.findings) == 5
    assert all(item.evidence == "***" for item in result.findings)
    assert "fixture-" not in json.dumps(asdict(result))


@pytest.mark.parametrize("path", ["Settings.kt", "app.properties"])
def test_secrets_combines_entropy_with_format_and_context(path, caplog):
    entries = [
        ("material", "Q2vp8KhJ41Ybm6Li3NVdT7fx0s"),
        ("identifier", "f47ac10b-58cc-4372-a567-0e02b2c3d479"),
        ("materialId", "d85a1f09bc37e264" * 4),
        ("password", "example-Q2vp8KhJ41Ybm6Li3NVdT7fx0s"),
        ("databasePassword", "zzzzzz"),
        ("pageToken", "G3js8Qvp1Lj5cY4rHu07axBt"),
        ("label", "aaaabbbbccccddddaaaabbbbccccdddd"),
        ("contentType", "application/x-www-form-urlencoded"),
        ("path", "/usr/local/share/zoneinfo/America/New_York"),
        ("materialHeader", "Bearer x47Vie9QbnOb2NCT79Ug5rdF"),
        ("artifact", "com-example-OrdersApplicationConfiguration"),
    ]
    lines = [f'val {key} = "{value}"' if path.endswith(".kt") else f"{key}={value}"
             for key, value in entries]
    result = scan_secrets(Snapshot(SHA, {path: "\n".join(lines)}))
    assert [(item.line, item.metadata["class"]) for item in result.findings] == [
        (1, "configuration-token"), (5, "password"), (10, "configuration-token"),
    ]
    report = merge_findings(SHA, (NodeResult("secrets", "succeeded", result),))
    outputs = (json.dumps(asdict(result)), submission_json(report), readable_report(report), caplog.text)
    assert all(value not in output for _, value in entries for output in outputs)


def test_secrets_uses_explicit_kotlin_context_for_the_same_literal():
    result = scan_secrets(Snapshot(SHA, {"Config.kt": """
        val material = "Q2vp8KhJ41Ybm6Li3NVdT7fx0s"
        val databasePassword = material
        val settings = mapOf("opaque" to "G3js8Qvp1Lj5cY4rHu07axBt")
        val service = Service(material = "kI79Fo68jcxQDCnbsUYN1BRW")
        val pageToken = "Lm8ofNqz1C6jVb75xeiSAZB4"
        val params = pageToken
    """}))
    assert [(item.line, item.metadata["class"]) for item in result.findings] == [
        (2, "password"), (4, "configuration-token"), (5, "configuration-token"),
    ]


def test_invalid_yaml_keeps_other_safe_results():
    result = scan_secrets(Snapshot(SHA, {
        ".env": "APP_TOKEN=synthetic-9d7",
        "broken.yml": "password: [synthetic-hidden",
    }))
    assert len(result.findings) == 1
    assert result.coverage.status == "partial"
    assert result.coverage.files_scanned == result.coverage.files_skipped == 1
    assert "synthetic" not in json.dumps(asdict(result))


@pytest.mark.parametrize(
    ("version", "affected"),
    [("1.1.9", False), ("1.2.0", True), ("1.5", True), ("1.6.0", True), ("1.6.1", False), ("1.10", False)],
)
def test_sca_inclusive_and_exclusive_ranges(version, affected):
    snapshot = Snapshot(SHA, {"build.gradle.kts": f'dependencies {{ implementation("demo:asset:{version}") }}'})
    findings = scan_sca(snapshot, catalog()).findings
    assert len(findings) == int(affected)
    if affected:
        assert findings[0].metadata["version"] == version


def test_sca_respects_both_exclusive_bounds_and_an_open_lower_bound():
    bounded = VersionRange.parse(
        {"lower": "1.2", "lowerInclusive": False, "upper": "2", "upperInclusive": False}
    )
    assert not bounded.contains((1, 2))
    assert bounded.contains((1, 3))
    assert not bounded.contains((2,))
    unbounded = VersionRange.parse(
        {"lower": None, "lowerInclusive": False, "upper": "1", "upperInclusive": True}
    )
    assert unbounded.contains((0, 1))
    assert unbounded.contains((1,))


def test_sca_resolves_versions_and_only_uses_matching_lock_entries():
    snapshot = Snapshot(SHA, {
        "gradle.properties": "depVersion=1.2\n",
        "build.gradle.kts": """
            val dep = providers.gradleProperty("depVersion").get()
            dependencies {
                // implementation("demo:asset:1.4")
                add("implementation", "demo:asset:$dep")
            }
        """,
        "gradle.lockfile": "demo:asset:1.5=runtimeClasspath\ndemo:asset:1.4=testRuntimeClasspath\n",
        "feature/build.gradle.kts": 'dependencies { runtimeOnly("demo:asset:1.+") }',
    })
    result = scan_sca(snapshot, catalog())
    assert len(result.findings) == 1
    assert result.findings[0].metadata["version"] == "1.5"
    assert result.findings[0].metadata["version_source"] == "lockfile"
    assert {item.code for item in result.warnings} == {"LOCK_VERSION_OVERRIDE", "UNRESOLVED_VERSION"}
    assert result.coverage.status == "partial"


def test_snapshot_rejects_bad_sha_and_skips_links_binary_and_oversized_files(tmp_path):
    (tmp_path / "small.kt").write_text("val x = 1\n")
    (tmp_path / "huge.kt").write_text("z" * 40)
    (tmp_path / "binary.kt").write_bytes(b"\0")
    (tmp_path / "linked.kt").symlink_to(tmp_path / "small.kt")
    with pytest.raises(ScanError):
        Snapshot.from_directory(tmp_path, "not-a-commit")
    snapshot = Snapshot.from_directory(tmp_path, SHA, max_file_bytes=35)
    assert set(snapshot.files) == {"small.kt"}
    assert {warning.code for warning in snapshot.skipped} == {
        "NON_TEXT_FILE", "NON_REGULAR_FILE", "SIZE_LIMIT",
    }


def test_sca_gradle_alias_and_distinct_versions_per_configuration():
    snapshot = Snapshot(SHA, {
        "app/build.gradle.kts": 'dependencies { implementation(libs.demo.asset) }',
        "gradle/libs.versions.toml": """
[versions]
asset = "1.2"
[libraries]
demo-asset = { module = "demo:asset", version.ref = "asset" }
""",
        "app/gradle.lockfile": "demo:asset:1.5=compileClasspath\ndemo:asset:1.7=runtimeClasspath\n",
    })
    result = scan_sca(snapshot, catalog())
    assert result.coverage.status == "complete"
    assert [(item.metadata["version"], item.metadata["configurations"]) for item in result.findings] == [
        ("1.5", "compileClasspath"),
    ]


def test_sca_explains_unresolved_coordinates_and_location():
    result = scan_sca(Snapshot(SHA, {
        "build.gradle.kts": 'dependencies {\n    implementation("demo:asset:1.+")\n}',
    }), catalog())
    warning, = result.warnings
    assert warning.code == "UNRESOLVED_VERSION"
    assert warning.path == "build.gradle.kts" and warning.line == 2
    assert "demo:asset" in warning.message and "implementation" in warning.message


def test_sca_distinguishes_missing_files_and_bad_json(tmp_path):
    with pytest.raises(ScanError) as missing:
        VulnerabilityCatalog.from_file(tmp_path / "missing.json")
    assert missing.value.warning.code == "SCA_CATALOG_NOT_FOUND"
    broken = tmp_path / "broken.json"
    broken.write_text('{\n"x": }', encoding="utf-8")
    with pytest.raises(ScanError) as malformed:
        VulnerabilityCatalog.from_file(broken)
    assert malformed.value.warning.code == "SCA_CATALOG_INVALID"
    assert malformed.value.warning.line == 2
    with pytest.raises(ScanError) as build:
        scan_sca(Snapshot(SHA, {"Sample.kt": "val id = 1"}), catalog())
    assert build.value.warning.code == "SCA_BUILD_FILE_MISSING"


def test_secrets_detects_spring_defaults_json_and_toml_without_false_positives():
    result = scan_secrets(Snapshot(SHA, {
        "Settings.kt": r'''
          @Value("\@{spring.datasource.password:fixture-spring-44}")
          val db: String = ""
          val paginationToken = "temporary-page-marker"
        '''.replace("@{", "$" + "{"),
        "config.json": '{\n  "apiKey": "fixture-json-45"\n}',
        "config.toml": '[auth]\npassword = "fixture-toml-46"\n',
        ".env": "SERVICE_TOKEN=@{SERVICE_TOKEN:-}\n".replace("@{", "$" + "{"),
    }))
    assert {(item.path, item.metadata["class"]) for item in result.findings} == {
        ("Settings.kt", "secret-fallback"), ("config.json", "api-key"), ("config.toml", "password"),
    }
    assert all(item.evidence == "***" and "fixture-" not in str(item) for item in result.findings)


@pytest.mark.parametrize(("owner_argument", "affected"), [("principal.name", False), ("owner", True)])
def test_sast_distinguishes_authenticated_owner_from_request_argument(owner_argument, affected):
    snapshot = Snapshot(SHA, {
        "src/Controller.kt": f"""
            package demo.api
            import demo.store.Cards
            @RestController
            class Controller(private val cards: Cards) {{
                @GetMapping("/cards/{{id}}")
                fun get(@PathVariable id: Long, @RequestParam owner: String, principal: Principal) =
                    cards.find(id, {owner_argument})
            }}
        """,
        "src/Cards.kt": """
            package demo.store
            class Cards(private val jdbc: JdbcTemplate) {
                fun find(id: Long, owner: String) =
                    jdbc.queryForList("SELECT owner_id FROM card WHERE id = ? AND owner_id = ?", id, owner)
            }
        """,
    })
    result = scan_sast(snapshot)
    assert result.coverage.status == "complete"
    assert [item.metadata["class"] for item in result.findings] == (["object-authorization"] if affected else [])


def test_worker_keeps_other_results_when_catalog_fails(tmp_path):
    (tmp_path / "Sample.kt").write_text("""
        import org.springframework.jdbc.core.JdbcTemplate

        class Sample(private val jdbc: JdbcTemplate) {
            val password = "fixture-sensitive-97"
            fun search(@RequestParam reference: String) =
                jdbc.queryForList("SELECT id FROM item WHERE reference = '$reference'")
        }
    """)
    (tmp_path / "invalid.json").write_text("{}")
    saved = []
    report = run_analysis(tmp_path, SHA, tmp_path / "invalid.json", save_node=saved.append)
    assert tuple(saved) == report.nodes
    assert report.status == "partial"
    assert [node.status for node in report.nodes] == ["succeeded", "failed", "succeeded"]
    assert report.nodes[1].warning.code == "SCA_CATALOG_INVALID"
    assert report.nodes[1].warning.field == "formatVersion"
    assert report.summary.total == report.summary.high_count == 2
    exported = submission_json(report)
    assert {item["category"] for item in json.loads(exported)["findings"]} == {"SECRET", "SAST"}
    assert "fixture-sensitive-97" not in exported + readable_report(report)


def test_inactive_branch_does_not_make_a_clean_report_partial():
    result = scan_secrets(Snapshot(SHA, {"src/Service.kt": "val id = 1"}))
    report = merge_findings(SHA, (
        NodeResult("secrets", "succeeded", result),
        NodeResult("sca", "skipped", warning=Warning("INACTIVE_BRANCH", "Ветка не выбрана.")),
    ))
    assert report.status == "succeeded"
    assert json.loads(submission_json(report)) == {"findings": []}


def test_worker_distinguishes_unavailable_snapshot_and_timeout(tmp_path):
    failed = run_analysis(tmp_path / "missing", SHA, None, scans=("secrets",))
    assert failed.status == "failed"
    assert failed.nodes[0].warning.code == "SNAPSHOT_UNAVAILABLE"
    with pytest.raises(ValueError):
        submission_json(failed)
    timed_out = run_analysis(tmp_path, SHA, None, scans=("sast",), timeout=0.000001)
    assert timed_out.status == "failed"
    assert timed_out.nodes[0].warning.code == "SCAN_TIMEOUT"


def matches(actual, expected):
    if actual["category"] != expected["category"]:
        return False
    if actual["category"] == "SCA":
        return (
            all(actual[key] == expected[key] for key in ("path", "group", "artifact", "version"))
            and actual["advisoryId"] in expected["advisoryIds"]
        )
    locations = [expected, *expected.get("alternateLocations", [])]
    return actual["class"] == expected["class"] and any(
        actual["path"] == location["path"] and actual["line"] <= location.get("endLine", location["line"])
        and location["line"] <= actual.get("endLine", actual["line"])
        for location in locations
    )


@pytest.mark.skipif(not os.environ.get("TRAINING_DATASET"), reason="set TRAINING_DATASET to the organizers' checkout")
def test_cli_matches_training_findings_and_controls(tmp_path):
    dataset = Path(os.environ["TRAINING_DATASET"]).resolve()
    commit = subprocess.check_output(
        ["git", "-C", str(dataset), "rev-parse", "HEAD"], text=True
    ).strip()
    json_out, markdown_out = tmp_path / "findings.json", tmp_path / "report.md"
    command = [
        sys.executable, "-m", "backend.app.worker", str(dataset / "training-project"),
        "--sha", commit,
        "--catalog", str(dataset / "vulnerability-database" / "vulnerabilities.json"),
        "--json-out", str(json_out), "--markdown-out", str(markdown_out),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    assert completed.returncode in {0, 1}, completed.stderr
    actual = json.loads(json_out.read_text(encoding="utf-8"))["findings"]
    expected = json.loads((dataset / "ground-truth" / "training-findings.json").read_text(encoding="utf-8"))
    assert Counter(item["category"] for item in actual) == {"SECRET": 5, "SCA": 5, "SAST": 5}
    assert all(sum(matches(item, target) for item in actual) == 1 for target in expected["findings"])
    assert not any(matches(item, control) for item in actual for control in expected["controls"])
    markdown = markdown_out.read_text(encoding="utf-8")
    assert sum(line.startswith(("| SECRET |", "| SCA |", "| SAST |")) for line in markdown.splitlines()) == len(actual)
