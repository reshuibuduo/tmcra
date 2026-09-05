from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from mcp.shared.memory import create_connected_server_and_client_session

from .config import MCPSettings
from .server import create_server


def _answer(question: str, memory_context: str) -> dict[str, str] | None:
    url = os.getenv("TMCRA_ANSWER_AGENT_URL", "").strip()
    if not url:
        return None
    request = Request(
        url,
        data=json.dumps({"question": question, "memory_context": memory_context}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urlopen(request, timeout=120) as response:
        payload = json.load(response)
    if not payload.get("ok") or not isinstance(payload.get("answer"), str):
        raise RuntimeError("MCP answer-agent response was invalid")
    return {"answer": payload["answer"], "model": str(payload.get("model", "unknown"))}


async def _run() -> dict[str, object]:
    run_id = uuid.uuid4().hex
    marker = (
        os.getenv("TMCRA_SMOKE_MARKER", "").strip()
        or f"MCP_SHARED_HANDOFF_{run_id.upper()}"
    )
    agent_a = f"mcp-planning-agent-{run_id[:12]}"
    agent_b = f"mcp-implementation-agent-{run_id[:12]}"
    session_a = f"mcp-planning-session-{run_id}"
    session_b = f"mcp-implementation-session-{run_id}"
    server = create_server(MCPSettings.from_env())
    client = getattr(server, "_tmcra_client")
    try:
        async with create_connected_server_and_client_session(server) as session:
            listed = await session.list_tools()
            names = sorted(tool.name for tool in listed.tools)
            now = datetime.now(timezone.utc).isoformat()
            written = await session.call_tool(
                "tmcra_ingest",
                {
                    "session_id": session_a,
                    "messages": [
                        {
                            "message_id": f"user-{uuid.uuid4()}",
                            "role": "user",
                            "content": f"Store this exact shared release handoff identifier: {marker}.",
                            "timestamp": now,
                        },
                        {
                            "message_id": f"assistant-{uuid.uuid4()}",
                            "role": "assistant",
                            "content": f"Planning agent stored shared handoff {marker}.",
                            "timestamp": now,
                        },
                    ],
                    "agent_id": agent_a,
                    "consistency": "read_your_writes",
                    "slow_policy": "auto",
                    "idempotency_key": f"mcp-smoke-{uuid.uuid4()}",
                },
            )
            if written.isError:
                raise RuntimeError("MCP ingest returned a protocol error")
            job_a = str((written.structuredContent or {}).get("job_id") or "")
            if not job_a:
                raise RuntimeError("MCP ingest did not return a job ID")
            completed = await session.call_tool(
                "tmcra_wait_job",
                {
                    "job_id": job_a,
                    "timeout_seconds": min(
                        900.0,
                        float(os.getenv("TMCRA_SMOKE_TIMEOUT_SECONDS", "900")),
                    ),
                    "poll_interval_seconds": 1.5,
                },
            )
            if completed.isError:
                raise RuntimeError("MCP job wait returned a protocol error")
            job_status = str((completed.structuredContent or {}).get("status") or "")
            if job_status != "succeeded":
                raise RuntimeError(f"MCP ingest job ended as {job_status}")
            question = "Which exact shared release handoff identifier did the planning agent leave?"
            result = await session.call_tool(
                "tmcra_recall",
                {
                    "query": question,
                    "wait_for_job_id": job_a,
                },
            )
            if result.isError:
                raise RuntimeError("MCP recall returned a protocol error")
            content = str(
                ((result.structuredContent or {}).get("prompt_evidence") or {}).get(
                    "content", ""
                )
            )
            if marker not in content:
                raise RuntimeError("MCP recall did not contain the written marker")
            answered = await asyncio.to_thread(_answer, question, content)
            if answered and marker not in answered["answer"]:
                raise RuntimeError("MCP answer agent did not use recalled memory")
            assistant_answer = (
                answered["answer"]
                if answered
                else f"Implementation agent recalled shared handoff {marker}."
            )
            written_b = await session.call_tool(
                "tmcra_ingest",
                {
                    "session_id": session_b,
                    "messages": [
                        {
                            "message_id": f"user-{uuid.uuid4()}",
                            "role": "user",
                            "content": question,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                        {
                            "message_id": f"assistant-{uuid.uuid4()}",
                            "role": "assistant",
                            "content": assistant_answer,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    ],
                    "agent_id": agent_b,
                    "consistency": "read_your_writes",
                    "slow_policy": "auto",
                    "idempotency_key": f"mcp-smoke-{uuid.uuid4()}",
                },
            )
            if written_b.isError:
                raise RuntimeError("MCP implementation-agent ingest returned a protocol error")
            job_b = str((written_b.structuredContent or {}).get("job_id") or "")
            if not job_b or job_b == job_a:
                raise RuntimeError("MCP implementation-agent ingest has no distinct job ID")
            completed_b = await session.call_tool(
                "tmcra_wait_job",
                {
                    "job_id": job_b,
                    "timeout_seconds": min(
                        900.0,
                        float(os.getenv("TMCRA_SMOKE_TIMEOUT_SECONDS", "900")),
                    ),
                    "poll_interval_seconds": 1.5,
                },
            )
            if completed_b.isError:
                raise RuntimeError("MCP implementation-agent job wait returned a protocol error")
            job_status_b = str((completed_b.structuredContent or {}).get("status") or "")
            if job_status_b != "succeeded":
                raise RuntimeError(f"MCP implementation-agent ingest ended as {job_status_b}")
            return {
                "schema_version": "tmcra.mcp-multi-agent-server-smoke.2",
                "status": "passed",
                "listed_tools": names,
                "shared_project_scope": True,
                "distinct_agent_sessions": session_a != session_b,
                "recall_before_agent_b_write": True,
                "job_ids": [job_a, job_b],
                "job_statuses": [job_status, job_status_b],
                "agent_ids": [agent_a, agent_b],
                "protocol_error": False,
                "recalled_marker": True,
                "roles_written": ["user", "assistant"],
                "agent_attribution_submitted": True,
                "answer_agent": (
                    {"verified": True, "model": answered["model"]}
                    if answered
                    else {"verified": False}
                ),
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
