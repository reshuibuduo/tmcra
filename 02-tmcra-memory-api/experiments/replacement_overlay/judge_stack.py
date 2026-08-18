from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

try:
    import torch
    from torch import nn
    import torch.nn.functional as F
    from torch.utils.data import DataLoader, Dataset
except Exception as exc:  # pragma: no cover
    raise RuntimeError(f"torch is required for TMCRA judge stack: {exc}")


FEATURE_SCHEMA_VERSION = "tmcra_judge_stack_v1"

_HISTORY_LABELS = ("none", "current", "previous", "compare", "timeline", "summary", "path")
_SLOT_MODE_LABELS = ("omit", "current", "previous", "compare", "timeline")
_PATH_MODE_LABELS = ("none", "single", "multi", "constrained", "counterfactual", "temporal_path", "state_evolution_path")
_CATEGORY_LABELS = ("goal", "constraint", "preference", "terminology", "stage_state", "fact", "path", "summary", "other")
_CONFLICT_LABELS = ("none", "ambiguous", "conflict", "multiple", "other")
_SOURCE_LABELS = ("prior", "graph", "forward", "reverse", "boundary", "path", "other")


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _normalize(value: object) -> str:
    return _clean_text(value).lower()


def _dedupe(items: Iterable[object]) -> List[str]:
    results: List[str] = []
    seen = set()
    for item in items:
        text = _clean_text(item)
        if not text:
            continue
        key = _normalize(text)
        if key in seen:
            continue
        seen.add(key)
        results.append(text)
    return results


def _hash01(value: object) -> float:
    text = _normalize(value)
    if not text:
        return 0.0
    digest = hashlib.md5(text.encode("utf-8", errors="ignore")).digest()
    return int.from_bytes(digest[:4], "big") / float(2**32)


def _tokenize(value: object) -> List[str]:
    text = _normalize(value)
    if not text:
        return []
    return _dedupe([*re.findall(r"[a-z0-9_.-]+", text), *[ch for ch in text if "\u4e00" <= ch <= "\u9fff"]])


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return float(default)


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _match_ratio(items: Sequence[str], expected: Sequence[str]) -> float:
    expected_norm = {_normalize(item) for item in expected if _clean_text(item)}
    if not expected_norm:
        return 0.0
    item_norm = {_normalize(item) for item in items if _clean_text(item)}
    hits = sum(1 for item in expected_norm if item in item_norm)
    return _safe_div(hits, len(expected_norm))


def _text_overlap(query: str, value: object) -> float:
    query_tokens = set(_tokenize(query))
    value_tokens = set(_tokenize(value))
    if not query_tokens or not value_tokens:
        return 0.0
    return _safe_div(len(query_tokens & value_tokens), len(value_tokens))


def _one_hot(label: str, labels: Sequence[str]) -> List[float]:
    normalized = _normalize(label)
    return [1.0 if normalized == _normalize(item) else 0.0 for item in labels]


def _history_label_id(value: str) -> int:
    normalized = _normalize(value)
    return _HISTORY_LABELS.index(normalized) if normalized in _HISTORY_LABELS else 0


def _slot_mode_id(value: str) -> int:
    normalized = _normalize(value)
    return _SLOT_MODE_LABELS.index(normalized) if normalized in _SLOT_MODE_LABELS else 0


def _path_mode_id(value: str) -> int:
    normalized = _normalize(value)
    return _PATH_MODE_LABELS.index(normalized) if normalized in _PATH_MODE_LABELS else 0


def _id_to_history(index: int) -> str:
    return _HISTORY_LABELS[max(0, min(len(_HISTORY_LABELS) - 1, int(index)))]


def _id_to_slot_mode(index: int) -> str:
    return _SLOT_MODE_LABELS[max(0, min(len(_SLOT_MODE_LABELS) - 1, int(index)))]


def _id_to_path_mode(index: int) -> str:
    return _PATH_MODE_LABELS[max(0, min(len(_PATH_MODE_LABELS) - 1, int(index)))]


def _normalize_history_label(value: object) -> str:
    normalized = _normalize(value)
    return normalized if normalized in _HISTORY_LABELS else "none"


def _normalize_slot_mode_label(value: object) -> str:
    normalized = _normalize(value)
    return normalized if normalized in _SLOT_MODE_LABELS else "omit"


def _normalize_path_mode_label(value: object) -> str:
    normalized = _normalize(value)
    return normalized if normalized in _PATH_MODE_LABELS else "none"


@dataclass(slots=True)
class HistoryJudgeLabel:
    history_kind: str = "none"
    requires_temporal_reasoning: bool = False
    requires_path_reasoning: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "history_kind": self.history_kind,
            "requires_temporal_reasoning": bool(self.requires_temporal_reasoning),
            "requires_path_reasoning": bool(self.requires_path_reasoning),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "HistoryJudgeLabel":
        payload = dict(payload or {})
        return cls(
            history_kind=_normalize_history_label(payload.get("history_kind", "")),
            requires_temporal_reasoning=bool(payload.get("requires_temporal_reasoning", False)),
            requires_path_reasoning=bool(payload.get("requires_path_reasoning", False)),
        )


@dataclass(slots=True)
class SlotDirectiveLabel:
    slot_key: str
    mode: str = "omit"
    selected_memory_ids: List[str] = field(default_factory=list)
    timeline_memory_ids: List[str] = field(default_factory=list)
    compare_pair: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_key": self.slot_key,
            "mode": self.mode,
            "selected_memory_ids": list(self.selected_memory_ids),
            "timeline_memory_ids": list(self.timeline_memory_ids),
            "compare_pair": dict(self.compare_pair),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "SlotDirectiveLabel":
        payload = dict(payload or {})
        compare_pair = payload.get("compare_pair", {})
        if not isinstance(compare_pair, dict):
            compare_pair = {}
        return cls(
            slot_key=_clean_text(payload.get("slot_key", "")),
            mode=_normalize_slot_mode_label(payload.get("mode", "")),
            selected_memory_ids=_dedupe(payload.get("selected_memory_ids", []) or []),
            timeline_memory_ids=_dedupe(payload.get("timeline_memory_ids", []) or []),
            compare_pair={
                "current_memory_id": _clean_text(compare_pair.get("current_memory_id", "")),
                "previous_memory_id": _clean_text(compare_pair.get("previous_memory_id", "")),
            },
        )


@dataclass(slots=True)
class PathRerankLabel:
    selected_path_indices: List[int] = field(default_factory=list)
    path_output_mode: str = "none"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selected_path_indices": [int(item) for item in self.selected_path_indices],
            "path_output_mode": self.path_output_mode,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "PathRerankLabel":
        payload = dict(payload or {})
        indices: List[int] = []
        for item in payload.get("selected_path_indices", []) or []:
            try:
                indices.append(int(item))
            except Exception:
                continue
        dedup = []
        seen = set()
        for item in indices:
            if item in seen or item < 0:
                continue
            seen.add(item)
            dedup.append(item)
        return cls(
            selected_path_indices=dedup,
            path_output_mode=_normalize_path_mode_label(payload.get("path_output_mode", "")),
        )


@dataclass(slots=True)
class JudgeTrainingExample:
    example_id: str
    query: str
    answer_mode: str
    intent_seed: Dict[str, Any] = field(default_factory=dict)
    temporal_hints: List[str] = field(default_factory=list)
    query_kind_tags: List[str] = field(default_factory=list)
    required_nodes: List[str] = field(default_factory=list)
    blocked_nodes: List[str] = field(default_factory=list)
    slot_candidates: List[Dict[str, Any]] = field(default_factory=list)
    path_candidates: List[Dict[str, Any]] = field(default_factory=list)
    history_label: HistoryJudgeLabel = field(default_factory=HistoryJudgeLabel)
    slot_directives_label: List[SlotDirectiveLabel] = field(default_factory=list)
    path_rerank_label: PathRerankLabel = field(default_factory=PathRerankLabel)
    coverage_label: str = "single_slot"
    min_slot_count: int = 1
    required_categories: List[str] = field(default_factory=list)
    must_keep_slot_keys: List[str] = field(default_factory=list)
    allow_omit: bool = True
    rule_gold: Dict[str, Any] = field(default_factory=dict)
    teacher_a_decision: Dict[str, Any] = field(default_factory=dict)
    teacher_b_review: Dict[str, Any] = field(default_factory=dict)
    agreement_score: float = 1.0
    source_split: str = "train"
    sample_weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def request_payload(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "answer_mode": self.answer_mode,
            "intent": dict(self.intent_seed),
            "temporal_hints": list(self.temporal_hints),
            "query_kind_tags": list(self.query_kind_tags),
            "required_nodes": list(self.required_nodes),
            "blocked_nodes": list(self.blocked_nodes),
            "baseline_selected_slots": list(self.rule_gold.get("selected_slot_keys", []) or self.must_keep_slot_keys),
            "coverage_budget": int(self.rule_gold.get("coverage_budget", max(self.min_slot_count, len(self.must_keep_slot_keys))) or max(self.min_slot_count, len(self.must_keep_slot_keys))),
            "required_categories": list(self.required_categories),
            "must_keep_slot_keys": list(self.must_keep_slot_keys),
            "allow_omit": bool(self.allow_omit),
            "slot_candidates": [dict(item) for item in self.slot_candidates],
            "path_candidates": [dict(item) for item in self.path_candidates],
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "example_id": self.example_id,
            "query": self.query,
            "answer_mode": self.answer_mode,
            "intent_seed": dict(self.intent_seed),
            "temporal_hints": list(self.temporal_hints),
            "query_kind_tags": list(self.query_kind_tags),
            "required_nodes": list(self.required_nodes),
            "blocked_nodes": list(self.blocked_nodes),
            "slot_candidates": [dict(item) for item in self.slot_candidates],
            "path_candidates": [dict(item) for item in self.path_candidates],
            "history_label": self.history_label.to_dict(),
            "slot_directives_label": [item.to_dict() for item in self.slot_directives_label],
            "path_rerank_label": self.path_rerank_label.to_dict(),
            "coverage_label": self.coverage_label,
            "min_slot_count": int(self.min_slot_count),
            "required_categories": list(self.required_categories),
            "must_keep_slot_keys": list(self.must_keep_slot_keys),
            "allow_omit": bool(self.allow_omit),
            "rule_gold": dict(self.rule_gold),
            "teacher_a_decision": dict(self.teacher_a_decision),
            "teacher_b_review": dict(self.teacher_b_review),
            "agreement_score": round(float(self.agreement_score), 6),
            "source_split": self.source_split,
            "sample_weight": round(float(self.sample_weight), 6),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "JudgeTrainingExample":
        return cls(
            example_id=_clean_text(payload.get("example_id", "")),
            query=_clean_text(payload.get("query", "")),
            answer_mode=_clean_text(payload.get("answer_mode", "")) or "transparent",
            intent_seed=dict(payload.get("intent_seed") or {}),
            temporal_hints=_dedupe(payload.get("temporal_hints", []) or []),
            query_kind_tags=_dedupe(payload.get("query_kind_tags", []) or []),
            required_nodes=_dedupe(payload.get("required_nodes", []) or []),
            blocked_nodes=_dedupe(payload.get("blocked_nodes", []) or []),
            slot_candidates=[dict(item) for item in payload.get("slot_candidates", []) or [] if isinstance(item, dict)],
            path_candidates=[dict(item) for item in payload.get("path_candidates", []) or [] if isinstance(item, dict)],
            history_label=HistoryJudgeLabel.from_dict(payload.get("history_label")),
            slot_directives_label=[SlotDirectiveLabel.from_dict(item) for item in payload.get("slot_directives_label", []) or [] if isinstance(item, dict)],
            path_rerank_label=PathRerankLabel.from_dict(payload.get("path_rerank_label")),
            coverage_label=_clean_text(payload.get("coverage_label", "")) or "single_slot",
            min_slot_count=max(1, int(payload.get("min_slot_count", 1) or 1)),
            required_categories=_dedupe(payload.get("required_categories", []) or []),
            must_keep_slot_keys=_dedupe(payload.get("must_keep_slot_keys", []) or []),
            allow_omit=bool(payload.get("allow_omit", True)),
            rule_gold=dict(payload.get("rule_gold") or {}),
            teacher_a_decision=dict(payload.get("teacher_a_decision") or {}),
            teacher_b_review=dict(payload.get("teacher_b_review") or {}),
            agreement_score=float(payload.get("agreement_score", 1.0) or 1.0),
            source_split=_clean_text(payload.get("source_split", "")) or "train",
            sample_weight=float(payload.get("sample_weight", 1.0) or 1.0),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(slots=True)
class TMCRAJudgeStackConfig:
    history_hidden_dim: int = 64
    slot_hidden_dim: int = 96
    path_hidden_dim: int = 96
    dropout: float = 0.1
    history_epochs: int = 12
    slot_epochs: int = 12
    path_epochs: int = 12
    batch_size: int = 32
    lr: float = 1e-3
    weight_decay: float = 1e-4
    min_confidence: float = 0.55
    max_selected_slots: int = 4
    max_selected_paths: int = 3
    current_slot_budget: int = 2
    history_slot_budget: int = 4
    summary_slot_budget: int = 6
    timeline_slot_budget: int = 6
    device: str = "auto"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "TMCRAJudgeStackConfig":
        return cls(**dict(payload or {}))


@dataclass(slots=True)
class TMCRAJudgeStackManifest:
    version: str = FEATURE_SCHEMA_VERSION
    config: Dict[str, Any] = field(default_factory=dict)
    history_model_path: str = ""
    slot_model_path: str = ""
    path_model_path: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "config": dict(self.config),
            "history_model_path": self.history_model_path,
            "slot_model_path": self.slot_model_path,
            "path_model_path": self.path_model_path,
            "metrics": dict(self.metrics),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "TMCRAJudgeStackManifest":
        return cls(**dict(payload or {}))


@dataclass(slots=True)
class TMCRAJudgeInferenceResult:
    decision: Dict[str, Any]
    history_confidence: float = 0.0
    slot_confidence: float = 0.0
    path_confidence: float = 0.0
    model_scores: Dict[str, Any] = field(default_factory=dict)
    teacher_agreement: float = 0.0
    rule_agreement: float = 0.0
    provider: str = "tmcra_judge"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": dict(self.decision),
            "history_confidence": round(float(self.history_confidence), 6),
            "slot_confidence": round(float(self.slot_confidence), 6),
            "path_confidence": round(float(self.path_confidence), 6),
            "model_scores": dict(self.model_scores),
            "teacher_agreement": round(float(self.teacher_agreement), 6),
            "rule_agreement": round(float(self.rule_agreement), 6),
            "provider": self.provider,
        }


def load_judge_training_examples(path: str | Path) -> List[JudgeTrainingExample]:
    example_path = Path(path)
    rows: List[JudgeTrainingExample] = []
    with example_path.open("r", encoding="utf-8-sig") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            rows.append(JudgeTrainingExample.from_dict(json.loads(line)))
    return rows


def write_judge_training_examples(path: str | Path, examples: Sequence[JudgeTrainingExample]) -> int:
    example_path = Path(path)
    example_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with example_path.open("w", encoding="utf-8") as handle:
        for item in examples:
            handle.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
            count += 1
    return count


def _query_flags(payload: Dict[str, Any]) -> List[float]:
    query = _clean_text(payload.get("query", ""))
    lowered = _normalize(query)
    tags = {_normalize(item) for item in payload.get("query_kind_tags", []) or []}
    intent = dict(payload.get("intent") or {})
    path_mode = _clean_text(intent.get("path_mode", ""))
    history_kind = _clean_text(intent.get("history_kind", ""))
    return [
        1.0 if "current" in lowered or "现在" in query else 0.0,
        1.0 if any(token in lowered for token in ("previous", "earlier", "before")) or any(token in query for token in ("之前", "以前", "原来")) else 0.0,
        1.0 if "compare" in lowered or "对比" in query else 0.0,
        1.0 if "timeline" in lowered or "变化过程" in query or "演变过程" in query else 0.0,
        1.0 if "summary" in lowered or "summarize" in lowered or "总结" in query else 0.0,
        1.0 if "path" in lowered or "route" in lowered or "路径" in query else 0.0,
        1.0 if "why" in lowered or "为什么" in query else 0.0,
        1.0 if "stable" in lowered or "稳定" in query else 0.0,
        1.0 if "multi" in lowered or "multiple" in lowered or "多条" in query else 0.0,
        1.0 if "without" in lowered or "if there is no" in lowered or "如果没有" in query else 0.0,
        1.0 if "through" in lowered or "via" in lowered or "经过" in query or "通过" in query else 0.0,
        1.0 if re.search(r"[\u4e00-\u9fff]", query) else 0.0,
        *_one_hot(history_kind, _HISTORY_LABELS),
        *_one_hot(path_mode, _PATH_MODE_LABELS),
        1.0 if "summary" in tags else 0.0,
        1.0 if "path" in tags else 0.0,
        1.0 if "history" in tags else 0.0,
    ]


def _intent_features(payload: Dict[str, Any]) -> List[float]:
    intent = dict(payload.get("intent") or {})
    category_hints = list(intent.get("category_hints", []) or [])
    entity_hints = list(intent.get("entity_hints", []) or [])
    temporal_hints = list(payload.get("temporal_hints", []) or []) or list(intent.get("temporal_hints", []) or [])
    return [
        _safe_div(len(category_hints), 4.0),
        _safe_div(len(entity_hints), 4.0),
        _safe_div(len(temporal_hints), 4.0),
        1.0 if intent.get("requires_temporal_resolution", False) else 0.0,
        1.0 if intent.get("requires_state_resolution", False) else 0.0,
        1.0 if intent.get("requires_path_reasoning", False) else 0.0,
    ]


def build_history_feature_vector(payload: Dict[str, Any]) -> List[float]:
    slot_candidates = [dict(item) for item in payload.get("slot_candidates", []) or [] if isinstance(item, dict)]
    path_candidates = [dict(item) for item in payload.get("path_candidates", []) or [] if isinstance(item, dict)]
    with_previous = sum(1 for item in slot_candidates if item.get("previous_record"))
    with_conflict = sum(1 for item in slot_candidates if _normalize(item.get("conflict_state", "")) not in {"", "none"})
    history_nodes = sum(len(item.get("history_chain", []) or []) for item in slot_candidates)
    candidate_scores = [float(item.get("score", 0.0) or 0.0) for item in slot_candidates]
    path_scores = [float(item.get("score", 0.0) or 0.0) for item in path_candidates]
    return [
        *_query_flags(payload),
        *_intent_features(payload),
        _safe_div(len(slot_candidates), 8.0),
        _safe_div(with_previous, max(1, len(slot_candidates))),
        _safe_div(with_conflict, max(1, len(slot_candidates))),
        _safe_div(history_nodes, max(1, len(slot_candidates) * 3)),
        max(candidate_scores) if candidate_scores else 0.0,
        sum(candidate_scores) / max(1, len(candidate_scores)),
        _safe_div(len(path_candidates), 6.0),
        max(path_scores) if path_scores else 0.0,
        _safe_div(len(payload.get("required_nodes", []) or []), 4.0),
        _safe_div(len(payload.get("blocked_nodes", []) or []), 4.0),
    ]


def build_slot_feature_vector(
    payload: Dict[str, Any],
    slot_candidate: Dict[str, Any],
    *,
    slot_index: int = 0,
    include_coverage_features: bool = True,
) -> List[float]:
    active_record = dict(slot_candidate.get("active_record") or {})
    previous_record = dict(slot_candidate.get("previous_record") or {})
    history_chain = [dict(item) for item in slot_candidate.get("history_chain", []) or [] if isinstance(item, dict)]
    history_scores = [float(item.get("score", 0.0) or 0.0) for item in history_chain]
    required_categories = {_normalize(item) for item in payload.get("required_categories", []) or [] if _clean_text(item)}
    must_keep_slot_keys = {_normalize(item) for item in payload.get("must_keep_slot_keys", []) or [] if _clean_text(item)}
    baseline_selected_slots = {_normalize(item) for item in payload.get("baseline_selected_slots", []) or [] if _clean_text(item)}
    coverage_budget = max(0, int(payload.get("coverage_budget", 0) or 0))
    category = _clean_text(slot_candidate.get("category", "")) or "other"
    if _normalize(category) not in {item for item in _CATEGORY_LABELS}:
        category = "other"
    slot_key = _clean_text(slot_candidate.get("slot_key", ""))
    conflict_state = _clean_text(slot_candidate.get("conflict_state", "")) or "none"
    if _normalize(conflict_state) not in {item for item in _CONFLICT_LABELS}:
        conflict_state = "other"
    core_features = [
        *_query_flags(payload),
        float(slot_index) / 8.0,
        _clamp01(slot_candidate.get("score", 0.0)),
        1.0 if active_record else 0.0,
        1.0 if previous_record else 0.0,
        _safe_div(len(history_chain), 3.0),
        _clamp01(active_record.get("score", 0.0)),
        _clamp01(previous_record.get("score", 0.0)),
        max(history_scores) if history_scores else 0.0,
        sum(history_scores) / max(1, len(history_scores)),
        _safe_div(abs(int(active_record.get("turn_index", 0) or 0) - int(previous_record.get("turn_index", 0) or 0)), 32.0),
        _text_overlap(payload.get("query", ""), slot_key),
        _text_overlap(payload.get("query", ""), active_record.get("value", "")),
        _text_overlap(payload.get("query", ""), previous_record.get("value", "")),
        _hash01(slot_key),
    ]
    coverage_features = [
        _safe_div(coverage_budget, 6.0),
        _safe_div(len(required_categories), 5.0),
        1.0 if _normalize(category) in required_categories else 0.0,
        1.0 if _normalize(slot_key) in must_keep_slot_keys else 0.0,
        1.0 if _normalize(slot_key) in baseline_selected_slots else 0.0,
    ]
    category_features = list(_one_hot(category, _CATEGORY_LABELS))
    conflict_features = list(_one_hot(conflict_state, _CONFLICT_LABELS))
    results = [*core_features]
    if include_coverage_features:
        results.extend(coverage_features)
    results.extend(category_features)
    results.extend(conflict_features)
    return results


def build_path_request_feature_vector(payload: Dict[str, Any]) -> List[float]:
    path_candidates = [dict(item) for item in payload.get("path_candidates", []) or [] if isinstance(item, dict)]
    required_nodes = list(payload.get("required_nodes", []) or [])
    blocked_nodes = list(payload.get("blocked_nodes", []) or [])
    tunnel_count = sum(len(item.get("temporal_tunnels", []) or []) for item in path_candidates)
    return [
        *_query_flags(payload),
        *_intent_features(payload),
        _safe_div(len(path_candidates), 6.0),
        _safe_div(len(required_nodes), 4.0),
        _safe_div(len(blocked_nodes), 4.0),
        _safe_div(tunnel_count, max(1, len(path_candidates) * 3)),
    ]


def build_path_candidate_feature_vector(payload: Dict[str, Any], candidate: Dict[str, Any], *, candidate_index: int = 0) -> List[float]:
    concepts = [str(item) for item in candidate.get("concepts", []) or candidate.get("nodes", []) or [] if _clean_text(item)]
    required_nodes = [str(item) for item in payload.get("required_nodes", []) or [] if _clean_text(item)]
    blocked_nodes = [str(item) for item in payload.get("blocked_nodes", []) or [] if _clean_text(item)]
    critical_nodes = [str(item) for item in candidate.get("critical_nodes", []) or [] if _clean_text(item)]
    normalized_concepts = {_normalize(item) for item in concepts}
    source = _clean_text(candidate.get("source", "")) or "other"
    if _normalize(source) not in {item for item in _SOURCE_LABELS}:
        source = "other"
    return [
        float(candidate_index) / 6.0,
        _clamp01(candidate.get("score", 0.0)),
        _safe_div(len(concepts), 6.0),
        _match_ratio(concepts, required_nodes),
        _match_ratio(concepts, critical_nodes),
        1.0 if any(_normalize(item) in normalized_concepts for item in blocked_nodes) else 0.0,
        _safe_div(len(candidate.get("temporal_tunnels", []) or []), 3.0),
        _safe_div(len(candidate.get("memory_ids", []) or []), 4.0),
        _text_overlap(payload.get("query", ""), " ".join(concepts)),
        _hash01(" ".join(concepts)),
        *_one_hot(source, _SOURCE_LABELS),
    ]


def _history_label_from_example(example: JudgeTrainingExample) -> int:
    return _history_label_id(example.history_label.history_kind)


def _slot_mode_for_candidate(example: JudgeTrainingExample, slot_key: str) -> str:
    target = _normalize(slot_key)
    for item in example.slot_directives_label:
        if _normalize(item.slot_key) == target:
            return item.mode
    selected_keys = {_normalize(item) for item in example.rule_gold.get("selected_slot_keys", []) or []}
    if target in selected_keys:
        fallback = _clean_text(example.rule_gold.get("slot_mode", "")) or example.history_label.history_kind
        if _normalize(fallback) in {"current", "previous", "compare", "timeline"}:
            return fallback
        return "current"
    return "omit"


def _path_targets_from_example(example: JudgeTrainingExample) -> List[int]:
    indices = list(example.path_rerank_label.selected_path_indices)
    if indices:
        return indices
    fallback = example.rule_gold.get("selected_path_indices", []) or []
    results: List[int] = []
    for item in fallback:
        try:
            results.append(int(item))
        except Exception:
            continue
    return results


def _path_mode_from_example(example: JudgeTrainingExample) -> str:
    if _normalize_path_mode_label(example.path_rerank_label.path_output_mode) != "none":
        return _normalize_path_mode_label(example.path_rerank_label.path_output_mode)
    fallback = _normalize_path_mode_label(example.rule_gold.get("path_output_mode", ""))
    if fallback != "none":
        return fallback
    return _normalize_path_mode_label(example.intent_seed.get("path_mode", ""))


def _coverage_budget_from_payload(payload: Dict[str, Any], *, fallback: int = 1) -> int:
    try:
        return max(1, int(payload.get("coverage_budget", fallback) or fallback))
    except Exception:
        return max(1, int(fallback))


class _HistoryDataset(Dataset):
    def __init__(self, examples: Sequence[JudgeTrainingExample]) -> None:
        self.rows = [
            (
                torch.tensor(build_history_feature_vector(item.request_payload()), dtype=torch.float32),
                torch.tensor(_history_label_from_example(item), dtype=torch.long),
                torch.tensor(float(item.sample_weight), dtype=torch.float32),
            )
            for item in examples
        ]

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int):
        return self.rows[index]


class _SlotDataset(Dataset):
    def __init__(self, examples: Sequence[JudgeTrainingExample]) -> None:
        rows = []
        for item in examples:
            payload = item.request_payload()
            for index, slot_candidate in enumerate(payload.get("slot_candidates", []) or []):
                rows.append(
                    (
                        torch.tensor(build_slot_feature_vector(payload, slot_candidate, slot_index=index), dtype=torch.float32),
                        torch.tensor(_slot_mode_id(_slot_mode_for_candidate(item, slot_candidate.get("slot_key", ""))), dtype=torch.long),
                        torch.tensor(float(item.sample_weight), dtype=torch.float32),
                    )
                )
        self.rows = rows

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int):
        return self.rows[index]


class _PathDataset(Dataset):
    def __init__(self, examples: Sequence[JudgeTrainingExample]) -> None:
        self.rows = []
        for item in examples:
            payload = item.request_payload()
            candidate_features = [
                build_path_candidate_feature_vector(payload, candidate, candidate_index=index)
                for index, candidate in enumerate(payload.get("path_candidates", []) or [])
            ]
            if not candidate_features:
                continue
            selected = set(_path_targets_from_example(item))
            self.rows.append(
                {
                    "request_features": torch.tensor(build_path_request_feature_vector(payload), dtype=torch.float32),
                    "candidate_features": torch.tensor(candidate_features, dtype=torch.float32),
                    "selected_mask": torch.tensor([1.0 if index in selected else 0.0 for index in range(len(candidate_features))], dtype=torch.float32),
                    "mode_label": torch.tensor(_path_mode_id(_path_mode_from_example(item)), dtype=torch.long),
                    "weight": torch.tensor(float(item.sample_weight), dtype=torch.float32),
                }
            )

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int):
        return self.rows[index]


def _path_collate(batch: Sequence[Dict[str, torch.Tensor]]) -> Dict[str, torch.Tensor]:
    max_candidates = max(int(item["candidate_features"].shape[0]) for item in batch)
    feat_dim = int(batch[0]["candidate_features"].shape[-1])
    candidate_features = torch.zeros(len(batch), max_candidates, feat_dim, dtype=torch.float32)
    candidate_mask = torch.zeros(len(batch), max_candidates, dtype=torch.bool)
    selected_mask = torch.zeros(len(batch), max_candidates, dtype=torch.float32)
    for row_index, item in enumerate(batch):
        count = int(item["candidate_features"].shape[0])
        candidate_features[row_index, :count] = item["candidate_features"]
        candidate_mask[row_index, :count] = True
        selected_mask[row_index, :count] = item["selected_mask"]
    return {
        "request_features": torch.stack([item["request_features"] for item in batch]),
        "candidate_features": candidate_features,
        "candidate_mask": candidate_mask,
        "selected_mask": selected_mask,
        "mode_label": torch.stack([item["mode_label"] for item in batch]),
        "weight": torch.stack([item["weight"] for item in batch]),
    }


class _MLPClassifier(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, output_dim: int, *, dropout: float = 0.1) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.SiLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.SiLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, output_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class _PathJudgeModel(nn.Module):
    def __init__(self, request_dim: int, candidate_dim: int, hidden_dim: int, *, dropout: float = 0.1) -> None:
        super().__init__()
        self.request_encoder = nn.Sequential(
            nn.Linear(request_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.SiLU(),
            nn.Dropout(dropout),
        )
        self.candidate_net = nn.Sequential(
            nn.Linear(hidden_dim + candidate_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.SiLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1),
        )
        self.mode_head = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.SiLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, len(_PATH_MODE_LABELS)),
        )

    def forward(self, request_features: torch.Tensor, candidate_features: torch.Tensor, candidate_mask: torch.Tensor) -> Dict[str, torch.Tensor]:
        request_hidden = self.request_encoder(request_features)
        expanded = request_hidden.unsqueeze(1).expand(-1, candidate_features.shape[1], -1)
        logits = self.candidate_net(torch.cat([expanded, candidate_features], dim=-1)).squeeze(-1)
        logits = logits.masked_fill(~candidate_mask, -1e4)
        return {"candidate_logits": logits, "mode_logits": self.mode_head(request_hidden)}


def _resolve_device(device: str) -> torch.device:
    if device != "auto":
        return torch.device(device)
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def _train_classifier(
    model: nn.Module,
    *,
    train_loader: DataLoader,
    val_loader: DataLoader | None,
    epochs: int,
    device: torch.device,
    lr: float,
    weight_decay: float,
) -> Dict[str, Any]:
    model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    best_state = None
    best_acc = -1.0
    history: List[Dict[str, Any]] = []
    for epoch in range(max(1, int(epochs))):
        model.train()
        train_loss = 0.0
        train_total = 0
        train_correct = 0
        for features, labels, weights in train_loader:
            features = features.to(device)
            labels = labels.to(device)
            weights = weights.to(device)
            logits = model(features)
            loss = F.cross_entropy(logits, labels, reduction="none")
            loss = (loss * weights).mean()
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            train_loss += float(loss.item()) * int(labels.shape[0])
            train_total += int(labels.shape[0])
            train_correct += int((logits.argmax(dim=-1) == labels).sum().item())
        val_loss = 0.0
        val_total = 0
        val_correct = 0
        model.eval()
        with torch.no_grad():
            for features, labels, weights in val_loader or []:
                features = features.to(device)
                labels = labels.to(device)
                weights = weights.to(device)
                logits = model(features)
                loss = F.cross_entropy(logits, labels, reduction="none")
                loss = (loss * weights).mean()
                val_loss += float(loss.item()) * int(labels.shape[0])
                val_total += int(labels.shape[0])
                val_correct += int((logits.argmax(dim=-1) == labels).sum().item())
        train_acc = _safe_div(train_correct, train_total)
        val_acc = _safe_div(val_correct, val_total) if val_total else train_acc
        history.append(
            {
                "epoch": epoch + 1,
                "train_loss": round(_safe_div(train_loss, train_total), 6),
                "train_accuracy": round(train_acc, 6),
                "val_loss": round(_safe_div(val_loss, val_total), 6) if val_total else round(_safe_div(train_loss, train_total), 6),
                "val_accuracy": round(val_acc, 6),
            }
        )
        if val_acc >= best_acc:
            best_acc = val_acc
            best_state = {key: value.detach().cpu() for key, value in model.state_dict().items()}
    if best_state is not None:
        model.load_state_dict(best_state)
    return {"best_accuracy": round(best_acc, 6), "history": history}


def _train_path_model(
    model: _PathJudgeModel,
    *,
    train_loader: DataLoader,
    val_loader: DataLoader | None,
    epochs: int,
    device: torch.device,
    lr: float,
    weight_decay: float,
) -> Dict[str, Any]:
    model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    best_state = None
    best_score = -1.0
    history: List[Dict[str, Any]] = []
    for epoch in range(max(1, int(epochs))):
        model.train()
        train_mode_correct = 0
        train_mode_total = 0
        train_sel_correct = 0.0
        train_sel_total = 0.0
        for batch in train_loader:
            request_features = batch["request_features"].to(device)
            candidate_features = batch["candidate_features"].to(device)
            candidate_mask = batch["candidate_mask"].to(device)
            selected_mask = batch["selected_mask"].to(device)
            mode_label = batch["mode_label"].to(device)
            weight = batch["weight"].to(device)
            outputs = model(request_features, candidate_features, candidate_mask)
            selection_loss = F.binary_cross_entropy_with_logits(outputs["candidate_logits"], selected_mask, reduction="none")
            selection_loss = (selection_loss * candidate_mask.float()).sum(dim=1) / candidate_mask.float().sum(dim=1).clamp_min(1.0)
            mode_loss = F.cross_entropy(outputs["mode_logits"], mode_label, reduction="none")
            loss = ((selection_loss + mode_loss) * weight).mean()
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            train_mode_correct += int((outputs["mode_logits"].argmax(dim=-1) == mode_label).sum().item())
            train_mode_total += int(mode_label.shape[0])
            train_sel_correct += float((((outputs["candidate_logits"] > 0).float() == selected_mask).float() * candidate_mask.float()).sum().item())
            train_sel_total += float(candidate_mask.float().sum().item())
        model.eval()
        val_mode_correct = 0
        val_mode_total = 0
        val_sel_correct = 0.0
        val_sel_total = 0.0
        with torch.no_grad():
            for batch in val_loader or []:
                request_features = batch["request_features"].to(device)
                candidate_features = batch["candidate_features"].to(device)
                candidate_mask = batch["candidate_mask"].to(device)
                selected_mask = batch["selected_mask"].to(device)
                mode_label = batch["mode_label"].to(device)
                outputs = model(request_features, candidate_features, candidate_mask)
                val_mode_correct += int((outputs["mode_logits"].argmax(dim=-1) == mode_label).sum().item())
                val_mode_total += int(mode_label.shape[0])
                val_sel_correct += float((((outputs["candidate_logits"] > 0).float() == selected_mask).float() * candidate_mask.float()).sum().item())
                val_sel_total += float(candidate_mask.float().sum().item())
        train_mode_acc = _safe_div(train_mode_correct, train_mode_total)
        val_mode_acc = _safe_div(val_mode_correct, val_mode_total) if val_mode_total else train_mode_acc
        train_sel_acc = _safe_div(train_sel_correct, train_sel_total)
        val_sel_acc = _safe_div(val_sel_correct, val_sel_total) if val_sel_total else train_sel_acc
        combined = (val_mode_acc + val_sel_acc) / 2.0
        history.append(
            {
                "epoch": epoch + 1,
                "train_mode_accuracy": round(train_mode_acc, 6),
                "train_selection_accuracy": round(train_sel_acc, 6),
                "val_mode_accuracy": round(val_mode_acc, 6),
                "val_selection_accuracy": round(val_sel_acc, 6),
            }
        )
        if combined >= best_score:
            best_score = combined
            best_state = {key: value.detach().cpu() for key, value in model.state_dict().items()}
    if best_state is not None:
        model.load_state_dict(best_state)
    return {"best_score": round(best_score, 6), "history": history}


def train_tmcra_judge_stack(
    examples: Sequence[JudgeTrainingExample],
    *,
    output_dir: str | Path,
    config: TMCRAJudgeStackConfig | None = None,
) -> TMCRAJudgeStackManifest:
    config = config or TMCRAJudgeStackConfig()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    device = _resolve_device(config.device)
    train_examples = [item for item in examples if _normalize(item.source_split) not in {"val", "validation"}]
    val_examples = [item for item in examples if _normalize(item.source_split) in {"val", "validation"}]
    if not train_examples:
        train_examples = list(examples)
    history_train = _HistoryDataset(train_examples)
    history_val = _HistoryDataset(val_examples) if val_examples else None
    slot_train = _SlotDataset(train_examples)
    slot_val = _SlotDataset(val_examples) if val_examples else None
    path_train = _PathDataset(train_examples)
    path_val = _PathDataset(val_examples) if val_examples else None

    sample_history = history_train[0][0] if len(history_train) else torch.zeros(1, dtype=torch.float32)
    sample_slot = slot_train[0][0] if len(slot_train) else torch.zeros(1, dtype=torch.float32)
    sample_path_req = path_train[0]["request_features"] if len(path_train) else torch.zeros(1, dtype=torch.float32)
    sample_path_cand = path_train[0]["candidate_features"][0] if len(path_train) else torch.zeros(1, dtype=torch.float32)

    history_model = _MLPClassifier(int(sample_history.shape[-1]), config.history_hidden_dim, len(_HISTORY_LABELS), dropout=config.dropout)
    slot_model = _MLPClassifier(int(sample_slot.shape[-1]), config.slot_hidden_dim, len(_SLOT_MODE_LABELS), dropout=config.dropout)
    path_model = _PathJudgeModel(int(sample_path_req.shape[-1]), int(sample_path_cand.shape[-1]), config.path_hidden_dim, dropout=config.dropout)

    history_metrics = _train_classifier(
        history_model,
        train_loader=DataLoader(history_train, batch_size=max(1, int(config.batch_size)), shuffle=True),
        val_loader=DataLoader(history_val, batch_size=max(1, int(config.batch_size))) if history_val is not None else None,
        epochs=config.history_epochs,
        device=device,
        lr=config.lr,
        weight_decay=config.weight_decay,
    )
    slot_metrics = _train_classifier(
        slot_model,
        train_loader=DataLoader(slot_train, batch_size=max(1, int(config.batch_size)), shuffle=True),
        val_loader=DataLoader(slot_val, batch_size=max(1, int(config.batch_size))) if slot_val is not None else None,
        epochs=config.slot_epochs,
        device=device,
        lr=config.lr,
        weight_decay=config.weight_decay,
    )
    path_metrics = _train_path_model(
        path_model,
        train_loader=DataLoader(path_train, batch_size=max(1, int(config.batch_size)), shuffle=True, collate_fn=_path_collate),
        val_loader=DataLoader(path_val, batch_size=max(1, int(config.batch_size)), collate_fn=_path_collate) if path_val is not None else None,
        epochs=config.path_epochs,
        device=device,
        lr=config.lr,
        weight_decay=config.weight_decay,
    )

    history_model_path = output_path / "tmcra_judge_history_v1.pt"
    slot_model_path = output_path / "tmcra_judge_slot_v1.pt"
    path_model_path = output_path / "tmcra_judge_path_v1.pt"
    torch.save({"state_dict": history_model.state_dict(), "input_dim": int(sample_history.shape[-1]), "labels": list(_HISTORY_LABELS), "feature_schema": FEATURE_SCHEMA_VERSION}, history_model_path)
    torch.save({"state_dict": slot_model.state_dict(), "input_dim": int(sample_slot.shape[-1]), "labels": list(_SLOT_MODE_LABELS), "feature_schema": FEATURE_SCHEMA_VERSION}, slot_model_path)
    torch.save({"state_dict": path_model.state_dict(), "request_dim": int(sample_path_req.shape[-1]), "candidate_dim": int(sample_path_cand.shape[-1]), "labels": list(_PATH_MODE_LABELS), "feature_schema": FEATURE_SCHEMA_VERSION}, path_model_path)
    manifest = TMCRAJudgeStackManifest(
        config=config.to_dict(),
        history_model_path=str(history_model_path),
        slot_model_path=str(slot_model_path),
        path_model_path=str(path_model_path),
        metrics={
            "history": history_metrics,
            "slot": slot_metrics,
            "path": path_metrics,
            "train_examples": len(train_examples),
            "val_examples": len(val_examples),
        },
    )
    manifest_path = output_path / "tmcra_judge_stack_manifest_v1.json"
    manifest_path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


class TMCRAJudgeProvider:
    def __init__(
        self,
        *,
        history_model_path: str | Path,
        slot_model_path: str | Path,
        path_model_path: str | Path,
        config: TMCRAJudgeStackConfig | None = None,
    ) -> None:
        self.config = config or TMCRAJudgeStackConfig()
        self.device = _resolve_device(self.config.device)
        history_payload = torch.load(Path(history_model_path), map_location="cpu")
        slot_payload = torch.load(Path(slot_model_path), map_location="cpu")
        path_payload = torch.load(Path(path_model_path), map_location="cpu")
        self.history_input_dim = int(history_payload["input_dim"])
        self.slot_input_dim = int(slot_payload["input_dim"])
        self.path_request_dim = int(path_payload["request_dim"])
        self.path_candidate_dim = int(path_payload["candidate_dim"])
        self.history_model = _MLPClassifier(self.history_input_dim, self.config.history_hidden_dim, len(history_payload.get("labels", _HISTORY_LABELS)), dropout=self.config.dropout)
        self.history_model.load_state_dict(history_payload["state_dict"])
        self.history_model.to(self.device).eval()
        self.slot_model = _MLPClassifier(self.slot_input_dim, self.config.slot_hidden_dim, len(slot_payload.get("labels", _SLOT_MODE_LABELS)), dropout=self.config.dropout)
        self.slot_model.load_state_dict(slot_payload["state_dict"])
        self.slot_model.to(self.device).eval()
        self.path_model = _PathJudgeModel(self.path_request_dim, self.path_candidate_dim, self.config.path_hidden_dim, dropout=self.config.dropout)
        self.path_model.load_state_dict(path_payload["state_dict"])
        self.path_model.to(self.device).eval()

    @classmethod
    def from_manifest(cls, manifest_path: str | Path) -> "TMCRAJudgeProvider":
        manifest = TMCRAJudgeStackManifest.from_dict(json.loads(Path(manifest_path).read_text(encoding="utf-8")))
        return cls(
            history_model_path=manifest.history_model_path,
            slot_model_path=manifest.slot_model_path,
            path_model_path=manifest.path_model_path,
            config=TMCRAJudgeStackConfig.from_dict(manifest.config),
        )

    def predict(self, payload: Dict[str, Any], *, fallback_mode: str = "current") -> TMCRAJudgeInferenceResult:
        history_vector = torch.tensor([self._align_feature_vector(build_history_feature_vector(payload), expected_dim=self.history_input_dim, feature_name="history")], dtype=torch.float32, device=self.device)
        with torch.no_grad():
            history_logits = self.history_model(history_vector)
            history_probs = torch.softmax(history_logits, dim=-1)[0]
        history_index = int(history_probs.argmax().item())
        history_kind = _id_to_history(history_index)
        history_confidence = float(history_probs[history_index].item())
        selection_fallback_mode = "current" if _normalize(fallback_mode) == "summary" else (_clean_text(fallback_mode) or "current")

        slot_candidates = [dict(item) for item in payload.get("slot_candidates", []) or [] if isinstance(item, dict)]
        intent = dict(payload.get("intent") or {})
        query_tags = {_normalize(item) for item in payload.get("query_kind_tags", []) or [] if _clean_text(item)}
        preserve_baseline = bool(
            _normalize(intent.get("kind", "")) == "summary"
            or history_kind in {"previous", "compare", "timeline"}
            or "combo" in " ".join(sorted(query_tags))
        )
        slot_rows = []
        slot_confidences = []
        for index, slot_candidate in enumerate(slot_candidates):
            feature_vector = torch.tensor([self._slot_feature_vector_for_model(payload, slot_candidate, slot_index=index)], dtype=torch.float32, device=self.device)
            with torch.no_grad():
                slot_logits = self.slot_model(feature_vector)
                slot_probs = torch.softmax(slot_logits, dim=-1)[0]
            slot_index_pred = int(slot_probs.argmax().item())
            slot_mode = _id_to_slot_mode(slot_index_pred)
            slot_confidence = float(slot_probs[slot_index_pred].item())
            slot_confidences.append(slot_confidence)
            directive = self._directive_from_slot(slot_candidate, self._coerce_slot_mode(slot_candidate, slot_mode, history_kind=history_kind, fallback_mode=selection_fallback_mode))
            directive["slot_score"] = round(float(slot_candidate.get("score", 0.0) or 0.0), 6)
            directive["confidence"] = round(slot_confidence, 6)
            directive["category"] = _clean_text(slot_candidate.get("category", "")) or "other"
            directive["baseline_rank"] = index
            directive["active_record"] = dict(slot_candidate.get("active_record") or {})
            directive["previous_record"] = dict(slot_candidate.get("previous_record") or {})
            directive["history_chain"] = [dict(item) for item in slot_candidate.get("history_chain", []) or [] if isinstance(item, dict)]
            slot_rows.append(directive)
        slot_rows.sort(key=lambda item: (item["mode"] != "omit", item.get("confidence", 0.0), item.get("slot_score", 0.0)), reverse=True)
        slot_rows_by_key = {_normalize(item.get("slot_key", "")): item for item in slot_rows if _clean_text(item.get("slot_key", ""))}
        baseline_selected_slots = _dedupe(payload.get("baseline_selected_slots", []) or [])
        required_categories = self._required_categories(payload, slot_rows=slot_rows, baseline_selected_slots=baseline_selected_slots)
        must_keep_slot_keys = self._must_keep_slot_keys(payload, baseline_selected_slots=baseline_selected_slots, preserve_baseline=preserve_baseline)
        coverage_budget = self._slot_budget(
            payload,
            history_kind=history_kind,
            fallback_mode=selection_fallback_mode,
            slot_count=len(slot_rows),
            required_categories=required_categories,
            baseline_selected_slots=baseline_selected_slots,
            preserve_baseline=preserve_baseline,
        )
        selected_rows, dropped_slots, drop_reasons = self._select_slot_rows(
            slot_rows,
            slot_rows_by_key=slot_rows_by_key,
            history_kind=history_kind,
            fallback_mode=selection_fallback_mode,
            coverage_budget=coverage_budget,
            required_categories=required_categories,
            must_keep_slot_keys=must_keep_slot_keys,
            baseline_selected_slots=baseline_selected_slots,
        )
        if not selected_rows and slot_rows:
            fallback = dict(slot_rows[0])
            fallback["mode"] = self._coerce_slot_mode(slot_candidates[0] if slot_candidates else {}, "current", history_kind=history_kind, fallback_mode=selection_fallback_mode)
            selected_rows = [fallback]

        slot_directives = [
            {
                "slot_key": item["slot_key"],
                "mode": item["mode"],
                "selected_memory_ids": list(item["selected_memory_ids"]),
                "timeline_memory_ids": list(item["timeline_memory_ids"]),
                "compare_pair": dict(item["compare_pair"]),
            }
            for item in selected_rows
        ]
        selected_memory_ids = _dedupe(
            memory_id
            for item in slot_directives
            for memory_id in [*item["selected_memory_ids"], *item["timeline_memory_ids"]]
        )
        timeline_memory_ids = _dedupe(
            memory_id
            for item in slot_directives
            for memory_id in item["timeline_memory_ids"]
        )
        compare_pairs: List[Dict[str, str]] = [
            {
                "slot_key": item["slot_key"],
                "current_memory_id": _clean_text(item["compare_pair"].get("current_memory_id", "")),
                "previous_memory_id": _clean_text(item["compare_pair"].get("previous_memory_id", "")),
            }
            for item in slot_directives
            if item["mode"] == "compare"
        ]
        selected_slot_keys = [item["slot_key"] for item in slot_directives]

        path_candidates = [dict(item) for item in payload.get("path_candidates", []) or [] if isinstance(item, dict)]
        selected_path_indices: List[int] = []
        path_confidence = 0.0
        requested_path_mode = _normalize_path_mode_label(dict(payload.get("intent") or {}).get("path_mode", ""))
        path_output_mode = requested_path_mode or "none"
        path_scores: List[float] = []
        if path_candidates:
            request_features = torch.tensor([self._align_feature_vector(build_path_request_feature_vector(payload), expected_dim=self.path_request_dim, feature_name="path_request")], dtype=torch.float32, device=self.device)
            candidate_features = torch.tensor([[
                self._align_feature_vector(
                    build_path_candidate_feature_vector(payload, candidate, candidate_index=index),
                    expected_dim=self.path_candidate_dim,
                    feature_name="path_candidate",
                )
                for index, candidate in enumerate(path_candidates)
            ]], dtype=torch.float32, device=self.device)
            candidate_mask = torch.ones(1, len(path_candidates), dtype=torch.bool, device=self.device)
            with torch.no_grad():
                outputs = self.path_model(request_features, candidate_features, candidate_mask)
                score_probs = torch.sigmoid(outputs["candidate_logits"])[0]
                mode_probs = torch.softmax(outputs["mode_logits"], dim=-1)[0]
            path_scores = [float(item) for item in score_probs.detach().cpu().tolist()]
            mode_index = int(mode_probs.argmax().item())
            predicted_path_mode = _id_to_path_mode(mode_index)
            path_output_mode = predicted_path_mode if predicted_path_mode != "none" else (requested_path_mode or "single")
            path_confidence = float(mode_probs[mode_index].item())
            ranked = sorted(enumerate(path_scores), key=lambda item: item[1], reverse=True)
            limit = self._path_limit(path_output_mode)
            selected_path_indices = [index for index, score in ranked if score >= 0.5][:limit]
            if not selected_path_indices and ranked:
                minimum = min(2, len(ranked)) if path_output_mode == "multi" else 1
                selected_path_indices = [ranked[index][0] for index in range(minimum)]

        average_slot_confidence = sum(slot_confidences) / max(1, len(slot_confidences))
        overall_confidence = (history_confidence + average_slot_confidence + (path_confidence if path_candidates else history_confidence)) / (3.0 if path_candidates else 2.0)
        decision = {
            "history_kind": history_kind,
            "slot_mode": history_kind if history_kind in {"current", "previous", "compare", "timeline"} else (_clean_text(fallback_mode) or "current"),
            "selected_slot_keys": list(selected_slot_keys),
            "selected_memory_ids": _dedupe(selected_memory_ids),
            "timeline_memory_ids": _dedupe(timeline_memory_ids),
            "compare_pairs": compare_pairs,
            "slot_directives": slot_directives,
            "coverage_budget": int(coverage_budget),
            "required_categories": list(required_categories),
            "must_keep_slot_keys": list(must_keep_slot_keys),
            "dropped_slots": list(dropped_slots),
            "drop_reasons": dict(drop_reasons),
            "selected_path_indices": [int(item) for item in selected_path_indices],
            "path_output_mode": path_output_mode,
            "conflict_state": "ambiguous" if any(_normalize(dict(item).get("conflict_state", "")) not in {"", "none"} for item in slot_candidates if _normalize(item.get("slot_key", "")) in {_normalize(slot) for slot in selected_slot_keys}) else "none",
            "requires_temporal_reasoning": history_kind in {"previous", "compare", "timeline"} or path_output_mode in {"temporal_path", "state_evolution_path"},
            "requires_path_reasoning": bool(selected_path_indices) or path_output_mode != "none",
            "confidence": round(float(overall_confidence), 6),
            "decision_valid": bool(overall_confidence >= float(self.config.min_confidence)),
        }
        return TMCRAJudgeInferenceResult(
            decision=decision,
            history_confidence=history_confidence,
            slot_confidence=average_slot_confidence,
            path_confidence=path_confidence,
            model_scores={
                "history_probs": {label: round(float(history_probs[index].item()), 6) for index, label in enumerate(_HISTORY_LABELS)},
                "slot_confidences": {item["slot_key"]: round(float(item.get("confidence", 0.0)), 6) for item in slot_rows},
                "path_scores": {str(index): round(float(score), 6) for index, score in enumerate(path_scores)},
                "path_output_mode": path_output_mode,
                "coverage_budget": int(coverage_budget),
                "required_categories": list(required_categories),
            },
        )

    def _slot_feature_vector_for_model(self, payload: Dict[str, Any], slot_candidate: Dict[str, Any], *, slot_index: int = 0) -> List[float]:
        vector = build_slot_feature_vector(payload, slot_candidate, slot_index=slot_index)
        if len(vector) == self.slot_input_dim:
            return vector
        # Backward compatibility for slot models trained before coverage features were appended.
        legacy_vector = build_slot_feature_vector(payload, slot_candidate, slot_index=slot_index, include_coverage_features=False)
        if len(legacy_vector) == self.slot_input_dim:
            return legacy_vector
        return self._align_feature_vector(vector, expected_dim=self.slot_input_dim, feature_name="slot")

    @staticmethod
    def _align_feature_vector(values: Sequence[float], *, expected_dim: int, feature_name: str) -> List[float]:
        vector = [float(item) for item in values]
        if len(vector) != int(expected_dim):
            raise RuntimeError(
                f"incompatible_{feature_name}_feature_dim: built={len(vector)} expected={int(expected_dim)}"
            )
        return vector

    def _directive_from_slot(self, slot_candidate: Dict[str, Any], slot_mode: str) -> Dict[str, Any]:
        active_record = dict(slot_candidate.get("active_record") or {})
        previous_record = dict(slot_candidate.get("previous_record") or {})
        history_chain = [dict(item) for item in slot_candidate.get("history_chain", []) or [] if isinstance(item, dict)]
        selected_memory_ids: List[str] = []
        timeline_memory_ids: List[str] = []
        compare_pair = {
            "current_memory_id": _clean_text(active_record.get("memory_id", "")),
            "previous_memory_id": _clean_text(previous_record.get("memory_id", "")),
        }
        if slot_mode == "current":
            selected_memory_ids = [_clean_text(active_record.get("memory_id", ""))] if active_record else []
        elif slot_mode == "previous":
            selected_memory_ids = [_clean_text(previous_record.get("memory_id", ""))] if previous_record else ([_clean_text(active_record.get("memory_id", ""))] if active_record else [])
        elif slot_mode == "compare":
            selected_memory_ids = [item for item in [_clean_text(previous_record.get("memory_id", "")), _clean_text(active_record.get("memory_id", ""))] if item]
        elif slot_mode == "timeline":
            timeline_memory_ids = _dedupe([_clean_text(item.get("memory_id", "")) for item in history_chain] + [_clean_text(active_record.get("memory_id", ""))])
            selected_memory_ids = list(timeline_memory_ids)
        return {
            "slot_key": _clean_text(slot_candidate.get("slot_key", "")),
            "mode": slot_mode,
            "selected_memory_ids": _dedupe(selected_memory_ids),
            "timeline_memory_ids": _dedupe(timeline_memory_ids),
            "compare_pair": compare_pair,
        }

    def _coerce_slot_mode(self, slot_candidate: Dict[str, Any], slot_mode: str, *, history_kind: str, fallback_mode: str) -> str:
        normalized = _normalize(slot_mode)
        active_record = dict(slot_candidate.get("active_record") or {})
        previous_record = dict(slot_candidate.get("previous_record") or {})
        history_chain = [dict(item) for item in slot_candidate.get("history_chain", []) or [] if isinstance(item, dict)]
        has_previous = bool(previous_record) or len(history_chain) > 0
        if history_kind == "timeline" and (has_previous or active_record):
            return "timeline"
        if history_kind == "compare" and active_record and has_previous:
            return "compare"
        if history_kind == "previous" and has_previous:
            return "previous"
        if normalized == "omit" and (_normalize(fallback_mode) in {"current", "previous", "compare", "timeline"}):
            return _normalize(fallback_mode)
        if normalized == "compare" and not (active_record and has_previous):
            return "current" if active_record else ("previous" if has_previous else "omit")
        if normalized == "timeline" and not (history_chain or active_record):
            return "omit"
        return slot_mode

    def _required_categories(self, payload: Dict[str, Any], *, slot_rows: Sequence[Dict[str, Any]], baseline_selected_slots: Sequence[str]) -> List[str]:
        from_payload = _dedupe(payload.get("required_categories", []) or [])
        if from_payload:
            return from_payload
        baseline_norm = {_normalize(item) for item in baseline_selected_slots}
        categories = [
            _clean_text(item.get("category", "")) or "other"
            for item in slot_rows
            if _normalize(item.get("slot_key", "")) in baseline_norm
        ]
        return _dedupe(categories)

    def _must_keep_slot_keys(self, payload: Dict[str, Any], *, baseline_selected_slots: Sequence[str], preserve_baseline: bool) -> List[str]:
        declared = _dedupe(payload.get("must_keep_slot_keys", []) or [])
        if declared:
            return declared
        if preserve_baseline:
            return _dedupe(baseline_selected_slots)
        return _dedupe(list(baseline_selected_slots)[:1])

    def _slot_budget(
        self,
        payload: Dict[str, Any],
        *,
        history_kind: str,
        fallback_mode: str,
        slot_count: int,
        required_categories: Sequence[str],
        baseline_selected_slots: Sequence[str],
        preserve_baseline: bool,
    ) -> int:
        explicit_budget = int(payload.get("coverage_budget", 0) or 0)
        if explicit_budget > 0:
            budget = explicit_budget
        else:
            intent = dict(payload.get("intent") or {})
            if _normalize(intent.get("kind", "")) == "summary":
                budget = int(self.config.summary_slot_budget)
            elif history_kind == "timeline":
                budget = int(self.config.timeline_slot_budget)
            elif history_kind in {"previous", "compare"}:
                budget = int(self.config.history_slot_budget)
            else:
                budget = int(self.config.current_slot_budget if _normalize(fallback_mode) == "current" else self.config.history_slot_budget)
        if preserve_baseline:
            budget = max(budget, len(list(baseline_selected_slots)))
        budget = max(budget, len(list(required_categories)))
        return max(1, min(max(1, slot_count), int(budget or 1)))

    def _select_slot_rows(
        self,
        slot_rows: Sequence[Dict[str, Any]],
        *,
        slot_rows_by_key: Dict[str, Dict[str, Any]],
        history_kind: str,
        fallback_mode: str,
        coverage_budget: int,
        required_categories: Sequence[str],
        must_keep_slot_keys: Sequence[str],
        baseline_selected_slots: Sequence[str],
    ) -> tuple[List[Dict[str, Any]], List[str], Dict[str, str]]:
        selected: List[Dict[str, Any]] = []
        selected_keys = set()
        drop_reasons: Dict[str, str] = {}
        non_omit_rows = [item for item in slot_rows if _normalize(item.get("mode", "")) != "omit"]
        required_category_norm = [_normalize(item) for item in required_categories if _clean_text(item)]

        def add_row(row: Dict[str, Any], *, reason: str = "") -> bool:
            slot_key = _clean_text(row.get("slot_key", ""))
            if not slot_key:
                return False
            normalized_key = _normalize(slot_key)
            if normalized_key in selected_keys:
                return True
            if len(selected) >= coverage_budget:
                if reason:
                    drop_reasons[slot_key] = reason
                return False
            selected.append(
                {
                    "slot_key": slot_key,
                    "mode": self._coerce_slot_mode(row, _clean_text(row.get("mode", "")) or fallback_mode, history_kind=history_kind, fallback_mode=fallback_mode),
                    "selected_memory_ids": list(row.get("selected_memory_ids", []) or []),
                    "timeline_memory_ids": list(row.get("timeline_memory_ids", []) or []),
                    "compare_pair": dict(row.get("compare_pair", {}) or {}),
                    "slot_score": float(row.get("slot_score", 0.0) or 0.0),
                    "confidence": float(row.get("confidence", 0.0) or 0.0),
                    "category": _clean_text(row.get("category", "")) or "other",
                    "baseline_rank": int(row.get("baseline_rank", 0) or 0),
                }
            )
            selected_keys.add(normalized_key)
            return True

        for slot_key in must_keep_slot_keys:
            row = slot_rows_by_key.get(_normalize(slot_key))
            if row is None:
                drop_reasons[_clean_text(slot_key)] = "missing_candidate"
                continue
            add_row(row)

        for category in required_category_norm:
            best = next((item for item in non_omit_rows if _normalize(item.get("category", "")) == category and _normalize(item.get("slot_key", "")) not in selected_keys), None)
            if best is not None:
                add_row(best)

        for slot_key in baseline_selected_slots:
            row = slot_rows_by_key.get(_normalize(slot_key))
            if row is None:
                drop_reasons[_clean_text(slot_key)] = "missing_candidate"
                continue
            add_row(row, reason="budget_limit")

        for row in non_omit_rows:
            if len(selected) >= coverage_budget:
                break
            add_row(row)

        dropped_slots = [
            slot_key
            for slot_key in baseline_selected_slots
            if _normalize(slot_key) not in selected_keys
        ]
        for slot_key in dropped_slots:
            drop_reasons.setdefault(slot_key, "not_selected")
        return selected, _dedupe(dropped_slots), drop_reasons

    def _path_limit(self, path_mode: str) -> int:
        normalized = _normalize(path_mode)
        if normalized == "multi":
            return max(2, int(self.config.max_selected_paths))
        if normalized in {"constrained", "counterfactual", "temporal_path", "state_evolution_path", "single"}:
            return 1
        return 1
