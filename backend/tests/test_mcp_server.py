import asyncio
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import UUID

import pytest

mcp = pytest.importorskip("mcp")
from mcp.client.stdio import stdio_client


def test_mcp_stdio_roundtrip():
    run_id = str(UUID(int=1))
    token = "mcp_" + "x" * 43
    row = dict(id="f1", category="secrets", rule_id="secret.password", severity="high",
               title="Password", path="demo.kt", line=3, end_line=3, evidence="PRIVATE",
               metadata={"other": "PRIVATE"})

    class Api(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers.get("Authorization") == "Bearer " + token
            assert self.headers.get("Cookie") is None
            if self.path == "/api/mcp/runs?offset=0&limit=20&status=succeeded":
                payload = {"total": 1, "items": [{
                    "id": run_id, "status": "succeeded", "created_at": "2026-01-01T00:00:00Z",
                    "project_name": "Project", "workflow_name": "Workflow", "source": "PRIVATE",
                }]}
            elif self.path == f"/api/mcp/runs/{run_id}/summary":
                payload = {"id": run_id, "status": "succeeded", "summary": {"total": 1},
                           "coverage": "complete", "ai_status": None, "explanation": "PRIVATE"}
            else:
                assert self.path == f"/api/mcp/runs/{run_id}/findings?offset=0&limit=20"
                payload = {"items": [row], "total": 1}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

    async def roundtrip(port):
        server = mcp.StdioServerParameters(command=sys.executable, args=["-m", "backend.app.mcp_server"],
                                           env={"CYBERFLOW_API_URL": f"http://127.0.0.1:{port}/api",
                                                "CYBERFLOW_MCP_TOKEN": token, "PYTHONUTF8": "1"})
        async with stdio_client(server) as streams, mcp.ClientSession(*streams) as session:
            await session.initialize()
            assert {t.name for t in (await session.list_tools()).tools} == {
                "node_catalog", "check_workflow", "prepare_workflow_draft", "list_runs", "run_summary", "run_findings"}
            assert {p.name for p in (await session.list_prompts()).prompts} == {"draft_workflow", "review_run"}
            catalog = await session.call_tool("node_catalog", {})
            assert any(item["type"] == "ai_report" for item in catalog.structuredContent["items"])
            draft = await session.call_tool("check_workflow", {"graph": {"nodes": [], "edges": []}})
            assert draft.structuredContent["valid"] is False
            prepared = await session.call_tool("prepare_workflow_draft", {"graph": {"nodes": [], "edges": []}})
            assert prepared.structuredContent == {"graph": {"nodes": [], "edges": []}, "issues": []}
            runs = await session.call_tool("list_runs", {"status": "succeeded"})
            assert runs.structuredContent["items"][0]["id"] == run_id and "PRIVATE" not in str(runs.content)
            summary = await session.call_tool("run_summary", {"run_id": run_id})
            assert summary.structuredContent["coverage"] == "complete" and "PRIVATE" not in str(summary.content)
            page = await session.call_tool("run_findings", {"run_id": run_id})
            assert page.structuredContent["items"][0]["id"] == "f1"
            assert "PRIVATE" not in str(page.content)

    with ThreadingHTTPServer(("127.0.0.1", 0), Api) as api:
        thread = Thread(target=api.serve_forever, daemon=True)
        thread.start()
        try:
            asyncio.run(asyncio.wait_for(roundtrip(api.server_port), timeout=45))
        finally:
            api.shutdown()
            thread.join()
