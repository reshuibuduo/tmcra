from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .result_labels import annotate_result_payload

_TIMEOUT_MARKERS = (
    "timeout",
    "timed out",
    "read timeout",
    "deadline",
    "broken pipe",
    "connection reset",
    "connection aborted",
    "connection refused",
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _bool_from(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
    return bool(value)


def _response_trace(case_record: Mapping[str, Any]) -> Mapping[str, Any]:
    response = case_record.get("response", {}) or {}
    trace = response.get("trace", {}) or {}
    return trace.get("tmcra_reasoning_v2", {}) or {}


def _judge_trace(case_record: Mapping[str, Any]) -> Mapping[str, Any]:
    trace = _response_trace(case_record)
    return trace.get("judge_trace", {}) or {}


def _avg_metric(records: Sequence[Mapping[str, Any]], key: str, *, predicate: Any | None = None) -> float:
    if predicate is None:
        selected = list(records)
    else:
        selected = [item for item in records if predicate(item)]
    if not selected:
        return 0.0
    return sum(_safe_float(item.get(key, 0.0)) for item in selected) / max(1, len(selected))


def classify_judge_affected_case(
    case_record: Mapping[str, Any],
    *,
    timeout_seconds: float = 1.2,
    likely_timeout_ratio: float = 0.9,
) -> Dict[str, Any]:
    trace = _judge_trace(case_record)
    decision = trace.get("decision", {}) or {}
    triggered = _bool_from(trace.get("triggered", False))
    judge_bypassed = _bool_from(trace.get("judge_bypassed", False))
    decision_valid = _bool_from(decision.get("decision_valid", False))
    fallback_reason = str(trace.get("fallback_reason", "") or "").strip()
    latency_seconds = _safe_float(trace.get("latency_seconds", 0.0))
    error_flag = triggered and fallback_reason.startswith("judge_error:")
    invalid_decision_flag = triggered and not decision_valid
    bypass_flag = triggered and judge_bypassed
    affected = triggered and (bypass_flag or error_flag or invalid_decision_flag)
    lowered_reason = fallback_reason.lower()
    likely_timeout = bool(
        affected
        and (
            any(marker in lowered_reason for marker in _TIMEOUT_MARKERS)
            or latency_seconds >= max(0.0, float(timeout_seconds) * float(likely_timeout_ratio))
        )
    )

    affected_reasons: List[str] = []
    if error_flag:
        affected_reasons.append("judge_error")
    if invalid_decision_flag:
        affected_reasons.append("invalid_decision")
    if bypass_flag and not error_flag:
        affected_reasons.append("judge_bypassed")
    if likely_timeout:
        affected_reasons.append("likely_timeout")
    if not affected_reasons and affected:
        affected_reasons.append("judge_affected")

    tm_trace = _response_trace(case_record)
    slot_resolution = tm_trace.get("slot_resolution", {}) or {}
    selection_before = list((slot_resolution.get("selected_slots_before")) or [])
    selection_after = list((slot_resolution.get("selected_slots_after")) or [])

    return {
        "case_id": str(case_record.get("case_id", "") or ""),
        "reasoner": str(case_record.get("reasoner", "") or ""),
        "memory": str(case_record.get("memory", "") or ""),
        "category": str(case_record.get("category", "") or ""),
        "phase": str(case_record.get("phase", "") or ""),
        "judge_enabled": _bool_from(trace.get("enabled", False)),
        "judge_mode": str(trace.get("mode", "") or ""),
        "judge_provider": str(trace.get("provider", "") or ""),
        "judge_triggered": triggered,
        "judge_bypassed": judge_bypassed,
        "decision_valid": decision_valid,
        "fallback_reason": fallback_reason,
        "latency_seconds": round(latency_seconds, 6),
        "confidence": round(_safe_float(decision.get("confidence", 0.0)), 6),
        "affected": affected,
        "likely_timeout": likely_timeout,
        "affected_reasons": affected_reasons,
        "selection_before": selection_before,
        "selection_after": selection_after,
        "answer_match": round(_safe_float(case_record.get("answer_match", 0.0)), 6),
        "memory_correctness": round(_safe_float(case_record.get("memory_correctness", 0.0)), 6),
        "overwrite_resolution": round(_safe_float(case_record.get("overwrite_resolution", 0.0)), 6),
        "unsupported_claim_rate": round(_safe_float(case_record.get("unsupported_claim_rate", 0.0)), 6),
    }


def _recompute_static_summary(cases: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for item in cases:
        grouped[(str(item.get("reasoner", "") or ""), str(item.get("memory", "") or ""))].append(item)

    summary: List[Dict[str, Any]] = []
    for (reasoner_name, memory_name), combo_records in grouped.items():
        avg = lambda key: _avg_metric(combo_records, key)
        seed_cases = [item for item in combo_records if str(item.get("phase", "")) == "seed"]
        followup_cases = [item for item in combo_records if str(item.get("phase", "")) == "followup"]
        summary_cases = [item for item in combo_records if str(item.get("category", "")) == "summary"]
        history_cases = [item for item in combo_records if str(item.get("category", "")) == "history_query"]
        compare_case_count = sum(1 for item in combo_records if _bool_from(item.get("has_compare_case", False)))
        timeline_case_count = sum(1 for item in combo_records if _bool_from(item.get("has_timeline_case", False)))
        record = {
            "reasoner": reasoner_name,
            "memory": memory_name,
            "cases": len(combo_records),
            "avg_answer_match": round(avg("answer_match"), 6),
            "avg_keyword_match": round(avg("keyword_match"), 6),
            "avg_memory_match": round(avg("memory_match"), 6),
            "avg_memory_correctness": round(avg("memory_correctness"), 6),
            "avg_overwrite_resolution": round(avg("overwrite_resolution"), 6),
            "avg_stale_recall_rate": round(avg("stale_recall_rate"), 6),
            "avg_false_recall_rate": round(avg("false_recall_rate"), 6),
            "avg_fact_match": round(avg("fact_match"), 6),
            "avg_reasoning_quality_score": round(avg("reasoning_quality_score"), 6),
            "avg_latency_seconds": round(avg("latency_seconds"), 6),
            "avg_storage_bytes": round(avg("storage_bytes"), 3),
            "llm_prompt_tokens": sum(_safe_int(item.get("llm_prompt_tokens", 0)) for item in combo_records),
            "llm_completion_tokens": sum(_safe_int(item.get("llm_completion_tokens", 0)) for item in combo_records),
            "llm_total_tokens": sum(_safe_int(item.get("llm_total_tokens", 0)) for item in combo_records),
            "judge_prompt_tokens": sum(_safe_int(item.get("judge_prompt_tokens", 0)) for item in combo_records),
            "judge_completion_tokens": sum(_safe_int(item.get("judge_completion_tokens", 0)) for item in combo_records),
            "judge_total_tokens": sum(_safe_int(item.get("judge_total_tokens", 0)) for item in combo_records),
            "combined_prompt_tokens": sum(_safe_int(item.get("combined_prompt_tokens", 0)) for item in combo_records),
            "combined_completion_tokens": sum(_safe_int(item.get("combined_completion_tokens", 0)) for item in combo_records),
            "combined_total_tokens": sum(_safe_int(item.get("combined_total_tokens", 0)) for item in combo_records),
            "avg_llm_total_tokens": round(avg("llm_total_tokens"), 6),
            "avg_judge_total_tokens": round(avg("judge_total_tokens"), 6),
            "avg_combined_total_tokens": round(avg("combined_total_tokens"), 6),
            "evidence_consistency_rate": round(avg("evidence_consistency"), 6),
            "seed_capture_rate": round(_avg_metric(seed_cases, "memory_correctness"), 6) if seed_cases else 0.0,
            "followup_recall_rate": round(_avg_metric(followup_cases, "memory_correctness"), 6) if followup_cases else 0.0,
            "slot_head_accuracy": round(avg("slot_head_accuracy"), 6),
            "history_query_accuracy": round(avg("history_query_accuracy"), 6),
            "history_query_accuracy_subset": round(_avg_metric(history_cases, "history_query_accuracy"), 6) if history_cases else None,
            "judge_trigger_rate": round(avg("judge_trigger_rate"), 6),
            "judge_applied_rate": round(avg("judge_applied_rate"), 6),
            "judge_decision_valid_rate": round(avg("judge_decision_valid_rate"), 6),
            "judge_effective_apply_rate": round(avg("judge_effective_apply_rate"), 6),
            "summary_slot_coverage": round(_avg_metric(combo_records, "summary_slot_coverage", predicate=lambda item: str(item.get("category", "")) == "summary"), 6),
            "summary_realized_coverage": round(_avg_metric(combo_records, "summary_realized_coverage", predicate=lambda item: str(item.get("category", "")) == "summary"), 6),
            "history_slot_coverage": round(_avg_metric(combo_records, "history_slot_coverage", predicate=lambda item: str(item.get("category", "")) == "history_query"), 6),
            "coverage_drop_rate": round(avg("coverage_drop_rate"), 6),
            "judge_under_selection_rate": round(avg("judge_under_selection_rate"), 6),
            "judge_trace_not_realized_rate": round(avg("judge_trace_not_realized_rate"), 6),
            "judge_semantic_not_realized_rate": round(avg("judge_semantic_not_realized_rate"), 6),
            "judge_selected_but_not_realized_rate": round(avg("judge_selected_but_not_realized_rate"), 6),
            "overwrite_trace_mismatch_rate": round(avg("overwrite_trace_mismatch_rate"), 6),
            "summary_subset_score": round(_avg_metric(summary_cases, "reasoning_quality_score"), 6) if summary_cases else None,
            "history_subset_score": round(_avg_metric(history_cases, "reasoning_quality_score"), 6) if history_cases else None,
            "compare_case_count": compare_case_count,
            "timeline_case_count": timeline_case_count,
            "compare_realization_accuracy": round(_avg_metric(combo_records, "compare_realization_accuracy", predicate=lambda item: _bool_from(item.get("has_compare_case", False))), 6) if compare_case_count else None,
            "timeline_realization_accuracy": round(_avg_metric(combo_records, "timeline_realization_accuracy", predicate=lambda item: _bool_from(item.get("has_timeline_case", False))), 6) if timeline_case_count else None,
            "path_rerank_gain": round(_avg_metric(combo_records, "path_rerank_gain", predicate=lambda item: _bool_from(item.get("has_path_case", False))), 6),
            "path_composition_accuracy": round(avg("path_composition_accuracy"), 6),
            "path_protocol_accuracy": round(avg("path_protocol_accuracy"), 6),
            "path_semantic_realization_accuracy": round(_avg_metric(combo_records, "path_semantic_realization_accuracy", predicate=lambda item: _bool_from(item.get("has_path_case", False))), 6),
            "path_consistency_score": round(_avg_metric(combo_records, "path_consistency_score", predicate=lambda item: "path" in str(item.get("category", ""))), 6),
            "critical_node_hit_rate": round(_avg_metric(combo_records, "critical_node_hit_rate", predicate=lambda item: _bool_from(item.get("has_critical_node_case", False))), 6),
            "multi_path_coverage": round(_avg_metric(combo_records, "multi_path_coverage", predicate=lambda item: _bool_from(item.get("has_multi_path_case", False))), 6),
            "science_reasoning_score": round(_avg_metric(combo_records, "reasoning_quality_score", predicate=lambda item: _bool_from(item.get("has_science_reasoning_case", False))), 6),
            "math_reasoning_score": round(_avg_metric(combo_records, "reasoning_quality_score", predicate=lambda item: _bool_from(item.get("has_math_reasoning_case", False))), 6),
            "emotion_reasoning_score": round(_avg_metric(combo_records, "reasoning_quality_score", predicate=lambda item: _bool_from(item.get("has_emotion_reasoning_case", False))), 6),
            "mixed_domain_reasoning_score": round(_avg_metric(combo_records, "reasoning_quality_score", predicate=lambda item: _bool_from(item.get("has_mixed_domain_reasoning_case", False))), 6),
            "science_math_emotion_combo_score": round(_avg_metric(combo_records, "reasoning_quality_score", predicate=lambda item: _bool_from(item.get("has_full_domain_combo_case", False))), 6),
            "contradiction_resolution": round(avg("contradiction_resolution"), 6),
            "entity_isolation_accuracy": round(_avg_metric(combo_records, "entity_isolation_accuracy", predicate=lambda item: _bool_from(item.get("has_entity_isolation_case", False))), 6),
            "temporal_consistency_score": round(_avg_metric(combo_records, "temporal_consistency_score", predicate=lambda item: _bool_from(item.get("has_temporal_case", False))), 6),
            "retrieval_precision": round(_avg_metric(combo_records, "retrieval_precision"), 6),
            "retrieval_recall": round(_avg_metric(combo_records, "retrieval_recall"), 6),
            "avg_verbalization_gap": round(avg("verbalization_gap"), 6),
            "unsupported_claim_rate": round(avg("unsupported_claim_rate"), 6),
            "Memory Correctness": round(avg("memory_correctness"), 6),
            "Overwrite Accuracy": round(avg("overwrite_resolution"), 6),
            "Conflict Resolution Accuracy": round(avg("contradiction_resolution"), 6),
            "False Recall Rate": round(avg("false_recall_rate"), 6),
            "Stale Recall Rate": round(avg("stale_recall_rate"), 6),
            "Entity Isolation Accuracy": round(_avg_metric(combo_records, "entity_isolation_accuracy", predicate=lambda item: _bool_from(item.get("has_entity_isolation_case", False))), 6),
            "Temporal Consistency Score": round(_avg_metric(combo_records, "temporal_consistency_score", predicate=lambda item: _bool_from(item.get("has_temporal_case", False))), 6),
            "Retrieval Precision": round(_avg_metric(combo_records, "retrieval_precision"), 6),
            "Retrieval Recall": round(_avg_metric(combo_records, "retrieval_recall"), 6),
            "Path Completeness Score": round(avg("path_composition_accuracy"), 6),
            "Path Consistency Score": round(_avg_metric(combo_records, "path_consistency_score", predicate=lambda item: "path" in str(item.get("category", ""))), 6),
            "Multi-path Coverage": round(_avg_metric(combo_records, "multi_path_coverage", predicate=lambda item: _bool_from(item.get("has_multi_path_case", False))), 6),
            "Critical Node Hit Rate": round(_avg_metric(combo_records, "critical_node_hit_rate", predicate=lambda item: _bool_from(item.get("has_critical_node_case", False))), 6),
            "Evidence Consistency": round(avg("evidence_consistency"), 6),
            "Unsupported Claim Rate": round(avg("unsupported_claim_rate"), 6),
            "Verbalization Gap": round(avg("verbalization_gap"), 6),
            "Latency (ms)": round(avg("latency_seconds") * 1000.0, 6),
        }
        summary.append(annotate_result_payload(record))
    summary.sort(key=lambda item: (-_safe_float(item.get("avg_reasoning_quality_score", 0.0)), -_safe_float(item.get("slot_head_accuracy", 0.0)), _safe_float(item.get("avg_latency_seconds", 0.0))))
    return summary


def _recompute_subsets(cases: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for item in cases:
        grouped[(str(item.get("reasoner", "") or ""), str(item.get("memory", "") or ""))].append(item)
    results: List[Dict[str, Any]] = []
    for (reasoner_name, memory_name), combo_records in grouped.items():
        for subset_name, predicate in (
            ("summary", lambda item: str(item.get("category", "")) == "summary"),
            ("history", lambda item: str(item.get("category", "")) == "history_query"),
            ("path", lambda item: "path" in str(item.get("category", ""))),
        ):
            subset_records = [item for item in combo_records if predicate(item)]
            if not subset_records:
                continue
            results.append(
                annotate_result_payload(
                    {
                        "reasoner": reasoner_name,
                        "memory": memory_name,
                        "subset": subset_name,
                        "cases": len(subset_records),
                        "avg_answer_match": round(_avg_metric(subset_records, "answer_match"), 6),
                        "avg_memory_correctness": round(_avg_metric(subset_records, "memory_correctness"), 6),
                        "avg_overwrite_resolution": round(_avg_metric(subset_records, "overwrite_resolution"), 6),
                        "avg_reasoning_quality_score": round(_avg_metric(subset_records, "reasoning_quality_score"), 6),
                        "avg_slot_coverage": round(_avg_metric(subset_records, "summary_slot_coverage" if subset_name == "summary" else ("history_slot_coverage" if subset_name == "history" else "path_composition_accuracy")), 6),
                        "avg_realized_coverage": round(_avg_metric(subset_records, "summary_realized_coverage" if subset_name == "summary" else ("history_slot_coverage" if subset_name == "history" else "path_semantic_realization_accuracy")), 6),
                        "avg_coverage_drop_rate": round(_avg_metric(subset_records, "coverage_drop_rate"), 6),
                    }
                )
            )
    return results


def build_static_only_leaderboard(summary_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    ordered = sorted(
        [dict(item) for item in summary_rows],
        key=lambda item: (
            -_safe_float(item.get("avg_reasoning_quality_score", 0.0)),
            -_safe_float(item.get("avg_answer_match", 0.0)),
            -_safe_float(item.get("avg_memory_correctness", 0.0)),
            _safe_float(item.get("avg_latency_seconds", 0.0)),
        ),
    )
    ranked: List[Dict[str, Any]] = []
    for index, item in enumerate(ordered, start=1):
        row = annotate_result_payload(dict(item))
        row["rank"] = index
        row["rank_scope"] = "local static cleaned"
        row["sort_basis"] = "avg_reasoning_quality_score>avg_answer_match>avg_memory_correctness>avg_latency_seconds"
        ranked.append(row)
    return {
        "rank_scope": "local static cleaned",
        "summary": ranked,
    }


def build_judge_affected_report(
    static_results: Mapping[str, Any],
    *,
    timeout_seconds: float = 1.2,
    likely_timeout_ratio: float = 0.9,
) -> Dict[str, Any]:
    cases = list(static_results.get("cases", []) or [])
    affected_rows = [
        classify_judge_affected_case(
            case,
            timeout_seconds=timeout_seconds,
            likely_timeout_ratio=likely_timeout_ratio,
        )
        for case in cases
    ]
    excluded_rows = [item for item in affected_rows if item["affected"]]
    excluded_ids = {item["case_id"] for item in excluded_rows if item.get("case_id")}
    cleaned_cases = [copy.deepcopy(case) for case in cases if str(case.get("case_id", "") or "") not in excluded_ids]
    cleaned_failures = [
        copy.deepcopy(item)
        for item in (static_results.get("failures", []) or [])
        if str(item.get("case_id", "") or "") not in excluded_ids
    ]
    cleaned_payload = {
        **{key: copy.deepcopy(value) for key, value in dict(static_results).items() if key not in {"cases", "summary", "subsets", "failures"}},
        "summary": _recompute_static_summary(cleaned_cases),
        "subsets": _recompute_subsets(cleaned_cases),
        "cases": cleaned_cases,
        "failures": cleaned_failures,
        "judge_affected_filter": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "timeout_seconds": float(timeout_seconds),
            "likely_timeout_ratio": float(likely_timeout_ratio),
            "excluded_case_count": len(excluded_rows),
            "remaining_case_count": len(cleaned_cases),
            "excluded_case_ids": sorted(excluded_ids),
        },
    }

    combo_counter: Counter[tuple[str, str]] = Counter()
    category_counter: Counter[str] = Counter()
    reason_counter: Counter[str] = Counter()
    provider_counter: Counter[str] = Counter()
    for row in excluded_rows:
        combo_counter[(str(row.get("reasoner", "")), str(row.get("memory", "")))] += 1
        category_counter[str(row.get("category", "") or "unknown")] += 1
        provider_counter[str(row.get("judge_provider", "") or "unknown")] += 1
        for reason in list(row.get("affected_reasons", []) or []):
            reason_counter[str(reason or "unknown")] += 1

    cleaned_leaderboard = build_static_only_leaderboard(cleaned_payload["summary"])
    summary_payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "timeout_seconds": float(timeout_seconds),
        "likely_timeout_ratio": float(likely_timeout_ratio),
        "total_cases": len(cases),
        "affected_case_count": len(excluded_rows),
        "clean_case_count": len(cleaned_cases),
        "affected_rate": round(len(excluded_rows) / max(1, len(cases)), 6),
        "likely_timeout_case_count": sum(1 for item in excluded_rows if item.get("likely_timeout")),
        "original_failure_count": len(list(static_results.get("failures", []) or [])),
        "cleaned_failure_count": len(cleaned_failures),
        "affected_by_reason": [{"reason": key, "cases": value} for key, value in reason_counter.most_common()],
        "affected_by_category": [{"category": key, "cases": value} for key, value in category_counter.most_common()],
        "affected_by_provider": [{"judge_provider": key, "cases": value} for key, value in provider_counter.most_common()],
        "affected_by_combo": [
            {"reasoner": reasoner, "memory": memory, "cases": count}
            for (reasoner, memory), count in combo_counter.most_common()
        ],
        "cleaned_static_leaderboard_top": cleaned_leaderboard.get("summary", [])[:10],
    }
    return {
        "affected_cases": excluded_rows,
        "cleaned_static_results": cleaned_payload,
        "cleaned_leaderboard": cleaned_leaderboard,
        "summary": summary_payload,
    }


def write_judge_affected_report(
    *,
    static_results_path: str | Path,
    output_dir: str | Path | None = None,
    timeout_seconds: float = 1.2,
    likely_timeout_ratio: float = 0.9,
) -> Dict[str, Path]:
    input_path = Path(static_results_path)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    report = build_judge_affected_report(
        payload,
        timeout_seconds=timeout_seconds,
        likely_timeout_ratio=likely_timeout_ratio,
    )
    out_dir = Path(output_dir) if output_dir else input_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    affected_json = out_dir / "judge_affected_cases.json"
    affected_json.write_text(json.dumps(report["affected_cases"], ensure_ascii=False, indent=2), encoding="utf-8")

    affected_jsonl = out_dir / "judge_affected_cases.jsonl"
    with affected_jsonl.open("w", encoding="utf-8") as handle:
        for item in report["affected_cases"]:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    cleaned_static = out_dir / "judge_cleaned_static_ab_results.json"
    cleaned_static.write_text(json.dumps(report["cleaned_static_results"], ensure_ascii=False, indent=2), encoding="utf-8")

    cleaned_leaderboard = out_dir / "judge_cleaned_static_leaderboard.json"
    cleaned_leaderboard.write_text(json.dumps(report["cleaned_leaderboard"], ensure_ascii=False, indent=2), encoding="utf-8")

    summary_json = out_dir / "judge_cleaned_summary.json"
    summary_json.write_text(json.dumps(report["summary"], ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "judge_affected_cases_json": affected_json,
        "judge_affected_cases_jsonl": affected_jsonl,
        "judge_cleaned_static_ab_results_json": cleaned_static,
        "judge_cleaned_static_leaderboard_json": cleaned_leaderboard,
        "judge_cleaned_summary_json": summary_json,
    }
