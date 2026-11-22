import json
from string import Template
from typing import Literal
from uuid import UUID

RunnerMode = Literal["local", "hosted"]

_WORKFLOW = Template('''name: CyberFlow Security
on:
  push:
    branches: [$branch]
  pull_request_target:
    branches: [$branch]
    types: [opened, reopened, synchronize, edited]
permissions: {}
jobs:
  cyberflow:
    if: $${{ github.event_name == 'push' && !github.event.deleted }}
    runs-on: $runner
    defaults:
      run:
        shell: $shell
    timeout-minutes: 12
    env:
      SOURCE_SHA: $${{ github.sha }}
    steps:
$scan_step
  report-pr:
    name: Report CyberFlow verdict
    if: github.event_name == 'pull_request_target' && github.repository == 'TBagirov/training'
    runs-on: ubuntu-latest
    timeout-minutes: 12
    defaults:
      run:
        shell: pwsh
    permissions:
      statuses: write
    env:
      SOURCE_SHA: $${{ github.event.pull_request.head.sha }}
    steps:
      - name: Mark PR check pending
        shell: bash
        env:
          GH_TOKEN: $${{ github.token }}
          STATE: pending
          DESCRIPTION: CyberFlow is scanning this commit
        run: $publish_status
      - *scan
      - name: Publish verdict on the checked commit
        if: always()
        shell: bash
        env:
          GH_TOKEN: $${{ github.token }}
          SCAN_OUTCOME: $${{ steps.scan.outcome }}
          DECISION: $${{ steps.scan.outputs.decision }}
        run: |
          case "$$SCAN_OUTCOME/$$DECISION" in
            success/*) STATE=success; DESCRIPTION='CyberFlow passed' ;;
            failure/blocked) STATE=failure; DESCRIPTION='CyberFlow blocked the commit' ;;
            *) STATE=error; DESCRIPTION='CyberFlow scan error' ;;
          esac
          $publish_status
''')

_PUBLISH_STATUS = (
    'gh api --method POST "repos/$GITHUB_REPOSITORY/statuses/$SOURCE_SHA" '
    '-f context="CyberFlow PR gate" -f state="$STATE" -f description="$DESCRIPTION" '
    '-f target_url="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"'
)

_SCAN_STEP = Template('''      - &scan
        name: Check commit with CyberFlow
        id: scan
        env:
          CYBERFLOW_API_URL: $${{ vars.CYBERFLOW_API_URL }}
          CYBERFLOW_TOKEN: $${{ secrets.CYBERFLOW_TOKEN }}
          CYBERFLOW_WORKFLOW_ID: "$workflow_id"
          SOURCE_REPOSITORY: $${{ github.repository }}
          SOURCE_RUN_ID: $${{ github.run_id }}
          SOURCE_RUN_ATTEMPT: $${{ github.run_attempt }}
        run: |
          $$ErrorActionPreference = 'Stop'
          $$baseUrl = $$env:CYBERFLOW_API_URL
          $$localPush = $allow_loopback -and $$env:GITHUB_EVENT_NAME -eq 'push'
          if (-not $$baseUrl -and $$localPush) { $$baseUrl = 'http://127.0.0.1:8000' }
          if (-not $$baseUrl -or -not $$env:CYBERFLOW_TOKEN) {
            throw 'Configure CYBERFLOW_API_URL and the CYBERFLOW_TOKEN secret.'
          }
          $$uri = [Uri]$$baseUrl
          if (-not $$uri.IsAbsoluteUri -or $$uri.UserInfo -or $$uri.Query -or $$uri.Fragment -or
              ($$uri.Scheme -ne 'https' -and -not ($$uri.Scheme -eq 'http' -and $$uri.IsLoopback -and $$localPush))) {
            throw 'Use an HTTPS CyberFlow API URL, or a loopback HTTP URL in local mode.'
          }
          $$endpoint = "$$($$baseUrl.TrimEnd('/'))/api/hooks/$$env:CYBERFLOW_WORKFLOW_ID"
          $$headers = @{ Authorization = "Bearer $$env:CYBERFLOW_TOKEN" }
          $$body = @{
            repository_url = "https://github.com/$$env:SOURCE_REPOSITORY"
            commit_sha = $$env:SOURCE_SHA
            request_id = "$$($$env:SOURCE_RUN_ID)-$$env:SOURCE_RUN_ATTEMPT"
          } | ConvertTo-Json -Compress
          $$run = Invoke-RestMethod -Method Post -Uri $$endpoint -Headers $$headers -ContentType 'application/json' -Body $$body -TimeoutSec 15 -MaximumRedirection 0
          $$deadline = [DateTime]::UtcNow.AddMinutes(10)
          do {
            $$result = Invoke-RestMethod -Uri "$$endpoint/runs/$$($$run.id)" -Headers $$headers -TimeoutSec 15 -MaximumRedirection 0
            if ($$result.gate) { break }
            Start-Sleep -Seconds 2
          } while ([DateTime]::UtcNow -lt $$deadline)
          if (-not $$result.gate) { throw "CyberFlow timed out. Run $$($$run.id)." }
          if ($$result.report_url) { Write-Output "Report: $$($$result.report_url)" }
          if ($$result.gate.decision -ne 'error' -and $$result.commit_sha -ne $$env:SOURCE_SHA) {
            throw "CyberFlow error: SHA mismatch. Expected $$env:SOURCE_SHA, received $$($$result.commit_sha)."
          }
          if ($$result.gate.decision -eq 'passed' -and $$result.status -ne 'succeeded') {
            throw "CyberFlow error: incomplete scan for $$env:SOURCE_SHA."
          }
          Write-Output "Commit $$env:SOURCE_SHA. CyberFlow: $$($$result.gate.decision), $$($$result.gate.reason)."
          if ($$result.gate.decision -eq 'blocked' -and $$env:GITHUB_OUTPUT) {
            Add-Content -LiteralPath $$env:GITHUB_OUTPUT -Value 'decision=blocked' -Encoding utf8
          }
          if ($$result.gate.decision -ne 'passed') { exit 1 }
''')


def security_workflow(workflow_id: UUID, branch: str, mode: RunnerMode) -> str:
    local = mode == "local"
    return _WORKFLOW.substitute(
        branch=json.dumps(branch), runner="[self-hosted, Windows, cyberflow]" if local else "ubuntu-latest",
        shell="powershell" if local else "pwsh", publish_status=_PUBLISH_STATUS,
        scan_step=_SCAN_STEP.substitute(workflow_id=workflow_id, allow_loopback="$true" if local else "$false").rstrip(),
    )
