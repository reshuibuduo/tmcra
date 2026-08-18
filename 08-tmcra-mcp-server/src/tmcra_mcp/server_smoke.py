from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from mcp.shared.memory import create_connected_server_and_client_session

from .config import MCPSettings
from .server import create_server


async def _run() -> dict[str, object]:
    expected = os.getenv("TMCRA_SMOKE_EXPECTED_TEXT", "").strip()
    if not expected:
        raise RuntimeError("TMCRA_SMOKE_EXPECTED_TEXT is required")
    server = create_server(MCPSettings.from_env())
    client = getattr(server, "_tmcra_client")
    try:
        async with create_connected_server_and_client_session(server) as session:
            listed = await session.list_tools()
            names = sorted(tool.name for tool in listed.tools)
            result = await session.call_tool(
                "tmcra_recall",
                {"query": "What is my launch verification code?"},
            )
            if result.isError:
                raise RuntimeError("MCP recall returned a protocol error")
            content = str(
                ((result.structuredContent or {}).get("prompt_evidence") or {}).get(
                    "content", ""
                )
            )
            if expected not in content:
                raise RuntimeError("MCP recall did not contain the expected evidence")
            return {
                "schema_version": "tmcra.mcp-server-smoke.1",
                "status": "passed",
                "listed_tools": names,
                "protocol_error": False,
                "recalled_expected_text": True,
            }
    finally:
        await client.aclose()


def main() -> int:
    report = asyncio.run(_run())
    report_path = os.getenv("TMCRA_SMOKE_REPORT", "").strip()
    if report_path:
        Path(report_path).write_text(
            json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(report_path, 0o600)
        except OSError:
            pass
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
