from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import time
import tracemalloc
from typing import Any, Callable, Dict, Iterable, List, Sequence

from .adapters import (
    EvalCase,
    FailureRecord,
    LLMProfile,
    LeaderboardRecord,
    LongDialogProfile,
    LongDialogProbe,
    MemoryAdapter,
    ReasoningAdapter,
    ScenarioProfile,
)


MemoryFactory = Callable[[], MemoryAdapter]
ReasonerFactory = Callable[[], ReasoningAdapter]


@dataclass(slots=True)
class BenchmarkConfig:
    answer_mode: str = "transparent"
    top_k: int = 6
    dialog_lengths: Sequence[int] = (3000, 5000)
    dialog_profiles: Sequence[str] = field(default_factory=lambda: tuple(item.profile_id for item in default_dialog_profiles()))
    scenario_profiles: Sequence[str] = field(default_factory=lambda: tuple(item.profile_id for item in default_scenario_profiles()))
    llm_profiles: Sequence[str] = field(default_factory=lambda: ("qwen7b", "deepseek7b"))
    score_weights: Dict[str, float] = field(default_factory=lambda: {"reasoning_quality": 1.0, "memory_quality": 1.0, "efficiency": 1.0})
    output_dir: Path | None = None
    static_case_limit: int = 0
    include_long_dialog: bool = True
    include_static: bool = True
    remote_run_id: str = ""


def default_scenario_profiles() -> List[ScenarioProfile]:
    return [
        ScenarioProfile("transparent_foundation", "Transparent foundation"),
        ScenarioProfile("terminology_continuity", "Terminology continuity"),
        ScenarioProfile("constraint_preference_overwrite", "Constraint and preference overwrite"),
        ScenarioProfile("delayed_multi_constraint", "Delayed multi-constraint recall"),
        ScenarioProfile("multi_hop_path_consistency", "Multi-hop path consistency"),
    ]


def default_dialog_profiles() -> List[LongDialogProfile]:
    return [
        LongDialogProfile("goal_persistence", "Goal persistence under noise"),
        LongDialogProfile("constraint_overwrite", "Constraint overwrite and stale memory"),
        LongDialogProfile("preference_shift", "Preference shift with long context"),
        LongDialogProfile("terminology_redefinition", "Terminology redefinition"),
        LongDialogProfile("stage_multi_constraint", "Stage transition with multiple active constraints"),
    ]


def default_llm_profiles() -> Dict[str, LLMProfile]:
    return {
        "qwen7b": LLMProfile(
            name="qwen7b",
            model=os.getenv("TMCRA_REPLACEMENT_QWEN_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
            base_url=os.getenv("TMCRA_REPLACEMENT_QWEN_BASE_URL", "http://127.0.0.1:18000/v1"),
            api_key=os.getenv("TMCRA_REPLACEMENT_QWEN_API_KEY", "EMPTY"),
            system_prompt="Use only the supplied evidence. If evidence is missing, say so.",
            timeout_seconds=float(os.getenv("TMCRA_REPLACEMENT_QWEN_TIMEOUT", "120")),
            temperature=0.1,
            max_tokens=256,
        ),
        "deepseek7b": LLMProfile(
            name="deepseek7b",
            model=os.getenv("TMCRA_REPLACEMENT_DEEPSEEK_MODEL", "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"),
            base_url=os.getenv("TMCRA_REPLACEMENT_DEEPSEEK_BASE_URL", "http://127.0.0.1:18001/v1"),
            api_key=os.getenv("TMCRA_REPLACEMENT_DEEPSEEK_API_KEY", "EMPTY"),
            system_prompt="Act as a constrained critic. Use only the supplied evidence and memory hits.",
            timeout_seconds=float(os.getenv("TMCRA_REPLACEMENT_DEEPSEEK_TIMEOUT", "120")),
            temperature=0.1,
            max_tokens=256,
        ),
        "gemma4e4b": LLMProfile(
            name="gemma4e4b",
            model=os.getenv("TMCRA_REPLACEMENT_GEMMA_MODEL", "gemma-4-e4b-it"),
            base_url=os.getenv("TMCRA_REPLACEMENT_GEMMA_BASE_URL", "http://127.0.0.1:18002/v1"),
            api_key=os.getenv("TMCRA_REPLACEMENT_GEMMA_API_KEY", "EMPTY"),
            system_prompt="You are an evidence-constrained assistant. Answer only from the supplied memory hits, facts, and paths. If support is missing, say so.",
            timeout_seconds=float(os.getenv("TMCRA_REPLACEMENT_GEMMA_TIMEOUT", "120")),
            temperature=0.1,
            max_tokens=256,
        ),
    }


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _match_ratio(text: str, keywords: Iterable[str]) -> float:
    items = [item for item in (_normalize_text(keyword) for keyword in keywords) if item]
    if not items:
        return 1.0
    haystack = _normalize_text(text)
    matched = sum(1 for item in items if item in haystack)
    return matched / max(1, len(items))


def _match_count(text: str, keywords: Iterable[str]) -> int:
    items = [item for item in (_normalize_text(keyword) for keyword in keywords) if item]
    if not items:
        return 0
    haystack = _normalize_text(text)
    return sum(1 for item in items if item in haystack)


def _present_ratio(text: str, values: Iterable[str]) -> float:
    items = [item for item in (_normalize_text(value) for value in values) if item]
    if not items:
        return 1.0
    haystack = _normalize_text(text)
    return sum(1 for item in items if item in haystack) / max(1, len(items))


def _violation_ratio(text: str, values: Iterable[str]) -> float:
    items = [item for item in (_normalize_text(value) for value in values) if item]
    if not items:
        return 0.0
    haystack = _normalize_text(text)
    return sum(1 for item in items if item in haystack) / max(1, len(items))


def _memory_hit_text(memory_hits: List[Dict[str, Any]]) -> str:
    values = [str(hit.get("value", "")) for hit in memory_hits]
    anchors = [str(anchor) for hit in memory_hits for anchor in hit.get("anchors", []) or []]
    return _normalize_text("\n".join(values + anchors))


def _evidence_text(response: Dict[str, Any]) -> str:
    return _normalize_text(json.dumps(response.get("facts", []), ensure_ascii=False) + "\n" + json.dumps(response.get("paths", []), ensure_ascii=False))


def _path_contains_concepts(path: Dict[str, Any], expected_concepts: Sequence[str]) -> bool:
    normalized_path = [_normalize_text(item) for item in path.get("concepts", []) or [] if _normalize_text(item)]
    if not normalized_path:
        return False
    wanted = [_normalize_text(item) for item in expected_concepts if _normalize_text(item)]
    if not wanted:
        return False
    return all(item in normalized_path for item in wanted)


def _path_edge_consistency(response: Dict[str, Any]) -> float:
    paths = list(response.get("paths", []) or [])
    facts = list(response.get("facts", []) or [])
    if not paths:
        return 0.0
    fact_edges = {
        (_normalize_text(item.get("from", "")), _normalize_text(item.get("to", "")))
        for item in facts
        if _normalize_text(item.get("from", "")) and _normalize_text(item.get("to", ""))
    }
    scores: List[float] = []
    for path in paths[:3]:
        concepts = [_normalize_text(item) for item in path.get("concepts", []) or [] if _normalize_text(item)]
        if len(concepts) < 2:
            continue
        edges = list(zip(concepts[:-1], concepts[1:]))
        matched = sum(1 for edge in edges if edge in fact_edges)
        scores.append(matched / max(1, len(edges)))
    return sum(scores) / max(1, len(scores)) if scores else 0.0


def _avg_metric(records: Sequence[Dict[str, Any]], key: str, *, predicate: Callable[[Dict[str, Any]], bool] | None = None) -> float:
    items = [record for record in records if predicate(record)] if predicate else list(records)
    if not items:
        return 0.0
    return sum(float(item.get(key, 0.0) or 0.0) for item in items) / max(1, len(items))


def _case_matches_filters(case: EvalCase, config: "BenchmarkConfig") -> bool:
    case_id = str(case.case_id or "")
    id_filters = {str(item or "").strip() for item in list(getattr(config, "case_id_filters", ()) or ()) if str(item or "").strip()}
    if id_filters and case_id not in id_filters:
        return False
    prefix_filters = [str(item or "").strip() for item in list(getattr(config, "case_prefix_filters", ()) or ()) if str(item or "").strip()]
    if prefix_filters and not any(case_id.startswith(prefix) for prefix in prefix_filters):
        return False
    return True


def _emit_progress(
    config: "BenchmarkConfig",
    *,
    benchmark: str,
    completed: int,
    total: int,
    status: str = "running",
    **payload: Any,
) -> None:
    callback = getattr(config, "progress_callback", None)
    if callback is None:
        return
    total_value = max(0, int(total or 0))
    completed_value = max(0, int(completed or 0))
    percent = 1.0 if total_value <= 0 and status == "completed" else (min(1.0, completed_value / max(1, total_value)) if total_value > 0 else 0.0)
    event = {
        "kind": "benchmark_progress",
        "benchmark": str(getattr(config, "progress_label", "") or benchmark),
        "status": str(status or "running"),
        "completed": completed_value,
        "total": total_value,
        "percent": round(percent, 6),
        "remote_run_id": str(getattr(config, "remote_run_id", "") or ""),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    event.update(payload)
    try:
        callback(event)
    except Exception:
        return


def _metric_at_least(value: Any, threshold: float = 0.999) -> bool:
    try:
        return float(value or 0.0) + 1e-9 >= threshold
    except Exception:
        return False


def _dedupe_texts(values: Iterable[Any]) -> List[str]:
    results: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = _normalize_text(text)
        if key in seen:
            continue
        seen.add(key)
        results.append(text)
    return results


def _path_hint_lists(reasoning_trace: Dict[str, Any]) -> tuple[List[str], List[str]]:
    preview = dict(reasoning_trace.get("path_preview_summary", {}) or {})
    required_nodes: List[str] = []
    blocked_nodes: List[str] = []
    for candidate in list(preview.get("candidates", []) or [])[:4]:
        if not isinstance(candidate, dict):
            continue
        required_nodes.extend(candidate.get("required_nodes", []) or [])
        blocked_nodes.extend(candidate.get("blocked_nodes", []) or [])
    return _dedupe_texts(required_nodes), _dedupe_texts(blocked_nodes)


def _path_semantic_realization_accuracy(case: EvalCase, response: Dict[str, Any], reasoning_trace: Dict[str, Any]) -> float:
    if "path" not in str(case.category or ""):
        return 0.0
    paths = list(response.get("paths", []) or [])
    candidate_scores = list(response.get("candidate_scores", []) or [])
    answer_text = _normalize_text(response.get("answer", ""))
    evidence_text = _evidence_text(response)
    path_realization = dict(reasoning_trace.get("path_realization", {}) or {})
    required_nodes, blocked_nodes = _path_hint_lists(reasoning_trace)
    critical_nodes = list(case.metadata.get("critical_nodes", []) or [])
    alternative_path_sets = list(case.metadata.get("alternative_path_sets", []) or [])
    normalized_query = _normalize_text(case.query)
    components: List[float] = []
    needs_gap = bool(
        "missingreason" in _normalize_text(case.case_id)
        or "what is missing" in normalized_query
        or ("missing" in normalized_query and "path" in normalized_query)
        or "complete path" in normalized_query
    )
    if needs_gap:
        gap_realized = bool(path_realization.get("missing_bridge_refs")) or any(
            "missing_bridge" in {_normalize_text(item) for item in path.get("relations", []) or []}
            for path in paths
        ) or ("missing" in answer_text and "bridge" in answer_text)
        components.append(1.0 if gap_realized else 0.0)
    else:
        components.append(1.0 if paths else 0.0)
    if case.expected_path_concepts:
        components.append(1.0 if any(_path_contains_concepts(path, case.expected_path_concepts) for path in paths) else 0.0)
    if required_nodes:
        components.append(
            1.0
            if any(
                all(_normalize_text(required) in [_normalize_text(concept) for concept in path.get("concepts", []) or []] for required in required_nodes)
                for path in paths
            )
            else 0.0
        )
    if blocked_nodes:
        first_path = list(paths[:1] or [{}])[0]
        components.append(
            1.0
            if all(_normalize_text(blocked) not in [_normalize_text(concept) for concept in first_path.get("concepts", []) or []] for blocked in blocked_nodes)
            else 0.0
        )
    if alternative_path_sets or "multipath" in normalized_query or "multibranch" in normalized_query or "multiple path" in normalized_query:
        components.append(
            1.0
            if len(paths) >= 2 or len(candidate_scores) >= 2 or len(list(path_realization.get("alternate_path_refs", []) or [])) >= 1
            else 0.0
        )
    if critical_nodes:
        components.append(_match_ratio(evidence_text, critical_nodes))
    return sum(components) / max(1, len(components)) if components else 0.0


def _to_case(payload: Dict[str, Any]) -> EvalCase:
    return EvalCase(
        case_id=str(payload.get("case_id", "")),
        query=str(payload.get("query", "")),
        answer_mode=str(payload.get("answer_mode", "transparent")),
        category=str(payload.get("category", "general")),
        expected_keywords=[str(item) for item in payload.get("expected_keywords", []) or []],
        expected_answer_keywords=[str(item) for item in payload.get("expected_answer_keywords", []) or []],
        expected_memory_values=[str(item) for item in payload.get("expected_memory_values", []) or []],
        expected_absent_values=[str(item) for item in payload.get("expected_absent_values", []) or []],
        expected_fact_phrases=[str(item) for item in payload.get("expected_fact_phrases", []) or []],
        expected_path_concepts=[str(item) for item in payload.get("expected_path_concepts", []) or []],
        metadata=dict(payload.get("metadata", {}) or {}),
    )


def default_eval_cases() -> List[EvalCase]:
    return [
        _to_case(
            {
                "case_id": "fallback_goal_seed",
                "query": "Goal update: build TMCRA as a transparent reasoning engine alpha.",
                "expected_answer_keywords": ["transparent reasoning engine alpha"],
                "expected_memory_values": ["build TMCRA as a transparent reasoning engine alpha"],
                "metadata": {
                    "session_group": "fallback",
                    "replacement_memory_records": [
                        {
                            "category": "goal",
                            "slot": "goal.primary",
                            "value": "build TMCRA as a transparent reasoning engine alpha",
                            "anchors": ["TMCRA", "transparent reasoning engine"],
                            "relation": "session_goal",
                        }
                    ],
                },
            }
        ),
        _to_case(
            {
                "case_id": "fallback_goal_followup",
                "query": "What is the current primary goal?",
                "expected_answer_keywords": ["transparent reasoning engine alpha"],
                "expected_memory_values": ["build TMCRA as a transparent reasoning engine alpha"],
                "metadata": {"session_group": "fallback"},
            }
        ),
    ]


def load_eval_cases(path: str | Path | None = None) -> List[EvalCase]:
    if path is None:
        return default_eval_cases()
    case_path = Path(path)
    if not case_path.exists():
        return default_eval_cases()
    cases: List[EvalCase] = []
    with case_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            cases.append(_to_case(json.loads(line)))
    return cases or default_eval_cases()


async def run_static_ab_benchmark(
    *,
    cases: Sequence[EvalCase],
    reasoner_factories: Dict[str, ReasonerFactory],
    memory_factories: Dict[str, MemoryFactory],
    config: BenchmarkConfig,
) -> Dict[str, Any]:
    selected_cases = list(cases[: config.static_case_limit]) if config.static_case_limit > 0 else list(cases)
    failures: List[FailureRecord] = []
    results: Dict[str, Any] = {"cases": [], "summary": [], "failures": []}
    for reasoner_name, reasoner_factory in reasoner_factories.items():
        for memory_name, memory_factory in memory_factories.items():
            reasoner = reasoner_factory()
            memory = memory_factory()
            combo_records: List[Dict[str, Any]] = []
            current_group = None
            for case in selected_cases:
                session_group = case.metadata.get("session_group")
                if current_group is None or current_group != session_group:
                    memory.reset()
                    current_group = session_group
                response = await reasoner.answer(case.query, answer_mode=case.answer_mode or config.answer_mode, memory_adapter=memory)
                answer_payload = response.to_dict()
                if case.metadata.get("replacement_memory_records"):
                    answer_payload = {**answer_payload, "replacement_memory_records": case.metadata["replacement_memory_records"]}
                memory.ingest_turn(case.query, response.answer, answer_payload=answer_payload, extraction_result=dict(response.metadata.get("extraction") or {}))
                haystack = _normalize_text(response.answer) + "\n" + _memory_hit_text(response.memory_hits)
                record = {
                    "reasoner": reasoner_name,
                    "memory": memory_name,
                    "case_id": case.case_id,
                    "answer": response.answer,
                    "latency_seconds": response.latency_seconds,
                    "answer_match": _match_ratio(response.answer, case.expected_answer_keywords or case.expected_keywords),
                    "keyword_match": _match_ratio(response.answer, case.expected_keywords or case.expected_answer_keywords),
                    "memory_match": _present_ratio(_memory_hit_text(response.memory_hits), case.expected_memory_values),
                    "memory_correctness": _present_ratio(haystack, case.expected_memory_values),
                    "overwrite_resolution": max(0.0, 1.0 - _violation_ratio(haystack, case.expected_absent_values)),
                    "stale_recall_rate": _violation_ratio(haystack, case.expected_absent_values),
                    "false_recall_rate": _violation_ratio(haystack, case.metadata.get("false_values", []) or []),
                    "fact_match": _match_ratio(_evidence_text(response.to_dict()), [*case.expected_fact_phrases, *case.expected_path_concepts]),
                    "evidence_consistency": 1.0 if response.evidence_consistent and not response.unsupported_claims else 0.0,
                    "storage_bytes": memory.storage_bytes(),
                    "response": response.to_dict(),
                }
                record["reasoning_quality_score"] = round((float(record["answer_match"]) + float(record["evidence_consistency"])) / 2.0, 6)
                combo_records.append(record)
                if record["answer_match"] < 0.999 or record["evidence_consistency"] < 0.999:
                    failures.append(FailureRecord(benchmark="static", reasoner=reasoner_name, memory=memory_name, case_id=case.case_id, reason="case_below_target", details={key: record[key] for key in ("answer_match", "memory_correctness", "overwrite_resolution", "evidence_consistency")}))
            avg = lambda key: sum(float(item.get(key, 0.0) or 0.0) for item in combo_records) / max(1, len(combo_records))
            results["summary"].append({
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
                "evidence_consistency_rate": round(avg("evidence_consistency"), 6),
            })
            results["cases"].extend(combo_records)
    results["summary"].sort(key=lambda item: (-float(item["avg_reasoning_quality_score"]), -float(item["avg_memory_correctness"]), float(item["avg_latency_seconds"])))
    results["failures"] = [item.to_dict() for item in failures]
    return results


def _noise_turn(index: int, profile_id: str, distractors: Sequence[str]) -> str:
    distractor = distractors[index % len(distractors)] if distractors else f"{profile_id}_noise_{index}"
    return f"Noise turn {index}: routine chatter for {profile_id}; distractor token {distractor} should not become active memory."


def _event_turn(statement: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"type": "memory", "statement": statement, "replacement_memory_records": records}


def _profile_events(profile_id: str, length: int) -> Dict[str, Any]:
    suffix = f"{profile_id}-{length}"
    if profile_id == "goal_persistence":
        goal = f"build TMCRA transparent reasoning engine {suffix}"
        return {
            "events": [
                _event_turn(
                    f"Goal update: {goal}.",
                    [{"category": "goal", "slot": "goal.primary", "value": goal, "anchors": ["TMCRA", "transparent reasoning"], "relation": "session_goal"}],
                )
            ],
            "probes": [LongDialogProbe("goal_current", "goal.primary", "What is the current primary goal?", [goal], [], [f"abandoned-goal-{suffix}"])],
            "distractors": [f"abandoned-goal-{suffix}", f"ghost-goal-{length}"],
        }
    if profile_id == "constraint_overwrite":
        old_value = f"never let an external llm give the final verdict {suffix}"
        new_value = f"teacher llms may assist supervision, but TMCRA evidence owns the final verdict {suffix}"
        return {
            "events": [
                _event_turn(
                    f"Constraint seed: {old_value}.",
                    [{"category": "constraint", "slot": "constraint.teacher_policy", "value": old_value, "anchors": ["external llm"], "relation": "constrained_by"}],
                ),
                _event_turn(
                    f"Constraint overwrite: {new_value}.",
                    [{"category": "constraint", "slot": "constraint.teacher_policy", "value": new_value, "anchors": ["teacher supervision", "TMCRA evidence"], "relation": "constrained_by"}],
                ),
            ],
            "probes": [LongDialogProbe("constraint_current", "constraint.teacher_policy", "What is the current teacher policy?", [new_value], [old_value], [f"free-llm-verdict-{length}"])],
            "distractors": [f"free-llm-verdict-{length}", f"archived-policy-{length}"],
        }
    if profile_id == "preference_shift":
        old_value = f"transparent only mode {suffix}"
        new_value = f"natural default with transparent expansion {suffix}"
        return {
            "events": [
                _event_turn(
                    f"Preference seed: {old_value}.",
                    [{"category": "preference", "slot": "preference.answer_mode", "value": old_value, "anchors": ["transparent"], "relation": "prefers"}],
                ),
                _event_turn(
                    f"Preference overwrite: {new_value}.",
                    [{"category": "preference", "slot": "preference.answer_mode", "value": new_value, "anchors": ["natural", "transparent expansion"], "relation": "prefers"}],
                ),
            ],
            "probes": [LongDialogProbe("preference_current", "preference.answer_mode", "What is the default answer mode now?", [new_value], [old_value], [f"free-chat-only-{length}"])],
            "distractors": [f"free-chat-only-{length}", f"style-drift-{length}"],
        }
    if profile_id == "terminology_redefinition":
        old_value = f"memory bridge means scratch buffer {suffix}"
        new_value = f"memory bridge means session graph memory {suffix}"
        return {
            "events": [
                _event_turn(
                    f"Term seed: {old_value}.",
                    [{"category": "terminology", "slot": "term.memory_bridge", "value": old_value, "anchors": ["memory bridge", "scratch buffer"], "relation": "uses_term"}],
                ),
                _event_turn(
                    f"Term overwrite: {new_value}.",
                    [{"category": "terminology", "slot": "term.memory_bridge", "value": new_value, "anchors": ["memory bridge", "session graph memory"], "relation": "uses_term"}],
                ),
            ],
            "probes": [LongDialogProbe("term_current", "term.memory_bridge", "What does memory bridge mean now?", [new_value], [old_value], [f"memory bridge means vector cache {length}"])],
            "distractors": [f"memory bridge means vector cache {length}", f"legacy-term-{length}"],
        }
    stage_old = f"design phase {suffix}"
    stage_new = f"deployment validation phase {suffix}"
    constraint_a = f"keep evidence-first ranking active {suffix}"
    constraint_b = f"keep graph memory inside the evidence loop {suffix}"
    return {
        "events": [
            _event_turn(
                f"Stage seed: {stage_old}.",
                [{"category": "stage_state", "slot": "stage.current", "value": stage_old, "anchors": ["design"], "relation": "stage_state"}],
            ),
            _event_turn(
                f"Constraint seed: {constraint_a}.",
                [{"category": "constraint", "slot": "constraint.evidence_first", "value": constraint_a, "anchors": ["evidence-first ranking"], "relation": "constrained_by"}],
            ),
            _event_turn(
                f"Constraint seed: {constraint_b}.",
                [{"category": "constraint", "slot": "constraint.graph_loop", "value": constraint_b, "anchors": ["graph memory"], "relation": "constrained_by"}],
            ),
            _event_turn(
                f"Stage overwrite: {stage_new}.",
                [{"category": "stage_state", "slot": "stage.current", "value": stage_new, "anchors": ["deployment validation"], "relation": "stage_state"}],
            ),
        ],
        "probes": [
            LongDialogProbe("stage_current", "stage.current", "What stage is the system currently in?", [stage_new], [stage_old], [f"free-exploration-{length}"]),
            LongDialogProbe("constraints_current", "constraint.bundle", "Which active constraints still apply right now?", [constraint_a, constraint_b], [], [f"free-exploration-{length}"]),
        ],
        "distractors": [f"free-exploration-{length}", f"hallucinated-bypass-{length}"],
    }


def _build_long_dialogue(profile_id: str, length: int) -> Dict[str, Any]:
    spec = _profile_events(profile_id, length)
    events = list(spec["events"])
    probes: List[LongDialogProbe] = list(spec["probes"])
    distractors = list(spec.get("distractors", []))
    positions = [max(1, int(length * ratio)) for ratio in (0.05, 0.22, 0.55, 0.8)]
    turns: List[Dict[str, Any]] = []
    cursor = 1
    for event_index, event in enumerate(events):
        target = positions[min(event_index, len(positions) - 1)]
        while len(turns) + 1 < target and len(turns) < max(0, length - len(events)):
            turns.append({"type": "noise", "text": _noise_turn(cursor, profile_id, distractors)})
            cursor += 1
        turns.append(event)
        cursor += 1
    while len(turns) < length:
        turns.append({"type": "noise", "text": _noise_turn(cursor, profile_id, distractors)})
        cursor += 1
    return {"profile_id": profile_id, "length": length, "turns": turns[:length], "probes": [probe.to_dict() for probe in probes]}


def _probe_result(retrieval: Dict[str, Any], probe: LongDialogProbe) -> Dict[str, Any]:
    hits_text = _normalize_text("\n".join([str(hit.get("value", "")) for hit in retrieval.get("hits", [])] + [json.dumps(retrieval.get("relations", []), ensure_ascii=False)]))
    expected_ratio = _present_ratio(hits_text, probe.expected_values)
    stale_ratio = _violation_ratio(hits_text, probe.stale_values)
    false_ratio = _violation_ratio(hits_text, probe.false_values)
    return {
        "probe_id": probe.probe_id,
        "slot": probe.slot,
        "prompt": probe.prompt,
        "expected_ratio": round(expected_ratio, 6),
        "stale_ratio": round(stale_ratio, 6),
        "false_ratio": round(false_ratio, 6),
        "overwrite_resolution": round(1.0 if not probe.stale_values else max(0.0, 1.0 - stale_ratio), 6),
        "retrieval": retrieval,
    }


def _efficiency_component(current: float, baseline: float) -> float:
    if baseline <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - (current / baseline)))


def _attach_efficiency_scores(benchmark: Dict[str, Any]) -> None:
    baseline_map = {(str(run["profile_id"]), int(run["dialog_length"])): run for run in benchmark.get("runs", []) if run.get("memory") == "full_history_memory"}
    for run in benchmark.get("runs", []):
        baseline = baseline_map.get((str(run["profile_id"]), int(run["dialog_length"])))
        if not baseline:
            run["efficiency_score"] = 0.0
            continue
        context_score = _efficiency_component(float(run["avg_context_tokens"]), float(baseline["avg_context_tokens"]))
        storage_score = _efficiency_component(float(run["storage_bytes"]), float(baseline["storage_bytes"]))
        retrieval_score = _efficiency_component(float(run["avg_retrieval_seconds"]), float(baseline["avg_retrieval_seconds"]))
        run["efficiency_score"] = round((context_score + storage_score + retrieval_score) / 3.0, 6)


def run_long_dialogue_benchmark(*, memory_factories: Dict[str, MemoryFactory], config: BenchmarkConfig) -> Dict[str, Any]:
    benchmark: Dict[str, Any] = {"runs": [], "summary": [], "failures": []}
    failures: List[FailureRecord] = []
    selected_profiles = set(config.dialog_profiles or [item.profile_id for item in default_dialog_profiles()])
    for memory_name, factory in memory_factories.items():
        for profile in default_dialog_profiles():
            if profile.profile_id not in selected_profiles:
                continue
            for length in config.dialog_lengths:
                scenario = _build_long_dialogue(profile.profile_id, int(length))
                adapter = factory()
                adapter.reset()
                tracemalloc.start()
                ingest_start = time.perf_counter()
                for turn in scenario["turns"]:
                    user_text = turn["statement"] if turn["type"] == "memory" else turn["text"]
                    adapter.ingest_turn(user_text, "recorded", answer_payload={"replacement_memory_records": turn.get("replacement_memory_records", [])})
                ingest_seconds = time.perf_counter() - ingest_start
                current_mem, peak_mem = tracemalloc.get_traced_memory()
                tracemalloc.stop()

                probe_records: List[Dict[str, Any]] = []
                total_expected = total_overwrite = total_stale = total_false = 0.0
                total_retrieval = total_context = 0.0
                for probe_payload in scenario["probes"]:
                    probe = LongDialogProbe(
                        probe_id=str(probe_payload["probe_id"]),
                        slot=str(probe_payload["slot"]),
                        prompt=str(probe_payload["prompt"]),
                        expected_values=list(probe_payload.get("expected_values", []) or []),
                        stale_values=list(probe_payload.get("stale_values", []) or []),
                        false_values=list(probe_payload.get("false_values", []) or []),
                    )
                    retrieval = adapter.retrieve(probe.prompt, top_k=config.top_k)
                    total_retrieval += retrieval.retrieval_seconds
                    total_context += retrieval.context_token_estimate
                    probe_result = _probe_result(retrieval.to_dict(), probe)
                    total_expected += probe_result["expected_ratio"]
                    total_overwrite += probe_result["overwrite_resolution"]
                    total_stale += probe_result["stale_ratio"]
                    total_false += probe_result["false_ratio"]
                    probe_records.append(probe_result)
                    if probe_result["expected_ratio"] < 0.999 or probe_result["stale_ratio"] > 0.0 or probe_result["false_ratio"] > 0.0:
                        failures.append(FailureRecord(benchmark="long_dialog", memory=memory_name, probe_id=probe.probe_id, reason="probe_below_target", details={"profile_id": profile.profile_id, "dialog_length": int(length), "expected_ratio": probe_result["expected_ratio"], "overwrite_resolution": probe_result["overwrite_resolution"], "stale_ratio": probe_result["stale_ratio"], "false_ratio": probe_result["false_ratio"]}))
                probe_count = max(1, len(probe_records))
                benchmark["runs"].append({
                    "memory": memory_name,
                    "profile_id": profile.profile_id,
                    "profile_title": profile.title,
                    "dialog_length": int(length),
                    "ingest_seconds": round(float(ingest_seconds), 6),
                    "avg_retrieval_seconds": round(float(total_retrieval / probe_count), 6),
                    "recall_rate": round(float(total_expected / probe_count), 6),
                    "memory_correctness": round(float(total_expected / probe_count), 6),
                    "overwrite_resolution": round(float(total_overwrite / probe_count), 6),
                    "stale_recall_rate": round(float(total_stale / probe_count), 6),
                    "false_recall_rate": round(float(total_false / probe_count), 6),
                    "storage_bytes": int(adapter.storage_bytes()),
                    "python_allocated_bytes": int(current_mem),
                    "python_peak_bytes": int(peak_mem),
                    "avg_context_tokens": round(float(total_context / probe_count), 3),
                    "per_turn_storage_bytes": round(float(adapter.storage_bytes() / max(1, length)), 3),
                    "stats": adapter.stats(),
                    "probes": probe_records,
                })
    _attach_efficiency_scores(benchmark)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for run in benchmark.get("runs", []):
        grouped.setdefault(str(run["memory"]), []).append(run)
    for memory_name, runs in grouped.items():
        avg = lambda key: sum(float(item.get(key, 0.0) or 0.0) for item in runs) / max(1, len(runs))
        benchmark["summary"].append({
            "memory": memory_name,
            "runs": len(runs),
            "avg_memory_correctness": round(avg("memory_correctness"), 6),
            "avg_overwrite_resolution": round(avg("overwrite_resolution"), 6),
            "avg_stale_recall_rate": round(avg("stale_recall_rate"), 6),
            "avg_false_recall_rate": round(avg("false_recall_rate"), 6),
            "avg_context_tokens": round(avg("avg_context_tokens"), 6),
            "avg_retrieval_seconds": round(avg("avg_retrieval_seconds"), 6),
            "avg_storage_bytes": round(avg("storage_bytes"), 3),
            "avg_python_peak_bytes": round(avg("python_peak_bytes"), 3),
            "memory_quality_score": round((avg("memory_correctness") + avg("overwrite_resolution") + (1.0 - avg("stale_recall_rate")) + (1.0 - avg("false_recall_rate"))) / 4.0, 6),
            "efficiency_score": round(avg("efficiency_score"), 6),
        })
    benchmark["summary"].sort(key=lambda item: (-float(item["memory_quality_score"]), -float(item["efficiency_score"])))
    benchmark["failures"] = [item.to_dict() for item in failures]
    return benchmark


def build_leaderboard(*, static_results: Dict[str, Any] | None, long_dialog_results: Dict[str, Any] | None, config: BenchmarkConfig) -> Dict[str, Any]:
    static_summary = {(item["reasoner"], item["memory"]): item for item in (static_results or {}).get("summary", [])}
    long_summary = {item["memory"]: item for item in (long_dialog_results or {}).get("summary", [])}
    weights = dict(config.score_weights or {})
    rw, mw, ew = float(weights.get("reasoning_quality", 1.0)), float(weights.get("memory_quality", 1.0)), float(weights.get("efficiency", 1.0))
    denominator = max(0.0001, rw + mw + ew)
    records: List[LeaderboardRecord] = []
    for (reasoner, memory), static_item in static_summary.items():
        long_item = long_summary.get(memory, {})
        reasoning_quality = float(static_item.get("avg_reasoning_quality_score", 0.0))
        memory_quality = float(long_item.get("memory_quality_score", 0.0))
        efficiency = float(long_item.get("efficiency_score", 0.0))
        total_score = ((reasoning_quality * rw) + (memory_quality * mw) + (efficiency * ew)) / denominator
        records.append(
            LeaderboardRecord(
                reasoner=reasoner,
                memory=memory,
                reasoning_quality_score=reasoning_quality,
                memory_quality_score=memory_quality,
                efficiency_score=efficiency,
                total_score=total_score,
                metadata={
                    "answer_match": static_item.get("avg_answer_match", 0.0),
                    "evidence_consistency_rate": static_item.get("evidence_consistency_rate", 0.0),
                    "memory_correctness": long_item.get("avg_memory_correctness", 0.0),
                    "overwrite_resolution": long_item.get("avg_overwrite_resolution", 0.0),
                    "stale_recall_rate": long_item.get("avg_stale_recall_rate", 0.0),
                    "false_recall_rate": long_item.get("avg_false_recall_rate", 0.0),
                },
            )
        )
    records.sort(key=lambda item: item.total_score, reverse=True)
    return {"summary": [item.to_dict() for item in records]}


def write_benchmark_report(*, output_dir: str | Path, static_results: Dict[str, Any] | None = None, long_dialog_results: Dict[str, Any] | None = None, leaderboard: Dict[str, Any] | None = None, config: BenchmarkConfig | None = None) -> Dict[str, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts: Dict[str, Path] = {}
    if static_results is not None:
        static_path = out_dir / "static_ab_results.json"
        static_path.write_text(json.dumps(static_results, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["static_json"] = static_path
    if long_dialog_results is not None:
        long_path = out_dir / "long_dialog_results.json"
        long_path.write_text(json.dumps(long_dialog_results, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["long_dialog_json"] = long_path
    if leaderboard is not None:
        leaderboard_path = out_dir / "leaderboard.json"
        leaderboard_path.write_text(json.dumps(leaderboard, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["leaderboard_json"] = leaderboard_path

    failure_items = [*((static_results or {}).get("failures", []) or []), *((long_dialog_results or {}).get("failures", []) or [])]
    failures_path = out_dir / "failures.jsonl"
    with failures_path.open("w", encoding="utf-8") as handle:
        for item in failure_items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    artifacts["failures_jsonl"] = failures_path

    lines: List[str] = [
        "# TMCRA Replacement A/B Report",
        "",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Answer mode: {(config.answer_mode if config else 'transparent')}",
        f"- Remote run id: {(config.remote_run_id if config else '') or 'local'}",
        "",
    ]
    if leaderboard is not None:
        lines.extend(["## Leaderboard", ""])
        for item in leaderboard.get("summary", [])[:12]:
            lines.append(f"- `{item['reasoner']}` x `{item['memory']}`: total={item['total_score']}, reasoning={item['reasoning_quality_score']}, memory={item['memory_quality_score']}, efficiency={item['efficiency_score']}")
        lines.append("")
    if static_results is not None:
        lines.extend(["## Static A/B Summary", ""])
        for item in static_results.get("summary", []) or []:
            lines.append(f"- `{item['reasoner']}` x `{item['memory']}`: answer={item['avg_answer_match']}, evidence={item['evidence_consistency_rate']}, memory={item['avg_memory_correctness']}, overwrite={item['avg_overwrite_resolution']}, latency={item['avg_latency_seconds']}s")
        lines.append("")
    if long_dialog_results is not None:
        lines.extend(["## Long Dialogue Memory Summary", ""])
        for item in long_dialog_results.get("summary", []) or []:
            lines.append(f"- `{item['memory']}`: memory_quality={item['memory_quality_score']}, efficiency={item['efficiency_score']}, context={item['avg_context_tokens']}, storage={item['avg_storage_bytes']}B, stale={item['avg_stale_recall_rate']}, false={item['avg_false_recall_rate']}")
        lines.append("")
    lines.extend(["## Failure Count", "", f"- Total failures logged: {len(failure_items)}", ""])
    report_path = out_dir / "replacement_ab_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    artifacts["report_md"] = report_path
    return artifacts


# ===== V2 replacement benchmark overrides =====

import copy
import math
import statistics

import numpy as np

from .result_labels import annotate_result_payload, format_code_with_label
from .token_usage import aggregate_token_usage, build_token_usage_summary, response_token_usage

try:  # pragma: no cover - optional
    import psutil
except Exception:  # pragma: no cover
    psutil = None


@dataclass(slots=True)
class BenchmarkConfig:
    answer_mode: str = "transparent"
    top_k: int = 6
    dialog_lengths: Sequence[int] = (3000, 5000, 10000, 20000)
    scaling_lengths: Sequence[int] = (50000, 100000, 500000)
    dialog_profiles: Sequence[str] = field(default_factory=lambda: tuple(item.profile_id for item in default_dialog_profiles()))
    scaling_profiles: Sequence[str] = field(default_factory=lambda: ("constraint_overwrite", "delayed_multi_hop_path", "distractor_collision"))
    scenario_profiles: Sequence[str] = field(default_factory=lambda: tuple(item.profile_id for item in default_scenario_profiles()))
    llm_profiles: Sequence[str] = field(default_factory=lambda: ("qwen7b", "deepseek7b"))
    score_weights: Dict[str, float] = field(default_factory=lambda: {"reasoning_quality": 1.0, "memory_quality": 1.0, "efficiency": 1.0})
    output_dir: Path | None = None
    static_case_limit: int = 0
    static_parallelism: int | str = "auto"
    include_long_dialog: bool = True
    include_reasoner_long_dialog: bool = True
    include_static: bool = True
    include_scaling: bool = False
    include_tunneling_ab: bool = False
    remote_run_id: str = ""
    case_prefix_filters: Sequence[str] = ()
    case_id_filters: Sequence[str] = ()
    progress_label: str = ""
    progress_callback: Callable[[Dict[str, Any]], None] | None = None
    snapshot_points: Sequence[int] = (1000, 5000, 10000, 20000, 50000, 100000, 200000, 300000, 500000)
    guard_thresholds: Dict[str, float] = field(
        default_factory=lambda: {
            "rss_bytes": 8.0 * 1024 * 1024 * 1024,
            "retrieval_p95_ms": 5000.0,
            "context_tokens": 2_000_000.0,
            "storage_bytes": 20.0 * 1024 * 1024 * 1024,
        }
    )


def default_scenario_profiles() -> List[ScenarioProfile]:
    return [
        ScenarioProfile("transparent_foundation", "Transparent foundation"),
        ScenarioProfile("terminology_continuity", "Terminology continuity"),
        ScenarioProfile("terminology_redefinition", "Terminology redefinition"),
        ScenarioProfile("constraint_preference_overwrite", "Constraint and preference overwrite"),
        ScenarioProfile("delayed_multi_constraint", "Delayed multi-constraint recall"),
        ScenarioProfile("conflict_history_trace", "Conflict and history trace"),
        ScenarioProfile("multi_hop_path_consistency", "Multi-hop path consistency"),
        ScenarioProfile("alias_version_stage_updates", "Alias, version and stage updates"),
        ScenarioProfile("multi_entity_separation", "Multi-entity separation"),
        ScenarioProfile("multi_source_merge", "Multi-source merge"),
        ScenarioProfile("multi_path_reasoning", "Multi-path reasoning"),
        ScenarioProfile("long_causal_chain_4plus", "Long causal chain 4+"),
        ScenarioProfile("constrained_path_reasoning", "Constrained path reasoning"),
        ScenarioProfile("counterfactual_reasoning", "Counterfactual reasoning"),
        ScenarioProfile("cross_level_reasoning", "Cross-level reasoning"),
        ScenarioProfile("non_intuitive_route_discovery", "Non-intuitive route discovery"),
        ScenarioProfile("multi_branch_search", "Multi-branch search"),
        ScenarioProfile("missing_info_reasoning", "Missing information reasoning"),
        ScenarioProfile("ambiguous_query_reasoning", "Ambiguous query reasoning"),
        ScenarioProfile("science_math_emotion_combo_reasoning", "Science, math and human emotion combo reasoning"),
        ScenarioProfile("chinese_state_tracking", "Chinese state tracking"),
        ScenarioProfile("chinese_overwrite_history", "Chinese overwrite and history"),
        ScenarioProfile("chinese_terminology_reference", "Chinese terminology reference"),
    ]


def _query_gpu_runtime_snapshot() -> Dict[str, float]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        lines = [line.strip() for line in str(result.stdout or "").splitlines() if line.strip()]
        if not lines:
            return {}
        util_gpu, util_mem, used_mem, total_mem = [float(part.strip()) for part in lines[0].split(",")[:4]]
        return {
            "gpu_util_percent": util_gpu,
            "gpu_memory_util_percent": util_mem,
            "gpu_memory_used_mib": used_mem,
            "gpu_memory_total_mib": total_mem,
            "gpu_memory_free_mib": max(0.0, total_mem - used_mem),
        }
    except Exception:
        return {}


def _resolve_static_parallelism(config: "BenchmarkConfig") -> tuple[int, Dict[str, Any]]:
    raw_value = getattr(config, "static_parallelism", "auto")
    if isinstance(raw_value, int) and raw_value > 0:
        return max(1, int(raw_value)), {"mode": "manual", "requested": int(raw_value), "reason": "fixed"}
    raw_text = str(raw_value or "").strip().lower()
    if raw_text and raw_text not in {"auto", "0"}:
        try:
            parsed = max(1, int(raw_text))
            return parsed, {"mode": "manual", "requested": parsed, "reason": "parsed"}
        except ValueError:
            pass
    cpu_count = int(os.cpu_count() or 1)
    if cpu_count < 8:
        return 1, {"mode": "auto", "requested": "auto", "reason": "cpu_below_8", "cpu_count": cpu_count}
    gpu_snapshot = _query_gpu_runtime_snapshot()
    if not gpu_snapshot:
        return (2 if cpu_count >= 16 else 1), {
            "mode": "auto",
            "requested": "auto",
            "reason": "gpu_probe_unavailable",
            "cpu_count": cpu_count,
        }
    if float(gpu_snapshot.get("gpu_util_percent", 100.0)) >= 75.0:
        return 1, {
            "mode": "auto",
            "requested": "auto",
            "reason": "gpu_util_high",
            "cpu_count": cpu_count,
            **gpu_snapshot,
        }
    if float(gpu_snapshot.get("gpu_memory_free_mib", 0.0)) < 256.0:
        return 1, {
            "mode": "auto",
            "requested": "auto",
            "reason": "gpu_memory_free_low",
            "cpu_count": cpu_count,
            **gpu_snapshot,
        }
    return 2, {
        "mode": "auto",
        "requested": "auto",
        "reason": "hardware_ready_for_two_way",
        "cpu_count": cpu_count,
        **gpu_snapshot,
    }


def default_dialog_profiles() -> List[LongDialogProfile]:
    return [
        LongDialogProfile("goal_persistence", "Goal persistence under noise"),
        LongDialogProfile("constraint_overwrite", "Constraint overwrite chain"),
        LongDialogProfile("preference_shift", "Preference shift"),
        LongDialogProfile("terminology_redefinition", "Terminology redefinition"),
        LongDialogProfile("stage_progression", "Stage progression"),
        LongDialogProfile("delayed_multi_hop_path", "Delayed multi-hop path"),
        LongDialogProfile("contradiction_saturation", "Contradiction saturation"),
        LongDialogProfile("distractor_collision", "Distractor collision"),
        LongDialogProfile("long_context_recall", "Long context recall"),
        LongDialogProfile("cross_turn_dependency", "Cross-turn dependency"),
        LongDialogProfile("multi_step_updates", "Multi-step updates"),
        LongDialogProfile("selective_retrieval", "Selective retrieval"),
        LongDialogProfile("missing_info", "Missing information"),
        LongDialogProfile("ambiguous_reference", "Ambiguous reference"),
        LongDialogProfile("multi_source_merge", "Multi-source merge"),
        LongDialogProfile("high_frequency_overwrite", "High-frequency overwrite"),
        LongDialogProfile("chinese_memory_persistence", "Chinese memory persistence"),
        LongDialogProfile("chinese_overwrite_chain", "Chinese overwrite chain"),
        LongDialogProfile("chinese_terminology_reference", "Chinese terminology reference"),
    ]


def _case_payload(**payload: Any) -> EvalCase:
    return _to_case(payload)


def _goal_family(variant: int) -> List[EvalCase]:
    suffix = f"foundation-{variant}"
    goal = f"build TMCRA as transparent reasoning engine alpha-{suffix}"
    protocol = f"every transparent answer must stay tied to cited paths and facts {suffix}"
    return [
        _case_payload(case_id=f"{suffix}-goal-seed", query=f"Goal update: {goal}.", answer_mode="transparent", category="goal", expected_answer_keywords=[goal], expected_memory_values=[goal], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "goal", "slot": "goal.primary", "value": goal, "anchors": ["TMCRA", "transparent reasoning engine"], "relation": "session_goal"}]}),
        _case_payload(case_id=f"{suffix}-goal-followup", query="What is the current primary goal?", answer_mode="transparent", category="memory_followup", expected_answer_keywords=[goal], expected_memory_values=[goal], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-constraint-seed", query=f"Constraint update: {protocol}.", answer_mode="transparent", category="constraint", expected_answer_keywords=["paths", "facts"], expected_memory_values=[protocol], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "constraint", "slot": "constraint.answer_protocol", "value": protocol, "anchors": ["paths", "facts"], "relation": "constrained_by"}]}),
        _case_payload(case_id=f"{suffix}-constraint-followup", query="What must every transparent answer stay tied to?", answer_mode="transparent", category="memory_followup", expected_answer_keywords=["paths", "facts"], expected_memory_values=[protocol], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-summary", query="Under the current protocol, what system are we building?", answer_mode="transparent", category="summary", expected_answer_keywords=["tmcra", goal], expected_memory_values=[goal], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-history", query="What was the earlier answer protocol statement?", answer_mode="transparent", category="history_query", expected_answer_keywords=[protocol], expected_memory_values=[protocol], metadata={"session_group": suffix}),
    ]


def _term_continuity_family(variant: int) -> List[EvalCase]:
    suffix = f"termkeep-{variant}"
    term = f"memory bridge {suffix}"
    meaning = f"{term} means the session memory graph {suffix}"
    goal = f"terminology continuity should preserve TMCRA vocabulary {suffix}"
    return [
        _case_payload(case_id=f"{suffix}-term-seed", query=f"Terminology: {meaning}.", answer_mode="transparent", category="terminology", expected_answer_keywords=["session memory graph"], expected_memory_values=[meaning], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "terminology", "slot": f"term.memory_bridge.{variant}", "value": meaning, "anchors": [term, "session memory graph"], "relation": "uses_term"}]}),
        _case_payload(case_id=f"{suffix}-term-followup", query=f"What does {term} mean here?", answer_mode="transparent", category="memory_followup", expected_answer_keywords=["session memory graph"], expected_memory_values=[meaning], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-goal-seed", query=f"Goal update: {goal}.", answer_mode="transparent", category="goal", expected_answer_keywords=["preserve", "vocabulary"], expected_memory_values=[goal], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "goal", "slot": f"goal.terminology.{variant}", "value": goal, "anchors": ["TMCRA vocabulary", "terminology continuity"], "relation": "session_goal"}]}),
        _case_payload(case_id=f"{suffix}-goal-followup", query="What is the terminology continuity goal?", answer_mode="transparent", category="memory_followup", expected_answer_keywords=["preserve", "vocabulary"], expected_memory_values=[goal], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-summary", query="Which term should stay stable across turns and why?", answer_mode="transparent", category="summary", expected_answer_keywords=[term, "session memory graph", "vocabulary"], expected_memory_values=[meaning], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-history", query=f"What was the exact earlier meaning assigned to {term}?", answer_mode="transparent", category="history_query", expected_answer_keywords=[meaning], expected_memory_values=[meaning], metadata={"session_group": suffix}),
    ]


def _term_redefinition_family(variant: int) -> List[EvalCase]:
    suffix = f"termredef-{variant}"
    term = f"memory bridge {suffix}"
    old_value = f"{term} means scratch buffer {suffix}"
    new_value = f"{term} means session graph memory {suffix}"
    return [
        _case_payload(case_id=f"{suffix}-seed-old", query=f"Terminology seed: {old_value}.", answer_mode="transparent", category="terminology", expected_answer_keywords=["scratch buffer"], expected_memory_values=[old_value], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "terminology", "slot": f"term.memory_bridge.redef.{variant}", "value": old_value, "anchors": [term, "scratch buffer"], "relation": "uses_term"}]}),
        _case_payload(case_id=f"{suffix}-seed-new", query=f"Terminology overwrite: {new_value}.", answer_mode="transparent", category="terminology", expected_answer_keywords=["session graph memory"], expected_memory_values=[new_value], expected_absent_values=[old_value], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "terminology", "slot": f"term.memory_bridge.redef.{variant}", "value": new_value, "anchors": [term, "session graph memory"], "relation": "uses_term"}]}),
        _case_payload(case_id=f"{suffix}-followup-current", query=f"What does {term} mean now?", answer_mode="transparent", category="memory_followup", expected_answer_keywords=["session graph memory"], expected_memory_values=[new_value], expected_absent_values=[old_value], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-history", query=f"What did {term} mean before the overwrite?", answer_mode="transparent", category="history_query", expected_answer_keywords=["scratch buffer"], expected_memory_values=[old_value], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-summary", query=f"Summarize the current and previous meanings of {term}.", answer_mode="transparent", category="summary", expected_answer_keywords=["session graph memory", "scratch buffer"], expected_memory_values=[new_value], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-contradiction", query=f"Which meaning is active and which one is historical for {term}?", answer_mode="transparent", category="summary", expected_answer_keywords=["active", "historical", "session graph memory", "scratch buffer"], expected_memory_values=[new_value], metadata={"session_group": suffix}),
    ]


def _overwrite_family(variant: int) -> List[EvalCase]:
    suffix = f"overwrite-{variant}"
    old_policy = f"external llm never gives the final verdict {suffix}"
    new_policy = f"teacher llms may assist supervision but TMCRA evidence owns the final verdict {suffix}"
    preference = f"natural default with transparent expansion {suffix}"
    return [
        _case_payload(case_id=f"{suffix}-constraint-old", query=f"Constraint update: {old_policy}.", answer_mode="transparent", category="constraint", expected_answer_keywords=["final verdict"], expected_memory_values=[old_policy], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "constraint", "slot": f"constraint.teacher_policy.{variant}", "value": old_policy, "anchors": ["external llm", "final verdict"], "relation": "constrained_by"}]}),
        _case_payload(case_id=f"{suffix}-constraint-new", query=f"Constraint overwrite: {new_policy}.", answer_mode="transparent", category="constraint", expected_answer_keywords=["teacher", "TMCRA evidence"], expected_memory_values=[new_policy], expected_absent_values=[old_policy], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "constraint", "slot": f"constraint.teacher_policy.{variant}", "value": new_policy, "anchors": ["teacher supervision", "TMCRA evidence"], "relation": "constrained_by"}]}),
        _case_payload(case_id=f"{suffix}-constraint-followup", query="What is the current teacher policy?", answer_mode="transparent", category="memory_followup", expected_answer_keywords=["teacher", "TMCRA evidence"], expected_memory_values=[new_policy], expected_absent_values=[old_policy], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-preference-seed", query=f"Preference update: {preference}.", answer_mode="transparent", category="preference", expected_answer_keywords=["natural", "transparent"], expected_memory_values=[preference], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "preference", "slot": f"preference.answer_mode.{variant}", "value": preference, "anchors": ["natural", "transparent"], "relation": "prefers"}]}),
        _case_payload(case_id=f"{suffix}-preference-followup", query="What is the default answer mode now?", answer_mode="transparent", category="memory_followup", expected_answer_keywords=["natural", "transparent"], expected_memory_values=[preference], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-history", query="What was the older teacher policy before overwrite?", answer_mode="transparent", category="history_query", expected_answer_keywords=[old_policy], expected_memory_values=[old_policy], metadata={"session_group": suffix}),
    ]


def _delayed_constraint_family(variant: int) -> List[EvalCase]:
    suffix = f"delayed-{variant}"
    constraint_a = f"keep evidence-first ranking active {suffix}"
    constraint_b = f"keep graph memory inside the evidence loop {suffix}"
    stage_value = f"benchmarking phase delta-{variant}"
    noise = f"ghost-mode should not become active requirement {suffix}"
    return [
        _case_payload(case_id=f"{suffix}-seed-a", query=f"Constraint update: {constraint_a}.", answer_mode="transparent", category="constraint", expected_answer_keywords=["evidence-first"], expected_memory_values=[constraint_a], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "constraint", "slot": f"constraint.evidence_first.{variant}", "value": constraint_a, "anchors": ["evidence-first ranking"], "relation": "constrained_by"}]}),
        _case_payload(case_id=f"{suffix}-seed-b", query=f"Constraint update: {constraint_b}.", answer_mode="transparent", category="constraint", expected_answer_keywords=["graph memory"], expected_memory_values=[constraint_b], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "constraint", "slot": f"constraint.graph_loop.{variant}", "value": constraint_b, "anchors": ["graph memory", "evidence loop"], "relation": "constrained_by"}]}),
        _case_payload(case_id=f"{suffix}-stage", query=f"Stage update: the system is in {stage_value}.", answer_mode="transparent", category="stage_state", expected_answer_keywords=[stage_value], expected_memory_values=[stage_value], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "stage_state", "slot": f"stage.current.{variant}", "value": stage_value, "anchors": ["benchmarking phase"], "relation": "stage_state"}]}),
        _case_payload(case_id=f"{suffix}-noise", query=f"Noise note: {noise}.", answer_mode="transparent", category="noise", expected_answer_keywords=["ghost-mode"], metadata={"session_group": suffix, "false_values": [noise]}),
        _case_payload(case_id=f"{suffix}-followup", query="After the delay, which active constraints still apply and what stage are we in?", answer_mode="transparent", category="summary", expected_answer_keywords=["evidence-first", "graph memory", stage_value], expected_memory_values=[constraint_a, constraint_b, stage_value], expected_absent_values=[noise], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-history", query="Which statement was just noise and should stay inactive?", answer_mode="transparent", category="history_query", expected_answer_keywords=["ghost-mode"], metadata={"session_group": suffix}),
    ]


def _history_conflict_family(variant: int) -> List[EvalCase]:
    suffix = f"history-{variant}"
    old_stage = f"design phase {suffix}"
    new_stage = f"deployment validation phase {suffix}"
    return [
        _case_payload(case_id=f"{suffix}-old", query=f"Stage update: the system is in {old_stage}.", answer_mode="transparent", category="stage_state", expected_answer_keywords=[old_stage], expected_memory_values=[old_stage], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "stage_state", "slot": f"stage.current.history.{variant}", "value": old_stage, "anchors": ["design"], "relation": "stage_state"}]}),
        _case_payload(case_id=f"{suffix}-new", query=f"Stage overwrite: the system is in {new_stage}.", answer_mode="transparent", category="stage_state", expected_answer_keywords=[new_stage], expected_memory_values=[new_stage], expected_absent_values=[old_stage], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "stage_state", "slot": f"stage.current.history.{variant}", "value": new_stage, "anchors": ["deployment validation"], "relation": "stage_state"}]}),
        _case_payload(case_id=f"{suffix}-current", query="What stage is currently active?", answer_mode="transparent", category="memory_followup", expected_answer_keywords=[new_stage], expected_memory_values=[new_stage], expected_absent_values=[old_stage], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-history", query="What stage was active before the overwrite?", answer_mode="transparent", category="history_query", expected_answer_keywords=[old_stage], expected_memory_values=[old_stage], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-summary", query="Summarize the current stage and previous stage.", answer_mode="transparent", category="summary", expected_answer_keywords=[new_stage, old_stage], expected_memory_values=[new_stage], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-status", query="Which stage is active and which one is historical?", answer_mode="transparent", category="summary", expected_answer_keywords=["active", "historical", new_stage, old_stage], expected_memory_values=[new_stage], metadata={"session_group": suffix}),
    ]


def _path_family(variant: int) -> List[EvalCase]:
    suffix = f"path-{variant}"
    a = f"user query {suffix}"
    b = f"candidate ranking {suffix}"
    c = f"transparent answer {suffix}"
    d = f"session memory graph {suffix}"
    return [
        _case_payload(case_id=f"{suffix}-a", query=f"Path fact: {a} leads to {b}.", answer_mode="transparent", category="fact", expected_fact_phrases=[a, b], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "goal", "slot": f"path.segment_a.{variant}", "value": f"{a} leads to {b}", "anchors": [a, b], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-b", query=f"Path fact: {b} leads to {c}.", answer_mode="transparent", category="fact", expected_fact_phrases=[b, c], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "goal", "slot": f"path.segment_b.{variant}", "value": f"{b} leads to {c}", "anchors": [b, c], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-c", query=f"Path fact: {c} leads to {d}.", answer_mode="transparent", category="fact", expected_fact_phrases=[c, d], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "goal", "slot": f"path.segment_c.{variant}", "value": f"{c} leads to {d}", "anchors": [c, d], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-route", query=f"What path connects {a} to {d}?", answer_mode="transparent", category="path_followup", expected_answer_keywords=[a, b, c, d], expected_fact_phrases=[a, b, c, d], expected_path_concepts=[a, b, c, d], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-why", query=f"Why is {d} still tied back to {a}?", answer_mode="transparent", category="path_followup", expected_answer_keywords=[b, c], expected_fact_phrases=[a, b, c, d], expected_path_concepts=[a, b, c, d], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-summary", query=f"Summarize the full reasoning chain from {a} to {d}.", answer_mode="transparent", category="summary", expected_answer_keywords=[a, b, c, d], expected_fact_phrases=[a, b, c, d], expected_path_concepts=[a, b, c, d], metadata={"session_group": suffix}),
    ]


def _alias_version_family(variant: int) -> List[EvalCase]:
    suffix = f"alias-{variant}"
    alias = f"policy v{variant}"
    version = f"build 2026.03.{10 + variant}"
    stage = f"release-candidate-{variant}"
    return [
        _case_payload(case_id=f"{suffix}-alias", query=f"Terminology: {alias} means transparent policy network {suffix}.", answer_mode="transparent", category="terminology", expected_answer_keywords=["transparent policy network"], expected_memory_values=[f"{alias} means transparent policy network {suffix}"], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "terminology", "slot": f"term.policy_alias.{variant}", "value": f"{alias} means transparent policy network {suffix}", "anchors": [alias, "transparent policy network"], "relation": "uses_term"}]}),
        _case_payload(case_id=f"{suffix}-version", query=f"Constraint update: current deployed version is {version}.", answer_mode="transparent", category="constraint", expected_answer_keywords=[version], expected_memory_values=[f"current deployed version is {version}"], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "constraint", "slot": f"constraint.version.{variant}", "value": f"current deployed version is {version}", "anchors": [version, "deployed version"], "relation": "constrained_by"}]}),
        _case_payload(case_id=f"{suffix}-stage", query=f"Stage update: the release stage is {stage}.", answer_mode="transparent", category="stage_state", expected_answer_keywords=[stage], expected_memory_values=[f"the release stage is {stage}"], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "stage_state", "slot": f"stage.release.{variant}", "value": f"the release stage is {stage}", "anchors": [stage, "release stage"], "relation": "stage_state"}]}),
        _case_payload(case_id=f"{suffix}-followup-alias", query=f"What does {alias} mean?", answer_mode="transparent", category="memory_followup", expected_answer_keywords=["transparent policy network"], expected_memory_values=[f"{alias} means transparent policy network {suffix}"], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-followup-version", query="Which build is currently deployed?", answer_mode="transparent", category="memory_followup", expected_answer_keywords=[version], expected_memory_values=[f"current deployed version is {version}"], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-summary", query="Summarize the alias, deployed version, and release stage.", answer_mode="transparent", category="summary", expected_answer_keywords=[alias, version, stage], expected_memory_values=[f"{alias} means transparent policy network {suffix}", f"current deployed version is {version}"], metadata={"session_group": suffix}),
    ]


def _chinese_state_tracking_family(variant: int) -> List[EvalCase]:
    suffix = f"zhstate-{variant}"
    goal = f"中文记忆评测跑通 {suffix}"
    preference = f"本地部署优先 {suffix}"
    constraint = f"不要上传隐私数据 {suffix}"
    stage = f"联调验证阶段 {suffix}"
    return [
        _case_payload(case_id=f"{suffix}-goal", query=f"请记住：当前目标是{goal}。", answer_mode="transparent", category="goal", expected_answer_keywords=[goal], expected_memory_values=[goal], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "goal", "slot": f"goal.zh.primary.{variant}", "value": goal, "anchors": ["中文记忆评测", "跑通"], "relation": "session_goal"}]}),
        _case_payload(case_id=f"{suffix}-preference", query=f"请记住：当前偏好是{preference}。", answer_mode="transparent", category="preference", expected_answer_keywords=[preference], expected_memory_values=[preference], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "preference", "slot": f"preference.zh.deploy.{variant}", "value": preference, "anchors": ["本地部署", "优先"], "relation": "prefers"}]}),
        _case_payload(case_id=f"{suffix}-constraint", query=f"请记住：当前约束是{constraint}。", answer_mode="transparent", category="constraint", expected_answer_keywords=[constraint], expected_memory_values=[constraint], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "constraint", "slot": f"constraint.zh.privacy.{variant}", "value": constraint, "anchors": ["隐私数据"], "relation": "constrained_by"}]}),
        _case_payload(case_id=f"{suffix}-stage", query=f"请记住：当前阶段是{stage}。", answer_mode="transparent", category="stage_state", expected_answer_keywords=[stage], expected_memory_values=[stage], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "stage_state", "slot": f"stage.zh.current.{variant}", "value": stage, "anchors": ["联调验证"], "relation": "stage_state"}]}),
        _case_payload(case_id=f"{suffix}-followup", query="当前核心目标是什么？", answer_mode="transparent", category="memory_followup", expected_answer_keywords=[goal], expected_memory_values=[goal], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-summary", query="当前目标、偏好、约束和阶段分别是什么？", answer_mode="transparent", category="summary", expected_answer_keywords=[goal, preference, constraint, stage], expected_memory_values=[goal, preference, constraint, stage], metadata={"session_group": suffix}),
    ]


def _chinese_overwrite_family(variant: int) -> List[EvalCase]:
    suffix = f"zhoverwrite-{variant}"
    old_constraint = f"只用本地模型 {suffix}"
    new_constraint = f"允许混合部署 {suffix}"
    old_stage = f"预演阶段 {suffix}"
    new_stage = f"联调阶段 {suffix}"
    return [
        _case_payload(case_id=f"{suffix}-constraint-old", query=f"请记住：最初约束是{old_constraint}。", answer_mode="transparent", category="constraint", expected_answer_keywords=[old_constraint], expected_memory_values=[old_constraint], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "constraint", "slot": f"constraint.zh.mode.{variant}", "value": old_constraint, "anchors": ["本地模型"], "relation": "constrained_by"}]}),
        _case_payload(case_id=f"{suffix}-constraint-new", query=f"约束更新：现在改成{new_constraint}。", answer_mode="transparent", category="constraint", expected_answer_keywords=[new_constraint], expected_memory_values=[new_constraint], expected_absent_values=[old_constraint], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "constraint", "slot": f"constraint.zh.mode.{variant}", "value": new_constraint, "anchors": ["混合部署"], "relation": "constrained_by"}]}),
        _case_payload(case_id=f"{suffix}-stage-old", query=f"请记住：最初阶段是{old_stage}。", answer_mode="transparent", category="stage_state", expected_answer_keywords=[old_stage], expected_memory_values=[old_stage], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "stage_state", "slot": f"stage.zh.chain.{variant}", "value": old_stage, "anchors": ["预演"], "relation": "stage_state"}]}),
        _case_payload(case_id=f"{suffix}-stage-new", query=f"阶段更新：当前阶段改成{new_stage}。", answer_mode="transparent", category="stage_state", expected_answer_keywords=[new_stage], expected_memory_values=[new_stage], expected_absent_values=[old_stage], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "stage_state", "slot": f"stage.zh.chain.{variant}", "value": new_stage, "anchors": ["联调"], "relation": "stage_state"}]}),
        _case_payload(case_id=f"{suffix}-current", query="当前约束和当前阶段分别是什么？", answer_mode="transparent", category="summary", expected_answer_keywords=[new_constraint, new_stage], expected_memory_values=[new_constraint, new_stage], expected_absent_values=[old_constraint, old_stage], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-history", query="之前的约束和之前的阶段分别是什么？", answer_mode="transparent", category="history_query", expected_answer_keywords=[old_constraint, old_stage], expected_memory_values=[old_constraint, old_stage], metadata={"session_group": suffix}),
    ]


def _chinese_terminology_family(variant: int) -> List[EvalCase]:
    suffix = f"zhterm-{variant}"
    term = f"透明链路{variant}"
    old_value = f"{term}指的是草稿缓冲区 {suffix}"
    new_value = f"{term}指的是带证据的推理路径 {suffix}"
    preference = f"回答时优先引用{term}相关证据 {suffix}"
    return [
        _case_payload(case_id=f"{suffix}-term-old", query=f"术语设定：{old_value}。", answer_mode="transparent", category="terminology", expected_answer_keywords=["草稿缓冲区"], expected_memory_values=[old_value], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "terminology", "slot": f"term.zh.trace.{variant}", "value": old_value, "anchors": [term, "草稿缓冲区"], "relation": "uses_term"}]}),
        _case_payload(case_id=f"{suffix}-term-new", query=f"术语更新：{new_value}。", answer_mode="transparent", category="terminology", expected_answer_keywords=["带证据的推理路径"], expected_memory_values=[new_value], expected_absent_values=[old_value], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "terminology", "slot": f"term.zh.trace.{variant}", "value": new_value, "anchors": [term, "带证据的推理路径"], "relation": "uses_term"}]}),
        _case_payload(case_id=f"{suffix}-preference", query=f"偏好设定：{preference}。", answer_mode="transparent", category="preference", expected_answer_keywords=[preference], expected_memory_values=[preference], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "preference", "slot": f"preference.zh.term.{variant}", "value": preference, "anchors": [term, "引用证据"], "relation": "prefers"}]}),
        _case_payload(case_id=f"{suffix}-followup", query=f"{term}现在指的是什么？", answer_mode="transparent", category="memory_followup", expected_answer_keywords=["带证据的推理路径"], expected_memory_values=[new_value], expected_absent_values=[old_value], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-history", query=f"{term}之前指的是什么？", answer_mode="transparent", category="history_query", expected_answer_keywords=["草稿缓冲区"], expected_memory_values=[old_value], metadata={"session_group": suffix}),
        _case_payload(case_id=f"{suffix}-summary", query=f"请总结{term}的当前含义和回答偏好。", answer_mode="transparent", category="summary", expected_answer_keywords=[term, "带证据的推理路径", "引用", "证据"], expected_memory_values=[new_value, preference], metadata={"session_group": suffix}),
    ]


def _multi_entity_family(variant: int) -> List[EvalCase]:
    suffix = f"entity-{variant}"
    alpha = f"teacher-alpha-{suffix}"
    beta = f"teacher-beta-{suffix}"
    alpha_value = f"{alpha} owns graph-memory audits {suffix}"
    beta_value = f"{beta} owns free-context validation {suffix}"
    return [
        _case_payload(case_id=f"{suffix}-alpha-seed", query=f"Fact: {alpha_value}.", answer_mode="transparent", category="fact", expected_answer_keywords=[alpha], expected_memory_values=[alpha_value], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"entity.owner.alpha.{variant}", "value": alpha_value, "anchors": [alpha, "graph-memory audits"], "relation": "owns"}]}),
        _case_payload(case_id=f"{suffix}-beta-seed", query=f"Fact: {beta_value}.", answer_mode="transparent", category="fact", expected_answer_keywords=[beta], expected_memory_values=[beta_value], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"entity.owner.beta.{variant}", "value": beta_value, "anchors": [beta, "free-context validation"], "relation": "owns"}]}),
        _case_payload(case_id=f"{suffix}-alpha-followup", query=f"What does {alpha} own?", answer_mode="transparent", category="memory_followup", expected_answer_keywords=["graph-memory audits"], expected_memory_values=[alpha_value], expected_absent_values=[beta_value], metadata={"session_group": suffix, "entity_isolation_targets": [alpha], "entity_isolation_absent": [beta]}),
        _case_payload(case_id=f"{suffix}-beta-followup", query=f"What does {beta} own?", answer_mode="transparent", category="memory_followup", expected_answer_keywords=["free-context validation"], expected_memory_values=[beta_value], expected_absent_values=[alpha_value], metadata={"session_group": suffix, "entity_isolation_targets": [beta], "entity_isolation_absent": [alpha]}),
    ]


def _multi_path_reasoning_family(variant: int) -> List[EvalCase]:
    suffix = f"multipath-{variant}"
    a = f"question-core-{suffix}"
    b1 = f"candidate-ranking-{suffix}"
    c1 = f"transparent-answer-{suffix}"
    b2 = f"memory-bridge-{suffix}"
    c2 = f"evidence-bundle-{suffix}"
    d = f"session-graph-{suffix}"
    return [
        _case_payload(case_id=f"{suffix}-a1", query=f"Path fact: {a} leads to {b1}.", answer_mode="transparent", category="fact", expected_fact_phrases=[a, b1], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.multi.a1.{variant}", "value": f"{a} leads to {b1}", "anchors": [a, b1], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-a2", query=f"Path fact: {b1} leads to {c1}.", answer_mode="transparent", category="fact", expected_fact_phrases=[b1, c1], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.multi.a2.{variant}", "value": f"{b1} leads to {c1}", "anchors": [b1, c1], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-a3", query=f"Path fact: {c1} leads to {d}.", answer_mode="transparent", category="fact", expected_fact_phrases=[c1, d], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.multi.a3.{variant}", "value": f"{c1} leads to {d}", "anchors": [c1, d], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-b1", query=f"Path fact: {a} leads to {b2}.", answer_mode="transparent", category="fact", expected_fact_phrases=[a, b2], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.multi.b1.{variant}", "value": f"{a} leads to {b2}", "anchors": [a, b2], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-b2", query=f"Path fact: {b2} leads to {c2}.", answer_mode="transparent", category="fact", expected_fact_phrases=[b2, c2], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.multi.b2.{variant}", "value": f"{b2} leads to {c2}", "anchors": [b2, c2], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-b3", query=f"Path fact: {c2} leads to {d}.", answer_mode="transparent", category="fact", expected_fact_phrases=[c2, d], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.multi.b3.{variant}", "value": f"{c2} leads to {d}", "anchors": [c2, d], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-route", query=f"What are two different paths from {a} to {d}?", answer_mode="transparent", category="path_followup", expected_answer_keywords=[a, b1, c1, b2, c2, d], expected_fact_phrases=[a, b1, c1, b2, c2, d], expected_path_concepts=[a, d], metadata={"session_group": suffix, "alternative_path_sets": [[a, b1, c1, d], [a, b2, c2, d]], "critical_nodes": [b1, c1, b2, c2]}),
    ]


def _constrained_path_family(variant: int) -> List[EvalCase]:
    suffix = f"constraintpath-{variant}"
    a = f"query-root-{suffix}"
    b = f"constraint-gate-{suffix}"
    c = f"reasoning-core-{suffix}"
    d = f"verdict-node-{suffix}"
    return [
        _case_payload(case_id=f"{suffix}-1", query=f"Path fact: {a} leads to {b}.", answer_mode="transparent", category="fact", expected_fact_phrases=[a, b], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.constraint.1.{variant}", "value": f"{a} leads to {b}", "anchors": [a, b], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-2", query=f"Path fact: {b} leads to {c}.", answer_mode="transparent", category="fact", expected_fact_phrases=[b, c], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.constraint.2.{variant}", "value": f"{b} leads to {c}", "anchors": [b, c], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-3", query=f"Path fact: {c} leads to {d}.", answer_mode="transparent", category="fact", expected_fact_phrases=[c, d], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.constraint.3.{variant}", "value": f"{c} leads to {d}", "anchors": [c, d], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-route", query=f"What path connects {a} to {d} and must include {b}?", answer_mode="transparent", category="path_followup", expected_answer_keywords=[a, b, c, d], expected_fact_phrases=[a, b, c, d], expected_path_concepts=[a, b, c, d], metadata={"session_group": suffix, "critical_nodes": [b, c]}),
    ]


def _counterfactual_family(variant: int) -> List[EvalCase]:
    suffix = f"counterfactual-{variant}"
    a = f"query-input-{suffix}"
    b = f"bridge-node-{suffix}"
    c = f"fallback-node-{suffix}"
    d = f"answer-output-{suffix}"
    return [
        _case_payload(case_id=f"{suffix}-1", query=f"Path fact: {a} leads to {b}.", answer_mode="transparent", category="fact", expected_fact_phrases=[a, b], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.cf.1.{variant}", "value": f"{a} leads to {b}", "anchors": [a, b], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-2", query=f"Path fact: {b} leads to {d}.", answer_mode="transparent", category="fact", expected_fact_phrases=[b, d], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.cf.2.{variant}", "value": f"{b} leads to {d}", "anchors": [b, d], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-3", query=f"Path fact: {a} leads to {c}.", answer_mode="transparent", category="fact", expected_fact_phrases=[a, c], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.cf.3.{variant}", "value": f"{a} leads to {c}", "anchors": [a, c], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-4", query=f"Path fact: {c} leads to {d}.", answer_mode="transparent", category="fact", expected_fact_phrases=[c, d], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.cf.4.{variant}", "value": f"{c} leads to {d}", "anchors": [c, d], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-route", query=f"Without {b}, is there still a path from {a} to {d}?", answer_mode="transparent", category="path_followup", expected_answer_keywords=[a, c, d, "still exists"], expected_fact_phrases=[a, c, d], expected_path_concepts=[a, c, d], metadata={"session_group": suffix, "critical_nodes": [c], "counterfactual_expected": "path_still_exists"}),
    ]


def _multi_source_merge_family(variant: int) -> List[EvalCase]:
    suffix = f"multisource-{variant}"
    goal = f"ship transparent hybrid runtime {suffix}"
    stage = f"evaluation-stage-{suffix}"
    constraint = f"must keep evidence and memory aligned {suffix}"
    return [
        _case_payload(case_id=f"{suffix}-goal", query=f"Goal update: {goal}.", answer_mode="transparent", category="goal", expected_answer_keywords=[goal], expected_memory_values=[goal], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "goal", "slot": f"goal.multi_source.{variant}", "value": goal, "anchors": ["transparent hybrid runtime", suffix], "relation": "session_goal"}]}),
        _case_payload(case_id=f"{suffix}-stage", query=f"Stage update: the system is in {stage}.", answer_mode="transparent", category="stage_state", expected_answer_keywords=[stage], expected_memory_values=[stage], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "stage_state", "slot": f"stage.multi_source.{variant}", "value": stage, "anchors": [stage, "system"], "relation": "stage_state"}]}),
        _case_payload(case_id=f"{suffix}-constraint", query=f"Constraint update: {constraint}.", answer_mode="transparent", category="constraint", expected_answer_keywords=["evidence", "memory"], expected_memory_values=[constraint], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "constraint", "slot": f"constraint.multi_source.{variant}", "value": constraint, "anchors": ["evidence", "memory"], "relation": "constrained_by"}]}),
        _case_payload(case_id=f"{suffix}-summary", query="Combine the current goal, stage, and active constraint into one answer.", answer_mode="transparent", category="summary", expected_answer_keywords=[goal, stage, "evidence", "memory"], expected_memory_values=[goal, stage, constraint], metadata={"session_group": suffix, "multi_source_merge": True}),
    ]


def _causal_chain_family(variant: int) -> List[EvalCase]:
    suffix = f"causal4-{variant}"
    a = f"signal-source-{suffix}"
    b = f"route-selector-{suffix}"
    c = f"evidence-gate-{suffix}"
    d = f"memory-bridge-{suffix}"
    e = f"answer-verdict-{suffix}"
    return [
        _case_payload(case_id=f"{suffix}-1", query=f"Path fact: {a} leads to {b}.", answer_mode="transparent", category="fact", expected_fact_phrases=[a, b], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.causal.1.{variant}", "value": f"{a} leads to {b}", "anchors": [a, b], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-2", query=f"Path fact: {b} leads to {c}.", answer_mode="transparent", category="fact", expected_fact_phrases=[b, c], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.causal.2.{variant}", "value": f"{b} leads to {c}", "anchors": [b, c], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-3", query=f"Path fact: {c} leads to {d}.", answer_mode="transparent", category="fact", expected_fact_phrases=[c, d], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.causal.3.{variant}", "value": f"{c} leads to {d}", "anchors": [c, d], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-4", query=f"Path fact: {d} leads to {e}.", answer_mode="transparent", category="fact", expected_fact_phrases=[d, e], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.causal.4.{variant}", "value": f"{d} leads to {e}", "anchors": [d, e], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-route", query=f"Explain the 4-step causal chain from {a} to {e}.", answer_mode="transparent", category="path_followup", expected_answer_keywords=[a, b, c, d, e], expected_fact_phrases=[a, b, c, d, e], expected_path_concepts=[a, b, c, d, e], metadata={"session_group": suffix, "critical_nodes": [b, c, d]}),
    ]


def _cross_level_reasoning_family(variant: int) -> List[EvalCase]:
    suffix = f"crosslevel-{variant}"
    a = f"user-intent-{suffix}"
    b = f"task-plan-{suffix}"
    c = f"benchmark-metric-{suffix}"
    d = f"resource-budget-{suffix}"
    return [
        _case_payload(case_id=f"{suffix}-1", query=f"Path fact: {a} leads to {b}.", answer_mode="transparent", category="fact", expected_fact_phrases=[a, b], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.crosslevel.1.{variant}", "value": f"{a} leads to {b}", "anchors": [a, b], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-2", query=f"Path fact: {b} leads to {c}.", answer_mode="transparent", category="fact", expected_fact_phrases=[b, c], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.crosslevel.2.{variant}", "value": f"{b} leads to {c}", "anchors": [b, c], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-3", query=f"Path fact: {c} leads to {d}.", answer_mode="transparent", category="fact", expected_fact_phrases=[c, d], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.crosslevel.3.{variant}", "value": f"{c} leads to {d}", "anchors": [c, d], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-route", query=f"How does {a} propagate across abstraction levels to {d}?", answer_mode="transparent", category="path_followup", expected_answer_keywords=[a, b, c, d], expected_fact_phrases=[a, b, c, d], expected_path_concepts=[a, b, c, d], metadata={"session_group": suffix, "critical_nodes": [b, c]}),
    ]


def _non_intuitive_route_family(variant: int) -> List[EvalCase]:
    suffix = f"nonintuitive-{variant}"
    a = f"query-root-{suffix}"
    b = f"common-shortcut-{suffix}"
    c = f"boundary-probe-{suffix}"
    d = f"tunnel-bridge-{suffix}"
    e = f"verdict-node-{suffix}"
    return [
        _case_payload(case_id=f"{suffix}-1", query=f"Path fact: {a} leads to {b}.", answer_mode="transparent", category="fact", expected_fact_phrases=[a, b], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.nonintuitive.1.{variant}", "value": f"{a} leads to {b}", "anchors": [a, b], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-2", query=f"Path fact: {b} leads to dead-end note {suffix}.", answer_mode="transparent", category="fact", expected_fact_phrases=[b], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.nonintuitive.dead.{variant}", "value": f"{b} leads to dead-end note {suffix}", "anchors": [b, f"dead-end note {suffix}"], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-3", query=f"Path fact: {a} leads to {c}.", answer_mode="transparent", category="fact", expected_fact_phrases=[a, c], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.nonintuitive.2.{variant}", "value": f"{a} leads to {c}", "anchors": [a, c], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-4", query=f"Path fact: {c} leads to {d}.", answer_mode="transparent", category="fact", expected_fact_phrases=[c, d], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.nonintuitive.3.{variant}", "value": f"{c} leads to {d}", "anchors": [c, d], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-5", query=f"Path fact: {d} leads to {e}.", answer_mode="transparent", category="fact", expected_fact_phrases=[d, e], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.nonintuitive.4.{variant}", "value": f"{d} leads to {e}", "anchors": [d, e], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-route", query=f"Find the non-obvious path from {a} to {e}.", answer_mode="transparent", category="path_followup", expected_answer_keywords=[a, c, d, e], expected_fact_phrases=[a, c, d, e], expected_path_concepts=[a, c, d, e], metadata={"session_group": suffix, "critical_nodes": [c, d], "expected_absent_path_nodes": [b]}),
    ]


def _multi_branch_search_family(variant: int) -> List[EvalCase]:
    suffix = f"multibranch-{variant}"
    a = f"origin-{suffix}"
    b1 = f"branch-a-{suffix}"
    b2 = f"branch-b-{suffix}"
    b3 = f"branch-c-{suffix}"
    e = f"goal-{suffix}"
    return [
        _case_payload(case_id=f"{suffix}-1", query=f"Path fact: {a} leads to {b1}.", answer_mode="transparent", category="fact", expected_fact_phrases=[a, b1], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.branch.1.{variant}", "value": f"{a} leads to {b1}", "anchors": [a, b1], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-2", query=f"Path fact: {a} leads to {b2}.", answer_mode="transparent", category="fact", expected_fact_phrases=[a, b2], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.branch.2.{variant}", "value": f"{a} leads to {b2}", "anchors": [a, b2], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-3", query=f"Path fact: {a} leads to {b3}.", answer_mode="transparent", category="fact", expected_fact_phrases=[a, b3], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.branch.3.{variant}", "value": f"{a} leads to {b3}", "anchors": [a, b3], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-4", query=f"Path fact: {b1} leads to {e}.", answer_mode="transparent", category="fact", expected_fact_phrases=[b1, e], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.branch.4.{variant}", "value": f"{b1} leads to {e}", "anchors": [b1, e], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-5", query=f"Path fact: {b2} leads to {e}.", answer_mode="transparent", category="fact", expected_fact_phrases=[b2, e], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.branch.5.{variant}", "value": f"{b2} leads to {e}", "anchors": [b2, e], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-route", query=f"Give multiple valid branches from {a} to {e}.", answer_mode="transparent", category="path_followup", expected_answer_keywords=[a, b1, b2, e], expected_fact_phrases=[a, b1, b2, e], expected_path_concepts=[a, e], metadata={"session_group": suffix, "alternative_path_sets": [[a, b1, e], [a, b2, e]], "critical_nodes": [b1, b2]}),
    ]


def _missing_info_reasoning_family(variant: int) -> List[EvalCase]:
    suffix = f"missingreason-{variant}"
    a = f"input-node-{suffix}"
    b = f"known-bridge-{suffix}"
    c = f"unknown-gap-{suffix}"
    d = f"target-node-{suffix}"
    return [
        _case_payload(case_id=f"{suffix}-1", query=f"Path fact: {a} leads to {b}.", answer_mode="transparent", category="fact", expected_fact_phrases=[a, b], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.missing.1.{variant}", "value": f"{a} leads to {b}", "anchors": [a, b], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-2", query=f"Path fact: {c} leads to {d}.", answer_mode="transparent", category="fact", expected_fact_phrases=[c, d], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"path.missing.2.{variant}", "value": f"{c} leads to {d}", "anchors": [c, d], "relation": "path_edge"}]}),
        _case_payload(case_id=f"{suffix}-route", query=f"What is missing if we want a complete path from {a} to {d}?", answer_mode="transparent", category="path_followup", expected_answer_keywords=[a, b, c, d, "missing"], expected_fact_phrases=[a, b, c, d], expected_path_concepts=[a, b, d], metadata={"session_group": suffix, "critical_nodes": [b, c], "missing_info_expected": c}),
    ]


def _ambiguous_reasoning_family(variant: int) -> List[EvalCase]:
    suffix = f"ambiguousreason-{variant}"
    alpha = f"planner-alpha-{suffix}"
    beta = f"planner-beta-{suffix}"
    alpha_value = f"{alpha} owns path search {suffix}"
    beta_value = f"{beta} owns memory alignment {suffix}"
    return [
        _case_payload(case_id=f"{suffix}-alpha", query=f"Fact: {alpha_value}.", answer_mode="transparent", category="fact", expected_answer_keywords=[alpha], expected_memory_values=[alpha_value], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"ambiguous.alpha.{variant}", "value": alpha_value, "anchors": [alpha, "path search"], "relation": "owns"}]}),
        _case_payload(case_id=f"{suffix}-beta", query=f"Fact: {beta_value}.", answer_mode="transparent", category="fact", expected_answer_keywords=[beta], expected_memory_values=[beta_value], metadata={"session_group": suffix, "replacement_memory_records": [{"category": "fact", "slot": f"ambiguous.beta.{variant}", "value": beta_value, "anchors": [beta, "memory alignment"], "relation": "owns"}]}),
        _case_payload(case_id=f"{suffix}-query", query="When I ask what it owns, resolve the one focused on path search.", answer_mode="transparent", category="summary", expected_answer_keywords=["path search", alpha], expected_memory_values=[alpha_value], expected_absent_values=[beta_value], metadata={"session_group": suffix, "entity_isolation_targets": [alpha], "entity_isolation_absent": [beta]}),
    ]


def _science_math_emotion_family(variant: int) -> List[EvalCase]:
    suffix = f"scimathemo-{variant}"
    science_value = f"resistance heating drives battery temperature upward {suffix}"
    math_value = f"battery temperature rose from 58C to 64C and safe threshold is 60C {suffix}"
    emotion_value = f"user is worried and needs a calm but direct explanation {suffix}"
    return [
        _case_payload(
            case_id=f"{suffix}-science-seed",
            query=f"Science fact: {science_value}.",
            answer_mode="transparent",
            category="fact",
            expected_answer_keywords=["resistance heating", "battery temperature"],
            expected_memory_values=[science_value],
            metadata={
                "session_group": suffix,
                "replacement_memory_records": [
                    {
                        "category": "fact",
                        "slot": f"fact.science.battery.{variant}",
                        "value": science_value,
                        "anchors": ["resistance heating", "battery temperature"],
                        "relation": "explains",
                    }
                ],
            },
        ),
        _case_payload(
            case_id=f"{suffix}-math-seed",
            query=f"Math fact: {math_value}.",
            answer_mode="transparent",
            category="fact",
            expected_answer_keywords=["64C", "60C"],
            expected_memory_values=[math_value],
            metadata={
                "session_group": suffix,
                "replacement_memory_records": [
                    {
                        "category": "fact",
                        "slot": f"fact.math.battery.{variant}",
                        "value": math_value,
                        "anchors": ["64C", "60C", "safe threshold"],
                        "relation": "measures",
                    }
                ],
            },
        ),
        _case_payload(
            case_id=f"{suffix}-emotion-seed",
            query=f"Preference update: {emotion_value}.",
            answer_mode="transparent",
            category="preference",
            expected_answer_keywords=["calm", "direct", "worried"],
            expected_memory_values=[emotion_value],
            metadata={
                "session_group": suffix,
                "replacement_memory_records": [
                    {
                        "category": "preference",
                        "slot": f"preference.user_emotion_style.{variant}",
                        "value": emotion_value,
                        "anchors": ["calm", "direct", "worried"],
                        "relation": "prefers",
                    }
                ],
            },
        ),
        _case_payload(
            case_id=f"{suffix}-science-query",
            query="Scientifically, why is the battery temperature rising?",
            answer_mode="transparent",
            category="summary",
            expected_answer_keywords=["resistance heating", "battery temperature"],
            expected_memory_values=[science_value],
            metadata={"session_group": suffix, "reasoning_domains": ["science"]},
        ),
        _case_payload(
            case_id=f"{suffix}-math-query",
            query="Mathematically, does 64C exceed the 60C safe threshold?",
            answer_mode="transparent",
            category="summary",
            expected_answer_keywords=["64C", "60C", "exceed", "unsafe"],
            expected_memory_values=[math_value],
            metadata={"session_group": suffix, "reasoning_domains": ["math"]},
        ),
        _case_payload(
            case_id=f"{suffix}-emotion-query",
            query="How should we answer the worried user?",
            answer_mode="transparent",
            category="summary",
            expected_answer_keywords=["calm", "direct", "reassuring"],
            expected_memory_values=[emotion_value],
            metadata={"session_group": suffix, "reasoning_domains": ["emotion"]},
        ),
        _case_payload(
            case_id=f"{suffix}-science-math-query",
            query="Using the mechanism and the threshold numbers, is the battery currently safe and why?",
            answer_mode="transparent",
            category="summary",
            expected_answer_keywords=["resistance heating", "64C", "60C", "unsafe"],
            expected_memory_values=[science_value, math_value],
            metadata={"session_group": suffix, "reasoning_domains": ["science", "math"]},
        ),
        _case_payload(
            case_id=f"{suffix}-science-emotion-query",
            query="Explain the battery issue in a calm way for the worried user.",
            answer_mode="transparent",
            category="summary",
            expected_answer_keywords=["resistance heating", "calm", "reassuring"],
            expected_memory_values=[science_value, emotion_value],
            metadata={"session_group": suffix, "reasoning_domains": ["science", "emotion"]},
        ),
        _case_payload(
            case_id=f"{suffix}-math-emotion-query",
            query="Tell the worried user what the 64C versus 60C comparison means.",
            answer_mode="transparent",
            category="summary",
            expected_answer_keywords=["64C", "60C", "unsafe", "calm"],
            expected_memory_values=[math_value, emotion_value],
            metadata={"session_group": suffix, "reasoning_domains": ["math", "emotion"]},
        ),
        _case_payload(
            case_id=f"{suffix}-all-query",
            query="Combine the scientific cause, the threshold comparison, and the user's emotion into one final answer.",
            answer_mode="transparent",
            category="summary",
            expected_answer_keywords=["resistance heating", "64C", "60C", "unsafe", "calm"],
            expected_memory_values=[science_value, math_value, emotion_value],
            metadata={"session_group": suffix, "reasoning_domains": ["science", "math", "emotion"]},
        ),
    ]


def _generated_eval_cases_v2() -> List[EvalCase]:
    cases: List[EvalCase] = []
    families = (
        _goal_family,
        _term_continuity_family,
        _term_redefinition_family,
        _overwrite_family,
        _delayed_constraint_family,
        _history_conflict_family,
        _path_family,
        _alias_version_family,
        _multi_entity_family,
        _multi_path_reasoning_family,
        _constrained_path_family,
        _counterfactual_family,
        _multi_source_merge_family,
        _causal_chain_family,
        _cross_level_reasoning_family,
        _non_intuitive_route_family,
        _multi_branch_search_family,
        _missing_info_reasoning_family,
        _ambiguous_reasoning_family,
        _science_math_emotion_family,
        _chinese_state_tracking_family,
        _chinese_overwrite_family,
        _chinese_terminology_family,
    )
    for variant in range(1, 6):
        for family in families:
            cases.extend(family(variant))
    return cases


def load_eval_cases(path: str | Path | None = None) -> List[EvalCase]:
    cases: List[EvalCase] = []
    if path is not None:
        case_path = Path(path)
        if case_path.exists():
            with case_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    cases.append(_to_case(json.loads(line)))
    if not cases:
        cases = default_eval_cases()
    merged: Dict[str, EvalCase] = {case.case_id: case for case in cases}
    for case in _generated_eval_cases_v2():
        merged[case.case_id] = case
    return list(merged.values())


def _scenario_profile_for_case(case_id: str) -> str:
    prefix = str(case_id).split("-", 1)[0]
    mapping = {
        "foundation": "transparent_foundation",
        "termkeep": "terminology_continuity",
        "termredef": "terminology_redefinition",
        "overwrite": "constraint_preference_overwrite",
        "delayed": "delayed_multi_constraint",
        "history": "conflict_history_trace",
        "path": "multi_hop_path_consistency",
        "alias": "alias_version_stage_updates",
        "entity": "multi_entity_separation",
        "multisource": "multi_source_merge",
        "multipath": "multi_path_reasoning",
        "constraintpath": "constrained_path_reasoning",
        "counterfactual": "counterfactual_reasoning",
        "causal4": "long_causal_chain_4plus",
        "crosslevel": "cross_level_reasoning",
        "nonintuitive": "non_intuitive_route_discovery",
        "multibranch": "multi_branch_search",
        "missingreason": "missing_info_reasoning",
        "ambiguousreason": "ambiguous_query_reasoning",
        "scimathemo": "science_math_emotion_combo_reasoning",
        "zhstate": "chinese_state_tracking",
        "zhoverwrite": "chinese_overwrite_history",
        "zhterm": "chinese_terminology_reference",
    }
    return mapping.get(prefix, "")


def _case_phase(case: EvalCase) -> str:
    category = str(case.category or "")
    if category in {"goal", "constraint", "preference", "terminology", "stage_state", "fact"}:
        return "seed"
    if category == "history_query":
        return "history"
    if "path" in category:
        return "path"
    return "followup"


async def run_static_ab_benchmark(
    *,
    cases: Sequence[EvalCase],
    reasoner_factories: Dict[str, ReasonerFactory],
    memory_factories: Dict[str, MemoryFactory],
    config: BenchmarkConfig,
) -> Dict[str, Any]:
    selected_profiles = set(config.scenario_profiles or [item.profile_id for item in default_scenario_profiles()])
    filtered_cases = [case for case in cases if not selected_profiles or not _scenario_profile_for_case(case.case_id) or _scenario_profile_for_case(case.case_id) in selected_profiles]
    filtered_cases = [case for case in filtered_cases if _case_matches_filters(case, config)]
    selected_cases = list(filtered_cases[: config.static_case_limit]) if config.static_case_limit > 0 else list(filtered_cases)
    combo_specs = [
        (reasoner_name, reasoner_factory, memory_name, memory_factory)
        for reasoner_name, reasoner_factory in reasoner_factories.items()
        for memory_name, memory_factory in memory_factories.items()
    ]
    resolved_parallelism, parallelism_meta = _resolve_static_parallelism(config)
    parallelism = min(max(1, int(resolved_parallelism or 1)), max(1, len(combo_specs)))
    if parallelism > 1 and len(combo_specs) > 1 and selected_cases:
        total_units = len(combo_specs) * len(selected_cases)
        _emit_progress(config, benchmark="static_ab", completed=0, total=total_units, status="running", static_parallelism=parallelism, static_parallelism_meta=parallelism_meta)
        combo_progress: Dict[str, int] = {f"{reasoner_name}::{memory_name}": 0 for reasoner_name, _, memory_name, _ in combo_specs}
        combo_meta: Dict[str, Dict[str, str]] = {
            f"{reasoner_name}::{memory_name}": {"reasoner": reasoner_name, "memory": memory_name, "case_id": "", "category": ""}
            for reasoner_name, _, memory_name, _ in combo_specs
        }

        def _make_combo_progress_callback(combo_key: str) -> Callable[[Dict[str, Any]], None]:
            def _callback(event: Dict[str, Any]) -> None:
                combo_progress[combo_key] = max(combo_progress.get(combo_key, 0), int(event.get("completed", 0) or 0))
                combo_meta[combo_key] = {
                    "reasoner": str(event.get("reasoner", combo_meta[combo_key].get("reasoner", "")) or combo_meta[combo_key].get("reasoner", "")),
                    "memory": str(event.get("memory", combo_meta[combo_key].get("memory", "")) or combo_meta[combo_key].get("memory", "")),
                    "case_id": str(event.get("case_id", combo_meta[combo_key].get("case_id", "")) or combo_meta[combo_key].get("case_id", "")),
                    "category": str(event.get("category", combo_meta[combo_key].get("category", "")) or combo_meta[combo_key].get("category", "")),
                }
                completed_units = sum(combo_progress.values())
                _emit_progress(
                    config,
                    benchmark="static_ab",
                    completed=completed_units,
                    total=total_units,
                    status="running" if completed_units < total_units else "completed",
                    reasoner=combo_meta[combo_key]["reasoner"],
                    memory=combo_meta[combo_key]["memory"],
                    case_id=combo_meta[combo_key]["case_id"],
                    category=combo_meta[combo_key]["category"],
                    static_parallelism=parallelism,
                    static_parallelism_meta=parallelism_meta,
                )

            return _callback

        semaphore = asyncio.Semaphore(parallelism)

        async def _run_combo(
            reasoner_name: str,
            reasoner_factory: ReasonerFactory,
            memory_name: str,
            memory_factory: MemoryFactory,
        ) -> Dict[str, Any]:
            combo_key = f"{reasoner_name}::{memory_name}"
            combo_config = replace(
                config,
                static_parallelism=1,
                progress_callback=_make_combo_progress_callback(combo_key),
            )
            async with semaphore:
                return await run_static_ab_benchmark(
                    cases=selected_cases,
                    reasoner_factories={reasoner_name: reasoner_factory},
                    memory_factories={memory_name: memory_factory},
                    config=combo_config,
                )

        combo_results = await asyncio.gather(
            *[
                _run_combo(reasoner_name, reasoner_factory, memory_name, memory_factory)
                for reasoner_name, reasoner_factory, memory_name, memory_factory in combo_specs
            ]
        )
        results: Dict[str, Any] = {"cases": [], "summary": [], "subsets": [], "failures": []}
        for combo_result in combo_results:
            results["cases"].extend(list(combo_result.get("cases", []) or []))
            results["summary"].extend(list(combo_result.get("summary", []) or []))
            results["subsets"].extend(list(combo_result.get("subsets", []) or []))
            results["failures"].extend(list(combo_result.get("failures", []) or []))
        results["runtime"] = {"static_parallelism": parallelism, **parallelism_meta}
        results["cases"].sort(key=lambda item: (str(item.get("reasoner", "")), str(item.get("memory", "")), str(item.get("case_id", ""))))
        results["summary"].sort(key=lambda item: (-float(item["avg_reasoning_quality_score"]), -float(item["slot_head_accuracy"]), float(item["avg_latency_seconds"])))
        results["subsets"].sort(key=lambda item: (str(item.get("reasoner", "")), str(item.get("memory", "")), str(item.get("subset", ""))))
        results["failures"].sort(key=lambda item: (str(item.get("reasoner", "")), str(item.get("memory", "")), str(item.get("case_id", "")), str(item.get("reason", ""))))
        _emit_progress(config, benchmark="static_ab", completed=total_units, total=total_units, status="completed", static_parallelism=parallelism, static_parallelism_meta=parallelism_meta)
        return results

    failures: List[FailureRecord] = []
    results: Dict[str, Any] = {"cases": [], "summary": [], "subsets": [], "failures": [], "runtime": {"static_parallelism": parallelism, **parallelism_meta}}
    total_units = len(reasoner_factories) * len(memory_factories) * len(selected_cases)
    completed_units = 0
    _emit_progress(config, benchmark="static_ab", completed=0, total=total_units, status="running", static_parallelism=parallelism, static_parallelism_meta=parallelism_meta)
    for reasoner_name, reasoner_factory in reasoner_factories.items():
        for memory_name, memory_factory in memory_factories.items():
            reasoner = reasoner_factory()
            memory = memory_factory()
            combo_records: List[Dict[str, Any]] = []
            current_group = None
            for case in selected_cases:
                session_group = case.metadata.get("session_group")
                if current_group is None or current_group != session_group:
                    memory.reset()
                    current_group = session_group
                preload_records = list(case.metadata.get("replacement_memory_records", []) or [])
                if preload_records:
                    memory.ingest_turn(
                        case.query,
                        "",
                        answer_payload={"replacement_memory_records": preload_records},
                        extraction_result={},
                    )
                response = await reasoner.answer(case.query, answer_mode=case.answer_mode or config.answer_mode, memory_adapter=memory)
                answer_payload = response.to_dict()
                if preload_records:
                    answer_payload = {**answer_payload, "replacement_memory_records": preload_records}
                else:
                    memory.ingest_turn(case.query, response.answer, answer_payload=answer_payload, extraction_result=dict(response.metadata.get("extraction") or {}))
                response_dict = response.to_dict()
                token_usage = response_token_usage(response_dict)
                completed_units += 1
                _emit_progress(
                    config,
                    benchmark="static_ab",
                    completed=completed_units,
                    total=total_units,
                    status="running",
                    reasoner=reasoner_name,
                    memory=memory_name,
                    case_id=case.case_id,
                    category=str(case.category or ""),
                    static_parallelism=parallelism,
                    static_parallelism_meta=parallelism_meta,
                )
                reasoning_trace = dict((dict(response.trace or {}).get("tmcra_reasoning_v2", {}) or {}))
                judge_trace = dict(reasoning_trace.get("judge_trace", {}) or {})
                slot_resolution_trace = dict((dict(reasoning_trace.get("slot_resolution", {}) or {}).get("resolution_trace", {}) or {}))
                judge_decision = dict(judge_trace.get("decision", {}) or {})
                judge_triggered = bool(judge_trace.get("triggered", False))
                judge_applied = bool(slot_resolution_trace.get("judge_applied", False))
                judge_effective = bool(reasoning_trace.get("judge_effective", False))
                judge_decision_valid = bool(judge_decision.get("decision_valid", False))
                path_preview_summary = dict(reasoning_trace.get("path_preview_summary", {}) or {})
                path_realization = dict(reasoning_trace.get("path_realization", {}) or {})
                selected_slots_before = list(slot_resolution_trace.get("selected_slots_before", []) or slot_resolution_trace.get("baseline_selected_slots", []) or [])
                selected_slots_after = list(slot_resolution_trace.get("selected_slots_after", []) or slot_resolution_trace.get("selected_slots", []) or [])
                realized_claim_slots = list(slot_resolution_trace.get("realized_claim_slots", []) or [])
                realized_view_set = list(slot_resolution_trace.get("realized_view_set", []) or [])
                coverage_before = len(selected_slots_before)
                coverage_after = len(selected_slots_after)
                realized_coverage = len(realized_claim_slots)
                claim_types = {
                    str(item.get("claim_type", ""))
                    for item in list(reasoning_trace.get("claims", []) or [])
                    if isinstance(item, dict)
                }
                normalized_query = _normalize_text(case.query)
                compare_query = bool(
                    case.category == "history_query"
                    and (
                        "compare" in normalized_query
                        or ("previous" in normalized_query and "current" in normalized_query)
                        or "对比" in case.query
                    )
                )
                timeline_query = bool(
                    case.category == "history_query"
                    and (
                        "timeline" in normalized_query
                        or "change over time" in normalized_query
                        or "how did" in normalized_query
                        or "变化过程" in case.query
                    )
                )
                compare_query = bool(
                    "compare" in normalized_query
                    or ("previous" in normalized_query and "current" in normalized_query)
                    or ("active" in normalized_query and "historical" in normalized_query)
                    or "对比" in case.query
                    or ("当前" in case.query and "历史" in case.query)
                )
                timeline_query = bool(
                    "timeline" in normalized_query
                    or "change over time" in normalized_query
                    or "how did" in normalized_query
                    or "history of changes" in normalized_query
                    or "state evolution" in normalized_query
                    or "变化过程" in case.query
                    or "演变过程" in case.query
                    or "状态演化" in case.query
                )
                memory_text = _memory_hit_text(response.memory_hits)
                evidence_text = _evidence_text(response_dict)
                haystack = _normalize_text(response.answer) + "\n" + memory_text + "\n" + evidence_text
                answer_match = _match_ratio(response.answer, case.expected_answer_keywords or case.expected_keywords)
                memory_correctness = _present_ratio(haystack, case.expected_memory_values)
                overwrite_resolution = max(0.0, 1.0 - _violation_ratio(haystack, case.expected_absent_values))
                stale_rate = _violation_ratio(haystack, case.expected_absent_values)
                false_rate = _violation_ratio(haystack, case.metadata.get("false_values", []) or [])
                unsupported_rate = 1.0 if response.unsupported_claims else 0.0
                verbalization_gap = max(0.0, memory_correctness - answer_match)
                path_fact_coverage = _match_ratio(evidence_text, case.expected_path_concepts) if "path" in case.category else 0.0
                path_semantic_realization_accuracy = _path_semantic_realization_accuracy(case, response_dict, reasoning_trace) if "path" in case.category else 0.0
                path_protocol_accuracy = path_semantic_realization_accuracy if "path" in case.category else 0.0
                entity_targets = list(case.metadata.get("entity_isolation_targets", []) or [])
                entity_absent = list(case.metadata.get("entity_isolation_absent", []) or [])
                entity_isolation_accuracy = (
                    (_present_ratio(haystack, entity_targets) + max(0.0, 1.0 - _violation_ratio(haystack, entity_absent))) / 2.0
                    if (entity_targets or entity_absent)
                    else 0.0
                )
                temporal_consistency_score = (
                    (memory_correctness + overwrite_resolution) / 2.0
                    if case.category == "history_query" or bool(case.expected_absent_values)
                    else 0.0
                )
                expected_count = len([item for item in case.expected_memory_values if _normalize_text(item)])
                present_count = _match_count(haystack, case.expected_memory_values)
                violation_count = _match_count(haystack, [*(case.expected_absent_values or []), *((case.metadata.get("false_values", []) or []))])
                retrieval_precision = (
                    present_count / max(1, present_count + violation_count)
                    if expected_count or violation_count
                    else 1.0
                )
                retrieval_recall = (present_count / max(1, expected_count)) if expected_count else 1.0
                critical_nodes = list(case.metadata.get("critical_nodes", []) or [])
                critical_node_hit_rate = _match_ratio(evidence_text, critical_nodes) if critical_nodes else 0.0
                alternative_path_sets = list(case.metadata.get("alternative_path_sets", []) or [])
                reasoning_domains = [_normalize_text(item) for item in list(case.metadata.get("reasoning_domains", []) or []) if _normalize_text(item)]
                multi_path_coverage = (
                    sum(1 for expected_path in alternative_path_sets if any(_path_contains_concepts(path, expected_path) for path in response.paths)) / max(1, len(alternative_path_sets))
                    if alternative_path_sets
                    else 0.0
                )
                path_consistency_score = _path_edge_consistency(response_dict) if "path" in case.category else 0.0
                intent_misclassified = bool(
                    (
                        compare_query
                        and _normalize_text(dict(reasoning_trace.get("intent", {}) or {}).get("history_kind", "")) == "previous"
                    )
                    or (
                        "threshold" in normalized_query
                        and _normalize_text(dict(reasoning_trace.get("intent", {}) or {}).get("history_kind", "")) == "previous"
                    )
                )
                overwrite_trace_mismatch = bool(
                    overwrite_resolution < 0.999
                    and _metric_at_least(answer_match)
                    and _metric_at_least(memory_correctness)
                    and _metric_at_least(1.0 if response.evidence_consistent and not response.unsupported_claims else 0.0)
                    and unsupported_rate <= 0.0
                )
                overwrite_semantic_failure = bool(overwrite_resolution < 0.999 and not overwrite_trace_mismatch)
                judge_trace_not_realized = bool(
                    judge_applied
                    and judge_decision_valid
                    and (
                        (coverage_after > 0 and realized_coverage < coverage_after)
                        or (
                            (compare_query and "slot_compare" not in claim_types)
                            or (timeline_query and "timeline_summary" not in claim_types)
                            or ("path" in case.category and judge_decision.get("selected_path_indices") and not list(path_realization.get("selected_path_refs", []) or []) and not response.paths)
                        )
                    )
                )
                semantic_visible_success = bool(
                    _metric_at_least(answer_match)
                    and _metric_at_least(memory_correctness)
                    and _metric_at_least(1.0 if response.evidence_consistent and not response.unsupported_claims else 0.0)
                    and unsupported_rate <= 0.0
                    and not overwrite_semantic_failure
                    and ("path" not in case.category or _metric_at_least(path_semantic_realization_accuracy))
                )
                judge_semantic_not_realized = bool(judge_trace_not_realized and not semantic_visible_success)
                path_semantic_not_realized = bool(
                    "path" in case.category and judge_semantic_not_realized and not _metric_at_least(path_semantic_realization_accuracy)
                )
                record = {
                    "reasoner": reasoner_name,
                    "memory": memory_name,
                    "case_id": case.case_id,
                    "category": case.category,
                    "phase": _case_phase(case),
                    "answer": response.answer,
                    "latency_seconds": response.latency_seconds,
                    "answer_match": answer_match,
                    "keyword_match": _match_ratio(response.answer, case.expected_keywords or case.expected_answer_keywords),
                    "memory_match": _present_ratio(_memory_hit_text(response.memory_hits), case.expected_memory_values),
                    "memory_correctness": memory_correctness,
                    "overwrite_resolution": overwrite_resolution,
                    "stale_recall_rate": stale_rate,
                    "false_recall_rate": false_rate,
                    "fact_match": _match_ratio(evidence_text, [*case.expected_fact_phrases, *case.expected_path_concepts]),
                    "evidence_consistency": 1.0 if response.evidence_consistent and not response.unsupported_claims else 0.0,
                    "unsupported_claim_rate": unsupported_rate,
                    "verbalization_gap": verbalization_gap,
                    "history_query_accuracy": answer_match if case.category == "history_query" else 0.0,
                    "judge_trigger_rate": 1.0 if judge_triggered else 0.0,
                    "judge_applied_rate": 1.0 if judge_applied else 0.0,
                    "judge_decision_valid_rate": 1.0 if judge_decision_valid else 0.0,
                    "judge_effective_apply_rate": 1.0 if judge_effective else 0.0,
                    "compare_realization_accuracy": 1.0 if compare_query and "slot_compare" in claim_types else 0.0,
                    "timeline_realization_accuracy": 1.0 if timeline_query and "timeline_summary" in claim_types else 0.0,
                    "summary_slot_coverage": min(1.0, realized_coverage / max(1, coverage_before)) if case.category == "summary" and coverage_before else (1.0 if case.category == "summary" and not coverage_before else 0.0),
                    "summary_realized_coverage": min(1.0, realized_coverage / max(1, coverage_after)) if case.category == "summary" and coverage_after else (1.0 if case.category == "summary" and not coverage_after else 0.0),
                    "history_slot_coverage": min(1.0, realized_coverage / max(1, coverage_before)) if case.category == "history_query" and coverage_before else (1.0 if case.category == "history_query" and not coverage_before else 0.0),
                    "coverage_drop_rate": max(0.0, (coverage_before - coverage_after) / max(1, coverage_before)) if coverage_before else 0.0,
                    "judge_under_selection_rate": 1.0 if judge_applied and coverage_before and coverage_after < coverage_before else 0.0,
                    "judge_trace_not_realized_rate": 1.0 if judge_trace_not_realized else 0.0,
                    "judge_semantic_not_realized_rate": 1.0 if judge_semantic_not_realized else 0.0,
                    "judge_selected_but_not_realized_rate": 1.0 if judge_trace_not_realized else 0.0,
                    "overwrite_trace_mismatch_rate": 1.0 if overwrite_trace_mismatch else 0.0,
                    "path_rerank_gain": path_fact_coverage if ("path" in case.category and judge_effective) else 0.0,
                    "path_composition_accuracy": path_fact_coverage,
                    "path_protocol_accuracy": path_protocol_accuracy,
                    "path_semantic_realization_accuracy": path_semantic_realization_accuracy,
                    "path_consistency_score": path_consistency_score,
                    "multi_path_coverage": multi_path_coverage,
                    "critical_node_hit_rate": critical_node_hit_rate,
                    "contradiction_resolution": overwrite_resolution if case.expected_absent_values else 1.0,
                    "slot_head_accuracy": memory_correctness if case.category in {"goal", "constraint", "preference", "terminology", "stage_state"} else 0.0,
                    "entity_isolation_accuracy": entity_isolation_accuracy,
                    "temporal_consistency_score": temporal_consistency_score,
                    "retrieval_precision": retrieval_precision,
                    "retrieval_recall": retrieval_recall,
                    "has_entity_isolation_case": bool(entity_targets or entity_absent),
                    "has_temporal_case": bool(case.category == "history_query" or case.expected_absent_values),
                    "has_compare_case": compare_query,
                    "has_timeline_case": timeline_query,
                    "has_path_case": bool("path" in case.category),
                    "path_candidate_empty": bool("path" in case.category and int(path_preview_summary.get("count", 0) or 0) == 0),
                    "path_candidate_empty_reason": str(path_preview_summary.get("empty_reason", "") or ""),
                    "intent_misclassified": intent_misclassified,
                    "judge_trace_not_realized": judge_trace_not_realized,
                    "judge_semantic_not_realized": judge_semantic_not_realized,
                    "judge_selected_but_not_realized": judge_trace_not_realized,
                    "overwrite_trace_mismatch": overwrite_trace_mismatch,
                    "overwrite_semantic_failure": overwrite_semantic_failure,
                    "path_semantic_not_realized": path_semantic_not_realized,
                    "path_semantic_realized": bool(path_realization.get("path_semantic_realized", False)),
                    "realized_view_set": list(realized_view_set),
                    "has_critical_node_case": bool(critical_nodes),
                    "has_multi_path_case": bool(alternative_path_sets),
                    "reasoning_domains": list(reasoning_domains),
                    "has_science_reasoning_case": "science" in reasoning_domains,
                    "has_math_reasoning_case": "math" in reasoning_domains,
                    "has_emotion_reasoning_case": "emotion" in reasoning_domains,
                    "has_mixed_domain_reasoning_case": len(reasoning_domains) >= 2,
                    "has_full_domain_combo_case": len(reasoning_domains) == 3,
                    "storage_bytes": memory.storage_bytes(),
                    **token_usage,
                    "response": response_dict,
                }
                reasoning_components = [float(record["answer_match"]), float(record["evidence_consistency"])]
                if case.category == "history_query":
                    reasoning_components.append(float(record["history_query_accuracy"]))
                if "path" in str(case.category or ""):
                    reasoning_components.extend(
                        [
                            float(record["path_composition_accuracy"]),
                            float(record["path_protocol_accuracy"]),
                            float(record["path_consistency_score"]),
                        ]
                    )
                if alternative_path_sets:
                    reasoning_components.append(float(record["multi_path_coverage"]))
                record["reasoning_quality_score"] = round(sum(reasoning_components) / max(1, len(reasoning_components)), 6)
                combo_records.append(record)
                failure_reason = ""
                if record["intent_misclassified"]:
                    failure_reason = "intent_misclassified"
                elif record["path_candidate_empty"]:
                    failure_reason = "path_candidate_empty"
                elif record["path_semantic_not_realized"]:
                    failure_reason = "path_semantic_not_realized"
                elif record["unsupported_claim_rate"] > 0.0:
                    failure_reason = "unsupported_claim"
                elif record["overwrite_semantic_failure"]:
                    failure_reason = "overwrite_semantic_failure"
                elif record["memory_correctness"] < 0.999 and case.expected_memory_values:
                    failure_reason = "retrieval_failure"
                elif record["answer_match"] + 1e-9 < record["memory_correctness"]:
                    failure_reason = "grounded_but_poorly_realized"
                elif record["answer_match"] < 0.999 or record["evidence_consistency"] < 0.999:
                    failure_reason = "evidence_failure"
                if failure_reason:
                    details = {
                        key: record[key]
                        for key in (
                            "answer_match",
                            "memory_correctness",
                            "overwrite_resolution",
                            "evidence_consistency",
                            "unsupported_claim_rate",
                            "verbalization_gap",
                            "judge_trace_not_realized",
                            "judge_semantic_not_realized",
                            "overwrite_trace_mismatch",
                            "path_semantic_realization_accuracy",
                        )
                    }
                    details["severity"] = "main"
                    failures.append(FailureRecord(benchmark="static", reasoner=reasoner_name, memory=memory_name, case_id=case.case_id, reason=failure_reason, details=details))
                for audit_reason in (
                    "judge_trace_not_realized" if record["judge_trace_not_realized"] else "",
                    "overwrite_trace_mismatch" if record["overwrite_trace_mismatch"] else "",
                ):
                    if not audit_reason:
                        continue
                    audit_details = {
                        key: record[key]
                        for key in (
                            "answer_match",
                            "memory_correctness",
                            "overwrite_resolution",
                            "evidence_consistency",
                            "unsupported_claim_rate",
                            "verbalization_gap",
                            "judge_trace_not_realized",
                            "judge_semantic_not_realized",
                            "overwrite_trace_mismatch",
                            "path_semantic_realization_accuracy",
                        )
                    }
                    audit_details["severity"] = "audit"
                    failures.append(FailureRecord(benchmark="static", reasoner=reasoner_name, memory=memory_name, case_id=case.case_id, reason=audit_reason, details=audit_details))
            avg = lambda key: sum(float(item.get(key, 0.0) or 0.0) for item in combo_records) / max(1, len(combo_records))
            seed_cases = [item for item in combo_records if item["phase"] == "seed"]
            followup_cases = [item for item in combo_records if item["phase"] == "followup"]
            summary_cases = [item for item in combo_records if item["category"] == "summary"]
            history_cases = [item for item in combo_records if item["category"] == "history_query"]
            compare_case_count = sum(1 for item in combo_records if bool(item.get("has_compare_case")))
            timeline_case_count = sum(1 for item in combo_records if bool(item.get("has_timeline_case")))
            results["summary"].append({
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
                "llm_prompt_tokens": int(sum(int(item.get("llm_prompt_tokens", 0) or 0) for item in combo_records)),
                "llm_completion_tokens": int(sum(int(item.get("llm_completion_tokens", 0) or 0) for item in combo_records)),
                "llm_total_tokens": int(sum(int(item.get("llm_total_tokens", 0) or 0) for item in combo_records)),
                "judge_prompt_tokens": int(sum(int(item.get("judge_prompt_tokens", 0) or 0) for item in combo_records)),
                "judge_completion_tokens": int(sum(int(item.get("judge_completion_tokens", 0) or 0) for item in combo_records)),
                "judge_total_tokens": int(sum(int(item.get("judge_total_tokens", 0) or 0) for item in combo_records)),
                "combined_prompt_tokens": int(sum(int(item.get("combined_prompt_tokens", 0) or 0) for item in combo_records)),
                "combined_completion_tokens": int(sum(int(item.get("combined_completion_tokens", 0) or 0) for item in combo_records)),
                "combined_total_tokens": int(sum(int(item.get("combined_total_tokens", 0) or 0) for item in combo_records)),
                "avg_llm_total_tokens": round(avg("llm_total_tokens"), 6),
                "avg_judge_total_tokens": round(avg("judge_total_tokens"), 6),
                "avg_combined_total_tokens": round(avg("combined_total_tokens"), 6),
                "evidence_consistency_rate": round(avg("evidence_consistency"), 6),
                "seed_capture_rate": round(sum(item["memory_correctness"] for item in seed_cases) / max(1, len(seed_cases)), 6),
                "followup_recall_rate": round(sum(item["memory_correctness"] for item in followup_cases) / max(1, len(followup_cases)), 6),
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
                "compare_realization_accuracy": round(_avg_metric(combo_records, "compare_realization_accuracy", predicate=lambda item: bool(item.get("has_compare_case"))), 6) if compare_case_count else None,
                "timeline_realization_accuracy": round(_avg_metric(combo_records, "timeline_realization_accuracy", predicate=lambda item: bool(item.get("has_timeline_case"))), 6) if timeline_case_count else None,
                "path_rerank_gain": round(_avg_metric(combo_records, "path_rerank_gain", predicate=lambda item: bool(item.get("has_path_case"))), 6),
                "path_composition_accuracy": round(avg("path_composition_accuracy"), 6),
                "path_protocol_accuracy": round(avg("path_protocol_accuracy"), 6),
                "path_semantic_realization_accuracy": round(_avg_metric(combo_records, "path_semantic_realization_accuracy", predicate=lambda item: bool(item.get("has_path_case"))), 6),
                "path_consistency_score": round(_avg_metric(combo_records, "path_consistency_score", predicate=lambda item: "path" in str(item.get("category", ""))), 6),
                "critical_node_hit_rate": round(_avg_metric(combo_records, "critical_node_hit_rate", predicate=lambda item: bool(item.get("has_critical_node_case"))), 6),
                "multi_path_coverage": round(_avg_metric(combo_records, "multi_path_coverage", predicate=lambda item: bool(item.get("has_multi_path_case"))), 6),
                "science_reasoning_score": round(_avg_metric(combo_records, "reasoning_quality_score", predicate=lambda item: bool(item.get("has_science_reasoning_case"))), 6),
                "math_reasoning_score": round(_avg_metric(combo_records, "reasoning_quality_score", predicate=lambda item: bool(item.get("has_math_reasoning_case"))), 6),
                "emotion_reasoning_score": round(_avg_metric(combo_records, "reasoning_quality_score", predicate=lambda item: bool(item.get("has_emotion_reasoning_case"))), 6),
                "mixed_domain_reasoning_score": round(_avg_metric(combo_records, "reasoning_quality_score", predicate=lambda item: bool(item.get("has_mixed_domain_reasoning_case"))), 6),
                "science_math_emotion_combo_score": round(_avg_metric(combo_records, "reasoning_quality_score", predicate=lambda item: bool(item.get("has_full_domain_combo_case"))), 6),
                "contradiction_resolution": round(avg("contradiction_resolution"), 6),
                "entity_isolation_accuracy": round(_avg_metric(combo_records, "entity_isolation_accuracy", predicate=lambda item: bool(item.get("has_entity_isolation_case"))), 6),
                "temporal_consistency_score": round(_avg_metric(combo_records, "temporal_consistency_score", predicate=lambda item: bool(item.get("has_temporal_case"))), 6),
                "retrieval_precision": round(_avg_metric(combo_records, "retrieval_precision"), 6),
                "retrieval_recall": round(_avg_metric(combo_records, "retrieval_recall"), 6),
                "avg_verbalization_gap": round(avg("verbalization_gap"), 6),
                "unsupported_claim_rate": round(avg("unsupported_claim_rate"), 6),
                "Memory Correctness": round(avg("memory_correctness"), 6),
                "Overwrite Accuracy": round(avg("overwrite_resolution"), 6),
                "Conflict Resolution Accuracy": round(avg("contradiction_resolution"), 6),
                "False Recall Rate": round(avg("false_recall_rate"), 6),
                "Stale Recall Rate": round(avg("stale_recall_rate"), 6),
                "Entity Isolation Accuracy": round(_avg_metric(combo_records, "entity_isolation_accuracy", predicate=lambda item: bool(item.get("has_entity_isolation_case"))), 6),
                "Temporal Consistency Score": round(_avg_metric(combo_records, "temporal_consistency_score", predicate=lambda item: bool(item.get("has_temporal_case"))), 6),
                "Retrieval Precision": round(_avg_metric(combo_records, "retrieval_precision"), 6),
                "Retrieval Recall": round(_avg_metric(combo_records, "retrieval_recall"), 6),
                "Path Completeness Score": round(avg("path_composition_accuracy"), 6),
                "Path Consistency Score": round(_avg_metric(combo_records, "path_consistency_score", predicate=lambda item: "path" in str(item.get("category", ""))), 6),
                "Multi-path Coverage": round(_avg_metric(combo_records, "multi_path_coverage", predicate=lambda item: bool(item.get("has_multi_path_case"))), 6),
                "Critical Node Hit Rate": round(_avg_metric(combo_records, "critical_node_hit_rate", predicate=lambda item: bool(item.get("has_critical_node_case"))), 6),
                "Evidence Consistency": round(avg("evidence_consistency"), 6),
                "Unsupported Claim Rate": round(avg("unsupported_claim_rate"), 6),
                "Verbalization Gap": round(avg("verbalization_gap"), 6),
                "Latency (ms)": round(avg("latency_seconds") * 1000.0, 6),
            })
            for subset_name, predicate in (
                ("summary", lambda item: str(item.get("category", "")) == "summary"),
                ("history", lambda item: str(item.get("category", "")) == "history_query"),
                ("path", lambda item: "path" in str(item.get("category", ""))),
            ):
                subset_records = [item for item in combo_records if predicate(item)]
                if not subset_records:
                    continue
                results["subsets"].append(
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
            results["cases"].extend(combo_records)
    results["summary"].sort(key=lambda item: (-float(item["avg_reasoning_quality_score"]), -float(item["slot_head_accuracy"]), float(item["avg_latency_seconds"])))
    results["failures"] = [item.to_dict() for item in failures]
    _emit_progress(config, benchmark="static_ab", completed=total_units, total=total_units, status="completed", static_parallelism=parallelism, static_parallelism_meta=parallelism_meta)
    return results


def _noise_turn(index: int, profile_id: str, distractors: Sequence[str]) -> str:
    distractor = distractors[index % len(distractors)] if distractors else f"{profile_id}_noise_{index}"
    if str(profile_id).startswith("chinese_"):
        return f"噪声轮次{index}：这是{profile_id}里的群聊闲聊片段；干扰词{distractor}不应成为有效记忆。"
    return f"Noise turn {index}: routine chatter for {profile_id}; distractor token {distractor} should not become active memory."


def _event_turn(statement: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"type": "memory", "statement": statement, "replacement_memory_records": records}


def _profile_events(profile_id: str, length: int) -> Dict[str, Any]:
    suffix = f"{profile_id}-{length}"
    if profile_id == "goal_persistence":
        goal = f"build TMCRA transparent reasoning engine {suffix}"
        return {
            "events": [_event_turn(f"Goal update: {goal}.", [{"category": "goal", "slot": "goal.primary", "value": goal, "anchors": ["TMCRA", "transparent reasoning"], "relation": "session_goal"}])],
            "probes": [LongDialogProbe("goal_current", "goal.primary", "What is the current primary goal?", [goal], [], [f"abandoned-goal-{suffix}"])],
            "distractors": [f"abandoned-goal-{suffix}", f"ghost-goal-{length}"],
        }
    if profile_id == "constraint_overwrite":
        old_value = f"never let an external llm give the final verdict {suffix}"
        new_value = f"teacher llms may assist supervision, but TMCRA evidence owns the final verdict {suffix}"
        return {
            "events": [
                _event_turn(f"Constraint seed: {old_value}.", [{"category": "constraint", "slot": "constraint.teacher_policy", "value": old_value, "anchors": ["external llm"], "relation": "constrained_by"}]),
                _event_turn(f"Constraint overwrite: {new_value}.", [{"category": "constraint", "slot": "constraint.teacher_policy", "value": new_value, "anchors": ["teacher supervision", "TMCRA evidence"], "relation": "constrained_by"}]),
            ],
            "probes": [LongDialogProbe("constraint_current", "constraint.teacher_policy", "What is the current teacher policy?", [new_value], [old_value], [f"free-llm-verdict-{length}"])],
            "distractors": [f"free-llm-verdict-{length}", f"archived-policy-{length}"],
        }
    if profile_id == "preference_shift":
        old_value = f"transparent only mode {suffix}"
        new_value = f"natural default with transparent expansion {suffix}"
        return {
            "events": [
                _event_turn(f"Preference seed: {old_value}.", [{"category": "preference", "slot": "preference.answer_mode", "value": old_value, "anchors": ["transparent"], "relation": "prefers"}]),
                _event_turn(f"Preference overwrite: {new_value}.", [{"category": "preference", "slot": "preference.answer_mode", "value": new_value, "anchors": ["natural", "transparent expansion"], "relation": "prefers"}]),
            ],
            "probes": [LongDialogProbe("preference_current", "preference.answer_mode", "What is the default answer mode now?", [new_value], [old_value], [f"free-chat-only-{length}"])],
            "distractors": [f"free-chat-only-{length}", f"style-drift-{length}"],
        }
    if profile_id == "terminology_redefinition":
        old_value = f"memory bridge means scratch buffer {suffix}"
        new_value = f"memory bridge means session graph memory {suffix}"
        return {
            "events": [
                _event_turn(f"Term seed: {old_value}.", [{"category": "terminology", "slot": "term.memory_bridge", "value": old_value, "anchors": ["memory bridge", "scratch buffer"], "relation": "uses_term"}]),
                _event_turn(f"Term overwrite: {new_value}.", [{"category": "terminology", "slot": "term.memory_bridge", "value": new_value, "anchors": ["memory bridge", "session graph memory"], "relation": "uses_term"}]),
            ],
            "probes": [
                LongDialogProbe("term_current", "term.memory_bridge", "What does memory bridge mean now?", [new_value], [old_value], [f"memory bridge means vector cache {length}"]),
                LongDialogProbe("term_history", "term.memory_bridge", "What did memory bridge mean before?", [old_value], [], []),
            ],
            "distractors": [f"memory bridge means vector cache {length}", f"legacy-term-{length}"],
        }
    if profile_id == "stage_progression":
        old_stage = f"design phase {suffix}"
        new_stage = f"deployment validation phase {suffix}"
        constraint_a = f"keep evidence-first ranking active {suffix}"
        constraint_b = f"keep graph memory inside the evidence loop {suffix}"
        return {
            "events": [
                _event_turn(f"Stage seed: {old_stage}.", [{"category": "stage_state", "slot": "stage.current", "value": old_stage, "anchors": ["design"], "relation": "stage_state"}]),
                _event_turn(f"Constraint seed: {constraint_a}.", [{"category": "constraint", "slot": "constraint.evidence_first", "value": constraint_a, "anchors": ["evidence-first ranking"], "relation": "constrained_by"}]),
                _event_turn(f"Constraint seed: {constraint_b}.", [{"category": "constraint", "slot": "constraint.graph_loop", "value": constraint_b, "anchors": ["graph memory"], "relation": "constrained_by"}]),
                _event_turn(f"Stage overwrite: {new_stage}.", [{"category": "stage_state", "slot": "stage.current", "value": new_stage, "anchors": ["deployment validation"], "relation": "stage_state"}]),
            ],
            "probes": [
                LongDialogProbe("stage_current", "stage.current", "What stage is the system currently in?", [new_stage], [old_stage], [f"free-exploration-{length}"]),
                LongDialogProbe("constraints_current", "constraint.bundle", "Which active constraints still apply right now?", [constraint_a, constraint_b], [], [f"free-exploration-{length}"]),
            ],
            "distractors": [f"free-exploration-{length}", f"hallucinated-bypass-{length}"],
        }
    if profile_id == "delayed_multi_hop_path":
        a = f"user query {suffix}"
        b = f"candidate ranking {suffix}"
        c = f"transparent answer {suffix}"
        d = f"session memory graph {suffix}"
        return {
            "events": [
                _event_turn(f"Path fact: {a} leads to {b}.", [{"category": "goal", "slot": "path.segment_a", "value": f"{a} leads to {b}", "anchors": [a, b], "relation": "path_edge"}]),
                _event_turn(f"Path fact: {b} leads to {c}.", [{"category": "goal", "slot": "path.segment_b", "value": f"{b} leads to {c}", "anchors": [b, c], "relation": "path_edge"}]),
                _event_turn(f"Path fact: {c} leads to {d}.", [{"category": "goal", "slot": "path.segment_c", "value": f"{c} leads to {d}", "anchors": [c, d], "relation": "path_edge"}]),
            ],
            "probes": [LongDialogProbe("path_route", "path.route", f"What path connects {a} to {d}?", [a, b, c, d], [], [f"broken-path-{length}"])],
            "distractors": [f"broken-path-{length}", f"path-noise-{length}"],
        }
    if profile_id == "contradiction_saturation":
        old_value = f"transparent-only output {suffix}"
        new_value = f"natural default with transparent expansion {suffix}"
        return {
            "events": [
                _event_turn(f"Preference seed: {old_value}.", [{"category": "preference", "slot": "preference.answer_mode", "value": old_value, "anchors": ["transparent-only"], "relation": "prefers"}]),
                _event_turn(f"Contradiction: keep {old_value} forever.", []),
                _event_turn(f"Preference overwrite: {new_value}.", [{"category": "preference", "slot": "preference.answer_mode", "value": new_value, "anchors": ["natural", "transparent"], "relation": "prefers"}]),
            ],
            "probes": [LongDialogProbe("contradiction_current", "preference.answer_mode", "Which answer mode is active now?", [new_value], [old_value], [f"freeform-style-{length}"])],
            "distractors": [f"freeform-style-{length}", f"mode-collision-{length}"],
        }
    if profile_id == "long_context_recall":
        goal = f"long context memory target {suffix}"
        alias = f"trace-anchor-{suffix}"
        return {
            "events": [
                _event_turn(f"Goal update: {goal}.", [{"category": "goal", "slot": "goal.primary", "value": goal, "anchors": [goal, "long context"], "relation": "session_goal"}]),
                _event_turn(f"Terminology: {alias} means the active long context anchor {suffix}.", [{"category": "terminology", "slot": "term.long_anchor", "value": f"{alias} means the active long context anchor {suffix}", "anchors": [alias, "active long context anchor"], "relation": "uses_term"}]),
            ],
            "probes": [
                LongDialogProbe("long_goal", "goal.primary", "What long context goal is still active?", [goal], [], [f"forgotten-goal-{length}"]),
                LongDialogProbe("long_alias", "term.long_anchor", f"What does {alias} mean?", [f"{alias} means the active long context anchor {suffix}"], [], [f"{alias} means drifted anchor {length}"]),
            ],
            "distractors": [f"forgotten-goal-{length}", f"{alias} means drifted anchor {length}"],
        }
    if profile_id == "cross_turn_dependency":
        project = f"dependency-project-{suffix}"
        constraint = f"must keep evidence-first replies for {project}"
        return {
            "events": [
                _event_turn(f"Goal update: focus on {project}.", [{"category": "goal", "slot": "goal.project", "value": project, "anchors": [project], "relation": "session_goal"}]),
                _event_turn(f"Constraint update: {constraint}.", [{"category": "constraint", "slot": "constraint.project", "value": constraint, "anchors": [project, "evidence-first"], "relation": "constrained_by"}]),
            ],
            "probes": [LongDialogProbe("cross_turn", "constraint.project", "For the active project, what constraint still applies?", [project, constraint], [], [f"other-project-{length}"])],
            "distractors": [f"other-project-{length}", f"cross-turn-noise-{length}"],
        }
    if profile_id == "multi_step_updates":
        v1 = f"stage-one-{suffix}"
        v2 = f"stage-two-{suffix}"
        v3 = f"stage-three-{suffix}"
        return {
            "events": [
                _event_turn(f"Stage update: {v1}.", [{"category": "stage_state", "slot": "stage.chain", "value": v1, "anchors": [v1], "relation": "stage_state"}]),
                _event_turn(f"Stage overwrite: {v2}.", [{"category": "stage_state", "slot": "stage.chain", "value": v2, "anchors": [v2], "relation": "stage_state"}]),
                _event_turn(f"Stage overwrite: {v3}.", [{"category": "stage_state", "slot": "stage.chain", "value": v3, "anchors": [v3], "relation": "stage_state"}]),
            ],
            "probes": [
                LongDialogProbe("multi_step_current", "stage.chain", "What is the current stage in the update chain?", [v3], [v1, v2], [f"ghost-stage-{length}"]),
                LongDialogProbe("multi_step_history", "stage.chain", "What were the earlier stages before the current one?", [v1, v2], [], []),
            ],
            "distractors": [f"ghost-stage-{length}", f"stale-stage-{length}"],
        }
    if profile_id == "selective_retrieval":
        alpha = f"component-alpha-{suffix}"
        beta = f"component-beta-{suffix}"
        return {
            "events": [
                _event_turn(f"Fact: {alpha} uses graph session memory.", [{"category": "fact", "slot": "component.alpha", "value": f"{alpha} uses graph session memory", "anchors": [alpha, "graph session memory"], "relation": "uses"}]),
                _event_turn(f"Fact: {beta} uses free-context prompts.", [{"category": "fact", "slot": "component.beta", "value": f"{beta} uses free-context prompts", "anchors": [beta, "free-context prompts"], "relation": "uses"}]),
            ],
            "probes": [LongDialogProbe("selective", "component.alpha", f"What does {alpha} use?", [alpha, "graph session memory"], [], [beta, "free-context prompts"])],
            "distractors": [f"{beta} uses free-context prompts", f"selective-noise-{length}"],
        }
    if profile_id == "missing_info":
        known = f"known-constraint-{suffix}"
        return {
            "events": [_event_turn(f"Constraint update: {known}.", [{"category": "constraint", "slot": "constraint.partial", "value": known, "anchors": [known], "relation": "constrained_by"}])],
            "probes": [LongDialogProbe("missing", "constraint.partial", "What do we know, and what is still missing?", [known], [], [f"made-up-detail-{length}"])],
            "distractors": [f"made-up-detail-{length}", f"fabricated-gap-{length}"],
        }
    if profile_id == "ambiguous_reference":
        alpha = f"planner-alpha-{suffix}"
        beta = f"planner-beta-{suffix}"
        return {
            "events": [
                _event_turn(f"Fact: {alpha} owns path search.", [{"category": "fact", "slot": "actor.alpha", "value": f"{alpha} owns path search", "anchors": [alpha, "path search"], "relation": "owns"}]),
                _event_turn(f"Fact: {beta} owns memory alignment.", [{"category": "fact", "slot": "actor.beta", "value": f"{beta} owns memory alignment", "anchors": [beta, "memory alignment"], "relation": "owns"}]),
            ],
            "probes": [LongDialogProbe("ambiguous_ref", "actor.alpha", "The one focused on path search owns what?", [alpha, "path search"], [], [beta, "memory alignment"])],
            "distractors": [f"{beta} owns memory alignment", f"ambiguous-noise-{length}"],
        }
    if profile_id == "multi_source_merge":
        goal = f"merge-goal-{suffix}"
        stage = f"merge-stage-{suffix}"
        constraint = f"merge-constraint-{suffix}"
        return {
            "events": [
                _event_turn(f"Goal update: {goal}.", [{"category": "goal", "slot": "goal.merge", "value": goal, "anchors": [goal], "relation": "session_goal"}]),
                _event_turn(f"Stage update: {stage}.", [{"category": "stage_state", "slot": "stage.merge", "value": stage, "anchors": [stage], "relation": "stage_state"}]),
                _event_turn(f"Constraint update: {constraint}.", [{"category": "constraint", "slot": "constraint.merge", "value": constraint, "anchors": [constraint], "relation": "constrained_by"}]),
            ],
            "probes": [LongDialogProbe("merge", "merge.bundle", "Combine the active goal, stage, and constraint.", [goal, stage, constraint], [], [f"merge-noise-{length}"])],
            "distractors": [f"merge-noise-{length}", f"merge-ghost-{length}"],
        }
    if profile_id == "high_frequency_overwrite":
        events = []
        stale_values: List[str] = []
        for idx in range(1, 7):
            value = f"hot-overwrite-{idx}-{suffix}"
            if idx < 6:
                stale_values.append(value)
            events.append(_event_turn(f"Preference overwrite: {value}.", [{"category": "preference", "slot": "preference.hot", "value": value, "anchors": [value], "relation": "prefers"}]))
        return {
            "events": events,
            "probes": [LongDialogProbe("high_freq", "preference.hot", "What is the latest high-frequency overwrite value?", [f"hot-overwrite-6-{suffix}"], stale_values, [f"hot-overwrite-ghost-{length}"])],
            "distractors": [f"hot-overwrite-ghost-{length}", f"overwrite-noise-{length}"],
        }
    if profile_id == "chinese_memory_persistence":
        goal = f"中文群聊记忆评测跑通 {suffix}"
        preference = f"本地部署优先 {suffix}"
        constraint = f"不要上传隐私数据 {suffix}"
        stage = f"联调验证阶段 {suffix}"
        return {
            "events": [
                _event_turn(f"请记住：当前目标是{goal}。", [{"category": "goal", "slot": "goal.zh.primary", "value": goal, "anchors": ["中文群聊记忆评测", "跑通"], "relation": "session_goal"}]),
                _event_turn(f"请记住：当前偏好是{preference}。", [{"category": "preference", "slot": "preference.zh.deploy", "value": preference, "anchors": ["本地部署"], "relation": "prefers"}]),
                _event_turn(f"请记住：当前约束是{constraint}。", [{"category": "constraint", "slot": "constraint.zh.privacy", "value": constraint, "anchors": ["隐私数据"], "relation": "constrained_by"}]),
                _event_turn(f"请记住：当前阶段是{stage}。", [{"category": "stage_state", "slot": "stage.zh.current", "value": stage, "anchors": ["联调验证"], "relation": "stage_state"}]),
            ],
            "probes": [
                LongDialogProbe("zh_bundle_current", "zh.bundle", "当前目标、偏好、约束和阶段分别是什么？", [goal, preference, constraint, stage], [], [f"伪造约束-{length}", f"虚假阶段-{length}"]),
                LongDialogProbe("zh_goal_current", "goal.zh.primary", "当前核心目标是什么？", [goal], [], [f"废弃目标-{length}"]),
            ],
            "distractors": [f"伪造约束-{length}", f"虚假阶段-{length}", f"废弃目标-{length}"],
        }
    if profile_id == "chinese_overwrite_chain":
        old_constraint = f"只用本地模型 {suffix}"
        new_constraint = f"允许混合部署 {suffix}"
        old_stage = f"预演阶段 {suffix}"
        new_stage = f"联调阶段 {suffix}"
        return {
            "events": [
                _event_turn(f"请记住：最初约束是{old_constraint}。", [{"category": "constraint", "slot": "constraint.zh.mode", "value": old_constraint, "anchors": ["本地模型"], "relation": "constrained_by"}]),
                _event_turn(f"约束更新：现在改成{new_constraint}。", [{"category": "constraint", "slot": "constraint.zh.mode", "value": new_constraint, "anchors": ["混合部署"], "relation": "constrained_by"}]),
                _event_turn(f"请记住：最初阶段是{old_stage}。", [{"category": "stage_state", "slot": "stage.zh.chain", "value": old_stage, "anchors": ["预演"], "relation": "stage_state"}]),
                _event_turn(f"阶段更新：当前阶段改成{new_stage}。", [{"category": "stage_state", "slot": "stage.zh.chain", "value": new_stage, "anchors": ["联调"], "relation": "stage_state"}]),
            ],
            "probes": [
                LongDialogProbe("zh_current_chain", "zh.current", "当前约束和当前阶段分别是什么？", [new_constraint, new_stage], [old_constraint, old_stage], [f"伪造部署-{length}"]),
                LongDialogProbe("zh_history_chain", "zh.history", "之前的约束和之前的阶段分别是什么？", [old_constraint, old_stage], [], []),
            ],
            "distractors": [f"伪造部署-{length}", f"旧阶段噪声-{length}"],
        }
    if profile_id == "chinese_terminology_reference":
        term = f"透明链路-{suffix}"
        old_value = f"{term}指的是草稿缓冲区 {suffix}"
        new_value = f"{term}指的是带证据的推理路径 {suffix}"
        preference = f"回答时优先引用{term}相关证据 {suffix}"
        return {
            "events": [
                _event_turn(f"术语设定：{old_value}。", [{"category": "terminology", "slot": "term.zh.trace", "value": old_value, "anchors": [term, "草稿缓冲区"], "relation": "uses_term"}]),
                _event_turn(f"术语更新：{new_value}。", [{"category": "terminology", "slot": "term.zh.trace", "value": new_value, "anchors": [term, "带证据的推理路径"], "relation": "uses_term"}]),
                _event_turn(f"偏好设定：{preference}。", [{"category": "preference", "slot": "preference.zh.term", "value": preference, "anchors": [term, "引用证据"], "relation": "prefers"}]),
            ],
            "probes": [
                LongDialogProbe("zh_term_current", "term.zh.trace", f"{term}现在指的是什么？", [new_value], [old_value], [f"{term}指的是向量缓存 {length}"]),
                LongDialogProbe("zh_term_history", "term.zh.trace", f"{term}之前指的是什么？", [old_value], [], []),
                LongDialogProbe("zh_term_pref", "preference.zh.term", f"当前和{term}相关的回答偏好是什么？", [preference], [], [f"忽略{term}证据 {length}"]),
            ],
            "distractors": [f"{term}指的是向量缓存 {length}", f"忽略{term}证据 {length}"],
        }
    goal = f"build TMCRA memory graph {suffix}"
    stage = f"stage epsilon {suffix}"
    return {
        "events": [
            _event_turn(f"Goal update: {goal}.", [{"category": "goal", "slot": "goal.primary", "value": goal, "anchors": ["TMCRA", "memory graph"], "relation": "session_goal"}]),
            _event_turn(f"Stage update: the system is in {stage}.", [{"category": "stage_state", "slot": "stage.current", "value": stage, "anchors": [stage, "system"], "relation": "stage_state"}]),
        ],
        "probes": [
            LongDialogProbe("distractor_goal", "goal.primary", "What is the current primary goal?", [goal], [], [f"ghost-goal-{length}", f"fake-goal-{length}"]),
            LongDialogProbe("distractor_stage", "stage.current", "What stage is active now?", [stage], [], [f"ghost-stage-{length}"]),
        ],
        "distractors": [f"ghost-goal-{length}", f"fake-goal-{length}", f"ghost-stage-{length}"],
    }


def _build_long_dialogue(profile_id: str, length: int) -> Dict[str, Any]:
    spec = _profile_events(profile_id, length)
    events = list(spec["events"])
    probes: List[LongDialogProbe] = list(spec["probes"])
    distractors = list(spec.get("distractors", []))
    positions = [max(1, int(length * ratio)) for ratio in (0.05, 0.22, 0.55, 0.8)]
    turns: List[Dict[str, Any]] = []
    cursor = 1
    for event_index, event in enumerate(events):
        target = positions[min(event_index, len(positions) - 1)]
        while len(turns) + 1 < target and len(turns) < max(0, length - len(events)):
            turns.append({"type": "noise", "text": _noise_turn(cursor, profile_id, distractors)})
            cursor += 1
        turns.append(event)
        cursor += 1
    while len(turns) < length:
        turns.append({"type": "noise", "text": _noise_turn(cursor, profile_id, distractors)})
        cursor += 1
    return {"profile_id": profile_id, "length": length, "turns": turns[:length], "probes": [probe.to_dict() for probe in probes]}


def _probe_result(retrieval: Dict[str, Any], probe: LongDialogProbe) -> Dict[str, Any]:
    hit_text = _normalize_text("\n".join([str(hit.get("value", "")) for hit in retrieval.get("hits", [])] + [json.dumps(retrieval.get("relations", []), ensure_ascii=False)]))
    expected_ratio = _present_ratio(hit_text, probe.expected_values)
    stale_ratio = _violation_ratio(hit_text, probe.stale_values)
    false_ratio = _violation_ratio(hit_text, probe.false_values)
    overwrite_resolution = 1.0 if not probe.stale_values else max(0.0, 1.0 - stale_ratio)
    return {
        "probe_id": probe.probe_id,
        "slot": probe.slot,
        "prompt": probe.prompt,
        "expected_ratio": round(expected_ratio, 6),
        "stale_ratio": round(stale_ratio, 6),
        "false_ratio": round(false_ratio, 6),
        "overwrite_resolution": round(overwrite_resolution, 6),
        "retrieval": retrieval,
    }


def _efficiency_component(current: float, baseline: float) -> float:
    if baseline <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - (current / baseline)))


def _attach_efficiency_scores(benchmark: Dict[str, Any]) -> None:
    baseline_map = {(str(run["profile_id"]), int(run["dialog_length"])): run for run in benchmark.get("runs", []) if run.get("memory") == "full_history_memory"}
    for run in benchmark.get("runs", []):
        baseline = baseline_map.get((str(run["profile_id"]), int(run["dialog_length"])))
        if not baseline:
            run["efficiency_score"] = 0.0
            continue
        context_score = _efficiency_component(float(run["avg_context_tokens"]), float(baseline["avg_context_tokens"]))
        storage_score = _efficiency_component(float(run["storage_bytes"]), float(baseline["storage_bytes"]))
        retrieval_score = _efficiency_component(float(run["avg_retrieval_seconds"]), float(baseline["avg_retrieval_seconds"]))
        run["efficiency_score"] = round((context_score + storage_score + retrieval_score) / 3.0, 6)


def _ingest_dialogue_turns(adapter: MemoryAdapter, turns: Sequence[Dict[str, Any]]) -> tuple[float, int, int]:
    tracemalloc.start()
    ingest_start = time.perf_counter()
    for turn in turns:
        user_text = turn["statement"] if turn["type"] == "memory" else turn["text"]
        adapter.ingest_turn(user_text, "recorded", answer_payload={"replacement_memory_records": turn.get("replacement_memory_records", [])})
    ingest_seconds = time.perf_counter() - ingest_start
    current_mem, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return ingest_seconds, current_mem, peak_mem


def run_long_dialogue_benchmark(*, memory_factories: Dict[str, MemoryFactory], config: BenchmarkConfig) -> Dict[str, Any]:
    benchmark: Dict[str, Any] = {"runs": [], "summary": [], "failures": [], "graph_exports": []}
    failures: List[FailureRecord] = []
    selected_profiles = set(config.dialog_profiles or [item.profile_id for item in default_dialog_profiles()])
    total_units = 0
    for _memory_name in memory_factories.keys():
        for profile in default_dialog_profiles():
            if profile.profile_id not in selected_profiles:
                continue
            for length in config.dialog_lengths:
                total_units += len(_build_long_dialogue(profile.profile_id, int(length))["probes"])
    completed_units = 0
    _emit_progress(config, benchmark="long_dialog", completed=0, total=total_units, status="running")
    for memory_name, factory in memory_factories.items():
        for profile in default_dialog_profiles():
            if profile.profile_id not in selected_profiles:
                continue
            for length in config.dialog_lengths:
                scenario = _build_long_dialogue(profile.profile_id, int(length))
                adapter = factory()
                adapter.reset()
                ingest_seconds, current_mem, peak_mem = _ingest_dialogue_turns(adapter, scenario["turns"])

                probe_records: List[Dict[str, Any]] = []
                total_expected = total_overwrite = total_stale = total_false = 0.0
                total_retrieval = total_context = 0.0
                for probe_payload in scenario["probes"]:
                    probe = LongDialogProbe(
                        probe_id=str(probe_payload["probe_id"]),
                        slot=str(probe_payload["slot"]),
                        prompt=str(probe_payload["prompt"]),
                        expected_values=list(probe_payload.get("expected_values", []) or []),
                        stale_values=list(probe_payload.get("stale_values", []) or []),
                        false_values=list(probe_payload.get("false_values", []) or []),
                    )
                    retrieval = adapter.retrieve(probe.prompt, top_k=config.top_k)
                    total_retrieval += retrieval.retrieval_seconds
                    total_context += retrieval.context_token_estimate
                    probe_result = _probe_result(retrieval.to_dict(), probe)
                    total_expected += probe_result["expected_ratio"]
                    total_overwrite += probe_result["overwrite_resolution"]
                    total_stale += probe_result["stale_ratio"]
                    total_false += probe_result["false_ratio"]
                    completed_units += 1
                    _emit_progress(
                        config,
                        benchmark="long_dialog",
                        completed=completed_units,
                        total=total_units,
                        status="running",
                        memory=memory_name,
                        profile_id=profile.profile_id,
                        dialog_length=int(length),
                        probe_id=probe.probe_id,
                    )
                    probe_records.append(probe_result)
                    if probe_result["expected_ratio"] < 0.999 or probe_result["stale_ratio"] > 0.0 or probe_result["false_ratio"] > 0.0:
                        failures.append(FailureRecord(benchmark="long_dialog", memory=memory_name, probe_id=probe.probe_id, reason="probe_below_target", details={"profile_id": profile.profile_id, "dialog_length": int(length), "expected_ratio": probe_result["expected_ratio"], "overwrite_resolution": probe_result["overwrite_resolution"], "stale_ratio": probe_result["stale_ratio"], "false_ratio": probe_result["false_ratio"]}))
                graph_export = adapter.export_dialog_graph()
                graph_summary = dict(graph_export.get("summary", {}) or {})
                benchmark["graph_exports"].append(
                    {
                        "kind": "long_dialog",
                        "memory": memory_name,
                        "profile_id": profile.profile_id,
                        "dialog_length": int(length),
                        "json": graph_export,
                        "mermaid": adapter.export_dialog_graph_mermaid(),
                    }
                )
                probe_count = max(1, len(probe_records))
                benchmark["runs"].append(
                    {
                        "memory": memory_name,
                        "profile_id": profile.profile_id,
                        "profile_title": profile.title,
                        "dialog_length": int(length),
                        "ingest_seconds": round(float(ingest_seconds), 6),
                        "avg_retrieval_seconds": round(float(total_retrieval / probe_count), 6),
                        "memory_correctness": round(float(total_expected / probe_count), 6),
                        "overwrite_resolution": round(float(total_overwrite / probe_count), 6),
                        "stale_recall_rate": round(float(total_stale / probe_count), 6),
                        "false_recall_rate": round(float(total_false / probe_count), 6),
                        "storage_bytes": int(adapter.storage_bytes()),
                        "python_allocated_bytes": int(current_mem),
                        "python_peak_bytes": int(peak_mem),
                        "avg_context_tokens": round(float(total_context / probe_count), 3),
                        "per_turn_storage_bytes": round(float(adapter.storage_bytes() / max(1, length)), 3),
                        "stats": adapter.stats(),
                        "graph_summary": graph_summary,
                        "probes": probe_records,
                    }
                )
    _attach_efficiency_scores(benchmark)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for run in benchmark.get("runs", []):
        grouped.setdefault(str(run["memory"]), []).append(run)
    for memory_name, runs in grouped.items():
        avg = lambda key: sum(float(item.get(key, 0.0) or 0.0) for item in runs) / max(1, len(runs))
        benchmark["summary"].append(
            {
                "memory": memory_name,
                "runs": len(runs),
                "avg_memory_correctness": round(avg("memory_correctness"), 6),
                "avg_overwrite_resolution": round(avg("overwrite_resolution"), 6),
                "avg_stale_recall_rate": round(avg("stale_recall_rate"), 6),
                "avg_false_recall_rate": round(avg("false_recall_rate"), 6),
                "avg_context_tokens": round(avg("avg_context_tokens"), 6),
                "avg_retrieval_seconds": round(avg("avg_retrieval_seconds"), 6),
                "avg_storage_bytes": round(avg("storage_bytes"), 3),
                "avg_python_peak_bytes": round(avg("python_peak_bytes"), 3),
                "avg_graph_nodes": round(avg("graph_summary.get('graph_nodes', 0)"), 3) if False else round(sum(float(item.get("graph_summary", {}).get("graph_nodes", 0) or 0) for item in runs) / max(1, len(runs)), 3),
                "avg_graph_edges": round(sum(float(item.get("graph_summary", {}).get("graph_edges", 0) or 0) for item in runs) / max(1, len(runs)), 3),
                "memory_quality_score": round((avg("memory_correctness") + avg("overwrite_resolution") + (1.0 - avg("stale_recall_rate")) + (1.0 - avg("false_recall_rate"))) / 4.0, 6),
                "efficiency_score": round(avg("efficiency_score"), 6),
                "Memory Correctness": round(avg("memory_correctness"), 6),
                "Overwrite Accuracy": round(avg("overwrite_resolution"), 6),
                "Conflict Resolution Accuracy": round(avg("overwrite_resolution"), 6),
                "False Recall Rate": round(avg("false_recall_rate"), 6),
                "Stale Recall Rate": round(avg("stale_recall_rate"), 6),
                "Avg Retrieval Context Tokens": round(avg("avg_context_tokens"), 6),
                "P95 Retrieval Context Tokens": round(max(float(item.get("avg_context_tokens", 0.0) or 0.0) for item in runs), 6),
                "Latency (ms)": round(avg("avg_retrieval_seconds") * 1000.0, 6),
            }
        )
    benchmark["summary"].sort(key=lambda item: (-float(item["memory_quality_score"]), -float(item["efficiency_score"])))
    benchmark["failures"] = [item.to_dict() for item in failures]
    _emit_progress(config, benchmark="long_dialog", completed=total_units, total=total_units, status="completed")
    return benchmark


async def run_reasoner_long_dialogue_benchmark(
    *,
    reasoner_factories: Dict[str, ReasonerFactory],
    memory_factories: Dict[str, MemoryFactory],
    config: BenchmarkConfig,
) -> Dict[str, Any]:
    benchmark: Dict[str, Any] = {"runs": [], "summary": [], "failures": []}
    failures: List[FailureRecord] = []
    selected_profiles = set(config.dialog_profiles or [item.profile_id for item in default_dialog_profiles()])
    total_units = 0
    for _reasoner_name in reasoner_factories.keys():
        for _memory_name in memory_factories.keys():
            for profile in default_dialog_profiles():
                if profile.profile_id not in selected_profiles:
                    continue
                for length in config.dialog_lengths:
                    total_units += len(_build_long_dialogue(profile.profile_id, int(length))["probes"])
    completed_units = 0
    _emit_progress(config, benchmark="reasoner_long_dialog", completed=0, total=total_units, status="running")
    for reasoner_name, reasoner_factory in reasoner_factories.items():
        for memory_name, memory_factory in memory_factories.items():
            for profile in default_dialog_profiles():
                if profile.profile_id not in selected_profiles:
                    continue
                for length in config.dialog_lengths:
                    scenario = _build_long_dialogue(profile.profile_id, int(length))
                    adapter = memory_factory()
                    adapter.reset()
                    ingest_seconds, current_mem, peak_mem = _ingest_dialogue_turns(adapter, scenario["turns"])
                    reasoner = reasoner_factory()
                    probe_records: List[Dict[str, Any]] = []
                    total_answer = total_memory = total_overwrite = total_false = total_evidence = total_reasoning = total_latency = total_context = total_verbalization_gap = total_unsupported = 0.0
                    for probe_payload in scenario["probes"]:
                        probe = LongDialogProbe(
                            probe_id=str(probe_payload["probe_id"]),
                            slot=str(probe_payload["slot"]),
                            prompt=str(probe_payload["prompt"]),
                            expected_values=list(probe_payload.get("expected_values", []) or []),
                            stale_values=list(probe_payload.get("stale_values", []) or []),
                            false_values=list(probe_payload.get("false_values", []) or []),
                        )
                        response = await reasoner.answer(probe.prompt, answer_mode=config.answer_mode, memory_adapter=adapter)
                        response_dict = response.to_dict()
                        token_usage = response_token_usage(response_dict)
                        memory_text = _memory_hit_text(response.memory_hits)
                        evidence_text = _evidence_text(response_dict)
                        haystack = _normalize_text(response.answer) + "\n" + memory_text + "\n" + evidence_text
                        answer_match = _present_ratio(response.answer, probe.expected_values)
                        memory_correctness = _present_ratio(haystack, probe.expected_values)
                        stale_ratio = _violation_ratio(haystack, probe.stale_values)
                        false_ratio = _violation_ratio(haystack, probe.false_values)
                        overwrite_resolution = 1.0 if not probe.stale_values else max(0.0, 1.0 - stale_ratio)
                        unsupported_rate = 1.0 if response.unsupported_claims else 0.0
                        evidence_consistency = 1.0 if response.evidence_consistent and not response.unsupported_claims else 0.0
                        verbalization_gap = max(0.0, memory_correctness - answer_match)
                        retrieval_meta = dict(response.metadata.get("retrieval", {}) or {})
                        retrieval_context_tokens = int(retrieval_meta.get("retrieval_context_token_estimate", retrieval_meta.get("context_token_estimate", 0)) or 0)
                        reasoning_quality_score = round((float(answer_match) + float(evidence_consistency) + float(overwrite_resolution) + float(1.0 - false_ratio)) / 4.0, 6)
                        record = {
                            "reasoner": reasoner_name,
                            "memory": memory_name,
                            "profile_id": profile.profile_id,
                            "profile_title": profile.title,
                            "dialog_length": int(length),
                            "probe_id": probe.probe_id,
                            "slot": probe.slot,
                            "prompt": probe.prompt,
                            "answer": response.answer,
                            "answer_match": round(answer_match, 6),
                            "memory_correctness": round(memory_correctness, 6),
                            "overwrite_resolution": round(overwrite_resolution, 6),
                            "false_recall_rate": round(false_ratio, 6),
                            "evidence_consistency": round(evidence_consistency, 6),
                            "unsupported_claim_rate": round(unsupported_rate, 6),
                            "verbalization_gap": round(verbalization_gap, 6),
                            "reasoning_quality_score": reasoning_quality_score,
                            "latency_seconds": round(float(response.latency_seconds), 6),
                            "retrieval_context_tokens": retrieval_context_tokens,
                            **token_usage,
                            "response": response_dict,
                        }
                        probe_records.append(record)
                        total_answer += float(record["answer_match"])
                        total_memory += float(record["memory_correctness"])
                        total_overwrite += float(record["overwrite_resolution"])
                        total_false += float(record["false_recall_rate"])
                        total_evidence += float(record["evidence_consistency"])
                        total_reasoning += float(record["reasoning_quality_score"])
                        total_latency += float(record["latency_seconds"])
                        total_context += float(record["retrieval_context_tokens"])
                        total_verbalization_gap += float(record["verbalization_gap"])
                        total_unsupported += float(record["unsupported_claim_rate"])
                        completed_units += 1
                        _emit_progress(
                            config,
                            benchmark="reasoner_long_dialog",
                            completed=completed_units,
                            total=total_units,
                            status="running",
                            reasoner=reasoner_name,
                            memory=memory_name,
                            profile_id=profile.profile_id,
                            dialog_length=int(length),
                            probe_id=probe.probe_id,
                        )

                        failure_reason = ""
                        if unsupported_rate > 0.0:
                            failure_reason = "unsupported_claim"
                        elif overwrite_resolution < 0.999 or false_ratio > 0.0:
                            failure_reason = "overwrite_stale_failure"
                        elif memory_correctness < 0.999 and probe.expected_values:
                            failure_reason = "retrieval_failure"
                        elif answer_match + 1e-9 < memory_correctness:
                            failure_reason = "grounded_but_poorly_realized"
                        elif answer_match < 0.999 or evidence_consistency < 0.999:
                            failure_reason = "evidence_failure"
                        if failure_reason:
                            failures.append(
                                FailureRecord(
                                    benchmark="reasoner_long_dialog",
                                    reasoner=reasoner_name,
                                    memory=memory_name,
                                    probe_id=probe.probe_id,
                                    reason=failure_reason,
                                    details={
                                        "profile_id": profile.profile_id,
                                        "dialog_length": int(length),
                                        "answer_match": record["answer_match"],
                                        "memory_correctness": record["memory_correctness"],
                                        "overwrite_resolution": record["overwrite_resolution"],
                                        "false_recall_rate": record["false_recall_rate"],
                                        "evidence_consistency": record["evidence_consistency"],
                                        "unsupported_claim_rate": record["unsupported_claim_rate"],
                                        "verbalization_gap": record["verbalization_gap"],
                                    },
                                )
                            )

                    probe_count = max(1, len(probe_records))
                    benchmark["runs"].append(
                        {
                            "reasoner": reasoner_name,
                            "memory": memory_name,
                            "profile_id": profile.profile_id,
                            "profile_title": profile.title,
                            "dialog_length": int(length),
                            "probe_count": len(probe_records),
                            "ingest_seconds": round(float(ingest_seconds), 6),
                            "avg_answer_match": round(float(total_answer / probe_count), 6),
                            "avg_memory_correctness": round(float(total_memory / probe_count), 6),
                            "avg_overwrite_resolution": round(float(total_overwrite / probe_count), 6),
                            "avg_false_recall_rate": round(float(total_false / probe_count), 6),
                            "avg_evidence_consistency": round(float(total_evidence / probe_count), 6),
                            "avg_reasoning_quality_score": round(float(total_reasoning / probe_count), 6),
                            "avg_latency_seconds": round(float(total_latency / probe_count), 6),
                            "avg_context_tokens": round(float(total_context / probe_count), 3),
                            "llm_prompt_tokens": int(sum(int(item.get("llm_prompt_tokens", 0) or 0) for item in probe_records)),
                            "llm_completion_tokens": int(sum(int(item.get("llm_completion_tokens", 0) or 0) for item in probe_records)),
                            "llm_total_tokens": int(sum(int(item.get("llm_total_tokens", 0) or 0) for item in probe_records)),
                            "judge_prompt_tokens": int(sum(int(item.get("judge_prompt_tokens", 0) or 0) for item in probe_records)),
                            "judge_completion_tokens": int(sum(int(item.get("judge_completion_tokens", 0) or 0) for item in probe_records)),
                            "judge_total_tokens": int(sum(int(item.get("judge_total_tokens", 0) or 0) for item in probe_records)),
                            "combined_prompt_tokens": int(sum(int(item.get("combined_prompt_tokens", 0) or 0) for item in probe_records)),
                            "combined_completion_tokens": int(sum(int(item.get("combined_completion_tokens", 0) or 0) for item in probe_records)),
                            "combined_total_tokens": int(sum(int(item.get("combined_total_tokens", 0) or 0) for item in probe_records)),
                            "avg_llm_total_tokens": round(sum(float(item.get("llm_total_tokens", 0) or 0) for item in probe_records) / probe_count, 6),
                            "avg_judge_total_tokens": round(sum(float(item.get("judge_total_tokens", 0) or 0) for item in probe_records) / probe_count, 6),
                            "avg_combined_total_tokens": round(sum(float(item.get("combined_total_tokens", 0) or 0) for item in probe_records) / probe_count, 6),
                            "avg_verbalization_gap": round(float(total_verbalization_gap / probe_count), 6),
                            "unsupported_claim_rate": round(float(total_unsupported / probe_count), 6),
                            "storage_bytes": int(adapter.storage_bytes()),
                            "python_allocated_bytes": int(current_mem),
                            "python_peak_bytes": int(peak_mem),
                            "stats": adapter.stats(),
                            "probes": probe_records,
                        }
                    )
    grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    for run in benchmark.get("runs", []):
        grouped.setdefault((str(run["reasoner"]), str(run["memory"])), []).append(run)
    for (reasoner_name, memory_name), runs in grouped.items():
        avg = lambda key: sum(float(item.get(key, 0.0) or 0.0) for item in runs) / max(1, len(runs))
        benchmark["summary"].append(
            {
                "reasoner": reasoner_name,
                "memory": memory_name,
                "runs": len(runs),
                "avg_answer_match": round(avg("avg_answer_match"), 6),
                "avg_memory_correctness": round(avg("avg_memory_correctness"), 6),
                "avg_overwrite_resolution": round(avg("avg_overwrite_resolution"), 6),
                "avg_false_recall_rate": round(avg("avg_false_recall_rate"), 6),
                "avg_evidence_consistency": round(avg("avg_evidence_consistency"), 6),
                "avg_reasoning_quality_score": round(avg("avg_reasoning_quality_score"), 6),
                "avg_latency_seconds": round(avg("avg_latency_seconds"), 6),
                "avg_context_tokens": round(avg("avg_context_tokens"), 6),
                "llm_prompt_tokens": int(sum(int(item.get("llm_prompt_tokens", 0) or 0) for item in runs)),
                "llm_completion_tokens": int(sum(int(item.get("llm_completion_tokens", 0) or 0) for item in runs)),
                "llm_total_tokens": int(sum(int(item.get("llm_total_tokens", 0) or 0) for item in runs)),
                "judge_prompt_tokens": int(sum(int(item.get("judge_prompt_tokens", 0) or 0) for item in runs)),
                "judge_completion_tokens": int(sum(int(item.get("judge_completion_tokens", 0) or 0) for item in runs)),
                "judge_total_tokens": int(sum(int(item.get("judge_total_tokens", 0) or 0) for item in runs)),
                "combined_prompt_tokens": int(sum(int(item.get("combined_prompt_tokens", 0) or 0) for item in runs)),
                "combined_completion_tokens": int(sum(int(item.get("combined_completion_tokens", 0) or 0) for item in runs)),
                "combined_total_tokens": int(sum(int(item.get("combined_total_tokens", 0) or 0) for item in runs)),
                "avg_llm_total_tokens": round(avg("avg_llm_total_tokens"), 6),
                "avg_judge_total_tokens": round(avg("avg_judge_total_tokens"), 6),
                "avg_combined_total_tokens": round(avg("avg_combined_total_tokens"), 6),
                "avg_verbalization_gap": round(avg("avg_verbalization_gap"), 6),
                "unsupported_claim_rate": round(avg("unsupported_claim_rate"), 6),
            }
        )
    benchmark["summary"].sort(key=lambda item: (-float(item["avg_reasoning_quality_score"]), -float(item["avg_answer_match"]), float(item["avg_latency_seconds"])))
    benchmark["failures"] = [item.to_dict() for item in failures]
    _emit_progress(config, benchmark="reasoner_long_dialog", completed=total_units, total=total_units, status="completed")
    return benchmark


def _current_rss_bytes() -> int:
    if psutil is None:
        return 0
    try:
        return int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception:
        return 0


def _cpu_percent() -> float:
    if psutil is None:
        return 0.0
    try:
        return float(psutil.Process(os.getpid()).cpu_percent(interval=None))
    except Exception:
        return 0.0


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(list(values), dtype=np.float64), q))


def _fit_linear(xs: Sequence[float], ys: Sequence[float]) -> Dict[str, Any]:
    if len(xs) < 2 or len(ys) < 2:
        return {"formula": "", "r2": 0.0, "mae": 0.0}
    x_arr = np.asarray(xs, dtype=np.float64)
    y_arr = np.asarray(ys, dtype=np.float64)
    coeffs = np.polyfit(x_arr, y_arr, 1)
    pred = coeffs[0] * x_arr + coeffs[1]
    residual = y_arr - pred
    ss_res = float(np.sum(np.square(residual)))
    ss_tot = float(np.sum(np.square(y_arr - np.mean(y_arr)))) or 1.0
    mae = float(np.mean(np.abs(residual)))
    return {
        "formula": f"y = {coeffs[0]:.8f} * x + {coeffs[1]:.8f}",
        "r2": round(1.0 - (ss_res / ss_tot), 6),
        "mae": round(mae, 6),
    }


def _fit_piecewise(xs: Sequence[float], ys: Sequence[float]) -> Dict[str, Any]:
    if len(xs) < 4:
        return {"split_at": 0, "left": _fit_linear(xs, ys), "right": _fit_linear(xs, ys)}
    split_index = max(2, len(xs) // 2)
    return {
        "split_at": xs[split_index - 1],
        "left": _fit_linear(xs[:split_index], ys[:split_index]),
        "right": _fit_linear(xs[split_index:], ys[split_index:]),
    }


def _fit_scaling_formulas(runs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for run in runs:
        grouped.setdefault(str(run["memory"]), []).append(run)
    results: Dict[str, Any] = {}
    metrics = (
        "storage_bytes",
        "retrieval_ms_p95",
        "python_peak_bytes",
        "context_token_estimate",
        "retrieval_context_token_estimate",
        "total_state_token_estimate",
    )
    for memory_name, items in grouped.items():
        items = sorted(items, key=lambda item: int(item["turn_count"]))
        results[memory_name] = {}
        for metric in metrics:
            bucketed: Dict[int, List[float]] = {}
            for item in items:
                bucketed.setdefault(int(item["turn_count"]), []).append(float(item.get(metric, 0.0) or 0.0))
            xs = [float(key) for key in sorted(bucketed)]
            ys = [float(sum(bucketed[int(key)]) / max(1, len(bucketed[int(key)]))) for key in xs]
            results[memory_name][metric] = {
                "linear": _fit_linear(xs, ys),
                "piecewise": _fit_piecewise(xs, ys),
                "points": [{"x": int(x), "y": round(float(y), 6)} for x, y in zip(xs, ys)],
            }
    return results


def _guard_triggered(*, storage_bytes: int, retrieval_context_tokens: int, total_state_tokens: int, retrieval_p95_ms: float, rss_bytes: int, config: BenchmarkConfig) -> str:
    thresholds = dict(config.guard_thresholds or {})
    if rss_bytes > float(thresholds.get("rss_bytes", float("inf"))):
        return "rss_bytes"
    if retrieval_p95_ms > float(thresholds.get("retrieval_p95_ms", float("inf"))):
        return "retrieval_p95_ms"
    if retrieval_context_tokens > float(thresholds.get("context_tokens", float("inf"))):
        return "context_tokens"
    if total_state_tokens > float(thresholds.get("total_state_tokens", float("inf"))):
        return "total_state_tokens"
    if storage_bytes > float(thresholds.get("storage_bytes", float("inf"))):
        return "storage_bytes"
    return ""


def run_memory_scaling_benchmark(*, memory_factories: Dict[str, MemoryFactory], config: BenchmarkConfig) -> Dict[str, Any]:
    benchmark: Dict[str, Any] = {"runs": [], "summary": [], "formula_fits": {}, "guard_events": [], "graph_exports": []}
    selected_profiles = tuple(config.scaling_profiles or ("constraint_overwrite",))
    total_units = sum(
        1
        for memory_name in memory_factories.keys()
        if memory_name != "null_memory"
        for _profile_id in selected_profiles
        for _length in config.scaling_lengths
    )
    completed_units = 0
    _emit_progress(config, benchmark="memory_scaling", completed=0, total=total_units, status="running")
    for memory_name, factory in memory_factories.items():
        if memory_name == "null_memory":
            continue
        for profile_id in selected_profiles:
            for length in config.scaling_lengths:
                scenario = _build_long_dialogue(profile_id, int(length))
                adapter = factory()
                adapter.reset()
                tracemalloc.start()
                ingest_start = time.perf_counter()
                retrieval_samples_ms: List[float] = []
                retrieval_context_samples: List[float] = []
                effective_turns = 0
                exploded = False
                guard_reason = ""
                for turn in scenario["turns"]:
                    effective_turns += 1
                    user_text = turn["statement"] if turn["type"] == "memory" else turn["text"]
                    adapter.ingest_turn(user_text, "recorded", answer_payload={"replacement_memory_records": turn.get("replacement_memory_records", [])})
                    if effective_turns in {1, 100, 1000, 5000, 10000} or effective_turns % max(1, min(10000, int(length // 10))) == 0:
                        for probe_payload in scenario["probes"][:1]:
                            retrieval = adapter.retrieve(str(probe_payload["prompt"]), top_k=config.top_k)
                            retrieval_samples_ms.append(float(retrieval.retrieval_seconds) * 1000.0)
                            retrieval_context_samples.append(float(retrieval.retrieval_context_token_estimate or retrieval.context_token_estimate or 0.0))
                        stats_snapshot = adapter.stats()
                        rss_bytes = _current_rss_bytes()
                        guard_reason = _guard_triggered(
                            storage_bytes=int(adapter.storage_bytes()),
                            retrieval_context_tokens=int(_percentile(retrieval_context_samples, 95)),
                            total_state_tokens=int(stats_snapshot.get("total_state_token_estimate", stats_snapshot.get("context_token_estimate", 0)) or 0),
                            retrieval_p95_ms=_percentile(retrieval_samples_ms, 95),
                            rss_bytes=rss_bytes,
                            config=config,
                        )
                        if guard_reason:
                            exploded = True
                            benchmark["guard_events"].append({"memory": memory_name, "profile_id": profile_id, "requested_turns": int(length), "effective_turns": effective_turns, "guard_reason": guard_reason})
                            break
                ingest_seconds = time.perf_counter() - ingest_start
                current_mem, peak_mem = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                graph_export = adapter.export_dialog_graph()
                stats_snapshot = adapter.stats()
                benchmark["graph_exports"].append({"kind": "scaling", "memory": memory_name, "profile_id": profile_id, "dialog_length": int(effective_turns), "json": graph_export, "mermaid": adapter.export_dialog_graph_mermaid()})
                run = {
                    "memory": memory_name,
                    "profile_id": profile_id,
                    "turn_count": int(effective_turns),
                    "requested_turn_count": int(length),
                    "records": int(stats_snapshot.get("records", 0) or 0),
                    "active_slots": int(stats_snapshot.get("active_slots", stats_snapshot.get("active_records", 0) or 0)),
                    "superseded_records": int(stats_snapshot.get("superseded_records", 0) or 0),
                    "graph_nodes": int(graph_export.get("summary", {}).get("graph_nodes", 0) or 0),
                    "graph_edges": int(graph_export.get("summary", {}).get("graph_edges", 0) or 0),
                    "storage_bytes": int(adapter.storage_bytes()),
                    "context_token_estimate": int(sum(retrieval_context_samples) / max(1, len(retrieval_context_samples))),
                    "retrieval_context_token_estimate": int(sum(retrieval_context_samples) / max(1, len(retrieval_context_samples))),
                    "retrieval_context_token_estimate_p95": int(_percentile(retrieval_context_samples, 95)),
                    "total_state_token_estimate": int(stats_snapshot.get("total_state_token_estimate", stats_snapshot.get("context_token_estimate", 0)) or 0),
                    "ingest_seconds_total": round(float(ingest_seconds), 6),
                    "ingest_us_per_turn": round((float(ingest_seconds) * 1_000_000.0) / max(1, effective_turns), 6),
                    "retrieval_ms_p50": round(_percentile(retrieval_samples_ms, 50), 6),
                    "retrieval_ms_p95": round(_percentile(retrieval_samples_ms, 95), 6),
                    "retrieval_ms_p99": round(_percentile(retrieval_samples_ms, 99), 6),
                    "python_rss_bytes": int(_current_rss_bytes()),
                    "python_peak_bytes": int(peak_mem),
                    "cpu_percent": round(_cpu_percent(), 6),
                    "disk_bytes_written": int(adapter.storage_bytes()),
                    "exploded": bool(exploded),
                    "guard_reason": guard_reason,
                }
                benchmark["runs"].append(run)
                completed_units += 1
                _emit_progress(
                    config,
                    benchmark="memory_scaling",
                    completed=completed_units,
                    total=total_units,
                    status="running",
                    memory=memory_name,
                    profile_id=profile_id,
                    requested_turn_count=int(length),
                    effective_turn_count=int(effective_turns),
                    exploded=bool(exploded),
                )
    benchmark["formula_fits"] = _fit_scaling_formulas(benchmark["runs"])
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for run in benchmark["runs"]:
        grouped.setdefault(str(run["memory"]), []).append(run)
    for memory_name, runs in grouped.items():
        safe_runs = [run for run in runs if not run["exploded"]]
        benchmark["summary"].append(
            {
                "memory": memory_name,
                "runs": len(runs),
                "safe_runs": len(safe_runs),
                "max_safe_turn_count": max((int(run["turn_count"]) for run in safe_runs), default=0),
                "exploded_runs": sum(1 for run in runs if run["exploded"]),
                "avg_storage_bytes": round(sum(float(run["storage_bytes"]) for run in runs) / max(1, len(runs)), 3),
                "avg_retrieval_ms_p95": round(sum(float(run["retrieval_ms_p95"]) for run in runs) / max(1, len(runs)), 6),
                "avg_context_token_estimate": round(sum(float(run["context_token_estimate"]) for run in runs) / max(1, len(runs)), 3),
                "avg_retrieval_context_token_estimate": round(sum(float(run["retrieval_context_token_estimate"]) for run in runs) / max(1, len(runs)), 3),
                "avg_total_state_token_estimate": round(sum(float(run["total_state_token_estimate"]) for run in runs) / max(1, len(runs)), 3),
                "avg_python_peak_bytes": round(sum(float(run["python_peak_bytes"]) for run in runs) / max(1, len(runs)), 3),
                "Performance vs Context Size Curve": [
                    {"turn_count": int(run["turn_count"]), "retrieval_context_tokens": int(run["retrieval_context_token_estimate"]), "storage_bytes": int(run["storage_bytes"]), "retrieval_ms_p95": float(run["retrieval_ms_p95"])}
                    for run in sorted(runs, key=lambda item: int(item["turn_count"]))
                ],
                "Stability under Scaling": round(len(safe_runs) / max(1, len(runs)), 6),
            }
        )
    benchmark["summary"].sort(key=lambda item: (-int(item["max_safe_turn_count"]), float(item["avg_retrieval_ms_p95"])))
    _emit_progress(config, benchmark="memory_scaling", completed=total_units, total=total_units, status="completed")
    return benchmark


async def run_tunneling_ab_benchmark(*, cases: Sequence[EvalCase], config: BenchmarkConfig) -> Dict[str, Any]:
    from .adapters import GraphSessionMemoryAdapter, NullMemoryAdapter, TriMazeIsolatedReasoner

    path_cases = [case for case in cases if "path" in str(case.category or "")]
    if not path_cases:
        return {"summary": [], "cases": [], "failures": []}
    return await run_static_ab_benchmark(
        cases=path_cases,
        reasoner_factories={
            "tmcra_isolated_trimaze_tunneling_on": lambda: TriMazeIsolatedReasoner(tunneling_enabled=True),
            "tmcra_isolated_trimaze_tunneling_off": lambda: TriMazeIsolatedReasoner(tunneling_enabled=False),
        },
        memory_factories={
            "graph_session_memory_v2": GraphSessionMemoryAdapter,
            "null_memory": NullMemoryAdapter,
        },
        config=config,
    )


def build_leaderboard(
    *,
    static_results: Dict[str, Any] | None,
    long_dialog_results: Dict[str, Any] | None,
    reasoner_long_dialog_results: Dict[str, Any] | None = None,
    config: BenchmarkConfig,
) -> Dict[str, Any]:
    static_summary = {(item["reasoner"], item["memory"]): item for item in (static_results or {}).get("summary", [])}
    long_summary = {item["memory"]: item for item in (long_dialog_results or {}).get("summary", [])}
    reasoner_long_summary = {(item["reasoner"], item["memory"]): item for item in (reasoner_long_dialog_results or {}).get("summary", [])}
    weights = dict(config.score_weights or {})
    rw, mw, ew = float(weights.get("reasoning_quality", 1.0)), float(weights.get("memory_quality", 1.0)), float(weights.get("efficiency", 1.0))
    denominator = max(0.0001, rw + mw + ew)
    records: List[LeaderboardRecord] = []
    for (reasoner, memory), static_item in static_summary.items():
        long_item = long_summary.get(memory, {})
        reasoner_long_item = reasoner_long_summary.get((reasoner, memory), {})
        static_reasoning_quality = float(static_item.get("avg_reasoning_quality_score", 0.0))
        long_dialog_reasoning_quality = float(reasoner_long_item.get("avg_reasoning_quality_score", static_reasoning_quality))
        reasoning_quality = (static_reasoning_quality + long_dialog_reasoning_quality) / 2.0 if reasoner_long_item else static_reasoning_quality
        memory_quality = float(long_item.get("memory_quality_score", 0.0))
        efficiency = float(long_item.get("efficiency_score", 0.0))
        total_score = ((reasoning_quality * rw) + (memory_quality * mw) + (efficiency * ew)) / denominator
        records.append(
            LeaderboardRecord(
                reasoner=reasoner,
                memory=memory,
                reasoning_quality_score=reasoning_quality,
                memory_quality_score=memory_quality,
                efficiency_score=efficiency,
                total_score=total_score,
                metadata={
                    "static_reasoning_quality_score": static_item.get("avg_reasoning_quality_score", 0.0),
                    "long_dialog_reasoning_quality_score": reasoner_long_item.get("avg_reasoning_quality_score", static_item.get("avg_reasoning_quality_score", 0.0)),
                    "long_dialog_answer_match": reasoner_long_item.get("avg_answer_match", static_item.get("avg_answer_match", 0.0)),
                    "long_dialog_memory_correctness": reasoner_long_item.get("avg_memory_correctness", 0.0),
                    "long_dialog_overwrite_resolution": reasoner_long_item.get("avg_overwrite_resolution", 0.0),
                    "long_dialog_false_recall_rate": reasoner_long_item.get("avg_false_recall_rate", 0.0),
                    "long_dialog_evidence_consistency_rate": reasoner_long_item.get("avg_evidence_consistency", 0.0),
                    "long_dialog_latency_seconds": reasoner_long_item.get("avg_latency_seconds", 0.0),
                    "long_dialog_context_tokens": reasoner_long_item.get("avg_context_tokens", 0.0),
                    "long_dialog_avg_llm_total_tokens": reasoner_long_item.get("avg_llm_total_tokens", 0.0),
                    "long_dialog_avg_judge_total_tokens": reasoner_long_item.get("avg_judge_total_tokens", 0.0),
                    "long_dialog_avg_combined_total_tokens": reasoner_long_item.get("avg_combined_total_tokens", 0.0),
                    "long_dialog_verbalization_gap": reasoner_long_item.get("avg_verbalization_gap", 0.0),
                    "answer_match": static_item.get("avg_answer_match", 0.0),
                    "evidence_consistency_rate": static_item.get("evidence_consistency_rate", 0.0),
                    "Latency (ms)": static_item.get("Latency (ms)", round(float(static_item.get("avg_latency_seconds", 0.0)) * 1000.0, 6)),
                    "avg_llm_total_tokens": static_item.get("avg_llm_total_tokens", 0.0),
                    "avg_judge_total_tokens": static_item.get("avg_judge_total_tokens", 0.0),
                    "avg_combined_total_tokens": static_item.get("avg_combined_total_tokens", 0.0),
                    "llm_total_tokens": static_item.get("llm_total_tokens", 0),
                    "judge_total_tokens": static_item.get("judge_total_tokens", 0),
                    "combined_total_tokens": static_item.get("combined_total_tokens", 0),
                    "memory_correctness": long_item.get("avg_memory_correctness", 0.0),
                    "overwrite_resolution": long_item.get("avg_overwrite_resolution", 0.0),
                    "stale_recall_rate": long_item.get("avg_stale_recall_rate", 0.0),
                    "false_recall_rate": long_item.get("avg_false_recall_rate", 0.0),
                    "slot_head_accuracy": static_item.get("slot_head_accuracy", 0.0),
                    "history_query_accuracy": static_item.get("history_query_accuracy", 0.0),
                    "judge_trigger_rate": static_item.get("judge_trigger_rate", 0.0),
                    "judge_applied_rate": static_item.get("judge_applied_rate", 0.0),
                    "judge_decision_valid_rate": static_item.get("judge_decision_valid_rate", 0.0),
                    "judge_effective_apply_rate": static_item.get("judge_effective_apply_rate", 0.0),
                    "compare_realization_accuracy": static_item.get("compare_realization_accuracy", 0.0),
                    "timeline_realization_accuracy": static_item.get("timeline_realization_accuracy", 0.0),
                    "path_rerank_gain": static_item.get("path_rerank_gain", 0.0),
                    "path_composition_accuracy": static_item.get("path_composition_accuracy", 0.0),
                    "path_protocol_accuracy": static_item.get("path_protocol_accuracy", 0.0),
                    "path_consistency_score": static_item.get("path_consistency_score", 0.0),
                    "critical_node_hit_rate": static_item.get("critical_node_hit_rate", 0.0),
                    "multi_path_coverage": static_item.get("multi_path_coverage", 0.0),
                    "science_reasoning_score": static_item.get("science_reasoning_score", 0.0),
                    "math_reasoning_score": static_item.get("math_reasoning_score", 0.0),
                    "emotion_reasoning_score": static_item.get("emotion_reasoning_score", 0.0),
                    "mixed_domain_reasoning_score": static_item.get("mixed_domain_reasoning_score", 0.0),
                    "science_math_emotion_combo_score": static_item.get("science_math_emotion_combo_score", 0.0),
                    "entity_isolation_accuracy": static_item.get("entity_isolation_accuracy", 0.0),
                    "temporal_consistency_score": static_item.get("temporal_consistency_score", 0.0),
                    "retrieval_precision": static_item.get("retrieval_precision", 0.0),
                    "retrieval_recall": static_item.get("retrieval_recall", 0.0),
                    "verbalization_gap": static_item.get("avg_verbalization_gap", 0.0),
                    "unsupported_claim_rate": static_item.get("unsupported_claim_rate", 0.0),
                },
            )
        )
    records.sort(key=lambda item: item.total_score, reverse=True)
    return {"summary": [item.to_dict() for item in records]}


def build_replacement_verdict(*, leaderboard: Dict[str, Any] | None, long_dialog_results: Dict[str, Any] | None, scaling_results: Dict[str, Any] | None = None) -> Dict[str, Any]:
    summary = list((leaderboard or {}).get("summary", []) or [])
    long_summary = {item["memory"]: item for item in (long_dialog_results or {}).get("summary", [])}
    scaling_summary = {item["memory"]: item for item in (scaling_results or {}).get("summary", [])}
    tmcra_graph = next((item for item in summary if item["reasoner"].startswith("tmcra_isolated_trimaze") and item["memory"] in {"graph_session_memory", "graph_session_memory_v2"}), None)
    llm_graph = next((item for item in summary if item["reasoner"].startswith("openai_compat_") and item["memory"] in {"graph_session_memory", "graph_session_memory_v2"}), None)
    graph_memory = long_summary.get("graph_session_memory_v2") or long_summary.get("graph_session_memory")
    scaling_memory = scaling_summary.get("graph_session_memory_v2") or scaling_summary.get("graph_session_memory")
    memory_ready = bool(graph_memory and float(graph_memory.get("avg_overwrite_resolution", 0.0)) >= 0.90 and float(graph_memory.get("avg_stale_recall_rate", 1.0)) <= 0.10 and float(graph_memory.get("avg_false_recall_rate", 1.0)) <= 0.02)
    reasoning_ready = bool(
        tmcra_graph
        and float(tmcra_graph["reasoning_quality_score"]) >= 0.80
        and float(tmcra_graph["metadata"].get("unsupported_claim_rate", 1.0)) == 0.0
        and (llm_graph is None or (float(llm_graph["total_score"]) - float(tmcra_graph["total_score"])) <= 0.05)
    )
    scaling_ready = bool(scaling_memory and int(scaling_memory.get("max_safe_turn_count", 0)) >= 100000)
    if memory_ready and reasoning_ready and scaling_ready:
        classification = "memory replacement ready"
    elif memory_ready:
        classification = "hybrid model ready"
    else:
        classification = "full replacement not ready"
    return {
        "classification": classification,
        "memory_ready": memory_ready,
        "reasoning_ready": reasoning_ready,
        "scaling_ready": scaling_ready,
        "tmcra_graph": tmcra_graph,
        "llm_graph": llm_graph,
        "graph_memory": graph_memory,
        "scaling_memory": scaling_memory,
    }


def write_benchmark_report(
    *,
    output_dir: str | Path,
    static_results: Dict[str, Any] | None = None,
    long_dialog_results: Dict[str, Any] | None = None,
    reasoner_long_dialog_results: Dict[str, Any] | None = None,
    leaderboard: Dict[str, Any] | None = None,
    scaling_results: Dict[str, Any] | None = None,
    tunneling_results: Dict[str, Any] | None = None,
    verdict: Dict[str, Any] | None = None,
    config: BenchmarkConfig | None = None,
) -> Dict[str, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts: Dict[str, Path] = {}
    graph_root = out_dir / "dialog_memory_snapshots"
    graph_root.mkdir(parents=True, exist_ok=True)

    static_results = annotate_result_payload(static_results) if static_results is not None else None
    long_dialog_results = annotate_result_payload(long_dialog_results) if long_dialog_results is not None else None
    reasoner_long_dialog_results = annotate_result_payload(reasoner_long_dialog_results) if reasoner_long_dialog_results is not None else None
    leaderboard = annotate_result_payload(leaderboard) if leaderboard is not None else None
    scaling_results = annotate_result_payload(scaling_results) if scaling_results is not None else None
    tunneling_results = annotate_result_payload(tunneling_results) if tunneling_results is not None else None
    verdict = annotate_result_payload(verdict) if verdict is not None else None

    def _write_graph_exports(payload: Dict[str, Any] | None) -> None:
        for export in list((payload or {}).get("graph_exports", []) or []):
            run_dir = graph_root / f"{export.get('kind', 'run')}_{export.get('memory', 'memory')}_{export.get('profile_id', 'profile')}_{export.get('dialog_length', 0)}"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "dialog_memory_graph.json").write_text(json.dumps(export.get("json", {}), ensure_ascii=False, indent=2), encoding="utf-8")
            (run_dir / "dialog_memory_graph.mmd").write_text(str(export.get("mermaid", "graph TD\n")), encoding="utf-8")
            (run_dir / "dialog_memory_graph_summary.json").write_text(json.dumps(export.get("json", {}).get("summary", {}), ensure_ascii=False, indent=2), encoding="utf-8")
            snapshots = list(export.get("json", {}).get("snapshots", []) or [])
            if snapshots:
                snap_dir = run_dir / "snapshots"
                snap_dir.mkdir(parents=True, exist_ok=True)
                for snapshot in snapshots:
                    point = int(snapshot.get("turn_index", 0) or 0)
                    (snap_dir / f"snapshot_{point}.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    _write_graph_exports(long_dialog_results)
    _write_graph_exports(scaling_results)

    if static_results is not None:
        static_path = out_dir / "static_ab_results.json"
        static_path.write_text(json.dumps(static_results, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["static_json"] = static_path
    if long_dialog_results is not None:
        long_copy = copy.deepcopy(long_dialog_results)
        long_copy["graph_exports"] = [{"kind": item.get("kind"), "memory": item.get("memory"), "profile_id": item.get("profile_id"), "dialog_length": item.get("dialog_length"), "summary": item.get("json", {}).get("summary", {})} for item in long_copy.get("graph_exports", [])]
        long_path = out_dir / "long_dialog_results.json"
        long_path.write_text(json.dumps(long_copy, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["long_dialog_json"] = long_path
    if reasoner_long_dialog_results is not None:
        reasoner_long_path = out_dir / "reasoner_long_dialog_results.json"
        reasoner_long_path.write_text(json.dumps(reasoner_long_dialog_results, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["reasoner_long_dialog_json"] = reasoner_long_path
    if scaling_results is not None:
        scaling_copy = copy.deepcopy(scaling_results)
        scaling_copy["graph_exports"] = [{"kind": item.get("kind"), "memory": item.get("memory"), "profile_id": item.get("profile_id"), "dialog_length": item.get("dialog_length"), "summary": item.get("json", {}).get("summary", {})} for item in scaling_copy.get("graph_exports", [])]
        scaling_path = out_dir / "memory_scaling_curve.json"
        scaling_path.write_text(json.dumps(scaling_copy, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["scaling_json"] = scaling_path
        resource_curve_path = out_dir / "resource_curve.json"
        resource_curve_path.write_text(json.dumps({"summary": scaling_copy.get("summary", []), "runs": scaling_copy.get("runs", [])}, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["resource_curve_json"] = resource_curve_path
        formula_path = out_dir / "memory_formula_fit.json"
        formula_path.write_text(json.dumps(scaling_copy.get("formula_fits", {}), ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["formula_json"] = formula_path
    if tunneling_results is not None:
        tunneling_path = out_dir / "tunneling_ab_results.json"
        tunneling_path.write_text(json.dumps(tunneling_results, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["tunneling_json"] = tunneling_path
    if leaderboard is not None:
        leaderboard_path = out_dir / "leaderboard_v2.json"
        leaderboard_path.write_text(json.dumps(leaderboard, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["leaderboard_json"] = leaderboard_path
    if verdict is not None:
        verdict_path = out_dir / "TMCRA_replacement_verdict.json"
        verdict_path.write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["verdict_json"] = verdict_path

    token_usage_summary = annotate_result_payload(
        build_token_usage_summary(
            static_results=static_results,
            long_dialog_results=long_dialog_results,
            reasoner_long_dialog_results=reasoner_long_dialog_results,
        )
    )
    token_usage_path = out_dir / "token_usage_summary.json"
    token_usage_path.write_text(json.dumps(token_usage_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts["token_usage_json"] = token_usage_path

    failure_items = [
        *((static_results or {}).get("failures", []) or []),
        *((long_dialog_results or {}).get("failures", []) or []),
        *((reasoner_long_dialog_results or {}).get("failures", []) or []),
        *((scaling_results or {}).get("guard_events", []) or []),
    ]
    failures_path = out_dir / "failures.jsonl"
    with failures_path.open("w", encoding="utf-8") as handle:
        for item in failure_items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    artifacts["failures_jsonl"] = failures_path
    failure_slices: Dict[str, int] = {}
    for item in failure_items:
        key = str(item.get("reason") or item.get("benchmark") or "unknown")
        failure_slices[key] = failure_slices.get(key, 0) + 1
    failure_slices_path = out_dir / "failure_slices.json"
    failure_slices_path.write_text(json.dumps(failure_slices, ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts["failure_slices_json"] = failure_slices_path

    lines: List[str] = [
        "# TMCRA Replacement A/B Report",
        "",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Answer mode: {(config.answer_mode if config else 'transparent')}",
        f"- Remote run id: {(config.remote_run_id if config else '') or 'local'}",
        "",
    ]
    if verdict is not None:
        lines.extend(["## Verdict", "", f"- Classification: `{verdict.get('classification', 'unknown')}`", f"- Memory ready: `{verdict.get('memory_ready', False)}`", f"- Reasoning ready: `{verdict.get('reasoning_ready', False)}`", f"- Scaling ready: `{verdict.get('scaling_ready', False)}`", ""])
    if leaderboard is not None:
        lines.extend(["## Leaderboard", ""])
        for item in leaderboard.get("summary", [])[:12]:
            lines.append(
                f"- {format_code_with_label('reasoner', item['reasoner'])} x {format_code_with_label('memory', item['memory'])}: total={item['total_score']}, reasoning={item['reasoning_quality_score']}, memory={item['memory_quality_score']}, efficiency={item['efficiency_score']}"
            )
        lines.append("")
        top = leaderboard.get("summary", [])[:3]
        if top:
            lines.extend(["## Domain Reasoning Slices", ""])
            for item in top:
                meta = dict(item.get("metadata", {}) or {})
                lines.append(
                    f"- {format_code_with_label('reasoner', item['reasoner'])} x {format_code_with_label('memory', item['memory'])}: science={meta.get('science_reasoning_score', 0.0)}, math={meta.get('math_reasoning_score', 0.0)}, emotion={meta.get('emotion_reasoning_score', 0.0)}, mixed={meta.get('mixed_domain_reasoning_score', 0.0)}, all3={meta.get('science_math_emotion_combo_score', 0.0)}"
                )
            lines.append("")
    if long_dialog_results is not None:
        lines.extend(["## Long Dialogue Memory Summary", ""])
        for item in long_dialog_results.get("summary", []) or []:
            lines.append(
                f"- {format_code_with_label('memory', item['memory'])}: memory_quality={item.get('memory_quality_score', 0.0)}, efficiency={item.get('efficiency_score', 0.0)}, context={item.get('avg_context_tokens', item.get('Avg Retrieval Context Tokens', 0.0))}, storage={item.get('avg_storage_bytes', 0)}B, stale={item.get('avg_stale_recall_rate', item.get('Stale Recall Rate', 0.0))}, false={item.get('avg_false_recall_rate', item.get('False Recall Rate', 0.0))}"
            )
        lines.append("")
    if reasoner_long_dialog_results is not None:
        lines.extend(["## Reasoner-Aware Long Dialogue Summary", ""])
        for item in reasoner_long_dialog_results.get("summary", [])[:12] or []:
            lines.append(
                f"- {format_code_with_label('reasoner', item['reasoner'])} x {format_code_with_label('memory', item['memory'])}: reasoning={item.get('avg_reasoning_quality_score', 0.0)}, answer={item.get('avg_answer_match', 0.0)}, memory_use={item.get('avg_memory_correctness', 0.0)}, overwrite={item.get('avg_overwrite_resolution', 0.0)}, false={item.get('avg_false_recall_rate', 0.0)}, latency={item.get('avg_latency_seconds', 0.0)}s"
            )
        lines.append("")
    if scaling_results is not None:
        lines.extend(["## Scaling Summary", ""])
        for item in scaling_results.get("summary", []) or []:
            lines.append(
                f"- {format_code_with_label('memory', item['memory'])}: max_safe_turn_count={item['max_safe_turn_count']}, exploded_runs={item['exploded_runs']}, retrieval_p95={item['avg_retrieval_ms_p95']}ms, retrieval_context={item.get('avg_retrieval_context_token_estimate', item.get('avg_context_token_estimate', 0))}, total_state={item.get('avg_total_state_token_estimate', 0)}, storage={item['avg_storage_bytes']}B"
            )
        lines.append("")
    report_path = out_dir / "replacement_decision_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    artifacts["report_md"] = report_path

    token_lines: List[str] = [
        "# Token Usage Summary",
        "",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Overall",
        "",
        f"- combined_total_tokens={token_usage_summary.get('overall', {}).get('combined_total_tokens', 0)}",
        f"- llm_total_tokens={token_usage_summary.get('overall', {}).get('llm_total_tokens', 0)}",
        f"- judge_total_tokens={token_usage_summary.get('overall', {}).get('judge_total_tokens', 0)}",
        f"- long_dialog_calls_without_llm={token_usage_summary.get('overall', {}).get('long_dialog_calls_without_llm', 0)}",
        "",
        "## By Benchmark",
        "",
    ]
    for item in token_usage_summary.get("by_benchmark", []) or []:
        token_lines.append(
            f"- `{item.get('benchmark', '')}`: calls={item.get('calls', 0)}, llm_total={item.get('llm_total_tokens', 0)}, judge_total={item.get('judge_total_tokens', 0)}, combined_total={item.get('combined_total_tokens', 0)}, avg_combined={item.get('avg_combined_total_tokens', 0)}"
        )
    token_lines.extend(["", "## By Combo", ""])
    for item in (token_usage_summary.get("by_combo", []) or [])[:24]:
        token_lines.append(
            f"- `{item.get('benchmark', '')}` / {format_code_with_label('reasoner', item.get('reasoner', ''))} x {format_code_with_label('memory', item.get('memory', ''))}: llm_total={item.get('llm_total_tokens', 0)}, judge_total={item.get('judge_total_tokens', 0)}, combined_total={item.get('combined_total_tokens', 0)}, avg_combined={item.get('avg_combined_total_tokens', 0)}"
        )
    token_md_path = out_dir / "token_usage_summary.md"
    token_md_path.write_text("\n".join(token_lines), encoding="utf-8")
    artifacts["token_usage_md"] = token_md_path

    compare_lines: List[str] = [
        "# TMCRA vs Qwen vs DeepSeek",
        "",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
    ]
    if leaderboard is not None:
        tmcra_rows = [item for item in leaderboard.get("summary", []) if str(item.get("reasoner", "")).startswith("tmcra_isolated_trimaze")]
        compare_lines.extend(["## TMCRA Summary", ""])
        compare_rows = tmcra_rows[:3]
        for row in compare_rows:
            compare_lines.append(
                f"- {format_code_with_label('reasoner', row['reasoner'])} x {format_code_with_label('memory', row['memory'])}: total={row['total_score']}, reasoning={row['reasoning_quality_score']}, memory={row['memory_quality_score']}, efficiency={row['efficiency_score']}"
            )
        compare_lines.extend(["", "## TMCRA vs Qwen/DeepSeek (Evidence-Constrained)", ""])
        for row in [item for item in leaderboard.get("summary", []) if str(item.get("reasoner", "")).startswith("openai_compat_") and not str(item.get("reasoner", "")).endswith("_cot") and not str(item.get("reasoner", "")).endswith("_full_context")][:8]:
            compare_lines.append(
                f"- {format_code_with_label('reasoner', row['reasoner'])} x {format_code_with_label('memory', row['memory'])}: total={row['total_score']}, evidence={row['metadata'].get('evidence_consistency_rate', 0.0)}, unsupported={row['metadata'].get('unsupported_claim_rate', 0.0)}"
            )
        compare_lines.extend(["", "## TMCRA vs Qwen/DeepSeek (Full-Context Free Reasoning)", ""])
        for row in [item for item in leaderboard.get("summary", []) if str(item.get("reasoner", "")).endswith("_full_context")][:8]:
            compare_lines.append(
                f"- {format_code_with_label('reasoner', row['reasoner'])} x {format_code_with_label('memory', row['memory'])}: total={row['total_score']}, reasoning={row['reasoning_quality_score']}, latency_hint={row['metadata'].get('Latency (ms)', 0.0)}"
            )
    llm_report_path = out_dir / "TMCRA_vs_Qwen_vs_DeepSeek_report.md"
    llm_report_path.write_text("\n".join(compare_lines), encoding="utf-8")
    artifacts["llm_report_md"] = llm_report_path
    return artifacts
