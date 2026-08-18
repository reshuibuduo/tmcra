from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence

try:
    import torch
    from torch import nn
    import torch.nn.functional as F
    from torch.utils.data import Dataset
except ModuleNotFoundError:
    torch = None
    nn = None
    F = None
    Dataset = object

from experiments.replacement.injection_planner import (
    InjectionPlannerConfig,
    candidate_scalar_features,
    candidate_text,
    clean_text,
    normalize_training_row,
    vectorize_text,
)


AGGREGATION_ARCHITECTURE = "hashed_candidate_aggregation_unit_planner_v1"
OPERATION_FAMILIES = (
    "none",
    "set_aggregation",
    "numeric_aggregation",
    "entity_attribute_join",
    "unit_relation",
    "time_bucket_filter",
    "current_value_selection",
    "multi_action_instance_count",
)
COMPLETENESS_STATES = ("none", "complete", "partial", "insufficient")
CANDIDATE_ROLES = (
    "answer_unit",
    "supporting_context",
    "old_value",
    "current_value",
    "excluded",
    "planned_not_done",
    "advice_only",
    "noise",
)
ANSWER_UNIT_ROLES = {"answer_unit", "current_value"}
USEFUL_UNIT_ROLES = {"answer_unit", "current_value", "supporting_context"}

_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


def _require_torch() -> None:
    if torch is None or nn is None or F is None:
        raise RuntimeError("torch is required for aggregation unit planner training")


def _normalize_choice(value: Any, *, choices: Sequence[str], default: str) -> str:
    text = clean_text(value).lower().replace("-", "_").replace(" ", "_")
    return text if text in set(choices) else default


def _stable_hash(value: str, *, modulo: int) -> int:
    digest = hashlib.blake2b(clean_text(value).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=False) % modulo


def _tokens(text: str) -> List[str]:
    return [item.lower() for item in _TOKEN_RE.findall(clean_text(text))]


def _query_operation_hints(query: str) -> List[float]:
    query_l = clean_text(query).lower()
    query_tokens = set(_tokens(query_l))
    numeric_markers = {
        "total",
        "sum",
        "amount",
        "cost",
        "price",
        "income",
        "revenue",
        "how",
        "many",
        "much",
    }
    set_markers = {"which", "list", "types", "items", "kinds", "names"}
    temporal_markers = {"before", "after", "first", "last", "current", "latest", "then", "now"}
    relation_markers = {"per", "each", "dozen", "rate", "unit", "times", "x"}
    action_markers = {"return", "pickup", "pick", "exchange", "send", "bring", "drop", "collect"}
    return [
        1.0 if query_tokens & numeric_markers else 0.0,
        1.0 if query_tokens & set_markers else 0.0,
        1.0 if query_tokens & temporal_markers else 0.0,
        1.0 if query_tokens & relation_markers else 0.0,
        1.0 if query_tokens & action_markers else 0.0,
        1.0 if any(ch.isdigit() for ch in query_l) else 0.0,
    ]


def _candidate_unit_hints(candidate: Mapping[str, Any]) -> List[float]:
    text = " ".join(
        clean_text(item)
        for item in (
            candidate.get("text"),
            candidate.get("summary"),
            candidate.get("entity_key"),
            candidate.get("event_key"),
            candidate.get("attribute_key"),
            candidate.get("unit_key"),
            candidate.get("aggregation_group"),
        )
        if clean_text(item)
    )
    text_l = text.lower()
    tokens = set(_tokens(text_l))
    return [
        1.0 if any(ch.isdigit() for ch in text_l) else 0.0,
        1.0 if "$" in text_l or "usd" in tokens or "dollar" in text_l else 0.0,
        1.0 if {"each", "per", "dozen", "unit", "rate"} & tokens else 0.0,
        1.0 if {"total", "sum", "combined"} & tokens else 0.0,
        1.0 if {"old", "previous", "earlier", "before", "superseded"} & tokens else 0.0,
        1.0 if {"current", "latest", "updated", "now"} & tokens else 0.0,
        1.0 if {"planned", "maybe", "considering", "advice", "suggest"} & tokens else 0.0,
        1.0 if clean_text(candidate.get("aggregation_group")) else 0.0,
        1.0 if clean_text(candidate.get("entity_key")) else 0.0,
        1.0 if clean_text(candidate.get("attribute_key")) or clean_text(candidate.get("unit_key")) else 0.0,
    ]


@dataclass(frozen=True)
class AggregationUnitPlannerConfig:
    query_hash_dim: int = 256
    candidate_hash_dim: int = 256
    scalar_dim: int = 39
    hidden_dim: int = 192
    dropout: float = 0.10

    @property
    def input_dim(self) -> int:
        return int(self.query_hash_dim) + int(self.candidate_hash_dim) + int(self.scalar_dim)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "AggregationUnitPlannerConfig":
        if not payload:
            return cls()
        values = dict(payload)
        allowed = {field.name for field in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{key: value for key, value in values.items() if key in allowed})


def _candidate_features(
    query: str,
    candidate: Mapping[str, Any],
    config: AggregationUnitPlannerConfig,
) -> List[float]:
    base_config = InjectionPlannerConfig(
        query_hash_dim=int(config.query_hash_dim),
        candidate_hash_dim=int(config.candidate_hash_dim),
        scalar_dim=23,
        hidden_dim=int(config.hidden_dim),
        dropout=float(config.dropout),
    )
    scalar = (
        candidate_scalar_features(candidate, base_config)
        + _query_operation_hints(query)
        + _candidate_unit_hints(candidate)
    )
    if len(scalar) < int(config.scalar_dim):
        scalar = scalar + [0.0] * (int(config.scalar_dim) - len(scalar))
    if len(scalar) > int(config.scalar_dim):
        scalar = scalar[: int(config.scalar_dim)]
    return (
        vectorize_text(query, dim=int(config.query_hash_dim))
        + vectorize_text(candidate_text(candidate), dim=int(config.candidate_hash_dim))
        + scalar
    )


def _infer_candidate_role(candidate: Mapping[str, Any], gold: Mapping[str, Any]) -> str:
    explicit = _normalize_choice(candidate.get("candidate_role"), choices=CANDIDATE_ROLES, default="")
    if explicit:
        return explicit
    candidate_id = clean_text(candidate.get("id"))
    selected_ids = {clean_text(item) for item in gold.get("selected_candidate_ids", [])}
    evidence_role_by_id = dict(gold.get("evidence_role_by_candidate_id") or {})
    evidence_role = clean_text(evidence_role_by_id.get(candidate_id, candidate.get("evidence_role", ""))).lower()
    temporal_state = clean_text(candidate.get("temporal_state", "")).lower()
    if candidate_id in selected_ids:
        if evidence_role in {"supporting_context", "bridge_context"}:
            return "supporting_context"
        if evidence_role in {"current_value", "updated_value"}:
            return "current_value"
        return "answer_unit"
    if temporal_state in {"superseded", "historical"} or evidence_role == "negative_evidence":
        return "old_value"
    return "noise"


def _normalize_gold_operation(gold: Mapping[str, Any]) -> str:
    return _normalize_choice(gold.get("operation_family"), choices=OPERATION_FAMILIES, default="none")


def _normalize_gold_completeness(gold: Mapping[str, Any]) -> str:
    return _normalize_choice(gold.get("completeness"), choices=COMPLETENESS_STATES, default="none")


def flatten_aggregation_rows(
    rows: Iterable[Mapping[str, Any]],
    config: AggregationUnitPlannerConfig | None = None,
) -> List[Dict[str, Any]]:
    resolved = config or AggregationUnitPlannerConfig()
    output: List[Dict[str, Any]] = []
    for raw_row in rows:
        row = normalize_training_row(raw_row)
        query = clean_text(row.get("query"))
        gold = dict(row.get("gold") or {})
        candidates = []
        for candidate in list(row.get("candidates") or []):
            if not isinstance(candidate, Mapping):
                continue
            role = _infer_candidate_role(candidate, gold)
            candidates.append(
                {
                    "id": clean_text(candidate.get("id")),
                    "features": _candidate_features(query, candidate, resolved),
                    "candidate_role_target": CANDIDATE_ROLES.index(role),
                    "answer_unit_target": 1.0 if role in ANSWER_UNIT_ROLES else 0.0,
                    "useful_unit_target": 1.0 if role in USEFUL_UNIT_ROLES else 0.0,
                    "raw": dict(candidate),
                }
            )
        operation = _normalize_gold_operation(gold)
        completeness = _normalize_gold_completeness(gold)
        output.append(
            {
                "id": clean_text(row.get("id")) or f"row_{_stable_hash(str(raw_row), modulo=10**12)}",
                "query": query,
                "candidates": candidates,
                "operation_target": OPERATION_FAMILIES.index(operation),
                "completeness_target": COMPLETENESS_STATES.index(completeness),
                "requires_multi_evidence_target": 1.0 if bool(gold.get("requires_multi_evidence")) else 0.0,
                "raw": dict(row),
            }
        )
    return output


class AggregationUnitPlannerDataset(Dataset):  # type: ignore[misc]
    def __init__(
        self,
        rows: Sequence[Mapping[str, Any]],
        config: AggregationUnitPlannerConfig | None = None,
    ):
        _require_torch()
        self.config = config or AggregationUnitPlannerConfig()
        self.examples = flatten_aggregation_rows(rows, self.config)

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        example = self.examples[index]
        candidates = list(example["candidates"])
        if not candidates:
            candidates = [
                {
                    "id": "__empty__",
                    "features": [0.0] * int(self.config.input_dim),
                    "candidate_role_target": CANDIDATE_ROLES.index("noise"),
                    "answer_unit_target": 0.0,
                    "useful_unit_target": 0.0,
                    "raw": {},
                }
            ]
        return {
            "id": example["id"],
            "features": torch.tensor([item["features"] for item in candidates], dtype=torch.float32),
            "valid_mask": torch.tensor([item["id"] != "__empty__" for item in candidates], dtype=torch.bool),
            "candidate_role_target": torch.tensor([item["candidate_role_target"] for item in candidates], dtype=torch.long),
            "answer_unit_target": torch.tensor([item["answer_unit_target"] for item in candidates], dtype=torch.float32),
            "useful_unit_target": torch.tensor([item["useful_unit_target"] for item in candidates], dtype=torch.float32),
            "operation_target": torch.tensor(int(example["operation_target"]), dtype=torch.long),
            "completeness_target": torch.tensor(int(example["completeness_target"]), dtype=torch.long),
            "requires_multi_evidence_target": torch.tensor(float(example["requires_multi_evidence_target"]), dtype=torch.float32),
        }


def collate_aggregation_batch(batch: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    _require_torch()
    batch_size = len(batch)
    max_candidates = max(int(item["features"].shape[0]) for item in batch)
    input_dim = int(batch[0]["features"].shape[-1])
    features = torch.zeros((batch_size, max_candidates, input_dim), dtype=torch.float32)
    valid_mask = torch.zeros((batch_size, max_candidates), dtype=torch.bool)
    candidate_role_target = torch.full(
        (batch_size, max_candidates),
        CANDIDATE_ROLES.index("noise"),
        dtype=torch.long,
    )
    answer_unit_target = torch.zeros((batch_size, max_candidates), dtype=torch.float32)
    useful_unit_target = torch.zeros((batch_size, max_candidates), dtype=torch.float32)
    for row_index, item in enumerate(batch):
        count = int(item["features"].shape[0])
        features[row_index, :count] = item["features"]
        valid_mask[row_index, :count] = item["valid_mask"]
        candidate_role_target[row_index, :count] = item["candidate_role_target"]
        answer_unit_target[row_index, :count] = item["answer_unit_target"]
        useful_unit_target[row_index, :count] = item["useful_unit_target"]
    return {
        "ids": [str(item["id"]) for item in batch],
        "features": features,
        "valid_mask": valid_mask,
        "candidate_role_target": candidate_role_target,
        "answer_unit_target": answer_unit_target,
        "useful_unit_target": useful_unit_target,
        "operation_target": torch.stack([item["operation_target"] for item in batch]),
        "completeness_target": torch.stack([item["completeness_target"] for item in batch]),
        "requires_multi_evidence_target": torch.stack([item["requires_multi_evidence_target"] for item in batch]),
    }


class AggregationUnitPlannerModel(nn.Module if nn is not None else object):  # type: ignore[misc]
    def __init__(self, config: AggregationUnitPlannerConfig | Mapping[str, Any] | None = None):
        _require_torch()
        super().__init__()
        self.config = config if isinstance(config, AggregationUnitPlannerConfig) else AggregationUnitPlannerConfig.from_dict(config)
        hidden = int(self.config.hidden_dim)
        self.candidate_encoder = nn.Sequential(
            nn.Linear(self.config.input_dim, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
            nn.Dropout(float(self.config.dropout)),
            nn.Linear(hidden, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
        )
        self.candidate_role_head = nn.Linear(hidden, len(CANDIDATE_ROLES))
        self.answer_unit_head = nn.Linear(hidden, 1)
        self.useful_unit_head = nn.Linear(hidden, 1)
        self.row_encoder = nn.Sequential(
            nn.Linear(hidden * 2, hidden),
            nn.SiLU(),
            nn.Dropout(float(self.config.dropout)),
        )
        self.operation_head = nn.Linear(hidden, len(OPERATION_FAMILIES))
        self.completeness_head = nn.Linear(hidden, len(COMPLETENESS_STATES))
        self.requires_multi_head = nn.Linear(hidden, 1)

    def forward(self, features: Any, valid_mask: Any | None = None) -> Dict[str, Any]:
        encoded = self.candidate_encoder(features)
        if valid_mask is None:
            valid_mask = torch.ones(encoded.shape[:2], dtype=torch.bool, device=encoded.device)
        mask_float = valid_mask.to(dtype=encoded.dtype).unsqueeze(-1)
        masked_encoded = encoded * mask_float
        count = mask_float.sum(dim=1).clamp_min(1.0)
        mean_pool = masked_encoded.sum(dim=1) / count
        max_source = encoded.masked_fill(~valid_mask.unsqueeze(-1), -1e4)
        max_pool = torch.max(max_source, dim=1).values
        max_pool = torch.where(torch.isfinite(max_pool), max_pool, torch.zeros_like(max_pool))
        row_hidden = self.row_encoder(torch.cat([mean_pool, max_pool], dim=-1))
        return {
            "candidate_hidden": encoded,
            "candidate_role_logits": self.candidate_role_head(encoded),
            "answer_unit_logits": self.answer_unit_head(encoded).squeeze(-1),
            "useful_unit_logits": self.useful_unit_head(encoded).squeeze(-1),
            "operation_logits": self.operation_head(row_hidden),
            "completeness_logits": self.completeness_head(row_hidden),
            "requires_multi_logits": self.requires_multi_head(row_hidden).squeeze(-1),
        }


def loss_for_aggregation_batch(
    outputs: Mapping[str, Any],
    batch: Mapping[str, Any],
    *,
    weights: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    _require_torch()
    raw_weights = dict(weights or {})
    candidate_role_class_weight = raw_weights.pop("candidate_role_class_weight", None)
    operation_class_weight = raw_weights.pop("operation_class_weight", None)
    completeness_class_weight = raw_weights.pop("completeness_class_weight", None)
    resolved = {
        "candidate_role": 1.0,
        "answer_unit": 0.8,
        "useful_unit": 0.6,
        "operation": 0.8,
        "completeness": 1.0,
        "requires_multi": 0.5,
        **raw_weights,
    }
    valid_mask = batch["valid_mask"]
    if int(valid_mask.sum().item()) > 0:
        candidate_role_loss = F.cross_entropy(
            outputs["candidate_role_logits"][valid_mask],
            batch["candidate_role_target"][valid_mask],
            weight=(
                candidate_role_class_weight.to(outputs["candidate_role_logits"].device)
                if hasattr(candidate_role_class_weight, "to")
                else candidate_role_class_weight
            ),
        )
        answer_unit_loss = F.binary_cross_entropy_with_logits(
            outputs["answer_unit_logits"][valid_mask],
            batch["answer_unit_target"][valid_mask],
        )
        useful_unit_loss = F.binary_cross_entropy_with_logits(
            outputs["useful_unit_logits"][valid_mask],
            batch["useful_unit_target"][valid_mask],
        )
    else:
        zero = outputs["requires_multi_logits"].sum() * 0.0
        candidate_role_loss = zero
        answer_unit_loss = zero
        useful_unit_loss = zero
    operation_loss = F.cross_entropy(
        outputs["operation_logits"],
        batch["operation_target"],
        weight=(
            operation_class_weight.to(outputs["operation_logits"].device)
            if hasattr(operation_class_weight, "to")
            else operation_class_weight
        ),
    )
    completeness_loss = F.cross_entropy(
        outputs["completeness_logits"],
        batch["completeness_target"],
        weight=(
            completeness_class_weight.to(outputs["completeness_logits"].device)
            if hasattr(completeness_class_weight, "to")
            else completeness_class_weight
        ),
    )
    requires_multi_loss = F.binary_cross_entropy_with_logits(
        outputs["requires_multi_logits"],
        batch["requires_multi_evidence_target"],
    )
    total = (
        resolved["candidate_role"] * candidate_role_loss
        + resolved["answer_unit"] * answer_unit_loss
        + resolved["useful_unit"] * useful_unit_loss
        + resolved["operation"] * operation_loss
        + resolved["completeness"] * completeness_loss
        + resolved["requires_multi"] * requires_multi_loss
    )
    return {
        "loss": total,
        "candidate_role_loss": candidate_role_loss.detach(),
        "answer_unit_loss": answer_unit_loss.detach(),
        "useful_unit_loss": useful_unit_loss.detach(),
        "operation_loss": operation_loss.detach(),
        "completeness_loss": completeness_loss.detach(),
        "requires_multi_loss": requires_multi_loss.detach(),
    }


def load_checkpoint(checkpoint_path: str, *, device: str | None = None) -> tuple[AggregationUnitPlannerModel, AggregationUnitPlannerConfig, Dict[str, Any]]:
    _require_torch()
    resolved_device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
    payload = torch.load(checkpoint_path, map_location=resolved_device, weights_only=False)
    config = AggregationUnitPlannerConfig.from_dict(payload.get("config", {}) or {})
    model = AggregationUnitPlannerModel(config).to(resolved_device)
    model.load_state_dict(dict(payload.get("state_dict", {}) or {}))
    model.eval()
    return model, config, payload


@torch.no_grad() if torch is not None else (lambda fn: fn)
def predict_row(
    model: AggregationUnitPlannerModel,
    config: AggregationUnitPlannerConfig,
    row: Mapping[str, Any],
    *,
    device: str | None = None,
) -> Dict[str, Any]:
    _require_torch()
    resolved_device = torch.device(device or next(model.parameters()).device)
    dataset = AggregationUnitPlannerDataset([row], config)
    batch = collate_aggregation_batch([dataset[0]])
    tensor_batch = {key: value.to(resolved_device) if hasattr(value, "to") else value for key, value in batch.items()}
    outputs = model(tensor_batch["features"], tensor_batch["valid_mask"])
    answer_scores = torch.sigmoid(outputs["answer_unit_logits"])[0].detach().cpu().tolist()
    useful_scores = torch.sigmoid(outputs["useful_unit_logits"])[0].detach().cpu().tolist()
    role_indices = torch.argmax(outputs["candidate_role_logits"], dim=-1)[0].detach().cpu().tolist()
    operation_index = int(torch.argmax(outputs["operation_logits"], dim=-1)[0].detach().cpu().item())
    completeness_index = int(torch.argmax(outputs["completeness_logits"], dim=-1)[0].detach().cpu().item())
    requires_multi_score = float(torch.sigmoid(outputs["requires_multi_logits"])[0].detach().cpu().item())
    raw_candidates = [dict(item) for item in list(row.get("candidates") or []) if isinstance(item, Mapping)]
    candidates = []
    for index, candidate in enumerate(raw_candidates):
        candidates.append(
            {
                "id": clean_text(candidate.get("id")) or f"c{index + 1}",
                "answer_unit_score": float(answer_scores[index]) if index < len(answer_scores) else 0.0,
                "useful_unit_score": float(useful_scores[index]) if index < len(useful_scores) else 0.0,
                "candidate_role": CANDIDATE_ROLES[int(role_indices[index])] if index < len(role_indices) else "noise",
            }
        )
    candidates.sort(key=lambda item: (-(item["answer_unit_score"] + item["useful_unit_score"]), item["id"]))
    return {
        "architecture": AGGREGATION_ARCHITECTURE,
        "operation_family": OPERATION_FAMILIES[operation_index],
        "completeness": COMPLETENESS_STATES[completeness_index],
        "requires_multi_evidence_score": requires_multi_score,
        "candidates": candidates,
    }
