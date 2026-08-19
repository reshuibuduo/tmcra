#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from run_tmcra_v4_build import DEFAULT_WRITER_ENV, _key_pool, _load_shell_environment
from tmcra_v4_online_runtime import resolve_packing_budget


SELECTOR_SCHEMA = "tmcra.evidence-selection.v1"
ROLES = {
    "direct",
    "operand",
    "temporal_anchor",
    "historical_state",
    "current_state",
    "preference",
    "counterevidence",
    "context",
}
CONFIDENCE = {"high", "medium", "low"}
SYSTEM_PROMPT = f"""You select immutable memory evidence for answering one question.
Return exactly one JSON object using this schema:
{{"schema_version":"{SELECTOR_SCHEMA}","selected":[{{"id":"E01","role":"direct|operand|temporal_anchor|historical_state|current_state|preference|counterevidence|context"}}],"confidence":"high|medium|low","needs_review":false}}

Select the smallest complete evidence set within max_selected. Read candidate content, not only scores.
For totals, comparisons, durations, sequences, and updates, retain every operand, temporal anchor, old state, and new state required by the question.
For preferences and recommendations, retain the specific prior experiences and preferences needed for a personalized response; reject generic but topically similar material.
For ambiguous or false-premise questions, retain both the closest positive evidence and the evidence that distinguishes the requested entity or activity.
Do not answer the question. Do not summarize or rewrite evidence. Never invent IDs. Use needs_review=true when the set may be incomplete or conflicting."""
PRO_SYSTEM_PROMPT = SYSTEM_PROMPT + "\nYou are the Pro reviewer. Replace the Flash selection with the best final selection from the original candidates."


class SelectionError(RuntimeError):
    pass


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    temporary.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _usage(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise SelectionError("selector response lacks usage")

    def count(name: str, *aliases: str) -> int:
        raw = next(
            (value.get(key) for key in (name, *aliases) if value.get(key) is not None),
            0,
        )
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
            raise SelectionError(f"selector usage.{name} is invalid")
        return int(raw)

    prompt = count("prompt_tokens", "input_tokens")
    completion = count("completion_tokens", "output_tokens")
    hit = count("prompt_cache_hit_tokens", "cache_read_input_tokens", "cached_tokens")
    miss = count("prompt_cache_miss_tokens", "cache_miss_input_tokens")
    if hit > prompt or (miss and hit + miss != prompt):
        raise SelectionError("selector cache usage is inconsistent")
    if not miss:
        miss = prompt - hit
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "prompt_cache_hit_tokens": hit,
        "prompt_cache_miss_tokens": miss,
        "total_tokens": count("total_tokens") or prompt + completion,
    }


def _call(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    payload: Mapping[str, Any],
    timeout: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
        ],
        "temperature": 0,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request_sha256 = hashlib.sha256(encoded).hexdigest()
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=encoded,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.getcode())
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise SelectionError(f"selector {model} HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SelectionError(f"selector {model} request failed: {exc}") from exc
    try:
        response_body = json.loads(raw)
        choice = response_body["choices"][0]
        content = choice["message"]["content"]
        finish_reason = _clean(choice.get("finish_reason"))
        parsed = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise SelectionError(f"selector {model} returned malformed JSON") from exc
    if status != 200 or finish_reason != "stop" or not isinstance(parsed, Mapping):
        raise SelectionError(f"selector {model} did not return one complete JSON object")
    usage = _usage(response_body.get("usage"))
    return dict(parsed), {
        "model": model,
        "http_status": status,
        "latency_seconds": round(time.time() - started, 3),
        "request_sha256": request_sha256,
        "response_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "usage": usage,
    }


def _validate_selection(
    value: Mapping[str, Any],
    candidate_ids: set[str],
    max_selected: int,
    *,
    allow_empty: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SelectionError("selection is not an object")
    if value.get("schema_version") != SELECTOR_SCHEMA:
        raise SelectionError("selection schema_version is invalid")
    selected = value.get("selected")
    if (
        not isinstance(selected, list)
        or (not selected and not allow_empty)
        or len(selected) > max_selected
    ):
        raise SelectionError("selected evidence count is invalid")
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in selected:
        if not isinstance(item, Mapping):
            raise SelectionError("selected evidence entry is not an object")
        evidence_id, role = _clean(item.get("id")), _clean(item.get("role"))
        if evidence_id not in candidate_ids or evidence_id in seen or role not in ROLES:
            raise SelectionError("selected evidence identity or role is invalid")
        seen.add(evidence_id)
        output.append({"id": evidence_id, "role": role})
    confidence = _clean(value.get("confidence"))
    if confidence not in CONFIDENCE or type(value.get("needs_review")) is not bool:
        raise SelectionError("selection confidence or review flag is invalid")
    return {
        "schema_version": SELECTOR_SCHEMA,
        "selected": output,
        "confidence": confidence,
        "needs_review": bool(value["needs_review"]),
    }


def _selection_payload(row: Mapping[str, Any], max_selected: int) -> tuple[dict[str, Any], dict[str, Mapping[str, Any]]]:
    windows = list(row.get("evidence_windows") or [])
    if not windows:
        raise SelectionError("evidence row has no windows")
    by_id: dict[str, Mapping[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    for index, window in enumerate(windows, start=1):
        evidence_id = f"E{index:02d}"
        text = _clean(window.get("text"))
        if not text:
            raise SelectionError(f"{evidence_id} has empty source text")
        by_id[evidence_id] = window
        candidates.append(
            {
                "id": evidence_id,
                "session_id": _clean(window.get("session_id")),
                "session_index": int(window.get("session_index", 0)),
                "parent_chunk_index": int(window.get("parent_chunk_index", 0)),
                "text": text,
            }
        )
    plan = dict(row.get("recall_plan") or {})
    return {
        "question": _clean(row.get("question")),
        "question_date": _clean(row.get("question_date")) or "unknown",
        "query_kind": _clean(plan.get("query_kind")) or "unknown",
        "temporal_focus": _clean(plan.get("temporal_focus")) or "unknown",
        "conflict_policy": _clean(plan.get("conflict_policy")) or "surface_uncertainty",
        "max_selected": max_selected,
        "candidates": candidates,
    }, by_id


def _cost_cny(metadata: Sequence[Mapping[str, Any]], rates: Mapping[str, tuple[float, float, float]]) -> float | None:
    total = 0.0
    for item in metadata:
        usage = dict(item.get("usage") or {})
        rate = rates.get(str(item["model"]))
        if rate is None:
            return None
        prompt_rate, completion_rate, cache_rate = rate
        total += (
            int(usage.get("prompt_cache_miss_tokens", 0)) * prompt_rate
            + int(usage.get("completion_tokens", 0)) * completion_rate
            + int(usage.get("prompt_cache_hit_tokens", 0)) * cache_rate
        ) / 1_000_000.0
    return round(total, 8)


def main() -> int:
    parser = argparse.ArgumentParser(description="Select complete source evidence groups with configurable writer and reviewer models")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--qid-list", type=Path)
    parser.add_argument("--writer-env", type=Path, default=DEFAULT_WRITER_ENV)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--max-selected", type=int, default=16)
    parser.add_argument("--review-policy", choices=("low", "all"), default="low")
    args = parser.parse_args()
    if args.max_selected <= 0 or args.workers <= 0:
        raise SelectionError("workers and max-selected must be positive")
    rows = _read_jsonl(args.evidence.resolve())
    if args.qid_list:
        qids = [line.strip() for line in args.qid_list.read_text(encoding="utf-8").splitlines() if line.strip()]
        by_qid = {_clean(row.get("question_id")): row for row in rows}
        if not qids or len(qids) != len(set(qids)) or any(qid not in by_qid for qid in qids):
            raise SelectionError("qid list is empty, duplicated, or absent from evidence")
        rows = [by_qid[qid] for qid in qids]
    environment = _load_shell_environment(args.writer_env.resolve())
    keys = _key_pool(environment)
    base_url = environment.get("TMCRA_DEEPSEEK_WRITER_BASE_URL") or environment.get("TMCRA_WRITER_BASE_URL") or "https://api.deepseek.com/v1"
    writer_model = environment.get("TMCRA_WRITER_MODEL") or environment.get("TMCRA_DEEPSEEK_FLASH_MODEL") or "deepseek-v4-flash"
    reviewer_model = environment.get("TMCRA_WRITER_REVIEWER_MODEL") or environment.get("TMCRA_DEEPSEEK_PRO_MODEL") or "deepseek-v4-pro"
    out_dir = args.out_dir.resolve()
    journal_dir = out_dir / "rows"
    journal_dir.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    def select_one(index: int, row: Mapping[str, Any]) -> dict[str, Any]:
        qid = _clean(row.get("question_id"))
        artifact = journal_dir / f"{index:06d}_{qid}.json"
        if artifact.is_file():
            saved = json.loads(artifact.read_text(encoding="utf-8"))
            if saved.get("question_id") != qid:
                raise SelectionError(f"{qid}: persisted selector identity mismatch")
            return saved
        plan = dict(row.get("recall_plan") or {})
        adaptive_budget, _ = resolve_packing_budget(
            plan,
            mode="adaptive",
            fixed_k=8,
            simple_k=8,
            standard_k=12,
            complex_k=args.max_selected,
        )
        max_selected = min(args.max_selected, adaptive_budget)
        payload, by_id = _selection_payload(row, max_selected)
        flash_path = journal_dir / f"{index:06d}_{qid}.flash.json"
        if flash_path.is_file():
            flash_attempt = json.loads(flash_path.read_text(encoding="utf-8"))
            if flash_attempt.get("question_id") != qid or flash_attempt.get("stage") != "flash":
                raise SelectionError(f"{qid}: persisted Flash attempt identity mismatch")
            flash = dict(flash_attempt.get("response") or {})
            flash_meta = dict(flash_attempt.get("metadata") or {})
        else:
            flash, flash_meta = _call(
                base_url=base_url,
                api_key=keys[index % len(keys)],
                model=writer_model,
                system_prompt=SYSTEM_PROMPT,
                payload=payload,
                timeout=args.timeout,
            )
            flash_attempt = {
                "question_id": qid,
                "stage": "flash",
                "response": flash,
                "metadata": flash_meta,
                "max_selected": max_selected,
            }
            with lock:
                _atomic_json(flash_path, flash_attempt)
        flash_validation_error = ""
        try:
            selection = _validate_selection(flash, set(by_id), len(by_id))
        except SelectionError as exc:
            selection = None
            flash_validation_error = str(exc)
        calls = [flash_meta]
        reviewed = (
            args.review_policy == "all"
            or selection is None
            or len(selection["selected"]) > max_selected
            or selection["needs_review"]
            or selection["confidence"] == "low"
        )
        if reviewed:
            pro_payload = {
                **payload,
                "flash_selection": selection if selection is not None else flash,
                "flash_validation_error": flash_validation_error,
            }
            pro_path = journal_dir / f"{index:06d}_{qid}.pro.json"
            if pro_path.is_file():
                pro_attempt = json.loads(pro_path.read_text(encoding="utf-8"))
                if pro_attempt.get("question_id") != qid or pro_attempt.get("stage") != "pro":
                    raise SelectionError(f"{qid}: persisted Pro attempt identity mismatch")
                pro = dict(pro_attempt.get("response") or {})
                pro_meta = dict(pro_attempt.get("metadata") or {})
            else:
                pro, pro_meta = _call(
                    base_url=base_url,
                    api_key=keys[(index + 1) % len(keys)],
                    model=reviewer_model,
                    system_prompt=PRO_SYSTEM_PROMPT,
                    payload=pro_payload,
                    timeout=args.timeout,
                )
                with lock:
                    _atomic_json(
                        pro_path,
                        {
                            "question_id": qid,
                            "stage": "pro",
                            "response": pro,
                            "metadata": pro_meta,
                            "max_selected": max_selected,
                        },
                    )
            selection = _validate_selection(
                pro, set(by_id), max_selected, allow_empty=True
            )
            calls.append(pro_meta)
        assert selection is not None
        selected_roles = {item["id"]: item["role"] for item in selection["selected"]}
        selected_windows: list[dict[str, Any]] = []
        for evidence_id, window in by_id.items():
            if evidence_id not in selected_roles:
                continue
            item = dict(window)
            metadata = dict(item.get("retrieval_metadata") or {})
            metadata.update(
                {
                    "evidence_selector_id": evidence_id,
                    "evidence_selector_role": selected_roles[evidence_id],
                    "evidence_selector_model": calls[-1]["model"],
                }
            )
            item["retrieval_metadata"] = metadata
            selected_windows.append(item)
        output_row = dict(row)
        output_row["evidence_windows"] = selected_windows
        output_row["selected_session_ids"] = list(
            dict.fromkeys(_clean(item.get("session_id")) for item in selected_windows)
        )
        output_row["evidence_selection"] = {
            **selection,
            "reviewed_by_pro": reviewed,
            "abstained": not selected_windows,
            "flash_validation_error": flash_validation_error,
            "input_candidate_count": len(by_id),
            "selected_count": len(selected_windows),
            "max_selected": max_selected,
            "calls": calls,
        }
        result = {"question_id": qid, "row": output_row}
        with lock:
            _atomic_json(artifact, result)
        return result

    results: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(select_one, index, row): _clean(row.get("question_id")) for index, row in enumerate(rows)}
        for future in as_completed(futures):
            qid = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                failures.append({"question_id": qid, "error": f"{exc.__class__.__name__}: {exc}"})
            else:
                results[qid] = result
    if failures:
        _atomic_json(out_dir / "failures.json", {"failures": failures})
        raise SelectionError(f"evidence selection failed for {len(failures)} rows")
    ordered = [results[_clean(row.get("question_id"))]["row"] for row in rows]
    failure_path = out_dir / "failures.json"
    if failure_path.exists():
        failure_path.unlink()
    all_calls = [call for row in ordered for call in row["evidence_selection"]["calls"]]
    rates = {
        "deepseek-v4-flash": (1.0, 2.0, 0.02),
        "deepseek-v4-pro": (3.0, 6.0, 0.025),
    }
    _atomic_jsonl(out_dir / "evidence_windows.jsonl", ordered)
    selected_qids = [
        _clean(row.get("question_id"))
        for row in ordered
        if row["evidence_windows"]
    ]
    abstained_qids = [
        _clean(row.get("question_id"))
        for row in ordered
        if not row["evidence_windows"]
    ]
    (out_dir / "selected_qids.txt").write_text(
        "".join(qid + "\n" for qid in selected_qids), encoding="utf-8"
    )
    (out_dir / "abstained_qids.txt").write_text(
        "".join(qid + "\n" for qid in abstained_qids), encoding="utf-8"
    )
    _atomic_json(
        out_dir / "report.json",
        {
            "schema_version": "tmcra.v4.evidence-selector-run.1",
            "status": "complete",
            "row_count": len(ordered),
            "flash_call_count": len(ordered),
            "pro_call_count": sum(
                bool(row["evidence_selection"]["reviewed_by_pro"]) for row in ordered
            ),
            "writer_model": writer_model,
            "reviewer_model": reviewer_model,
            "physical_call_count": len(all_calls),
            "review_policy": args.review_policy,
            "selected_count_total": sum(len(row["evidence_windows"]) for row in ordered),
            "abstained_count": len(abstained_qids),
            "exact_cost_cny": _cost_cny(all_calls, rates),
        },
    )
    (out_dir / "SELECTION_COMPLETE").write_text(time.strftime("%Y-%m-%dT%H:%M:%S%z") + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
