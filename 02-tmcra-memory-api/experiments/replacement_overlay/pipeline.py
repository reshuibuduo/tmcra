from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Sequence

from experiments.replacement.adapters.base import AdapterResponse, MemoryAdapter, MemoryRetrieval
from experiments.replacement.memory_profiles import TMCRAProfile

from .contracts import OverlayReasonerConfig, StructuredReasoningPrior
from .answer_planner import AnswerPlan, AnswerPlanner
from .evidence import EvidenceRealizer
from .intent import QueryIntent, QueryIntentParser
from .judge import JudgmentTrace, LightweightJudge
from .pathing import PathCandidate, PathOverlayPlanner
from .slot_state import SlotStateResolution, SlotStateResolver
from .temporal_reasoning import TemporalReasoner, TemporalReasoningTrace


@dataclass(slots=True)
class ReasoningRequest:
    query: str
    answer_mode: str = "transparent"
    top_k: int = 6
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "answer_mode": self.answer_mode,
            "top_k": int(self.top_k),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ReasoningContext:
    request: ReasoningRequest
    intent: QueryIntent
    retrieval: MemoryRetrieval
    slot_resolution: SlotStateResolution
    temporal_trace: TemporalReasoningTrace
    judge_trace: JudgmentTrace = field(default_factory=JudgmentTrace)
    prior: StructuredReasoningPrior = field(default_factory=StructuredReasoningPrior)
    config: OverlayReasonerConfig = field(default_factory=OverlayReasonerConfig)
    path_candidates: List[PathCandidate] = field(default_factory=list)
    answer_plan: AnswerPlan | None = None
    base_response: AdapterResponse | None = None
    reasoner_name: str = ""
    memory_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "intent": self.intent.to_dict(),
            "retrieval": self.retrieval.to_dict(),
            "judge_trace": self.judge_trace.to_dict(),
            "prior": self.prior.to_dict(),
            "config": self.config.to_dict(),
            "slot_resolution": self.slot_resolution.to_dict(),
            "temporal_trace": self.temporal_trace.to_dict(),
            "path_candidates": [item.to_path() for item in self.path_candidates],
            "answer_plan": self.answer_plan.to_dict() if self.answer_plan is not None else None,
            "reasoner_name": self.reasoner_name,
            "memory_name": self.memory_name,
        }


@dataclass(slots=True)
class ReasoningResultBundle:
    request: ReasoningRequest
    context: ReasoningContext
    response: AdapterResponse
    claims: List[Dict[str, Any]] = field(default_factory=list)
    telemetry: Dict[str, Any] = field(default_factory=dict)
    judge_trace: JudgmentTrace = field(default_factory=JudgmentTrace)
    fallback_used: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "context": self.context.to_dict(),
            "response": self.response.to_dict(),
            "claims": list(self.claims),
            "telemetry": dict(self.telemetry),
            "judge_trace": self.judge_trace.to_dict(),
            "fallback_used": bool(self.fallback_used),
        }


class TMCRAReasoningPipeline:
    def __init__(
        self,
        *,
        profile: TMCRAProfile | None = None,
        intent_parser: QueryIntentParser | None = None,
        slot_resolver: SlotStateResolver | None = None,
        temporal_reasoner: TemporalReasoner | None = None,
        path_planner: PathOverlayPlanner | None = None,
        answer_planner: AnswerPlanner | None = None,
        realizer: EvidenceRealizer | None = None,
        judge: LightweightJudge | None = None,
    ) -> None:
        self.profile = profile or TMCRAProfile()
        self.intent_parser = intent_parser or QueryIntentParser(profile=self.profile)
        self.slot_resolver = slot_resolver or SlotStateResolver()
        self.temporal_reasoner = temporal_reasoner or TemporalReasoner()
        self.path_planner = path_planner or PathOverlayPlanner()
        self.answer_planner = answer_planner or AnswerPlanner(profile=self.profile)
        self.realizer = realizer or EvidenceRealizer(profile=self.profile)
        self.judge = judge

    def run(
        self,
        request: ReasoningRequest,
        *,
        memory_adapter: MemoryAdapter,
        base_response: AdapterResponse | None = None,
        prior: StructuredReasoningPrior | None = None,
        config: OverlayReasonerConfig | None = None,
        reasoner_name: str,
        memory_name: str,
        retrieval: MemoryRetrieval | None = None,
    ) -> ReasoningResultBundle:
        checkpoints: Dict[str, float] = {"start": time.perf_counter()}
        stage_status: Dict[str, Dict[str, Any]] = {}
        stage_errors: Dict[str, str] = {}
        first_failure_stage = ""

        def _run_stage(stage_name: str, func: Any) -> Any:
            nonlocal first_failure_stage
            stage_start = time.perf_counter()
            try:
                result = func()
                stage_end = time.perf_counter()
                checkpoints[stage_name] = stage_end
                stage_status[stage_name] = {
                    "status": "ok",
                    "latency_seconds": round(max(0.0, stage_end - stage_start), 6),
                }
                return result
            except Exception as exc:
                stage_end = time.perf_counter()
                checkpoints[stage_name] = stage_end
                error_message = f"{exc.__class__.__name__}: {exc}"
                stage_status[stage_name] = {
                    "status": "error",
                    "latency_seconds": round(max(0.0, stage_end - stage_start), 6),
                    "error": error_message,
                }
                stage_errors[stage_name] = error_message
                if not first_failure_stage:
                    first_failure_stage = stage_name
                raise RuntimeError(f"pipeline_stage_error:{stage_name}:{error_message}") from exc

        prior = prior or StructuredReasoningPrior()
        config = config or OverlayReasonerConfig()
        intent = _run_stage("intent_parse", lambda: self.intent_parser.parse(request.query, answer_mode=request.answer_mode))
        retrieval = retrieval or _run_stage(
            "memory_retrieve",
            lambda: memory_adapter.retrieve(request.query, top_k=max(1, int(request.top_k))),
        )
        judge_trace = JudgmentTrace()
        slot_preview = None
        path_preview: List[PathCandidate] = []
        path_query_meta = self.path_planner.query_constraints(request.query, intent=intent)
        should_preview_paths = bool(intent.requires_path_reasoning or intent.kind == "path" or prior.candidate_paths)
        if should_preview_paths:
            path_preview = _run_stage(
                "path_preview",
                lambda: self.path_planner.preview_candidates(
                    request.query,
                    intent=intent,
                    retrieval=retrieval,
                    prior=prior,
                    config=config,
                    base_response=base_response,
                ),
            )
        else:
            checkpoints["path_preview"] = time.perf_counter()
            stage_status["path_preview"] = {"status": "skipped", "reason": "path_preview_not_required", "latency_seconds": 0.0}
        if self.judge is not None and config.judge_config.mode != "disabled":
            slot_preview = _run_stage(
                "judge_preview",
                lambda: self.slot_resolver.preview(
                    request.query,
                    intent=intent,
                    retrieval=retrieval,
                    limit=max(1, int(config.judge_config.max_slot_groups)),
                ),
            )
            path_preview_summary = self.path_planner.summarize_candidates(
                path_preview,
                required_nodes=path_query_meta.get("required_nodes", []),
                blocked_nodes=path_query_meta.get("blocked_nodes", []),
            )

            def _judge_call() -> JudgmentTrace:
                try:
                    return self.judge.evaluate(
                        query=request.query,
                        answer_mode=request.answer_mode,
                        intent=intent,
                        preview=slot_preview,
                        prior_paths=list(prior.candidate_paths),
                        path_candidates=path_preview_summary,
                        query_kind_tags=path_query_meta.get("query_kind_tags", []),
                        required_nodes=path_query_meta.get("required_nodes", []),
                        blocked_nodes=path_query_meta.get("blocked_nodes", []),
                    )
                except TypeError:
                    return self.judge.evaluate(
                        query=request.query,
                        answer_mode=request.answer_mode,
                        intent=intent,
                        preview=slot_preview,
                        prior_paths=path_preview_summary,
                    )

            judge_trace = _run_stage("judge_decide", _judge_call)
        else:
            checkpoints["judge_preview"] = time.perf_counter()
            stage_status["judge_preview"] = {"status": "skipped", "reason": "judge_disabled", "latency_seconds": 0.0}
            checkpoints["judge_decide"] = time.perf_counter()
            stage_status["judge_decide"] = {"status": "skipped", "reason": "judge_disabled", "latency_seconds": 0.0}
        slot_resolution = _run_stage(
            "slot_state_resolve",
            lambda: self.slot_resolver.resolve(
                request.query,
                intent=intent,
                retrieval=retrieval,
                judge_decision=judge_trace.decision if judge_trace.decision.decision_valid else None,
                preview=slot_preview,
            ),
        )
        temporal_trace = _run_stage(
            "temporal_reason",
            lambda: self.temporal_reasoner.reason(request.query, intent=intent, resolution=slot_resolution),
        )
        path_candidates = _run_stage(
            "path_finalize",
            lambda: self.path_planner.finalize_candidates(
                request.query,
                intent=intent,
                candidates=path_preview,
                judge_decision=judge_trace.decision if judge_trace.decision.decision_valid else None,
            ),
        )
        answer_plan = _run_stage(
            "answer_plan",
            lambda: self.answer_planner.plan(
                request.query,
                intent=intent,
                resolution=slot_resolution,
                temporal_trace=temporal_trace,
                path_candidates=path_candidates,
                path_output_mode=judge_trace.decision.path_output_mode if judge_trace.decision.decision_valid else "",
            ),
        )
        response = _run_stage(
            "evidence_realize",
            lambda: self.realizer.render_planned_response(
                query=request.query,
                answer_mode=request.answer_mode,
                intent=intent,
                retrieval=retrieval,
                path_candidates=path_candidates,
                answer_plan=answer_plan,
                slot_resolution=slot_resolution,
                temporal_trace=temporal_trace,
                base_response=base_response,
                prior=prior,
                config=config,
                reasoner_name=reasoner_name,
                memory_name=memory_name,
            ),
        )
        path_preview_summary = self.path_planner.summarize_candidates(
            path_preview,
            required_nodes=path_query_meta.get("required_nodes", []),
            blocked_nodes=path_query_meta.get("blocked_nodes", []),
        )
        path_preview_empty_reason = (
            self.path_planner.empty_preview_reason(
                request.query,
                intent=intent,
                retrieval=retrieval,
                prior=prior,
            )
            if should_preview_paths and not path_preview
            else ""
        )
        path_finalize_summary = self.path_planner.summarize_candidates(
            path_candidates,
            required_nodes=path_query_meta.get("required_nodes", []),
            blocked_nodes=path_query_meta.get("blocked_nodes", []),
        )
        path_realization = self.path_planner.realize_paths(
            request.query,
            intent=intent,
            preview_candidates=path_preview,
            final_candidates=path_candidates,
            judge_decision=judge_trace.decision if judge_trace.decision.decision_valid else None,
        )
        judge_effective = self._judge_effective(
            slot_resolution=slot_resolution,
            judge_trace=judge_trace,
            path_preview=path_preview_summary,
            path_final=path_finalize_summary,
        )
        context = ReasoningContext(
            request=request,
            intent=intent,
            retrieval=retrieval,
            judge_trace=judge_trace,
            prior=prior,
            config=config,
            slot_resolution=slot_resolution,
            temporal_trace=temporal_trace,
            path_candidates=list(path_candidates),
            answer_plan=answer_plan,
            base_response=base_response,
            reasoner_name=reasoner_name,
            memory_name=memory_name,
        )
        fallback_used = bool((response.metadata.get("overlay_evidence_pack", {}) or {}).get("fallback_used", False))
        telemetry = self._telemetry(
            intent=intent,
            retrieval=retrieval,
            prior=prior,
            config=config,
            slot_resolution=slot_resolution,
            judge_trace=judge_trace,
            path_candidates=path_candidates,
            fallback_used=fallback_used,
            checkpoints=checkpoints,
            judge_effective=judge_effective,
            stage_status=stage_status,
            stage_errors=stage_errors,
            first_failure_stage=first_failure_stage,
        )
        response.trace = {
            **dict(response.trace or {}),
            "tmcra_reasoning_v2": {
                "intent": intent.to_dict(),
                "judge_trace": judge_trace.to_dict(),
                "slot_resolution": slot_resolution.to_dict(),
                "temporal_trace": temporal_trace.to_dict(),
                "path_preview_summary": {"count": len(path_preview_summary), "empty_reason": path_preview_empty_reason, "candidates": path_preview_summary[:6]},
                "path_finalize_summary": {"count": len(path_finalize_summary), "candidates": path_finalize_summary[:6]},
                "path_realization": dict(path_realization),
                "judge_effective": judge_effective,
                "claims": [item.to_dict() for item in answer_plan.claims],
                "telemetry": telemetry,
            },
        }
        response.metadata = {
            **dict(response.metadata or {}),
            "tmcra_reasoning_bundle": {
                "intent": intent.to_dict(),
                "judge_trace": judge_trace.to_dict(),
                "slot_resolution": slot_resolution.to_dict(),
                "temporal_trace": temporal_trace.to_dict(),
                "path_preview_summary": {"count": len(path_preview_summary), "empty_reason": path_preview_empty_reason, "candidates": path_preview_summary[:6]},
                "path_finalize_summary": {"count": len(path_finalize_summary), "candidates": path_finalize_summary[:6]},
                "path_realization": dict(path_realization),
                "judge_effective": judge_effective,
                "claims": [item.to_dict() for item in answer_plan.claims],
                "telemetry": telemetry,
            },
        }
        return ReasoningResultBundle(
            request=request,
            context=context,
            response=response,
            claims=[item.to_dict() for item in answer_plan.claims],
            telemetry=telemetry,
            judge_trace=judge_trace,
            fallback_used=fallback_used,
        )

    def _telemetry(
        self,
        *,
        intent: QueryIntent,
        retrieval: MemoryRetrieval,
        prior: StructuredReasoningPrior,
        config: OverlayReasonerConfig,
        slot_resolution: SlotStateResolution,
        judge_trace: JudgmentTrace,
        path_candidates: Sequence[PathCandidate],
        fallback_used: bool,
        checkpoints: Dict[str, float],
        judge_effective: bool,
        stage_status: Dict[str, Dict[str, Any]],
        stage_errors: Dict[str, str],
        first_failure_stage: str,
    ) -> Dict[str, Any]:
        ordered_keys = [
            "intent_parse",
            "memory_retrieve",
            "path_preview",
            "judge_preview",
            "judge_decide",
            "slot_state_resolve",
            "temporal_reason",
            "path_finalize",
            "answer_plan",
            "evidence_realize",
        ]
        previous = checkpoints["start"]
        latency_breakdown: Dict[str, float] = {}
        for key in ordered_keys:
            current = checkpoints.get(key, previous)
            latency_breakdown[key] = round(max(0.0, current - previous), 6)
            previous = current
        latency_breakdown["total"] = round(max(0.0, previous - checkpoints["start"]), 6)
        temporal_meta = dict(retrieval.metadata.get("temporal_shards", {}) or {})
        return {
            "intent_kind": intent.kind,
            "history_kind": intent.history_kind,
            "resolution_mode": slot_resolution.mode,
            "suppressed_hit_count": len(slot_resolution.suppressed_records()),
            "temporal_shard_hit_count": int(temporal_meta.get("shard_hit_count", 0) or 0),
            "path_candidate_count": len(path_candidates),
            "prior_sources": list(prior.prior_sources),
            "fallback_policy": config.fallback_policy,
            "path_generation_source": config.path_prior_source,
            "slot_aliases": dict(retrieval.metadata.get("slot_aliases", {}) or {}),
            "judge_enabled": bool(judge_trace.enabled),
            "judge_triggered": bool(judge_trace.triggered),
            "judge_mode": judge_trace.mode,
            "judge_confidence": round(float(judge_trace.decision.confidence), 6),
            "judge_decision_valid": bool(judge_trace.decision.decision_valid),
            "judge_fallback_reason": judge_trace.fallback_reason,
            "judge_effective": bool(judge_effective),
            "fallback_used": fallback_used,
            "latency_breakdown": latency_breakdown,
            "stage_status": dict(stage_status),
            "stage_errors": dict(stage_errors),
            "first_failure_stage": first_failure_stage,
            "semantic_failure": bool(first_failure_stage),
            "audit_only_failure": False,
        }

    def _judge_effective(
        self,
        *,
        slot_resolution: SlotStateResolution,
        judge_trace: JudgmentTrace,
        path_preview: Sequence[Dict[str, Any]],
        path_final: Sequence[Dict[str, Any]],
    ) -> bool:
        if not judge_trace.decision.decision_valid:
            return False
        if bool(slot_resolution.resolution_trace.get("judge_effective")) or bool(slot_resolution.resolution_trace.get("coverage_changed")):
            return True
        preview_ids = [str(item.get("path_id", "")) for item in path_preview]
        final_ids = [str(item.get("path_id", "")) for item in path_final]
        if final_ids and final_ids != preview_ids[: len(final_ids)]:
            return True
        return False
