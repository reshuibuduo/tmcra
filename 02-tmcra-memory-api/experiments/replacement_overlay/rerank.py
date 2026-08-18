from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
from typing import Dict, Iterable, List, Sequence, Tuple

from experiments.replacement.adapters.base import MemoryHit, MemoryRetrieval

from .entity import EntityDisambiguator
from .intent import QueryIntent


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _normalize(value: object) -> str:
    return _clean_text(value).lower()


def _tokenize(value: object) -> List[str]:
    import re

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


def _estimate_tokens(text: object) -> int:
    clean = _clean_text(text)
    if not clean:
        return 0
    return max(1, math.ceil(len(clean) / 4))


def _overlap_ratio(left: Sequence[str], right: Sequence[str]) -> float:
    left_set, right_set = set(left), set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / max(1, len(left_set | right_set))


def _copy_hit(hit: MemoryHit, *, extra_metadata: Dict[str, object] | None = None, score: float | None = None) -> MemoryHit:
    metadata = dict(hit.metadata or {})
    if extra_metadata:
        metadata.update(extra_metadata)
    return MemoryHit(
        memory_id=hit.memory_id,
        category=hit.category,
        value=hit.value,
        relation=hit.relation,
        anchors=list(hit.anchors),
        score=float(hit.score if score is None else score),
        source_kind=hit.source_kind,
        slot_key=hit.slot_key,
        state=hit.state,
        turn_index=hit.turn_index,
        metadata=metadata,
    )


@dataclass(slots=True)
class ScoredCandidate:
    hit: MemoryHit
    memory_id: str
    base_score: float
    rerank_score: float
    score_breakdown: Dict[str, float]
    entity_key: str
    slot_key: str
    state: str
    turn_index: int
    temporal_rank: int
    temporal_role: str = "neutral"
    overwrite_distance: int = 0
    entity_last_seen_turn: int = 0
    entity_match_type: str = "none"
    selection_reason: str = ""
    partial_compare: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "base_score": round(float(self.base_score), 6),
            "rerank_score": round(float(self.rerank_score), 6),
            "score_breakdown": {key: round(float(value), 6) for key, value in self.score_breakdown.items()},
            "entity_key": self.entity_key,
            "slot_key": self.slot_key,
            "state": self.state,
            "turn_index": int(self.turn_index),
            "temporal_rank": int(self.temporal_rank),
            "temporal_role": self.temporal_role,
            "overwrite_distance": int(self.overwrite_distance),
            "entity_last_seen_turn": int(self.entity_last_seen_turn),
            "entity_match_type": self.entity_match_type,
            "selection_reason": self.selection_reason,
            "partial_compare": bool(self.partial_compare),
        }


class RetrievalReranker:
    def __init__(self, *, entity_disambiguator: EntityDisambiguator | None = None) -> None:
        self.entity_disambiguator = entity_disambiguator or EntityDisambiguator()

    def rerank(self, query: str, base_retrieval: MemoryRetrieval, *, top_k: int, intent: QueryIntent) -> MemoryRetrieval:
        query_tokens = _tokenize(query)
        unique_hits = self._unique_hits(base_retrieval)
        latest_turn = max((hit.turn_index for hit in unique_hits), default=0)
        candidates = [self._score_hit(query_tokens, hit, intent, latest_turn=latest_turn) for hit in unique_hits]
        entity_last_seen: Dict[str, int] = defaultdict(int)
        slot_latest_turn: Dict[str, int] = defaultdict(int)
        for item in candidates:
            entity_last_seen[item.entity_key] = max(entity_last_seen[item.entity_key], int(item.turn_index or 0))
            slot_latest_turn[item.slot_key] = max(slot_latest_turn[item.slot_key], int(item.turn_index or 0))
        for item in candidates:
            item.entity_last_seen_turn = int(entity_last_seen.get(item.entity_key, item.turn_index))
            item.overwrite_distance = max(0, int(slot_latest_turn.get(item.slot_key, item.turn_index)) - int(item.turn_index or 0))
        candidates.sort(key=lambda item: (item.rerank_score, item.state == "active", item.turn_index), reverse=True)
        selected, partial_compare_slots, entity_conflict = self._select(candidates, intent=intent, top_k=top_k)
        selected_hits = [
            _copy_hit(
                item.hit,
                score=item.rerank_score,
                extra_metadata={
                    "overlay_score_breakdown": dict(item.score_breakdown),
                    "entity_key": item.entity_key,
                    "overlay_rerank_score": round(float(item.rerank_score), 6),
                    "overlay_base_score": round(float(item.base_score), 6),
                    "overlay_entity_match_type": item.entity_match_type,
                    "overlay_selection_reason": item.selection_reason,
                    "overlay_partial_compare": bool(item.partial_compare),
                    "overlay_time": {
                        "turn_index": int(item.turn_index),
                        "temporal_rank": int(item.temporal_rank),
                        "temporal_role": item.temporal_role,
                        "overwrite_distance": int(item.overwrite_distance),
                        "entity_last_seen_turn": int(item.entity_last_seen_turn),
                        "age_from_latest": int(max(0, latest_turn - item.turn_index)) if latest_turn and item.turn_index else 0,
                        "query_temporal_hints": list(intent.temporal_hints),
                    },
                },
            )
            for item in selected
        ]
        context_tokens = self._context_tokens(selected_hits)
        return MemoryRetrieval(
            concepts=list(base_retrieval.concepts),
            relations=list(base_retrieval.relations),
            hits=list(selected_hits),
            active_hits=[hit for hit in selected_hits if hit.state == "active"],
            history_hits=[hit for hit in selected_hits if hit.state != "active"],
            stale_hits=[hit for hit in selected_hits if hit.state == "superseded"],
            overwrite_hits=[hit for hit in selected_hits if hit.state == "superseded"],
            false_hits=[hit for hit in selected_hits if hit.state == "false"],
            retrieval_seconds=base_retrieval.retrieval_seconds,
            context_token_estimate=context_tokens,
            retrieval_context_token_estimate=context_tokens,
            metadata={
                **dict(base_retrieval.metadata or {}),
                "overlay": {
                    "enabled": True,
                    "query_intent": intent.to_dict(),
                    "coarse_hit_count": len(candidates),
                    "selected_memory_ids": [item.memory_id for item in selected],
                    "selection_reasons": {item.memory_id: item.selection_reason for item in selected},
                    "partial_compare_slots": list(partial_compare_slots),
                    "entity_conflict": bool(entity_conflict),
                    "temporal_scope": {
                        "latest_turn": int(latest_turn),
                        "selected_turns": [int(item.turn_index) for item in selected if item.turn_index],
                        "history_kind": intent.history_kind,
                    },
                    "score_breakdown": {item.memory_id: item.to_dict() for item in candidates},
                    "original_retrieval": base_retrieval.to_dict(),
                },
            },
        )

    def _unique_hits(self, retrieval: MemoryRetrieval) -> List[MemoryHit]:
        seen = set()
        unique: List[MemoryHit] = []
        for hit in [*retrieval.hits, *retrieval.active_hits, *retrieval.history_hits]:
            if not hit.memory_id or hit.memory_id in seen:
                continue
            seen.add(hit.memory_id)
            unique.append(hit)
        return unique

    def _score_hit(self, query_tokens: List[str], hit: MemoryHit, intent: QueryIntent, *, latest_turn: int) -> ScoredCandidate:
        slot_tokens = _tokenize(hit.slot_key)
        anchor_tokens = _tokenize(" ".join(hit.anchors))
        match_details = self.entity_disambiguator.match_details(
            hit,
            intent.entity_hints,
            temporal_hints=intent.temporal_hints,
            latest_turn=latest_turn,
        )
        category_match = 0.22 if intent.category_hints and hit.category in intent.category_hints else 0.0
        slot_match = 0.16 * _overlap_ratio(query_tokens, slot_tokens)
        entity_match = float(match_details.get("score", 0.0))
        history_match = self._history_score(hit, intent.history_kind)
        path_match = self._path_score(hit, intent.kind)
        state_preference = self._state_preference(hit, intent.history_kind)
        anchor_support = 0.12 * _overlap_ratio(query_tokens, anchor_tokens)
        temporal_alignment = self._temporal_alignment(hit, history_kind=intent.history_kind, latest_turn=latest_turn)
        base_score = float(hit.score)
        breakdown = {
            "category_match": category_match,
            "slot_match": slot_match,
            "entity_match": entity_match,
            "history_match": history_match,
            "path_match": path_match,
            "state_preference": state_preference,
            "anchor_support": anchor_support,
            "temporal_alignment": temporal_alignment,
        }
        turn_index = int(hit.turn_index or 0)
        return ScoredCandidate(
            hit=hit,
            memory_id=hit.memory_id,
            base_score=base_score,
            rerank_score=base_score + sum(breakdown.values()),
            score_breakdown=breakdown,
            entity_key=self.entity_disambiguator.derive_entity_key(hit),
            slot_key=hit.slot_key or hit.category,
            state=hit.state,
            turn_index=turn_index,
            temporal_rank=max(0, latest_turn - turn_index) if latest_turn and turn_index else 0,
            temporal_role=self._temporal_role(hit, history_kind=intent.history_kind, latest_turn=latest_turn),
            entity_match_type=str(match_details.get("match_type", "none")),
        )

    def _history_score(self, hit: MemoryHit, history_kind: str) -> float:
        if history_kind == "none":
            return 0.1 if hit.state == "active" else -0.22
        if history_kind == "current":
            return 0.18 if hit.state == "active" else -0.14
        if history_kind == "previous":
            return 0.26 if hit.state != "active" else -0.1
        if history_kind == "compare":
            return 0.16 if hit.state == "active" else 0.24
        if history_kind == "timeline":
            return 0.22 if hit.state != "active" else 0.04
        return 0.0

    def _state_preference(self, hit: MemoryHit, history_kind: str) -> float:
        if history_kind in {"none", "current"}:
            return 0.1 if hit.state == "active" else -0.12
        if history_kind == "previous":
            return 0.12 if hit.state != "active" else -0.05
        if history_kind == "compare":
            return 0.04
        if history_kind == "timeline":
            return 0.02 if hit.state != "active" else -0.04
        return 0.0

    def _path_score(self, hit: MemoryHit, kind: str) -> float:
        if kind == "path":
            return 0.28 if hit.relation == "path_edge" else -0.02
        return 0.0

    def _temporal_alignment(self, hit: MemoryHit, *, history_kind: str, latest_turn: int) -> float:
        if not latest_turn or not hit.turn_index:
            return 0.0
        age = max(0, latest_turn - int(hit.turn_index))
        if history_kind in {"none", "current"}:
            return max(-0.02, 0.08 - (age * 0.015))
        if history_kind == "previous":
            if hit.state == "active":
                return -0.03
            return max(0.02, 0.1 - (max(0, age - 1) * 0.01))
        if history_kind == "compare":
            return 0.04 if age <= 2 else 0.02
        if history_kind == "timeline":
            return min(0.08, 0.02 + (age * 0.01))
        return 0.0

    def _temporal_role(self, hit: MemoryHit, *, history_kind: str, latest_turn: int) -> str:
        if hit.state == "active":
            return "current"
        age = max(0, int(latest_turn or 0) - int(hit.turn_index or 0))
        if history_kind == "timeline":
            return "timeline_oldest" if age >= 2 else "timeline_mid"
        return "previous"

    def _select(
        self,
        ordered: List[ScoredCandidate],
        *,
        intent: QueryIntent,
        top_k: int,
    ) -> Tuple[List[ScoredCandidate], List[str], bool]:
        selected: List[ScoredCandidate] = []
        slot_counts: Dict[str, int] = defaultdict(int)
        entity_counts: Dict[str, int] = defaultdict(int)
        partial_compare_slots: List[str] = []
        entity_conflict = self._detect_entity_conflict(ordered, intent=intent)
        slot_limit = 4 if intent.history_kind == "timeline" else 2
        entity_limit = 4 if intent.history_kind == "timeline" else 2

        def can_add(item: ScoredCandidate) -> bool:
            if item.slot_key and slot_counts[item.slot_key] >= slot_limit:
                return False
            if item.entity_key and entity_counts[item.entity_key] >= entity_limit:
                return False
            return True

        primary_pool = [item for item in ordered if item.hit.relation == "path_edge"] if intent.kind == "path" else list(ordered)
        secondary_pool = [item for item in ordered if item.hit.relation != "path_edge"] if intent.kind == "path" else []

        for item in [*primary_pool, *secondary_pool]:
            if len(selected) >= top_k:
                break
            if not can_add(item):
                continue
            item.selection_reason = "top_rank"
            selected.append(item)
            slot_counts[item.slot_key] += 1
            entity_counts[item.entity_key] += 1

        if intent.history_kind == "compare":
            extras, partial_compare_slots = self._pair_history_compare(selected, ordered)
            for item in extras:
                if item.memory_id in {entry.memory_id for entry in selected}:
                    continue
                if not can_add(item):
                    continue
                selected.append(item)
                slot_counts[item.slot_key] += 1
                entity_counts[item.entity_key] += 1

        if intent.history_kind == "timeline":
            for item in self._timeline_chain(selected, ordered):
                if len(selected) >= max(top_k, 4):
                    break
                if item.memory_id in {entry.memory_id for entry in selected} or not can_add(item):
                    continue
                selected.append(item)
                slot_counts[item.slot_key] += 1
                entity_counts[item.entity_key] += 1

        if not selected and intent.category_hints:
            fallback = self._category_fallback(ordered, intent=intent)
            if fallback is not None:
                fallback.selection_reason = "category_fallback"
                selected = [fallback]

        selected.sort(key=lambda item: (item.rerank_score, item.turn_index), reverse=True)
        limit = max(top_k, 4 if intent.history_kind == "timeline" else (1 if intent.kind == "path" else top_k))
        return selected[:limit], partial_compare_slots, entity_conflict

    def _pair_history_compare(
        self,
        selected: List[ScoredCandidate],
        ordered: List[ScoredCandidate],
    ) -> Tuple[List[ScoredCandidate], List[str]]:
        target_slots = {item.slot_key for item in selected if item.slot_key}
        if not target_slots and ordered:
            target_slots = {ordered[0].slot_key}
        extras: List[ScoredCandidate] = []
        partial_compare_slots: List[str] = []
        for slot_key in target_slots:
            group = [item for item in ordered if item.slot_key == slot_key]
            active = next((item for item in group if item.state == "active"), None)
            historical = next((item for item in group if item.state != "active"), None)
            if active is None or historical is None:
                if slot_key:
                    partial_compare_slots.append(slot_key)
                survivor = active or historical
                if survivor is not None:
                    survivor.partial_compare = True
                    survivor.selection_reason = survivor.selection_reason or "history_compare_partial"
                    extras.append(survivor)
                continue
            active.selection_reason = active.selection_reason or "history_compare_pair"
            historical.selection_reason = historical.selection_reason or "history_compare_pair"
            extras.extend([historical, active])
        return extras, _dedupe(partial_compare_slots)

    def _timeline_chain(self, selected: List[ScoredCandidate], ordered: List[ScoredCandidate]) -> List[ScoredCandidate]:
        slot_key = ""
        for item in selected:
            if item.slot_key:
                slot_key = item.slot_key
                break
        if not slot_key:
            historical = [item for item in ordered if item.state != "active"]
            slot_key = historical[0].slot_key if historical else (ordered[0].slot_key if ordered else "")
        if not slot_key:
            return []
        group = [item for item in ordered if item.slot_key == slot_key]
        group.sort(key=lambda item: item.turn_index)
        results: List[ScoredCandidate] = []
        for item in group[:4]:
            item.selection_reason = item.selection_reason or "timeline_chain"
            results.append(item)
        return results

    def _category_fallback(self, ordered: List[ScoredCandidate], *, intent: QueryIntent) -> ScoredCandidate | None:
        for item in ordered:
            if item.hit.category in intent.category_hints and item.state == "active":
                return item
        for item in ordered:
            if item.hit.category in intent.category_hints:
                return item
        return None

    def _detect_entity_conflict(self, ordered: List[ScoredCandidate], *, intent: QueryIntent) -> bool:
        if not intent.entity_hints:
            return False
        top = [item for item in ordered if item.entity_key][:2]
        if len(top) < 2:
            return False
        if top[0].entity_key == top[1].entity_key:
            return False
        return abs(top[0].rerank_score - top[1].rerank_score) <= 0.08

    def _context_tokens(self, hits: Sequence[MemoryHit]) -> int:
        total = 0
        for hit in hits:
            total += _estimate_tokens(hit.value)
            total += sum(_estimate_tokens(anchor) for anchor in hit.anchors)
        return total
