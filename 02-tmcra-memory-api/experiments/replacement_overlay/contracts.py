from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from experiments.replacement.adapters.base import AdapterResponse, LLMProfile
from .judge import JudgeConfig


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _default_writeback_manifest_path() -> str:
    return str(
        (
            Path(__file__).resolve().parents[2]
            / "outputs"
            / "replacement_eval"
            / "tmcra_writeback_judge"
            / "tmcra_writeback_judge_manifest_v1.json"
        )
    )


@dataclass(slots=True)
class WritebackConfig:
    mode: str = "shadow"
    provider: str = "tmcra_writeback_judge"
    profile: LLMProfile = field(
        default_factory=lambda: LLMProfile(
            name="qwen3b_writeback_judge",
            model="Qwen/Qwen2.5-3B-Instruct",
            base_url="",
            api_key="",
            system_prompt="You are a TMCRA writeback decision layer. Return strict JSON only and never answer the user.",
            timeout_seconds=1.2,
            temperature=0.0,
            max_tokens=192,
        )
    )
    manifest_path: str = field(default_factory=_default_writeback_manifest_path)
    gate_model_path: str = ""
    class_model_path: str = ""
    slot_model_path: str = ""
    min_confidence: float = 0.8
    borderline_min_confidence: float = 0.7
    shadow_only: bool = True
    standard_promotion_repeats: int = 2
    standard_promotion_confidence: float = 0.9
    standard_promotion_support_refs: int = 2
    fast_promotion_confidence: float = 0.97
    fast_promotion_support_refs: int = 3

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "provider": self.provider,
            "profile": {
                "name": self.profile.name,
                "model": self.profile.model,
                "base_url": self.profile.base_url,
                "timeout_seconds": float(self.profile.timeout_seconds),
                "temperature": float(self.profile.temperature),
                "max_tokens": int(self.profile.max_tokens),
            },
            "manifest_path": self.manifest_path,
            "gate_model_path": self.gate_model_path,
            "class_model_path": self.class_model_path,
            "slot_model_path": self.slot_model_path,
            "min_confidence": round(float(self.min_confidence), 6),
            "borderline_min_confidence": round(float(self.borderline_min_confidence), 6),
            "shadow_only": bool(self.shadow_only),
            "standard_promotion_repeats": int(self.standard_promotion_repeats),
            "standard_promotion_confidence": round(float(self.standard_promotion_confidence), 6),
            "standard_promotion_support_refs": int(self.standard_promotion_support_refs),
            "fast_promotion_confidence": round(float(self.fast_promotion_confidence), 6),
            "fast_promotion_support_refs": int(self.fast_promotion_support_refs),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "WritebackConfig":
        payload = dict(payload or {})
        mode = _clean_text(payload.get("mode", "")) or "shadow"
        if mode == "assist":
            mode = "enabled"
        shadow_only = bool(payload.get("shadow_only", mode == "shadow"))
        profile_payload = dict(payload.get("profile") or {})
        return cls(
            mode=mode,
            provider=_clean_text(payload.get("provider", "")) or "tmcra_writeback_judge",
            profile=LLMProfile(
                name=_clean_text(profile_payload.get("name", "")) or "qwen3b_writeback_judge",
                model=_clean_text(profile_payload.get("model", "")) or "Qwen/Qwen2.5-3B-Instruct",
                base_url=_clean_text(profile_payload.get("base_url", "")),
                api_key=_clean_text(profile_payload.get("api_key", "")),
                system_prompt=_clean_text(profile_payload.get("system_prompt", "")) or "You are a TMCRA writeback decision layer. Return strict JSON only and never answer the user.",
                timeout_seconds=float(profile_payload.get("timeout_seconds", 1.2) or 1.2),
                temperature=float(profile_payload.get("temperature", 0.0) or 0.0),
                max_tokens=int(profile_payload.get("max_tokens", 192) or 192),
            ),
            manifest_path=_clean_text(payload.get("manifest_path", "")) or _default_writeback_manifest_path(),
            gate_model_path=_clean_text(payload.get("gate_model_path", "")),
            class_model_path=_clean_text(payload.get("class_model_path", "")),
            slot_model_path=_clean_text(payload.get("slot_model_path", "")),
            min_confidence=float(payload.get("min_confidence", 0.8) or 0.8),
            borderline_min_confidence=float(payload.get("borderline_min_confidence", 0.7) or 0.7),
            shadow_only=shadow_only,
            standard_promotion_repeats=int(payload.get("standard_promotion_repeats", 2) or 2),
            standard_promotion_confidence=float(payload.get("standard_promotion_confidence", 0.9) or 0.9),
            standard_promotion_support_refs=int(payload.get("standard_promotion_support_refs", 2) or 2),
            fast_promotion_confidence=float(payload.get("fast_promotion_confidence", 0.97) or 0.97),
            fast_promotion_support_refs=int(payload.get("fast_promotion_support_refs", 3) or 3),
        )


@dataclass(slots=True)
class MemoryPersistenceConfig:
    backend: str = "sqlite"
    storage_path: str = ""
    scope_id: str = ""
    profile_name: str = "tmcra"
    audit_mode: str = "bounded"
    audit_retention: int = 256
    lightweight_stats: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "storage_path": self.storage_path,
            "scope_id": self.scope_id,
            "profile_name": self.profile_name,
            "audit_mode": self.audit_mode,
            "audit_retention": int(self.audit_retention),
            "lightweight_stats": bool(self.lightweight_stats),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "MemoryPersistenceConfig":
        payload = dict(payload or {})
        return cls(
            backend=_clean_text(payload.get("backend", "")) or "sqlite",
            storage_path=_clean_text(payload.get("storage_path", "")),
            scope_id=_clean_text(payload.get("scope_id", "")),
            profile_name=_clean_text(payload.get("profile_name", "")) or "tmcra",
            audit_mode=_clean_text(payload.get("audit_mode", "")) or "bounded",
            audit_retention=int(payload.get("audit_retention", 256) or 256),
            lightweight_stats=bool(payload.get("lightweight_stats", True)),
        )


@dataclass(slots=True)
class OverlayReasonerConfig:
    base_assist_mode: str = "structured_prior"
    temporal_shards_enabled: bool = False
    path_prior_source: str = "base_and_graph"
    fallback_policy: str = "disabled"
    memory_config: MemoryPersistenceConfig = field(default_factory=MemoryPersistenceConfig)
    judge_config: JudgeConfig = field(default_factory=JudgeConfig)
    writeback_config: WritebackConfig = field(default_factory=WritebackConfig)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_assist_mode": self.base_assist_mode,
            "temporal_shards_enabled": bool(self.temporal_shards_enabled),
            "path_prior_source": self.path_prior_source,
            "fallback_policy": self.fallback_policy,
            "memory_config": self.memory_config.to_dict(),
            "judge_config": self.judge_config.to_dict(),
            "writeback_config": self.writeback_config.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "OverlayReasonerConfig":
        payload = dict(payload or {})
        return cls(
            base_assist_mode=_clean_text(payload.get("base_assist_mode", "")) or "structured_prior",
            temporal_shards_enabled=bool(payload.get("temporal_shards_enabled", False)),
            path_prior_source=_clean_text(payload.get("path_prior_source", "")) or "base_and_graph",
            fallback_policy=_clean_text(payload.get("fallback_policy", "")) or "disabled",
            memory_config=MemoryPersistenceConfig.from_dict(payload.get("memory_config")),
            judge_config=JudgeConfig.from_dict(payload.get("judge_config")),
            writeback_config=WritebackConfig.from_dict(payload.get("writeback_config")),
        )


@dataclass(slots=True)
class StructuredReasoningPrior:
    source_reasoner: str = ""
    candidate_paths: List[Dict[str, Any]] = field(default_factory=list)
    candidate_facts: List[Dict[str, Any]] = field(default_factory=list)
    candidate_scores: List[Dict[str, Any]] = field(default_factory=list)
    runtime_hints: Dict[str, Any] = field(default_factory=dict)
    prior_sources: List[str] = field(default_factory=list)

    @classmethod
    def from_response(cls, response: AdapterResponse | None) -> "StructuredReasoningPrior":
        if response is None:
            return cls()
        metadata = dict(response.metadata or {})
        trace = dict(response.trace or {})
        candidate_paths: List[Dict[str, Any]] = [dict(item) for item in list(response.paths or [])]
        trace_maze_paths = [dict(item) for item in list(trace.get("maze_paths", []) or []) if isinstance(item, dict)]
        route_hypotheses = [dict(item) for item in list(metadata.get("route_hypotheses", []) or []) if isinstance(item, dict)]
        if trace_maze_paths:
            candidate_paths.extend(trace_maze_paths)
        candidate_scores = [dict(item) for item in list(response.candidate_scores or []) if isinstance(item, dict)]
        if route_hypotheses:
            candidate_scores.extend(route_hypotheses)
        prior_sources: List[str] = []
        if response.paths:
            prior_sources.append("response_paths")
        if trace_maze_paths:
            prior_sources.append("maze_paths")
        if route_hypotheses:
            prior_sources.append("route_hypotheses")
        if response.facts:
            prior_sources.append("response_facts")
        return cls(
            source_reasoner=_clean_text(response.reasoner_name),
            candidate_paths=candidate_paths,
            candidate_facts=[dict(item) for item in list(response.facts or []) if isinstance(item, dict)],
            candidate_scores=candidate_scores,
            runtime_hints={
                "reasoner_name": response.reasoner_name,
                "memory_name": response.memory_name,
                "trace": trace,
                "metadata": metadata,
            },
            prior_sources=prior_sources,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_reasoner": self.source_reasoner,
            "candidate_paths": list(self.candidate_paths),
            "candidate_facts": list(self.candidate_facts),
            "candidate_scores": list(self.candidate_scores),
            "runtime_hints": dict(self.runtime_hints),
            "prior_sources": list(self.prior_sources),
        }
