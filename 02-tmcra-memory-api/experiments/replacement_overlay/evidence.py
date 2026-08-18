from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from experiments.replacement.adapters.base import AdapterResponse, MemoryHit, MemoryRetrieval
from experiments.replacement.adapters.reasoning_adapters import _render_natural_answer, _render_transparent_answer
from experiments.replacement.memory_profiles import TMCRAProfile

from .answer_planner import AnswerPlan
from .contracts import OverlayReasonerConfig, StructuredReasoningPrior
from .intent import QueryIntent
from .pathing import PathCandidate
from .slot_state import SlotStateResolution
from .temporal_reasoning import TemporalReasoningTrace


_CATEGORY_LABELS = {
    "goal": ("\u5f53\u524d\u76ee\u6807", "\u5386\u53f2\u76ee\u6807"),
    "constraint": ("\u5f53\u524d\u7ea6\u675f", "\u5386\u53f2\u7ea6\u675f"),
    "preference": ("\u5f53\u524d\u504f\u597d", "\u5386\u53f2\u504f\u597d"),
    "terminology": ("\u5f53\u524d\u672f\u8bed", "\u5386\u53f2\u672f\u8bed"),
    "stage_state": ("\u5f53\u524d\u9636\u6bb5", "\u5386\u53f2\u9636\u6bb5"),
}


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _contains_cjk(value: object) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in _clean_text(value))


def _dedupe(items: Iterable[object]) -> List[str]:
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
class EvidencePack:
    summary: str
    facts: List[Dict[str, Any]] = field(default_factory=list)
    paths: List[Dict[str, Any]] = field(default_factory=list)
    used_memory_ids: List[str] = field(default_factory=list)
    suppressed_memory_ids: List[str] = field(default_factory=list)
    claims: List[Dict[str, Any]] = field(default_factory=list)
    selected_memory_ids: List[str] = field(default_factory=list)
    temporal_trace: Dict[str, Any] = field(default_factory=dict)
    resolution_mode: str = "current"
    conflict_state: str = "none"
    evidence_mode: str = "slot_current"
    candidate_scores: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    unsupported_claims: List[str] = field(default_factory=list)
    fallback_used: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "facts": list(self.facts),
            "paths": list(self.paths),
            "used_memory_ids": list(self.used_memory_ids),
            "suppressed_memory_ids": list(self.suppressed_memory_ids),
            "claims": list(self.claims),
            "selected_memory_ids": list(self.selected_memory_ids),
            "temporal_trace": dict(self.temporal_trace),
            "resolution_mode": self.resolution_mode,
            "conflict_state": self.conflict_state,
            "evidence_mode": self.evidence_mode,
            "candidate_scores": list(self.candidate_scores),
            "confidence": round(float(self.confidence), 6),
            "unsupported_claims": list(self.unsupported_claims),
            "fallback_used": bool(self.fallback_used),
        }


class EvidenceRealizer:
    def __init__(self, *, profile: TMCRAProfile | None = None) -> None:
        self.profile = profile or TMCRAProfile()

    def build(
        self,
        query: str,
        *,
        intent: QueryIntent,
        retrieval: MemoryRetrieval,
        path_candidates: Sequence[PathCandidate],
        base_response: AdapterResponse,
    ) -> EvidencePack:
        _ = base_response
        if intent.kind == "path":
            return self._path_pack(query, intent=intent, retrieval=retrieval, path_candidates=path_candidates)
        if intent.kind == "summary":
            return self._summary_pack(query, retrieval=retrieval)
        if intent.kind == "history":
            return self._history_pack(query, intent=intent, retrieval=retrieval)
        return self._slot_pack(query, retrieval=retrieval)

    def render_response(
        self,
        query: str,
        *,
        intent: QueryIntent,
        retrieval: MemoryRetrieval,
        path_candidates: Sequence[PathCandidate],
        base_response: AdapterResponse,
        reasoner_name: str,
        memory_name: str,
    ) -> AdapterResponse:
        pack = self.build(query, intent=intent, retrieval=retrieval, path_candidates=path_candidates, base_response=base_response)
        if not pack.used_memory_ids and not pack.paths and not pack.facts:
            return base_response
        visible_hits = [hit.to_dict() for hit in retrieval.hits if hit.memory_id in set(pack.used_memory_ids)][:8]
        candidate_scores = [item.to_candidate_score() for item in path_candidates]
        confidence = max(0.08, min(0.98, base_response.confidence if pack.summary else 0.25))
        natural = _render_natural_answer(summary=pack.summary, facts=pack.facts, paths=pack.paths, memory_hits=visible_hits)
        answer = (
            _render_transparent_answer(
                natural,
                facts=pack.facts,
                paths=pack.paths,
                memory_hits=visible_hits,
                candidate_scores=candidate_scores,
                confidence=confidence,
            )
            if base_response.answer_mode == "transparent"
            else natural
        )
        return AdapterResponse(
            answer=answer,
            answer_mode=base_response.answer_mode,
            reasoner_name=reasoner_name,
            memory_name=memory_name,
            confidence=confidence,
            paths=pack.paths,
            facts=pack.facts,
            candidate_scores=candidate_scores,
            memory_hits=visible_hits,
            evidence_consistent=pack.conflict_state == "none" and bool(pack.used_memory_ids or pack.paths or pack.facts),
            unsupported_claims=[],
            pillar_scores=dict(base_response.pillar_scores or {}),
            latency_seconds=base_response.latency_seconds,
            trace=dict(base_response.trace or {}),
            metadata={**dict(base_response.metadata or {}), "overlay_evidence_pack": pack.to_dict()},
        )

    def render_planned_response(
        self,
        query: str,
        *,
        answer_mode: str,
        intent: QueryIntent,
        retrieval: MemoryRetrieval,
        path_candidates: Sequence[PathCandidate],
        answer_plan: AnswerPlan,
        slot_resolution: SlotStateResolution,
        temporal_trace: TemporalReasoningTrace,
        base_response: AdapterResponse | None,
        prior: StructuredReasoningPrior,
        config: OverlayReasonerConfig,
        reasoner_name: str,
        memory_name: str,
    ) -> AdapterResponse:
        pack = EvidencePack(
            summary=answer_plan.summary,
            facts=list(answer_plan.facts),
            paths=list(answer_plan.paths),
            used_memory_ids=list(answer_plan.selected_memory_ids),
            suppressed_memory_ids=list(answer_plan.suppressed_memory_ids),
            claims=[item.to_dict() for item in answer_plan.claims],
            selected_memory_ids=list(answer_plan.selected_memory_ids),
            temporal_trace=temporal_trace.to_dict(),
            resolution_mode=slot_resolution.mode,
            conflict_state=answer_plan.conflict_state,
            evidence_mode=answer_plan.evidence_mode,
            candidate_scores=list(answer_plan.candidate_scores),
            confidence=float(answer_plan.confidence),
            unsupported_claims=list(answer_plan.unsupported_claims),
        )
        fallback_allowed = config.fallback_policy == "fallback_only"
        shadow_compare = config.fallback_policy == "shadow_compare"
        if not pack.used_memory_ids and not pack.paths and not pack.facts and base_response is not None and fallback_allowed:
            pack.fallback_used = True
            fallback = AdapterResponse(
                answer=base_response.answer,
                answer_mode=base_response.answer_mode,
                reasoner_name=reasoner_name,
                memory_name=memory_name,
                confidence=base_response.confidence,
                paths=list(base_response.paths),
                facts=list(base_response.facts),
                candidate_scores=list(base_response.candidate_scores),
                memory_hits=list(base_response.memory_hits),
                evidence_consistent=bool(base_response.evidence_consistent),
                unsupported_claims=list(base_response.unsupported_claims),
                pillar_scores=dict(base_response.pillar_scores or {}),
                latency_seconds=base_response.latency_seconds,
                trace=dict(base_response.trace or {}),
                metadata={**dict(base_response.metadata or {}), "overlay_evidence_pack": pack.to_dict()},
            )
            return fallback

        visible_ids = set(pack.used_memory_ids)
        visible_hits = [hit.to_dict() for hit in retrieval.hits if not visible_ids or hit.memory_id in visible_ids][:8]
        confidence = max(0.08, min(0.98, float(pack.confidence or (base_response.confidence if base_response is not None else 0.25))))
        natural = _render_natural_answer(summary=pack.summary, facts=pack.facts, paths=pack.paths, memory_hits=visible_hits)
        answer = (
            _render_transparent_answer(
                natural,
                facts=pack.facts,
                paths=pack.paths,
                memory_hits=visible_hits,
                candidate_scores=pack.candidate_scores or [item.to_candidate_score() for item in path_candidates],
                confidence=confidence,
            )
            if answer_mode == "transparent"
            else natural
        )
        pillar_scores = dict(base_response.pillar_scores or {}) if base_response is not None else {}
        latency_seconds = float(base_response.latency_seconds) if base_response is not None else 0.0
        return AdapterResponse(
            answer=answer,
            answer_mode=answer_mode,
            reasoner_name=reasoner_name,
            memory_name=memory_name,
            confidence=confidence,
            paths=pack.paths,
            facts=pack.facts,
            candidate_scores=pack.candidate_scores or [item.to_candidate_score() for item in path_candidates],
            memory_hits=visible_hits,
            evidence_consistent=not pack.unsupported_claims and (bool(pack.used_memory_ids or pack.paths or pack.facts) or any(claim.get("claim_type") in {"missing_notice", "conflict_notice"} for claim in pack.claims)),
            unsupported_claims=list(pack.unsupported_claims),
            pillar_scores=pillar_scores,
            latency_seconds=latency_seconds,
            trace=dict(base_response.trace or {}) if base_response is not None else {},
            metadata={
                **(dict(base_response.metadata or {}) if base_response is not None else {}),
                "overlay_evidence_pack": pack.to_dict(),
                "overlay_resolution_mode": slot_resolution.mode,
                "overlay_temporal_trace": temporal_trace.to_dict(),
                "overlay_claims": list(pack.claims),
                "overlay_intent": intent.to_dict(),
                "overlay_prior": prior.to_dict(),
                "overlay_config": config.to_dict(),
                "overlay_shadow_base_response": base_response.to_dict() if base_response is not None and shadow_compare else None,
            },
        )

    def _slot_pack(self, query: str, *, retrieval: MemoryRetrieval) -> EvidencePack:
        prefer_chinese = _contains_cjk(query)
        overlay_meta = dict(retrieval.metadata.get("overlay", {}) or {})
        if overlay_meta.get("entity_conflict"):
            summary = (
                "\u5b9e\u4f53\u5019\u9009\u5b58\u5728\u51b2\u7a81\uff0c\u9700\u8981\u66f4\u660e\u786e\u7684\u9650\u5b9a\u6761\u4ef6\u624d\u80fd\u786e\u5b9a\u76ee\u6807\u5b9e\u4f53\u3002"
                if prefer_chinese
                else "Entity candidates conflict; a more specific discriminator is needed."
            )
            return EvidencePack(summary=summary, conflict_state="entity_conflict", evidence_mode="slot_current")
        if self._has_active_conflict(retrieval.active_hits):
            summary = (
                "\u5f53\u524d\u8bc1\u636e\u5b58\u5728\u51b2\u7a81\uff0c\u65e0\u6cd5\u786e\u5b9a\u5355\u4e00\u5f53\u524d\u503c\u3002"
                if prefer_chinese
                else "Current evidence conflicts, so no single current value can be confirmed."
            )
            return EvidencePack(summary=summary, conflict_state="ambiguous", evidence_mode="slot_current")
        hit = retrieval.active_hits[0] if retrieval.active_hits else (retrieval.hits[0] if retrieval.hits else None)
        if hit is None:
            summary = (
                "\u6ca1\u6709\u627e\u5230\u6709\u4f9d\u636e\u7684\u5f53\u524d\u8bb0\u5fc6\u3002"
                if prefer_chinese
                else "No grounded current memory found."
            )
            return EvidencePack(summary=summary, conflict_state="missing", evidence_mode="slot_current")
        label = self._label(hit.category, prefer_chinese=prefer_chinese, history=False)
        time_label = self._turn_label(hit.turn_index, prefer_chinese=prefer_chinese)
        role_label = self._temporal_role_label(hit, prefer_chinese=prefer_chinese)
        summary = (
            f"{label}\u662f{hit.value}\uff0c\u5c5e\u4e8e{role_label}\uff0c\u4f9d\u636e\u6765\u81ea{time_label}\u7684\u8bb0\u5fc6\u3002"
            if prefer_chinese
            else f"{label}: {hit.value}, treated as {role_label}, grounded by memory from {time_label}."
        )
        return EvidencePack(summary=summary, facts=self._facts_from_hits([hit]), used_memory_ids=[hit.memory_id], evidence_mode="slot_current")

    def _history_pack(self, query: str, *, intent: QueryIntent, retrieval: MemoryRetrieval) -> EvidencePack:
        prefer_chinese = _contains_cjk(query)
        overlay_meta = dict(retrieval.metadata.get("overlay", {}) or {})
        if intent.history_kind == "timeline":
            sequence = self._timeline_hits(retrieval)
            if not sequence:
                summary = (
                    "\u6ca1\u6709\u627e\u5230\u6709\u4f9d\u636e\u7684\u53d8\u5316\u65f6\u95f4\u7ebf\u3002"
                    if prefer_chinese
                    else "No grounded change timeline found."
                )
                return EvidencePack(summary=summary, conflict_state="missing", evidence_mode="history_timeline")
            label = self._label(sequence[-1].category, prefer_chinese=prefer_chinese, history=True)
            rendered = " -> ".join(
                f"{self._turn_label(hit.turn_index, prefer_chinese=prefer_chinese)}={hit.value}" for hit in sequence[:4]
            )
            summary = (
                f"{label}\u7684\u53d8\u5316\u65f6\u95f4\u7ebf\u662f\uff1a{rendered}\u3002"
                if prefer_chinese
                else f"Timeline for {sequence[-1].slot_key or sequence[-1].category}: {rendered}."
            )
            return EvidencePack(
                summary=summary,
                facts=self._facts_from_hits(sequence[:4]),
                used_memory_ids=[hit.memory_id for hit in sequence[:4]],
                evidence_mode="history_timeline",
            )

        history_hit, active_hit = self._paired_history_hits(retrieval)
        partial_compare = bool(overlay_meta.get("partial_compare_slots"))
        if history_hit is None and active_hit is None:
            summary = (
                "\u6ca1\u6709\u627e\u5230\u6709\u4f9d\u636e\u7684\u5386\u53f2\u8bb0\u5fc6\u3002"
                if prefer_chinese
                else "No grounded historical memory found."
            )
            return EvidencePack(summary=summary, conflict_state="missing", evidence_mode="history_previous")

        if intent.history_kind == "compare":
            if history_hit is not None and active_hit is not None:
                label = self._label(history_hit.category, prefer_chinese=prefer_chinese, history=True)
                summary = (
                    f"{label}\u662f{history_hit.value}\uff08{self._turn_label(history_hit.turn_index, prefer_chinese=prefer_chinese)}\uff0c{self._temporal_role_label(history_hit, prefer_chinese=prefer_chinese)}\uff09\uff1b"
                    f"\u5f53\u524d\u662f{active_hit.value}\uff08{self._turn_label(active_hit.turn_index, prefer_chinese=prefer_chinese)}\uff0c{self._temporal_role_label(active_hit, prefer_chinese=prefer_chinese)}\uff09\u3002"
                    if prefer_chinese
                    else f"Historical value was {history_hit.value} ({self._turn_label(history_hit.turn_index, prefer_chinese=prefer_chinese)}, {self._temporal_role_label(history_hit, prefer_chinese=prefer_chinese)}); "
                    f"current value is {active_hit.value} ({self._turn_label(active_hit.turn_index, prefer_chinese=prefer_chinese)}, {self._temporal_role_label(active_hit, prefer_chinese=prefer_chinese)})."
                )
                return EvidencePack(
                    summary=summary,
                    facts=self._facts_from_hits([history_hit, active_hit]),
                    used_memory_ids=[history_hit.memory_id, active_hit.memory_id],
                    evidence_mode="history_compare",
                )
            survivor = active_hit or history_hit
            if survivor is None:
                summary = (
                    "\u6ca1\u6709\u627e\u5230\u53ef\u6bd4\u5bf9\u7684\u5386\u53f2\u4e0e\u5f53\u524d\u53d6\u503c\u3002"
                    if prefer_chinese
                    else "No grounded current/history pair available."
                )
                return EvidencePack(summary=summary, conflict_state="missing", evidence_mode="history_compare")
            summary = (
                f"\u53ea\u627e\u5230{survivor.value}\uff08{self._turn_label(survivor.turn_index, prefer_chinese=prefer_chinese)}\uff09\uff0c"
                "\u53e6\u4e00\u4fa7\u8bc1\u636e\u4e0d\u8db3\uff0c\u65e0\u6cd5\u5b8c\u6574\u5bf9\u6bd4\u3002"
                if prefer_chinese
                else f"Only one side of the comparison is grounded: {survivor.value} "
                f"({self._turn_label(survivor.turn_index, prefer_chinese=prefer_chinese)}); the other side is missing."
            )
            return EvidencePack(
                summary=summary,
                facts=self._facts_from_hits([survivor]),
                used_memory_ids=[survivor.memory_id],
                conflict_state="partial_compare" if partial_compare else "missing",
                evidence_mode="history_compare",
            )

        hit = history_hit or active_hit
        label = self._label(hit.category, prefer_chinese=prefer_chinese, history=True)
        summary = (
            f"{label}\u662f{hit.value}\uff0c\u5c5e\u4e8e{self._temporal_role_label(hit, prefer_chinese=prefer_chinese)}\uff0c\u8bb0\u5f55\u5728{self._turn_label(hit.turn_index, prefer_chinese=prefer_chinese)}\u3002"
            if prefer_chinese
            else f"Historical value was {hit.value} as {self._temporal_role_label(hit, prefer_chinese=prefer_chinese)} at {self._turn_label(hit.turn_index, prefer_chinese=prefer_chinese)}."
        )
        return EvidencePack(summary=summary, facts=self._facts_from_hits([hit]), used_memory_ids=[hit.memory_id], evidence_mode="history_previous")

    def _summary_pack(self, query: str, *, retrieval: MemoryRetrieval) -> EvidencePack:
        prefer_chinese = _contains_cjk(query)
        ordered = self._best_active_by_category(retrieval.active_hits or retrieval.hits)
        if not ordered:
            summary = (
                "\u6ca1\u6709\u627e\u5230\u53ef\u603b\u7ed3\u7684\u6709\u4f9d\u636e\u8bb0\u5fc6\u3002"
                if prefer_chinese
                else "No grounded memory available for summary."
            )
            return EvidencePack(summary=summary, conflict_state="missing", evidence_mode="summary")
        parts: List[str] = []
        used_ids: List[str] = []
        for hit in ordered:
            label = self._label(hit.category, prefer_chinese=prefer_chinese, history=False)
            parts.append(
                f"{label}\uff1a{hit.value}\uff08{self._turn_label(hit.turn_index, prefer_chinese=prefer_chinese)}\uff0c{self._temporal_role_label(hit, prefer_chinese=prefer_chinese)}\uff09"
                if prefer_chinese
                else f"{label}: {hit.value} ({self._turn_label(hit.turn_index, prefer_chinese=prefer_chinese)}, {self._temporal_role_label(hit, prefer_chinese=prefer_chinese)})"
            )
            used_ids.append(hit.memory_id)
        summary = "\uff1b".join(parts) + ("\u3002" if prefer_chinese else ".")
        return EvidencePack(summary=summary, facts=self._facts_from_hits(ordered), used_memory_ids=used_ids, evidence_mode="summary")

    def _path_pack(
        self,
        query: str,
        *,
        intent: QueryIntent,
        retrieval: MemoryRetrieval,
        path_candidates: Sequence[PathCandidate],
    ) -> EvidencePack:
        _ = retrieval
        prefer_chinese = _contains_cjk(query)
        if not path_candidates:
            summary = (
                "\u6ca1\u6709\u627e\u5230\u6709\u4f9d\u636e\u7684\u8def\u5f84\u3002"
                if prefer_chinese
                else "No grounded path found."
            )
            return EvidencePack(summary=summary, conflict_state="missing", evidence_mode="path")

        used_ids = _dedupe(memory_id for item in path_candidates for memory_id in item.memory_ids)
        rendered_paths = [item.to_path() for item in path_candidates]

        if intent.path_mode == "multi" and len(path_candidates) > 1:
            descriptions = [self._describe_path(item, prefer_chinese=prefer_chinese) for item in path_candidates[:2]]
            zh_separator = "\uff1b"
            summary = (
                f"\u627e\u5230\u591a\u6761\u6709\u4f9d\u636e\u7684\u8def\u5f84\uff1a{zh_separator.join(descriptions)}\u3002"
                if prefer_chinese
                else f"Multiple grounded paths found: {'; '.join(descriptions)}."
            )
            return EvidencePack(summary=summary, paths=rendered_paths[:2], used_memory_ids=used_ids, evidence_mode="path")

        first = path_candidates[0]
        description = self._describe_path(first, prefer_chinese=prefer_chinese)
        if intent.path_mode == "counterfactual":
            summary = (
                f"\u79fb\u9664\u963b\u65ad\u6761\u4ef6\u540e\uff0c\u4ecd\u7136\u5b58\u5728\u6709\u4f9d\u636e\u7684\u8def\u5f84\uff1a{description}\u3002"
                if prefer_chinese
                else f"After removing the blocker, a grounded path still exists: {description}."
            )
        else:
            summary = (
                f"\u7ed3\u8bba\u8def\u5f84\u662f\uff1a{description}\u3002"
                if prefer_chinese
                else f"Grounded path: {description}."
            )
        return EvidencePack(summary=summary, paths=rendered_paths[:1], used_memory_ids=used_ids, evidence_mode="path")

    def _describe_path(self, candidate: PathCandidate, *, prefer_chinese: bool) -> str:
        path_text = " -> ".join(candidate.concepts)
        if not candidate.temporal_tunnels:
            return path_text
        tunnel_parts = [
            (
                f"{item['concept']}({self._turn_label(item['from_turn'], prefer_chinese=prefer_chinese)} -> "
                f"{self._turn_label(item['to_turn'], prefer_chinese=prefer_chinese)})"
            )
            for item in candidate.temporal_tunnels
        ]
        if prefer_chinese:
            zh_separator = "\uff0c"
            return f"{path_text}\uff0c\u901a\u8fc7\u65f6\u95f4\u9697\u7a7f\u8fde\u63a5\uff1a{zh_separator.join(tunnel_parts)}"
        return f"{path_text}, bridged by temporal tunnels: {', '.join(tunnel_parts)}"

    def _label(self, category: str, *, prefer_chinese: bool, history: bool) -> str:
        return self.profile.label(category, prefer_chinese=prefer_chinese, historical=history, variant="history")

    def _turn_label(self, turn_index: int, *, prefer_chinese: bool) -> str:
        if not turn_index:
            return "\u672a\u77e5\u65f6\u95f4" if prefer_chinese else "unknown turn"
        return f"\u7b2c{int(turn_index)}\u8f6e" if prefer_chinese else f"T{int(turn_index)}"

    def _temporal_role_label(self, hit: MemoryHit, *, prefer_chinese: bool) -> str:
        temporal_info = dict((hit.metadata or {}).get("overlay_time", {}) or {})
        role = str(temporal_info.get("temporal_role", "current" if hit.state == "active" else "previous"))
        if prefer_chinese:
            labels = {
                "current": "\u5f53\u524d\u65f6\u95f4\u7247",
                "previous": "\u4e0a\u4e00\u7248\u65f6\u95f4\u7247",
                "timeline_mid": "\u65f6\u95f4\u7ebf\u4e2d\u95f4\u8282\u70b9",
                "timeline_oldest": "\u65f6\u95f4\u7ebf\u65e9\u671f\u8282\u70b9",
                "neutral": "\u666e\u901a\u65f6\u95f4\u8282\u70b9",
            }
            return labels.get(role, role)
        labels = {
            "current": "current time slice",
            "previous": "previous time slice",
            "timeline_mid": "mid timeline node",
            "timeline_oldest": "early timeline node",
            "neutral": "neutral time node",
        }
        return labels.get(role, role)

    def _facts_from_hits(self, hits: Sequence[MemoryHit]) -> List[Dict[str, Any]]:
        facts: List[Dict[str, Any]] = []
        for hit in hits:
            temporal_info = dict((hit.metadata or {}).get("overlay_time", {}) or {})
            facts.append(
                {
                    "from": hit.slot_key or hit.category,
                    "to": hit.value,
                    "relation": "historical_value" if hit.state != "active" else "current_value",
                    "weight": max(0.3, min(0.98, 0.5 + float(hit.score) * 0.25)),
                    "source": "overlay_memory_hit",
                    "turn_index": int(hit.turn_index),
                    "temporal_role": temporal_info.get("temporal_role", "current" if hit.state == "active" else "previous"),
                    "overwrite_distance": int(temporal_info.get("overwrite_distance", 0) or 0),
                }
            )
        return facts

    def _best_active_by_category(self, hits: Sequence[MemoryHit]) -> List[MemoryHit]:
        grouped: Dict[str, MemoryHit] = {}
        for hit in hits:
            existing = grouped.get(hit.category)
            if existing is None or float(hit.score) > float(existing.score):
                grouped[hit.category] = hit
        order = [
            record_type
            for record_type, _position in sorted(self.profile.render_order.items(), key=lambda item: item[1])
        ]
        return [grouped[key] for key in order if key in grouped]

    def _has_active_conflict(self, hits: Sequence[MemoryHit]) -> bool:
        grouped: Dict[str, set[str]] = defaultdict(set)
        for hit in hits:
            grouped[hit.slot_key or hit.category].add(hit.value)
        return any(len(values) > 1 for values in grouped.values())

    def _paired_history_hits(self, retrieval: MemoryRetrieval) -> Tuple[MemoryHit | None, MemoryHit | None]:
        history_hit = retrieval.history_hits[0] if retrieval.history_hits else None
        if history_hit is None and retrieval.hits:
            history_hit = next((hit for hit in retrieval.hits if hit.state != "active"), None)
        active_hit = None
        if history_hit is not None:
            for hit in retrieval.active_hits or retrieval.hits:
                if hit.state == "active" and (hit.slot_key == history_hit.slot_key or hit.category == history_hit.category):
                    active_hit = hit
                    break
        if active_hit is None:
            active_hit = retrieval.active_hits[0] if retrieval.active_hits else next((hit for hit in retrieval.hits if hit.state == "active"), None)
        return history_hit, active_hit

    def _timeline_hits(self, retrieval: MemoryRetrieval) -> List[MemoryHit]:
        slot = ""
        if retrieval.history_hits:
            slot = retrieval.history_hits[0].slot_key or retrieval.history_hits[0].category
        elif retrieval.hits:
            slot = retrieval.hits[0].slot_key or retrieval.hits[0].category
        if not slot:
            return []
        sequence = [hit for hit in [*retrieval.history_hits, *retrieval.active_hits, *retrieval.hits] if (hit.slot_key or hit.category) == slot]
        deduped: Dict[str, MemoryHit] = {}
        for hit in sequence:
            deduped.setdefault(hit.memory_id, hit)
        results = list(deduped.values())
        results.sort(key=lambda hit: hit.turn_index)
        return results[:4]
