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
except ModuleNotFoundError:  # Allows corpus validation on machines without torch.
    torch = None
    nn = None
    F = None
    Dataset = object


EVIDENCE_ROLES = ("positive", "negative", "constraint", "context", "duplicate", "noise")
ANSWER_UNIT_ROLES = ("core", "support", "calculation", "anchor", "conflict", "background")
OPERATION_FAMILIES = (
    "direct",
    "temporal",
    "count",
    "sum",
    "ratio",
    "profile",
    "current_value",
    "synthesis",
    "unknown",
)
UNIFIED_PLANNER_ARCHITECTURE = "hashed_unified_operation_planner_v1"

BENCHMARK_BLOCKLIST = (
    "longmemeval",
    "longmem eval",
    "official benchmark",
    "official dataset",
    "benchmark sample",
    "s500",
    "stratified48",
)

_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


def _require_torch() -> None:
    if torch is None or nn is None or F is None:
        raise RuntimeError("torch is required for unified operation planner training")


def clean_text(value: Any) -> str:
    return " ".join(("" if value is None else str(value)).strip().split())


def tokens(text: str) -> List[str]:
    return [item.lower() for item in _TOKEN_RE.findall(clean_text(text))]


def stable_hash(value: str, *, modulo: int) -> int:
    if modulo <= 0:
        raise ValueError("modulo must be positive")
    digest = hashlib.blake2b(clean_text(value).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=False) % modulo


def normalize_label(value: Any, *, choices: Sequence[str], default: str) -> str:
    text = clean_text(value).lower().replace("-", "_").replace(" ", "_")
    return text if text in set(choices) else default


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _float_feature(value: Any, *, default: float = 0.0, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if math.isnan(parsed) or math.isinf(parsed):
        parsed = default
    return max(lo, min(hi, parsed))


def vectorize_text(text: str, *, dim: int) -> List[float]:
    values = [0.0] * int(dim)
    for token in tokens(text):
        index = stable_hash(token, modulo=int(dim))
        sign = -1.0 if stable_hash("sign:" + token, modulo=2) == 0 else 1.0
        values[index] += sign
    norm = math.sqrt(sum(value * value for value in values))
    if norm > 0.0:
        values = [value / norm for value in values]
    return values


@dataclass(frozen=True)
class UnifiedPlannerConfig:
    query_hash_dim: int = 256
    unit_hash_dim: int = 256
    scalar_dim: int = 18
    hidden_dim: int = 224
    dropout: float = 0.10

    @property
    def input_dim(self) -> int:
        return int(self.query_hash_dim) + int(self.unit_hash_dim) + int(self.scalar_dim)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "UnifiedPlannerConfig":
        if not payload:
            return cls()
        allowed = {field.name for field in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{key: value for key, value in dict(payload).items() if key in allowed})


def contains_blocklisted_benchmark_text(row: Mapping[str, Any]) -> bool:
    raw = json_dumps_compact(row).lower()
    return any(marker in raw for marker in BENCHMARK_BLOCKLIST)


def json_dumps_compact(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _first_nonempty(mapping: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if clean_text(value):
            return value
    return ""


def normalize_unit(unit: Mapping[str, Any]) -> Dict[str, Any]:
    unit_id = _first_nonempty(unit, ("unit_id", "id", "memory_id", "record_id"))
    return {
        **dict(unit),
        "unit_id": clean_text(unit_id) or f"unit_{stable_hash(json_dumps_compact(unit), modulo=10**12)}",
        "record_id": clean_text(unit.get("record_id", unit.get("memory_id", ""))),
        "session_id": clean_text(unit.get("session_id", "")),
        "turn_index": int(_float_feature(unit.get("turn_index"), lo=0.0, hi=100000.0)),
        "speaker": normalize_label(unit.get("speaker"), choices=("user", "assistant", "system", "unknown"), default="unknown"),
        "text": clean_text(_first_nonempty(unit, ("text", "excerpt", "summary", "content"))),
        "timestamp": clean_text(unit.get("timestamp", "")),
        "topic_bucket": clean_text(unit.get("topic_bucket", "")),
        "node_features": dict(unit.get("node_features") or {}),
        "graph_neighbors": [clean_text(item) for item in _as_list(unit.get("graph_neighbors")) if clean_text(item)],
    }


def normalize_operation(payload: Mapping[str, Any] | None) -> Dict[str, Any]:
    raw = dict(payload or {})
    return {
        "requires_temporal": bool(raw.get("requires_temporal", False)),
        "requires_aggregation": bool(raw.get("requires_aggregation", False)),
        "requires_profile": bool(raw.get("requires_profile", False)),
        "requires_current_value": bool(raw.get("requires_current_value", False)),
        "requires_multi_hop": bool(raw.get("requires_multi_hop", False)),
        "operation_family": normalize_label(raw.get("operation_family"), choices=OPERATION_FAMILIES, default="unknown"),
    }


def normalize_contract(payload: Mapping[str, Any] | None) -> Dict[str, Any]:
    raw = dict(payload or {})
    return {
        "selected_unit_ids": [clean_text(item) for item in _as_list(raw.get("selected_unit_ids")) if clean_text(item)],
        "coverage_complete": bool(raw.get("coverage_complete", False)),
        "conflict_detected": bool(raw.get("conflict_detected", False)),
        "confidence": _float_feature(raw.get("confidence"), default=0.0),
    }


def normalize_unit_labels(row: Mapping[str, Any], units: Sequence[Mapping[str, Any]], contract: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    gold = dict(row.get("gold") or {})
    output = dict(row.get("output") or {})
    raw_labels = gold.get("unit_labels", output.get("unit_scores", row.get("unit_scores", [])))
    labels: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw_labels, Mapping):
        iterator = raw_labels.items()
    else:
        iterator = []
        for item in _as_list(raw_labels):
            if isinstance(item, Mapping):
                unit_id = clean_text(item.get("unit_id"))
                if unit_id:
                    iterator.append((unit_id, item))
    selected = {clean_text(item) for item in contract.get("selected_unit_ids", [])}
    for unit_id, raw in iterator:
        item = dict(raw or {})
        labels[clean_text(unit_id)] = {
            "relevance": _float_feature(item.get("relevance", item.get("relevance_score", 0.0))),
            "answer": _float_feature(item.get("answer", item.get("answer_score", 0.0))),
            "temporal": _float_feature(item.get("temporal", item.get("temporal_score", 0.0))),
            "aggregation": _float_feature(item.get("aggregation", item.get("aggregation_score", 0.0))),
            "profile": _float_feature(item.get("profile", item.get("profile_score", 0.0))),
            "current_value": _float_feature(item.get("current_value", item.get("current_value_score", 0.0))),
            "evidence_role": normalize_label(item.get("evidence_role"), choices=EVIDENCE_ROLES, default="context"),
            "answer_unit_role": normalize_label(item.get("answer_unit_role"), choices=ANSWER_UNIT_ROLES, default="background"),
        }
    for unit in units:
        unit_id = clean_text(unit.get("unit_id"))
        if unit_id and unit_id not in labels:
            labels[unit_id] = {
                "relevance": 1.0 if unit_id in selected else 0.0,
                "answer": 1.0 if unit_id in selected else 0.0,
                "temporal": 0.0,
                "aggregation": 0.0,
                "profile": 0.0,
                "current_value": 0.0,
                "evidence_role": "positive" if unit_id in selected else "noise",
                "answer_unit_role": "core" if unit_id in selected else "background",
            }
    return labels


def normalize_training_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    gold = dict(row.get("gold") or {})
    output = dict(row.get("output") or {})
    query_operation = normalize_operation(gold.get("query_operation", output.get("query_operation", row.get("query_operation", {}))))
    answer_contract = normalize_contract(gold.get("answer_contract", output.get("answer_contract", row.get("answer_contract", {}))))
    units = [normalize_unit(unit) for unit in _as_list(row.get("memory_units", row.get("units", []))) if isinstance(unit, Mapping)]
    return {
        **dict(row),
        "id": clean_text(_first_nonempty(row, ("id", "sample_id", "qid"))) or f"row_{stable_hash(json_dumps_compact(row), modulo=10**12)}",
        "query": clean_text(_first_nonempty(row, ("query", "question", "user_message"))),
        "query_time": clean_text(row.get("query_time", "")),
        "retrieval_metadata": dict(row.get("retrieval_metadata") or {}),
        "memory_units": units,
        "query_operation": query_operation,
        "answer_contract": answer_contract,
        "unit_labels": normalize_unit_labels(row, units, answer_contract),
    }


def unit_text(unit: Mapping[str, Any]) -> str:
    return " ".join(
        clean_text(piece)
        for piece in (
            unit.get("text"),
            unit.get("topic_bucket"),
            unit.get("speaker"),
            unit.get("timestamp"),
        )
        if clean_text(piece)
    )


def unit_scalar_features(unit: Mapping[str, Any], row: Mapping[str, Any]) -> List[float]:
    metadata = dict(row.get("retrieval_metadata") or {})
    node_features = dict(unit.get("node_features") or {})
    speaker = clean_text(unit.get("speaker")).lower()
    text = clean_text(unit.get("text"))
    turn_index = _float_feature(unit.get("turn_index"), lo=0.0, hi=100000.0) / 100000.0
    graph_degree = min(len(unit.get("graph_neighbors") or []), 20) / 20.0
    values = [
        1.0 if speaker == "user" else 0.0,
        1.0 if speaker == "assistant" else 0.0,
        1.0 if bool(unit.get("timestamp")) else 0.0,
        1.0 if bool(unit.get("topic_bucket")) else 0.0,
        1.0 if re.search(r"\b\d[\d,.]*\b|\$\s?\d|%", text) else 0.0,
        1.0 if re.search(r"\b(before|after|latest|current|previous|ago|week|month|year|date)\b", text.lower()) else 0.0,
        1.0 if re.search(r"\b(prefer|like|avoid|favorite|usually|always|never)\b", text.lower()) else 0.0,
        turn_index,
        graph_degree,
        _float_feature(node_features.get("retrieval_score", node_features.get("score", 0.0))),
        _float_feature(node_features.get("graph_score", 0.0)),
        _float_feature(node_features.get("tunnel_score", 0.0)),
        min(_float_feature(metadata.get("candidate_count"), lo=0.0, hi=200.0) / 200.0, 1.0),
        min(_float_feature(metadata.get("session_span"), lo=0.0, hi=100.0) / 100.0, 1.0),
        min(_float_feature(metadata.get("topic_span"), lo=0.0, hi=100.0) / 100.0, 1.0),
        1.0 if bool(metadata.get("has_temporal_anchor")) else 0.0,
        1.0 if bool(metadata.get("has_numeric_units")) else 0.0,
        1.0 if bool(metadata.get("has_profile_units")) else 0.0,
    ]
    if len(values) != UnifiedPlannerConfig().scalar_dim:
        raise AssertionError(f"scalar feature dim mismatch: {len(values)}")
    return values


def flatten_training_rows(rows: Iterable[Mapping[str, Any]], config: UnifiedPlannerConfig | None = None) -> List[Dict[str, Any]]:
    resolved = config or UnifiedPlannerConfig()
    examples: List[Dict[str, Any]] = []
    for raw_row in rows:
        row = normalize_training_row(raw_row)
        query_vec = vectorize_text(row["query"], dim=resolved.query_hash_dim)
        unit_items = []
        for unit in row["memory_units"]:
            label = row["unit_labels"].get(unit["unit_id"], {})
            unit_items.append(
                {
                    "unit_id": unit["unit_id"],
                    "features": query_vec + vectorize_text(unit_text(unit), dim=resolved.unit_hash_dim) + unit_scalar_features(unit, row),
                    "relevance_target": 1.0 if _float_feature(label.get("relevance")) >= 0.5 else 0.0,
                    "answer_target": 1.0 if _float_feature(label.get("answer")) >= 0.5 else 0.0,
                    "temporal_target": 1.0 if _float_feature(label.get("temporal")) >= 0.5 else 0.0,
                    "aggregation_target": 1.0 if _float_feature(label.get("aggregation")) >= 0.5 else 0.0,
                    "profile_target": 1.0 if _float_feature(label.get("profile")) >= 0.5 else 0.0,
                    "current_value_target": 1.0 if _float_feature(label.get("current_value")) >= 0.5 else 0.0,
                    "evidence_role_target": EVIDENCE_ROLES.index(normalize_label(label.get("evidence_role"), choices=EVIDENCE_ROLES, default="context")),
                    "answer_unit_role_target": ANSWER_UNIT_ROLES.index(normalize_label(label.get("answer_unit_role"), choices=ANSWER_UNIT_ROLES, default="background")),
                    "raw": unit,
                }
            )
        op = row["query_operation"]
        contract = row["answer_contract"]
        examples.append(
            {
                "id": row["id"],
                "query": row["query"],
                "units": unit_items,
                "requires_temporal_target": 1.0 if op["requires_temporal"] else 0.0,
                "requires_aggregation_target": 1.0 if op["requires_aggregation"] else 0.0,
                "requires_profile_target": 1.0 if op["requires_profile"] else 0.0,
                "requires_current_value_target": 1.0 if op["requires_current_value"] else 0.0,
                "requires_multi_hop_target": 1.0 if op["requires_multi_hop"] else 0.0,
                "operation_family_target": OPERATION_FAMILIES.index(op["operation_family"]),
                "coverage_complete_target": 1.0 if contract["coverage_complete"] else 0.0,
                "conflict_detected_target": 1.0 if contract["conflict_detected"] else 0.0,
                "raw": row,
            }
        )
    return examples


class UnifiedOperationPlannerDataset(Dataset):  # type: ignore[misc]
    def __init__(self, rows: Sequence[Mapping[str, Any]], config: UnifiedPlannerConfig | None = None):
        _require_torch()
        self.config = config or UnifiedPlannerConfig()
        self.examples = flatten_training_rows(rows, self.config)

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        example = self.examples[index]
        units = list(example["units"])
        if not units:
            units = [
                {
                    "unit_id": "__empty__",
                    "features": [0.0] * self.config.input_dim,
                    "relevance_target": 0.0,
                    "answer_target": 0.0,
                    "temporal_target": 0.0,
                    "aggregation_target": 0.0,
                    "profile_target": 0.0,
                    "current_value_target": 0.0,
                    "evidence_role_target": EVIDENCE_ROLES.index("noise"),
                    "answer_unit_role_target": ANSWER_UNIT_ROLES.index("background"),
                }
            ]
        return {
            "id": example["id"],
            "features": torch.tensor([item["features"] for item in units], dtype=torch.float32),
            "valid_mask": torch.tensor([item["unit_id"] != "__empty__" for item in units], dtype=torch.bool),
            "unit_ids": [item["unit_id"] for item in units],
            "relevance_target": torch.tensor([item["relevance_target"] for item in units], dtype=torch.float32),
            "answer_target": torch.tensor([item["answer_target"] for item in units], dtype=torch.float32),
            "temporal_target": torch.tensor([item["temporal_target"] for item in units], dtype=torch.float32),
            "aggregation_target": torch.tensor([item["aggregation_target"] for item in units], dtype=torch.float32),
            "profile_target": torch.tensor([item["profile_target"] for item in units], dtype=torch.float32),
            "current_value_target": torch.tensor([item["current_value_target"] for item in units], dtype=torch.float32),
            "evidence_role_target": torch.tensor([item["evidence_role_target"] for item in units], dtype=torch.long),
            "answer_unit_role_target": torch.tensor([item["answer_unit_role_target"] for item in units], dtype=torch.long),
            "requires_temporal_target": torch.tensor(example["requires_temporal_target"], dtype=torch.float32),
            "requires_aggregation_target": torch.tensor(example["requires_aggregation_target"], dtype=torch.float32),
            "requires_profile_target": torch.tensor(example["requires_profile_target"], dtype=torch.float32),
            "requires_current_value_target": torch.tensor(example["requires_current_value_target"], dtype=torch.float32),
            "requires_multi_hop_target": torch.tensor(example["requires_multi_hop_target"], dtype=torch.float32),
            "operation_family_target": torch.tensor(example["operation_family_target"], dtype=torch.long),
            "coverage_complete_target": torch.tensor(example["coverage_complete_target"], dtype=torch.float32),
            "conflict_detected_target": torch.tensor(example["conflict_detected_target"], dtype=torch.float32),
        }


def collate_unified_planner_batch(batch: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    _require_torch()
    max_units = max(int(item["features"].shape[0]) for item in batch)
    input_dim = int(batch[0]["features"].shape[-1])
    batch_size = len(batch)
    features = torch.zeros((batch_size, max_units, input_dim), dtype=torch.float32)
    valid_mask = torch.zeros((batch_size, max_units), dtype=torch.bool)
    result: Dict[str, Any] = {
        "ids": [str(item["id"]) for item in batch],
        "unit_ids": [list(item["unit_ids"]) for item in batch],
        "features": features,
        "valid_mask": valid_mask,
    }
    unit_float_targets = (
        "relevance_target",
        "answer_target",
        "temporal_target",
        "aggregation_target",
        "profile_target",
        "current_value_target",
    )
    unit_long_targets = ("evidence_role_target", "answer_unit_role_target")
    for key in unit_float_targets:
        result[key] = torch.zeros((batch_size, max_units), dtype=torch.float32)
    for key in unit_long_targets:
        default = EVIDENCE_ROLES.index("noise") if key == "evidence_role_target" else ANSWER_UNIT_ROLES.index("background")
        result[key] = torch.full((batch_size, max_units), default, dtype=torch.long)
    for row_index, item in enumerate(batch):
        count = int(item["features"].shape[0])
        features[row_index, :count] = item["features"]
        valid_mask[row_index, :count] = item["valid_mask"]
        for key in unit_float_targets + unit_long_targets:
            result[key][row_index, :count] = item[key]
    for key in (
        "requires_temporal_target",
        "requires_aggregation_target",
        "requires_profile_target",
        "requires_current_value_target",
        "requires_multi_hop_target",
        "coverage_complete_target",
        "conflict_detected_target",
    ):
        result[key] = torch.stack([item[key] for item in batch])
    result["operation_family_target"] = torch.stack([item["operation_family_target"] for item in batch])
    return result


class UnifiedOperationPlannerModel(nn.Module if nn is not None else object):  # type: ignore[misc]
    def __init__(self, config: UnifiedPlannerConfig | Mapping[str, Any] | None = None):
        _require_torch()
        super().__init__()
        self.config = config if isinstance(config, UnifiedPlannerConfig) else UnifiedPlannerConfig.from_dict(config)
        hidden = int(self.config.hidden_dim)
        self.unit_encoder = nn.Sequential(
            nn.Linear(self.config.input_dim, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
            nn.Dropout(float(self.config.dropout)),
            nn.Linear(hidden, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
        )
        self.relevance_head = nn.Linear(hidden, 1)
        self.answer_head = nn.Linear(hidden, 1)
        self.temporal_head = nn.Linear(hidden, 1)
        self.aggregation_head = nn.Linear(hidden, 1)
        self.profile_head = nn.Linear(hidden, 1)
        self.current_value_head = nn.Linear(hidden, 1)
        self.evidence_role_head = nn.Linear(hidden, len(EVIDENCE_ROLES))
        self.answer_unit_role_head = nn.Linear(hidden, len(ANSWER_UNIT_ROLES))
        self.row_encoder = nn.Sequential(
            nn.Linear(hidden * 2, hidden),
            nn.SiLU(),
            nn.Dropout(float(self.config.dropout)),
        )
        self.operation_required_head = nn.Linear(hidden, 5)
        self.operation_family_head = nn.Linear(hidden, len(OPERATION_FAMILIES))
        self.contract_head = nn.Linear(hidden, 2)

    def forward(self, features: Any, valid_mask: Any | None = None) -> Dict[str, Any]:
        encoded = self.unit_encoder(features)
        if valid_mask is None:
            valid_mask = torch.ones(encoded.shape[:2], dtype=torch.bool, device=encoded.device)
        mask_float = valid_mask.to(dtype=encoded.dtype).unsqueeze(-1)
        masked = encoded * mask_float
        count = mask_float.sum(dim=1).clamp_min(1.0)
        mean_pool = masked.sum(dim=1) / count
        max_source = encoded.masked_fill(~valid_mask.unsqueeze(-1), -1e4)
        max_pool = torch.max(max_source, dim=1).values
        max_pool = torch.where(torch.isfinite(max_pool), max_pool, torch.zeros_like(max_pool))
        row_hidden = self.row_encoder(torch.cat([mean_pool, max_pool], dim=-1))
        return {
            "unit_hidden": encoded,
            "relevance_logits": self.relevance_head(encoded).squeeze(-1),
            "answer_logits": self.answer_head(encoded).squeeze(-1),
            "temporal_logits": self.temporal_head(encoded).squeeze(-1),
            "aggregation_logits": self.aggregation_head(encoded).squeeze(-1),
            "profile_logits": self.profile_head(encoded).squeeze(-1),
            "current_value_logits": self.current_value_head(encoded).squeeze(-1),
            "evidence_role_logits": self.evidence_role_head(encoded),
            "answer_unit_role_logits": self.answer_unit_role_head(encoded),
            "operation_required_logits": self.operation_required_head(row_hidden),
            "operation_family_logits": self.operation_family_head(row_hidden),
            "contract_logits": self.contract_head(row_hidden),
        }


def loss_for_batch(outputs: Mapping[str, Any], batch: Mapping[str, Any], *, weights: Mapping[str, float] | None = None) -> Dict[str, Any]:
    _require_torch()
    resolved = {
        "unit_binary": 1.0,
        "unit_role": 0.6,
        "operation_required": 0.8,
        "operation_family": 0.5,
        "contract": 0.5,
        **dict(weights or {}),
    }
    valid_mask = batch["valid_mask"]
    if bool(valid_mask.any().item()):
        unit_losses = []
        for output_key, target_key in (
            ("relevance_logits", "relevance_target"),
            ("answer_logits", "answer_target"),
            ("temporal_logits", "temporal_target"),
            ("aggregation_logits", "aggregation_target"),
            ("profile_logits", "profile_target"),
            ("current_value_logits", "current_value_target"),
        ):
            unit_losses.append(F.binary_cross_entropy_with_logits(outputs[output_key][valid_mask], batch[target_key][valid_mask]))
        unit_binary_loss = sum(unit_losses) / float(len(unit_losses))
        evidence_role_loss = F.cross_entropy(outputs["evidence_role_logits"][valid_mask], batch["evidence_role_target"][valid_mask])
        answer_unit_role_loss = F.cross_entropy(outputs["answer_unit_role_logits"][valid_mask], batch["answer_unit_role_target"][valid_mask])
        unit_role_loss = (evidence_role_loss + answer_unit_role_loss) / 2.0
    else:
        zero = outputs["operation_family_logits"].sum() * 0.0
        unit_binary_loss = zero
        unit_role_loss = zero
    operation_required_target = torch.stack(
        [
            batch["requires_temporal_target"],
            batch["requires_aggregation_target"],
            batch["requires_profile_target"],
            batch["requires_current_value_target"],
            batch["requires_multi_hop_target"],
        ],
        dim=-1,
    )
    operation_required_loss = F.binary_cross_entropy_with_logits(
        outputs["operation_required_logits"],
        operation_required_target,
    )
    operation_family_loss = F.cross_entropy(outputs["operation_family_logits"], batch["operation_family_target"])
    contract_target = torch.stack([batch["coverage_complete_target"], batch["conflict_detected_target"]], dim=-1)
    contract_loss = F.binary_cross_entropy_with_logits(outputs["contract_logits"], contract_target)
    total = (
        resolved["unit_binary"] * unit_binary_loss
        + resolved["unit_role"] * unit_role_loss
        + resolved["operation_required"] * operation_required_loss
        + resolved["operation_family"] * operation_family_loss
        + resolved["contract"] * contract_loss
    )
    return {
        "loss": total,
        "unit_binary_loss": unit_binary_loss.detach(),
        "unit_role_loss": unit_role_loss.detach(),
        "operation_required_loss": operation_required_loss.detach(),
        "operation_family_loss": operation_family_loss.detach(),
        "contract_loss": contract_loss.detach(),
    }


@torch.no_grad() if torch is not None else (lambda fn: fn)
def predict_row(
    model: UnifiedOperationPlannerModel,
    row: Mapping[str, Any],
    *,
    config: UnifiedPlannerConfig | None = None,
    device: Any | None = None,
) -> Dict[str, Any]:
    _require_torch()
    model.eval()
    dataset = UnifiedOperationPlannerDataset([row], config or model.config)
    batch = collate_unified_planner_batch([dataset[0]])
    resolved_device = device or next(model.parameters()).device
    moved = {key: value.to(resolved_device) if isinstance(value, torch.Tensor) else value for key, value in batch.items()}
    outputs = model(moved["features"], moved["valid_mask"])
    unit_ids = batch["unit_ids"][0]
    valid_count = int(batch["valid_mask"][0].sum().item())
    unit_scores = []
    for index, unit_id in enumerate(unit_ids[:valid_count]):
        unit_scores.append(
            {
                "unit_id": unit_id,
                "relevance_score": float(torch.sigmoid(outputs["relevance_logits"][0, index]).detach().cpu().item()),
                "answer_score": float(torch.sigmoid(outputs["answer_logits"][0, index]).detach().cpu().item()),
                "temporal_score": float(torch.sigmoid(outputs["temporal_logits"][0, index]).detach().cpu().item()),
                "aggregation_score": float(torch.sigmoid(outputs["aggregation_logits"][0, index]).detach().cpu().item()),
                "profile_score": float(torch.sigmoid(outputs["profile_logits"][0, index]).detach().cpu().item()),
                "current_value_score": float(torch.sigmoid(outputs["current_value_logits"][0, index]).detach().cpu().item()),
                "evidence_role": EVIDENCE_ROLES[int(torch.argmax(outputs["evidence_role_logits"][0, index]).detach().cpu().item())],
                "answer_unit_role": ANSWER_UNIT_ROLES[int(torch.argmax(outputs["answer_unit_role_logits"][0, index]).detach().cpu().item())],
            }
        )
    operation_required = torch.sigmoid(outputs["operation_required_logits"][0]).detach().cpu().tolist()
    contract = torch.sigmoid(outputs["contract_logits"][0]).detach().cpu().tolist()
    return {
        "architecture": UNIFIED_PLANNER_ARCHITECTURE,
        "query_operation": {
            "requires_temporal": bool(operation_required[0] >= 0.5),
            "requires_aggregation": bool(operation_required[1] >= 0.5),
            "requires_profile": bool(operation_required[2] >= 0.5),
            "requires_current_value": bool(operation_required[3] >= 0.5),
            "requires_multi_hop": bool(operation_required[4] >= 0.5),
            "operation_family": OPERATION_FAMILIES[int(torch.argmax(outputs["operation_family_logits"][0]).detach().cpu().item())],
        },
        "unit_scores": unit_scores,
        "answer_contract": {
            "selected_unit_ids": [item["unit_id"] for item in sorted(unit_scores, key=lambda value: value["answer_score"], reverse=True)[:8]],
            "coverage_complete": bool(contract[0] >= 0.5),
            "conflict_detected": bool(contract[1] >= 0.5),
            "confidence": max(item["answer_score"] for item in unit_scores) if unit_scores else 0.0,
        },
    }


def load_checkpoint(path: str | Any, *, map_location: str | Any = "cpu") -> Dict[str, Any]:
    _require_torch()
    payload = torch.load(path, map_location=map_location)
    config = UnifiedPlannerConfig.from_dict(payload.get("config"))
    model = UnifiedOperationPlannerModel(config)
    model.load_state_dict(payload["model_state"])
    model.eval()
    return {"model": model, "config": config, "payload": payload}
