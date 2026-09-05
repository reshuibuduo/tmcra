"""Local, credential-isolated memory controls; JSON contract shared with JS plugins."""
from __future__ import annotations
import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def control_key(settings: Any, scope: str) -> str:
    return _hash(f"{settings.base_url.rstrip('/')}\0{settings.api_key}\0{scope}")


def _root() -> Path:
    return Path(os.getenv("TMCRA_MEMORY_STATE_DIR") or (str(Path(os.environ["PLUGIN_DATA"]) / "memory-controls") if os.getenv("PLUGIN_DATA") else str(Path.home() / ".config/tmcra/memory-controls")))


def _read(key: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{64}", key):
        raise ValueError("Invalid memory control key")
    try:
        value = json.loads((_root() / f"{key}.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schemaVersion": 1, "sessions": {}, "tasks": {}, "recent": [], "budgetChars": 12000}
    if value.get("schemaVersion") != 1:
        raise ValueError("Unsupported memory controls version")
    return value


@contextmanager
def _edit(key: str):
    _read(key)
    _root().mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = _root() / f"{key}.lock"
    for _ in range(60):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            time.sleep(.025)
    else:
        raise RuntimeError("Memory controls busy; inspect the local lock")
    temporary = _root() / f"{key}.{uuid.uuid4()}.tmp"
    try:
        state = _read(key)
        yield state
        with os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, _root() / f"{key}.json")
    finally:
        temporary.unlink(missing_ok=True)
        os.close(fd)
        lock.unlink()


def _session(state: dict, session_id: str) -> dict:
    if not session_id.strip():
        raise ValueError("Exact session_id is required")
    return state["sessions"].setdefault(_hash(session_id), {"mode": "normal", "generation": 0, "taskId": None})


def policy(key: str, session_id: str) -> dict:
    state = _read(key)
    row = _session(state, session_id)
    parent = _session(state, session_id.split(":subagent:")[0]) if ":subagent:" in session_id else None
    return {"key": key, "sessionId": session_id, "mode": row["mode"], "generation": row["generation"],
            "turnHash": row.get("currentTurnHash"), "parentTurnHash": parent.get("currentTurnHash") if parent else None,
            "parentGeneration": parent["generation"] if parent else None,
            "read": row["mode"] != "off" and (not parent or parent["mode"] != "off"),
            "write": row["mode"] == "normal" and (not parent or parent["mode"] == "normal")}


def may_write(capture: dict | None) -> bool:
    if not capture or not capture.get("write"):
        return False
    current = policy(capture["key"], capture["sessionId"])
    state = _read(capture["key"])
    def allowed(session_id, turn_hash):
        row = _session(state, session_id)
        return not row.get("suppressedTurns", {}).get(turn_hash) if turn_hash else not row.get("suppressLegacyCapture")
    return (current["write"] and current["generation"] == capture["generation"]
            and current.get("parentGeneration") == capture.get("parentGeneration")
            and allowed(capture["sessionId"], capture.get("turnHash"))
            and (":subagent:" not in capture["sessionId"] or allowed(capture["sessionId"].split(":subagent:")[0], capture.get("parentTurnHash"))))


def begin_turn(key: str, session_id: str, turn_id: str) -> dict:
    if not turn_id.strip():
        raise ValueError("An exact host turn ID is required")
    if policy(key, session_id)["write"]:
        with _edit(key) as state:
            _session(state, session_id)["currentTurnHash"] = _hash(turn_id)
    return policy(key, session_id)


def suppress_turn(key: str, session_id: str) -> dict:
    with _edit(key) as state:
        row = _session(state, session_id)
        if row.get("currentTurnHash"):
            row.setdefault("suppressedTurns", {})[row["currentTurnHash"]] = True
        row["suppressLegacyCapture"] = True
        return {"automaticCapture": "suppressed", "turnIdentified": bool(row.get("currentTurnHash")), "originalMemoryChanged": False}


def control(key: str, session_id: str, action: str, args: dict) -> dict:
    if action == "correction_start":
        return suppress_turn(key, session_id)
    if action == "dashboard":
        state = _read(key)
        current = policy(key, session_id)
        current.pop("key")
        return {"policy": current, "tasks": list(state["tasks"].values()), "budgetChars": state["budgetChars"],
                "recent": [item for item in state["recent"] if item.get("sessionKey") == _hash(session_id)]}
    with _edit(key) as state:
        row = _session(state, session_id)
        if action == "mode":
            mode = args.get("mode")
            if mode not in {"normal", "recall_only", "off"}:
                raise ValueError("Invalid memory mode")
            if row["mode"] != mode:
                row["generation"] += 1
            row["mode"] = mode
            if mode != "normal":
                row["taskId"] = None
            return {"mode": mode, "generation": row["generation"], "disabledContentBackfill": False}
        if action == "budget":
            budget = args.get("budgetChars")
            if type(budget) is not int or not 1000 <= budget <= 64000:
                raise ValueError("budgetChars must be 1000..64000")
            state["budgetChars"] = budget
            return {"budgetChars": budget}
        if action == "task":
            if row["mode"] != "normal":
                raise ValueError("Task capture is disabled")
            task_id = args.get("id") or f"task_{uuid.uuid4()}"
            if args.get("id") and task_id not in state["tasks"]:
                raise ValueError("Unknown task in this scope")
            task = dict(state["tasks"].get(task_id, {}))
            for field in ("objective", "summary", "nextStep"):
                if field in args:
                    task[field] = str(args[field]).strip()[:4000]
            task.update(id=task_id, status=args.get("status", "active"), updatedAt=datetime.now(timezone.utc).isoformat())
            if not task.get("objective") or task["status"] not in {"active", "completed", "blocked"}:
                raise ValueError("Task objective and valid status required")
            state["tasks"][task_id] = task
            row["taskId"] = task_id if task["status"] == "active" else None
            return task
        raise ValueError("Unknown memory control action")


def continuation(key: str, session_id: str, prompt: str) -> dict:
    if not re.fullmatch(r"(?:好的?[,，\s]*)?(?:继续|接着[做来]?|往下[做走]?|补齐这些|完成这些|continue|resume|go on|carry on)[。.!！\s]*", prompt.strip(), re.I):
        return {"query": prompt, "task": None, "candidates": []}
    state = _read(key)
    tasks = [task for task in state["tasks"].values() if task["status"] == "active"]
    task = state["tasks"].get(_session(state, session_id).get("taskId"))
    if not task or task["status"] != "active":
        task = tasks[0] if len(tasks) == 1 else None
    if not task:
        return {"query": prompt, "task": None, "candidates": [{"id": t["id"], "objective": t["objective"]} for t in tasks]}
    return {"query": f"{prompt}\nCurrent task: {task['objective']}\nLast observed result: {task.get('summary', '')}\nNext step: {task.get('nextStep', '')}", "task": task, "candidates": []}


def select_evidence(content: str, budget: int, visible: str = "") -> dict:
    selected = []
    seen = set()
    omitted = []
    used = 0
    for block in re.split(r"\n\n(?=\[(?:Immutable |Slow memory |Fast memory |TMCRA actor section))", content):
        digest = _hash(block)
        reason = "duplicate" if digest in seen or (visible and block in visible) else "budget" if used + len(block) + 2 > budget else None
        seen.add(digest)
        if reason:
            omitted.append({"hash": digest, "reason": reason})
        else:
            selected.append(block)
            used += len(block) + 2
    return {"content": "\n\n".join(selected), "omitted": omitted, "characters": used, "estimatedTokens": (used + 2) // 3, "tokenEstimateOnly": True}
