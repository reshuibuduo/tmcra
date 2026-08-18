from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, Iterable, List, Sequence

from experiments.replacement.memory_profiles import TMCRAProfile

from .intent import QueryIntent
from .pathing import PathCandidate
from .slot_state import ResolvedSlotRecord, ResolvedSlotView, SlotStateResolution
from .temporal_reasoning import TemporalReasoningTrace


_CATEGORY_LABELS = {
    "goal": ("current goal", "previous goal", "当前目标", "之前目标"),
    "constraint": ("current constraint", "previous constraint", "当前约束", "之前约束"),
    "preference": ("current preference", "previous preference", "当前偏好", "之前偏好"),
    "terminology": ("current terminology", "previous terminology", "当前术语", "之前术语"),
    "stage_state": ("current stage", "previous stage", "当前阶段", "之前阶段"),
}

_CATEGORY_RENDER_ORDER = {
    "goal": 0,
    "constraint": 1,
    "stage_state": 2,
    "preference": 3,
    "terminology": 4,
}


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _normalize(value: object) -> str:
    return _clean_text(value).lower()


def _contains_cjk(value: object) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in _clean_text(value))


def _contains_marker(text: object, marker: object) -> bool:
    source = _clean_text(text)
    needle = _clean_text(marker)
    if not source or not needle:
        return False
    if _contains_cjk(needle):
        return needle in source
    pattern = re.escape(_normalize(needle)).replace(r"\ ", r"\s+")
    return bool(re.search(rf"(?<![a-z0-9_]){pattern}(?![a-z0-9_])", _normalize(source), flags=re.IGNORECASE))


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
class ClaimUnit:
    claim_type: str
    text: str
    memory_ids: List[str] = field(default_factory=list)
    fact_refs: List[str] = field(default_factory=list)
    path_refs: List[str] = field(default_factory=list)
    time_scope: str = ""
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_type": self.claim_type,
            "text": self.text,
            "memory_ids": list(self.memory_ids),
            "fact_refs": list(self.fact_refs),
            "path_refs": list(self.path_refs),
            "time_scope": self.time_scope,
            "confidence": round(float(self.confidence), 6),
        }


@dataclass(slots=True)
class ReasoningTraceBundle:
    intent_kind: str
    resolution_mode: str
    claim_count: int
    path_mode: str = "none"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_kind": self.intent_kind,
            "resolution_mode": self.resolution_mode,
            "claim_count": int(self.claim_count),
            "path_mode": self.path_mode,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class AnswerPlan:
    summary: str
    claims: List[ClaimUnit] = field(default_factory=list)
    facts: List[Dict[str, Any]] = field(default_factory=list)
    paths: List[Dict[str, Any]] = field(default_factory=list)
    candidate_scores: List[Dict[str, Any]] = field(default_factory=list)
    selected_memory_ids: List[str] = field(default_factory=list)
    suppressed_memory_ids: List[str] = field(default_factory=list)
    unsupported_claims: List[str] = field(default_factory=list)
    conflict_state: str = "none"
    resolution_mode: str = "current"
    evidence_mode: str = "slot_current"
    confidence: float = 0.0
    trace_bundle: ReasoningTraceBundle | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "claims": [item.to_dict() for item in self.claims],
            "facts": list(self.facts),
            "paths": list(self.paths),
            "candidate_scores": list(self.candidate_scores),
            "selected_memory_ids": list(self.selected_memory_ids),
            "suppressed_memory_ids": list(self.suppressed_memory_ids),
            "unsupported_claims": list(self.unsupported_claims),
            "conflict_state": self.conflict_state,
            "resolution_mode": self.resolution_mode,
            "evidence_mode": self.evidence_mode,
            "confidence": round(float(self.confidence), 6),
            "trace_bundle": self.trace_bundle.to_dict() if self.trace_bundle is not None else None,
        }


class AnswerPlanner:
    def __init__(self, *, profile: TMCRAProfile | None = None) -> None:
        self.profile = profile or TMCRAProfile()

    def plan(
        self,
        query: str,
        *,
        intent: QueryIntent,
        resolution: SlotStateResolution,
        temporal_trace: TemporalReasoningTrace,
        path_candidates: Sequence[PathCandidate],
        path_output_mode: str = "",
    ) -> AnswerPlan:
        prefer_chinese = _contains_cjk(query)
        if intent.kind == "path":
            return self._path_plan(query=query, intent=intent, path_candidates=path_candidates, prefer_chinese=prefer_chinese, path_output_mode=path_output_mode)

        claims: List[ClaimUnit] = []
        facts: List[Dict[str, Any]] = []
        selected_memory_ids: List[str] = []
        realized_claim_slots: List[str] = []
        realized_view_set: List[Dict[str, Any]] = []
        view_modes: Dict[str, str] = {}
        for view in self._ordered_views(query, intent=intent, resolution=resolution):
            output_mode = _clean_text(view.output_mode or resolution.mode) or resolution.mode
            if output_mode == "summary":
                output_mode = "current"
            view_modes[view.slot_key] = output_mode
            if output_mode == "omit":
                continue
            if view.conflict_state != "none":
                claims.append(self._conflict_claim(view, prefer_chinese=prefer_chinese))
                selected_memory_ids.extend(item.memory_id for item in view.historical_chain[:2])
                realized_claim_slots.append(view.slot_key)
                realized_view_set.append(
                    {
                        "slot_key": view.slot_key,
                        "category": view.category,
                        "output_mode": output_mode,
                        "claim_type": "conflict_notice",
                    }
                )
                continue
            if output_mode == "timeline":
                claim = self._timeline_claim(view, prefer_chinese=prefer_chinese)
                if claim is not None:
                    claims.append(claim)
                    timeline_records = view.historical_chain or view.active_records()
                    facts.extend(self._facts_from_records(timeline_records))
                    selected_memory_ids.extend(record.memory_id for record in timeline_records)
                    realized_claim_slots.append(view.slot_key)
                    realized_view_set.append(
                        {
                            "slot_key": view.slot_key,
                            "category": view.category,
                            "output_mode": output_mode,
                            "claim_type": claim.claim_type,
                        }
                    )
                continue
            if output_mode == "compare":
                claim = self._compare_claim(query, view, prefer_chinese=prefer_chinese)
                if claim is not None:
                    claims.append(claim)
                    compare_records = [record for record in [view.previous_record, view.active_record] if record is not None]
                    facts.extend(self._facts_from_records(compare_records))
                    selected_memory_ids.extend(record.memory_id for record in compare_records)
                    realized_claim_slots.append(view.slot_key)
                    realized_view_set.append(
                        {
                            "slot_key": view.slot_key,
                            "category": view.category,
                            "output_mode": output_mode,
                            "claim_type": claim.claim_type,
                        }
                    )
                continue
            record = self._record_for_mode(view, output_mode=output_mode)
            claim = self._single_claim(query, view, record=record, prefer_chinese=prefer_chinese, previous=output_mode == "previous")
            if claim is not None:
                claims.append(claim)
                if record is not None:
                    facts.extend(self._facts_from_records([record]))
                    selected_memory_ids.append(record.memory_id)
                realized_claim_slots.append(view.slot_key)
                realized_view_set.append(
                    {
                        "slot_key": view.slot_key,
                        "category": view.category,
                        "output_mode": output_mode,
                        "claim_type": claim.claim_type,
                    }
                )

        if not claims:
            claims.append(self._missing_claim(intent=intent, prefer_chinese=prefer_chinese))

        unsupported_claims = [claim.text for claim in claims if self._claim_requires_evidence(claim) and not (claim.memory_ids or claim.path_refs or claim.fact_refs)]
        supported_claims = [claim.text for claim in claims if claim.text and claim.text not in unsupported_claims]
        summary = (("；" if prefer_chinese else "; ").join(supported_claims)).strip()
        if summary and prefer_chinese and not summary.endswith(("。", "！", "？")):
            summary += "。"
        elif summary and not prefer_chinese and not summary.endswith((".", "!", "?")):
            summary += "."

        confidence = min(
            0.98,
            max(
                0.18,
                0.35
                + len([claim for claim in claims if claim.memory_ids]) * 0.1
                + (0.1 if temporal_trace.mode in {"compare", "timeline"} else 0.0)
                - (0.15 if unsupported_claims else 0.0),
            ),
        )
        realized_claim_slots = _dedupe_strings(realized_claim_slots)
        resolution.resolution_trace["realized_claim_slots"] = list(realized_claim_slots)
        resolution.resolution_trace["realized_view_set"] = list(realized_view_set)
        resolution.resolution_trace["semantic_history_mode"] = self._semantic_history_mode(
            intent=intent,
            resolution=resolution,
            view_modes=view_modes,
        )
        resolution.resolution_trace["judge_trace_not_realized"] = [
            slot_key
            for slot_key in list(resolution.resolution_trace.get("selected_slots_after", []) or resolution.selected_slots)
            if slot_key not in set(realized_claim_slots)
        ]
        return AnswerPlan(
            summary=summary,
            claims=claims,
            facts=self._dedupe_facts(facts),
            paths=[],
            candidate_scores=[],
            selected_memory_ids=_dedupe_strings(selected_memory_ids),
            suppressed_memory_ids=_dedupe_strings(record.memory_id for record in resolution.suppressed_records()),
            unsupported_claims=unsupported_claims,
            conflict_state=resolution.conflict_state,
            resolution_mode=resolution.mode,
            evidence_mode=f"slot_{resolution.mode}",
            confidence=confidence,
            trace_bundle=ReasoningTraceBundle(
                intent_kind=intent.kind,
                resolution_mode=resolution.mode,
                claim_count=len(claims),
                path_mode=intent.path_mode,
                metadata={
                    "selected_slots": list(resolution.selected_slots),
                    "view_modes": view_modes,
                    "realized_claim_slots": list(realized_claim_slots),
                    "realized_view_set": list(realized_view_set),
                    "semantic_history_mode": resolution.resolution_trace.get("semantic_history_mode", resolution.mode),
                    "claim_types": [claim.claim_type for claim in claims],
                },
            ),
        )

    def _ordered_views(self, query: str, *, intent: QueryIntent, resolution: SlotStateResolution) -> List[ResolvedSlotView]:
        if len(resolution.views) <= 1:
            return list(resolution.views)
        requested = self._requested_category_order(query, intent=intent)
        if not requested and intent.kind != "summary":
            return list(resolution.views)
        requested_rank = {category: index for index, category in enumerate(requested)}

        def rank(view: ResolvedSlotView) -> tuple[int, int, str]:
            category = _normalize(view.category)
            if category in requested_rank:
                return (0, requested_rank[category], _normalize(view.slot_key))
            return (1, self.profile.render_position(category), _normalize(view.slot_key))

        return sorted(resolution.views, key=rank)

    def _requested_category_order(self, query: str, *, intent: QueryIntent) -> List[str]:
        return self.profile.requested_category_order(query, intent.category_hints)

    def _semantic_history_mode(self, *, intent: QueryIntent, resolution: SlotStateResolution, view_modes: Dict[str, str]) -> str:
        modes = [_clean_text(mode) for mode in view_modes.values() if _clean_text(mode) and _clean_text(mode) != "omit"]
        if not modes:
            return resolution.mode
        normalized_modes = {_normalize(mode) for mode in modes}
        if "timeline" in normalized_modes:
            return "timeline"
        if "compare" in normalized_modes or ({"current", "previous"} <= normalized_modes):
            return "compare"
        if len(normalized_modes) == 1:
            return modes[0]
        if intent.kind == "summary" or resolution.mode == "summary":
            return "summary"
        return modes[0]

    def _path_plan(self, query: str, *, intent: QueryIntent, path_candidates: Sequence[PathCandidate], prefer_chinese: bool, path_output_mode: str = "") -> AnswerPlan:
        effective_path_mode = _clean_text(path_output_mode) or intent.path_mode
        if not path_candidates:
            missing_text = "没有找到有依据的路径。"
            if effective_path_mode == "counterfactual":
                missing_text = "没有找到可验证的替代路径，当前更像是缺少桥接点或被阻断。"
            elif "missing" in _normalize(query):
                missing_text = "当前没有完整路径；更可能是缺少桥接点。"
            claim = ClaimUnit(
                claim_type="missing_notice",
                text=missing_text if prefer_chinese else self._missing_path_text(query, path_mode=effective_path_mode),
                confidence=0.25,
            )
            return AnswerPlan(
                summary=claim.text,
                claims=[claim],
                conflict_state="missing",
                resolution_mode="path",
                evidence_mode="path",
                confidence=0.25,
                trace_bundle=ReasoningTraceBundle(intent_kind="path", resolution_mode="path", claim_count=1, path_mode=effective_path_mode),
            )

        rendered_paths = [item.to_path() for item in path_candidates]
        candidate_scores = [item.to_candidate_score() for item in path_candidates]
        claims: List[ClaimUnit] = []
        summary_parts: List[str] = []
        summary_limit = 2 if effective_path_mode == "multi" else 1
        if effective_path_mode in {"constrained", "counterfactual", "temporal_path", "state_evolution_path"}:
            summary_limit = min(2, len(path_candidates))
        for index, item in enumerate(path_candidates[:summary_limit]):
            description = self._describe_path(query, item, prefer_chinese=prefer_chinese, path_mode=effective_path_mode)
            claims.append(
                ClaimUnit(
                    claim_type="path_claim",
                    text=description,
                    memory_ids=_dedupe_strings(item.memory_ids),
                    path_refs=[f"path:{index}"],
                    time_scope=self._time_scope(item),
                    confidence=min(0.98, max(0.22, 0.45 + item.final_score)),
                )
            )
            summary_parts.append(description)
        prefix = "结论路径是" if prefer_chinese else "Grounded path"
        if effective_path_mode == "multi":
            prefix = "找到多条有依据的路径" if prefer_chinese else "Multiple grounded paths"
        elif effective_path_mode == "counterfactual":
            prefix = "反事实路径判断" if prefer_chinese else "Counterfactual path judgment"
        elif effective_path_mode == "constrained":
            prefix = "满足约束的路径" if prefer_chinese else "Constraint-satisfying path"
        summary = f"{prefix}：" + (("；" if prefer_chinese else "; ").join(summary_parts))
        if prefer_chinese:
            summary = summary.rstrip("；")
            if not summary.endswith(("。", "！", "？")):
                summary += "。"
        else:
            summary = summary.rstrip("; ")
            if not summary.endswith((".", "!", "?")):
                summary += "."
        return AnswerPlan(
            summary=summary,
            claims=claims,
            facts=[],
            paths=rendered_paths[:summary_limit],
            candidate_scores=candidate_scores,
            selected_memory_ids=_dedupe_strings(memory_id for item in path_candidates for memory_id in item.memory_ids),
            suppressed_memory_ids=[],
            unsupported_claims=[],
            conflict_state="none",
            resolution_mode="path",
            evidence_mode="path",
            confidence=min(0.98, max(0.32, 0.42 + len(claims) * 0.12)),
            trace_bundle=ReasoningTraceBundle(intent_kind="path", resolution_mode="path", claim_count=len(claims), path_mode=effective_path_mode),
        )

    def _single_claim(self, query: str, view: ResolvedSlotView, *, record: ResolvedSlotRecord | None, prefer_chinese: bool, previous: bool) -> ClaimUnit | None:
        if record is None:
            return None
        inactive_query = previous and self._is_inactive_query(query)
        label = self._inactive_label(prefer_chinese=prefer_chinese) if inactive_query else self._label(view.category, prefer_chinese=prefer_chinese, previous=previous)
        text = f"{label}是{record.value}" if prefer_chinese else f"{label}: {record.value}"
        return ClaimUnit(
            claim_type="slot_inactive" if inactive_query else ("slot_previous" if previous else "slot_current"),
            text=text,
            memory_ids=[record.memory_id],
            fact_refs=[f"fact:{record.memory_id}"],
            time_scope=f"turn:{record.turn_index}",
            confidence=min(0.96, 0.42 + record.score * 0.4),
        )

    def _record_for_mode(self, view: ResolvedSlotView, *, output_mode: str) -> ResolvedSlotRecord | None:
        if output_mode == "previous":
            return (
                view.previous_record
                or next((record for record in reversed(view.historical_chain) if _normalize(record.state) != "active"), None)
                or (view.suppressed_records[-1] if view.suppressed_records else None)
                or (view.active_record if view.active_record is not None and _normalize(view.active_record.state) != "active" else None)
            )
        return view.active_record

    def _compare_claim(self, query: str, view: ResolvedSlotView, *, prefer_chinese: bool) -> ClaimUnit | None:
        if view.previous_record is None and view.active_record is None:
            return None
        label_current, label_previous = self._compare_labels(query, view.category, prefer_chinese=prefer_chinese)
        if view.previous_record is None or view.active_record is None:
            record = view.active_record or view.previous_record
            if record is None:
                return None
            text = (
                f"只找到{record.value}，另一侧证据不足，无法完整对比。"
                if prefer_chinese
                else f"Only one side is grounded ({record.value}); the other side is missing."
            )
            return ClaimUnit(
                claim_type="missing_notice",
                text=text,
                memory_ids=[record.memory_id],
                fact_refs=[f"fact:{record.memory_id}"],
                time_scope=f"turn:{record.turn_index}",
                confidence=0.32,
            )
        text = (
            f"{label_previous}是{view.previous_record.value}；{label_current}是{view.active_record.value}"
            if prefer_chinese
            else f"{label_previous}: {view.previous_record.value}; {label_current}: {view.active_record.value}"
        )
        return ClaimUnit(
            claim_type="slot_compare",
            text=text,
            memory_ids=[view.previous_record.memory_id, view.active_record.memory_id],
            fact_refs=[f"fact:{view.previous_record.memory_id}", f"fact:{view.active_record.memory_id}"],
            time_scope=f"turn:{view.previous_record.turn_index}->{view.active_record.turn_index}",
            confidence=min(0.97, 0.5 + max(view.previous_record.score, view.active_record.score) * 0.35),
        )

    def _timeline_claim(self, view: ResolvedSlotView, *, prefer_chinese: bool) -> ClaimUnit | None:
        chain = list(view.historical_chain)
        if view.active_record is not None and all(view.active_record.memory_id != item.memory_id for item in chain):
            chain.append(view.active_record)
            chain.sort(key=lambda item: (item.turn_index, item.score))
        if not chain:
            return None
        label = self._label(view.category, prefer_chinese=prefer_chinese, previous=False)
        steps = []
        for item in chain[:6]:
            steps.append(f"第{item.turn_index}轮={item.value}" if prefer_chinese else f"turn {item.turn_index}={item.value}")
        rendered = " -> ".join(steps)
        text = f"{label}变化链：{rendered}" if prefer_chinese else f"{label} timeline: {rendered}"
        return ClaimUnit(
            claim_type="timeline_summary",
            text=text,
            memory_ids=[item.memory_id for item in chain],
            fact_refs=[f"fact:{item.memory_id}" for item in chain],
            time_scope=f"turn:{chain[0].turn_index}->{chain[-1].turn_index}",
            confidence=min(0.98, 0.46 + len(chain) * 0.08),
        )

    def _conflict_claim(self, view: ResolvedSlotView, *, prefer_chinese: bool) -> ClaimUnit:
        text = (
            f"{self._label(view.category, prefer_chinese=prefer_chinese, previous=False)}存在冲突，当前无法确定单一有效值。"
            if prefer_chinese
            else f"{self._label(view.category, prefer_chinese=prefer_chinese, previous=False)} conflicts, so no single active value can be confirmed."
        )
        memory_ids = [item.memory_id for item in view.historical_chain[:2]]
        return ClaimUnit(
            claim_type="conflict_notice",
            text=text,
            memory_ids=memory_ids,
            fact_refs=[f"fact:{memory_id}" for memory_id in memory_ids],
            confidence=0.2,
        )

    def _missing_claim(self, *, intent: QueryIntent, prefer_chinese: bool) -> ClaimUnit:
        text = "没有找到有依据的记忆。" if prefer_chinese else "No grounded memory was found."
        if intent.history_kind == "timeline":
            text = "没有找到有依据的时间演化链。" if prefer_chinese else "No grounded timeline was found."
        elif intent.history_kind == "previous":
            text = "没有找到有依据的历史值。" if prefer_chinese else "No grounded previous value was found."
        return ClaimUnit(claim_type="missing_notice", text=text, confidence=0.18)

    def _inactive_label(self, *, prefer_chinese: bool) -> str:
        return "应保持不激活的内容" if prefer_chinese else "inactive statement"

    def _facts_from_records(self, records: Sequence[ResolvedSlotRecord]) -> List[Dict[str, Any]]:
        facts: List[Dict[str, Any]] = []
        for record in records:
            if record is None:
                continue
            subject = record.anchors[0] if record.anchors else (record.slot_key or record.category)
            facts.append(
                {
                    "from": subject,
                    "to": record.value,
                    "relation": record.relation or f"{record.category}_memory",
                    "weight": round(max(0.25, min(0.98, 0.42 + record.score * 0.4)), 6),
                    "memory_id": record.memory_id,
                    "slot_key": record.slot_key,
                }
            )
        return facts

    def _dedupe_facts(self, facts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        seen = set()
        for fact in facts:
            key = (
                _normalize(fact.get("from", "")),
                _normalize(fact.get("relation", "")),
                _normalize(fact.get("to", "")),
                _normalize(fact.get("memory_id", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            results.append(dict(fact))
        return results

    def _claim_requires_evidence(self, claim: ClaimUnit) -> bool:
        return claim.claim_type not in {"missing_notice", "conflict_notice"}

    def _is_inactive_query(self, query: str) -> bool:
        lowered = _normalize(query)
        return any(
            marker in lowered
            for marker in (
                "noise",
                "inactive",
                "stay inactive",
                "should stay inactive",
                "should be ignored",
                "ignore this",
                "ignore that",
                "\u566a\u58f0",
                "\u4e0d\u6fc0\u6d3b",
                "\u4fdd\u6301\u4e0d\u6fc0\u6d3b",
                "\u4e0d\u5e94\u751f\u6548",
                "\u5ffd\u7565",
            )
        )

    def _label(self, category: str, *, prefer_chinese: bool, previous: bool) -> str:
        return self.profile.label(category, prefer_chinese=prefer_chinese, historical=previous, variant="compare")

    def _describe_path(self, query: str, item: PathCandidate, *, prefer_chinese: bool, path_mode: str = "") -> str:
        path = " -> ".join(item.concepts)
        normalized_query = _normalize(query)
        if path_mode == "counterfactual":
            blocker = self._extract_counterfactual_blocker(query)
            blocker_text = f" without {blocker}" if blocker else ""
            return f"{path} (path still exists{blocker_text})" if not prefer_chinese else f"{path}（移除{blocker or '阻断点'}后路径仍然存在）"
        if "missing" in normalized_query:
            return f"{path} (current best partial bridge)" if not prefer_chinese else f"{path}（当前最接近的桥接候选）"
        if path_mode == "multi":
            return f"{path} (branch candidate)" if not prefer_chinese else f"{path}（一条候选分支）"
        if item.temporal_tunnels:
            tunnel_count = len(item.temporal_tunnels)
            return f"{path}（含{tunnel_count}个时间隧穿）" if prefer_chinese else f"{path} (with {tunnel_count} temporal tunnel(s))"
        return path

    def _compare_labels(self, query: str, category: str, *, prefer_chinese: bool) -> tuple[str, str]:
        normalized = _normalize(query)
        base_label = self._label(category, prefer_chinese=prefer_chinese, previous=False)
        base_root = base_label.replace("current ", "").replace("当前", "")
        if _contains_marker(query, "active") and _contains_marker(query, "historical"):
            return (f"active {base_root}", f"historical {base_root}") if not prefer_chinese else (f"活跃{base_root}", f"历史{base_root}")
        if _contains_marker(query, "current") and _contains_marker(query, "previous"):
            return self._label(category, prefer_chinese=prefer_chinese, previous=False), self._label(category, prefer_chinese=prefer_chinese, previous=True)
        if "historical" in normalized:
            return self._label(category, prefer_chinese=prefer_chinese, previous=False), self._label(category, prefer_chinese=prefer_chinese, previous=True)
        return self._label(category, prefer_chinese=prefer_chinese, previous=False), self._label(category, prefer_chinese=prefer_chinese, previous=True)

    def _extract_counterfactual_blocker(self, query: str) -> str:
        match = re.search(r"\bwithout\s+([a-z0-9_.-]+)", _normalize(query), flags=re.IGNORECASE)
        return match.group(1) if match else ""

    def _missing_path_text(self, query: str, *, path_mode: str) -> str:
        if path_mode == "counterfactual":
            blocker = self._extract_counterfactual_blocker(query)
            return f"No grounded alternative path was found after removing {blocker or 'the blocker'}."
        if "missing" in _normalize(query):
            return "No complete grounded path was found; the graph appears to be missing a bridge."
        return "No grounded path was found."

    def _time_scope(self, item: PathCandidate) -> str:
        if not item.timeline_nodes:
            return ""
        return f"turn:{int(item.timeline_nodes[0]['turn_index'])}->{int(item.timeline_nodes[-1]['turn_index'])}"
