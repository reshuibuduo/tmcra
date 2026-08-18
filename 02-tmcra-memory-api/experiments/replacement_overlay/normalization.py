from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from experiments.replacement.adapters.base import MemoryHit, MemoryRetrieval
from experiments.replacement.memory_graph import stable_slot_key


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _dedupe(items: List[str]) -> List[str]:
    results: List[str] = []
    seen = set()
    for item in items:
        text = _clean_text(item)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(text)
    return results


@dataclass(slots=True)
class LegacySlotNormalizer:
    def normalize_slot_key(
        self,
        *,
        category: str,
        value: str,
        anchors: List[str],
        slot_key: str = "",
        relation: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> Tuple[str, List[str]]:
        raw_slot = _clean_text(slot_key or (metadata or {}).get("slot_key") or (metadata or {}).get("slot"))
        normalized = stable_slot_key(
            category=category,
            value=value,
            anchors=anchors,
            slot_key=raw_slot,
            relation=relation,
            metadata=metadata,
        )
        aliases = _dedupe([raw_slot] if raw_slot and raw_slot != normalized else [])
        return normalized, aliases

    def normalize_hit(self, hit: MemoryHit) -> MemoryHit:
        metadata = dict(hit.metadata or {})
        normalized_slot, aliases = self.normalize_slot_key(
            category=hit.category,
            value=hit.value,
            anchors=list(hit.anchors),
            slot_key=hit.slot_key,
            relation=hit.relation,
            metadata=metadata,
        )
        if aliases:
            metadata["legacy_slot_aliases"] = aliases
        metadata["normalized_slot_key"] = normalized_slot
        return MemoryHit(
            memory_id=hit.memory_id,
            category=hit.category,
            value=hit.value,
            relation=hit.relation,
            anchors=list(hit.anchors),
            score=float(hit.score),
            source_kind=hit.source_kind,
            slot_key=normalized_slot,
            state=hit.state,
            turn_index=int(hit.turn_index or 0),
            metadata=metadata,
        )

    def normalize_retrieval(self, retrieval: MemoryRetrieval) -> MemoryRetrieval:
        alias_map: Dict[str, str] = {}

        def _normalize_hits(items: List[MemoryHit]) -> List[MemoryHit]:
            normalized_items: List[MemoryHit] = []
            for hit in items:
                normalized = self.normalize_hit(hit)
                for alias in normalized.metadata.get("legacy_slot_aliases", []) or []:
                    alias_map[str(alias)] = normalized.slot_key
                normalized_items.append(normalized)
            return normalized_items

        hits = _normalize_hits(list(retrieval.hits))
        active_hits = _normalize_hits(list(retrieval.active_hits))
        history_hits = _normalize_hits(list(retrieval.history_hits))
        stale_hits = _normalize_hits(list(retrieval.stale_hits))
        overwrite_hits = _normalize_hits(list(retrieval.overwrite_hits))
        false_hits = _normalize_hits(list(retrieval.false_hits))
        return MemoryRetrieval(
            concepts=list(retrieval.concepts),
            relations=list(retrieval.relations),
            hits=hits,
            active_hits=active_hits,
            history_hits=history_hits,
            stale_hits=stale_hits,
            overwrite_hits=overwrite_hits,
            false_hits=false_hits,
            retrieval_seconds=float(retrieval.retrieval_seconds),
            context_token_estimate=int(retrieval.context_token_estimate),
            retrieval_context_token_estimate=int(retrieval.retrieval_context_token_estimate or retrieval.context_token_estimate),
            metadata={
                **dict(retrieval.metadata or {}),
                "slot_aliases": alias_map,
            },
        )
