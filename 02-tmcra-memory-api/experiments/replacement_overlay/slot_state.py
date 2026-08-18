from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Sequence

from experiments.replacement.adapters.base import MemoryHit, MemoryRetrieval

from .intent import QueryIntent

if TYPE_CHECKING:
    from .judge import JudgmentDecision, JudgmentSlotDirective


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _normalize(value: object) -> str:
    return _clean_text(value).lower()


def _dedupe_strings(values: Iterable[object]) -> List[str]:
    results: List[str] = []
    seen = set()
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        key = _normalize(text)
        if key in seen:
            continue
        seen.add(key)
        results.append(text)
    return results


@dataclass(slots=True)
class ResolvedSlotRecord:
    memory_id: str
    slot_key: str
    category: str
    value: str
    relation: str = "related_to"
    anchors: List[str] = field(default_factory=list)
    score: float = 0.0
    source_kind: str = "memory"
    state: str = "active"
    turn_index: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_hit(cls, hit: MemoryHit) -> "ResolvedSlotRecord":
        return cls(
            memory_id=hit.memory_id,
            slot_key=hit.slot_key or f"{hit.category}.{_normalize(hit.value)[:32]}",
            category=hit.category,
            value=hit.value,
            relation=hit.relation,
            anchors=list(hit.anchors),
            score=float(hit.score),
            source_kind=hit.source_kind,
            state=hit.state,
            turn_index=int(hit.turn_index),
            metadata=dict(hit.metadata or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "slot_key": self.slot_key,
            "category": self.category,
            "value": self.value,
            "relation": self.relation,
            "anchors": list(self.anchors),
            "score": round(float(self.score), 6),
            "source_kind": self.source_kind,
            "state": self.state,
            "turn_index": int(self.turn_index),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ResolvedSlotView:
    slot_key: str
    category: str
    active_record: ResolvedSlotRecord | None = None
    previous_record: ResolvedSlotRecord | None = None
    historical_chain: List[ResolvedSlotRecord] = field(default_factory=list)
    suppressed_records: List[ResolvedSlotRecord] = field(default_factory=list)
    conflict_state: str = "none"
    output_mode: str = "current"
    resolution_trace: Dict[str, Any] = field(default_factory=dict)

    def active_records(self) -> List[ResolvedSlotRecord]:
        return [self.active_record] if self.active_record is not None else []

    def previous_records(self) -> List[ResolvedSlotRecord]:
        return [self.previous_record] if self.previous_record is not None else []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_key": self.slot_key,
            "category": self.category,
            "active_record": self.active_record.to_dict() if self.active_record is not None else None,
            "previous_record": self.previous_record.to_dict() if self.previous_record is not None else None,
            "historical_chain": [item.to_dict() for item in self.historical_chain],
            "suppressed_records": [item.to_dict() for item in self.suppressed_records],
            "conflict_state": self.conflict_state,
            "output_mode": self.output_mode,
            "resolution_trace": dict(self.resolution_trace),
        }


@dataclass(slots=True)
class SlotStateResolution:
    mode: str
    views: List[ResolvedSlotView] = field(default_factory=list)
    selected_slots: List[str] = field(default_factory=list)
    conflict_state: str = "none"
    resolution_trace: Dict[str, Any] = field(default_factory=dict)

    def selected_records(self) -> List[ResolvedSlotRecord]:
        records: List[ResolvedSlotRecord] = []
        for view in self.views:
            view_mode = self._effective_view_mode(view)
            if view_mode == "omit":
                continue
            if view_mode == "previous":
                previous_record = (
                    view.previous_record
                    or next((record for record in reversed(view.historical_chain) if _normalize(record.state) != "active"), None)
                    or (view.suppressed_records[-1] if view.suppressed_records else None)
                    or (view.active_record if view.active_record is not None and _normalize(view.active_record.state) != "active" else None)
                )
                if previous_record is not None:
                    records.append(previous_record)
                continue
            if view_mode == "compare":
                if view.previous_record is not None:
                    records.append(view.previous_record)
                if view.active_record is not None:
                    records.append(view.active_record)
                continue
            if view_mode == "timeline":
                records.extend(view.historical_chain)
                if view.active_record is not None and all(view.active_record.memory_id != item.memory_id for item in view.historical_chain):
                    records.append(view.active_record)
                continue
            if view.active_record is not None:
                records.append(view.active_record)
        return records

    def active_records(self) -> List[ResolvedSlotRecord]:
        return [record for view in self.views for record in view.active_records()]

    def previous_records(self) -> List[ResolvedSlotRecord]:
        return [record for view in self.views for record in view.previous_records()]

    def suppressed_records(self) -> List[ResolvedSlotRecord]:
        return [record for view in self.views for record in view.suppressed_records]

    def historical_records(self) -> List[ResolvedSlotRecord]:
        return [record for view in self.views for record in view.historical_chain]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "views": [view.to_dict() for view in self.views],
            "selected_slots": list(self.selected_slots),
            "conflict_state": self.conflict_state,
            "resolution_trace": dict(self.resolution_trace),
        }

    def _effective_view_mode(self, view: ResolvedSlotView) -> str:
        mode = _clean_text(view.output_mode or self.mode) or self.mode
        return "current" if mode == "summary" else mode


class SlotStateResolver:
    def preview(self, query: str, *, intent: QueryIntent, retrieval: MemoryRetrieval, limit: int = 8) -> SlotStateResolution:
        query_text = _normalize(query)
        groups, filtered = self._prepare_groups(query_text=query_text, intent=intent, retrieval=retrieval)
        ranked_groups = sorted((filtered or groups).items(), key=lambda item: self._group_rank(item[1]), reverse=True)
        selected_groups = ranked_groups[: max(1, int(limit))]
        default_mode = self._mode_from_intent(intent)
        views = [self._resolve_group(slot_key, hits, retrieval=retrieval, default_output_mode=self._default_view_output_mode(default_mode)) for slot_key, hits in selected_groups]
        overall_conflict = "ambiguous" if any(view.conflict_state != "none" for view in views) else "none"
        return SlotStateResolution(
            mode=default_mode,
            views=views,
            selected_slots=[slot_key for slot_key, _hits in selected_groups],
            conflict_state=overall_conflict,
            resolution_trace={
                "query": _clean_text(query),
                "intent_kind": intent.kind,
                "history_kind": intent.history_kind,
                "candidate_slots": list(groups.keys()),
                "selected_slots": [slot_key for slot_key, _hits in selected_groups],
                "preview": True,
            },
        )

    def resolve(
        self,
        query: str,
        *,
        intent: QueryIntent,
        retrieval: MemoryRetrieval,
        judge_decision: "JudgmentDecision | None" = None,
        preview: SlotStateResolution | None = None,
    ) -> SlotStateResolution:
        if judge_decision is not None and judge_decision.decision_valid:
            resolved = self._resolve_with_judge(query, intent=intent, retrieval=retrieval, judge_decision=judge_decision, preview=preview)
            if resolved is not None:
                return resolved
        query_text = _normalize(query)
        groups, filtered = self._prepare_groups(query_text=query_text, intent=intent, retrieval=retrieval)
        selected_groups = self._select_groups(filtered or groups, intent=intent, query_text=query_text)
        default_mode = self._mode_from_intent(intent)
        views = [
            self._resolve_group(
                slot_key,
                hits,
                retrieval=retrieval,
                default_output_mode=self._default_view_output_mode(default_mode),
            )
            for slot_key, hits in selected_groups
        ]
        overall_conflict = "ambiguous" if any(view.conflict_state != "none" for view in views) else "none"
        return SlotStateResolution(
            mode=default_mode,
            views=views,
            selected_slots=[slot_key for slot_key, _hits in selected_groups],
            conflict_state=overall_conflict,
            resolution_trace={
                "query": _clean_text(query),
                "intent_kind": intent.kind,
                "history_kind": intent.history_kind,
                "candidate_slots": list(groups.keys()),
                "selected_slots_before": [slot_key for slot_key, _hits in selected_groups],
                "selected_slots": [slot_key for slot_key, _hits in selected_groups],
                "selected_slots_after": [slot_key for slot_key, _hits in selected_groups],
                "baseline_selected_slots": [slot_key for slot_key, _hits in selected_groups],
                "judge_selected_slots": [],
                "selection_source": "baseline",
                "judge_applied": False,
                "judge_effective": False,
                "coverage_budget": len(selected_groups),
                "required_categories": [],
                "dropped_slots": [],
                "drop_reasons": {},
                "coverage_preserved": True,
                "coverage_changed": False,
                "coverage_improved": False,
                "realized_claim_slots": [],
                "realized_view_set": [],
                "semantic_history_mode": default_mode,
                "coverage_drop_reason": {},
                "judge_trace_not_realized": [],
                "judge_semantic_not_realized": False,
            },
        )

    def _prepare_groups(self, *, query_text: str, intent: QueryIntent, retrieval: MemoryRetrieval) -> tuple[Dict[str, List[MemoryHit]], Dict[str, List[MemoryHit]]]:
        groups = self._group_hits(retrieval)
        filtered = self._filter_groups(groups, query_text=query_text, intent=intent)
        if not filtered:
            filtered = groups
        return groups, filtered

    def _resolve_with_judge(
        self,
        query: str,
        *,
        intent: QueryIntent,
        retrieval: MemoryRetrieval,
        judge_decision: "JudgmentDecision",
        preview: SlotStateResolution | None,
    ) -> SlotStateResolution | None:
        candidate_preview = preview or self.preview(query, intent=intent, retrieval=retrieval, limit=8)
        if not candidate_preview.views:
            return None
        candidate_map = {view.slot_key: view for view in candidate_preview.views}
        directives = list(judge_decision.slot_directives or [])
        selected_slots = [slot for slot in judge_decision.selected_slot_keys if slot]
        judge_views: List[ResolvedSlotView] = []
        trace_directives: List[Dict[str, Any]] = []

        if directives:
            for directive in directives:
                view = candidate_map.get(directive.slot_key)
                if view is None:
                    continue
                judge_views.append(view)
                trace_directives.append(directive.to_dict())
            if not selected_slots:
                selected_slots = [directive.slot_key for directive in directives if directive.mode != "omit" and directive.slot_key in candidate_map]

        if not judge_views and selected_slots:
            judge_views = [candidate_map[slot] for slot in selected_slots if slot in candidate_map]
        merged_selection = self._merge_judge_views(
            intent=intent,
            candidate_preview=candidate_preview,
            candidate_map=candidate_map,
            judge_views=judge_views,
            judge_selected_slots=selected_slots,
            judge_decision=judge_decision,
        )
        ordered_views = list(merged_selection["views"])
        if not ordered_views:
            ordered_views = list(candidate_preview.views[: max(1, min(4, len(candidate_preview.views)))])
        if not selected_slots:
            selected_slots = [view.slot_key for view in ordered_views if view.slot_key]

        default_mode = self._mode_from_intent(intent)
        effective_mode = self._judge_resolution_mode(intent=intent, judge_decision=judge_decision, default_mode=default_mode)
        fallback_mode = self._default_view_output_mode(default_mode)
        views = [
            self._apply_judge_to_view(
                view,
                judge_decision=judge_decision,
                directive=judge_decision.directive_for_slot(view.slot_key),
                fallback_mode=fallback_mode,
            )
            for view in ordered_views
        ]
        selected_output_slots = [view.slot_key for view in views if view.output_mode != "omit"]
        if not selected_output_slots:
            selected_output_slots = [view.slot_key for view in views]
        overall_conflict = judge_decision.conflict_state or ("ambiguous" if any(view.conflict_state != "none" for view in views) else "none")
        judge_effective = self._judge_effective(default_mode=fallback_mode, selected_slots=candidate_preview.selected_slots, selected_views=views)
        coverage_changed = bool(merged_selection["coverage_changed"]) or list(selected_output_slots) != list(candidate_preview.selected_slots)
        coverage_preserved = bool(merged_selection["coverage_preserved"])
        coverage_improved = bool(merged_selection["coverage_improved"]) or len(selected_output_slots) > len(candidate_preview.selected_slots)
        return SlotStateResolution(
            mode=effective_mode,
            views=views,
            selected_slots=selected_output_slots,
            conflict_state=overall_conflict if overall_conflict != "none" else ("ambiguous" if any(view.conflict_state != "none" for view in views) else "none"),
            resolution_trace={
                "query": _clean_text(query),
                "intent_kind": intent.kind,
                "history_kind": intent.history_kind,
                "selection_source": merged_selection["selection_source"],
                "baseline_selected_slots": list(merged_selection["baseline_selected_slots"]),
                "judge_selected_slots": list(merged_selection["judge_selected_slots"]),
                "selected_slots_before": list(candidate_preview.selected_slots),
                "selected_slots": list(selected_output_slots),
                "selected_slots_after": list(selected_output_slots),
                "judge_applied": True,
                "judge_effective": bool(judge_effective),
                "coverage_budget": int(merged_selection["coverage_budget"]),
                "required_categories": list(merged_selection["required_categories"]),
                "dropped_slots": list(merged_selection["dropped_slots"]),
                "drop_reasons": dict(merged_selection["drop_reasons"]),
                "coverage_preserved": coverage_preserved,
                "coverage_changed": coverage_changed,
                "coverage_improved": coverage_improved,
                "realized_claim_slots": [],
                "realized_view_set": [],
                "semantic_history_mode": effective_mode,
                "coverage_drop_reason": dict(merged_selection["drop_reasons"]),
                "judge_trace_not_realized": [],
                "judge_semantic_not_realized": False,
                "judge_decision": judge_decision.to_dict(),
                "slot_directives": trace_directives,
                "selected_path_indices": list(judge_decision.selected_path_indices),
                "judge_degraded": False,
            },
        )

    def _apply_judge_to_view(
        self,
        view: ResolvedSlotView,
        *,
        judge_decision: "JudgmentDecision",
        directive: "JudgmentSlotDirective | None",
        fallback_mode: str,
    ) -> ResolvedSlotView:
        records_by_id = self._records_by_id(view)
        mode = _clean_text(directive.mode if directive is not None else "") or fallback_mode
        if mode == "summary":
            mode = fallback_mode
        selected_ids = [memory_id for memory_id in (directive.selected_memory_ids if directive is not None else judge_decision.selected_memory_ids) if memory_id in records_by_id]
        timeline_ids = [memory_id for memory_id in (directive.timeline_memory_ids if directive is not None else judge_decision.timeline_memory_ids) if memory_id in records_by_id]
        compare_pair = directive.compare_pair if directive is not None and directive.compare_pair else {}
        active_record = view.active_record
        previous_record = view.previous_record
        historical_chain = list(view.historical_chain)
        conflict_state = judge_decision.conflict_state if judge_decision.conflict_state not in {"", "none"} else view.conflict_state

        if mode == "omit":
            return ResolvedSlotView(
                slot_key=view.slot_key,
                category=view.category,
                active_record=active_record,
                previous_record=previous_record,
                historical_chain=historical_chain,
                suppressed_records=list(view.suppressed_records),
                conflict_state=conflict_state,
                output_mode="omit",
                resolution_trace={**dict(view.resolution_trace), "judge_selected_memory_ids": list(selected_ids), "judge_mode": mode},
            )

        if conflict_state not in {"", "none"}:
            return ResolvedSlotView(
                slot_key=view.slot_key,
                category=view.category,
                active_record=None,
                previous_record=view.previous_record,
                historical_chain=historical_chain,
                suppressed_records=list(view.suppressed_records),
                conflict_state=conflict_state,
                output_mode=mode,
                resolution_trace={**dict(view.resolution_trace), "judge_selected_memory_ids": list(selected_ids), "judge_mode": mode},
            )

        if mode == "current":
            if selected_ids:
                active_record = records_by_id.get(selected_ids[0], active_record)
        elif mode == "previous":
            if selected_ids:
                previous_record = records_by_id.get(selected_ids[0], previous_record)
        elif mode == "compare":
            pair = compare_pair if _clean_text(compare_pair.get("current_memory_id", "")) or _clean_text(compare_pair.get("previous_memory_id", "")) else next(
                (item for item in judge_decision.compare_pairs if _clean_text(item.get("slot_key", "")) == view.slot_key),
                None,
            )
            if pair is not None:
                active_record = records_by_id.get(_clean_text(pair.get("current_memory_id", "")), active_record)
                previous_record = records_by_id.get(_clean_text(pair.get("previous_memory_id", "")), previous_record)
            elif len(selected_ids) >= 2:
                ordered = sorted((records_by_id[memory_id] for memory_id in selected_ids if memory_id in records_by_id), key=lambda item: (item.turn_index, item.score))
                if ordered:
                    previous_record = ordered[0]
                    active_record = ordered[-1]
        elif mode == "timeline":
            candidate_ids = timeline_ids or selected_ids
            if candidate_ids:
                historical_chain = [records_by_id[memory_id] for memory_id in candidate_ids if memory_id in records_by_id]
                historical_chain.sort(key=lambda item: (item.turn_index, item.score))
                if historical_chain:
                    active_record = historical_chain[-1]
                    previous_record = historical_chain[-2] if len(historical_chain) > 1 else previous_record

        return ResolvedSlotView(
            slot_key=view.slot_key,
            category=view.category,
            active_record=active_record,
            previous_record=previous_record,
            historical_chain=historical_chain,
            suppressed_records=list(view.suppressed_records),
            conflict_state=conflict_state,
            output_mode=mode,
            resolution_trace={**dict(view.resolution_trace), "judge_selected_memory_ids": list(selected_ids), "judge_mode": mode},
        )

    def _merge_judge_views(
        self,
        *,
        intent: QueryIntent,
        candidate_preview: SlotStateResolution,
        candidate_map: Dict[str, ResolvedSlotView],
        judge_views: Sequence[ResolvedSlotView],
        judge_selected_slots: Sequence[str],
        judge_decision: "JudgmentDecision",
    ) -> Dict[str, Any]:
        baseline_selected_slots = list(candidate_preview.selected_slots or [view.slot_key for view in candidate_preview.views if view.slot_key])
        judge_selected = list(judge_selected_slots or [view.slot_key for view in judge_views if view.slot_key])
        required_categories = [
            _clean_text(item)
            for item in (judge_decision.required_categories or [candidate_map[slot].category for slot in baseline_selected_slots if slot in candidate_map])
            if _clean_text(item)
        ]
        preserve_baseline = bool(intent.kind == "summary" or intent.history_kind in {"previous", "compare", "timeline"})
        must_keep_slots = [
            slot
            for slot in (judge_decision.must_keep_slot_keys or (baseline_selected_slots if preserve_baseline else baseline_selected_slots[:1]))
            if _clean_text(slot)
        ]
        coverage_budget = int(judge_decision.coverage_budget or max(len(judge_selected), len(must_keep_slots), len(required_categories), 1))
        if preserve_baseline:
            coverage_budget = max(coverage_budget, len(baseline_selected_slots))
        coverage_budget = min(max(1, coverage_budget), max(1, len(candidate_preview.views)))
        merged_views: List[ResolvedSlotView] = []
        selected_keys = set()
        drop_reasons: Dict[str, str] = {}

        def add_view(view: ResolvedSlotView, *, reason: str = "") -> None:
            slot_key = _clean_text(view.slot_key)
            if not slot_key:
                return
            normalized = _normalize(slot_key)
            if normalized in selected_keys:
                return
            if len(merged_views) >= coverage_budget:
                if reason:
                    drop_reasons[slot_key] = reason
                return
            merged_views.append(view)
            selected_keys.add(normalized)

        for view in judge_views:
            add_view(view)
        for slot in must_keep_slots:
            view = candidate_map.get(slot)
            if view is None:
                drop_reasons[_clean_text(slot)] = "missing_candidate"
                continue
            add_view(view, reason="budget_limit")
        selected_categories = {_normalize(view.category) for view in merged_views if _clean_text(view.category)}
        for category in required_categories:
            normalized_category = _normalize(category)
            if normalized_category in selected_categories:
                continue
            view = next((item for item in candidate_preview.views if _normalize(item.category) == normalized_category and _normalize(item.slot_key) not in selected_keys), None)
            if view is not None:
                add_view(view, reason="budget_limit")
                selected_categories.add(normalized_category)
        if preserve_baseline:
            for slot in baseline_selected_slots:
                view = candidate_map.get(slot)
                if view is None:
                    drop_reasons[_clean_text(slot)] = "missing_candidate"
                    continue
                add_view(view, reason="budget_limit")
        if not merged_views:
            for view in candidate_preview.views[:coverage_budget]:
                add_view(view)
        dropped_slots = [slot for slot in baseline_selected_slots if _normalize(slot) not in selected_keys]
        for slot in dropped_slots:
            drop_reasons.setdefault(slot, "not_selected")
        judge_category_count = len({_normalize(candidate_map[slot].category) for slot in judge_selected if slot in candidate_map})
        merged_category_count = len({_normalize(view.category) for view in merged_views if _clean_text(view.category)})
        return {
            "views": merged_views,
            "baseline_selected_slots": baseline_selected_slots,
            "judge_selected_slots": judge_selected,
            "required_categories": required_categories,
            "coverage_budget": coverage_budget,
            "dropped_slots": dropped_slots,
            "drop_reasons": drop_reasons,
            "coverage_preserved": not dropped_slots,
            "coverage_changed": judge_selected != [view.slot_key for view in merged_views],
            "coverage_improved": merged_category_count > judge_category_count or len(merged_views) > len(judge_selected),
            "selection_source": "judge_merged" if judge_selected != [view.slot_key for view in merged_views] else ("judge_direct" if judge_selected else "preview_fallback"),
        }

    def _judge_resolution_mode(self, *, intent: QueryIntent, judge_decision: "JudgmentDecision", default_mode: str) -> str:
        if judge_decision.slot_directives:
            output_modes = [_clean_text(item.mode) for item in judge_decision.slot_directives if _clean_text(item.mode) and _clean_text(item.mode) != "omit"]
            if len(set(output_modes)) > 1:
                return "summary" if intent.kind == "summary" else (_clean_text(judge_decision.slot_mode) or default_mode)
            if output_modes:
                return output_modes[0] if output_modes[0] != "current" or intent.kind != "summary" else "summary"
        if intent.kind == "summary":
            return "summary"
        return _clean_text(judge_decision.slot_mode or judge_decision.history_kind) or default_mode

    def _judge_effective(self, *, default_mode: str, selected_slots: Sequence[str], selected_views: Sequence[ResolvedSlotView]) -> bool:
        if any(bool(dict(view.resolution_trace or {}).get("coverage_changed", False)) for view in selected_views):
            return True
        selected_slot_list = list(selected_slots or [])
        resolved_slot_list = [view.slot_key for view in selected_views if view.output_mode != "omit"]
        if resolved_slot_list and resolved_slot_list != selected_slot_list[: len(resolved_slot_list)]:
            return True
        for view in selected_views:
            if view.output_mode != default_mode:
                return True
        return False

    def _records_by_id(self, view: ResolvedSlotView) -> Dict[str, ResolvedSlotRecord]:
        records: Dict[str, ResolvedSlotRecord] = {}
        for record in [view.active_record, view.previous_record, *list(view.historical_chain), *list(view.suppressed_records)]:
            if record is None:
                continue
            records[record.memory_id] = record
        return records

    def _group_hits(self, retrieval: MemoryRetrieval) -> Dict[str, List[MemoryHit]]:
        grouped: Dict[str, List[MemoryHit]] = defaultdict(list)
        by_id: Dict[str, MemoryHit] = {}
        for hit in [*list(retrieval.hits), *list(retrieval.active_hits), *list(retrieval.history_hits), *list(retrieval.overwrite_hits), *list(retrieval.stale_hits), *list(retrieval.false_hits)]:
            key = hit.memory_id or f"{hit.slot_key}:{hit.turn_index}:{hit.value}"
            if key in by_id:
                existing = by_id[key]
                if float(hit.score) > float(existing.score):
                    by_id[key] = hit
                continue
            by_id[key] = hit
        for hit in by_id.values():
            slot_key = hit.slot_key or f"{hit.category}.{_normalize(hit.value)[:32]}"
            grouped[slot_key].append(hit)
        return grouped

    def _filter_groups(self, groups: Dict[str, List[MemoryHit]], *, query_text: str, intent: QueryIntent) -> Dict[str, List[MemoryHit]]:
        if not groups:
            return {}
        category_hints = {_normalize(item) for item in intent.category_hints}
        entity_hints = {_normalize(item) for item in intent.entity_hints}
        filtered: Dict[str, List[MemoryHit]] = {}
        for slot_key, hits in groups.items():
            category = _normalize(hits[0].category if hits else "")
            slot_match = _normalize(slot_key)
            anchors = {_normalize(anchor) for hit in hits for anchor in hit.anchors}
            values = {_normalize(hit.value) for hit in hits}
            category_ok = not category_hints or category in category_hints or intent.kind == "summary"
            entity_ok = not entity_hints or bool(entity_hints & anchors) or bool(entity_hints & values) or any(entity in slot_match for entity in entity_hints)
            query_ok = any(token in query_text for token in [slot_match, category]) or category_ok
            if category_ok and entity_ok and query_ok:
                filtered[slot_key] = hits
        return filtered

    def _select_groups(self, groups: Dict[str, List[MemoryHit]], *, intent: QueryIntent, query_text: str) -> List[tuple[str, List[MemoryHit]]]:
        ranked = sorted(groups.items(), key=lambda item: self._group_rank(item[1]), reverse=True)
        requested_categories = [_normalize(item) for item in intent.category_hints if _normalize(item)]
        if intent.kind == "summary":
            return self._choose_groups(ranked, requested_categories=requested_categories, limit=min(max(4, len(requested_categories) + 1), 6), one_per_category_first=True)
        if intent.history_kind == "timeline":
            return self._choose_groups(ranked, requested_categories=requested_categories, limit=min(max(4, len(requested_categories) or 2), 6), one_per_category_first=bool(requested_categories))
        if intent.history_kind == "compare":
            return self._choose_groups(ranked, requested_categories=requested_categories, limit=min(max(2, len(requested_categories) or 1), 4), one_per_category_first=bool(requested_categories))
        plural_query = any(marker in query_text for marker in (" and ", "分别", "、", "和", "constraints", "goals", "preferences", "terms", "stages"))
        limit = 1
        if len(requested_categories) > 1 or plural_query:
            limit = min(max(len(requested_categories) or 2, 2), 4)
        elif requested_categories == ["constraint"] and any(marker in query_text for marker in ("constraint", "constraints", "约束")):
            limit = min(3, len(ranked))
        return self._choose_groups(ranked, requested_categories=requested_categories, limit=limit, one_per_category_first=len(requested_categories) > 1)

    def _choose_groups(
        self,
        ranked: Sequence[tuple[str, List[MemoryHit]]],
        *,
        requested_categories: Sequence[str],
        limit: int,
        one_per_category_first: bool,
    ) -> List[tuple[str, List[MemoryHit]]]:
        if not ranked:
            return []
        chosen: List[tuple[str, List[MemoryHit]]] = []
        seen_slots = set()
        seen_categories = set()
        category_pool = list(requested_categories)
        if category_pool:
            for category in category_pool:
                match = next(
                    ((slot_key, hits) for slot_key, hits in ranked if slot_key not in seen_slots and _normalize(hits[0].category if hits else "") == category),
                    None,
                )
                if match is None:
                    continue
                slot_key, hits = match
                chosen.append(match)
                seen_slots.add(slot_key)
                seen_categories.add(_normalize(hits[0].category if hits else ""))
                if len(chosen) >= limit:
                    return chosen
        for slot_key, hits in ranked:
            if slot_key in seen_slots:
                continue
            category = _normalize(hits[0].category if hits else "")
            if one_per_category_first and category in seen_categories and len(chosen) < max(1, len(category_pool)):
                continue
            chosen.append((slot_key, hits))
            seen_slots.add(slot_key)
            seen_categories.add(category)
            if len(chosen) >= limit:
                break
        return chosen

    def _group_rank(self, hits: Sequence[MemoryHit]) -> tuple[float, int, int]:
        best_score = max((float(hit.score) for hit in hits), default=0.0)
        latest_turn = max((int(hit.turn_index) for hit in hits), default=0)
        active_count = sum(1 for hit in hits if hit.state == "active")
        return (best_score + active_count * 0.05, latest_turn, len(hits))

    def _resolve_group(self, slot_key: str, hits: Sequence[MemoryHit], *, retrieval: MemoryRetrieval, default_output_mode: str) -> ResolvedSlotView:
        category = hits[0].category if hits else "memory"
        ordered = sorted(hits, key=lambda hit: (0 if hit.state == "active" else 1, -int(hit.turn_index or 0), -float(hit.score or 0.0)))
        active_candidates = [hit for hit in ordered if hit.state == "active"]
        active_values = _dedupe_strings(hit.value for hit in active_candidates)
        active_record: ResolvedSlotRecord | None = None
        conflict_state = "none"
        if len(active_values) > 1:
            strongest = sorted(active_candidates, key=lambda hit: (-float(hit.score), -int(hit.turn_index or 0)))
            if len(strongest) >= 2 and abs(float(strongest[0].score) - float(strongest[1].score)) <= 0.12:
                conflict_state = "ambiguous_active"
            else:
                active_record = ResolvedSlotRecord.from_hit(strongest[0])
        elif active_candidates:
            active_record = ResolvedSlotRecord.from_hit(active_candidates[0])
        elif ordered:
            active_record = ResolvedSlotRecord.from_hit(ordered[0])

        chain_hits = self._build_chain(slot_key, hits, retrieval=retrieval, active_record=active_record)
        previous_record: ResolvedSlotRecord | None = None
        for item in reversed(chain_hits[:-1] if active_record is not None else chain_hits):
            if active_record is None or item.memory_id != active_record.memory_id:
                previous_record = item
                break

        suppressed_lookup: Dict[str, ResolvedSlotRecord] = {}
        selected_ids = {item.memory_id for item in chain_hits}
        if active_record is not None:
            selected_ids.add(active_record.memory_id)
        for source in [*list(retrieval.overwrite_hits), *list(retrieval.stale_hits), *list(retrieval.false_hits), *ordered]:
            if (source.slot_key or slot_key) != slot_key:
                continue
            if source.memory_id in selected_ids:
                continue
            suppressed_lookup[source.memory_id] = ResolvedSlotRecord.from_hit(source)

        return ResolvedSlotView(
            slot_key=slot_key,
            category=category,
            active_record=active_record if conflict_state == "none" else None,
            previous_record=previous_record,
            historical_chain=chain_hits,
            suppressed_records=sorted(suppressed_lookup.values(), key=lambda item: (item.turn_index, item.score)),
            conflict_state=conflict_state,
            output_mode=default_output_mode,
            resolution_trace={
                "slot_key": slot_key,
                "candidate_count": len(hits),
                "active_candidates": len(active_candidates),
                "selected_active_memory_id": active_record.memory_id if active_record is not None else "",
                "selected_previous_memory_id": previous_record.memory_id if previous_record is not None else "",
            },
        )

    def _build_chain(
        self,
        slot_key: str,
        hits: Sequence[MemoryHit],
        *,
        retrieval: MemoryRetrieval,
        active_record: ResolvedSlotRecord | None,
    ) -> List[ResolvedSlotRecord]:
        chain_sources = [hit for hit in [*list(hits), *list(retrieval.history_hits), *list(retrieval.overwrite_hits), *list(retrieval.stale_hits)] if (hit.slot_key or slot_key) == slot_key]
        deduped: Dict[str, ResolvedSlotRecord] = {}
        for hit in sorted(chain_sources, key=lambda item: (int(item.turn_index or 0), float(item.score or 0.0))):
            key = _normalize(hit.value)
            if key in deduped:
                if int(hit.turn_index or 0) >= deduped[key].turn_index:
                    deduped[key] = ResolvedSlotRecord.from_hit(hit)
                continue
            deduped[key] = ResolvedSlotRecord.from_hit(hit)
        records_by_id = {record.memory_id: record for record in deduped.values()}
        chain = sorted(deduped.values(), key=lambda item: (item.turn_index, item.score))
        if active_record is not None:
            backward_ids = list((active_record.metadata or {}).get("supersedes", []) or [])
            explicit_chain: List[ResolvedSlotRecord] = []
            seen = {active_record.memory_id}
            while backward_ids:
                previous_id = str(backward_ids[0])
                if not previous_id or previous_id in seen:
                    break
                seen.add(previous_id)
                previous = records_by_id.get(previous_id)
                if previous is None:
                    break
                explicit_chain.append(previous)
                backward_ids = list((previous.metadata or {}).get("supersedes", []) or [])
            if explicit_chain:
                explicit_ids = {item.memory_id for item in explicit_chain}
                chain = sorted([*explicit_chain, *[record for record in chain if record.memory_id not in explicit_ids]], key=lambda item: (item.turn_index, item.score))
        if active_record is not None and all(active_record.memory_id != item.memory_id for item in chain):
            chain.append(active_record)
            chain.sort(key=lambda item: (item.turn_index, item.score))
        return chain

    def _mode_from_intent(self, intent: QueryIntent) -> str:
        if intent.history_kind in {"current", "previous", "compare", "timeline"}:
            return intent.history_kind
        if intent.kind == "summary":
            return "summary"
        return "current"

    def _default_view_output_mode(self, mode: str) -> str:
        return "current" if mode == "summary" else mode
