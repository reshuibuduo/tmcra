from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
import time
from typing import Any, Dict, Iterable, List, Sequence

from experiments.replacement.adapters.base import LLMProfile
from experiments.replacement.adapters.reasoning_adapters import OpenAI

from .intent import QueryIntent
from .slot_state import ResolvedSlotRecord, ResolvedSlotView, SlotStateResolution


def _completion_usage_dict(completion: Any) -> Dict[str, int]:
    usage = getattr(completion, "usage", None)
    if usage is None and isinstance(completion, dict):
        usage = completion.get("usage")
    if usage is None:
        return {}
    if isinstance(usage, dict):
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or (prompt_tokens + completion_tokens))
    else:
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or (prompt_tokens + completion_tokens))
    usage_dict = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    return usage_dict if any(usage_dict.values()) else {}


_JUDGE_QUERY_MARKERS = (
    "history",
    "before",
    "earlier",
    "previous",
    "current",
    "now",
    "latest",
    "timeline",
    "compare",
    "summarize",
    "summary",
    "why",
    "stable across turns",
    "after the delay",
    "path",
    "route",
    "must go through",
    "if there is no",
    "if there is no longer",
    "if removed",
    "change over time",
    "变化过程",
    "总结",
    "为什么",
    "现在",
    "之前",
    "当前",
    "路径",
    "路线",
    "必须经过",
    "如果没有",
)


_CURRENT_ONLY_HISTORY_MARKERS = (
    "previous",
    "earlier",
    "before",
    "historical",
    "history",
    "compare",
    "versus",
    "vs",
    "\u4e4b\u524d",
    "\u4ee5\u524d",
    "\u5386\u53f2",
    "\u5bf9\u6bd4",
)
_INACTIVE_QUERY_MARKERS = (
    "noise",
    "inactive",
    "stay inactive",
    "should stay inactive",
    "should be ignored",
    "ignore this",
    "ignore that",
    "\u566a\u58f0",
    "\u4e0d\u6fc0\u6d3b",
    "\u4fdd\u6301\u4e0d\u6fc0\u6d3b",
    "\u4e0d\u5e94\u751f\u6548",
    "\u5ffd\u7565",
)

# Rebind the query markers with clean UTF-8 strings so judge triggering does not
# depend on mojibake constants that may still exist earlier in the file.
_JUDGE_QUERY_MARKERS = (
    "history",
    "before",
    "earlier",
    "previous",
    "current",
    "now",
    "latest",
    "timeline",
    "compare",
    "summarize",
    "summary",
    "why",
    "stable across turns",
    "after the delay",
    "path",
    "route",
    "must go through",
    "if there is no",
    "if there is no longer",
    "if removed",
    "change over time",
    "变化过程",
    "总结",
    "为什么",
    "现在",
    "之前",
    "当前",
    "路径",
    "路线",
    "必须经过",
    "如果没有",
)

def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _normalize(value: object) -> str:
    return _clean_text(value).lower()


def _env_bool(name: str, default: bool) -> bool:
    raw = _clean_text(os.getenv(name, ""))
    if not raw:
        return bool(default)
    return _normalize(raw) not in {"0", "false", "no", "off"}


def _dedupe_strings(values: Iterable[object]) -> List[str]:
    results: List[str] = []
    seen = set()
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        key = _normalize(text)
        if key in seen:
            continue
        seen.add(key)
        results.append(text)
    return results


def _dedupe_ints(values: Iterable[object]) -> List[int]:
    results: List[int] = []
    seen = set()
    for value in values:
        try:
            number = int(value)
        except Exception:
            continue
        if number < 0 or number in seen:
            continue
        seen.add(number)
        results.append(number)
    return results


def _record_payload(record: ResolvedSlotRecord | None) -> Dict[str, Any] | None:
    return record.to_dict() if record is not None else None


def _path_summary(candidate: Dict[str, Any], *, path_index: int = 0) -> Dict[str, Any]:
    concepts = [str(value) for value in list(candidate.get("concepts", []) or candidate.get("nodes", []) or []) if _clean_text(value)][:8]
    if not concepts:
        raw_path = _clean_text(candidate.get("path", ""))
        if raw_path and "->" in raw_path:
            concepts = [part.strip() for part in raw_path.split("->") if _clean_text(part)]
    required_nodes = _dedupe_strings(candidate.get("required_nodes", []) or [])
    blocked_nodes = _dedupe_strings(candidate.get("blocked_nodes", []) or [])
    critical_nodes = _dedupe_strings(candidate.get("critical_nodes", []) or concepts[1:-1])
    tunnels = list(candidate.get("temporal_tunnels", []) or [])
    return {
        "path_index": int(candidate.get("path_index", path_index) or path_index),
        "path_id": _clean_text(candidate.get("path_id", "")) or f"path:{path_index}",
        "concepts": concepts,
        "nodes": concepts,
        "score": float(candidate.get("score", candidate.get("final_score", 0.0)) or 0.0),
        "source": _clean_text(candidate.get("source", candidate.get("source_kind", ""))),
        "required_nodes": required_nodes,
        "blocked_nodes": blocked_nodes,
        "critical_nodes": critical_nodes[:6],
        "temporal_tunnels": tunnels[:4],
    }


@dataclass(slots=True)
class JudgeConfig:
    mode: str = "assist"
    provider: str = _clean_text(os.getenv("TMCRA_JUDGE_PROVIDER", "llm_assist")) or "llm_assist"
    profile: LLMProfile = field(
        default_factory=lambda: LLMProfile(
            name=_clean_text(os.getenv("TMCRA_JUDGE_PROFILE", "qwen3b_judge")),
            model=_clean_text(os.getenv("TMCRA_JUDGE_MODEL", "Qwen/Qwen2.5-3B-Instruct")),
            base_url=_clean_text(os.getenv("TMCRA_JUDGE_BASE_URL", "")),
            api_key=_clean_text(os.getenv("TMCRA_JUDGE_API_KEY", "")),
            system_prompt="You are a TMCRA decision layer. Return strict JSON only and never answer the user directly.",
            timeout_seconds=float(os.getenv("TMCRA_JUDGE_TIMEOUT_SECONDS", "1.2") or 1.2),
            temperature=float(os.getenv("TMCRA_JUDGE_TEMPERATURE", "0.0") or 0.0),
            max_tokens=int(os.getenv("TMCRA_JUDGE_MAX_TOKENS", "256") or 256),
        )
    )
    min_confidence: float = 0.55
    max_slot_groups: int = 8
    max_history_per_slot: int = 3
    max_path_candidates: int = 6
    max_selected_slots: int = 4
    max_selected_paths: int = 3
    trigger_on_summary: bool = True
    trigger_on_path: bool = True
    trigger_on_current_with_history: bool = True
    manifest_path: str = _clean_text(os.getenv("TMCRA_JUDGE_MANIFEST_PATH", ""))
    history_model_path: str = _clean_text(os.getenv("TMCRA_JUDGE_HISTORY_MODEL_PATH", ""))
    slot_model_path: str = _clean_text(os.getenv("TMCRA_JUDGE_SLOT_MODEL_PATH", ""))
    path_model_path: str = _clean_text(os.getenv("TMCRA_JUDGE_PATH_MODEL_PATH", ""))
    teacher_audit_mode: str = _clean_text(os.getenv("TMCRA_JUDGE_TEACHER_AUDIT_MODE", "")) or "disabled"
    judge_bypass_allowed: bool = _env_bool("TMCRA_JUDGE_BYPASS_ALLOWED", True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "provider": self.provider,
            "profile": {
                "name": self.profile.name,
                "model": self.profile.model,
                "base_url": self.profile.base_url,
                "timeout_seconds": float(self.profile.timeout_seconds),
                "temperature": float(self.profile.temperature),
                "max_tokens": int(self.profile.max_tokens),
            },
            "min_confidence": round(float(self.min_confidence), 6),
            "max_slot_groups": int(self.max_slot_groups),
            "max_history_per_slot": int(self.max_history_per_slot),
            "max_path_candidates": int(self.max_path_candidates),
            "max_selected_slots": int(self.max_selected_slots),
            "max_selected_paths": int(self.max_selected_paths),
            "trigger_on_summary": bool(self.trigger_on_summary),
            "trigger_on_path": bool(self.trigger_on_path),
            "trigger_on_current_with_history": bool(self.trigger_on_current_with_history),
            "manifest_path": self.manifest_path,
            "history_model_path": self.history_model_path,
            "slot_model_path": self.slot_model_path,
            "path_model_path": self.path_model_path,
            "teacher_audit_mode": self.teacher_audit_mode,
            "judge_bypass_allowed": bool(self.judge_bypass_allowed),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "JudgeConfig":
        payload = dict(payload or {})
        profile_payload = dict(payload.get("profile") or {})
        profile = LLMProfile(
            name=_clean_text(profile_payload.get("name", "")) or _clean_text(os.getenv("TMCRA_JUDGE_PROFILE", "qwen3b_judge")),
            model=_clean_text(profile_payload.get("model", "")) or _clean_text(os.getenv("TMCRA_JUDGE_MODEL", "Qwen/Qwen2.5-3B-Instruct")),
            base_url=_clean_text(profile_payload.get("base_url", "")),
            api_key=_clean_text(profile_payload.get("api_key", "")),
            system_prompt=_clean_text(profile_payload.get("system_prompt", "")) or "You are a TMCRA decision layer. Return strict JSON only and never answer the user directly.",
            timeout_seconds=float(profile_payload.get("timeout_seconds", os.getenv("TMCRA_JUDGE_TIMEOUT_SECONDS", "1.2")) or 1.2),
            temperature=float(profile_payload.get("temperature", os.getenv("TMCRA_JUDGE_TEMPERATURE", "0.0")) or 0.0),
            max_tokens=int(profile_payload.get("max_tokens", os.getenv("TMCRA_JUDGE_MAX_TOKENS", "256")) or 256),
        )
        return cls(
            mode=_clean_text(payload.get("mode", "")) or "assist",
            provider=_clean_text(payload.get("provider", "")) or "llm_assist",
            profile=profile,
            min_confidence=float(payload.get("min_confidence", 0.55) or 0.55),
            max_slot_groups=int(payload.get("max_slot_groups", 8) or 8),
            max_history_per_slot=int(payload.get("max_history_per_slot", 3) or 3),
            max_path_candidates=int(payload.get("max_path_candidates", 6) or 6),
            max_selected_slots=int(payload.get("max_selected_slots", 4) or 4),
            max_selected_paths=int(payload.get("max_selected_paths", 3) or 3),
            trigger_on_summary=bool(payload.get("trigger_on_summary", True)),
            trigger_on_path=bool(payload.get("trigger_on_path", True)),
            trigger_on_current_with_history=bool(payload.get("trigger_on_current_with_history", True)),
            manifest_path=_clean_text(payload.get("manifest_path", "")),
            history_model_path=_clean_text(payload.get("history_model_path", "")),
            slot_model_path=_clean_text(payload.get("slot_model_path", "")),
            path_model_path=_clean_text(payload.get("path_model_path", "")),
            teacher_audit_mode=_clean_text(payload.get("teacher_audit_mode", "")) or "disabled",
            judge_bypass_allowed=bool(payload.get("judge_bypass_allowed", True)),
        )


@dataclass(slots=True)
class JudgmentSlotCandidate:
    slot_key: str
    category: str
    score: float = 0.0
    conflict_state: str = "none"
    active_record: Dict[str, Any] | None = None
    previous_record: Dict[str, Any] | None = None
    history_chain: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_view(cls, view: ResolvedSlotView, *, score: float = 0.0, max_history: int = 3) -> "JudgmentSlotCandidate":
        return cls(
            slot_key=view.slot_key,
            category=view.category,
            score=float(score),
            conflict_state=view.conflict_state,
            active_record=_record_payload(view.active_record),
            previous_record=_record_payload(view.previous_record),
            history_chain=[item.to_dict() for item in list(view.historical_chain)[: max(1, int(max_history))]],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_key": self.slot_key,
            "category": self.category,
            "score": round(float(self.score), 6),
            "conflict_state": self.conflict_state,
            "active_record": dict(self.active_record or {}) or None,
            "previous_record": dict(self.previous_record or {}) or None,
            "history_chain": [dict(item) for item in self.history_chain],
        }


@dataclass(slots=True)
class JudgmentRequest:
    query: str
    answer_mode: str
    intent: Dict[str, Any]
    temporal_hints: List[str] = field(default_factory=list)
    query_kind_tags: List[str] = field(default_factory=list)
    required_nodes: List[str] = field(default_factory=list)
    blocked_nodes: List[str] = field(default_factory=list)
    baseline_selected_slots: List[str] = field(default_factory=list)
    coverage_budget: int = 0
    required_categories: List[str] = field(default_factory=list)
    must_keep_slot_keys: List[str] = field(default_factory=list)
    slot_candidates: List[JudgmentSlotCandidate] = field(default_factory=list)
    path_candidates: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "answer_mode": self.answer_mode,
            "intent": dict(self.intent),
            "temporal_hints": list(self.temporal_hints),
            "query_kind_tags": list(self.query_kind_tags),
            "required_nodes": list(self.required_nodes),
            "blocked_nodes": list(self.blocked_nodes),
            "baseline_selected_slots": list(self.baseline_selected_slots),
            "coverage_budget": int(self.coverage_budget),
            "required_categories": list(self.required_categories),
            "must_keep_slot_keys": list(self.must_keep_slot_keys),
            "slot_candidates": [item.to_dict() for item in self.slot_candidates],
            "path_candidates": [dict(item) for item in self.path_candidates],
        }


@dataclass(slots=True)
class JudgmentSlotDirective:
    slot_key: str
    mode: str = "current"
    selected_memory_ids: List[str] = field(default_factory=list)
    timeline_memory_ids: List[str] = field(default_factory=list)
    compare_pair: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "JudgmentSlotDirective":
        compare_pair = payload.get("compare_pair", {})
        if not isinstance(compare_pair, dict):
            compare_pair = {}
        if not compare_pair:
            compare_pair = {
                "current_memory_id": _clean_text(payload.get("current_memory_id", "")),
                "previous_memory_id": _clean_text(payload.get("previous_memory_id", "")),
            }
        compare_pair = {
            "current_memory_id": _clean_text(compare_pair.get("current_memory_id", "")),
            "previous_memory_id": _clean_text(compare_pair.get("previous_memory_id", "")),
        }
        return cls(
            slot_key=_clean_text(payload.get("slot_key", "")),
            mode=_clean_text(payload.get("mode", "")) or "current",
            selected_memory_ids=_dedupe_strings(payload.get("selected_memory_ids", []) or []),
            timeline_memory_ids=_dedupe_strings(payload.get("timeline_memory_ids", []) or []),
            compare_pair=compare_pair,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_key": self.slot_key,
            "mode": self.mode,
            "selected_memory_ids": list(self.selected_memory_ids),
            "timeline_memory_ids": list(self.timeline_memory_ids),
            "compare_pair": {
                "current_memory_id": _clean_text(self.compare_pair.get("current_memory_id", "")),
                "previous_memory_id": _clean_text(self.compare_pair.get("previous_memory_id", "")),
            },
        }


@dataclass(slots=True)
class JudgmentDecision:
    history_kind: str = "none"
    slot_mode: str = ""
    selected_slot_keys: List[str] = field(default_factory=list)
    selected_memory_ids: List[str] = field(default_factory=list)
    timeline_memory_ids: List[str] = field(default_factory=list)
    compare_pairs: List[Dict[str, str]] = field(default_factory=list)
    slot_directives: List[JudgmentSlotDirective] = field(default_factory=list)
    coverage_budget: int = 0
    required_categories: List[str] = field(default_factory=list)
    must_keep_slot_keys: List[str] = field(default_factory=list)
    dropped_slots: List[str] = field(default_factory=list)
    drop_reasons: Dict[str, str] = field(default_factory=dict)
    selected_path_indices: List[int] = field(default_factory=list)
    path_output_mode: str = ""
    conflict_state: str = "none"
    requires_temporal_reasoning: bool = False
    requires_path_reasoning: bool = False
    confidence: float = 0.0
    decision_valid: bool = False

    @classmethod
    def from_dict(cls, payload: Dict[str, Any], *, fallback_mode: str = "current") -> "JudgmentDecision":
        directives = [
            JudgmentSlotDirective.from_dict(item)
            for item in payload.get("slot_directives", []) or []
            if isinstance(item, dict) and _clean_text(item.get("slot_key", ""))
        ]
        selected_slot_keys = _dedupe_strings(payload.get("selected_slot_keys", []) or [])
        if directives and not selected_slot_keys:
            selected_slot_keys = [item.slot_key for item in directives if item.slot_key and item.mode != "omit"]
        compare_pairs = [
            {
                "slot_key": _clean_text(item.get("slot_key", "")),
                "current_memory_id": _clean_text(item.get("current_memory_id", "")),
                "previous_memory_id": _clean_text(item.get("previous_memory_id", "")),
            }
            for item in payload.get("compare_pairs", []) or []
            if isinstance(item, dict)
        ]
        if directives and not compare_pairs:
            compare_pairs = [
                {
                    "slot_key": item.slot_key,
                    "current_memory_id": _clean_text(item.compare_pair.get("current_memory_id", "")),
                    "previous_memory_id": _clean_text(item.compare_pair.get("previous_memory_id", "")),
                }
                for item in directives
                if item.mode == "compare"
                and (
                    _clean_text(item.compare_pair.get("current_memory_id", ""))
                    or _clean_text(item.compare_pair.get("previous_memory_id", ""))
                )
            ]
        selected_memory_ids = _dedupe_strings(payload.get("selected_memory_ids", []) or [])
        if directives and not selected_memory_ids:
            selected_memory_ids = _dedupe_strings(
                memory_id
                for directive in directives
                for memory_id in [*directive.selected_memory_ids, *directive.timeline_memory_ids]
            )
        timeline_memory_ids = _dedupe_strings(payload.get("timeline_memory_ids", []) or [])
        if directives and not timeline_memory_ids:
            timeline_memory_ids = _dedupe_strings(memory_id for directive in directives for memory_id in directive.timeline_memory_ids)
        return cls(
            history_kind=_clean_text(payload.get("history_kind", "")) or "none",
            slot_mode=_clean_text(payload.get("slot_mode", "")) or fallback_mode,
            selected_slot_keys=selected_slot_keys,
            selected_memory_ids=selected_memory_ids,
            timeline_memory_ids=timeline_memory_ids,
            compare_pairs=compare_pairs,
            slot_directives=directives,
            coverage_budget=max(0, int(payload.get("coverage_budget", 0) or 0)),
            required_categories=_dedupe_strings(payload.get("required_categories", []) or []),
            must_keep_slot_keys=_dedupe_strings(payload.get("must_keep_slot_keys", []) or []),
            dropped_slots=_dedupe_strings(payload.get("dropped_slots", []) or []),
            drop_reasons={
                _clean_text(key): _clean_text(value)
                for key, value in dict(payload.get("drop_reasons", {}) or {}).items()
                if _clean_text(key)
            },
            selected_path_indices=_dedupe_ints(payload.get("selected_path_indices", []) or []),
            path_output_mode=_clean_text(payload.get("path_output_mode", "")),
            conflict_state=_clean_text(payload.get("conflict_state", "")) or "none",
            requires_temporal_reasoning=bool(payload.get("requires_temporal_reasoning", False)),
            requires_path_reasoning=bool(payload.get("requires_path_reasoning", False)),
            confidence=max(0.0, min(1.0, float(payload.get("confidence", 0.0) or 0.0))),
            decision_valid=bool(payload.get("decision_valid", False)),
        )

    def directive_for_slot(self, slot_key: str) -> JudgmentSlotDirective | None:
        normalized = _normalize(slot_key)
        for item in self.slot_directives:
            if _normalize(item.slot_key) == normalized:
                return item
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "history_kind": self.history_kind,
            "slot_mode": self.slot_mode,
            "selected_slot_keys": list(self.selected_slot_keys),
            "selected_memory_ids": list(self.selected_memory_ids),
            "timeline_memory_ids": list(self.timeline_memory_ids),
            "compare_pairs": [dict(item) for item in self.compare_pairs],
            "slot_directives": [item.to_dict() for item in self.slot_directives],
            "coverage_budget": int(self.coverage_budget),
            "required_categories": list(self.required_categories),
            "must_keep_slot_keys": list(self.must_keep_slot_keys),
            "dropped_slots": list(self.dropped_slots),
            "drop_reasons": dict(self.drop_reasons),
            "selected_path_indices": list(self.selected_path_indices),
            "path_output_mode": self.path_output_mode,
            "conflict_state": self.conflict_state,
            "requires_temporal_reasoning": bool(self.requires_temporal_reasoning),
            "requires_path_reasoning": bool(self.requires_path_reasoning),
            "confidence": round(float(self.confidence), 6),
            "decision_valid": bool(self.decision_valid),
        }


@dataclass(slots=True)
class JudgmentTrace:
    enabled: bool = False
    mode: str = "disabled"
    provider: str = "llm_assist"
    triggered: bool = False
    trigger_reason: str = ""
    request: Dict[str, Any] = field(default_factory=dict)
    raw_response: str = ""
    decision: JudgmentDecision = field(default_factory=JudgmentDecision)
    llm_profile: Dict[str, Any] = field(default_factory=dict)
    teacher_agreement: float = 0.0
    rule_agreement: float = 0.0
    model_confidences: Dict[str, Any] = field(default_factory=dict)
    baseline_selected_slots: List[str] = field(default_factory=list)
    judge_selected_slots: List[str] = field(default_factory=list)
    coverage_preserved: bool = False
    coverage_changed: bool = False
    judge_bypassed: bool = False
    latency_seconds: float = 0.0
    token_usage: Dict[str, int] = field(default_factory=dict)
    fallback_reason: str = ""
    provider_init_error: str = ""
    provider_load_error: str = ""
    judge_error_type: str = ""
    judge_error_message: str = ""
    judge_bypass_reason: str = ""
    parse_error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "mode": self.mode,
            "provider": self.provider,
            "triggered": bool(self.triggered),
            "trigger_reason": self.trigger_reason,
            "request": dict(self.request),
            "raw_response": self.raw_response,
            "decision": self.decision.to_dict(),
            "llm_profile": dict(self.llm_profile),
            "teacher_agreement": round(float(self.teacher_agreement), 6),
            "rule_agreement": round(float(self.rule_agreement), 6),
            "model_confidences": dict(self.model_confidences),
            "baseline_selected_slots": list(self.baseline_selected_slots),
            "judge_selected_slots": list(self.judge_selected_slots),
            "coverage_preserved": bool(self.coverage_preserved),
            "coverage_changed": bool(self.coverage_changed),
            "judge_bypassed": bool(self.judge_bypassed),
            "latency_seconds": round(float(self.latency_seconds), 6),
            "token_usage": dict(self.token_usage),
            "fallback_reason": self.fallback_reason,
            "provider_init_error": self.provider_init_error,
            "provider_load_error": self.provider_load_error,
            "judge_error_type": self.judge_error_type,
            "judge_error_message": self.judge_error_message,
            "judge_bypass_reason": self.judge_bypass_reason,
            "parse_error": self.parse_error,
        }


def _exception_details(exc: Exception) -> tuple[str, str]:
    message = _clean_text(str(exc)) or exc.__class__.__name__
    lowered = _normalize(message)
    if isinstance(exc, TimeoutError) or "timeout" in lowered or "timed out" in lowered:
        return "timeout", message
    if "json" in lowered:
        return "invalid_json", message
    if "connection" in lowered or "refused" in lowered or "unavailable" in lowered:
        return "provider_unavailable", message
    return "runtime_error", message


class LightweightJudge:
    def __init__(self, config: JudgeConfig | None = None, *, client: Any | None = None) -> None:
        self.config = config or JudgeConfig()
        self.provider_name = _normalize(self.config.provider) or "llm_assist"
        self.client = None
        self.runtime_provider = None
        self.provider_init_error = ""
        self.provider_load_error = ""
        if self.provider_name == "tmcra_judge":
            if client is not None and hasattr(client, "predict"):
                self.runtime_provider = client
            else:
                self.runtime_provider = self._load_tmcra_provider()
        else:
            self.client = client
            if self.client is None and OpenAI is not None and self.config.profile.base_url and self.config.profile.model:
                try:
                    self.client = OpenAI(base_url=self.config.profile.base_url, api_key=self.config.profile.api_key or "EMPTY")
                except Exception as exc:
                    self.provider_init_error = f"{exc.__class__.__name__}: {_clean_text(exc)}"
                    self.client = None

    def evaluate(
        self,
        *,
        query: str,
        answer_mode: str,
        intent: QueryIntent,
        preview: SlotStateResolution,
        prior_paths: Sequence[Dict[str, Any]] = (),
        path_candidates: Sequence[Dict[str, Any]] | None = None,
        query_kind_tags: Sequence[str] = (),
        required_nodes: Sequence[str] = (),
        blocked_nodes: Sequence[str] = (),
    ) -> JudgmentTrace:
        trigger_reason = self._trigger_reason(
            query=query,
            intent=intent,
            preview=preview,
            path_candidates=path_candidates or prior_paths,
        )
        if not trigger_reason and any(marker in _normalize(query) for marker in _INACTIVE_QUERY_MARKERS):
            trigger_reason = "inactive_history_query"
        trace = JudgmentTrace(
            enabled=self.config.mode != "disabled",
            mode=self.config.mode,
            provider=self.provider_name,
            triggered=bool(trigger_reason),
            trigger_reason=trigger_reason,
            llm_profile=self._provider_profile(),
            provider_init_error=self.provider_init_error,
            provider_load_error=self.provider_load_error,
        )
        if self.config.mode == "disabled":
            trace.fallback_reason = "judge_disabled"
            trace.judge_bypassed = True
            trace.judge_bypass_reason = "judge_disabled"
            return trace
        if not trigger_reason:
            trace.fallback_reason = "judge_not_needed"
            trace.judge_bypassed = True
            trace.judge_bypass_reason = "judge_not_needed"
            return trace

        request = self._build_request(
            query=query,
            answer_mode=answer_mode,
            intent=intent,
            preview=preview,
            prior_paths=prior_paths,
            path_candidates=path_candidates,
            query_kind_tags=query_kind_tags,
            required_nodes=required_nodes,
            blocked_nodes=blocked_nodes,
        )
        trace.request = request.to_dict()
        trace.baseline_selected_slots = list(request.baseline_selected_slots)
        start = time.perf_counter()
        try:
            if self.provider_name == "tmcra_judge":
                if self.runtime_provider is None:
                    return self._bypass_or_raise(trace, "judge_provider_unavailable")
                raw, decision_payload, provider_meta = self._call_tmcra_provider(request=request, fallback_mode=intent.slot_mode)
                trace.raw_response = raw
                trace.teacher_agreement = float(provider_meta.get("teacher_agreement", 0.0) or 0.0)
                trace.rule_agreement = float(provider_meta.get("rule_agreement", 0.0) or 0.0)
                trace.model_confidences = dict(provider_meta.get("model_confidences", {}) or {})
                decision = JudgmentDecision.from_dict(decision_payload, fallback_mode=intent.slot_mode)
            else:
                if self.client is None:
                    return self._bypass_or_raise(trace, "judge_client_unavailable")
                raw, token_usage = self._call_llm(
                    messages=[
                        {"role": "system", "content": self.config.profile.system_prompt},
                        {"role": "user", "content": self._prompt_text(request)},
                    ]
                )
                trace.raw_response = _clean_text(raw)
                trace.token_usage = dict(token_usage or {})
                decision, parse_error = self._parse_decision_with_error(trace.raw_response, fallback_mode=intent.slot_mode)
                trace.parse_error = parse_error
                if parse_error and not decision.decision_valid:
                    trace.judge_error_type = "invalid_json"
                    trace.judge_error_message = parse_error
            decision = self._coerce_decision(query=query, intent=intent, request=request, decision=decision)
            trace.latency_seconds = time.perf_counter() - start
            if not decision.decision_valid:
                trace.fallback_reason = "judge_invalid_json" if trace.parse_error else "judge_invalid_decision"
            elif decision.confidence < float(self.config.min_confidence):
                decision.decision_valid = False
                trace.fallback_reason = "judge_low_confidence"
            trace.decision = decision
            trace.judge_selected_slots = list(decision.selected_slot_keys)
            baseline_keys = {_normalize(item) for item in request.baseline_selected_slots}
            judge_keys = {_normalize(item) for item in decision.selected_slot_keys}
            trace.coverage_preserved = not baseline_keys or baseline_keys.issubset(judge_keys)
            trace.coverage_changed = bool(judge_keys and judge_keys != baseline_keys)
            trace.judge_bypassed = not bool(decision.decision_valid)
            trace.judge_bypass_reason = trace.fallback_reason if trace.judge_bypassed else ""
            if trace.judge_bypassed and not bool(self.config.judge_bypass_allowed):
                raise RuntimeError(f"judge_fail_fast:{trace.fallback_reason or 'judge_bypassed'}")
        except Exception as exc:
            trace.latency_seconds = time.perf_counter() - start
            error_type, error_message = _exception_details(exc)
            trace.fallback_reason = f"judge_{error_type}"
            trace.judge_bypassed = True
            trace.judge_bypass_reason = trace.fallback_reason
            trace.judge_error_type = error_type
            trace.judge_error_message = error_message
            if not bool(self.config.judge_bypass_allowed):
                raise
        return trace

    def _bypass_or_raise(self, trace: JudgmentTrace, reason: str) -> JudgmentTrace:
        trace.fallback_reason = reason
        trace.judge_bypassed = True
        trace.judge_bypass_reason = reason
        trace.judge_error_type = reason.removeprefix("judge_")
        if reason == "judge_provider_unavailable" and self.provider_load_error and not trace.provider_load_error:
            trace.provider_load_error = self.provider_load_error
            trace.judge_error_message = self.provider_load_error
        if reason == "judge_client_unavailable" and self.provider_init_error and not trace.provider_init_error:
            trace.provider_init_error = self.provider_init_error
            trace.judge_error_message = self.provider_init_error
        if not bool(self.config.judge_bypass_allowed):
            raise RuntimeError(f"judge_fail_fast:{reason}")
        return trace

    def _provider_profile(self) -> Dict[str, Any]:
        if self.provider_name == "tmcra_judge":
            return {
                "provider": "tmcra_judge",
                "manifest_path": self.config.manifest_path,
                "history_model_path": self.config.history_model_path,
                "slot_model_path": self.config.slot_model_path,
                "path_model_path": self.config.path_model_path,
                "provider_load_error": self.provider_load_error,
            }
        return {
            "name": self.config.profile.name,
            "model": self.config.profile.model,
            "base_url": self.config.profile.base_url,
            "provider_init_error": self.provider_init_error,
        }

    def _load_tmcra_provider(self) -> Any | None:
        self.provider_load_error = ""
        try:
            from .judge_stack import TMCRAJudgeProvider
        except Exception as exc:
            self.provider_load_error = f"import_error:{exc.__class__.__name__}:{_clean_text(exc)}"
            return None
        try:
            if self.config.manifest_path:
                return TMCRAJudgeProvider.from_manifest(self.config.manifest_path)
            if self.config.history_model_path and self.config.slot_model_path and self.config.path_model_path:
                return TMCRAJudgeProvider(
                    history_model_path=self.config.history_model_path,
                    slot_model_path=self.config.slot_model_path,
                    path_model_path=self.config.path_model_path,
                )
            self.provider_load_error = "provider_config_missing"
        except Exception as exc:
            self.provider_load_error = f"provider_load_error:{exc.__class__.__name__}:{_clean_text(exc)}"
            return None
        return None

    def _call_tmcra_provider(self, *, request: JudgmentRequest, fallback_mode: str) -> tuple[str, Dict[str, Any], Dict[str, Any]]:
        result = self.runtime_provider.predict(request.to_dict(), fallback_mode=fallback_mode)
        decision_payload = dict(getattr(result, "decision", result) or {})
        provider_meta = {
            "teacher_agreement": getattr(result, "teacher_agreement", 0.0),
            "rule_agreement": getattr(result, "rule_agreement", 0.0),
            "model_confidences": getattr(result, "model_scores", {}),
        }
        return _clean_text(json.dumps(decision_payload, ensure_ascii=False)), decision_payload, provider_meta

    def _call_llm(self, *, messages: List[Dict[str, str]]) -> tuple[str, Dict[str, int]]:
        completion = self.client.chat.completions.create(
            model=self.config.profile.model,
            messages=messages,
            temperature=float(self.config.profile.temperature),
            max_tokens=int(self.config.profile.max_tokens),
            timeout=float(self.config.profile.timeout_seconds),
        )
        return _clean_text(completion.choices[0].message.content if completion.choices else ""), _completion_usage_dict(completion)

    def _build_request(
        self,
        *,
        query: str,
        answer_mode: str,
        intent: QueryIntent,
        preview: SlotStateResolution,
        prior_paths: Sequence[Dict[str, Any]],
        path_candidates: Sequence[Dict[str, Any]] | None,
        query_kind_tags: Sequence[str],
        required_nodes: Sequence[str],
        blocked_nodes: Sequence[str],
    ) -> JudgmentRequest:
        candidates: List[JudgmentSlotCandidate] = []
        for view in list(preview.views)[: max(1, int(self.config.max_slot_groups))]:
            view_score = 0.0
            if view.active_record is not None:
                view_score = max(view_score, float(view.active_record.score))
            if view.previous_record is not None:
                view_score = max(view_score, float(view.previous_record.score))
            for item in view.historical_chain:
                view_score = max(view_score, float(item.score))
            candidates.append(
                JudgmentSlotCandidate.from_view(view, score=view_score, max_history=max(1, int(self.config.max_history_per_slot)))
            )
        raw_path_candidates = list(path_candidates if path_candidates is not None else prior_paths)[: max(0, int(self.config.max_path_candidates))]
        path_preview = [_path_summary(item, path_index=index) for index, item in enumerate(raw_path_candidates)]
        tags = _dedupe_strings(
            [
                *list(query_kind_tags or []),
                intent.kind,
                intent.history_kind,
                intent.path_mode,
                intent.summary_mode,
            ]
        )
        baseline_selected_slots = [view.slot_key for view in preview.views if view.slot_key]
        if preview.selected_slots:
            baseline_selected_slots = _dedupe_strings([*preview.selected_slots, *baseline_selected_slots])
        required_categories = _dedupe_strings(
            [
                *list(intent.category_hints or []),
                *[
                    view.category
                    for view in preview.views
                    if view.slot_key in set(baseline_selected_slots)
                ],
            ]
        )
        must_keep_slot_keys = []
        if intent.kind == "summary" or intent.history_kind in {"previous", "compare", "timeline"}:
            must_keep_slot_keys = list(baseline_selected_slots)
        elif len(intent.category_hints or []) > 1:
            must_keep_slot_keys = list(baseline_selected_slots)
        elif baseline_selected_slots:
            must_keep_slot_keys = baseline_selected_slots[:1]
        return JudgmentRequest(
            query=query,
            answer_mode=answer_mode,
            intent=intent.to_dict(),
            temporal_hints=list(intent.temporal_hints),
            query_kind_tags=tags,
            required_nodes=_dedupe_strings(required_nodes),
            blocked_nodes=_dedupe_strings(blocked_nodes),
            baseline_selected_slots=baseline_selected_slots,
            coverage_budget=self._coverage_budget(intent=intent, preview=preview),
            required_categories=required_categories,
            must_keep_slot_keys=must_keep_slot_keys,
            slot_candidates=candidates,
            path_candidates=path_preview,
        )

    def _coerce_decision(
        self,
        *,
        query: str,
        intent: QueryIntent,
        request: JudgmentRequest,
        decision: JudgmentDecision,
    ) -> JudgmentDecision:
        if not decision.decision_valid:
            return decision
        if not self._should_force_current_only(query=query, intent=intent):
            return decision
        candidate_map = {item.slot_key: item for item in request.slot_candidates if item.slot_key}
        directives = list(decision.slot_directives)
        selected_slot_keys = list(decision.selected_slot_keys or request.baseline_selected_slots[:1])
        if not directives:
            directives = [JudgmentSlotDirective(slot_key=slot_key, mode="current") for slot_key in selected_slot_keys]
        coerced_directives: List[JudgmentSlotDirective] = []
        selected_memory_ids: List[str] = []
        for directive in directives:
            slot_key = _clean_text(directive.slot_key)
            if not slot_key or directive.mode == "omit":
                continue
            candidate = candidate_map.get(slot_key)
            current_memory_id = _clean_text(directive.compare_pair.get("current_memory_id", ""))
            if not current_memory_id and candidate and isinstance(candidate.active_record, dict):
                current_memory_id = _clean_text(candidate.active_record.get("memory_id", ""))
            if not current_memory_id:
                current_memory_id = next((memory_id for memory_id in directive.selected_memory_ids if _clean_text(memory_id)), "")
            selected_ids = [current_memory_id] if current_memory_id else []
            selected_memory_ids.extend(selected_ids)
            coerced_directives.append(
                JudgmentSlotDirective(
                    slot_key=slot_key,
                    mode="current",
                    selected_memory_ids=selected_ids,
                    timeline_memory_ids=[],
                    compare_pair={},
                )
            )
        return JudgmentDecision(
            history_kind="current",
            slot_mode="current",
            selected_slot_keys=[item.slot_key for item in coerced_directives],
            selected_memory_ids=_dedupe_strings(selected_memory_ids),
            timeline_memory_ids=[],
            compare_pairs=[],
            slot_directives=coerced_directives,
            coverage_budget=decision.coverage_budget,
            required_categories=list(decision.required_categories),
            must_keep_slot_keys=list(decision.must_keep_slot_keys),
            dropped_slots=list(decision.dropped_slots),
            drop_reasons=dict(decision.drop_reasons),
            selected_path_indices=list(decision.selected_path_indices),
            path_output_mode=decision.path_output_mode,
            conflict_state=decision.conflict_state,
            requires_temporal_reasoning=decision.requires_temporal_reasoning,
            requires_path_reasoning=decision.requires_path_reasoning,
            confidence=decision.confidence,
            decision_valid=decision.decision_valid,
        )

    def _should_force_current_only(self, *, query: str, intent: QueryIntent) -> bool:
        if intent.kind != "slot" or intent.history_kind != "current":
            return False
        lowered = _normalize(query)
        if any(marker in lowered for marker in _CURRENT_ONLY_HISTORY_MARKERS):
            return False
        if any(marker in lowered for marker in _INACTIVE_QUERY_MARKERS):
            return False
        return True

    def _coverage_budget(self, *, intent: QueryIntent, preview: SlotStateResolution) -> int:
        if intent.kind == "summary":
            return min(6, max(2, len(preview.views)))
        if intent.history_kind == "timeline":
            return min(6, max(2, len(preview.views)))
        if intent.history_kind in {"previous", "compare"}:
            return min(4, max(1, len(preview.views)))
        if intent.kind == "path":
            return 0
        return min(2, max(1, len(preview.views)))

    def _prompt_text(self, request: JudgmentRequest) -> str:
        return json.dumps(
            {
                "task": "Select grounded slot outputs and optional path reranking for TMCRA. Return exactly one JSON object and no prose.",
                "rules": [
                    "Use only supplied slot candidates and path summaries.",
                    "Never invent memory ids, slot keys, or path indices.",
                    "Slot directives decide whether each slot should be current, previous, compare, timeline, or omit.",
                    "If the query is a summary, multiple slots may be selected with different modes.",
                    "If evidence is ambiguous, set conflict_state instead of guessing.",
                    "Path selection can only reorder or keep existing path candidates.",
                ],
                "schema": {
                    "history_kind": "none|current|previous|compare|timeline|summary|path",
                    "slot_mode": "current|previous|compare|timeline|summary|path",
                    "selected_slot_keys": ["slot_key"],
                    "selected_memory_ids": ["memory_id"],
                    "timeline_memory_ids": ["memory_id"],
                    "compare_pairs": [{"slot_key": "slot", "current_memory_id": "id", "previous_memory_id": "id"}],
                    "slot_directives": [
                        {
                            "slot_key": "slot_key",
                            "mode": "current|previous|compare|timeline|omit",
                            "selected_memory_ids": ["memory_id"],
                            "timeline_memory_ids": ["memory_id"],
                            "compare_pair": {"current_memory_id": "id", "previous_memory_id": "id"},
                        }
                    ],
                    "coverage_budget": 0,
                    "required_categories": ["goal|constraint|preference|terminology|stage_state"],
                    "must_keep_slot_keys": ["slot_key"],
                    "dropped_slots": ["slot_key"],
                    "drop_reasons": {"slot_key": "reason"},
                    "selected_path_indices": [0],
                    "path_output_mode": "single|multi|constrained|counterfactual|temporal_path|state_evolution_path",
                    "conflict_state": "none|ambiguous|ambiguous_active|missing",
                    "requires_temporal_reasoning": True,
                    "requires_path_reasoning": False,
                    "confidence": 0.0,
                    "decision_valid": True,
                },
                "input": request.to_dict(),
            },
            ensure_ascii=False,
        )

    def _parse_decision(self, raw: str, *, fallback_mode: str) -> JudgmentDecision:
        decision, _ = self._parse_decision_with_error(raw, fallback_mode=fallback_mode)
        return decision

    def _parse_decision_with_error(self, raw: str, *, fallback_mode: str) -> tuple[JudgmentDecision, str]:
        payload, parse_error = self._extract_json_object_with_error(raw)
        if payload is None:
            return JudgmentDecision(slot_mode=fallback_mode, decision_valid=False), parse_error
        return JudgmentDecision.from_dict(payload, fallback_mode=fallback_mode), ""

    def _extract_json_object(self, raw: str) -> Dict[str, Any] | None:
        payload, _ = self._extract_json_object_with_error(raw)
        return payload

    def _extract_json_object_with_error(self, raw: str) -> tuple[Dict[str, Any] | None, str]:
        text = _clean_text(raw)
        if not text:
            return None, "empty_response"
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
        if fenced:
            text = _clean_text(fenced.group(1))
        try:
            payload = json.loads(text)
            return (payload if isinstance(payload, dict) else None), ("" if isinstance(payload, dict) else "top_level_not_object")
        except Exception as exc:
            top_level_error = f"{exc.__class__.__name__}: {_clean_text(exc)}"
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None, top_level_error or "json_object_not_found"
        try:
            payload = json.loads(match.group(0))
            return (payload if isinstance(payload, dict) else None), ("" if isinstance(payload, dict) else "embedded_json_not_object")
        except Exception as exc:
            return None, f"{top_level_error} | embedded:{exc.__class__.__name__}: {_clean_text(exc)}"

    def _trigger_reason(
        self,
        *,
        query: str,
        intent: QueryIntent,
        preview: SlotStateResolution,
        path_candidates: Sequence[Dict[str, Any]],
    ) -> str:
        if self.config.mode == "shadow":
            return "shadow_mode"
        lowered = _normalize(query)
        has_history = any(view.previous_record is not None or len(view.historical_chain) > 1 for view in preview.views)
        has_multiple_slots = len(preview.views) > 1
        has_multiple_categories = len({_normalize(view.category) for view in preview.views if _clean_text(view.category)}) > 1
        has_conflict = any(view.conflict_state != "none" for view in preview.views)
        has_paths = bool(path_candidates)

        if self.config.trigger_on_path and (intent.kind == "path" or intent.requires_path_reasoning):
            return "path_query"
        if self.config.trigger_on_summary and intent.kind == "summary" and (has_multiple_slots or has_multiple_categories or has_history):
            return "summary_multi_slot_or_history"
        if intent.history_kind in {"previous", "compare", "timeline"}:
            return f"history_kind:{intent.history_kind}"
        if self.config.trigger_on_current_with_history and intent.history_kind == "current" and has_history:
            return "current_with_history"
        if has_conflict:
            return "slot_conflict"
        if has_history:
            return "history_chain_present"
        if has_paths and any(marker in lowered for marker in ("path", "route", "why", "must go through", "如果没有", "必须经过")):
            return "path_marker"
        if any(marker in lowered for marker in _JUDGE_QUERY_MARKERS):
            return "query_marker"
        return ""
