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
    row = dict(id="f1", category="secrets", rule_id="secret.password", severity="high",
               title="Password", path="demo.kt", line=3, end_line=3, evidence="PRIVATE",
               metadata={"other": "PRIVATE"})

    class Api(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == f"/api/runs/{run_id}/findings?offset=0&limit=20"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"items": [row], "total": 1}).encode())

    async def roundtrip(port):
        server = mcp.StdioServerParameters(command=sys.executable, args=["-m", "backend.app.mcp_server"],
                                           env={"CYBERFLOW_API_URL": f"http://127.0.0.1:{port}/api",
                                                "CYBERFLOW_MCP_RUN_ID": run_id, "PYTHONUTF8": "1"})
        async with stdio_client(server) as streams, mcp.ClientSession(*streams) as session:
            await session.initialize()
            assert {t.name for t in (await session.list_tools()).tools} == {
                "node_catalog", "check_workflow", "run_summary", "run_findings"}
            assert {p.name for p in (await session.list_prompts()).prompts} == {"draft_workflow", "review_run"}
            catalog = await session.call_tool("node_catalog", {})
            assert any(item["type"] == "ai_report" for item in catalog.structuredContent["items"])
            draft = await session.call_tool("check_workflow", {"graph": {"nodes": [], "edges": []}})
            assert draft.structuredContent["valid"] is False
            page = await session.call_tool("run_findings", {})
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
