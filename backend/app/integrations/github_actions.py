import json
from string import Template
from typing import Literal
from uuid import UUID

RunnerMode = Literal["local", "hosted"]

_WORKFLOW = Template('''name: CyberFlow Security
on:
  push:
    branches: [$branch]
permissions: {}
jobs:
  cyberflow:
    if: $${{ !github.event.deleted }}
    runs-on: $runner
    timeout-minutes: 12
    steps:
      - name: Check commit with CyberFlow
        shell: $shell
        env:
          CYBERFLOW_API_URL: $${{ vars.CYBERFLOW_API_URL }}
          CYBERFLOW_TOKEN: $${{ secrets.CYBERFLOW_TOKEN }}
          CYBERFLOW_WORKFLOW_ID: "$workflow_id"
          SOURCE_REPOSITORY: $${{ github.repository }}
          SOURCE_SHA: $${{ github.sha }}
          SOURCE_RUN_ID: $${{ github.run_id }}
          SOURCE_RUN_ATTEMPT: $${{ github.run_attempt }}
        run: |
          $$ErrorActionPreference = 'Stop'
          $$baseUrl = $$env:CYBERFLOW_API_URL
          if (-not $$baseUrl) { $$baseUrl = '$default_api_url' }
          if (-not $$baseUrl -or -not $$env:CYBERFLOW_TOKEN) {
            throw 'Configure CYBERFLOW_API_URL and the CYBERFLOW_TOKEN secret.'
          }
          $$uri = [Uri]$$baseUrl
          if (-not $$uri.IsAbsoluteUri -or $$uri.UserInfo -or $$uri.Query -or $$uri.Fragment -or
              ($$uri.Scheme -ne 'https' -and -not ($$uri.Scheme -eq 'http' -and $$uri.IsLoopback -and $allow_loopback))) {
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
          Write-Output "Commit $$env:SOURCE_SHA. CyberFlow: $$($$result.gate.decision), $$($$result.gate.reason)."
          if ($$result.gate.decision -ne 'passed' -or $$result.commit_sha -ne $$env:SOURCE_SHA) { exit 1 }
''')


def security_workflow(workflow_id: UUID, branch: str, mode: RunnerMode) -> str:
    local = mode == "local"
    return _WORKFLOW.substitute(
        branch=json.dumps(branch), workflow_id=str(workflow_id),
        runner="[self-hosted, Windows, cyberflow]" if local else "ubuntu-latest",
        shell="powershell" if local else "pwsh",
        default_api_url="http://127.0.0.1:8000" if local else "",
        allow_loopback="$true" if local else "$false",
    )
