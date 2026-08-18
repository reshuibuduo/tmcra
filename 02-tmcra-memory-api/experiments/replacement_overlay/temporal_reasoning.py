from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .intent import QueryIntent
from .slot_state import ResolvedSlotRecord, SlotStateResolution


@dataclass(slots=True)
class TemporalStateChain:
    slot_key: str
    category: str
    current_state: ResolvedSlotRecord | None = None
    previous_state: ResolvedSlotRecord | None = None
    change_chain: List[ResolvedSlotRecord] = field(default_factory=list)
    time_anchor_start: int = 0
    time_anchor_end: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_key": self.slot_key,
            "category": self.category,
            "current_state": self.current_state.to_dict() if self.current_state is not None else None,
            "previous_state": self.previous_state.to_dict() if self.previous_state is not None else None,
            "change_chain": [item.to_dict() for item in self.change_chain],
            "time_anchor_start": int(self.time_anchor_start),
            "time_anchor_end": int(self.time_anchor_end),
        }


@dataclass(slots=True)
class TemporalReasoningTrace:
    mode: str
    current_state: Dict[str, Any] | None = None
    previous_state: Dict[str, Any] | None = None
    change_chain: List[Dict[str, Any]] = field(default_factory=list)
    time_anchor_start: int = 0
    time_anchor_end: int = 0
    temporal_confidence: float = 0.0
    chains: List[TemporalStateChain] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "current_state": dict(self.current_state or {}) if self.current_state is not None else None,
            "previous_state": dict(self.previous_state or {}) if self.previous_state is not None else None,
            "change_chain": list(self.change_chain),
            "time_anchor_start": int(self.time_anchor_start),
            "time_anchor_end": int(self.time_anchor_end),
            "temporal_confidence": round(float(self.temporal_confidence), 6),
            "chains": [item.to_dict() for item in self.chains],
        }


class TemporalReasoner:
    def reason(self, query: str, *, intent: QueryIntent, resolution: SlotStateResolution) -> TemporalReasoningTrace:
        _ = query
        mode = self._mode(intent, resolution)
        chains: List[TemporalStateChain] = []
        for view in resolution.views:
            chain = list(view.historical_chain)
            if view.active_record is not None and all(view.active_record.memory_id != item.memory_id for item in chain):
                chain.append(view.active_record)
                chain.sort(key=lambda item: (item.turn_index, item.score))
            chains.append(
                TemporalStateChain(
                    slot_key=view.slot_key,
                    category=view.category,
                    current_state=view.active_record,
                    previous_state=view.previous_record,
                    change_chain=chain,
                    time_anchor_start=int(chain[0].turn_index) if chain else (int(view.active_record.turn_index) if view.active_record is not None else 0),
                    time_anchor_end=int(chain[-1].turn_index) if chain else (int(view.active_record.turn_index) if view.active_record is not None else 0),
                )
            )
        primary = chains[0] if chains else TemporalStateChain(slot_key="", category="")
        total_states = sum(len(chain.change_chain) for chain in chains)
        confidence = 0.0
        if chains:
            confidence = min(0.98, 0.45 + total_states * 0.08 + (0.1 if primary.current_state is not None else 0.0))
            if primary.previous_state is not None:
                confidence += 0.08
            confidence = min(0.98, confidence)
        return TemporalReasoningTrace(
            mode=mode,
            current_state=primary.current_state.to_dict() if primary.current_state is not None else None,
            previous_state=primary.previous_state.to_dict() if primary.previous_state is not None else None,
            change_chain=[item.to_dict() for item in primary.change_chain],
            time_anchor_start=int(primary.time_anchor_start),
            time_anchor_end=int(primary.time_anchor_end),
            temporal_confidence=confidence,
            chains=chains,
        )

    def _mode(self, intent: QueryIntent, resolution: SlotStateResolution) -> str:
        if intent.history_kind in {"current", "previous", "compare", "timeline"}:
            return intent.history_kind
        if intent.path_mode in {"temporal_path", "state_evolution_path"}:
            return "temporal_path"
        if resolution.mode in {"current", "previous", "compare", "timeline"}:
            return resolution.mode
        return "current"
