"""MCP server: tool functions and a real stdio round trip."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from chartcleaner import mcp_server  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_tools_wrap_the_service_layer():
    assert mcp_server.abbreviate("Hypertension") == {"text": "HTN", "replacements": {"HTN": 1}}
    out = mcp_server.expand_abbreviations("MS and HTN")
    assert out["text"] == "MS and hypertension" and out["ambiguous"] == {"MS": 1}
    names = [t["name"] for t in mcp_server.list_prompt_templates()]
    assert "Progress note" in names


def test_server_lists_every_tool():
    server = mcp_server.build_server()
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == {fn.__name__ for fn in mcp_server.TOOLS}


def test_stdio_round_trip(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "chartcleaner.mcp_server"],
                                       cwd=str(ROOT), env={**os.environ,
                                                           "CHARTCLEANER_DATA_DIR": str(tmp_path)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("abbreviate", {"text": "Hypertension noted"})
                return result.structured_content

    assert asyncio.run(asyncio.wait_for(go(), 60))["text"] == "HTN noted"
    assert (tmp_path / "stats.jsonl").exists()  # history went to the override, not data/
