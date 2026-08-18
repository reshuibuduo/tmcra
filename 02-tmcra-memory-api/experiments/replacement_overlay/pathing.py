from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import networkx as nx

from experiments.replacement.adapters.base import AdapterResponse, MemoryRetrieval

from .contracts import OverlayReasonerConfig, StructuredReasoningPrior
from .judge import JudgmentDecision
from .intent import QueryIntent


_ZH_FROM = "\u4ece"
_ZH_TO = "\u5230"
_ZH_THROUGH = "\u7ecf\u8fc7"
_ZH_VIA = "\u901a\u8fc7"
_ZH_REMOVE = "\u79fb\u9664"
_ZH_REMOVE_ALT = "\u53bb\u6389"
_ZH_WITHOUT = "\u5982\u679c\u6ca1\u6709"
_TEMPORAL_PATH_MARKERS = (
    "event chain",
    "state evolution",
    "timeline path",
    "temporal path",
    "change chain",
    "\u4e8b\u4ef6\u94fe",
    "\u72b6\u6001\u6f14\u5316",
    "\u53d8\u5316\u8fc7\u7a0b",
    "\u6f14\u53d8\u8fc7\u7a0b",
    "\u7ecf\u5386\u4e86\u54ea\u4e9b\u53d8\u5316",
)
_TEMPORAL_PATH_CATEGORIES = {"stage_state", "history", "event", "milestone"}


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _normalize(value: object) -> str:
    return _clean_text(value).lower()


def _clean_concept_text(value: object) -> str:
    return _clean_text(value).strip(" \t\r\n.,!?;:'\"`()[]{}")


def _dedupe(items: Iterable[object]) -> List[str]:
    results: List[str] = []
    seen = set()
    for item in items:
        text = _clean_concept_text(item)
        if not text:
            continue
        key = _normalize(text)
        if key in seen:
            continue
        seen.add(key)
        results.append(text)
    return results


@dataclass(slots=True)
class PathCandidate:
    source: str
    concepts: List[str]
    relations: List[str] = field(default_factory=list)
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    final_score: float = 0.0
    memory_ids: List[str] = field(default_factory=list)
    timeline_nodes: List[Dict[str, Any]] = field(default_factory=list)
    temporal_tunnels: List[Dict[str, Any]] = field(default_factory=list)

    def to_path(self) -> Dict[str, Any]:
        return {
            "mode": self.source,
            "source": self.source,
            "concepts": list(self.concepts),
            "relations": list(self.relations),
            "score": round(float(self.final_score), 6),
            "score_breakdown": {key: round(float(value), 6) for key, value in self.score_breakdown.items()},
            "timeline_nodes": list(self.timeline_nodes),
            "temporal_tunnels": list(self.temporal_tunnels),
            "time_start": int(self.timeline_nodes[0]["turn_index"]) if self.timeline_nodes else 0,
            "time_end": int(self.timeline_nodes[-1]["turn_index"]) if self.timeline_nodes else 0,
        }

    def to_candidate_score(self) -> Dict[str, Any]:
        label = " -> ".join(self.concepts) if self.concepts else self.source
        start = int(self.timeline_nodes[0]["turn_index"]) if self.timeline_nodes else 0
        end = int(self.timeline_nodes[-1]["turn_index"]) if self.timeline_nodes else 0
        return {
            "label": label,
            "path": label,
            "score": round(float(self.final_score), 6),
            "source": self.source,
            "time_start": start,
            "time_end": end,
            "temporal_tunnels": len(self.temporal_tunnels),
        }


class PathOverlayPlanner:
    def query_constraints(self, query: str, *, intent: QueryIntent) -> Dict[str, Any]:
        required_nodes = self._required_nodes(query)
        blocked_node = self._blocker(query) if intent.path_mode == "counterfactual" else ""
        query_kind_tags = _dedupe(
            [
                intent.kind,
                intent.history_kind,
                intent.path_mode,
                intent.summary_mode,
                "path" if intent.requires_path_reasoning else "",
                "summary" if intent.kind == "summary" else "",
                "history" if intent.history_kind != "none" else "",
            ]
        )
        return {
            "required_nodes": required_nodes,
            "blocked_nodes": [blocked_node] if blocked_node else [],
            "query_kind_tags": query_kind_tags,
        }

    def summarize_candidates(
        self,
        candidates: Sequence[PathCandidate],
        *,
        required_nodes: Sequence[str] = (),
        blocked_nodes: Sequence[str] = (),
    ) -> List[Dict[str, Any]]:
        summary: List[Dict[str, Any]] = []
        for index, item in enumerate(candidates):
            summary.append(
                {
                    "path_index": index,
                    "path_id": f"path:{index}",
                    "concepts": list(item.concepts),
                    "nodes": list(item.concepts),
                    "score": round(float(item.final_score), 6),
                    "source": item.source,
                    "required_nodes": list(required_nodes),
                    "blocked_nodes": list(blocked_nodes),
                    "temporal_tunnels": list(item.temporal_tunnels),
                    "critical_nodes": list(item.concepts[1:-1]),
                    "memory_ids": list(item.memory_ids),
                }
            )
        return summary

    def preview_candidates(
        self,
        query: str,
        *,
        intent: QueryIntent,
        retrieval: MemoryRetrieval,
        prior: StructuredReasoningPrior | None = None,
        config: OverlayReasonerConfig | None = None,
        base_response: AdapterResponse | None = None,
    ) -> List[PathCandidate]:
        prior = prior or StructuredReasoningPrior.from_response(base_response)
        config = config or OverlayReasonerConfig()
        if intent.kind != "path" and not prior.candidate_paths:
            return []
        temporal_enabled = self._should_enable_temporal_reasoning(query, intent=intent, retrieval=retrieval, prior=prior)
        candidates: List[PathCandidate] = []
        if config.path_prior_source in {"base", "base_and_graph"}:
            candidates.extend(self._prior_candidates(prior))
        if config.path_prior_source in {"graph", "base_and_graph"}:
            candidates.extend(self._graph_candidates(query, intent=intent, retrieval=retrieval, prior=prior, temporal_enabled=temporal_enabled))
        if not candidates and self._query_needs_gap_candidate(query):
            candidates.extend(self._memory_gap_candidates(query, intent=intent, retrieval=retrieval))
        if not candidates:
            return []
        required_nodes = self._required_nodes(query)
        history_ids = {hit.memory_id for hit in retrieval.history_hits}
        scored: List[PathCandidate] = []
        seen = set()
        for item in candidates:
            key = self._candidate_key(item, include_timeline=temporal_enabled)
            if not item.concepts or key in seen:
                continue
            seen.add(key)
            if intent.path_mode == "constrained" and required_nodes:
                normalized_concepts = {_normalize(value) for value in item.concepts}
                if not all(_normalize(value) in normalized_concepts for value in required_nodes):
                    continue
            breakdown = self._score(item, required_nodes=required_nodes, history_ids=history_ids, temporal_enabled=temporal_enabled)
            item.score_breakdown = breakdown
            item.final_score = (
                0.22 * breakdown["edge_support_score"]
                + 0.2 * breakdown["constraint_satisfaction_score"]
                + 0.12 * breakdown["critical_node_score"]
                + 0.14 * breakdown["path_consistency_score"]
                + 0.14 * breakdown["source_support_score"]
                + 0.1 * breakdown["temporal_tunnel_score"]
                + 0.1 * breakdown["temporal_order_score"]
                - breakdown["path_length_penalty"]
                - breakdown["history_conflict_penalty"]
            )
            scored.append(item)
        scored.sort(key=lambda item: item.final_score, reverse=True)
        return scored

    def finalize_candidates(
        self,
        query: str,
        *,
        intent: QueryIntent,
        candidates: Sequence[PathCandidate],
        judge_decision: JudgmentDecision | None = None,
    ) -> List[PathCandidate]:
        ordered = list(candidates)
        if not ordered:
            return []
        effective_mode = _clean_text(judge_decision.path_output_mode if judge_decision is not None else "") or intent.path_mode
        if judge_decision is not None and judge_decision.decision_valid and judge_decision.selected_path_indices:
            preferred: List[PathCandidate] = []
            seen = set()
            for index in judge_decision.selected_path_indices:
                if 0 <= int(index) < len(ordered) and int(index) not in seen:
                    preferred.append(ordered[int(index)])
                    seen.add(int(index))
            if preferred:
                ordered = [*preferred, *[item for idx, item in enumerate(ordered) if idx not in seen]]
        deduped: List[PathCandidate] = []
        seen_candidates = set()
        for item in ordered:
            key = self._candidate_key(item, include_timeline=effective_mode in {"temporal_path", "state_evolution_path"})
            if key in seen_candidates:
                continue
            seen_candidates.add(key)
            deduped.append(item)
        return deduped[: self._path_limit(effective_mode)]

    def build(
        self,
        query: str,
        *,
        intent: QueryIntent,
        retrieval: MemoryRetrieval,
        prior: StructuredReasoningPrior | None = None,
        config: OverlayReasonerConfig | None = None,
        base_response: AdapterResponse | None = None,
        judge_decision: JudgmentDecision | None = None,
    ) -> List[PathCandidate]:
        preview = self.preview_candidates(
            query,
            intent=intent,
            retrieval=retrieval,
            prior=prior,
            config=config,
            base_response=base_response,
        )
        return self.finalize_candidates(query, intent=intent, candidates=preview, judge_decision=judge_decision)

    def realize_paths(
        self,
        query: str,
        *,
        intent: QueryIntent,
        preview_candidates: Sequence[PathCandidate],
        final_candidates: Sequence[PathCandidate],
        judge_decision: JudgmentDecision | None = None,
    ) -> Dict[str, Any]:
        required_nodes = self._required_nodes(query)
        blocked_node = self._blocker(query) if intent.path_mode == "counterfactual" else ""
        preview_signatures = [tuple(_normalize(concept) for concept in item.concepts) for item in preview_candidates]
        final_signatures = [tuple(_normalize(concept) for concept in item.concepts) for item in final_candidates]
        selection_changed = bool(final_signatures and final_signatures != preview_signatures[: len(final_signatures)])
        selected_path_refs = [f"path:{index}" for index, _item in enumerate(final_candidates)]
        alternate_path_refs = [f"path:{index}" for index in range(1, len(final_candidates))]
        missing_bridge_refs = _dedupe(
            concept
            for item in final_candidates
            if item.source in {"graph_gap", "memory_gap"} or "missing_bridge" in item.relations
            for concept in item.concepts[1:-1]
        )
        return {
            "judge_path_applied": bool(judge_decision is not None and judge_decision.decision_valid and judge_decision.selected_path_indices),
            "path_selection_changed": selection_changed,
            "path_semantic_realized": self._path_semantic_realized(
                query,
                intent=intent,
                preview_candidates=preview_candidates,
                final_candidates=final_candidates,
                required_nodes=required_nodes,
                blocked_node=blocked_node,
            ),
            "selected_path_refs": selected_path_refs,
            "alternate_path_refs": alternate_path_refs,
            "blocked_node_refs": [blocked_node] if blocked_node else [],
            "missing_bridge_refs": missing_bridge_refs,
            "selected_path_indices": list(judge_decision.selected_path_indices) if judge_decision is not None else [],
        }

    def _prior_candidates(self, prior: StructuredReasoningPrior) -> List[PathCandidate]:
        results: List[PathCandidate] = []
        for item in list(prior.candidate_paths or []):
            concepts = [str(value) for value in item.get("concepts", []) or [] if _clean_text(value)]
            if not concepts:
                raw_path = _clean_text(item.get("path", ""))
                if raw_path and "->" in raw_path:
                    concepts = [part.strip() for part in raw_path.split("->") if _clean_text(part)]
            if not concepts:
                continue
            timeline_nodes = list(item.get("timeline_nodes", []) or [])
            results.append(
                PathCandidate(
                    source=str(item.get("mode", item.get("source", "prior")) or "prior"),
                    concepts=concepts,
                    relations=[str(value) for value in item.get("relations", []) or [] if _clean_text(value)],
                    final_score=float(item.get("score", 0.0) or 0.0),
                    memory_ids=_dedupe([str(value) for value in item.get("memory_ids", []) or [] if _clean_text(value)]),
                    timeline_nodes=timeline_nodes,
                    temporal_tunnels=list(item.get("temporal_tunnels", []) or []),
                )
            )
        return results

    def _graph_candidates(
        self,
        query: str,
        *,
        intent: QueryIntent,
        retrieval: MemoryRetrieval,
        prior: StructuredReasoningPrior,
        temporal_enabled: bool,
    ) -> List[PathCandidate]:
        graph = nx.DiGraph()
        concept_turns: Dict[str, set[int]] = {}

        def register_node(concept: str, turn_index: int) -> Tuple[str, int]:
            clean_concept = _clean_concept_text(concept)
            clean_turn = int(turn_index or 0) if temporal_enabled else 0
            concept_turns.setdefault(clean_concept, set()).add(clean_turn)
            graph.add_node((clean_concept, clean_turn), concept=clean_concept, turn_index=clean_turn)
            return clean_concept, clean_turn

        relation_sources: List[Dict[str, Any]] = []
        for relation in [*list(retrieval.relations or []), *list(prior.candidate_facts or [])]:
            if self._should_include_relation_source(relation):
                relation_sources.append(dict(relation))
        for item in list(prior.candidate_paths or []):
            concepts = [str(value) for value in item.get("concepts", []) or [] if _clean_text(value)]
            relations = [str(value) for value in item.get("relations", []) or [] if _clean_text(value)]
            if len(concepts) < 2:
                continue
            for index, (left_concept, right_concept) in enumerate(zip(concepts[:-1], concepts[1:])):
                relation_sources.append(
                    {
                        "from": left_concept,
                        "to": right_concept,
                        "relation": relations[index] if index < len(relations) else "maze_edge",
                        "weight": max(0.15, min(0.85, 0.75 - (float(item.get("score", 0.5) or 0.5) * 0.2))),
                        "memory_id": "",
                        "source": item.get("mode", item.get("source", "prior_path")),
                    }
                )
        for relation in relation_sources:
            src = _clean_concept_text(relation.get("from", ""))
            dst = _clean_concept_text(relation.get("to", ""))
            rel = _clean_text(relation.get("relation", "related_to"))
            turn_index = int(relation.get("turn_index", 0) or 0)
            if src and dst:
                left = register_node(src, turn_index)
                right = register_node(dst, turn_index)
                graph.add_edge(
                    left,
                    right,
                    relation=rel,
                    weight=float(relation.get("weight", 0.5) or 0.5),
                    memory_id=_clean_text(relation.get("memory_id", "")),
                )

        for hit in retrieval.hits:
            if hit.relation != "path_edge" or len(hit.anchors) < 2:
                continue
            src = _clean_concept_text(hit.anchors[0])
            dst = _clean_concept_text(hit.anchors[1])
            if not src or not dst:
                continue
            left = register_node(src, int(hit.turn_index or 0))
            right = register_node(dst, int(hit.turn_index or 0))
            graph.add_edge(
                left,
                right,
                relation="path_edge",
                weight=max(0.2, min(1.0, float(hit.score) or 0.5)),
                memory_id=hit.memory_id,
            )

        if temporal_enabled:
            for concept, turns in concept_turns.items():
                ordered_turns = sorted(turns)
                for left_turn, right_turn in zip(ordered_turns, ordered_turns[1:]):
                    penalty = max(0.15, min(0.8, 0.12 + ((right_turn - left_turn) * 0.02)))
                    left = (concept, left_turn)
                    right = (concept, right_turn)
                    graph.add_edge(left, right, relation="temporal_tunnel", weight=penalty, memory_id="")
                    graph.add_edge(right, left, relation="temporal_tunnel", weight=penalty, memory_id="")

        if graph.number_of_edges() == 0:
            return []

        source_concept, target_concept = self._endpoints(query, intent=intent, retrieval=retrieval)
        if not source_concept or not target_concept:
            return []
        blocker = self._blocker(query) if intent.path_mode == "counterfactual" else ""
        if blocker:
            graph = graph.copy()
            for node in list(graph.nodes):
                concept, _turn = node
                if _normalize(concept) == _normalize(blocker):
                    graph.remove_node(node)

        source_nodes = [node for node in graph.nodes if _normalize(node[0]) == _normalize(source_concept)]
        target_nodes = [node for node in graph.nodes if _normalize(node[0]) == _normalize(target_concept)]
        if not source_nodes or not target_nodes:
            return []

        super_source = ("__overlay_source__", 0)
        super_target = ("__overlay_target__", 0)
        work = graph.copy()
        work.add_node(super_source, concept="__overlay_source__", turn_index=0)
        work.add_node(super_target, concept="__overlay_target__", turn_index=0)
        for node in source_nodes:
            work.add_edge(super_source, node, relation="entry", weight=0.0, memory_id="")
        for node in target_nodes:
            work.add_edge(node, super_target, relation="exit", weight=0.0, memory_id="")

        try:
            path_iter = nx.shortest_simple_paths(work, super_source, super_target, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return self._gap_candidates(work, source_nodes=source_nodes, target_nodes=target_nodes) if self._query_needs_gap_candidate(query) else []

        results: List[PathCandidate] = []
        try:
            preview_limit = self._preview_limit(intent.path_mode)
            for index, raw_path in enumerate(path_iter):
                if index >= preview_limit:
                    break
                path_nodes = [node for node in raw_path if node not in {super_source, super_target}]
                candidate = self._candidate_from_nodes(path_nodes, work)
                if candidate is not None:
                    results.append(candidate)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return self._gap_candidates(work, source_nodes=source_nodes, target_nodes=target_nodes) if self._query_needs_gap_candidate(query) else []
        if not results:
            return self._gap_candidates(work, source_nodes=source_nodes, target_nodes=target_nodes) if self._query_needs_gap_candidate(query) else []
        return results

    def empty_preview_reason(
        self,
        query: str,
        *,
        intent: QueryIntent,
        retrieval: MemoryRetrieval,
        prior: StructuredReasoningPrior | None = None,
    ) -> str:
        prior = prior or StructuredReasoningPrior()
        relation_sources = [relation for relation in list(retrieval.relations or []) if self._should_include_relation_source(relation)]
        path_edge_hits = [hit for hit in retrieval.hits if hit.relation == "path_edge" and len(hit.anchors) >= 2]
        if not relation_sources and not path_edge_hits and not list(prior.candidate_paths or []):
            return "no_relation_sources"
        source_concept, target_concept = self._endpoints(query, intent=intent, retrieval=retrieval)
        if not source_concept or not target_concept:
            return "endpoint_not_inferred"
        concept_pool = {
            _normalize(value)
            for relation in relation_sources
            for value in (relation.get("from", ""), relation.get("to", ""))
            if _clean_text(value)
        }
        concept_pool.update(_normalize(anchor) for hit in path_edge_hits for anchor in hit.anchors if _clean_text(anchor))
        concept_pool.update(
            _normalize(concept)
            for item in list(prior.candidate_paths or [])
            for concept in list(item.get("concepts", []) or [])
            if _clean_text(concept)
        )
        if concept_pool and (_normalize(source_concept) not in concept_pool or _normalize(target_concept) not in concept_pool):
            return "endpoint_not_grounded"
        if self._query_needs_gap_candidate(query):
            return "gap_candidate_unavailable"
        return "no_path_found"

    def _should_enable_temporal_reasoning(self, query: str, *, intent: QueryIntent, retrieval: MemoryRetrieval, prior: StructuredReasoningPrior) -> bool:
        if intent.kind != "path":
            return False
        lowered = _normalize(query)
        if intent.history_kind == "timeline":
            return True
        if any(marker in lowered for marker in _TEMPORAL_PATH_MARKERS):
            return True
        category_hints = {_normalize(item) for item in intent.category_hints}
        if category_hints & _TEMPORAL_PATH_CATEGORIES and any(hint in {"timeline", "previous", "earliest", "latest", "after"} for hint in intent.temporal_hints):
            return True
        for hit in list(retrieval.hits or []):
            if hit.category in _TEMPORAL_PATH_CATEGORIES and int(hit.turn_index or 0) > 0:
                if hit.state != "active" or any(hint in {"timeline", "previous", "earliest", "latest", "after"} for hint in intent.temporal_hints):
                    return True
        for item in list(prior.candidate_paths or []):
            if list(item.get("temporal_tunnels", []) or []):
                return True
        return False

    def _candidate_from_nodes(self, path_nodes: Sequence[Tuple[str, int]], graph: nx.DiGraph) -> PathCandidate | None:
        if len(path_nodes) < 2:
            return None
        concepts: List[str] = []
        relations: List[str] = []
        memory_ids: List[str] = []
        timeline_nodes: List[Dict[str, Any]] = []
        temporal_tunnels: List[Dict[str, Any]] = []

        previous_concept = ""
        for concept, turn_index in path_nodes:
            timeline_nodes.append({"concept": concept, "turn_index": int(turn_index)})
            if _normalize(previous_concept) != _normalize(concept):
                concepts.append(concept)
                previous_concept = concept

        for left, right in zip(path_nodes, path_nodes[1:]):
            edge = graph.get_edge_data(left, right) or {}
            relation = str(edge.get("relation", "related_to"))
            relations.append(relation)
            memory_id = _clean_text(edge.get("memory_id", ""))
            if memory_id:
                memory_ids.append(memory_id)
            if relation == "temporal_tunnel":
                temporal_tunnels.append(
                    {
                        "concept": left[0],
                        "from_turn": int(left[1]),
                        "to_turn": int(right[1]),
                    }
                )

        return PathCandidate(
            source="graph_temporal" if temporal_tunnels else "graph",
            concepts=concepts,
            relations=relations,
            memory_ids=_dedupe(memory_ids),
            timeline_nodes=timeline_nodes,
            temporal_tunnels=temporal_tunnels,
        )

    def _gap_candidates(
        self,
        graph: nx.DiGraph,
        *,
        source_nodes: Sequence[Tuple[str, int]],
        target_nodes: Sequence[Tuple[str, int]],
    ) -> List[PathCandidate]:
        forward_path = self._best_partial_path(graph, starts=source_nodes, banned=set(target_nodes))
        backward_path = self._best_partial_path(graph.reverse(copy=False), starts=target_nodes, banned=set(source_nodes), reverse_result=True)
        if not forward_path and not backward_path:
            return []
        candidate = self._candidate_from_gap_paths(graph, forward_path=forward_path, backward_path=backward_path)
        return [candidate] if candidate is not None else []

    def _best_partial_path(
        self,
        graph: nx.DiGraph,
        *,
        starts: Sequence[Tuple[str, int]],
        banned: set[Tuple[str, int]],
        reverse_result: bool = False,
    ) -> List[Tuple[str, int]]:
        best: List[Tuple[str, int]] = []
        for start in starts:
            try:
                paths = nx.single_source_dijkstra_path(graph, start, weight="weight")
            except Exception:
                continue
            for end_node, path in paths.items():
                if end_node in banned or len(path) < 2:
                    continue
                candidate = list(reversed(path)) if reverse_result else list(path)
                if len(candidate) > len(best):
                    best = candidate
        return best

    def _candidate_from_gap_paths(
        self,
        graph: nx.DiGraph,
        *,
        forward_path: Sequence[Tuple[str, int]],
        backward_path: Sequence[Tuple[str, int]],
    ) -> PathCandidate | None:
        merged: List[Tuple[str, int]] = list(forward_path or [])
        if backward_path:
            if not merged:
                merged = list(backward_path)
            else:
                if merged[-1] == backward_path[0]:
                    merged.extend(list(backward_path)[1:])
                else:
                    merged.extend(list(backward_path))
        if len(merged) < 2:
            return None
        concepts: List[str] = []
        relations: List[str] = []
        memory_ids: List[str] = []
        timeline_nodes: List[Dict[str, Any]] = []
        previous_concept = ""
        for concept, turn_index in merged:
            timeline_nodes.append({"concept": concept, "turn_index": int(turn_index)})
            if _normalize(previous_concept) != _normalize(concept):
                concepts.append(concept)
                previous_concept = concept
        for left, right in zip(merged[:-1], merged[1:]):
            edge = graph.get_edge_data(left, right) or {}
            relation = str(edge.get("relation", "missing_bridge"))
            relations.append(relation)
            memory_id = _clean_text(edge.get("memory_id", ""))
            if memory_id:
                memory_ids.append(memory_id)
        return PathCandidate(
            source="graph_gap",
            concepts=concepts,
            relations=relations,
            memory_ids=_dedupe(memory_ids),
            timeline_nodes=timeline_nodes,
            temporal_tunnels=[],
        )

    def _memory_gap_candidates(self, query: str, *, intent: QueryIntent, retrieval: MemoryRetrieval) -> List[PathCandidate]:
        source_concept, target_concept = self._endpoints(query, intent=intent, retrieval=retrieval)
        if not source_concept or not target_concept:
            return []
        source_segments: List[Tuple[str, str, str]] = []
        target_segments: List[Tuple[str, str, str]] = []
        for hit in retrieval.hits:
            if hit.relation != "path_edge" or len(hit.anchors) < 2:
                continue
            left = _clean_concept_text(hit.anchors[0])
            right = _clean_concept_text(hit.anchors[1])
            if _normalize(left) == _normalize(source_concept):
                source_segments.append((left, right, hit.memory_id))
            if _normalize(right) == _normalize(target_concept):
                target_segments.append((left, right, hit.memory_id))
        if not source_segments and not target_segments:
            return []
        concepts: List[str] = []
        memory_ids: List[str] = []
        relations: List[str] = []
        if source_segments:
            left, right, memory_id = source_segments[0]
            concepts.extend([left, right])
            relations.append("path_edge")
            if memory_id:
                memory_ids.append(memory_id)
        if target_segments:
            left, right, memory_id = target_segments[0]
            if not concepts:
                concepts.extend([left, right])
            else:
                if _normalize(concepts[-1]) != _normalize(left):
                    concepts.append(left)
                    relations.append("missing_bridge")
                concepts.append(right)
                relations.append("path_edge")
            if memory_id:
                memory_ids.append(memory_id)
        if len(concepts) < 2:
            return []
        timeline_nodes = [{"concept": concept, "turn_index": 0} for concept in concepts]
        return [PathCandidate(source="memory_gap", concepts=_dedupe(concepts), relations=relations, memory_ids=_dedupe(memory_ids), timeline_nodes=timeline_nodes)]

    def _query_needs_gap_candidate(self, query: str) -> bool:
        normalized = _normalize(query)
        return "what is missing" in normalized or ("missing" in normalized and "path" in normalized) or "complete path" in normalized

    def _endpoints(self, query: str, *, intent: QueryIntent, retrieval: MemoryRetrieval) -> Tuple[str, str]:
        query_text = _clean_text(query)
        lowered = _normalize(query_text)
        reverse_patterns = (
            r"(?:why|how)\s+is\s+(.+?)\s+(?:still\s+)?(?:tied|linked|connected)\s+back\s+to\s+(.+?)(?:[?.!,;:]|$)",
            r"(?:trace|link|connect)\s+(.+?)\s+back\s+to\s+(.+?)(?:[?.!,;:]|$)",
        )
        for pattern in reverse_patterns:
            match = re.search(pattern, query_text, flags=re.IGNORECASE)
            if match:
                target = _clean_concept_text(match.group(1))
                source = _clean_concept_text(match.group(2))
                if source and target:
                    return source, target
        patterns = (
            r"from\s+(.+?)\s+to\s+(.+?)(?:[\s?.!,;:]|$)",
            r"between\s+(.+?)\s+and\s+(.+?)(?:[\s?.!,;:]|$)",
            rf"{_ZH_FROM}\s*(.+?)\s*{_ZH_TO}\s*(.+?)(?:[\s?.!,;:]|$)",
        )
        for pattern in patterns:
            match = re.search(pattern, query_text, flags=re.IGNORECASE)
            if match:
                source = _clean_concept_text(match.group(1))
                target = _clean_concept_text(match.group(2))
                if source and target:
                    return source, target
        if len(intent.entity_hints) >= 2:
            return _clean_concept_text(intent.entity_hints[0]), _clean_concept_text(intent.entity_hints[1])
        anchored_hits = [hit for hit in retrieval.hits if hit.relation == "path_edge" and len(hit.anchors) >= 2]
        candidate_concepts = _dedupe(
            [anchor for hit in anchored_hits for anchor in hit.anchors]
            + [
                relation.get("from", "")
                for relation in retrieval.relations or []
                if self._should_include_relation_source(relation)
            ]
            + [
                relation.get("to", "")
                for relation in retrieval.relations or []
                if self._should_include_relation_source(relation)
            ]
        )
        mentioned: List[Tuple[int, str]] = []
        for concept in candidate_concepts:
            position = lowered.find(_normalize(concept))
            if position >= 0:
                mentioned.append((position, concept))
        if len(mentioned) >= 2:
            mentioned.sort(key=lambda item: item[0])
            return _clean_concept_text(mentioned[0][1]), _clean_concept_text(mentioned[-1][1])
        sources = _dedupe(hit.anchors[0] for hit in anchored_hits)
        targets = _dedupe(hit.anchors[1] for hit in anchored_hits)
        source_only = [concept for concept in sources if _normalize(concept) not in {_normalize(item) for item in targets}]
        target_only = [concept for concept in targets if _normalize(concept) not in {_normalize(item) for item in sources}]
        if source_only and target_only:
            return _clean_concept_text(source_only[0]), _clean_concept_text(target_only[0])
        return "", ""

    def _candidate_key(self, item: PathCandidate, *, include_timeline: bool) -> Tuple[Any, ...]:
        key: List[Any] = [
            tuple(_normalize(_clean_concept_text(node)) for node in item.concepts),
            tuple(_normalize(relation) for relation in item.relations),
        ]
        if include_timeline:
            key.append(
                tuple(
                    (_normalize(_clean_concept_text(entry.get("concept", ""))), int(entry.get("turn_index", 0) or 0))
                    for entry in item.timeline_nodes
                )
            )
        return tuple(key)

    def _should_include_relation_source(self, relation: Dict[str, Any]) -> bool:
        src = _clean_concept_text(relation.get("from", ""))
        dst = _clean_concept_text(relation.get("to", ""))
        rel = _normalize(relation.get("relation", ""))
        if not src or not dst:
            return False
        if src.startswith("slot::") or rel == "active_in_slot":
            return False
        if rel == "path_edge" and (" leads to " in _normalize(dst) or dst.startswith("slot::")):
            return False
        return True

    def _required_nodes(self, query: str) -> List[str]:
        patterns = (
            r"\bvia\s+([a-z0-9_.-]+)",
            r"\bthrough\s+([a-z0-9_.-]+)",
            r"\bmust include\s+([a-z0-9_.-]+)",
            rf"{_ZH_THROUGH}\s*([a-z0-9_.-]+)",
            rf"{_ZH_VIA}\s*([a-z0-9_.-]+)",
        )
        required: List[str] = []
        lowered = _normalize(query)
        for pattern in patterns:
            match = re.search(pattern, lowered, flags=re.IGNORECASE)
            if match:
                required.append(match.group(1))
        return _dedupe(required)

    def _blocker(self, query: str) -> str:
        patterns = (
            r"\bwithout\s+([a-z0-9_.-]+)",
            r"\bremove\s+([a-z0-9_.-]+)",
            r"\bremoved\s+([a-z0-9_.-]+)",
            rf"{_ZH_REMOVE}\s*([a-z0-9_.-]+)",
            rf"{_ZH_REMOVE_ALT}\s*([a-z0-9_.-]+)",
            rf"{_ZH_WITHOUT}\s*([a-z0-9_.-]+)",
        )
        lowered = _normalize(query)
        for pattern in patterns:
            match = re.search(pattern, lowered, flags=re.IGNORECASE)
            if match:
                return match.group(1)
        return ""

    def _score(self, item: PathCandidate, *, required_nodes: Sequence[str], history_ids: set[str], temporal_enabled: bool) -> Dict[str, float]:
        non_tunnel_edges = [relation for relation in item.relations if relation != "temporal_tunnel"]
        normalized_concepts = [_normalize(value) for value in item.concepts]
        edge_support = 1.0 if non_tunnel_edges else 0.0
        constraint_satisfaction = 1.0 if not required_nodes else float(all(_normalize(node) in normalized_concepts for node in required_nodes))
        critical_node_score = min(1.0, len(item.concepts) / 4.0)
        source_support = self._source_support(item)
        repeated_penalty = max(0, len(item.timeline_nodes) - len(item.concepts))
        path_consistency = max(0.35, 1.0 - (repeated_penalty * 0.08))
        path_length_penalty = max(0.0, (len(item.timeline_nodes) - 5) * 0.03)
        history_conflict_penalty = 0.12 if any(memory_id in history_ids for memory_id in item.memory_ids) else 0.0
        temporal_tunnel_score = min(1.0, len(item.temporal_tunnels) * 0.5) if temporal_enabled else 0.0
        temporal_order_score = self._temporal_order_score(item.timeline_nodes) if temporal_enabled else 0.0
        return {
            "edge_support_score": edge_support,
            "constraint_satisfaction_score": constraint_satisfaction,
            "critical_node_score": critical_node_score,
            "source_support_score": source_support,
            "path_consistency_score": path_consistency,
            "path_length_penalty": path_length_penalty,
            "history_conflict_penalty": history_conflict_penalty,
            "temporal_tunnel_score": temporal_tunnel_score,
            "temporal_order_score": temporal_order_score,
        }

    def _source_support(self, item: PathCandidate) -> float:
        source = _normalize(item.source)
        if source in {"forward", "reverse", "boundary", "maze", "graph_temporal"}:
            return 1.0
        if "maze" in source:
            return 0.95
        if source in {"graph_gap", "memory_gap"}:
            return 0.68
        if source in {"graph", "graph_temporal"}:
            return 0.82
        if "prior" in source:
            return 0.72
        return 0.55 if item.memory_ids else 0.4

    def _temporal_order_score(self, timeline_nodes: Sequence[Dict[str, Any]]) -> float:
        if len(timeline_nodes) < 2:
            return 0.0
        turns = [int(entry.get("turn_index", 0) or 0) for entry in timeline_nodes]
        if all(left <= right for left, right in zip(turns, turns[1:])):
            return 1.0
        if all(left >= right for left, right in zip(turns, turns[1:])):
            return 0.7
        return 0.35

    def _path_limit(self, path_mode: str) -> int:
        if path_mode == "multi":
            return 4
        if path_mode in {"constrained", "counterfactual", "temporal_path", "state_evolution_path"}:
            return 2
        return 1

    def _preview_limit(self, path_mode: str) -> int:
        if path_mode == "multi":
            return 6
        if path_mode in {"constrained", "counterfactual", "temporal_path", "state_evolution_path"}:
            return 4
        return 4

    def _path_semantic_realized(
        self,
        query: str,
        *,
        intent: QueryIntent,
        preview_candidates: Sequence[PathCandidate],
        final_candidates: Sequence[PathCandidate],
        required_nodes: Sequence[str],
        blocked_node: str,
    ) -> bool:
        if self._query_needs_gap_candidate(query):
            return bool(
                final_candidates
                and any(item.source in {"graph_gap", "memory_gap"} or "missing_bridge" in item.relations for item in final_candidates)
            )
        if not final_candidates:
            return False
        if required_nodes:
            normalized_required = {_normalize(node) for node in required_nodes if _clean_text(node)}
            if normalized_required and not any(
                normalized_required <= {_normalize(concept) for concept in item.concepts}
                for item in final_candidates
            ):
                return False
        if blocked_node:
            blocked = _normalize(blocked_node)
            if blocked and not any(blocked not in {_normalize(concept) for concept in item.concepts} for item in final_candidates):
                return False
        normalized_query = _normalize(query)
        multi_requested = bool(
            intent.path_mode == "multi"
            or "multiple path" in normalized_query
            or "multiple paths" in normalized_query
            or "multi path" in normalized_query
            or "multibranch" in normalized_query
            or "branch" in normalized_query
        )
        if multi_requested and len(preview_candidates) >= 2:
            return len(final_candidates) >= 2
        return True
