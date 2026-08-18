from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, Iterable, List, Sequence

from experiments.replacement.adapters.base import MemoryHit

from .intent import QueryIntent


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize(value: object) -> str:
    return _clean_text(value).lower()


def _tokenize(value: object) -> List[str]:
    text = _normalize(value)
    if not text:
        return []
    english = re.findall(r"[a-z0-9_.-]+", text)
    cjk = [char for char in text if "\u4e00" <= char <= "\u9fff"]
    return _dedupe([*english, *cjk])


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


def _overlap(left: Sequence[str], right: Sequence[str]) -> float:
    left_set, right_set = set(left), set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / max(1, len(left_set | right_set))


@dataclass(slots=True)
class TemporalTurnRecord:
    turn_index: int
    user_text: str
    assistant_text: str = ""
    category_hints: List[str] = field(default_factory=list)
    slot_hints: List[str] = field(default_factory=list)
    entity_hints: List[str] = field(default_factory=list)

    @property
    def combined_text(self) -> str:
        return _clean_text(f"{self.user_text} {self.assistant_text}")


@dataclass(slots=True)
class TemporalShard:
    scale: str
    bucket_id: int
    start_turn: int
    end_turn: int
    turns: List[TemporalTurnRecord] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    slots: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)

    def summary_text(self) -> str:
        parts = [
            f"{self.scale}_bucket={self.bucket_id}",
            f"turns={self.start_turn}-{self.end_turn}",
        ]
        if self.categories:
            parts.append("categories=" + ", ".join(self.categories))
        if self.slots:
            parts.append("slots=" + ", ".join(self.slots[:4]))
        if self.entities:
            parts.append("entities=" + ", ".join(self.entities[:4]))
        return " | ".join(parts)


class TemporalShardIndex:
    def __init__(self, *, turns_per_hour: int = 12, hours_per_day: int = 24) -> None:
        self.turns_per_hour = max(2, int(turns_per_hour))
        self.hours_per_day = max(2, int(hours_per_day))
        self.turns: List[TemporalTurnRecord] = []

    def reset(self) -> None:
        self.turns = []

    def ingest_turn(
        self,
        *,
        turn_index: int,
        user_text: str,
        assistant_text: str = "",
        extraction_result: Dict[str, Any] | None = None,
        answer_payload: Dict[str, Any] | None = None,
    ) -> None:
        category_hints: List[str] = []
        slot_hints: List[str] = []
        entity_hints: List[str] = []
        for source in (extraction_result or {}, answer_payload or {}):
            category_hints.extend(self._collect_categories(source))
            slot_hints.extend(self._collect_slots(source))
            entity_hints.extend(self._collect_entities(source))
        self.turns.append(
            TemporalTurnRecord(
                turn_index=int(turn_index),
                user_text=_clean_text(user_text),
                assistant_text=_clean_text(assistant_text),
                category_hints=_dedupe(category_hints),
                slot_hints=_dedupe(slot_hints),
                entity_hints=_dedupe(entity_hints),
            )
        )

    def retrieve(self, query: str, *, intent: QueryIntent, top_k: int = 6) -> List[MemoryHit]:
        if not self.turns:
            return []
        if not self._needs_temporal_shards(intent):
            return []
        query_tokens = _tokenize(query)
        shards = self._build_shards(intent=intent)
        scored: List[tuple[float, TemporalShard]] = []
        for shard in shards:
            score = self._score_shard(shard, query_tokens=query_tokens, intent=intent)
            if score <= 0.0:
                continue
            scored.append((score, shard))
        scored.sort(key=lambda item: item[0], reverse=True)
        hits: List[MemoryHit] = []
        for index, (score, shard) in enumerate(scored[: max(2, top_k)]):
            hits.append(
                MemoryHit(
                    memory_id=f"temporal_shard:{shard.scale}:{shard.bucket_id}",
                    category="history" if intent.history_kind in {"previous", "compare", "timeline"} else "event",
                    value=shard.summary_text(),
                    relation="temporal_shard",
                    anchors=_dedupe([*shard.categories, *shard.entities, *shard.slots]),
                    score=float(score),
                    source_kind="temporal_shard",
                    slot_key=f"{shard.scale}.bucket.{shard.bucket_id}",
                    state="superseded" if shard.end_turn < self.latest_turn else "active",
                    turn_index=int(shard.end_turn),
                    metadata={
                        "temporal_shard": {
                            "enabled": True,
                            "scale": shard.scale,
                            "bucket_id": int(shard.bucket_id),
                            "start_turn": int(shard.start_turn),
                            "end_turn": int(shard.end_turn),
                            "turn_count": len(shard.turns),
                            "categories": list(shard.categories),
                            "slots": list(shard.slots),
                            "entities": list(shard.entities),
                            "query_temporal_hints": list(intent.temporal_hints),
                            "rank": int(index),
                        }
                    },
                )
            )
        return hits

    @property
    def latest_turn(self) -> int:
        return max((item.turn_index for item in self.turns), default=0)

    def stats(self) -> Dict[str, Any]:
        hour_shards = self._build_shards(intent=QueryIntent(kind="history", history_kind="timeline", temporal_hints=["timeline"]))
        day_shards = self._build_shards(intent=QueryIntent(kind="history", history_kind="timeline", temporal_hints=["timeline", "latest", "earliest"]), scale="day")
        return {
            "turn_count": len(self.turns),
            "hour_shard_count": len(hour_shards),
            "day_shard_count": len(day_shards),
            "turns_per_hour": int(self.turns_per_hour),
            "hours_per_day": int(self.hours_per_day),
        }

    def _needs_temporal_shards(self, intent: QueryIntent) -> bool:
        if intent.history_kind in {"previous", "compare", "timeline"}:
            return True
        temporal_markers = {"timeline", "previous", "earliest", "latest", "after"}
        if any(marker in temporal_markers for marker in intent.temporal_hints):
            return True
        return False

    def _build_shards(self, *, intent: QueryIntent, scale: str = "") -> List[TemporalShard]:
        use_day = scale == "day" or ("earliest" in intent.temporal_hints and "latest" in intent.temporal_hints)
        bucket_span = self.turns_per_hour * self.hours_per_day if use_day else self.turns_per_hour
        shard_scale = "day" if use_day else "hour"
        grouped: Dict[int, List[TemporalTurnRecord]] = {}
        for record in self.turns:
            bucket = max(0, (int(record.turn_index) - 1) // bucket_span)
            grouped.setdefault(bucket, []).append(record)
        shards: List[TemporalShard] = []
        for bucket_id, records in grouped.items():
            ordered = sorted(records, key=lambda item: item.turn_index)
            shards.append(
                TemporalShard(
                    scale=shard_scale,
                    bucket_id=int(bucket_id),
                    start_turn=int(ordered[0].turn_index),
                    end_turn=int(ordered[-1].turn_index),
                    turns=ordered,
                    categories=_dedupe(item for record in ordered for item in record.category_hints),
                    slots=_dedupe(item for record in ordered for item in record.slot_hints),
                    entities=_dedupe(item for record in ordered for item in record.entity_hints),
                )
            )
        shards.sort(key=lambda item: (item.end_turn, item.bucket_id), reverse=True)
        return shards

    def _score_shard(self, shard: TemporalShard, *, query_tokens: List[str], intent: QueryIntent) -> float:
        summary_tokens = _tokenize(shard.summary_text())
        text_tokens = _tokenize(" ".join(record.combined_text for record in shard.turns))
        category_match = 0.22 if intent.category_hints and any(item in shard.categories for item in intent.category_hints) else 0.0
        entity_match = 0.18 if intent.entity_hints and any(_normalize(hint) in {_normalize(item) for item in shard.entities} for hint in intent.entity_hints) else 0.0
        lexical = 0.3 * _overlap(query_tokens, summary_tokens) + 0.24 * _overlap(query_tokens, text_tokens)
        recency = 0.12 if "latest" in intent.temporal_hints and shard.end_turn == self.latest_turn else 0.0
        older = 0.12 if "previous" in intent.temporal_hints and shard.end_turn < self.latest_turn else 0.0
        timeline = 0.16 if intent.history_kind == "timeline" and len(shard.turns) >= 2 else 0.0
        return category_match + entity_match + lexical + recency + older + timeline

    def _collect_categories(self, source: Dict[str, Any]) -> List[str]:
        values: List[str] = []
        for key in ("categories", "category_hints"):
            raw = source.get(key)
            if isinstance(raw, list):
                values.extend(str(item) for item in raw if _clean_text(item))
        records = source.get("replacement_memory_records") or source.get("records") or []
        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict) and _clean_text(record.get("category", "")):
                    values.append(str(record.get("category")))
        return _dedupe(values)

    def _collect_slots(self, source: Dict[str, Any]) -> List[str]:
        values: List[str] = []
        records = source.get("replacement_memory_records") or source.get("records") or []
        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict) and _clean_text(record.get("slot_key", "")):
                    values.append(str(record.get("slot_key")))
        return _dedupe(values)

    def _collect_entities(self, source: Dict[str, Any]) -> List[str]:
        values: List[str] = []
        records = source.get("replacement_memory_records") or source.get("records") or []
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict):
                    continue
                for key in ("entity", "entity_name", "subject", "anchor"):
                    if _clean_text(record.get(key, "")):
                        values.append(str(record.get(key)))
        return _dedupe(values)
