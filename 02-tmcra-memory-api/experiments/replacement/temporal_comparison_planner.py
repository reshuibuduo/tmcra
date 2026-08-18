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


DIRECTIONS = (
    "first",
    "last",
    "before",
    "after",
    "immediate_before",
    "immediate_after",
    "duration",
    "bucket_count",
    "unknown",
)
COMPARISON_ROLES = (
    "answer_event",
    "comparison_anchor",
    "timeline_context",
    "distractor",
    "hypothetical_mention",
    "insufficient_time",
)
ARCHITECTURE = "hashed_temporal_comparison_planner_v11_optional_cross_encoder"
_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
_DATE_RE = re.compile(r"\b(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]20\d{2}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|monday|tuesday|wednesday|thursday|friday|saturday|sunday|yesterday|today|tomorrow|last|next|before|after|later|earlier|then|finally|already)\b", re.I)
_QUERY_ANCHOR_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "before",
    "after",
    "between",
    "did",
    "do",
    "does",
    "during",
    "earlier",
    "earliest",
    "first",
    "for",
    "from",
    "had",
    "has",
    "have",
    "how",
    "i",
    "in",
    "is",
    "last",
    "later",
    "latest",
    "me",
    "my",
    "of",
    "or",
    "the",
    "then",
    "to",
    "was",
    "were",
    "what",
    "when",
    "which",
    "who",
}


def _require_torch() -> None:
    if torch is None or nn is None or F is None:
        raise RuntimeError("torch is required")


def clean_text(value: Any) -> str:
    return " ".join(("" if value is None else str(value)).strip().split())


def normalize_label(value: Any, *, choices: Sequence[str], default: str) -> str:
    text = clean_text(value).lower().replace("-", "_").replace(" ", "_")
    return text if text in set(choices) else default


def tokens(text: str) -> List[str]:
    return [item.lower() for item in _TOKEN_RE.findall(clean_text(text))]


def stable_hash(value: str, *, modulo: int) -> int:
    digest = hashlib.blake2b(clean_text(value).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=False) % modulo


def vectorize_text(text: str, *, dim: int) -> List[float]:
    values = [0.0] * dim
    for token in tokens(text):
        index = stable_hash(token, modulo=dim)
        sign = -1.0 if stable_hash("sign:" + token, modulo=2) == 0 else 1.0
        values[index] += sign
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values] if norm > 0 else values


def query_overlap_score(query: str, candidate: str) -> float:
    query_tokens = {item for item in tokens(query) if len(item) > 1 and item not in _QUERY_ANCHOR_STOPWORDS}
    candidate_tokens = {item for item in tokens(candidate) if len(item) > 1}
    if not query_tokens or not candidate_tokens:
        return 0.0
    return len(query_tokens & candidate_tokens) / max(1, len(query_tokens))


def _float(value: Any, default: float = 0.0, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if math.isnan(parsed) or math.isinf(parsed):
        parsed = default
    return max(lo, min(hi, parsed))


@dataclass(frozen=True)
class TemporalComparisonConfig:
    query_hash_dim: int = 256
    candidate_hash_dim: int = 256
    scalar_dim: int = 14
    token_hash_dim: int = 8192
    token_embed_dim: int = 96
    max_query_tokens: int = 32
    max_candidate_tokens: int = 128
    use_cross_encoder: bool = False
    cross_layers: int = 2
    cross_heads: int = 4
    hidden_dim: int = 192
    dropout: float = 0.10

    @property
    def input_dim(self) -> int:
        cross_dim = min(int(self.query_hash_dim), int(self.candidate_hash_dim))
        return self.query_hash_dim + self.candidate_hash_dim + cross_dim * 2 + self.scalar_dim

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "TemporalComparisonConfig":
        values = dict(payload or {})
        allowed = {field.name for field in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{key: value for key, value in values.items() if key in allowed})


def candidate_text(candidate: Mapping[str, Any]) -> str:
    return " ".join(
        clean_text(candidate.get(key))
        for key in ("text", "event", "target", "session_date", "time_anchor")
        if clean_text(candidate.get(key))
    )


def candidate_scalar_features(candidate: Mapping[str, Any]) -> List[float]:
    text = candidate_text(candidate)
    return [
        _float(candidate.get("query_overlap")),
        _float(candidate.get("retrieval_score", candidate.get("score", 0.0))),
        _float(candidate.get("graph_score")),
        _float(candidate.get("tunnel_score")),
        _float(candidate.get("confidence", 0.5), default=0.5),
        _float(candidate.get("rank_score")),
        _float(candidate.get("age_turns"), lo=0.0, hi=500.0) / 500.0,
        _float(candidate.get("turn_index"), lo=0.0, hi=500.0) / 500.0,
        1.0 if bool(candidate.get("is_current")) else 0.0,
        1.0 if bool(candidate.get("is_hypothetical")) else 0.0,
        1.0 if bool(candidate.get("is_user_event", True)) else 0.0,
        1.0 if _DATE_RE.search(text) else 0.0,
        1.0 if re.search(r"\b\d+\b", text) else 0.0,
        1.0 if bool(candidate.get("has_explicit_order_anchor")) else 0.0,
    ]


def token_indices(text: str, *, buckets: int, limit: int) -> List[int]:
    if buckets <= 1 or limit <= 0:
        return []
    values: List[int] = []
    for token in tokens(text):
        values.append(1 + stable_hash(token, modulo=buckets - 1))
        if len(values) >= limit:
            break
    return values


def pad_indices(values: Sequence[int], *, length: int) -> List[int]:
    output = [int(item) for item in values[:length]]
    if len(output) < length:
        output.extend([0] * (length - len(output)))
    return output


def normalize_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    candidates = [dict(item) for item in list(row.get("candidates") or []) if isinstance(item, Mapping)]
    for index, candidate in enumerate(candidates, start=1):
        candidate.setdefault("id", f"c{index}")
    gold = dict(row.get("gold") or {})
    direction = normalize_label(gold.get("query_direction", row.get("query_direction")), choices=DIRECTIONS, default="unknown")
    role_by_id = {
        clean_text(key): normalize_label(value, choices=COMPARISON_ROLES, default="distractor")
        for key, value in dict(gold.get("comparison_role_by_candidate_id") or {}).items()
    }
    selected = [clean_text(item) for item in list(gold.get("selected_candidate_ids") or []) if clean_text(item)]
    answer_id = clean_text(gold.get("answer_candidate_id", ""))
    return {
        **dict(row),
        "id": clean_text(row.get("id")) or f"row_{stable_hash(str(row), modulo=10**12)}",
        "query": clean_text(row.get("query")),
        "query_date": clean_text(row.get("query_date", "")),
        "candidates": candidates,
        "gold": {
            **gold,
            "query_direction": direction,
            "selected_candidate_ids": selected,
            "answer_candidate_id": answer_id,
            "comparison_role_by_candidate_id": role_by_id,
            "should_plan": bool(gold.get("should_plan", True)),
        },
    }


def flatten_rows(rows: Iterable[Mapping[str, Any]], config: TemporalComparisonConfig | None = None) -> List[Dict[str, Any]]:
    resolved = config or TemporalComparisonConfig()
    output: List[Dict[str, Any]] = []
    for raw in rows:
        row = normalize_row(raw)
        query = row["query"]
        query_vec = vectorize_text(query, dim=resolved.query_hash_dim)
        query_token_ids = pad_indices(
            token_indices(query, buckets=resolved.token_hash_dim, limit=resolved.max_query_tokens),
            length=resolved.max_query_tokens,
        )
        gold = row["gold"]
        selected = set(gold["selected_candidate_ids"])
        answer_id = clean_text(gold.get("answer_candidate_id"))
        role_by_id = dict(gold.get("comparison_role_by_candidate_id") or {})
        items = []
        for candidate in row["candidates"]:
            cid = clean_text(candidate.get("id"))
            role = role_by_id.get(cid, "answer_event" if cid == answer_id else "distractor")
            text = candidate_text(candidate)
            candidate_vec = vectorize_text(text, dim=resolved.candidate_hash_dim)
            cross_dim = min(len(query_vec), len(candidate_vec))
            product_vec = [query_vec[index] * candidate_vec[index] for index in range(cross_dim)]
            distance_vec = [abs(query_vec[index] - candidate_vec[index]) for index in range(cross_dim)]
            feature_candidate = dict(candidate)
            feature_candidate["query_overlap"] = _float(
                candidate.get("query_overlap", query_overlap_score(query, text)),
                default=query_overlap_score(query, text),
            )
            items.append(
                {
                    "id": cid,
                    "features": query_vec + candidate_vec + product_vec + distance_vec + candidate_scalar_features(feature_candidate),
                    "candidate_indices": pad_indices(
                        token_indices(text, buckets=resolved.token_hash_dim, limit=resolved.max_candidate_tokens),
                        length=resolved.max_candidate_tokens,
                    ),
                    "selected_target": 1.0 if cid in selected else 0.0,
                    "answer_target": 1.0 if cid == answer_id else 0.0,
                    "role_target": COMPARISON_ROLES.index(normalize_label(role, choices=COMPARISON_ROLES, default="distractor")),
                    "raw": candidate,
                }
            )
        output.append(
            {
                "id": row["id"],
                "query": query,
                "query_indices": query_token_ids,
                "candidates": items,
                "direction_target": DIRECTIONS.index(gold["query_direction"]),
                "should_plan_target": 1.0 if bool(gold.get("should_plan", True)) else 0.0,
                "raw": row,
            }
        )
    return output


class TemporalComparisonDataset(Dataset):  # type: ignore[misc]
    def __init__(self, rows: Sequence[Mapping[str, Any]], config: TemporalComparisonConfig | None = None):
        _require_torch()
        self.config = config or TemporalComparisonConfig()
        self.examples = flatten_rows(rows, self.config)

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        example = self.examples[index]
        candidates = example["candidates"] or [
            {
                "id": "__empty__",
                "features": [0.0] * self.config.input_dim,
                "candidate_indices": [0] * self.config.max_candidate_tokens,
                "selected_target": 0.0,
                "answer_target": 0.0,
                "role_target": COMPARISON_ROLES.index("distractor"),
            }
        ]
        return {
            "id": example["id"],
            "features": torch.tensor([item["features"] for item in candidates], dtype=torch.float32),
            "query_indices": torch.tensor(example["query_indices"], dtype=torch.long),
            "candidate_indices": torch.tensor([item["candidate_indices"] for item in candidates], dtype=torch.long),
            "valid_mask": torch.tensor([item["id"] != "__empty__" for item in candidates], dtype=torch.bool),
            "selected_target": torch.tensor([item["selected_target"] for item in candidates], dtype=torch.float32),
            "answer_target": torch.tensor([item["answer_target"] for item in candidates], dtype=torch.float32),
            "role_target": torch.tensor([item["role_target"] for item in candidates], dtype=torch.long),
            "direction_target": torch.tensor(int(example["direction_target"]), dtype=torch.long),
            "should_plan_target": torch.tensor(float(example["should_plan_target"]), dtype=torch.float32),
        }


def collate_temporal_comparison_batch(batch: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    _require_torch()
    max_candidates = max(int(item["features"].shape[0]) for item in batch)
    input_dim = int(batch[0]["features"].shape[-1])
    batch_size = len(batch)
    features = torch.zeros((batch_size, max_candidates, input_dim), dtype=torch.float32)
    max_query_tokens = int(batch[0]["query_indices"].shape[0])
    max_candidate_tokens = int(batch[0]["candidate_indices"].shape[-1])
    query_indices = torch.zeros((batch_size, max_query_tokens), dtype=torch.long)
    candidate_indices = torch.zeros((batch_size, max_candidates, max_candidate_tokens), dtype=torch.long)
    valid_mask = torch.zeros((batch_size, max_candidates), dtype=torch.bool)
    selected_target = torch.zeros((batch_size, max_candidates), dtype=torch.float32)
    answer_target = torch.zeros((batch_size, max_candidates), dtype=torch.float32)
    role_target = torch.full((batch_size, max_candidates), COMPARISON_ROLES.index("distractor"), dtype=torch.long)
    for row_index, item in enumerate(batch):
        count = int(item["features"].shape[0])
        features[row_index, :count] = item["features"]
        query_indices[row_index] = item["query_indices"]
        candidate_indices[row_index, :count] = item["candidate_indices"]
        valid_mask[row_index, :count] = item["valid_mask"]
        selected_target[row_index, :count] = item["selected_target"]
        answer_target[row_index, :count] = item["answer_target"]
        role_target[row_index, :count] = item["role_target"]
    return {
        "ids": [str(item["id"]) for item in batch],
        "features": features,
        "query_indices": query_indices,
        "candidate_indices": candidate_indices,
        "valid_mask": valid_mask,
        "selected_target": selected_target,
        "answer_target": answer_target,
        "role_target": role_target,
        "direction_target": torch.stack([item["direction_target"] for item in batch]),
        "should_plan_target": torch.stack([item["should_plan_target"] for item in batch]),
    }


class TemporalComparisonModel(nn.Module if nn is not None else object):  # type: ignore[misc]
    def __init__(self, config: TemporalComparisonConfig | Mapping[str, Any] | None = None):
        _require_torch()
        super().__init__()
        self.config = config if isinstance(config, TemporalComparisonConfig) else TemporalComparisonConfig.from_dict(config)
        hidden = int(self.config.hidden_dim)
        token_dim = int(self.config.token_embed_dim)
        self.use_cross_encoder = bool(self.config.use_cross_encoder)
        self.token_embedding = nn.Embedding(int(self.config.token_hash_dim), token_dim, padding_idx=0)
        self.query_text_encoder = nn.Sequential(
            nn.Linear(token_dim * 2, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
            nn.Dropout(float(self.config.dropout)),
        )
        self.candidate_text_encoder = nn.Sequential(
            nn.Linear(token_dim * 2, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
            nn.Dropout(float(self.config.dropout)),
        )
        self.text_pair_encoder = nn.Sequential(
            nn.Linear(hidden * 4 + 6, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
            nn.Dropout(float(self.config.dropout)),
            nn.Linear(hidden, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
        )
        if self.use_cross_encoder:
            max_cross_tokens = 1 + int(self.config.max_query_tokens) + int(self.config.max_candidate_tokens)
            heads = max(1, int(self.config.cross_heads))
            if token_dim % heads != 0:
                heads = 1
            self.cross_cls = nn.Parameter(torch.zeros(1, 1, token_dim))
            self.cross_position_embedding = nn.Embedding(max_cross_tokens, token_dim)
            self.cross_role_embedding = nn.Embedding(3, token_dim)
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=token_dim,
                nhead=heads,
                dim_feedforward=token_dim * 4,
                dropout=float(self.config.dropout),
                activation="gelu",
                batch_first=True,
                norm_first=True,
            )
            self.cross_transformer = nn.TransformerEncoder(encoder_layer, num_layers=max(1, int(self.config.cross_layers)))
            self.cross_text_projection = nn.Sequential(
                nn.LayerNorm(token_dim),
                nn.Linear(token_dim, hidden),
                nn.LayerNorm(hidden),
                nn.SiLU(),
                nn.Dropout(float(self.config.dropout)),
            )
            self.text_cross_fusion = nn.Sequential(
                nn.Linear(hidden * 4, hidden),
                nn.LayerNorm(hidden),
                nn.SiLU(),
                nn.Dropout(float(self.config.dropout)),
                nn.Linear(hidden, hidden),
                nn.LayerNorm(hidden),
                nn.SiLU(),
            )
        self.candidate_encoder = nn.Sequential(
            nn.Linear(self.config.input_dim, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
            nn.Dropout(float(self.config.dropout)),
            nn.Linear(hidden, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
        )
        self.feature_text_merger = nn.Sequential(
            nn.Linear(hidden * 4, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
            nn.Dropout(float(self.config.dropout)),
            nn.Linear(hidden, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
        )
        self.row_encoder = nn.Sequential(nn.Linear(hidden * 2, hidden), nn.SiLU(), nn.Dropout(float(self.config.dropout)))
        self.contextual_candidate_encoder = nn.Sequential(
            nn.Linear(hidden * 4, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
            nn.Dropout(float(self.config.dropout)),
            nn.Linear(hidden, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
        )
        self.selection_head = nn.Linear(hidden, 1)
        self.answer_head = nn.Linear(hidden, 1)
        self.role_head = nn.Linear(hidden, len(COMPARISON_ROLES))
        self.direction_head = nn.Linear(hidden, len(DIRECTIONS))
        self.should_plan_head = nn.Linear(hidden, 1)

    def _masked_mean_max(self, embeddings: Any, mask: Any) -> Any:
        mask_float = mask.to(dtype=embeddings.dtype).unsqueeze(-1)
        mean_pool = (embeddings * mask_float).sum(dim=-2) / mask_float.sum(dim=-2).clamp_min(1.0)
        max_source = embeddings.masked_fill(~mask.unsqueeze(-1), -1e4)
        max_pool = torch.max(max_source, dim=-2).values
        max_pool = torch.where(torch.isfinite(max_pool), max_pool, torch.zeros_like(max_pool))
        return torch.cat([mean_pool, max_pool], dim=-1)

    def _encode_cross_pair(self, query_indices: Any, candidate_indices: Any, query_mask: Any, candidate_mask: Any, features: Any) -> Any:
        batch_size, candidate_count = int(features.shape[0]), int(features.shape[1])
        query_embeddings = self.token_embedding(query_indices).unsqueeze(1).expand(-1, candidate_count, -1, -1)
        candidate_embeddings = self.token_embedding(candidate_indices)
        pair_count = batch_size * candidate_count
        query_flat = query_embeddings.reshape(pair_count, int(self.config.max_query_tokens), int(self.config.token_embed_dim))
        candidate_flat = candidate_embeddings.reshape(pair_count, int(self.config.max_candidate_tokens), int(self.config.token_embed_dim))
        cls = self.cross_cls.to(features.device, dtype=features.dtype).expand(pair_count, -1, -1)
        sequence = torch.cat([cls, query_flat, candidate_flat], dim=1)

        query_mask_flat = query_mask.unsqueeze(1).expand(-1, candidate_count, -1).reshape(pair_count, int(self.config.max_query_tokens))
        candidate_mask_flat = candidate_mask.reshape(pair_count, int(self.config.max_candidate_tokens))
        cls_mask = torch.ones((pair_count, 1), dtype=torch.bool, device=features.device)
        sequence_mask = torch.cat([cls_mask, query_mask_flat, candidate_mask_flat], dim=1)

        seq_len = int(sequence.shape[1])
        position_ids = torch.arange(seq_len, dtype=torch.long, device=features.device).unsqueeze(0).expand(pair_count, -1)
        role_ids = torch.cat(
            [
                torch.zeros((pair_count, 1), dtype=torch.long, device=features.device),
                torch.ones((pair_count, int(self.config.max_query_tokens)), dtype=torch.long, device=features.device),
                torch.full((pair_count, int(self.config.max_candidate_tokens)), 2, dtype=torch.long, device=features.device),
            ],
            dim=1,
        )
        encoded = self.cross_transformer(
            sequence + self.cross_position_embedding(position_ids) + self.cross_role_embedding(role_ids),
            src_key_padding_mask=~sequence_mask,
        )
        return self.cross_text_projection(encoded[:, 0, :]).reshape(batch_size, candidate_count, int(self.config.hidden_dim))

    def _encode_text_pair(self, query_indices: Any, candidate_indices: Any, features: Any) -> Any:
        batch_size, candidate_count = int(features.shape[0]), int(features.shape[1])
        if query_indices is None or candidate_indices is None:
            return torch.zeros((batch_size, candidate_count, int(self.config.hidden_dim)), dtype=features.dtype, device=features.device)
        query_indices = query_indices.to(features.device)
        candidate_indices = candidate_indices.to(features.device)
        query_mask = query_indices != 0
        candidate_mask = candidate_indices != 0
        query_embeddings = self.token_embedding(query_indices)
        candidate_embeddings = self.token_embedding(candidate_indices)
        query_pool = self._masked_mean_max(query_embeddings, query_mask)
        candidate_pool = self._masked_mean_max(candidate_embeddings, candidate_mask)
        query_hidden = self.query_text_encoder(query_pool)
        candidate_hidden = self.candidate_text_encoder(candidate_pool)
        query_expanded = query_hidden.unsqueeze(1).expand_as(candidate_hidden)

        query_norm = F.normalize(query_embeddings, dim=-1).unsqueeze(1)
        candidate_norm = F.normalize(candidate_embeddings, dim=-1)
        similarity = torch.einsum("bqf,bncf->bnqc", query_norm.squeeze(1), candidate_norm)
        pair_mask = query_mask.unsqueeze(1).unsqueeze(-1) & candidate_mask.unsqueeze(2)
        masked_similarity = similarity.masked_fill(~pair_mask, -1e4)
        q_to_c = torch.max(masked_similarity, dim=-1).values
        c_to_q = torch.max(masked_similarity, dim=-2).values
        q_to_c = torch.where(torch.isfinite(q_to_c), q_to_c, torch.zeros_like(q_to_c))
        c_to_q = torch.where(torch.isfinite(c_to_q), c_to_q, torch.zeros_like(c_to_q))
        q_mask_float = query_mask.to(dtype=features.dtype).unsqueeze(1)
        c_mask_float = candidate_mask.to(dtype=features.dtype)
        q_mean = (q_to_c * q_mask_float).sum(dim=-1) / q_mask_float.sum(dim=-1).clamp_min(1.0)
        c_mean = (c_to_q * c_mask_float).sum(dim=-1) / c_mask_float.sum(dim=-1).clamp_min(1.0)
        q_max = torch.max(q_to_c.masked_fill(~query_mask.unsqueeze(1), -1e4), dim=-1).values
        c_max = torch.max(c_to_q.masked_fill(~candidate_mask, -1e4), dim=-1).values
        q_max = torch.where(torch.isfinite(q_max), q_max, torch.zeros_like(q_max))
        c_max = torch.where(torch.isfinite(c_max), c_max, torch.zeros_like(c_max))
        exact = (query_indices.unsqueeze(1).unsqueeze(-1) == candidate_indices.unsqueeze(2)) & pair_mask
        query_exact = exact.any(dim=-1).to(dtype=features.dtype)
        candidate_exact = exact.any(dim=-2).to(dtype=features.dtype)
        query_exact_ratio = (query_exact * q_mask_float).sum(dim=-1) / q_mask_float.sum(dim=-1).clamp_min(1.0)
        candidate_exact_ratio = (candidate_exact * c_mask_float).sum(dim=-1) / c_mask_float.sum(dim=-1).clamp_min(1.0)
        interaction_stats = torch.stack([q_mean, q_max, c_mean, c_max, query_exact_ratio, candidate_exact_ratio], dim=-1)

        interaction_hidden = self.text_pair_encoder(
            torch.cat(
                [
                    query_expanded,
                    candidate_hidden,
                    query_expanded * candidate_hidden,
                    torch.abs(query_expanded - candidate_hidden),
                    interaction_stats,
                ],
                dim=-1,
            )
        )
        if not self.use_cross_encoder:
            return interaction_hidden
        cross_hidden = self._encode_cross_pair(query_indices, candidate_indices, query_mask, candidate_mask, features)
        return self.text_cross_fusion(
            torch.cat(
                [
                    interaction_hidden,
                    cross_hidden,
                    interaction_hidden * cross_hidden,
                    torch.abs(interaction_hidden - cross_hidden),
                ],
                dim=-1,
            )
        )

    def forward(
        self,
        features: Any,
        valid_mask: Any | None = None,
        query_indices: Any | None = None,
        candidate_indices: Any | None = None,
    ) -> Dict[str, Any]:
        feature_encoded = self.candidate_encoder(features)
        text_encoded = self._encode_text_pair(query_indices, candidate_indices, features)
        encoded = self.feature_text_merger(
            torch.cat(
                [
                    feature_encoded,
                    text_encoded,
                    feature_encoded * text_encoded,
                    torch.abs(feature_encoded - text_encoded),
                ],
                dim=-1,
            )
        )
        if valid_mask is None:
            valid_mask = torch.ones(encoded.shape[:2], dtype=torch.bool, device=encoded.device)
        mask_float = valid_mask.to(dtype=encoded.dtype).unsqueeze(-1)
        mean_pool = (encoded * mask_float).sum(dim=1) / mask_float.sum(dim=1).clamp_min(1.0)
        max_source = encoded.masked_fill(~valid_mask.unsqueeze(-1), -1e4)
        max_pool = torch.max(max_source, dim=1).values
        max_pool = torch.where(torch.isfinite(max_pool), max_pool, torch.zeros_like(max_pool))
        row_hidden = self.row_encoder(torch.cat([mean_pool, max_pool], dim=-1))
        row_expanded = row_hidden.unsqueeze(1).expand_as(encoded)
        contextual = self.contextual_candidate_encoder(
            torch.cat(
                [
                    encoded,
                    row_expanded,
                    encoded * row_expanded,
                    torch.abs(encoded - row_expanded),
                ],
                dim=-1,
            )
        )
        return {
            "selection_logits": self.selection_head(contextual).squeeze(-1),
            "answer_logits": self.answer_head(contextual).squeeze(-1),
            "role_logits": self.role_head(contextual),
            "direction_logits": self.direction_head(row_hidden),
            "should_plan_logits": self.should_plan_head(row_hidden).squeeze(-1),
        }


class TemporalComparisonLegacyModel(nn.Module if nn is not None else object):  # type: ignore[misc]
    """Load pre-v8 checkpoints without changing their behavior."""

    def __init__(self, config: TemporalComparisonConfig | Mapping[str, Any] | None = None):
        _require_torch()
        super().__init__()
        self.config = config if isinstance(config, TemporalComparisonConfig) else TemporalComparisonConfig.from_dict(config)
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
        self.row_encoder = nn.Sequential(nn.Linear(hidden * 2, hidden), nn.SiLU(), nn.Dropout(float(self.config.dropout)))
        self.contextual_candidate_encoder = nn.Sequential(
            nn.Linear(hidden * 4, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
            nn.Dropout(float(self.config.dropout)),
            nn.Linear(hidden, hidden),
            nn.LayerNorm(hidden),
            nn.SiLU(),
        )
        self.selection_head = nn.Linear(hidden, 1)
        self.answer_head = nn.Linear(hidden, 1)
        self.role_head = nn.Linear(hidden, len(COMPARISON_ROLES))
        self.direction_head = nn.Linear(hidden, len(DIRECTIONS))
        self.should_plan_head = nn.Linear(hidden, 1)

    def forward(
        self,
        features: Any,
        valid_mask: Any | None = None,
        query_indices: Any | None = None,
        candidate_indices: Any | None = None,
    ) -> Dict[str, Any]:
        del query_indices, candidate_indices
        encoded = self.candidate_encoder(features)
        if valid_mask is None:
            valid_mask = torch.ones(encoded.shape[:2], dtype=torch.bool, device=encoded.device)
        mask_float = valid_mask.to(dtype=encoded.dtype).unsqueeze(-1)
        mean_pool = (encoded * mask_float).sum(dim=1) / mask_float.sum(dim=1).clamp_min(1.0)
        max_source = encoded.masked_fill(~valid_mask.unsqueeze(-1), -1e4)
        max_pool = torch.max(max_source, dim=1).values
        max_pool = torch.where(torch.isfinite(max_pool), max_pool, torch.zeros_like(max_pool))
        row_hidden = self.row_encoder(torch.cat([mean_pool, max_pool], dim=-1))
        row_expanded = row_hidden.unsqueeze(1).expand_as(encoded)
        contextual = self.contextual_candidate_encoder(
            torch.cat(
                [
                    encoded,
                    row_expanded,
                    encoded * row_expanded,
                    torch.abs(encoded - row_expanded),
                ],
                dim=-1,
            )
        )
        return {
            "selection_logits": self.selection_head(contextual).squeeze(-1),
            "answer_logits": self.answer_head(contextual).squeeze(-1),
            "role_logits": self.role_head(contextual),
            "direction_logits": self.direction_head(row_hidden),
            "should_plan_logits": self.should_plan_head(row_hidden).squeeze(-1),
        }


def loss_for_batch(outputs: Mapping[str, Any], batch: Mapping[str, Any], *, weights: Mapping[str, float] | None = None) -> Dict[str, Any]:
    _require_torch()
    resolved = {
        "selection": 1.0,
        "answer": 1.0,
        "answer_rank": 0.8,
        "role": 0.7,
        "direction": 0.8,
        "should_plan": 0.4,
        **dict(weights or {}),
    }
    valid = batch["valid_mask"]
    selection_loss = F.binary_cross_entropy_with_logits(outputs["selection_logits"][valid], batch["selected_target"][valid])
    answer_loss = F.binary_cross_entropy_with_logits(outputs["answer_logits"][valid], batch["answer_target"][valid])
    has_answer = ((batch["answer_target"] > 0.5) & valid).any(dim=1)
    if bool(has_answer.any().item()):
        answer_logits = outputs["answer_logits"].masked_fill(~valid, -1e4)
        answer_indices = batch["answer_target"].masked_fill(~valid, 0.0).argmax(dim=1)
        answer_rank_loss = F.cross_entropy(answer_logits[has_answer], answer_indices[has_answer])
    else:
        answer_rank_loss = outputs["answer_logits"].sum() * 0.0
    role_loss = F.cross_entropy(outputs["role_logits"][valid], batch["role_target"][valid])
    direction_loss = F.cross_entropy(outputs["direction_logits"], batch["direction_target"])
    should_plan_loss = F.binary_cross_entropy_with_logits(outputs["should_plan_logits"], batch["should_plan_target"])
    total = (
        resolved["selection"] * selection_loss
        + resolved["answer"] * answer_loss
        + resolved["answer_rank"] * answer_rank_loss
        + resolved["role"] * role_loss
        + resolved["direction"] * direction_loss
        + resolved["should_plan"] * should_plan_loss
    )
    return {
        "loss": total,
        "selection_loss": selection_loss.detach(),
        "answer_loss": answer_loss.detach(),
        "answer_rank_loss": answer_rank_loss.detach(),
        "role_loss": role_loss.detach(),
        "direction_loss": direction_loss.detach(),
        "should_plan_loss": should_plan_loss.detach(),
    }


def load_checkpoint(checkpoint_path: str, *, device: str | None = None) -> tuple[TemporalComparisonModel, TemporalComparisonConfig, Dict[str, Any]]:
    _require_torch()
    resolved_device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
    payload = torch.load(checkpoint_path, map_location=resolved_device)
    config = TemporalComparisonConfig.from_dict(payload.get("config", {}))
    state_dict = payload["state_dict"]
    model_class = TemporalComparisonModel if "token_embedding.weight" in state_dict else TemporalComparisonLegacyModel
    model = model_class(config).to(resolved_device)
    model.load_state_dict(payload["state_dict"])
    model.eval()
    return model, config, payload


@torch.no_grad() if torch is not None else (lambda fn: fn)
def predict_row(
    model: TemporalComparisonModel,
    config: TemporalComparisonConfig,
    row: Mapping[str, Any],
    *,
    device: str | None = None,
) -> Dict[str, Any]:
    _require_torch()
    resolved_device = torch.device(device or next(model.parameters()).device)
    dataset = TemporalComparisonDataset([row], config)
    batch = collate_temporal_comparison_batch([dataset[0]])
    tensor_batch = {key: value.to(resolved_device) if hasattr(value, "to") else value for key, value in batch.items()}
    outputs = model(
        tensor_batch["features"],
        tensor_batch["valid_mask"],
        tensor_batch.get("query_indices"),
        tensor_batch.get("candidate_indices"),
    )
    selection = torch.sigmoid(outputs["selection_logits"])[0].detach().cpu().tolist()
    answer = torch.sigmoid(outputs["answer_logits"])[0].detach().cpu().tolist()
    role_ids = torch.argmax(outputs["role_logits"], dim=-1)[0].detach().cpu().tolist()
    direction_id = int(torch.argmax(outputs["direction_logits"], dim=-1)[0].detach().cpu().item())
    should_plan = float(torch.sigmoid(outputs["should_plan_logits"])[0].detach().cpu().item())
    raw_candidates = [dict(item) for item in list(row.get("candidates") or []) if isinstance(item, Mapping)]
    candidates = []
    for index, candidate in enumerate(raw_candidates):
        candidates.append(
            {
                "id": clean_text(candidate.get("id")) or f"c{index + 1}",
                "selection_score": float(selection[index]) if index < len(selection) else 0.0,
                "answer_score": float(answer[index]) if index < len(answer) else 0.0,
                "role": COMPARISON_ROLES[int(role_ids[index])] if index < len(role_ids) else "distractor",
            }
        )
    candidates.sort(key=lambda item: (-(item["selection_score"] + item["answer_score"]), item["id"]))
    return {
        "architecture": ARCHITECTURE,
        "should_plan_score": should_plan,
        "query_direction": DIRECTIONS[direction_id],
        "candidates": candidates,
    }
