from __future__ import annotations

import time
from typing import Any, Dict

from experiments.replacement.adapters import (
    DirectExtractionReasoner,
    OpenAICompatCoTReasoner,
    OpenAICompatDirectReasoner,
    OpenAICompatFullContextReasoner,
    TriMazeIsolatedReasoner,
)
from experiments.replacement.adapters.base import AdapterResponse, LLMProfile, MemoryAdapter, MemoryRetrieval, ReasoningAdapter
from experiments.replacement.memory_profiles import TMCRAProfile

from .contracts import OverlayReasonerConfig, StructuredReasoningPrior
from .entity import EntityDisambiguator
from .evidence import EvidenceRealizer
from .intent import QueryIntentParser
from .judge import LightweightJudge
from .normalization import LegacySlotNormalizer
from .pathing import PathOverlayPlanner
from .pipeline import ReasoningRequest, TMCRAReasoningPipeline
from .rerank import RetrievalReranker
from .temporal_shards import TemporalShardIndex
from .writeback_judge import AnswerWritebackManager


class OverlayMemoryAdapter(MemoryAdapter):
    def __init__(
        self,
        base_adapter: MemoryAdapter,
        *,
        config: OverlayReasonerConfig | None = None,
        parser: QueryIntentParser | None = None,
        reranker: RetrievalReranker | None = None,
        coarse_multiplier: int = 4,
        temporal_shards_enabled: bool = False,
    ) -> None:
        self.base_adapter = base_adapter
        self.name = base_adapter.name
        self.config = config or OverlayReasonerConfig(temporal_shards_enabled=temporal_shards_enabled)
        self.profile = TMCRAProfile()
        self.parser = parser or QueryIntentParser(profile=self.profile)
        self.reranker = reranker or RetrievalReranker(entity_disambiguator=EntityDisambiguator())
        self.coarse_multiplier = max(2, int(coarse_multiplier))
        self.temporal_shards_enabled = bool(self.config.temporal_shards_enabled or temporal_shards_enabled)
        self.temporal_shards = TemporalShardIndex() if self.temporal_shards_enabled else None
        self.slot_normalizer = LegacySlotNormalizer()
        self._last_query = ""
        self._last_retrieval = MemoryRetrieval()
        self._turn_index = 0

    def reset(self) -> None:
        self.base_adapter.reset()
        self._turn_index = 0
        if self.temporal_shards is not None:
            self.temporal_shards.reset()
        self._last_query = ""
        self._last_retrieval = MemoryRetrieval()

    def ingest_turn(
        self,
        user_text: str,
        assistant_text: str = "",
        *,
        answer_payload: Dict[str, Any] | None = None,
        extraction_result: Dict[str, Any] | None = None,
    ) -> None:
        self._turn_index += 1
        self.base_adapter.ingest_turn(
            user_text,
            assistant_text,
            answer_payload=answer_payload,
            extraction_result=extraction_result,
        )
        if self.temporal_shards is not None:
            self.temporal_shards.ingest_turn(
                turn_index=self._turn_index,
                user_text=user_text,
                assistant_text=assistant_text,
                extraction_result=extraction_result,
                answer_payload=answer_payload,
            )

    def retrieve(self, query: str, *, top_k: int = 6) -> MemoryRetrieval:
        intent = self.parser.parse(query)
        coarse_top_k = max(top_k * self.coarse_multiplier, 16)
        base_retrieval = self.base_adapter.retrieve(query, top_k=coarse_top_k)
        if self.temporal_shards is not None:
            shard_hits = self.temporal_shards.retrieve(query, intent=intent, top_k=max(2, top_k // 2))
            if shard_hits:
                base_retrieval = MemoryRetrieval(
                    concepts=list(base_retrieval.concepts),
                    relations=list(base_retrieval.relations),
                    hits=[*list(base_retrieval.hits), *list(shard_hits)],
                    active_hits=[*list(base_retrieval.active_hits), *[hit for hit in shard_hits if hit.state == "active"]],
                    history_hits=[*list(base_retrieval.history_hits), *[hit for hit in shard_hits if hit.state != "active"]],
                    stale_hits=list(base_retrieval.stale_hits),
                    overwrite_hits=list(base_retrieval.overwrite_hits),
                    false_hits=list(base_retrieval.false_hits),
                    retrieval_seconds=base_retrieval.retrieval_seconds,
                    context_token_estimate=base_retrieval.context_token_estimate,
                    retrieval_context_token_estimate=base_retrieval.retrieval_context_token_estimate,
                    metadata={
                        **dict(base_retrieval.metadata or {}),
                        "temporal_shards": {
                            "enabled": True,
                            "shard_hit_count": len(shard_hits),
                            "stats": self.temporal_shards.stats(),
                        },
                    },
                )
        normalized_retrieval = self.slot_normalizer.normalize_retrieval(base_retrieval)
        retrieval = self.reranker.rerank(query, normalized_retrieval, top_k=top_k, intent=intent)
        self._last_query = query
        self._last_retrieval = retrieval
        return retrieval

    def stats(self) -> Dict[str, Any]:
        stats = dict(self.base_adapter.stats() or {})
        stats["context_token_estimate"] = int(self._last_retrieval.context_token_estimate)
        stats["retrieval_context_token_estimate"] = int(self._last_retrieval.retrieval_context_token_estimate)
        stats["overlay_enabled"] = True
        stats["temporal_shards_enabled"] = bool(self.temporal_shards_enabled)
        if self.temporal_shards is not None:
            stats["temporal_shards"] = self.temporal_shards.stats()
        return stats

    def storage_bytes(self) -> int:
        return self.base_adapter.storage_bytes()

    def build_prompt_context(self, query: str, *, top_k: int = 8) -> Dict[str, Any]:
        retrieval = self.retrieve(query, top_k=top_k)
        graph = getattr(self.base_adapter, "graph", None)
        if hasattr(graph, "to_dict"):
            try:
                graph_state = graph.to_dict(mode="light")
            except TypeError:
                graph_state = graph.to_dict()
        else:
            graph_state = {}
        return {
            "mode": "overlay_retrieval",
            "query": query,
            "retrieval": retrieval.to_dict(),
            "stats": self.stats(),
            "state": graph_state,
        }

    def export_dialog_graph(self, *, mode: str = "light") -> Dict[str, Any]:
        try:
            return self.base_adapter.export_dialog_graph(mode=mode)
        except TypeError:
            return self.base_adapter.export_dialog_graph()

    def export_dialog_graph_mermaid(self) -> str:
        return self.base_adapter.export_dialog_graph_mermaid()

    def register_answer_support(self, *, answer_id: str, memory_ids: list[str], query_id: str = "", answer_text: str = "") -> None:
        self.base_adapter.register_answer_support(answer_id=answer_id, memory_ids=memory_ids, query_id=query_id, answer_text=answer_text)

    def ingest_answer_writeback(
        self,
        *,
        query_text: str,
        answer_text: str,
        answer_id: str,
        writeback_records: list[dict[str, Any]],
        trace: Dict[str, Any] | None = None,
    ) -> list[str]:
        stored_ids = self.base_adapter.ingest_answer_writeback(
            query_text=query_text,
            answer_text=answer_text,
            answer_id=answer_id,
            writeback_records=writeback_records,
            trace=trace,
        )
        if stored_ids and self.temporal_shards is not None:
            self._turn_index = max(self._turn_index + 1, int(getattr(getattr(self.base_adapter, "graph", None), "turn_index", self._turn_index + 1)))
            self.temporal_shards.ingest_turn(
                turn_index=self._turn_index,
                user_text=query_text,
                assistant_text=answer_text,
                extraction_result={},
                answer_payload={"replacement_memory_records": list(writeback_records), "metadata": {"memory_write": True, "source": "assistant_writeback", "trace": dict(trace or {})}},
            )
        return stored_ids

    def telemetry_snapshot(self) -> Dict[str, Any]:
        snapshot = dict(self.base_adapter.telemetry_snapshot() or {})
        snapshot["overlay_enabled"] = True
        snapshot["temporal_shards_enabled"] = bool(self.temporal_shards_enabled)
        return snapshot


class _OverlayReasonerBase(ReasoningAdapter):
    def __init__(self, base_reasoner: ReasoningAdapter, *, config: OverlayReasonerConfig | None = None, judge: LightweightJudge | None = None) -> None:
        self.base_reasoner = base_reasoner
        self.name = base_reasoner.name
        self.config = config or OverlayReasonerConfig()
        self.profile = TMCRAProfile()
        self.intent_parser = QueryIntentParser(profile=self.profile)
        self.reranker = RetrievalReranker(entity_disambiguator=EntityDisambiguator())
        self.path_planner = PathOverlayPlanner()
        self.realizer = EvidenceRealizer(profile=self.profile)
        self.pipeline = TMCRAReasoningPipeline(
            profile=self.profile,
            intent_parser=self.intent_parser,
            path_planner=self.path_planner,
            realizer=self.realizer,
            judge=judge or LightweightJudge(self.config.judge_config),
        )
        self.writeback_manager = AnswerWritebackManager(self.config.writeback_config)

    async def answer(self, query: str, *, answer_mode: str, memory_adapter: MemoryAdapter) -> AdapterResponse:
        start = time.perf_counter()
        overlay_memory = (
            memory_adapter
            if isinstance(memory_adapter, OverlayMemoryAdapter)
            else OverlayMemoryAdapter(
                memory_adapter,
                config=self.config,
                parser=self.intent_parser,
                reranker=self.reranker,
                temporal_shards_enabled=self.config.temporal_shards_enabled,
            )
        )
        base_response: AdapterResponse | None = None
        prior = StructuredReasoningPrior()
        base_assist_enabled = self.config.base_assist_mode != "disabled"
        if base_assist_enabled:
            base_response = await self.base_reasoner.answer(query, answer_mode=answer_mode, memory_adapter=overlay_memory)
            prior = StructuredReasoningPrior.from_response(base_response)
        retrieval = overlay_memory._last_retrieval if overlay_memory._last_query == query else overlay_memory.retrieve(query, top_k=6)
        bundle = self.pipeline.run(
            ReasoningRequest(query=query, answer_mode=answer_mode, top_k=6, metadata={"source": "overlay_wrapper"}),
            memory_adapter=overlay_memory,
            prior=prior,
            config=self.config,
            reasoner_name=self.name,
            memory_name=memory_adapter.name,
            base_response=base_response,
            retrieval=retrieval,
        )
        response = bundle.response
        intent = bundle.context.intent
        path_candidates = bundle.context.path_candidates
        fallback_used = bool(bundle.fallback_used)
        used_memory_ids = list((response.metadata.get("overlay_evidence_pack", {}) or {}).get("used_memory_ids", []))
        if used_memory_ids:
            memory_adapter.register_answer_support(
                answer_id=f"overlay-answer:{int(time.time() * 1000)}",
                memory_ids=used_memory_ids,
                query_id=str(retrieval.metadata.get("query_id", "")),
                answer_text=response.answer,
            )
        writeback_answer_id = f"overlay-answer:{int(time.time() * 1000)}:writeback"
        writeback_trace = self.writeback_manager.process(
            query_text=query,
            response=response,
            memory_adapter=overlay_memory,
            answer_id=writeback_answer_id,
        )
        response.reasoner_name = self.name
        response.memory_name = memory_adapter.name
        response.latency_seconds = max(float(response.latency_seconds), time.perf_counter() - start)
        response.trace = {
            **dict(response.trace or {}),
            "overlay": {
                **dict((dict(response.trace or {}).get("overlay", {}) or {})),
                "overlay_enabled": True,
                "temporal_overlay_enabled": True,
                "base_reasoner_used": base_assist_enabled,
                "fallback_used": fallback_used,
                "base_assist_mode": self.config.base_assist_mode,
                "fallback_policy": self.config.fallback_policy,
                "path_prior_source": self.config.path_prior_source,
                "prior_sources": list(prior.prior_sources),
                "query_intent": intent.to_dict(),
                "writeback_enabled": bool(writeback_trace.get("enabled", False)),
                "writeback_mode": self.config.writeback_config.mode,
                "writeback_trace": dict(writeback_trace),
            },
        }
        response.metadata = {
            **dict(response.metadata or {}),
            **({"llm_usage": dict((base_response.metadata or {}).get("llm_usage", {}) or {})} if base_response and dict((base_response.metadata or {}).get("llm_usage", {}) or {}) else {}),
            "tmcra_reasoning_bundle": bundle.to_dict(),
            "writeback_trace": dict(writeback_trace),
            "writeback_written_count": int(writeback_trace.get("written_count", 0) or 0),
            "writeback_rejected_count": int(writeback_trace.get("rejected_count", 0) or 0),
            "promotion_events": [dict(item) for item in writeback_trace.get("promotion_events", []) or [] if isinstance(item, dict)],
            "overlay": {
                **dict((dict(response.metadata or {}).get("overlay", {}) or {})),
                "enabled": True,
                "temporal_enabled": True,
                "base_reasoner_name": self.base_reasoner.name,
                "fallback_used": fallback_used,
                "config": self.config.to_dict(),
                "structured_prior": prior.to_dict(),
                "retrieval": retrieval.to_dict(),
                "path_candidates": [item.to_path() for item in path_candidates],
                "tmcra_reasoning_bundle": bundle.to_dict(),
                "writeback_trace": dict(writeback_trace),
            },
        }
        return response


class OverlayTriMazeReasoner(_OverlayReasonerBase):
    def __init__(self, *, tunneling_enabled: bool = True, config: OverlayReasonerConfig | None = None, judge: LightweightJudge | None = None) -> None:
        super().__init__(TriMazeIsolatedReasoner(tunneling_enabled=tunneling_enabled), config=config, judge=judge)


class OverlayDirectReasoner(_OverlayReasonerBase):
    def __init__(self, *, config: OverlayReasonerConfig | None = None, judge: LightweightJudge | None = None) -> None:
        super().__init__(DirectExtractionReasoner(), config=config, judge=judge)


class OverlayOpenAICompatDirectReasoner(_OverlayReasonerBase):
    def __init__(self, *, profile: LLMProfile | None = None, base_url: str | None = None, api_key: str | None = None, model: str | None = None, config: OverlayReasonerConfig | None = None, judge: LightweightJudge | None = None) -> None:
        super().__init__(OpenAICompatDirectReasoner(profile=profile, base_url=base_url, api_key=api_key, model=model), config=config, judge=judge)


class OverlayOpenAICompatCoTReasoner(_OverlayReasonerBase):
    def __init__(self, *, profile: LLMProfile | None = None, base_url: str | None = None, api_key: str | None = None, model: str | None = None, config: OverlayReasonerConfig | None = None, judge: LightweightJudge | None = None) -> None:
        super().__init__(OpenAICompatCoTReasoner(profile=profile, base_url=base_url, api_key=api_key, model=model), config=config, judge=judge)


class OverlayOpenAICompatFullContextReasoner(_OverlayReasonerBase):
    def __init__(self, *, profile: LLMProfile | None = None, base_url: str | None = None, api_key: str | None = None, model: str | None = None, config: OverlayReasonerConfig | None = None, judge: LightweightJudge | None = None) -> None:
        super().__init__(OpenAICompatFullContextReasoner(profile=profile, base_url=base_url, api_key=api_key, model=model), config=config, judge=judge)
