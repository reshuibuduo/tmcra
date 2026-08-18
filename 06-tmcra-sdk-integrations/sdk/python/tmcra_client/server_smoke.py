"""Real remote write -> wait -> recall verification for the Python SDK."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from .client import SyncClient
from .lifecycle import AutomaticLifecycleConfig, PreparedTurn, SyncMemoryLifecycle


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
        raise RuntimeError("Python SDK answer-agent response was invalid")
    return {"answer": payload["answer"], "model": str(payload.get("model", "unknown"))}


def main() -> int:
    api_key = os.getenv("TMCRA_API_KEY", "").strip()
    scope = os.getenv("TMCRA_DEFAULT_SCOPE", "").strip()
    if not api_key or not scope:
        raise RuntimeError("TMCRA_API_KEY and TMCRA_DEFAULT_SCOPE are required")
    run_id = uuid.uuid4().hex
    marker = (
        os.getenv("TMCRA_SMOKE_MARKER", "").strip()
        or f"PYTHON_SDK_SHARED_HANDOFF_{run_id.upper()}"
    )
    agent_a = f"python-planning-agent-{run_id[:12]}"
    agent_b = f"python-implementation-agent-{run_id[:12]}"
    session_a = f"python-planning-session-{run_id}"
    session_b = f"python-implementation-session-{run_id}"
    with SyncClient(
        os.getenv("TMCRA_BASE_URL", "https://api.tmcra.com"),
        api_key,
        timeout=60,
    ) as client:
        health = client.healthz()
        readiness = client.readyz()
        common = {
            "project_scope": scope,
            "global_scope": os.getenv("TMCRA_GLOBAL_SCOPE", "").strip() or None,
            "job_timeout_seconds": float(
                os.getenv("TMCRA_SMOKE_TIMEOUT_SECONDS", "1800")
            ),
        }
        lifecycle_a = SyncMemoryLifecycle(
            client,
            AutomaticLifecycleConfig(
                **common,
                agent_id=agent_a,
                agent_metadata={
                    "agent_id": agent_a,
                    "agent_role": "planner",
                    "smoke": True,
                },
            ),
        )
        lifecycle_b = SyncMemoryLifecycle(
            client,
            AutomaticLifecycleConfig(
                **common,
                agent_id=agent_b,
                agent_metadata={
                    "agent_id": agent_b,
                    "agent_role": "implementer",
                    "smoke": True,
                },
            ),
        )
        seeded = lifecycle_a.run_turn(
            f"Store this exact shared release handoff identifier: {marker}.",
            lambda _prepared: f"Planning agent stored shared handoff {marker}.",
            session_id=session_a,
        )
        question = "Which exact shared release handoff identifier did the planning agent leave?"
        answer_state: dict[str, str] = {}

        def answer_as_agent_b(prepared: PreparedTurn) -> str:
            if marker not in prepared.system_context:
                raise RuntimeError(
                    "Python SDK implementation agent did not recall planning-agent memory"
                )
            answered = _answer(question, prepared.system_context)
            if answered:
                if marker not in answered["answer"]:
                    raise RuntimeError("Python SDK answer agent did not use recalled memory")
                answer_state.update(answered)
                return answered["answer"]
            return f"Implementation agent recalled shared handoff {marker}."

        recalled = lifecycle_b.run_turn(
            question,
            answer_as_agent_b,
            session_id=session_b,
        )
        report = {
            "schema_version": "tmcra.python-sdk-multi-agent-server-smoke.2",
            "status": "passed",
            "health": health.status,
            "readiness": readiness.status,
            "automatic_lifecycle": True,
            "shared_project_scope": True,
            "distinct_agent_sessions": session_a != session_b,
            "recall_before_agent_b_write": True,
            "job_ids": [seeded.job_id, recalled.job_id],
            "job_statuses": [seeded.job_status, recalled.job_status],
            "agent_ids": [agent_a, agent_b],
            "recalled_marker": True,
            "roles_written": ["user", "assistant"],
            "answer_agent": (
                {"verified": True, "model": answer_state["model"]}
                if answer_state
                else {"verified": False}
            ),
        }
    report_path = os.getenv("TMCRA_SMOKE_REPORT", "").strip()
    if report_path:
        Path(report_path).write_text(
            json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
