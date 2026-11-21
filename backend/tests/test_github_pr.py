import json
import os
import shutil
import subprocess
from uuid import UUID

import pytest
import yaml

from backend.app.integrations.github_actions import security_workflow


@pytest.mark.parametrize(("decision", "status", "stale", "expected", "message"), [
    ("passed", "succeeded", False, "success", "passed"),
    ("blocked", "succeeded", False, "failure", "blocked"),
    ("error", "failed", False, "error", "error"),
    ("passed", "succeeded", True, "error", "SHA mismatch"),
    ("passed", "partial", False, "error", "incomplete"),
])
def test_exported_pr_step(tmp_path, decision, status, stale, expected, message):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("Install PowerShell to execute the exported GitHub Actions step.")
    workflow_id = UUID(int=13)
    workflow = yaml.load(security_workflow(workflow_id, "main", "hosted"), Loader=yaml.BaseLoader)
    pending, step, publish = workflow["jobs"]["report-pr"]["steps"]
    sha = "a" * 40
    run_id = str(UUID(int=1))
    result = {
        "id": run_id, "commit_sha": "b" * 40 if stale else sha, "status": status,
        "gate": {"decision": decision, "threshold": "CRITICAL", "reason": "simulation"},
    }
    # Stand in for the two authenticated API routes, then run the exported script verbatim.
    simulator = """
function Invoke-RestMethod($Method, $Uri, $Headers, $ContentType, $Body, $TimeoutSec, $MaximumRedirection) {
    $endpoint = "https://ci.example/api/hooks/$env:CYBERFLOW_WORKFLOW_ID"
    if ($Headers.Authorization -ne "Bearer simulator-token") { throw "Missing token" }
    if ($Method -eq 'Post' -and $Uri -eq $endpoint) {
        $payload = $Body | ConvertFrom-Json
        if ($payload.commit_sha -ne $env:SOURCE_SHA -or $payload.repository_url -ne 'https://github.com/TBagirov/training') {
            throw "Wrong scan source"
        }
        return @{id = $env:SIM_RUN_ID}
    }
    if ($Uri -ne "$endpoint/runs/$env:SIM_RUN_ID") { throw "Wrong status URL" }
    return $env:SIM_RESULT | ConvertFrom-Json
}
"""
    script = tmp_path / "check.ps1"
    script.write_text(simulator + step["run"], encoding="utf-8")
    outputs = tmp_path / "outputs"
    process = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(script)], env={
        **os.environ, "CYBERFLOW_API_URL": "https://ci.example", "CYBERFLOW_TOKEN": "simulator-token",
        "CYBERFLOW_WORKFLOW_ID": step["env"]["CYBERFLOW_WORKFLOW_ID"], "SOURCE_REPOSITORY": "TBagirov/training",
        "SOURCE_SHA": sha, "SOURCE_RUN_ID": "13", "SOURCE_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": "pull_request_target",
        "GITHUB_OUTPUT": str(outputs),
        "SIM_RUN_ID": run_id, "SIM_RESULT": json.dumps(result),
    }, capture_output=True, text=True, timeout=15)
    output = process.stdout + process.stderr
    outcome = "success" if process.returncode == 0 else "failure"
    assert (outcome == "success") == (expected == "success"), output
    assert message in output, output
    step_outputs = {}
    if outputs.exists():
        step_outputs = dict(line.split("=", 1) for line in outputs.read_text(encoding="utf-8-sig").splitlines())
    for publication, state in [(pending, "pending"), (publish, expected)]:
        # Capture the actual GitHub CLI request without sending a status to GitHub.
        command = "gh() { printf '%s\\n' \"$@\"; }\n" + publication["run"]
        request = subprocess.run(["bash", "-eu", "-c", command], capture_output=True, text=True, check=True, env={
            **os.environ, **publication["env"], "GITHUB_REPOSITORY": "TBagirov/training", "SOURCE_SHA": sha,
            "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": "13",
            "SCAN_OUTCOME": outcome, "DECISION": step_outputs.get("decision", ""),
        }).stdout.splitlines()
        assert request[:4] == ["api", "--method", "POST", f"repos/TBagirov/training/statuses/{sha}"]
        assert "context=CyberFlow PR gate" in request and f"state={state}" in request
