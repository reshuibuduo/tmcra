from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping


def _usage_bucket(payload: Mapping[str, Any] | None) -> Dict[str, int]:
    data = dict(payload or {})
    prompt_tokens = int(data.get("prompt_tokens", 0) or 0)
    completion_tokens = int(data.get("completion_tokens", 0) or 0)
    total_tokens = int(data.get("total_tokens", prompt_tokens + completion_tokens) or (prompt_tokens + completion_tokens))
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def response_token_usage(response: Mapping[str, Any] | None) -> Dict[str, int]:
    payload = dict(response or {})
    metadata = dict(payload.get("metadata", {}) or {})
    llm_usage = _usage_bucket(metadata.get("llm_usage", {}))
    reasoning_trace = dict((payload.get("trace", {}) or {}).get("tmcra_reasoning_v2", {}) or {})
    judge_trace = dict(reasoning_trace.get("judge_trace", {}) or {})
    judge_usage = _usage_bucket(judge_trace.get("token_usage", {}))
    return {
        "llm_prompt_tokens": llm_usage["prompt_tokens"],
        "llm_completion_tokens": llm_usage["completion_tokens"],
        "llm_total_tokens": llm_usage["total_tokens"],
        "judge_prompt_tokens": judge_usage["prompt_tokens"],
        "judge_completion_tokens": judge_usage["completion_tokens"],
        "judge_total_tokens": judge_usage["total_tokens"],
        "combined_prompt_tokens": llm_usage["prompt_tokens"] + judge_usage["prompt_tokens"],
        "combined_completion_tokens": llm_usage["completion_tokens"] + judge_usage["completion_tokens"],
        "combined_total_tokens": llm_usage["total_tokens"] + judge_usage["total_tokens"],
    }


def zero_token_usage() -> Dict[str, int]:
    return response_token_usage({})


def aggregate_token_usage(rows: Iterable[Mapping[str, Any]], *, benchmark_name: str = "") -> Dict[str, Any]:
    total_rows = 0
    llm_prompt = llm_completion = llm_total = 0
    judge_prompt = judge_completion = judge_total = 0
    combined_prompt = combined_completion = combined_total = 0
    for row in rows:
        record = dict(row)
        if "combined_total_tokens" not in record and isinstance(record.get("response"), Mapping):
            record.update(response_token_usage(record.get("response", {})))
        total_rows += 1
        llm_prompt += int(record.get("llm_prompt_tokens", 0) or 0)
        llm_completion += int(record.get("llm_completion_tokens", 0) or 0)
        llm_total += int(record.get("llm_total_tokens", 0) or 0)
        judge_prompt += int(record.get("judge_prompt_tokens", 0) or 0)
        judge_completion += int(record.get("judge_completion_tokens", 0) or 0)
        judge_total += int(record.get("judge_total_tokens", 0) or 0)
        combined_prompt += int(record.get("combined_prompt_tokens", 0) or 0)
        combined_completion += int(record.get("combined_completion_tokens", 0) or 0)
        combined_total += int(record.get("combined_total_tokens", 0) or 0)
    calls = max(1, total_rows)
    return {
        "benchmark": benchmark_name,
        "calls": total_rows,
        "llm_prompt_tokens": llm_prompt,
        "llm_completion_tokens": llm_completion,
        "llm_total_tokens": llm_total,
        "judge_prompt_tokens": judge_prompt,
        "judge_completion_tokens": judge_completion,
        "judge_total_tokens": judge_total,
        "combined_prompt_tokens": combined_prompt,
        "combined_completion_tokens": combined_completion,
        "combined_total_tokens": combined_total,
        "avg_llm_total_tokens": round(float(llm_total) / calls, 6) if total_rows else 0.0,
        "avg_judge_total_tokens": round(float(judge_total) / calls, 6) if total_rows else 0.0,
        "avg_combined_total_tokens": round(float(combined_total) / calls, 6) if total_rows else 0.0,
    }


def build_token_usage_summary(
    *,
    static_results: Mapping[str, Any] | None = None,
    long_dialog_results: Mapping[str, Any] | None = None,
    reasoner_long_dialog_results: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    static_cases = [dict(item) for item in list((static_results or {}).get("cases", []) or [])]
    reasoner_probe_rows = []
    for run in list((reasoner_long_dialog_results or {}).get("runs", []) or []):
        for probe in list(run.get("probes", []) or []):
            row = dict(probe)
            row["reasoner"] = str(run.get("reasoner", "") or "")
            row["memory"] = str(run.get("memory", "") or "")
            row["profile_id"] = str(run.get("profile_id", "") or "")
            row["benchmark"] = "reasoner_long_dialog"
            reasoner_probe_rows.append(row)

    static_summary = aggregate_token_usage(static_cases, benchmark_name="static")
    reasoner_long_summary = aggregate_token_usage(reasoner_probe_rows, benchmark_name="reasoner_long_dialog")
    long_dialog_summary = {
        "benchmark": "long_dialog",
        "calls": int(sum(int(item.get("probe_count", 0) or 0) for item in list((long_dialog_results or {}).get("runs", []) or []))),
        "llm_prompt_tokens": 0,
        "llm_completion_tokens": 0,
        "llm_total_tokens": 0,
        "judge_prompt_tokens": 0,
        "judge_completion_tokens": 0,
        "judge_total_tokens": 0,
        "combined_prompt_tokens": 0,
        "combined_completion_tokens": 0,
        "combined_total_tokens": 0,
        "avg_llm_total_tokens": 0.0,
        "avg_judge_total_tokens": 0.0,
        "avg_combined_total_tokens": 0.0,
        "comment": "纯记忆长对话 benchmark 不调用回答模型，因此 token 统计固定为 0。",
    }

    static_by_category: Dict[str, list[Dict[str, Any]]] = {}
    for row in static_cases:
        static_by_category.setdefault(str(row.get("category", "") or "unknown"), []).append(row)
    static_category_rows = [
        {
            "category": category,
            **aggregate_token_usage(rows, benchmark_name="static_category"),
        }
        for category, rows in sorted(static_by_category.items())
    ]

    combo_groups: Dict[tuple[str, str, str], list[Dict[str, Any]]] = {}
    for row in static_cases:
        combo_groups.setdefault(("static", str(row.get("reasoner", "")), str(row.get("memory", ""))), []).append(row)
    for row in reasoner_probe_rows:
        combo_groups.setdefault(("reasoner_long_dialog", str(row.get("reasoner", "")), str(row.get("memory", ""))), []).append(row)
    by_combo = []
    for (benchmark_name, reasoner, memory), rows in sorted(combo_groups.items()):
        by_combo.append(
            {
                "benchmark": benchmark_name,
                "reasoner": reasoner,
                "memory": memory,
                **aggregate_token_usage(rows, benchmark_name=benchmark_name),
            }
        )

    overall = aggregate_token_usage([*static_cases, *reasoner_probe_rows], benchmark_name="overall")
    overall["long_dialog_calls_without_llm"] = int(long_dialog_summary["calls"])

    return {
        "overall": overall,
        "by_benchmark": [static_summary, reasoner_long_summary, long_dialog_summary],
        "static_by_category": static_category_rows,
        "by_combo": by_combo,
    }
