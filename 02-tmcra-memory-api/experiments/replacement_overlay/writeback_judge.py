from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import hashlib
import re
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Sequence

try:
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, Dataset
except Exception as exc:  # pragma: no cover
    raise RuntimeError(f"torch is required for TMCRA WritebackJudge: {exc}")

from experiments.replacement.adapters.base import AdapterResponse, MemoryAdapter
from experiments.replacement.adapters.reasoning_adapters import OpenAI
from .contracts import WritebackConfig


FEATURE_SCHEMA_VERSION = "tmcra_writeback_judge_v1"

_WRITEBACK_CLASSES = ("fact", "state_change", "high_conf_conclusion")
_UNCERTAIN_MARKERS = ("maybe", "probably", "perhaps", "\u53ef\u80fd", "\u4e5f\u8bb8")
_RHETORICAL_MARKERS = ("you should", "feel free", "\u5efa\u8bae", "\u5b89\u6170")

_GATE_LABELS = ("skip", "write")
_CLASS_LABELS = ("fact", "state_change", "high_conf_conclusion", "reject")
_CATEGORY_LABELS = ("goal", "constraint", "preference", "terminology", "stage_state", "fact", "path", "summary", "other")
_HARD_REJECT_CLAIM_TYPES = {"missing_notice", "conflict_notice"}
_ALLOWED_FACT_TYPES = {"slot_current"}
_ALLOWED_STATE_CHANGE_TYPES = {"slot_compare", "timeline_summary"}
_ALLOWED_CONCLUSION_TYPES = {"path_claim", "summary_conclusion"}
_UNCERTAIN_MARKERS = ("maybe", "probably", "perhaps", "可能", "也许")
_RHETORICAL_MARKERS = ("you should", "建议", "安慰", "feel free")

_UNCERTAIN_MARKERS = ("maybe", "probably", "perhaps", "\u53ef\u80fd", "\u4e5f\u8bb8")
_RHETORICAL_MARKERS = ("you should", "feel free", "\u5efa\u8bae", "\u5b89\u6170")


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
    usage_dict = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens}
    return usage_dict if any(usage_dict.values()) else {}


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


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return float(default)


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


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
    latin = re.findall(r"[a-z0-9_.-]+", text)
    cjk = [char for char in text if "\u4e00" <= char <= "\u9fff"]
    return _dedupe([*latin, *cjk])


def _one_hot(label: str, labels: Sequence[str]) -> List[float]:
    normalized = _normalize(label)
    return [1.0 if normalized == _normalize(item) else 0.0 for item in labels]


def _text_overlap(query: str, value: object) -> float:
    query_tokens = set(_tokenize(query))
    value_tokens = set(_tokenize(value))
    if not query_tokens or not value_tokens:
        return 0.0
    return _safe_div(len(query_tokens & value_tokens), len(value_tokens))


def _normalize_gate_label(value: object) -> str:
    normalized = _normalize(value)
    return normalized if normalized in _GATE_LABELS else "skip"


def _normalize_class_label(value: object) -> str:
    normalized = _normalize(value)
    return normalized if normalized in _CLASS_LABELS else "reject"


def _gate_label_id(value: object) -> int:
    return _GATE_LABELS.index(_normalize_gate_label(value))


def _class_label_id(value: object) -> int:
    return _CLASS_LABELS.index(_normalize_class_label(value))


def _id_to_gate(index: int) -> str:
    return _GATE_LABELS[max(0, min(len(_GATE_LABELS) - 1, int(index)))]


def _id_to_class(index: int) -> str:
    return _CLASS_LABELS[max(0, min(len(_CLASS_LABELS) - 1, int(index)))]


@dataclass(slots=True)
class WritebackGateLabel:
    gate: str = "skip"

    def to_dict(self) -> Dict[str, Any]:
        return {"gate": self.gate}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "WritebackGateLabel":
        payload = dict(payload or {})
        return cls(gate=_normalize_gate_label(payload.get("gate", "")))


@dataclass(slots=True)
class WritebackClassLabel:
    writeback_class: str = "reject"

    def to_dict(self) -> Dict[str, Any]:
        return {"writeback_class": self.writeback_class}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "WritebackClassLabel":
        payload = dict(payload or {})
        return cls(writeback_class=_normalize_class_label(payload.get("writeback_class", "")))


@dataclass(slots=True)
class WritebackSlotLabel:
    target_canonical_slot: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"target_canonical_slot": self.target_canonical_slot}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "WritebackSlotLabel":
        payload = dict(payload or {})
        return cls(target_canonical_slot=_clean_text(payload.get("target_canonical_slot", "")))


@dataclass(slots=True)
class WritebackClaimCandidate:
    claim_id: str
    claim_type: str
    claim_text: str
    claim_confidence: float = 0.0
    memory_ids: List[str] = field(default_factory=list)
    fact_refs: List[str] = field(default_factory=list)
    path_refs: List[str] = field(default_factory=list)
    category: str = ""
    canonical_slot_key: str = ""
    candidate_slot_pool: List[Dict[str, Any]] = field(default_factory=list)
    support_count: int = 0
    has_conflict: bool = False
    fallback_used: bool = False
    unsupported: bool = False
    structured_value: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_type": self.claim_type,
            "claim_text": self.claim_text,
            "claim_confidence": round(float(self.claim_confidence), 6),
            "memory_ids": list(self.memory_ids),
            "fact_refs": list(self.fact_refs),
            "path_refs": list(self.path_refs),
            "category": self.category,
            "canonical_slot_key": self.canonical_slot_key,
            "candidate_slot_pool": [dict(item) for item in self.candidate_slot_pool],
            "support_count": int(self.support_count),
            "has_conflict": bool(self.has_conflict),
            "fallback_used": bool(self.fallback_used),
            "unsupported": bool(self.unsupported),
            "structured_value": dict(self.structured_value),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "WritebackClaimCandidate":
        payload = dict(payload or {})
        return cls(
            claim_id=_clean_text(payload.get("claim_id", "")),
            claim_type=_clean_text(payload.get("claim_type", "")),
            claim_text=_clean_text(payload.get("claim_text", "")),
            claim_confidence=float(payload.get("claim_confidence", 0.0) or 0.0),
            memory_ids=_dedupe(payload.get("memory_ids", []) or []),
            fact_refs=_dedupe(payload.get("fact_refs", []) or []),
            path_refs=_dedupe(payload.get("path_refs", []) or []),
            category=_clean_text(payload.get("category", "")),
            canonical_slot_key=_clean_text(payload.get("canonical_slot_key", "")),
            candidate_slot_pool=[dict(item) for item in payload.get("candidate_slot_pool", []) or [] if isinstance(item, dict)],
            support_count=int(payload.get("support_count", 0) or 0),
            has_conflict=bool(payload.get("has_conflict", False)),
            fallback_used=bool(payload.get("fallback_used", False)),
            unsupported=bool(payload.get("unsupported", False)),
            structured_value=dict(payload.get("structured_value") or {}),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(slots=True)
class WritebackJudgeTrainingExample:
    example_id: str
    query: str
    intent: Dict[str, Any] = field(default_factory=dict)
    claim_candidate: WritebackClaimCandidate = field(default_factory=lambda: WritebackClaimCandidate(claim_id="", claim_type="", claim_text=""))
    candidate_slot_pool: List[Dict[str, Any]] = field(default_factory=list)
    rule_gold: Dict[str, Any] = field(default_factory=dict)
    teacher_a_decision: Dict[str, Any] = field(default_factory=dict)
    teacher_b_review: Dict[str, Any] = field(default_factory=dict)
    agreement_score: float = 1.0
    gate_label: WritebackGateLabel = field(default_factory=WritebackGateLabel)
    class_label: WritebackClassLabel = field(default_factory=WritebackClassLabel)
    slot_label: WritebackSlotLabel = field(default_factory=WritebackSlotLabel)
    source_split: str = "train"
    sample_weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def request_payload(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "intent": dict(self.intent),
            "claim_candidate": self.claim_candidate.to_dict(),
            "candidate_slot_pool": [dict(item) for item in self.candidate_slot_pool or self.claim_candidate.candidate_slot_pool],
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "example_id": self.example_id,
            "query": self.query,
            "intent": dict(self.intent),
            "claim_candidate": self.claim_candidate.to_dict(),
            "candidate_slot_pool": [dict(item) for item in self.candidate_slot_pool],
            "rule_gold": dict(self.rule_gold),
            "teacher_a_decision": dict(self.teacher_a_decision),
            "teacher_b_review": dict(self.teacher_b_review),
            "agreement_score": round(float(self.agreement_score), 6),
            "gate_label": self.gate_label.to_dict(),
            "class_label": self.class_label.to_dict(),
            "slot_label": self.slot_label.to_dict(),
            "source_split": self.source_split,
            "sample_weight": round(float(self.sample_weight), 6),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "WritebackJudgeTrainingExample":
        payload = dict(payload or {})
        return cls(
            example_id=_clean_text(payload.get("example_id", "")),
            query=_clean_text(payload.get("query", "")),
            intent=dict(payload.get("intent") or {}),
            claim_candidate=WritebackClaimCandidate.from_dict(payload.get("claim_candidate")),
            candidate_slot_pool=[dict(item) for item in payload.get("candidate_slot_pool", []) or [] if isinstance(item, dict)],
            rule_gold=dict(payload.get("rule_gold") or {}),
            teacher_a_decision=dict(payload.get("teacher_a_decision") or {}),
            teacher_b_review=dict(payload.get("teacher_b_review") or {}),
            agreement_score=float(payload.get("agreement_score", 1.0) or 1.0),
            gate_label=WritebackGateLabel.from_dict(payload.get("gate_label")),
            class_label=WritebackClassLabel.from_dict(payload.get("class_label")),
            slot_label=WritebackSlotLabel.from_dict(payload.get("slot_label")),
            source_split=_clean_text(payload.get("source_split", "")) or "train",
            sample_weight=float(payload.get("sample_weight", 1.0) or 1.0),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(slots=True)
class WritebackJudgeConfig:
    gate_hidden_dim: int = 64
    class_hidden_dim: int = 64
    slot_hidden_dim: int = 96
    dropout: float = 0.1
    epochs: int = 12
    batch_size: int = 32
    lr: float = 1e-3
    weight_decay: float = 1e-4
    min_confidence: float = 0.8
    device: str = "auto"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "WritebackJudgeConfig":
        return cls(**dict(payload or {}))


@dataclass(slots=True)
class TMCRAWritebackJudgeManifest:
    version: str = FEATURE_SCHEMA_VERSION
    config: Dict[str, Any] = field(default_factory=dict)
    gate_model_path: str = ""
    class_model_path: str = ""
    slot_model_path: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "config": dict(self.config),
            "gate_model_path": self.gate_model_path,
            "class_model_path": self.class_model_path,
            "slot_model_path": self.slot_model_path,
            "metrics": dict(self.metrics),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "TMCRAWritebackJudgeManifest":
        return cls(**dict(payload or {}))


@dataclass(slots=True)
class WritebackDecision:
    gate: str = "skip"
    writeback_class: str = "reject"
    target_canonical_slot: str = ""
    confidence: float = 0.0
    provider: str = "rule"
    decision_valid: bool = False
    model_scores: Dict[str, Any] = field(default_factory=dict)
    rejected_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate": self.gate,
            "writeback_class": self.writeback_class,
            "target_canonical_slot": self.target_canonical_slot,
            "confidence": round(float(self.confidence), 6),
            "provider": self.provider,
            "decision_valid": bool(self.decision_valid),
            "model_scores": dict(self.model_scores),
            "rejected_reason": self.rejected_reason,
        }


@dataclass(slots=True)
class WritebackTraceRecord:
    claim_id: str
    claim_type: str
    rule_decision: Dict[str, Any] = field(default_factory=dict)
    model_decision: Dict[str, Any] = field(default_factory=dict)
    final_decision: Dict[str, Any] = field(default_factory=dict)
    rule_agreement: float = 0.0
    teacher_agreement: float = 0.0
    rejected_reason: str = ""
    writeback_record: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_type": self.claim_type,
            "rule_decision": dict(self.rule_decision),
            "model_decision": dict(self.model_decision),
            "final_decision": dict(self.final_decision),
            "rule_agreement": round(float(self.rule_agreement), 6),
            "teacher_agreement": round(float(self.teacher_agreement), 6),
            "rejected_reason": self.rejected_reason,
            "writeback_record": dict(self.writeback_record),
        }


WritebackCandidate = WritebackClaimCandidate


@dataclass(slots=True)
class WritebackTrace:
    enabled: bool = False
    mode: str = "disabled"
    provider: str = "rule_only"
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    records: List[Dict[str, Any]] = field(default_factory=list)
    claims: List[Dict[str, Any]] = field(default_factory=list)
    stored_record_ids: List[str] = field(default_factory=list)
    written_count: int = 0
    rejected_count: int = 0
    promotion_events: List[Dict[str, Any]] = field(default_factory=list)
    global_reject_reason: str = ""
    token_usage: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "mode": self.mode,
            "provider": self.provider,
            "candidates": [dict(item) for item in self.candidates],
            "records": [dict(item) for item in self.records],
            "claims": [dict(item) for item in self.claims],
            "stored_record_ids": list(self.stored_record_ids),
            "written_count": int(self.written_count),
            "rejected_count": int(self.rejected_count),
            "promotion_events": [dict(item) for item in self.promotion_events],
            "global_reject_reason": self.global_reject_reason,
            "token_usage": dict(self.token_usage),
        }


@dataclass(slots=True)
class TMCRAWritebackInferenceResult:
    decision: WritebackDecision = field(default_factory=WritebackDecision)
    gate_confidence: float = 0.0
    class_confidence: float = 0.0
    slot_confidence: float = 0.0
    provider: str = "tmcra_writeback_judge"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.to_dict(),
            "gate_confidence": round(float(self.gate_confidence), 6),
            "class_confidence": round(float(self.class_confidence), 6),
            "slot_confidence": round(float(self.slot_confidence), 6),
            "provider": self.provider,
        }


def load_writeback_training_examples(path: str | Path) -> List[WritebackJudgeTrainingExample]:
    example_path = Path(path)
    rows: List[WritebackJudgeTrainingExample] = []
    with example_path.open("r", encoding="utf-8-sig") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            rows.append(WritebackJudgeTrainingExample.from_dict(json.loads(line)))
    return rows


def write_writeback_training_examples(path: str | Path, examples: Sequence[WritebackJudgeTrainingExample]) -> int:
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
    intent = dict(payload.get("intent") or {})
    return [
        1.0 if "current" in lowered or "现在" in query else 0.0,
        1.0 if any(token in lowered for token in ("previous", "before", "earlier", "historical")) or any(token in query for token in ("之前", "以前", "历史")) else 0.0,
        1.0 if any(token in lowered for token in ("compare", "difference", "vs")) or "对比" in query else 0.0,
        1.0 if any(token in lowered for token in ("timeline", "evolution", "over time")) or "变化过程" in query else 0.0,
        1.0 if any(token in lowered for token in ("summary", "summarize", "combine")) or "总结" in query else 0.0,
        1.0 if any(token in lowered for token in ("path", "route")) or "路径" in query else 0.0,
        1.0 if re.search(r"[\u4e00-\u9fff]", query) else 0.0,
        1.0 if _normalize(intent.get("kind", "")) == "summary" else 0.0,
        1.0 if _normalize(intent.get("kind", "")) == "path" else 0.0,
        1.0 if _normalize(intent.get("history_kind", "")) in {"previous", "compare", "timeline"} else 0.0,
    ]


def build_gate_feature_vector(payload: Dict[str, Any]) -> List[float]:
    claim = dict(payload.get("claim_candidate") or {})
    slot_pool = [dict(item) for item in payload.get("candidate_slot_pool", []) or [] if isinstance(item, dict)]
    claim_text = _clean_text(claim.get("claim_text", ""))
    claim_type = _clean_text(claim.get("claim_type", ""))
    category = _clean_text(claim.get("category", "")) or "other"
    if _normalize(category) not in {_normalize(item) for item in _CATEGORY_LABELS}:
        category = "other"
    return [
        *_query_flags(payload),
        _clamp01(claim.get("claim_confidence", 0.0)),
        _safe_div(len(claim.get("memory_ids", []) or []), 4.0),
        _safe_div(len(claim.get("fact_refs", []) or []), 4.0),
        _safe_div(len(claim.get("path_refs", []) or []), 3.0),
        _safe_div(len(slot_pool), 6.0),
        _safe_div(int(claim.get("support_count", 0) or 0), 4.0),
        1.0 if claim.get("has_conflict", False) else 0.0,
        1.0 if claim.get("fallback_used", False) else 0.0,
        1.0 if claim.get("unsupported", False) else 0.0,
        _text_overlap(payload.get("query", ""), claim_text),
        _hash01(claim_type),
        _hash01(claim.get("canonical_slot_key", "")),
        *_one_hot(category, _CATEGORY_LABELS),
    ]


def build_class_feature_vector(payload: Dict[str, Any]) -> List[float]:
    claim = dict(payload.get("claim_candidate") or {})
    claim_type = _clean_text(claim.get("claim_type", ""))
    structured_value = dict(claim.get("structured_value") or {})
    return [
        *build_gate_feature_vector(payload),
        1.0 if claim_type in {"slot_current", "slot_previous", "slot_inactive"} else 0.0,
        1.0 if claim_type in {"slot_compare", "timeline_summary"} else 0.0,
        1.0 if claim_type == "path_claim" else 0.0,
        1.0 if claim_type in {"missing_notice", "conflict_notice"} else 0.0,
        1.0 if "previous_value" in structured_value else 0.0,
        1.0 if "current_value" in structured_value else 0.0,
        1.0 if "path_summary" in structured_value else 0.0,
    ]


def build_slot_feature_vector(payload: Dict[str, Any], slot_candidate: Dict[str, Any], *, slot_index: int = 0) -> List[float]:
    claim = dict(payload.get("claim_candidate") or {})
    category = _clean_text(slot_candidate.get("category", "")) or "other"
    if _normalize(category) not in {_normalize(item) for item in _CATEGORY_LABELS}:
        category = "other"
    slot_key = _clean_text(slot_candidate.get("canonical_slot_key", "")) or _clean_text(slot_candidate.get("slot_key", ""))
    return [
        *build_class_feature_vector(payload),
        float(slot_index) / 8.0,
        _clamp01(slot_candidate.get("score", 0.0)),
        _text_overlap(payload.get("query", ""), slot_key),
        _text_overlap(claim.get("claim_text", ""), slot_candidate.get("value", "")),
        1.0 if _normalize(slot_key) == _normalize(claim.get("canonical_slot_key", "")) else 0.0,
        1.0 if _normalize(category) == _normalize(claim.get("category", "")) else 0.0,
        _hash01(slot_key),
        *_one_hot(category, _CATEGORY_LABELS),
    ]


class _GateDataset(Dataset):
    def __init__(self, examples: Sequence[WritebackJudgeTrainingExample]) -> None:
        self.rows = [
            (
                torch.tensor(build_gate_feature_vector(item.request_payload()), dtype=torch.float32),
                torch.tensor(_gate_label_id(item.gate_label.gate), dtype=torch.long),
                torch.tensor(float(item.sample_weight), dtype=torch.float32),
            )
            for item in examples
        ]

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int):
        return self.rows[index]


class _ClassDataset(Dataset):
    def __init__(self, examples: Sequence[WritebackJudgeTrainingExample]) -> None:
        self.rows = [
            (
                torch.tensor(build_class_feature_vector(item.request_payload()), dtype=torch.float32),
                torch.tensor(_class_label_id(item.class_label.writeback_class), dtype=torch.long),
                torch.tensor(float(item.sample_weight), dtype=torch.float32),
            )
            for item in examples
        ]

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int):
        return self.rows[index]


class _SlotDataset(Dataset):
    def __init__(self, examples: Sequence[WritebackJudgeTrainingExample]) -> None:
        self.rows = []
        for item in examples:
            if _normalize(item.gate_label.gate) != "write":
                continue
            payload = item.request_payload()
            slot_pool = [dict(row) for row in payload.get("candidate_slot_pool", []) or [] if isinstance(row, dict)]
            for index, candidate in enumerate(slot_pool):
                self.rows.append(
                    (
                        torch.tensor(build_slot_feature_vector(payload, candidate, slot_index=index), dtype=torch.float32),
                        torch.tensor(1.0 if _normalize(candidate.get("canonical_slot_key", candidate.get("slot_key", ""))) == _normalize(item.slot_label.target_canonical_slot) else 0.0, dtype=torch.float32),
                        torch.tensor(float(item.sample_weight), dtype=torch.float32),
                    )
                )

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int):
        return self.rows[index]


class _MLPClassifier(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, output_dim: int, *, dropout: float) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, output_dim),
        )

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        return self.layers(inputs)


def _resolve_device(device: str) -> torch.device:
    normalized = _normalize(device)
    if normalized in {"", "auto"}:
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(device)


def _resolve_manifest_artifact_path(path_value: str | Path, base_dir: Path) -> Path:
    raw_value = str(path_value)
    candidate = Path(raw_value)
    if candidate.exists():
        return candidate
    filename = PureWindowsPath(raw_value).name or Path(raw_value.replace("\\", "/")).name or candidate.name
    by_name = base_dir / filename
    if by_name.exists():
        return by_name
    joined = base_dir / candidate
    if joined.exists():
        return joined
    return candidate


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
    criterion = nn.CrossEntropyLoss(reduction="none")
    last_loss = 0.0
    for _epoch in range(max(1, int(epochs))):
        model.train()
        for features, targets, weights in train_loader:
            features = features.to(device)
            targets = targets.to(device)
            weights = weights.to(device)
            logits = model(features)
            loss = criterion(logits, targets)
            loss = (loss * weights).mean()
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            last_loss = float(loss.item())
    metrics = {"train_loss": round(last_loss, 6)}
    if val_loader is None:
        return metrics
    model.eval()
    correct = 0
    total = 0
    with torch.no_grad():
        for features, targets, _weights in val_loader:
            logits = model(features.to(device))
            predictions = torch.argmax(logits, dim=-1).cpu()
            correct += int((predictions == targets).sum().item())
            total += int(targets.shape[0])
    metrics["val_accuracy"] = round(_safe_div(correct, total), 6)
    return metrics


def _train_slot_scorer(
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
    criterion = nn.BCEWithLogitsLoss(reduction="none")
    last_loss = 0.0
    for _epoch in range(max(1, int(epochs))):
        model.train()
        for features, targets, weights in train_loader:
            features = features.to(device)
            targets = targets.to(device).unsqueeze(-1)
            weights = weights.to(device).unsqueeze(-1)
            logits = model(features)
            loss = criterion(logits, targets)
            loss = (loss * weights).mean()
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            last_loss = float(loss.item())
    metrics = {"train_loss": round(last_loss, 6)}
    if val_loader is None:
        return metrics
    model.eval()
    correct = 0
    total = 0
    with torch.no_grad():
        for features, targets, _weights in val_loader:
            logits = model(features.to(device))
            predictions = (torch.sigmoid(logits).cpu().squeeze(-1) >= 0.5).to(dtype=torch.float32)
            correct += int((predictions == targets).sum().item())
            total += int(targets.shape[0])
    metrics["val_accuracy"] = round(_safe_div(correct, total), 6)
    return metrics


def train_tmcra_writeback_judge(
    examples: Sequence[WritebackJudgeTrainingExample],
    *,
    output_dir: str | Path,
    config: WritebackJudgeConfig | None = None,
) -> TMCRAWritebackJudgeManifest:
    config = config or WritebackJudgeConfig()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    device = _resolve_device(config.device)
    train_examples = [item for item in examples if _normalize(item.source_split) not in {"val", "validation"}]
    val_examples = [item for item in examples if _normalize(item.source_split) in {"val", "validation"}]
    if not train_examples:
        train_examples = list(examples)
    gate_train = _GateDataset(train_examples)
    gate_val = _GateDataset(val_examples) if val_examples else None
    class_train = _ClassDataset(train_examples)
    class_val = _ClassDataset(val_examples) if val_examples else None
    slot_train = _SlotDataset(train_examples)
    slot_val = _SlotDataset(val_examples) if val_examples else None
    sample_gate = gate_train[0][0] if len(gate_train) else torch.zeros(1, dtype=torch.float32)
    sample_class = class_train[0][0] if len(class_train) else torch.zeros(1, dtype=torch.float32)
    sample_slot = slot_train[0][0] if len(slot_train) else torch.zeros(1, dtype=torch.float32)
    gate_model = _MLPClassifier(int(sample_gate.shape[-1]), config.gate_hidden_dim, len(_GATE_LABELS), dropout=config.dropout)
    class_model = _MLPClassifier(int(sample_class.shape[-1]), config.class_hidden_dim, len(_CLASS_LABELS), dropout=config.dropout)
    slot_model = _MLPClassifier(int(sample_slot.shape[-1]), config.slot_hidden_dim, 1, dropout=config.dropout)
    gate_metrics = _train_classifier(
        gate_model,
        train_loader=DataLoader(gate_train, batch_size=max(1, int(config.batch_size)), shuffle=True),
        val_loader=DataLoader(gate_val, batch_size=max(1, int(config.batch_size))) if gate_val is not None else None,
        epochs=config.epochs,
        device=device,
        lr=config.lr,
        weight_decay=config.weight_decay,
    )
    class_metrics = _train_classifier(
        class_model,
        train_loader=DataLoader(class_train, batch_size=max(1, int(config.batch_size)), shuffle=True),
        val_loader=DataLoader(class_val, batch_size=max(1, int(config.batch_size))) if class_val is not None else None,
        epochs=config.epochs,
        device=device,
        lr=config.lr,
        weight_decay=config.weight_decay,
    )
    slot_metrics = _train_slot_scorer(
        slot_model,
        train_loader=DataLoader(slot_train, batch_size=max(1, int(config.batch_size)), shuffle=True) if len(slot_train) else DataLoader(_SlotDataset([]), batch_size=1),
        val_loader=DataLoader(slot_val, batch_size=max(1, int(config.batch_size))) if slot_val is not None and len(slot_val) else None,
        epochs=config.epochs,
        device=device,
        lr=config.lr,
        weight_decay=config.weight_decay,
    )
    gate_model_path = output_path / "writeback_gate.pt"
    class_model_path = output_path / "writeback_class.pt"
    slot_model_path = output_path / "writeback_slot.pt"
    torch.save({"input_dim": int(sample_gate.shape[-1]), "labels": list(_GATE_LABELS), "state_dict": gate_model.cpu().state_dict()}, gate_model_path)
    torch.save({"input_dim": int(sample_class.shape[-1]), "labels": list(_CLASS_LABELS), "state_dict": class_model.cpu().state_dict()}, class_model_path)
    torch.save({"input_dim": int(sample_slot.shape[-1]), "state_dict": slot_model.cpu().state_dict()}, slot_model_path)
    manifest = TMCRAWritebackJudgeManifest(
        config=config.to_dict(),
        gate_model_path=str(gate_model_path.resolve()),
        class_model_path=str(class_model_path.resolve()),
        slot_model_path=str(slot_model_path.resolve()),
        metrics={
            "train_examples": len(train_examples),
            "val_examples": len(val_examples),
            "gate": gate_metrics,
            "class": class_metrics,
            "slot": slot_metrics,
        },
    )
    manifest_path = output_path / "tmcra_writeback_judge_manifest_v1.json"
    manifest_path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


class TMCRAWritebackJudgeProvider:
    def __init__(
        self,
        *,
        gate_model_path: str | Path,
        class_model_path: str | Path,
        slot_model_path: str | Path,
        config: WritebackJudgeConfig | None = None,
    ) -> None:
        self.config = config or WritebackJudgeConfig()
        self.device = _resolve_device(self.config.device)
        gate_payload = torch.load(Path(gate_model_path), map_location="cpu")
        class_payload = torch.load(Path(class_model_path), map_location="cpu")
        slot_payload = torch.load(Path(slot_model_path), map_location="cpu")
        self.gate_input_dim = int(gate_payload["input_dim"])
        self.class_input_dim = int(class_payload["input_dim"])
        self.slot_input_dim = int(slot_payload["input_dim"])
        self.gate_model = _MLPClassifier(self.gate_input_dim, self.config.gate_hidden_dim, len(gate_payload.get("labels", _GATE_LABELS)), dropout=self.config.dropout)
        self.gate_model.load_state_dict(gate_payload["state_dict"])
        self.gate_model.to(self.device).eval()
        self.class_model = _MLPClassifier(self.class_input_dim, self.config.class_hidden_dim, len(class_payload.get("labels", _CLASS_LABELS)), dropout=self.config.dropout)
        self.class_model.load_state_dict(class_payload["state_dict"])
        self.class_model.to(self.device).eval()
        self.slot_model = _MLPClassifier(self.slot_input_dim, self.config.slot_hidden_dim, 1, dropout=self.config.dropout)
        self.slot_model.load_state_dict(slot_payload["state_dict"])
        self.slot_model.to(self.device).eval()

    @classmethod
    def from_manifest(cls, manifest_path: str | Path) -> "TMCRAWritebackJudgeProvider":
        manifest_file = Path(manifest_path)
        manifest = TMCRAWritebackJudgeManifest.from_dict(json.loads(manifest_file.read_text(encoding="utf-8")))
        base_dir = manifest_file.resolve().parent
        return cls(
            gate_model_path=_resolve_manifest_artifact_path(manifest.gate_model_path, base_dir),
            class_model_path=_resolve_manifest_artifact_path(manifest.class_model_path, base_dir),
            slot_model_path=_resolve_manifest_artifact_path(manifest.slot_model_path, base_dir),
            config=WritebackJudgeConfig.from_dict(manifest.config),
        )

    def predict(self, payload: Dict[str, Any]) -> TMCRAWritebackInferenceResult:
        gate_features = torch.tensor([self._align_vector(build_gate_feature_vector(payload), expected_dim=self.gate_input_dim)], dtype=torch.float32, device=self.device)
        class_features = torch.tensor([self._align_vector(build_class_feature_vector(payload), expected_dim=self.class_input_dim)], dtype=torch.float32, device=self.device)
        with torch.no_grad():
            gate_probs = torch.softmax(self.gate_model(gate_features), dim=-1)[0]
            class_probs = torch.softmax(self.class_model(class_features), dim=-1)[0]
        gate_index = int(gate_probs.argmax().item())
        class_index = int(class_probs.argmax().item())
        gate = _id_to_gate(gate_index)
        writeback_class = _id_to_class(class_index)
        gate_confidence = float(gate_probs[gate_index].item())
        class_confidence = float(class_probs[class_index].item())
        slot_confidence = 0.0
        target_canonical_slot = ""
        slot_pool = [dict(item) for item in payload.get("candidate_slot_pool", []) or [] if isinstance(item, dict)]
        if gate == "write" and slot_pool:
            best_score = None
            for index, candidate in enumerate(slot_pool):
                feature_vector = torch.tensor(
                    [self._align_vector(build_slot_feature_vector(payload, candidate, slot_index=index), expected_dim=self.slot_input_dim)],
                    dtype=torch.float32,
                    device=self.device,
                )
                with torch.no_grad():
                    score = float(torch.sigmoid(self.slot_model(feature_vector))[0][0].item())
                if best_score is None or score > best_score:
                    best_score = score
                    slot_confidence = score
                    target_canonical_slot = _clean_text(candidate.get("canonical_slot_key", "")) or _clean_text(candidate.get("slot_key", ""))
        confidence = min(gate_confidence, class_confidence, slot_confidence or 1.0)
        decision = WritebackDecision(
            gate="write" if gate == "write" and confidence >= float(self.config.min_confidence) else "skip",
            writeback_class=writeback_class if gate == "write" else "reject",
            target_canonical_slot=target_canonical_slot,
            confidence=confidence,
            provider="tmcra_writeback_judge",
            decision_valid=True,
            model_scores={
                "gate": {label: round(float(gate_probs[index].item()), 6) for index, label in enumerate(_GATE_LABELS)},
                "class": {label: round(float(class_probs[index].item()), 6) for index, label in enumerate(_CLASS_LABELS)},
                "slot_confidence": round(float(slot_confidence), 6),
            },
            rejected_reason="" if gate == "write" else "model_skip",
        )
        if decision.gate != "write":
            decision.writeback_class = "reject"
            decision.target_canonical_slot = ""
        elif not decision.target_canonical_slot:
            decision.gate = "skip"
            decision.writeback_class = "reject"
            decision.rejected_reason = "missing_slot"
        return TMCRAWritebackInferenceResult(
            decision=decision,
            gate_confidence=gate_confidence,
            class_confidence=class_confidence,
            slot_confidence=slot_confidence,
        )

    def _align_vector(self, values: Sequence[float], *, expected_dim: int) -> List[float]:
        vector = [float(item) for item in values]
        if len(vector) == expected_dim:
            return vector
        if len(vector) > expected_dim:
            return vector[:expected_dim]
        return [*vector, *([0.0] * (expected_dim - len(vector)))]


def _extract_record_map(response: AdapterResponse) -> Dict[str, Dict[str, Any]]:
    bundle = dict(response.metadata.get("tmcra_reasoning_bundle", {}) or {})
    slot_resolution = dict(bundle.get("slot_resolution", {}) or {})
    views = [dict(item) for item in slot_resolution.get("views", []) or [] if isinstance(item, dict)]
    record_map: Dict[str, Dict[str, Any]] = {}
    for view in views:
        base_slot = _clean_text(view.get("slot_key", ""))
        base_category = _clean_text(view.get("category", ""))
        for field_name, mode_name in (("active_record", "current"), ("previous_record", "previous")):
            record = dict(view.get(field_name) or {})
            memory_id = _clean_text(record.get("memory_id", ""))
            if not memory_id:
                continue
            record_map[memory_id] = {
                "memory_id": memory_id,
                "slot_key": _clean_text(record.get("slot_key", "")) or base_slot,
                "canonical_slot_key": _clean_text(record.get("slot_key", "")) or base_slot,
                "category": _clean_text(record.get("category", "")) or base_category,
                "value": _clean_text(record.get("value", "")),
                "relation": _clean_text(record.get("relation", "")),
                "anchors": list(record.get("anchors", []) or []),
                "state": mode_name,
                "turn_index": int(record.get("turn_index", 0) or 0),
                "metadata": dict(record.get("metadata") or {}),
            }
        for record in view.get("historical_chain", []) or []:
            if not isinstance(record, dict):
                continue
            memory_id = _clean_text(record.get("memory_id", ""))
            if not memory_id:
                continue
            record_map[memory_id] = {
                "memory_id": memory_id,
                "slot_key": _clean_text(record.get("slot_key", "")) or base_slot,
                "canonical_slot_key": _clean_text(record.get("slot_key", "")) or base_slot,
                "category": _clean_text(record.get("category", "")) or base_category,
                "value": _clean_text(record.get("value", "")),
                "relation": _clean_text(record.get("relation", "")),
                "anchors": list(record.get("anchors", []) or []),
                "state": _clean_text(record.get("state", "")) or "historical",
                "turn_index": int(record.get("turn_index", 0) or 0),
                "metadata": dict(record.get("metadata") or {}),
            }
    evidence_pack = dict(response.metadata.get("overlay_evidence_pack", {}) or {})
    for fact in evidence_pack.get("facts", []) or []:
        if not isinstance(fact, dict):
            continue
        memory_id = _clean_text(fact.get("memory_id", ""))
        if not memory_id or memory_id in record_map:
            continue
        record_map[memory_id] = {
            "memory_id": memory_id,
            "slot_key": _clean_text(fact.get("slot_key", "")) or _clean_text(fact.get("from", "")),
            "canonical_slot_key": _clean_text(fact.get("slot_key", "")) or _clean_text(fact.get("from", "")),
            "category": _clean_text(fact.get("category", "")) or "fact",
            "value": _clean_text(fact.get("to", "")),
            "relation": _clean_text(fact.get("relation", "")) or "related_to",
            "anchors": _dedupe([fact.get("from", "")]),
            "state": _clean_text(fact.get("temporal_role", "")) or "current",
            "turn_index": int(fact.get("turn_index", 0) or 0),
            "metadata": {},
        }
    return record_map


def _canonical_slot_pool(record_map: Dict[str, Dict[str, Any]], claim: Dict[str, Any]) -> List[Dict[str, Any]]:
    pool: List[Dict[str, Any]] = []
    seen = set()
    for memory_id in claim.get("memory_ids", []) or []:
        record = record_map.get(_clean_text(memory_id))
        if record is None:
            continue
        slot_key = _clean_text(record.get("canonical_slot_key", record.get("slot_key", "")))
        if not slot_key:
            continue
        key = _normalize(slot_key)
        if key in seen:
            continue
        seen.add(key)
        pool.append(
            {
                "slot_key": _clean_text(record.get("slot_key", "")) or slot_key,
                "canonical_slot_key": slot_key,
                "category": _clean_text(record.get("category", "")) or "other",
                "value": _clean_text(record.get("value", "")),
                "score": _clamp01(record.get("metadata", {}).get("score", 0.85), default=0.85),
                "turn_index": int(record.get("turn_index", 0) or 0),
            }
        )
    if pool:
        return pool
    fallback_slot = _clean_text(claim.get("canonical_slot_key", ""))
    if fallback_slot:
        return [
            {
                "slot_key": fallback_slot,
                "canonical_slot_key": fallback_slot,
                "category": _clean_text(claim.get("category", "")) or "other",
                "value": _clean_text(claim.get("claim_text", "")),
                "score": _clamp01(claim.get("claim_confidence", 0.0)),
                "turn_index": 0,
            }
        ]
    return []


def extract_writeback_candidates(*, query_text: str, response: AdapterResponse) -> List[WritebackClaimCandidate]:
    bundle = dict(response.metadata.get("tmcra_reasoning_bundle", {}) or {})
    evidence_pack = dict(response.metadata.get("overlay_evidence_pack", {}) or {})
    claims = [dict(item) for item in evidence_pack.get("claims", []) or bundle.get("claims", []) or [] if isinstance(item, dict)]
    record_map = _extract_record_map(response)
    intent = dict(bundle.get("intent", {}) or {})
    path_realization = dict(bundle.get("path_realization", {}) or {})
    candidates: List[WritebackClaimCandidate] = []
    for index, claim in enumerate(claims):
        claim_id = _clean_text(claim.get("claim_id", "")) or f"claim:{index}"
        claim_type = _clean_text(claim.get("claim_type", ""))
        memory_ids = _dedupe(claim.get("memory_ids", []) or [])
        slot_pool = _canonical_slot_pool(record_map, claim)
        primary_record = record_map.get(memory_ids[0]) if memory_ids else None
        secondary_record = record_map.get(memory_ids[1]) if len(memory_ids) > 1 else None
        category = _clean_text(claim.get("category", "")) or _clean_text((primary_record or {}).get("category", ""))
        canonical_slot_key = _clean_text(claim.get("canonical_slot_key", "")) or _clean_text((primary_record or {}).get("canonical_slot_key", ""))
        structured_value: Dict[str, Any] = {}
        if claim_type == "slot_compare":
            if secondary_record is not None:
                structured_value["current_value"] = secondary_record.get("value", "")
            if primary_record is not None:
                structured_value["previous_value"] = primary_record.get("value", "")
        elif primary_record is not None:
            structured_value["current_value"] = primary_record.get("value", "")
        if claim_type != "slot_compare" and secondary_record is not None:
            structured_value["previous_value"] = secondary_record.get("value", "")
        if claim_type == "timeline_summary":
            structured_value["timeline_memory_ids"] = list(memory_ids)
        if claim_type == "path_claim":
            structured_value["path_summary"] = _clean_text(claim.get("text", ""))
        candidates.append(
            WritebackClaimCandidate(
                claim_id=claim_id,
                claim_type=claim_type,
                claim_text=_clean_text(claim.get("text", "")),
                claim_confidence=float(claim.get("confidence", 0.0) or 0.0),
                memory_ids=memory_ids,
                fact_refs=_dedupe(claim.get("fact_refs", []) or []),
                path_refs=_dedupe(claim.get("path_refs", []) or []),
                category=category or "other",
                canonical_slot_key=canonical_slot_key,
                candidate_slot_pool=slot_pool,
                support_count=max(len(memory_ids), len(claim.get("fact_refs", []) or []), len(claim.get("path_refs", []) or [])),
                has_conflict=claim_type == "conflict_notice",
                fallback_used=bool(evidence_pack.get("fallback_used", False)),
                unsupported=bool(response.unsupported_claims or evidence_pack.get("unsupported_claims", []) or []) or (_clean_text(claim.get("text", "")) in set(response.unsupported_claims or evidence_pack.get("unsupported_claims", []) or [])),
                structured_value=structured_value,
                metadata={
                    "source": "overlay_claim",
                    "query_text": query_text,
                    "intent_kind": _clean_text(intent.get("kind", "")),
                    "history_kind": _clean_text(intent.get("history_kind", "")),
                    "path_mode": _clean_text(path_realization.get("path_output_mode", intent.get("path_mode", ""))),
                    "blocked_node_refs": list(path_realization.get("blocked_node_refs", []) or []),
                    "missing_bridge_refs": list(path_realization.get("missing_bridge_refs", []) or []),
                },
            )
        )
    if (
        _normalize(intent.get("kind", "")) == "summary"
        and len([item for item in claims if _clean_text(item.get("claim_type", "")) not in _HARD_REJECT_CLAIM_TYPES]) >= 2
        and not bool(response.unsupported_claims or evidence_pack.get("unsupported_claims", []) or [])
        and not bool(evidence_pack.get("fallback_used", False))
    ):
        supported_claims = [item for item in claims if _clean_text(item.get("claim_type", "")) not in _HARD_REJECT_CLAIM_TYPES]
        summary_memory_ids = _dedupe(memory_id for item in supported_claims for memory_id in item.get("memory_ids", []) or [])
        categories = _dedupe(item.get("category", "") for item in supported_claims if _clean_text(item.get("category", "")))
        summary_text = _clean_text(evidence_pack.get("summary", "")) or _clean_text(response.answer)
        candidates.append(
            WritebackClaimCandidate(
                claim_id=f"summary:{len(candidates)}",
                claim_type="summary_conclusion",
                claim_text=summary_text,
                claim_confidence=min(0.98, max(float(item.get("confidence", 0.0) or 0.0) for item in supported_claims)),
                memory_ids=summary_memory_ids,
                fact_refs=_dedupe(ref for item in supported_claims for ref in item.get("fact_refs", []) or []),
                path_refs=_dedupe(ref for item in supported_claims for ref in item.get("path_refs", []) or []),
                category="summary",
                canonical_slot_key=f"summary.{'.'.join(categories[:3])}" if categories else "summary.general",
                candidate_slot_pool=_canonical_slot_pool(record_map, {"memory_ids": summary_memory_ids}),
                support_count=len(summary_memory_ids),
                fallback_used=False,
                unsupported=False,
                structured_value={"summary_categories": categories},
                metadata={"source": "overlay_summary", "query_text": query_text, "intent_kind": "summary"},
            )
        )
    return candidates


class AnswerWritebackManager:
    def __init__(self, config: WritebackConfig | None = None, provider: TMCRAWritebackJudgeProvider | None = None) -> None:
        self.config = config or WritebackConfig()
        self.provider = provider
        self.llm_client = None
        provider_kind = self._configured_provider()
        if provider_kind == "tmcra_writeback_judge" and self.provider is None:
            self.provider = self._load_provider(self.config)
        if provider_kind == "llm_assist":
            self.llm_client = self._load_llm_client(self.config)

    def process(self, *, query_text: str, response: AdapterResponse, memory_adapter: MemoryAdapter, answer_id: str) -> Dict[str, Any]:
        mode = self._mode()
        if mode == "disabled":
            return WritebackTrace(enabled=False, mode=self.config.mode, provider="rule_only").to_dict()
        bundle = dict(response.metadata.get("tmcra_reasoning_bundle", {}) or {})
        evidence_pack = dict(response.metadata.get("overlay_evidence_pack", {}) or {})
        intent = dict(bundle.get("intent", {}) or {})
        candidates = extract_writeback_candidates(query_text=query_text, response=response)
        global_reject_reason = self._global_reject_reason(response=response, evidence_pack=evidence_pack)
        claim_traces: List[WritebackTraceRecord] = []
        writeback_records: List[Dict[str, Any]] = []
        token_usage: Dict[str, int] = {}
        for candidate in candidates:
            rule_decision = self._rule_decision(candidate)
            model_decision = WritebackDecision(provider=self._trace_provider_name(), rejected_reason="not_consulted")
            final_decision = rule_decision
            if global_reject_reason:
                rule_decision = self._reject_decision(global_reject_reason)
                final_decision = rule_decision
                model_decision = WritebackDecision(provider=self._trace_provider_name(), rejected_reason="global_veto")
            elif self._should_consult_model(candidate, rule_decision):
                model_decision, usage = self._consult_model(query_text=query_text, intent=intent, candidate=candidate)
                token_usage = self._merge_token_usage(token_usage, usage)
                if self._can_apply_model_decision(candidate, model_decision):
                    final_decision = self._normalize_model_decision(candidate, model_decision)
            record_payload: Dict[str, Any] = {}
            if final_decision.gate == "write":
                record_payload = self._build_writeback_record(candidate, final_decision, answer_id=answer_id, query_text=query_text)
                writeback_records.append(record_payload)
            claim_traces.append(
                WritebackTraceRecord(
                    claim_id=candidate.claim_id,
                    claim_type=candidate.claim_type,
                    rule_decision=rule_decision.to_dict(),
                    model_decision=model_decision.to_dict(),
                    final_decision=final_decision.to_dict(),
                    rule_agreement=1.0 if final_decision.gate == rule_decision.gate and final_decision.writeback_class == rule_decision.writeback_class else 0.0,
                    teacher_agreement=0.0,
                    rejected_reason=final_decision.rejected_reason,
                    writeback_record=record_payload,
                )
            )
        stored_record_ids: List[str] = []
        promotion_events: List[Dict[str, Any]] = []
        if self._write_enabled() and writeback_records:
            stored_record_ids = memory_adapter.ingest_answer_writeback(
                query_text=query_text,
                answer_text=response.answer,
                answer_id=answer_id,
                writeback_records=writeback_records,
                trace={
                    "claims": [item.to_dict() for item in claim_traces],
                    "global_reject_reason": global_reject_reason,
                    "token_usage": dict(token_usage),
                },
            )
            summary = self._read_adapter_writeback_summary(memory_adapter)
            promotion_events = [dict(item) for item in summary.get("promotion_events", []) or [] if isinstance(item, dict)]
        trace = WritebackTrace(
            enabled=True,
            mode=self.config.mode,
            provider=self._trace_provider_name(),
            candidates=[item.to_dict() for item in candidates],
            records=list(writeback_records),
            claims=[item.to_dict() for item in claim_traces],
            stored_record_ids=list(stored_record_ids),
            written_count=len(stored_record_ids),
            rejected_count=sum(1 for item in claim_traces if _normalize(item.final_decision.get("gate", "")) != "write"),
            promotion_events=promotion_events,
            global_reject_reason=global_reject_reason,
            token_usage=token_usage,
        ).to_dict()
        trace["candidate_count"] = len(candidates)
        return trace

    def _load_provider(self, config: WritebackConfig) -> TMCRAWritebackJudgeProvider | None:
        manifest_path = _clean_text(config.manifest_path)
        if manifest_path and Path(manifest_path).exists():
            return TMCRAWritebackJudgeProvider.from_manifest(manifest_path)
        model_paths = [_clean_text(config.gate_model_path), _clean_text(config.class_model_path), _clean_text(config.slot_model_path)]
        if all(model_paths) and all(Path(path).exists() for path in model_paths):
            return TMCRAWritebackJudgeProvider(
                gate_model_path=model_paths[0],
                class_model_path=model_paths[1],
                slot_model_path=model_paths[2],
                config=WritebackJudgeConfig(min_confidence=float(config.min_confidence or 0.8)),
            )
        return None

    def _load_llm_client(self, config: WritebackConfig) -> Any | None:
        profile = config.profile
        if OpenAI is None or not _clean_text(profile.base_url) or not _clean_text(profile.model):
            return None
        try:
            return OpenAI(base_url=profile.base_url, api_key=profile.api_key or "EMPTY")
        except Exception:  # pragma: no cover
            return None

    def _configured_provider(self) -> str:
        provider = _normalize(self.config.provider)
        if provider in {"llm_assist", "tmcra_writeback_judge", "rule_only"}:
            return provider
        return "rule_only"

    def _mode(self) -> str:
        mode = _normalize(self.config.mode)
        if mode in {"enabled", "shadow", "disabled"}:
            return mode
        return "shadow"

    def _write_enabled(self) -> bool:
        return self._mode() == "enabled"

    def _trace_provider_name(self) -> str:
        provider_kind = self._configured_provider()
        if provider_kind == "tmcra_writeback_judge" and self.provider is not None:
            return "tmcra_writeback_judge"
        if provider_kind == "llm_assist" and self.llm_client is not None:
            return "llm_assist"
        return "rule_only"

    def _global_reject_reason(self, *, response: AdapterResponse, evidence_pack: Dict[str, Any]) -> str:
        if not bool(response.evidence_consistent):
            return "evidence_inconsistent"
        if list(response.unsupported_claims or []) or list(evidence_pack.get("unsupported_claims", []) or []):
            return "unsupported_claims"
        if bool(evidence_pack.get("fallback_used", False)):
            return "fallback_used"
        return ""

    def _reject_decision(self, reason: str) -> WritebackDecision:
        return WritebackDecision(gate="skip", writeback_class="reject", provider="rule", decision_valid=True, rejected_reason=reason)

    def _should_consult_model(self, candidate: WritebackClaimCandidate, decision: WritebackDecision) -> bool:
        if self._trace_provider_name() == "rule_only":
            return False
        if decision.writeback_class not in _WRITEBACK_CLASSES:
            return False
        if decision.gate == "skip" and decision.rejected_reason != "borderline_confidence":
            return False
        return self._is_boundary_candidate(candidate, decision.writeback_class)

    def _rule_decision(self, candidate: WritebackClaimCandidate) -> WritebackDecision:
        if candidate.unsupported:
            return self._reject_decision("unsupported_claim")
        if candidate.fallback_used:
            return self._reject_decision("fallback_used")
        if candidate.has_conflict or candidate.claim_type in _HARD_REJECT_CLAIM_TYPES:
            return self._reject_decision("conflict_or_missing")
        if self._contains_uncertain_language(candidate.claim_text):
            return self._reject_decision("uncertain_language")
        if self._contains_rhetorical_language(candidate.claim_text):
            return self._reject_decision("rhetorical_text")
        if not self._support_refs(candidate):
            return self._reject_decision("no_support_refs")

        writeback_class = self._expected_writeback_class(candidate)
        if not writeback_class:
            return self._reject_decision("unsupported_claim_type")
        if writeback_class == "state_change" and not self._has_state_change_values(candidate):
            return self._reject_decision("incomplete_state_change")
        if candidate.claim_type == "path_claim" and self._path_semantics_unstable(candidate):
            return self._reject_decision("path_unstable")

        target_slot = self._candidate_target_slot(candidate, writeback_class)
        if not target_slot:
            return self._reject_decision("slot_ambiguous")

        threshold = self._hard_threshold(writeback_class)
        confidence = _clamp01(candidate.claim_confidence)
        if writeback_class == "high_conf_conclusion" and len(self._support_refs(candidate)) < 2:
            return self._reject_decision("low_support")
        if confidence >= threshold:
            return WritebackDecision(
                gate="write",
                writeback_class=writeback_class,
                target_canonical_slot=target_slot,
                confidence=confidence,
                provider="rule",
                decision_valid=True,
            )
        if confidence >= float(self.config.borderline_min_confidence):
            return WritebackDecision(
                gate="skip",
                writeback_class=writeback_class,
                target_canonical_slot=target_slot,
                confidence=confidence,
                provider="rule",
                decision_valid=True,
                rejected_reason="borderline_confidence",
            )
        return self._reject_decision("low_confidence")

    def _consult_model(self, *, query_text: str, intent: Dict[str, Any], candidate: WritebackClaimCandidate) -> tuple[WritebackDecision, Dict[str, int]]:
        provider_kind = self._configured_provider()
        if provider_kind == "tmcra_writeback_judge" and self.provider is not None:
            try:
                inferred = self.provider.predict(
                    {
                        "query": query_text,
                        "intent": dict(intent or {}),
                        "claim_candidate": candidate.to_dict(),
                        "candidate_slot_pool": [dict(item) for item in candidate.candidate_slot_pool],
                    }
                )
                return inferred.decision, {}
            except Exception as exc:  # pragma: no cover
                return WritebackDecision(provider="tmcra_writeback_judge", rejected_reason=f"provider_error:{type(exc).__name__}"), {}
        if provider_kind == "llm_assist" and self.llm_client is not None:
            return self._llm_assist_decision(query_text=query_text, intent=intent, candidate=candidate)
        return WritebackDecision(provider=self._trace_provider_name(), rejected_reason="provider_unavailable"), {}

    def _llm_assist_decision(self, *, query_text: str, intent: Dict[str, Any], candidate: WritebackClaimCandidate) -> tuple[WritebackDecision, Dict[str, int]]:
        payload = {
            "task": "Decide whether this grounded structured claim may be written back into TMCRA assistant memory.",
            "query": query_text,
            "intent": dict(intent or {}),
            "claim_candidate": candidate.to_dict(),
            "candidate_slot_pool": [dict(item) for item in candidate.candidate_slot_pool],
            "required_output_schema": {
                "write": True,
                "writeback_class": "fact|state_change|high_conf_conclusion|reject",
                "target_slot_key": "canonical slot key or empty string",
                "confidence": 0.0,
                "reason": "short reason",
            },
        }
        try:
            completion = self.llm_client.chat.completions.create(
                model=self.config.profile.model,
                messages=[
                    {"role": "system", "content": self.config.profile.system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=float(self.config.profile.temperature),
                max_tokens=int(self.config.profile.max_tokens),
                timeout=float(self.config.profile.timeout_seconds),
            )
            usage = _completion_usage_dict(completion)
            raw_content = _clean_text(completion.choices[0].message.content if getattr(completion, "choices", None) else "")
            parsed = self._parse_model_json(raw_content)
            if not parsed:
                return WritebackDecision(provider="llm_assist", rejected_reason="invalid_json"), usage
            gate = "write" if self._truthy_flag(parsed.get("write")) else "skip"
            writeback_class = _normalize(parsed.get("writeback_class", "")) or ("reject" if gate != "write" else "")
            confidence = _clamp01(parsed.get("confidence", 0.0))
            reason = _clean_text(parsed.get("reason", ""))
            return (
                WritebackDecision(
                    gate=gate,
                    writeback_class=writeback_class,
                    target_canonical_slot=_clean_text(parsed.get("target_slot_key", "")),
                    confidence=confidence,
                    provider="llm_assist",
                    decision_valid=True,
                    model_scores={"reason": reason, **({"usage": usage} if usage else {})},
                    rejected_reason=reason if gate != "write" else "",
                ),
                usage,
            )
        except Exception as exc:  # pragma: no cover
            return WritebackDecision(provider="llm_assist", rejected_reason=f"provider_error:{type(exc).__name__}"), {}

    def _can_apply_model_decision(self, candidate: WritebackClaimCandidate, decision: WritebackDecision) -> bool:
        if not decision.decision_valid:
            return False
        if float(decision.confidence or 0.0) < float(self.config.min_confidence):
            return False
        if decision.gate != "write":
            return True
        expected_class = self._expected_writeback_class(candidate)
        return bool(self._validated_target_slot(candidate, decision.target_canonical_slot, expected_class))

    def _normalize_model_decision(self, candidate: WritebackClaimCandidate, decision: WritebackDecision) -> WritebackDecision:
        if decision.gate != "write":
            return WritebackDecision(
                gate="skip",
                writeback_class="reject",
                confidence=decision.confidence,
                provider=decision.provider,
                decision_valid=True,
                model_scores=dict(decision.model_scores),
                rejected_reason=decision.rejected_reason or _clean_text(dict(decision.model_scores).get("reason", "")) or "model_skip",
            )
        expected_class = self._expected_writeback_class(candidate)
        return WritebackDecision(
            gate="write",
            writeback_class=expected_class,
            target_canonical_slot=self._validated_target_slot(candidate, decision.target_canonical_slot, expected_class),
            confidence=decision.confidence,
            provider=decision.provider,
            decision_valid=True,
            model_scores=dict(decision.model_scores),
        )

    def _best_slot(self, candidate: WritebackClaimCandidate) -> str:
        if candidate.canonical_slot_key:
            return candidate.canonical_slot_key
        if candidate.candidate_slot_pool:
            top = max(candidate.candidate_slot_pool, key=lambda item: float(item.get("score", 0.0) or 0.0))
            return _clean_text(top.get("canonical_slot_key", "")) or _clean_text(top.get("slot_key", ""))
        return ""

    def _candidate_target_slot(self, candidate: WritebackClaimCandidate, writeback_class: str) -> str:
        fallback = "path.summary" if writeback_class == "high_conf_conclusion" and candidate.claim_type == "path_claim" else ""
        return self._validated_target_slot(candidate, candidate.canonical_slot_key or self._best_slot(candidate) or fallback, writeback_class)

    def _validated_target_slot(self, candidate: WritebackClaimCandidate, target_slot: str, writeback_class: str) -> str:
        target = _clean_text(target_slot)
        allowed = {
            _normalize(candidate.canonical_slot_key),
            *{
                _normalize(item.get("canonical_slot_key", "")) or _normalize(item.get("slot_key", ""))
                for item in candidate.candidate_slot_pool
                if isinstance(item, dict)
            },
        }
        allowed.discard("")
        if target and (not allowed or _normalize(target) in allowed):
            return target
        if self._best_slot(candidate):
            return self._best_slot(candidate)
        if writeback_class == "high_conf_conclusion":
            return _clean_text(candidate.canonical_slot_key) or ("path.summary" if candidate.claim_type == "path_claim" else "summary.general")
        return ""

    def _expected_writeback_class(self, candidate: WritebackClaimCandidate) -> str:
        if candidate.claim_type in _ALLOWED_FACT_TYPES:
            return "fact"
        if candidate.claim_type in _ALLOWED_STATE_CHANGE_TYPES:
            return "state_change"
        if candidate.claim_type in _ALLOWED_CONCLUSION_TYPES:
            return "high_conf_conclusion"
        return ""

    def _hard_threshold(self, writeback_class: str) -> float:
        return 0.9 if _normalize(writeback_class) == "high_conf_conclusion" else 0.85

    def _support_refs(self, candidate: WritebackClaimCandidate) -> List[str]:
        return _dedupe([*list(candidate.memory_ids), *list(candidate.fact_refs), *list(candidate.path_refs)])

    def _contains_uncertain_language(self, text: str) -> bool:
        lowered = _normalize(text)
        return any(marker in lowered for marker in _UNCERTAIN_MARKERS)

    def _contains_rhetorical_language(self, text: str) -> bool:
        lowered = _normalize(text)
        return any(marker in lowered for marker in _RHETORICAL_MARKERS)

    def _has_state_change_values(self, candidate: WritebackClaimCandidate) -> bool:
        structured = dict(candidate.structured_value or {})
        return bool(_clean_text(structured.get("previous_value", "")) and _clean_text(structured.get("current_value", "")))

    def _path_semantics_unstable(self, candidate: WritebackClaimCandidate) -> bool:
        metadata = dict(candidate.metadata or {})
        return _normalize(metadata.get("path_mode", "")) == "counterfactual" or bool(metadata.get("blocked_node_refs") or metadata.get("missing_bridge_refs"))

    def _is_boundary_candidate(self, candidate: WritebackClaimCandidate, writeback_class: str) -> bool:
        confidence = _clamp01(candidate.claim_confidence)
        threshold = self._hard_threshold(writeback_class)
        return (
            (confidence >= float(self.config.borderline_min_confidence) and confidence < threshold)
            or writeback_class == "high_conf_conclusion"
            or len(_tokenize(candidate.claim_text)) >= 24
            or candidate.claim_type == "summary_conclusion"
        )

    def _parse_model_json(self, raw_content: str) -> Dict[str, Any]:
        content = _clean_text(raw_content)
        if not content:
            return {}
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
            content = re.sub(r"\s*```$", "", content)
        try:
            payload = json.loads(content)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            start = content.find("{")
            end = content.rfind("}")
            if start < 0 or end <= start:
                return {}
            try:
                payload = json.loads(content[start : end + 1])
                return payload if isinstance(payload, dict) else {}
            except Exception:
                return {}

    def _truthy_flag(self, value: Any) -> bool:
        return value is True or _normalize(value) in {"true", "1", "yes", "write"}

    def _merge_token_usage(self, current: Dict[str, int], new_usage: Dict[str, int]) -> Dict[str, int]:
        merged = dict(current or {})
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            merged[key] = int(merged.get(key, 0) or 0) + int(new_usage.get(key, 0) or 0)
        return {key: value for key, value in merged.items() if value}

    def _read_adapter_writeback_summary(self, memory_adapter: MemoryAdapter) -> Dict[str, Any]:
        if hasattr(memory_adapter, "last_writeback_summary"):
            try:
                return dict(memory_adapter.last_writeback_summary() or {})
            except Exception:
                return {}
        base_adapter = getattr(memory_adapter, "base_adapter", None)
        if base_adapter is not None and hasattr(base_adapter, "last_writeback_summary"):
            try:
                return dict(base_adapter.last_writeback_summary() or {})
            except Exception:
                return {}
        return {}

    def _fact_value(self, candidate: WritebackClaimCandidate) -> str:
        structured = dict(candidate.structured_value or {})
        return _clean_text(structured.get("current_value", "")) or _clean_text(next((item.get("value", "") for item in candidate.candidate_slot_pool if isinstance(item, dict)), "")) or _clean_text(candidate.claim_text)

    def _state_change_values(self, candidate: WritebackClaimCandidate) -> Dict[str, str]:
        structured = dict(candidate.structured_value or {})
        return {
            "previous_value": _clean_text(structured.get("previous_value", "")),
            "current_value": _clean_text(structured.get("current_value", "")),
            "previous_memory_id": _clean_text(candidate.memory_ids[0] if candidate.memory_ids else ""),
            "current_memory_id": _clean_text(candidate.memory_ids[-1] if candidate.memory_ids else ""),
        }

    def _build_writeback_record(
        self,
        candidate: WritebackClaimCandidate,
        decision: WritebackDecision,
        *,
        answer_id: str,
        query_text: str,
    ) -> Dict[str, Any]:
        canonical_slot = decision.target_canonical_slot or self._candidate_target_slot(candidate, decision.writeback_class) or "summary.general"
        namespaced_slot = f"assistant.{canonical_slot}.{decision.writeback_class}"
        relation = {
            "fact": "assistant_fact",
            "state_change": "assistant_state_change",
            "high_conf_conclusion": "assistant_conclusion",
        }.get(decision.writeback_class, "assistant_memory")
        source_kind = {
            "fact": "assistant_fact_memory",
            "state_change": "assistant_state_change_memory",
            "high_conf_conclusion": "assistant_conclusion_memory",
        }.get(decision.writeback_class, "assistant_memory")
        anchors: List[str] = []
        for slot in candidate.candidate_slot_pool[:2]:
            if isinstance(slot, dict):
                anchors.extend([slot.get("canonical_slot_key", ""), slot.get("value", "")])
        structured_value = dict(candidate.structured_value or {})
        metadata: Dict[str, Any] = {
            "memory_role": "assistant",
            "authority": "derived",
            "canonical_slot_key": canonical_slot,
            "writeback_class": decision.writeback_class,
            "origin_query": query_text,
            "origin_answer_id": answer_id,
            "support_memory_ids": list(candidate.memory_ids),
            "support_fact_refs": list(candidate.fact_refs),
            "support_path_refs": list(candidate.path_refs),
            "promotion_state": "candidate",
            "claim_id": candidate.claim_id,
            "claim_type": candidate.claim_type,
            "structured_value": structured_value,
            "support_count": int(candidate.support_count),
            "user_slot_protected": True,
        }
        value = _clean_text(candidate.claim_text)
        if decision.writeback_class == "fact":
            value = self._fact_value(candidate)
            structured_value["current_value"] = value
        elif decision.writeback_class == "state_change":
            state_change = self._state_change_values(candidate)
            value = state_change["current_value"] or self._fact_value(candidate)
            structured_value.update({key: val for key, val in state_change.items() if key.endswith("_value") and val})
            metadata.update(state_change)
        return {
            "category": candidate.category or ("summary" if decision.writeback_class == "high_conf_conclusion" else "fact"),
            "slot_key": namespaced_slot,
            "slot": namespaced_slot,
            "value": value,
            "anchors": _dedupe(anchors)[:8],
            "relation": relation,
            "source_kind": source_kind,
            "confidence": round(float(decision.confidence or candidate.claim_confidence), 6),
            "metadata": metadata,
        }
