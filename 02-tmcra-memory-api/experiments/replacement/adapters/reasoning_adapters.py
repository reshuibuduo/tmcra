from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import networkx as nx

from core.concept_graph import ConceptGraph
from core.concept_memory import ConceptMemory
from core.local_concept_extractor import LocalConceptExtractor
from core.maze_engine import MazePath, TriMazeEngine
from experiments.replacement.memory_graph import infer_category_hints, infer_history_mode

from .base import AdapterResponse, LLMProfile, MemoryAdapter, MemoryHit, MemoryRetrieval, ReasoningAdapter

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None


_PATH_MARKERS = ("path", "route", "connect", "why", "tied", "chain", "路径", "连接", "链路")
_HISTORY_MARKERS = ("history", "previous", "earlier", "before", "old", "prior", "之前", "以前", "历史")


_MULTI_PATH_MARKERS = ("multiple paths", "different paths", "another path", "all paths", "two paths")
_CONSTRAINT_PATH_MARKERS = ("must include", "through", "via")
_COUNTERFACTUAL_MARKERS = ("without", "were removed", "is removed", "remove", "removed")
_PATH_QUERY_PATTERNS = (
    r"\bwhat path\b",
    r"\bwhich path\b",
    r"\bfind (?:the )?(?:path|route)\b",
    r"\bshow (?:the )?(?:path|route)\b",
    r"\btrace (?:the )?(?:path|route|chain)\b",
    r"\bmap (?:the )?(?:path|route|chain)\b",
    r"\bgive (?:the )?(?:path|route|chain|branches?)\b",
    r"\blist (?:the )?(?:path|route|chain)\b",
    r"\bexplain (?:the )?(?:path|route|chain)\b",
    r"\b(?:path|route|chain)\b.*\b(?:from|to|between|connects?|through|via|must include)\b",
    r"\bconnects?\b.*\bto\b",
    r"\bhow does\b.*\b(?:propagate|reach|connect|flow)\b",
    r"\bwhy\b.*\b(?:tied|connected|linked)\b",
    r"\bcausal chain\b",
    r"\bcomplete path\b",
    r"\bbranches?\b.*\bfrom\b.*\bto\b",
)
_PATH_QUERY_CHINESE_PATTERNS = (
    r"(?:从.+到.+)(?:路径|链路|路线)",
    r"(?:路径|链路|路线).*(?:从|到|连接|经过|通过)",
    r"(?:多条|不同|另一条).*(?:路径|链路|路线)",
    r"(?:没有|去掉|移除).*(?:路径|链路|路线)",
    r"(?:因果链|传播链)",
)


_SUMMARY_MARKERS = (
    "summary",
    "overall",
    "active constraints",
    "protocol",
    "\u603b\u7ed3",
    "\u6982\u62ec",
    "\u5f53\u524d\u7ea6\u675f",
    "\u534f\u8bae",
)
_HISTORY_MARKERS = _HISTORY_MARKERS + (
    "\u4e4b\u524d",
    "\u4ee5\u524d",
    "\u5386\u53f2",
    "\u8986\u76d6\u524d",
    "\u4e0a\u4e00\u7248",
)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text(value: Any) -> str:
    return _clean_text(value).lower()


def _query_is_temporal(value: Any) -> bool:
    return bool(re.search(r"\bwhen\b|\bdate\b|\bday\b|\bmonth\b|\byear\b|\btime\b", _normalize_text(value)))


def _query_semantic_slot(value: Any) -> str:
    lowered = _normalize_text(value)
    if "identity" in lowered or "identify as" in lowered:
        return "identity"
    if "research" in lowered:
        return "research_topic"
    if any(marker in lowered for marker in ("study", "major", "degree", "school", "college", "university")):
        return "education"
    if any(marker in lowered for marker in ("job", "work", "occupation", "career")):
        return "occupation"
    return "event_time" if _query_is_temporal(value) else "event"


def _snippet_priority(query: str, hit: MemoryHit) -> tuple[float, float, int]:
    metadata = dict(getattr(hit, "metadata", {}) or {})
    semantic_slot = _normalize_text(metadata.get("semantic_slot", ""))
    source_kind = _normalize_text(getattr(hit, "source_kind", ""))
    time_granularity = _normalize_text(metadata.get("time_granularity", ""))
    target_status = _normalize_text(metadata.get("target_status", ""))
    match_reason = _normalize_text(metadata.get("match_reason", ""))
    query_semantic_slot = _query_semantic_slot(query)
    priority = 0.0
    learned_score = float(
        metadata.get(
            "effective_path_score",
            metadata.get("hybrid_score", getattr(hit, "score", 0.0)),
        )
        or 0.0
    )
    if _query_is_temporal(query):
        if semantic_slot == "event_time":
            priority += 4.0
        if source_kind == "public_dialog_time":
            priority += 3.0
        if time_granularity in {"day", "month", "year", "relative_day_reference", "relative_week_reference"}:
            priority += 2.0
        if target_status == "planned" and any(marker in _normalize_text(query) for marker in ("plan", "planning", "going to", "will ")):
            priority += 0.8
    elif query_semantic_slot in {"identity", "research_topic", "education", "occupation"}:
        if semantic_slot == query_semantic_slot:
            priority += 4.0
        if source_kind == "public_dialog_profile":
            priority += 2.0
    else:
        if source_kind == "public_dialog_event":
            priority += 2.0
    if "speaker" in match_reason:
        priority += 0.4
    if "event_signature" in match_reason:
        priority += 0.4
    if "semantic_slot" in match_reason:
        priority += 0.4
    if "speaker" in match_reason and "event_signature" in match_reason:
        priority += 0.6
    if source_kind == "public_dialog_profile" and query_semantic_slot == "event_time":
        priority -= 0.8
    if source_kind == "public_dialog_time" and query_semantic_slot in {"identity", "research_topic", "education", "occupation"}:
        priority -= 0.8
    query_tokens = {
        token
        for token in _query_tokens(query)
        if len(token) > 2 and token not in {"when", "what", "which", "would", "likely", "about", "there", "their", "conversation", "mentioned"}
    }
    snippet_text = " ".join(
        [
            _clean_text(metadata.get("event_signature", "")),
            _clean_text(metadata.get("raw_text", "")),
            _clean_text(getattr(hit, "value", "")),
        ]
    ).lower()
    lexical_overlap = sum(1 for token in query_tokens if token and token in snippet_text)
    priority += min(1.2, float(lexical_overlap) * 0.3)
    return priority, learned_score, int(getattr(hit, "turn_index", 0) or 0)


def _render_compact_evidence_tuple(snippet: Dict[str, Any]) -> str:
    evidence = dict(snippet.get("evidence", {}) or {})
    parts = [
        f"rank={int(snippet.get('rank', 0) or 0)}",
        f"type={_clean_text(snippet.get('evidence_type', 'memory')) or 'memory'}",
    ]
    for label in ("speaker", "event", "time", "status", "profile", "source_turn"):
        value = _clean_text(evidence.get(label, ""))
        if value:
            parts.append(f"{label}={value}")
    return " | ".join(parts)


def _normalized_answer_type_scores(retrieval: MemoryRetrieval) -> Dict[str, float]:
    return {
        _normalize_text(answer_type): float(value or 0.0)
        for answer_type, value in dict(getattr(retrieval, "metadata", {}).get("answer_type_scores", {}) or {}).items()
        if _normalize_text(answer_type)
    }


def _soft_abstain_margin(retrieval: MemoryRetrieval) -> float:
    scores = _normalized_answer_type_scores(retrieval)
    abstain = float(scores.get("abstain", 0.0))
    best_non_abstain = max((value for key, value in scores.items() if key != "abstain"), default=0.0)
    return abstain - best_non_abstain


def _contains_cjk(value: Any) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", _clean_text(value)))


def _safe_weight(value: Any, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default


def _completion_usage_dict(completion: Any) -> Dict[str, int]:
    usage = getattr(completion, "usage", None)
    if usage is None and isinstance(completion, dict):
        usage = completion.get("usage")
    if usage is None:
        return {}
    if isinstance(usage, dict):
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or (prompt_tokens + completion_tokens))
    else:
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or (prompt_tokens + completion_tokens))
    usage_dict = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    return usage_dict if any(usage_dict.values()) else {}


def _normalize_answer_mode(value: str | None) -> str:
    return "transparent" if _normalize_text(value) == "transparent" else "natural"


def _dedupe_relations(relations: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    seen = set()
    for relation in relations:
        key = (relation.get("from"), relation.get("to"), relation.get("relation"))
        if key in seen:
            continue
        seen.add(key)
        results.append(relation)
    return results


def _dedupe_strings(values: Iterable[Any], *, max_items: int | None = None) -> List[str]:
    items: List[str] = []
    seen = set()
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(text)
        if max_items is not None and len(items) >= max_items:
            break
    return items


def _serialize_maze_path(path: MazePath, mode: str) -> Dict[str, Any]:
    return {
        "mode": mode,
        "concepts": [_clean_text(node.concept) for node in getattr(path, "nodes", []) if _clean_text(getattr(node, "concept", ""))],
        "relations": [_clean_text(edge.relation) for edge in getattr(path, "edges", []) if _clean_text(getattr(edge, "relation", ""))],
        "length": int(path.length),
        "score": round(float(path.score()), 6),
        "total_resistance": round(float(getattr(path, "total_resistance", 0.0)), 6),
        "has_memory": bool(getattr(path, "has_memory", False)),
        "has_tunneling": bool(getattr(path, "has_tunneling", False)),
        "has_expanded": bool(getattr(path, "has_expanded", False)),
    }


def _path_relations(paths: Sequence[MazePath] | None, source: str, *, max_paths: int = 2) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in (paths or [])[:max_paths]:
        for edge in getattr(path, "edges", [])[:6]:
            records.append(
                {
                    "from": _clean_text(edge.from_node.concept),
                    "to": _clean_text(edge.to_node.concept),
                    "relation": _clean_text(edge.relation),
                    "weight": round(max(0.05, min(1.0, 1.0 - float(edge.resistance))), 6),
                    "source": source,
                }
            )
    return records


def _render_transparent_answer(summary: str, *, facts: Sequence[Dict[str, Any]], paths: Sequence[Dict[str, Any]], memory_hits: Sequence[Dict[str, Any]], candidate_scores: Sequence[Dict[str, Any]], confidence: float) -> str:
    lines = [f"Answer: {summary}"]
    for index, fact in enumerate(list(facts)[:5], start=1):
        lines.append(
            f"Fact {index}: {fact.get('from', '')} -[{fact.get('relation', '')}]-> {fact.get('to', '')} "
            f"(score={_safe_weight(fact.get('weight', 0.5)):.2f})"
        )
    if paths:
        primary = paths[0]
        if primary.get("concepts"):
            lines.append(f"Path: {' -> '.join(primary.get('concepts', []))}")
        if primary.get("mode"):
            lines.append(f"Path mode: {primary.get('mode')}")
    if candidate_scores:
        rendered = []
        for item in list(candidate_scores)[:4]:
            label = _clean_text(item.get("label", "")) or _clean_text(item.get("path", ""))
            score = float(item.get("score", 0.0) or 0.0)
            rendered.append(f"{label or 'candidate'} ({score:.3f})")
        if rendered:
            lines.append("Candidates: " + " | ".join(rendered))
    if memory_hits:
        rendered_hits = [
            f"{item.get('slot_key') or item.get('category', 'memory')}={item.get('value', '')}"
            for item in list(memory_hits)[:4]
            if _clean_text(item.get("value", ""))
        ]
        if rendered_hits:
            lines.append("Memory hits: " + " | ".join(rendered_hits))
    lines.append(f"Confidence: {float(confidence):.3f}")
    return "\n".join(lines).strip()


def _render_natural_answer(*, summary: str, facts: Sequence[Dict[str, Any]], paths: Sequence[Dict[str, Any]], memory_hits: Sequence[Dict[str, Any]]) -> str:
    if summary:
        return summary
    if paths:
        concepts = list(paths[0].get("concepts", []) or [])
        if concepts:
            return f"Grounded route: {' -> '.join(concepts)}."
    if memory_hits:
        first = memory_hits[0]
        return f"Current evidence points to {first.get('value', '')}."
    if facts:
        first = facts[0]
        return f"Evidence suggests {first.get('from', '')} {first.get('relation', '')} {first.get('to', '')}."
    return "Insufficient grounded evidence."


def _memory_hit_dicts(retrieval: MemoryRetrieval) -> List[Dict[str, Any]]:
    return [hit.to_dict() for hit in retrieval.hits]


def _response_confidence_from_text(text: str, *, fallback: float = 0.35) -> float:
    length_bonus = min(0.2, max(0.0, len(_clean_text(text)) / 400.0))
    return max(0.05, min(0.9, fallback + length_bonus))


def _query_mentions_path(query: str) -> bool:
    lowered = _normalize_text(query)
    if not lowered:
        return False
    if any(re.search(pattern, lowered) for pattern in _PATH_QUERY_PATTERNS):
        return True
    if any(re.search(pattern, _clean_text(query)) for pattern in _PATH_QUERY_CHINESE_PATTERNS):
        return True
    if _query_requests_multiple_paths(query):
        return True
    if _query_requests_counterfactual(query) and any(marker in lowered for marker in ("path", "route", "chain")):
        return True
    return False


def _query_requests_multiple_paths(query: str) -> bool:
    lowered = _normalize_text(query)
    return any(marker in lowered for marker in _MULTI_PATH_MARKERS)


def _query_requests_counterfactual(query: str) -> bool:
    lowered = _normalize_text(query)
    return any(marker in lowered for marker in _COUNTERFACTUAL_MARKERS)


def _query_tokens(query: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", _normalize_text(query)))


def _query_requests_current_pair(query: str) -> bool:
    lowered = _normalize_text(query)
    markers = (
        "current",
        "active",
        "now",
        "historical",
        "both",
        "summary",
        "summarize",
        "current and previous",
        "which one is active",
        "褰撳墠",
        "鐜板湪",
        "鍘嗗彶",
        "鎬荤粨",
        "姒傛嫭",
    )
    return any(marker in lowered for marker in markers)


def _query_requests_current_pair_clean(query: str) -> bool:
    lowered = _normalize_text(query)
    clean_markers = (
        "\u5f53\u524d",
        "\u73b0\u5728",
        "\u603b\u7ed3",
        "\u5386\u53f2",
        "\u4e4b\u524d",
    )
    return _query_requests_current_pair(query) or any(marker in lowered for marker in clean_markers)


def _query_requests_current_pair(query: str) -> bool:
    lowered = _normalize_text(query)
    markers = (
        "current",
        "active",
        "now",
        "historical",
        "both",
        "summary",
        "summarize",
        "current and previous",
        "which one is active",
        "当前",
        "现在",
        "历史",
        "总结",
        "概括",
    )
    return any(marker in lowered for marker in markers)


def _query_requests_current_pair_clean(query: str) -> bool:
    lowered = _normalize_text(query)
    clean_markers = (
        "当前",
        "现在",
        "总结",
        "历史",
        "之前",
    )
    return _query_requests_current_pair(query) or any(marker in lowered for marker in clean_markers)


def _hit_query_score(hit: MemoryHit, query: str) -> float:
    query_text = _normalize_text(query)
    if not query_text:
        return float(hit.score)
    score = min(0.12, float(hit.score) * 0.1)
    value_text = _normalize_text(hit.value)
    if value_text and value_text in query_text:
        score += 0.35
    slot_text = _normalize_text(hit.slot_key).replace(".", " ")
    if slot_text and slot_text in query_text:
        score += 0.2
    query_tokens = _query_tokens(query)
    anchor_tokens: set[str] = set()
    for anchor in hit.anchors[:6]:
        anchor_text = _normalize_text(anchor)
        if not anchor_text:
            continue
        if anchor_text in query_text:
            score += 0.22
        anchor_tokens.update(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", anchor_text))
    if query_tokens and anchor_tokens:
        score += 0.42 * (len(query_tokens & anchor_tokens) / max(1, len(query_tokens)))
    return score


@dataclass(slots=True)
class QueryIntent:
    kind: str
    category_hint: str = ""
    category_hints: List[str] = field(default_factory=list)
    history_mode: bool = False
    transparent: bool = True


@dataclass(slots=True)
class RouteHypothesis:
    label: str
    concepts: List[str] = field(default_factory=list)
    relations: List[str] = field(default_factory=list)
    source: str = "graph_path"
    score: float = 0.0
    memory_ids: List[str] = field(default_factory=list)
    has_tunneling: bool = False

    def to_candidate_score(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "path": " -> ".join(self.concepts),
            "score": round(float(self.score), 6),
            "source": self.source,
            "has_tunneling": self.has_tunneling,
        }

    def to_path(self) -> Dict[str, Any]:
        return {
            "mode": self.source,
            "concepts": list(self.concepts),
            "relations": list(self.relations),
            "length": max(0, len(self.concepts) - 1),
            "score": round(float(self.score), 6),
            "has_tunneling": self.has_tunneling,
        }


@dataclass(slots=True)
class AnswerPlan:
    summary: str
    used_memory_ids: List[str] = field(default_factory=list)
    facts: List[Dict[str, Any]] = field(default_factory=list)
    paths: List[Dict[str, Any]] = field(default_factory=list)
    candidate_scores: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0


def _resolve_intent(query: str, *, answer_mode: str) -> QueryIntent:
    hints = infer_category_hints(query)
    path_requested = _query_mentions_path(query)
    if not path_requested:
        hints = [hint for hint in hints if hint != "path"]
    normalized_query = _normalize_text(query)
    history_mode = infer_history_mode(query) or any(marker in normalized_query for marker in _HISTORY_MARKERS)
    kind = "path" if path_requested else "slot"
    if history_mode:
        kind = "history" if kind != "path" else kind
    if path_requested:
        kind = "path"
    elif history_mode:
        kind = "history"
    elif not hints and any(token in _normalize_text(query) for token in ("summary", "overall", "active constraints", "protocol", "总结", "概括", "汇总")):
        kind = "summary"
    if kind == "slot" and any(token in normalized_query for token in _SUMMARY_MARKERS):
        kind = "summary"
    return QueryIntent(
        kind=kind,
        category_hint=next((hint for hint in hints if hint != "path"), ""),
        category_hints=[hint for hint in hints if hint != "path"],
        history_mode=history_mode,
        transparent=_normalize_answer_mode(answer_mode) == "transparent",
    )


def _relations_from_memory(retrieval: MemoryRetrieval) -> List[Dict[str, Any]]:
    records = list(retrieval.relations)
    for hit in retrieval.hits:
        anchors = _dedupe_strings(hit.anchors, max_items=4)
        if hit.relation == "path_edge" and len(anchors) >= 2:
            for left, right in zip(anchors[:-1], anchors[1:]):
                records.append(
                    {
                        "from": left,
                        "to": right,
                        "relation": "path_edge",
                        "weight": round(max(0.4, min(0.99, 0.5 + hit.score * 0.35)), 6),
                        "source": "memory_path_edge",
                        "memory_id": hit.memory_id,
                    }
                )
        if hit.anchors:
            records.append(
                {
                    "from": hit.anchors[0],
                    "to": hit.value,
                    "relation": hit.relation,
                    "weight": round(max(0.25, min(0.98, 0.42 + hit.score * 0.4)), 6),
                    "source": "memory_hit",
                    "memory_id": hit.memory_id,
                }
            )
    return _dedupe_relations(records)


def _build_relation_graph(relations: Sequence[Dict[str, Any]]) -> Tuple[nx.DiGraph, Dict[Tuple[str, str], Dict[str, Any]]]:
    graph = nx.DiGraph()
    lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for relation in relations:
        source = _clean_text(relation.get("from", ""))
        target = _clean_text(relation.get("to", ""))
        relation_name = _clean_text(relation.get("relation", "")) or "related_to"
        if not source or not target or source == target:
            continue
        weight = _safe_weight(relation.get("weight", 0.5))
        graph.add_edge(source, target, weight=weight, relation=relation_name)
        lookup[(source, target)] = {
            "from": source,
            "to": target,
            "relation": relation_name,
            "weight": weight,
            "source": relation.get("source", relation.get("source_kind", "relation")),
            "memory_id": relation.get("memory_id", ""),
        }
    return graph, lookup


def _query_concepts(query: str, extraction: Dict[str, Any], retrieval: MemoryRetrieval) -> List[str]:
    concepts = []
    for item in extraction.get("concepts", []) or []:
        if isinstance(item, dict):
            concepts.append(_clean_text(item.get("concept", "")))
    query_text = _normalize_text(query)
    for item in retrieval.concepts:
        concept = _clean_text(item.get("concept", ""))
        if concept and _normalize_text(concept) in query_text:
            concepts.append(concept)
    for hit in [*retrieval.active_hits, *retrieval.history_hits, *retrieval.hits]:
        if hit.value and _normalize_text(hit.value) in query_text:
            concepts.append(hit.value)
        for anchor in hit.anchors:
            if anchor and _normalize_text(anchor) in query_text:
                concepts.append(anchor)
    for relation in retrieval.relations:
        source = _clean_text(relation.get("from", ""))
        target = _clean_text(relation.get("to", ""))
        if source and _normalize_text(source) in query_text:
            concepts.append(source)
        if target and _normalize_text(target) in query_text:
            concepts.append(target)
    return _dedupe_strings(concepts, max_items=16)


def _graph_query_mentions(query: str, relation_graph: nx.DiGraph, *, extraction: Dict[str, Any], retrieval: MemoryRetrieval) -> List[str]:
    query_text = _normalize_text(query)
    graph_mentions = [
        node
        for node in relation_graph.nodes
        if _normalize_text(node) and _normalize_text(node) in query_text
    ]
    combined = [*sorted(graph_mentions, key=lambda item: (query_text.find(_normalize_text(item)), -len(item))), *_query_concepts(query, extraction, retrieval)]
    return _dedupe_strings(combined, max_items=16)


def _endpoint_pairs(query: str, *, extraction: Dict[str, Any], retrieval: MemoryRetrieval, relation_graph: nx.DiGraph) -> List[Tuple[str, str]]:
    query_text = _normalize_text(query)
    mentioned = [concept for concept in _graph_query_mentions(query, relation_graph, extraction=extraction, retrieval=retrieval) if relation_graph.has_node(concept)]
    mentioned.sort(key=lambda concept: (query_text.find(_normalize_text(concept)) if _normalize_text(concept) in query_text else 10**6, -len(concept)))
    prioritized: List[Tuple[str, str]] = []
    for marker in _CONSTRAINT_PATH_MARKERS:
        marker_index = query_text.find(marker)
        if marker_index < 0:
            continue
        prefix = query_text[:marker_index]
        prefix_mentions = [
            concept
            for concept in mentioned
            if _normalize_text(concept) and _normalize_text(concept) in prefix
        ]
        if len(prefix_mentions) >= 2:
            prioritized.extend([(prefix_mentions[0], prefix_mentions[-1]), (prefix_mentions[-1], prefix_mentions[0])])
            break
    if len(mentioned) >= 2:
        prioritized.extend([(mentioned[0], mentioned[-1]), (mentioned[-1], mentioned[0])])
    pairs: List[Tuple[str, str]] = []
    for index, source in enumerate(mentioned):
        for target in mentioned[index + 1 :]:
            if source != target:
                pairs.append((source, target))
    if pairs:
        ordered: List[Tuple[str, str]] = []
        seen = set()
        for source, target in [*prioritized, *pairs]:
            for pair in ((source, target), (target, source)):
                if pair in seen:
                    continue
                seen.add(pair)
                ordered.append(pair)
        return ordered[:8]
    if len(mentioned) >= 2:
        return [(mentioned[0], mentioned[-1])]
    hit_values = [hit.value for hit in retrieval.active_hits or retrieval.hits if relation_graph.has_node(hit.value)]
    if len(hit_values) >= 2:
        return [(hit_values[0], hit_values[-1])]
    return []


def _required_middle_concepts(query: str, *, extraction: Dict[str, Any], retrieval: MemoryRetrieval) -> List[str]:
    lowered = _normalize_text(query)
    if not any(marker in lowered for marker in _CONSTRAINT_PATH_MARKERS):
        return []
    candidates = _dedupe_strings(
        [
            *[item.get("concept", "") for item in extraction.get("concepts", []) or [] if isinstance(item, dict)],
            *[hit.value for hit in retrieval.hits],
            *[anchor for hit in retrieval.hits for anchor in hit.anchors],
            *[relation.get("from", "") for relation in retrieval.relations],
            *[relation.get("to", "") for relation in retrieval.relations],
        ],
        max_items=64,
    )
    matched: List[str] = []
    for marker in _CONSTRAINT_PATH_MARKERS:
        marker_index = lowered.find(marker)
        if marker_index < 0:
            continue
        segment = lowered[marker_index + len(marker) :]
        for candidate in sorted(candidates, key=len, reverse=True):
            normalized = _normalize_text(candidate)
            if normalized and normalized in segment:
                matched.append(candidate)
    if matched:
        filtered: List[str] = []
        for candidate in sorted(_dedupe_strings(matched, max_items=16), key=len, reverse=True):
            normalized = _normalize_text(candidate)
            if any(normalized in _normalize_text(existing) for existing in filtered):
                continue
            filtered.append(candidate)
        return filtered[:4]
    mentioned = _query_concepts(query, extraction, retrieval)
    if len(mentioned) <= 2:
        return []
    return _dedupe_strings(mentioned[1:-1], max_items=4)


def _graph_route_hypotheses(query: str, *, extraction: Dict[str, Any], retrieval: MemoryRetrieval) -> Tuple[List[RouteHypothesis], List[Dict[str, Any]]]:
    relations = _relations_from_memory(retrieval) + [
        {
            "from": _clean_text(item.get("from", "")),
            "to": _clean_text(item.get("to", "")),
            "relation": _clean_text(item.get("relation", "")),
            "weight": _safe_weight(item.get("weight", 0.5)),
            "source": "extraction",
        }
        for item in extraction.get("relations", []) or []
        if _clean_text(item.get("from", "")) and _clean_text(item.get("to", "")) and _clean_text(item.get("relation", ""))
    ]
    relations = _dedupe_relations(relations)
    graph, lookup = _build_relation_graph(relations)
    if not graph.nodes:
        return [], relations
    hypotheses: List[RouteHypothesis] = []
    required_middle = set(_required_middle_concepts(query, extraction=extraction, retrieval=retrieval))
    pairs = _endpoint_pairs(query, extraction=extraction, retrieval=retrieval, relation_graph=graph)
    for source, target in pairs[:6]:
        try:
            for path_index, concepts in enumerate(nx.shortest_simple_paths(graph, source, target, weight=None)):
                if path_index >= 3:
                    break
                if len(concepts) < 2 or len(concepts) > 6:
                    continue
                if required_middle and not required_middle.issubset(set(concepts)):
                    continue
                memory_ids: List[str] = []
                relation_names: List[str] = []
                score = 0.0
                for left, right in zip(concepts[:-1], concepts[1:]):
                    payload = lookup.get((left, right), {"from": left, "to": right, "relation": "related_to", "weight": 0.5, "source": "graph"})
                    relation_names.append(payload.get("relation", "related_to"))
                    score += float(payload.get("weight", 0.5) or 0.5)
                    memory_id = _clean_text(payload.get("memory_id", ""))
                    if memory_id:
                        memory_ids.append(memory_id)
                hypotheses.append(
                    RouteHypothesis(
                        label=f"{source} -> {target}",
                        concepts=list(concepts),
                        relations=relation_names,
                        source="graph_path",
                        score=score / max(1, len(concepts) - 1),
                        memory_ids=_dedupe_strings(memory_ids, max_items=8),
                    )
                )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
    hypotheses.sort(key=lambda item: (item.score, -len(item.concepts)), reverse=True)
    return hypotheses[:6], relations


def _counterfactual_blocker(query: str, *, extraction: Dict[str, Any], retrieval: MemoryRetrieval) -> str:
    query_text = _normalize_text(query)
    concepts = _query_concepts(query, extraction, retrieval)
    best_concept = ""
    best_distance = 10**9
    for concept in concepts:
        concept_text = _normalize_text(concept)
        concept_index = query_text.find(concept_text)
        if concept_index < 0:
            continue
        marker_positions = [query_text.rfind(marker, 0, concept_index) for marker in _COUNTERFACTUAL_MARKERS]
        marker_positions = [item for item in marker_positions if item >= 0]
        if not marker_positions:
            continue
        distance = concept_index - max(marker_positions)
        if distance < best_distance:
            best_distance = distance
            best_concept = concept
    return best_concept


def _counterfactual_plan(query: str, *, extraction: Dict[str, Any], retrieval: MemoryRetrieval) -> AnswerPlan | None:
    if not _query_requests_counterfactual(query):
        return None
    relations = _relations_from_memory(retrieval) + [
        {
            "from": _clean_text(item.get("from", "")),
            "to": _clean_text(item.get("to", "")),
            "relation": _clean_text(item.get("relation", "")),
            "weight": _safe_weight(item.get("weight", 0.5)),
            "source": "extraction",
        }
        for item in extraction.get("relations", []) or []
        if _clean_text(item.get("from", "")) and _clean_text(item.get("to", "")) and _clean_text(item.get("relation", ""))
    ]
    graph, lookup = _build_relation_graph(_dedupe_relations(relations))
    blocker = _counterfactual_blocker(query, extraction=extraction, retrieval=retrieval)
    if blocker and graph.has_node(blocker):
        graph = graph.copy()
        graph.remove_node(blocker)
    mentioned = [concept for concept in _graph_query_mentions(query, graph, extraction=extraction, retrieval=retrieval) if concept and concept != blocker and graph.has_node(concept)]
    if len(mentioned) < 2:
        return None
    source, target = mentioned[0], mentioned[-1]
    try:
        concepts = nx.shortest_path(graph, source, target)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        concepts = []
    if not concepts:
        return AnswerPlan(
            summary=f"Without {blocker}, no grounded path remains from {source} to {target}.",
            candidate_scores=[{"label": f"without {blocker}", "score": 0.78, "source": "counterfactual"}],
            confidence=0.78,
        )
    facts = [lookup.get((left, right), {"from": left, "to": right, "relation": "related_to", "weight": 0.5, "source": "counterfactual"}) for left, right in zip(concepts[:-1], concepts[1:])]
    return AnswerPlan(
        summary=f"Without {blocker}, a grounded path still exists: {' -> '.join(concepts)}.",
        facts=facts,
        paths=[{"mode": "counterfactual", "concepts": list(concepts), "relations": [str(item.get('relation', 'related_to')) for item in facts], "length": max(0, len(concepts) - 1), "score": 0.82, "has_tunneling": False}],
        candidate_scores=[{"label": f"without {blocker}", "path": ' -> '.join(concepts), "score": 0.82, "source": "counterfactual"}],
        confidence=0.82,
    )


def _pick_slot_hit(intent: QueryIntent, retrieval: MemoryRetrieval, *, query: str = "") -> MemoryHit | None:
    preferred = retrieval.active_hits if not intent.history_mode else (retrieval.history_hits or retrieval.hits)
    category_hints = list(intent.category_hints or ([intent.category_hint] if intent.category_hint else []))
    if category_hints:
        for category in category_hints:
            hit = _match_category_hit(category, (preferred, retrieval.hits), query=query)
            if hit:
                return hit
        for category in category_hints:
            hit = _match_category_hit(category, (retrieval.hits,), query=query)
            if hit:
                return hit
    ordered = sorted((preferred or retrieval.hits), key=lambda item: (_hit_query_score(item, query), item.state == "active", item.turn_index), reverse=True)
    return ordered[0] if ordered else None


def _intent_categories(intent: QueryIntent, retrieval: MemoryRetrieval) -> List[str]:
    categories = _dedupe_strings([*(intent.category_hints or []), intent.category_hint], max_items=6)
    if categories:
        return categories
    if not intent.category_hint and not intent.category_hints:
        return []
    preferred = retrieval.history_hits if intent.history_mode else retrieval.active_hits
    return _dedupe_strings([hit.category for hit in [*preferred, *retrieval.hits] if hit.category], max_items=4)


def _match_category_hit(category: str, pools: Sequence[Sequence[MemoryHit]], *, query: str = "") -> MemoryHit | None:
    candidates: List[MemoryHit] = []
    seen = set()
    for pool in pools:
        for hit in pool:
            if category and hit.category != category:
                continue
            if hit.memory_id in seen:
                continue
            seen.add(hit.memory_id)
            candidates.append(hit)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (_hit_query_score(item, query), item.state == "active", item.turn_index), reverse=True)
    return candidates[0]


def _active_slot_hit(category: str, retrieval: MemoryRetrieval, *, query: str = "") -> MemoryHit | None:
    return _match_category_hit(
        category,
        (
            retrieval.active_hits,
            [hit for hit in retrieval.hits if hit.state == "active"],
            retrieval.hits,
        ),
        query=query,
    )


def _historical_slot_hit(category: str, retrieval: MemoryRetrieval, *, query: str = "") -> MemoryHit | None:
    return _match_category_hit(
        category,
        (
            retrieval.history_hits,
            retrieval.overwrite_hits,
            [hit for hit in retrieval.hits if hit.state != "active"],
            retrieval.active_hits,
            retrieval.hits,
        ),
        query=query,
    )


def _active_counterpart(hit: MemoryHit, retrieval: MemoryRetrieval, *, query: str = "") -> MemoryHit | None:
    if not hit:
        return None
    same_slot = [candidate for candidate in retrieval.active_hits if candidate.slot_key == hit.slot_key and candidate.memory_id != hit.memory_id]
    if same_slot:
        same_slot.sort(key=lambda item: (_hit_query_score(item, query), item.turn_index), reverse=True)
        return same_slot[0]
    return _active_slot_hit(hit.category, retrieval, query=query)


def _select_diverse_hits(intent: QueryIntent, retrieval: MemoryRetrieval, *, query: str, limit: int) -> List[MemoryHit]:
    pool = retrieval.history_hits if intent.history_mode else retrieval.active_hits
    ordered = sorted((pool or retrieval.hits), key=lambda item: (_hit_query_score(item, query), item.state == "active", item.turn_index), reverse=True)
    selected: List[MemoryHit] = []
    seen_groups = set()
    for hit in ordered:
        group = hit.slot_key or f"{hit.category}:{hit.value}"
        if group in seen_groups:
            continue
        seen_groups.add(group)
        selected.append(hit)
        if len(selected) >= max(1, limit):
            break
    return selected


def _slot_label(category: str, *, history_mode: bool, prefer_chinese: bool) -> str:
    english = {
        "goal": ("previous goal", "current goal"),
        "constraint": ("previous constraint", "current constraint"),
        "preference": ("previous preference", "current preference"),
        "terminology": ("previous term meaning", "current term meaning"),
        "stage_state": ("previous stage", "current stage"),
    }
    chinese = {
        "goal": ("之前的目标", "当前目标"),
        "constraint": ("之前的约束", "当前约束"),
        "preference": ("之前的偏好", "当前偏好"),
        "terminology": ("之前的术语含义", "当前术语含义"),
        "stage_state": ("之前的阶段", "当前阶段"),
    }
    labels = chinese if prefer_chinese else english
    fallback = ("historical value", "current value") if not prefer_chinese else ("之前的值", "当前值")
    history_label, current_label = labels.get(category, fallback)
    return history_label if history_mode else current_label


def _compose_slot_summary(*, hits: Sequence[MemoryHit], history_mode: bool, prefer_chinese: bool) -> str:
    if not hits:
        return ""
    parts = []
    for hit in hits:
        item_history_mode = history_mode
        if any(other.state != hit.state for other in hits):
            item_history_mode = hit.state != "active"
        label = _slot_label(hit.category, history_mode=item_history_mode, prefer_chinese=prefer_chinese)
        if prefer_chinese:
            parts.append(f"{label}是{hit.value}")
        else:
            parts.append(f"{label} is {hit.value}")
    joiner = "；" if prefer_chinese else "; "
    ending = "。" if prefer_chinese else "."
    return joiner.join(parts) + ending


def _slot_facts(hit: MemoryHit | None, retrieval: MemoryRetrieval) -> List[Dict[str, Any]]:
    if not hit:
        return []
    facts = []
    for anchor in _dedupe_strings(hit.anchors[:4], max_items=4):
        facts.append(
            {
                "from": anchor,
                "to": hit.value,
                "relation": hit.relation,
                "weight": round(max(0.25, min(0.98, 0.42 + hit.score * 0.4)), 6),
                "source": hit.source_kind,
            }
        )
    if hit.slot_key:
        facts.append(
            {
                "from": hit.slot_key,
                "to": hit.value,
                "relation": "active_in_slot" if hit.state == "active" else "historical_in_slot",
                "weight": 0.92 if hit.state == "active" else 0.7,
                "source": "memory_slot",
            }
        )
    previous = next((item for item in retrieval.overwrite_hits if item.slot_key == hit.slot_key and item.value != hit.value), None)
    if previous is None and retrieval.overwrite_hits and hit.state == "active":
        previous = retrieval.overwrite_hits[0]
    if previous and hit.state == "active":
        facts.append(
            {
                "from": hit.value,
                "to": previous.value,
                "relation": "supersedes",
                "weight": 0.91,
                "source": "memory_slot",
            }
        )
    elif hit.state != "active":
        current = next((item for item in retrieval.active_hits if item.slot_key == hit.slot_key and item.value != hit.value), None)
        if current is None:
            current = retrieval.active_hits[0] if retrieval.active_hits else None
        if current and current.value != hit.value:
            facts.append(
                {
                    "from": current.value,
                    "to": hit.value,
                    "relation": "supersedes",
                    "weight": 0.91,
                    "source": "memory_slot",
                }
            )
    return facts


def _plan_from_slot(intent: QueryIntent, retrieval: MemoryRetrieval, *, query: str = "") -> AnswerPlan:
    hit = _pick_slot_hit(intent, retrieval, query=query)
    if not hit:
        return AnswerPlan(summary="No grounded slot memory found.", confidence=0.05)
    abstain_margin = _soft_abstain_margin(retrieval)
    explicit_categories = bool(intent.category_hint or intent.category_hints)
    if query and not explicit_categories and _hit_query_score(hit, query) < 0.35:
        return AnswerPlan(summary="No grounded slot memory found.", confidence=0.05)
    if query and abstain_margin > 0.15 and _hit_query_score(hit, query) < 0.55:
        return AnswerPlan(summary="No grounded slot memory found.", confidence=0.05)
    prefer_chinese = _contains_cjk(query)
    categories = _intent_categories(intent, retrieval)
    selected_hits: List[MemoryHit] = []
    if intent.history_mode:
        for category in categories:
            historical = _historical_slot_hit(category, retrieval, query=query)
            if historical and historical.memory_id not in {item.memory_id for item in selected_hits}:
                selected_hits.append(historical)
    else:
        for category in categories:
            current = _active_slot_hit(category, retrieval, query=query)
            if current and current.memory_id not in {item.memory_id for item in selected_hits}:
                selected_hits.append(current)
    if intent.kind == "summary":
        seen_ids = {item.memory_id for item in selected_hits}
        seen_groups = {item.slot_key or f"{item.category}:{item.value}" for item in selected_hits}
        for extra in _select_diverse_hits(intent, retrieval, query=query, limit=3):
            group = extra.slot_key or f"{extra.category}:{extra.value}"
            if extra.memory_id in seen_ids or group in seen_groups:
                continue
            selected_hits.append(extra)
            seen_ids.add(extra.memory_id)
            seen_groups.add(group)
            if len(selected_hits) >= 3:
                break
    if not selected_hits:
        selected_hits = _select_diverse_hits(intent, retrieval, query=query, limit=3 if intent.kind == "summary" else 1) or [hit]
    if intent.history_mode and (_query_requests_current_pair_clean(query) or intent.kind == "summary"):
        paired_hits = list(selected_hits)
        seen_ids = {item.memory_id for item in paired_hits}
        for item in list(selected_hits):
            current = _active_counterpart(item, retrieval, query=query)
            if current and current.value != item.value and current.memory_id not in seen_ids:
                paired_hits.append(current)
                seen_ids.add(current.memory_id)
        selected_hits = paired_hits
    facts = _dedupe_relations([relation for item in selected_hits for relation in _slot_facts(item, retrieval)])[:12]
    active_hit = _active_counterpart(hit, retrieval, query=query) if intent.history_mode else (_active_slot_hit(hit.category, retrieval, query=query) or (retrieval.active_hits[0] if retrieval.active_hits else None))
    if len(selected_hits) > 1 or intent.kind == "summary":
        summary = _compose_slot_summary(hits=selected_hits, history_mode=intent.history_mode, prefer_chinese=prefer_chinese)
        used_memory_ids = [item.memory_id for item in selected_hits]
    elif intent.history_mode:
        history_hit = _historical_slot_hit(hit.category, retrieval, query=query) or (retrieval.history_hits[0] if retrieval.history_hits else (retrieval.overwrite_hits[0] if retrieval.overwrite_hits else hit))
        history_slot = history_hit.slot_key or history_hit.category
        category_label = _slot_label(history_hit.category, history_mode=True, prefer_chinese=prefer_chinese)
        if active_hit and active_hit.value != history_hit.value and active_hit.category == history_hit.category:
            if prefer_chinese:
                summary = f"{category_label}是{history_hit.value}；当前是{active_hit.value}。"
            else:
                summary = f"Previously, {history_slot} was {history_hit.value}; current value is {active_hit.value}."
            used_memory_ids = [history_hit.memory_id, active_hit.memory_id]
        else:
            if prefer_chinese:
                summary = f"{category_label}是{history_hit.value}。"
            else:
                summary = f"Historical value for {history_slot} was {history_hit.value}."
            used_memory_ids = [history_hit.memory_id]
    elif intent.category_hint == "terminology":
        if prefer_chinese:
            summary = hit.value
        else:
            summary = hit.value if "mean" in _normalize_text(hit.value) else f"{hit.slot_key or hit.category} means {hit.value}."
        used_memory_ids = [hit.memory_id]
    elif intent.category_hint == "stage_state":
        summary = f"当前阶段是{hit.value}。" if prefer_chinese else f"Current stage is {hit.value}."
        used_memory_ids = [hit.memory_id]
    elif intent.category_hint == "constraint":
        summary = f"当前约束是{hit.value}。" if prefer_chinese else f"Active constraint: {hit.value}."
        used_memory_ids = [hit.memory_id]
    elif intent.category_hint == "preference":
        summary = f"当前偏好是{hit.value}。" if prefer_chinese else f"Current preference: {hit.value}."
        used_memory_ids = [hit.memory_id]
    elif intent.category_hint == "goal":
        summary = f"当前目标是{hit.value}。" if prefer_chinese else f"Current primary goal is {hit.value}."
        used_memory_ids = [hit.memory_id]
    else:
        summary = f"当前值是{hit.value}。" if prefer_chinese else f"Current grounded value is {hit.value}."
        used_memory_ids = [hit.memory_id]
    return AnswerPlan(
        summary=summary,
        used_memory_ids=_dedupe_strings([*used_memory_ids, *[item.memory_id for item in retrieval.overwrite_hits[:2]]], max_items=6),
        facts=facts,
        candidate_scores=[{"label": item.slot_key or item.category, "score": round(item.score, 6), "source": "slot_head"} for item in selected_hits[:4]],
        confidence=max(
            0.1,
            min(
                0.92,
                max(0.2, 0.4 + (sum(item.score for item in selected_hits) / max(1, len(selected_hits))) * 0.45)
                - max(0.0, abstain_margin) * 0.25,
            ),
        ),
    )


def _plan_from_path(query: str, intent: QueryIntent, *, hypotheses: Sequence[RouteHypothesis], retrieval: MemoryRetrieval, maze_paths: Sequence[Dict[str, Any]], maze_candidate_scores: Sequence[Dict[str, Any]], supporting_relations: Sequence[Dict[str, Any]]) -> AnswerPlan:
    primary = hypotheses[0] if hypotheses else None
    abstain_margin = _soft_abstain_margin(retrieval)
    required_middle = set(_required_middle_concepts(query, extraction={"concepts": [], "relations": []}, retrieval=retrieval))
    if required_middle and hypotheses:
        constrained = [item for item in hypotheses if required_middle.issubset(set(item.concepts))]
        if constrained:
            primary = max(constrained, key=lambda item: (len(item.concepts), item.score))
    if primary is None and maze_paths:
        first = maze_paths[0]
        primary = RouteHypothesis(
            label="maze_path",
            concepts=list(first.get("concepts", []) or []),
            relations=list(first.get("relations", []) or []),
            source=first.get("mode", "maze"),
            score=float(first.get("score", 0.0) or 0.0),
            has_tunneling=bool(first.get("has_tunneling", False)),
        )
    if primary is None:
        fallback = _plan_from_slot(intent, retrieval)
        if not fallback.summary:
            fallback.summary = "No grounded route found."
        return fallback
    if abstain_margin > 0.18 and float(primary.score) < 0.6:
        fallback = _plan_from_slot(intent, retrieval, query=query)
        if not fallback.summary:
            fallback.summary = "No grounded route found."
        return fallback
    relation_lookup = {(item.get("from"), item.get("to")): item for item in supporting_relations}
    selected_hypotheses = [primary]
    if _query_requests_multiple_paths(query) and len(hypotheses) >= 2:
        for candidate in hypotheses:
            if candidate.label == primary.label and candidate.concepts == primary.concepts:
                continue
            primary_middle = set(primary.concepts[1:-1])
            candidate_middle = set(candidate.concepts[1:-1])
            if candidate_middle and candidate_middle == primary_middle:
                continue
            selected_hypotheses.append(candidate)
            if len(selected_hypotheses) >= 2:
                break
    facts: List[Dict[str, Any]] = []
    for hypothesis in selected_hypotheses:
        for left, right, relation_name in zip(hypothesis.concepts[:-1], hypothesis.concepts[1:], hypothesis.relations):
            facts.append(relation_lookup.get((left, right), {"from": left, "to": right, "relation": relation_name, "weight": 0.7, "source": hypothesis.source}))
    facts = _dedupe_relations([*facts, *list(supporting_relations)[:6], *list(_slot_facts(_pick_slot_hit(intent, retrieval, query=query), retrieval))[:3]])[:12]
    summary = f"Path connects {' -> '.join(primary.concepts)}."
    if len(selected_hypotheses) > 1:
        summary = "Multiple grounded paths found: " + " | ".join(" -> ".join(item.concepts) for item in selected_hypotheses)
    if intent.history_mode and retrieval.overwrite_hits:
        summary += f" Historical note: previous value was {retrieval.overwrite_hits[0].value}."
    return AnswerPlan(
        summary=summary,
        used_memory_ids=_dedupe_strings([*primary.memory_ids, *[hit.memory_id for hit in retrieval.active_hits[:2]]], max_items=8),
        facts=facts,
        paths=[*[item.to_path() for item in selected_hypotheses], *list(maze_paths)[:2]],
        candidate_scores=[*[item.to_candidate_score() for item in selected_hypotheses], *list(maze_candidate_scores)[:4], *[item.to_candidate_score() for item in hypotheses[1:4]]],
        confidence=max(
            0.12,
            min(
                0.95,
                max(0.25, 0.45 + primary.score * 0.35 + (0.08 if primary.has_tunneling else 0.0))
                - max(0.0, abstain_margin) * 0.25,
            ),
        ),
    )


class TriMazeIsolatedReasoner(ReasoningAdapter):
    name = "tmcra_isolated_trimaze_v2"

    def __init__(self, *, tunneling_enabled: bool = True) -> None:
        self.extractor = LocalConceptExtractor()
        self._scratch_dir = tempfile.TemporaryDirectory(prefix="tmcra_replacement_")
        self.tunneling_enabled = bool(tunneling_enabled)

    def _new_memory(self) -> ConceptMemory:
        memory_path = Path(self._scratch_dir.name) / "isolated_concept_memory.json"
        memory = ConceptMemory(memory_file=str(memory_path))
        memory.clear_memory()
        return memory

    def _build_concept_graph(self, extraction: Dict[str, Any], retrieval: MemoryRetrieval) -> ConceptGraph:
        concept_graph = ConceptGraph()
        concept_seen = set()
        for source in [*(extraction.get("concepts", []) or []), *retrieval.concepts]:
            concept = _clean_text(source.get("concept", "")) if isinstance(source, dict) else _clean_text(source)
            concept_type = _clean_text(source.get("type", "general")) if isinstance(source, dict) else "general"
            if not concept or concept in concept_seen:
                continue
            concept_seen.add(concept)
            concept_graph.add_concept(concept, concept_type or "general")
        for relation in _relations_from_memory(retrieval):
            src = _clean_text(relation.get("from", ""))
            dst = _clean_text(relation.get("to", ""))
            rel = _clean_text(relation.get("relation", "")) or "related_to"
            if not src or not dst or src == dst:
                continue
            if src not in concept_graph.graph:
                concept_graph.add_concept(src, "general")
            if dst not in concept_graph.graph:
                concept_graph.add_concept(dst, "general")
            concept_graph.graph.add_edge(src, dst, relation=rel, weight=_safe_weight(relation.get("weight", 0.5)), source_kind=_clean_text(relation.get("source_kind", relation.get("source", ""))))
        for relation in extraction.get("relations", []) or []:
            src = _clean_text(relation.get("from", ""))
            dst = _clean_text(relation.get("to", ""))
            rel = _clean_text(relation.get("relation", "")) or "related_to"
            if not src or not dst or src == dst:
                continue
            if src not in concept_graph.graph:
                concept_graph.add_concept(src, "general")
            if dst not in concept_graph.graph:
                concept_graph.add_concept(dst, "general")
            concept_graph.graph.add_edge(src, dst, relation=rel, weight=_safe_weight(relation.get("weight", 0.5)), source_kind="extraction")
        return concept_graph

    async def answer(self, query: str, *, answer_mode: str, memory_adapter: MemoryAdapter) -> AdapterResponse:
        start = time.perf_counter()
        mode = _normalize_answer_mode(answer_mode)
        extraction = self.extractor.extract(query) or {"concepts": [], "relations": []}
        retrieval = memory_adapter.retrieve(query, top_k=8)
        intent = _resolve_intent(query, answer_mode=mode)
        hypotheses, supporting_relations = _graph_route_hypotheses(query, extraction=extraction, retrieval=retrieval)
        counterfactual_plan = _counterfactual_plan(query, extraction=extraction, retrieval=retrieval)

        concept_graph = self._build_concept_graph(extraction, retrieval)
        maze_paths: List[Dict[str, Any]] = []
        maze_candidate_scores: List[Dict[str, Any]] = []
        if concept_graph.graph.number_of_nodes() > 0:
            engine = TriMazeEngine(
                concept_graph.graph,
                concept_memory=self._new_memory(),
                multimodal_generator=None,
                policy_enabled=False,
            )
            engine.max_paths = 3
            engine.max_exploration_steps = 40
            engine.tunneling_enabled = self.tunneling_enabled
            engine.grinding_enabled = True
            endpoint_pairs = _endpoint_pairs(query, extraction=extraction, retrieval=retrieval, relation_graph=concept_graph.graph)
            if endpoint_pairs:
                start_concept, target_concept = endpoint_pairs[0]
            else:
                concepts = _query_concepts(query, extraction, retrieval)
                start_concept = concepts[0] if concepts else ""
                target_concept = concepts[-1] if len(concepts) >= 2 else None
            if start_concept and start_concept in engine.nodes:
                try:
                    forward_paths = list(await engine.forward_maze_explore(start_concept, target_concept) or [])
                    reverse_paths = list(await engine.reverse_maze_explore(start_concept, target_concept) or [])
                    boundary_paths = list(await engine.boundary_maze_explore(start_concept) or [])
                except Exception:
                    forward_paths, reverse_paths, boundary_paths = [], [], []
                maze_paths = [
                    *[_serialize_maze_path(path, "forward") for path in forward_paths[:2]],
                    *[_serialize_maze_path(path, "reverse") for path in reverse_paths[:1]],
                    *[_serialize_maze_path(path, "boundary") for path in boundary_paths[:1]],
                ]
                for path in maze_paths[:4]:
                    maze_candidate_scores.append(
                        {
                            "label": " -> ".join(path.get("concepts", [])[:4]),
                            "score": float(path.get("score", 0.0) or 0.0),
                            "source": path.get("mode", "maze"),
                            "has_tunneling": bool(path.get("has_tunneling", False)),
                        }
                    )

        if counterfactual_plan is not None:
            plan = counterfactual_plan
        else:
            plan = _plan_from_path(query, intent, hypotheses=hypotheses, retrieval=retrieval, maze_paths=maze_paths, maze_candidate_scores=maze_candidate_scores, supporting_relations=supporting_relations) if intent.kind == "path" else _plan_from_slot(intent, retrieval, query=query)
        if intent.kind != "path" and hypotheses:
            plan.candidate_scores.extend([item.to_candidate_score() for item in hypotheses[:3]])
            if not plan.paths:
                plan.paths = [item.to_path() for item in hypotheses[:1]]
            if not plan.facts:
                plan.facts = _dedupe_relations([*plan.facts, *supporting_relations[:4]])[:8]

        memory_hits = _memory_hit_dicts(retrieval)
        answer_id = f"answer:{int(time.time() * 1000)}"
        if plan.used_memory_ids:
            memory_adapter.register_answer_support(
                answer_id=answer_id,
                memory_ids=plan.used_memory_ids,
                query_id=str(retrieval.metadata.get("query_id", "")),
                answer_text=plan.summary,
            )
        summary = _render_natural_answer(summary=plan.summary, facts=plan.facts, paths=plan.paths, memory_hits=memory_hits)
        answer = _render_transparent_answer(summary, facts=plan.facts, paths=plan.paths, memory_hits=memory_hits, candidate_scores=plan.candidate_scores, confidence=plan.confidence) if mode == "transparent" else summary
        return AdapterResponse(
            answer=answer,
            answer_mode=mode,
            reasoner_name=self.name,
            memory_name=memory_adapter.name,
            confidence=plan.confidence,
            paths=plan.paths,
            facts=plan.facts,
            candidate_scores=plan.candidate_scores,
            memory_hits=memory_hits,
            evidence_consistent=bool(plan.facts or plan.paths),
            unsupported_claims=[],
            pillar_scores={"evidence_consistency": 1.0 if (plan.facts or plan.paths) else 0.0, "path_signal": 1.0 if intent.kind == "path" and plan.paths else 0.0},
            latency_seconds=time.perf_counter() - start,
            trace={"intent": {"kind": intent.kind, "category_hint": intent.category_hint, "category_hints": list(intent.category_hints), "history_mode": intent.history_mode}, "used_memory_ids": list(plan.used_memory_ids), "maze_paths": maze_paths[:4]},
            metadata={"retrieval": retrieval.to_dict(), "extraction": extraction, "route_hypotheses": [item.to_candidate_score() for item in hypotheses[:6]], "tunneling_enabled": self.tunneling_enabled},
        )


class DirectExtractionReasoner(ReasoningAdapter):
    name = "direct_extraction_reasoner"

    def __init__(self) -> None:
        self.extractor = LocalConceptExtractor()

    async def answer(self, query: str, *, answer_mode: str, memory_adapter: MemoryAdapter) -> AdapterResponse:
        start = time.perf_counter()
        mode = _normalize_answer_mode(answer_mode)
        extraction = self.extractor.extract(query) or {"concepts": [], "relations": []}
        retrieval = memory_adapter.retrieve(query, top_k=6)
        intent = _resolve_intent(query, answer_mode=mode)
        hypotheses, supporting_relations = _graph_route_hypotheses(query, extraction=extraction, retrieval=retrieval)
        counterfactual_plan = _counterfactual_plan(query, extraction=extraction, retrieval=retrieval)
        facts = []
        for relation in list(extraction.get("relations", []) or [])[:6]:
            src = _clean_text(relation.get("from", ""))
            dst = _clean_text(relation.get("to", ""))
            rel = _clean_text(relation.get("relation", ""))
            if src and dst and rel:
                facts.append({"from": src, "to": dst, "relation": rel, "weight": _safe_weight(relation.get("weight", 0.5)), "source": "direct_extraction"})
        if intent.kind == "path":
            facts.extend(_relations_from_memory(retrieval)[:4])
        if counterfactual_plan is not None:
            plan = counterfactual_plan
        elif intent.kind == "path":
            plan = _plan_from_path(
                query,
                intent,
                hypotheses=hypotheses,
                retrieval=retrieval,
                maze_paths=[],
                maze_candidate_scores=[],
                supporting_relations=supporting_relations,
            )
        else:
            plan = _plan_from_slot(intent, retrieval, query=query)
        candidate_scores = list(plan.candidate_scores) if plan else []
        if plan:
            facts.extend(plan.facts)
        elif not facts:
            facts.extend(_relations_from_memory(retrieval)[:4])
        if plan and not plan.used_memory_ids and "No grounded slot memory found." in _clean_text(plan.summary):
            facts = []
            candidate_scores = []
        facts = _dedupe_relations(facts)[:10]
        hit = retrieval.hits[0] if retrieval.hits else None
        if plan and _clean_text(plan.summary):
            summary = plan.summary
        elif hit:
            summary = f"Direct extraction suggests {hit.value}."
        else:
            summary = _render_natural_answer(summary="", facts=facts, paths=list(plan.paths) if plan else [], memory_hits=_memory_hit_dicts(retrieval))
        confidence = plan.confidence if plan and _clean_text(plan.summary) else max(0.05, min(0.8, 0.18 + (0.28 if facts else 0.0) + (0.16 if hit else 0.0)))
        answer_id = f"answer:{int(time.time() * 1000)}"
        if plan and plan.used_memory_ids:
            memory_adapter.register_answer_support(
                answer_id=answer_id,
                memory_ids=plan.used_memory_ids,
                query_id=str(retrieval.metadata.get("query_id", "")),
                answer_text=summary,
            )
        visible_memory_hits = [hit.to_dict() for hit in retrieval.hits if not plan or not plan.used_memory_ids or hit.memory_id in plan.used_memory_ids][:4]
        if plan and not plan.used_memory_ids and "No grounded slot memory found." in _clean_text(plan.summary):
            visible_memory_hits = []
        answer = _render_transparent_answer(summary, facts=facts, paths=list(plan.paths) if plan else [], memory_hits=visible_memory_hits, candidate_scores=candidate_scores, confidence=confidence) if mode == "transparent" else summary
        return AdapterResponse(
            answer=answer,
            answer_mode=mode,
            reasoner_name=self.name,
            memory_name=memory_adapter.name,
            confidence=confidence,
            facts=facts,
            paths=list(plan.paths) if plan else [],
            candidate_scores=candidate_scores,
            memory_hits=visible_memory_hits,
            evidence_consistent=bool(facts or (plan and plan.paths)),
            unsupported_claims=[],
            pillar_scores={"evidence_consistency": 1.0 if (facts or (plan and plan.paths)) else 0.0},
            latency_seconds=time.perf_counter() - start,
            trace={"intent": {"kind": intent.kind, "category_hint": intent.category_hint, "category_hints": list(intent.category_hints), "history_mode": intent.history_mode}, "route_hypotheses": [item.to_candidate_score() for item in hypotheses[:6]]},
            metadata={"retrieval": retrieval.to_dict(), "extraction": extraction},
        )


class OpenAICompatDirectReasoner(DirectExtractionReasoner):
    name = "openai_compat_direct_reasoner"

    def __init__(self, *, profile: LLMProfile | None = None, base_url: str | None = None, api_key: str | None = None, model: str | None = None) -> None:
        super().__init__()
        profile = profile or LLMProfile(
            name="generic",
            model=_clean_text(model or os.getenv("TMCRA_REPLACEMENT_LLM_MODEL", "")),
            base_url=_clean_text(base_url or os.getenv("TMCRA_REPLACEMENT_LLM_BASE_URL", "")),
            api_key=_clean_text(api_key or os.getenv("TMCRA_REPLACEMENT_LLM_API_KEY", "")),
        )
        self.profile = profile
        self.name = f"openai_compat_{profile.name}"
        self.client = None
        if OpenAI is not None and profile.base_url and profile.model:
            try:
                self.client = OpenAI(base_url=profile.base_url, api_key=profile.api_key or "EMPTY")
            except Exception:
                self.client = None

    async def answer(self, query: str, *, answer_mode: str, memory_adapter: MemoryAdapter) -> AdapterResponse:
        baseline = await super().answer(query, answer_mode=answer_mode, memory_adapter=memory_adapter)
        baseline.reasoner_name = self.name
        baseline.metadata["llm_profile"] = {"name": self.profile.name, "model": self.profile.model, "base_url": self.profile.base_url}
        if self.client is None:
            baseline.metadata["llm_fallback"] = True
            return baseline
        prompt = {
            "query": query,
            "facts": baseline.facts,
            "memory_hits": baseline.memory_hits,
            "paths": baseline.paths,
            "answer_mode": baseline.answer_mode,
            "instruction": "Answer only from the provided facts, memory hits, and paths. Never invent unsupported claims.",
        }
        try:
            llm_start = time.perf_counter()
            completion = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self.profile.model,
                messages=[
                    {"role": "system", "content": self.profile.system_prompt},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                temperature=float(self.profile.temperature),
                max_tokens=int(self.profile.max_tokens),
                timeout=float(self.profile.timeout_seconds),
            )
            usage = _completion_usage_dict(completion)
            if usage:
                baseline.metadata["llm_usage"] = dict(usage)
            raw_content = _clean_text(completion.choices[0].message.content if completion.choices else "")
            if raw_content:
                baseline.answer = _render_transparent_answer(raw_content, facts=baseline.facts, paths=baseline.paths, memory_hits=baseline.memory_hits, candidate_scores=baseline.candidate_scores, confidence=baseline.confidence) if baseline.answer_mode == "transparent" else raw_content
                baseline.metadata["raw_llm_answer"] = raw_content
                baseline.metadata["llm_fallback"] = False
                baseline.latency_seconds += time.perf_counter() - llm_start
        except Exception as exc:
            baseline.metadata["llm_fallback"] = True
            baseline.metadata["llm_error"] = str(exc)
        return baseline


class OpenAICompatCoTReasoner(OpenAICompatDirectReasoner):
    name = "openai_compat_cot_reasoner"

    def __init__(self, *, profile: LLMProfile | None = None, base_url: str | None = None, api_key: str | None = None, model: str | None = None) -> None:
        super().__init__(profile=profile, base_url=base_url, api_key=api_key, model=model)
        self.name = f"openai_compat_{self.profile.name}_cot"

    async def answer(self, query: str, *, answer_mode: str, memory_adapter: MemoryAdapter) -> AdapterResponse:
        baseline = await DirectExtractionReasoner.answer(self, query, answer_mode=answer_mode, memory_adapter=memory_adapter)
        baseline.reasoner_name = self.name
        baseline.metadata["llm_profile"] = {"name": self.profile.name, "model": self.profile.model, "base_url": self.profile.base_url}
        if self.client is None:
            baseline.metadata["llm_fallback"] = True
            return baseline
        prompt = {
            "query": query,
            "facts": baseline.facts,
            "memory_hits": baseline.memory_hits,
            "paths": baseline.paths,
            "instruction": "Reason step by step over the supplied grounded evidence. Prefer multiple candidate explanations when possible. Do not invent unsupported claims.",
        }
        try:
            llm_start = time.perf_counter()
            completion = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self.profile.model,
                messages=[
                    {"role": "system", "content": self.profile.system_prompt},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                temperature=float(self.profile.temperature),
                max_tokens=int(self.profile.max_tokens),
                timeout=float(self.profile.timeout_seconds),
            )
            usage = _completion_usage_dict(completion)
            if usage:
                baseline.metadata["llm_usage"] = dict(usage)
            raw_content = _clean_text(completion.choices[0].message.content if completion.choices else "")
            if raw_content:
                baseline.answer = _render_transparent_answer(raw_content, facts=baseline.facts, paths=baseline.paths, memory_hits=baseline.memory_hits, candidate_scores=baseline.candidate_scores, confidence=baseline.confidence) if baseline.answer_mode == "transparent" else raw_content
                baseline.metadata["raw_llm_answer"] = raw_content
                baseline.metadata["llm_fallback"] = False
                baseline.trace["intent"] = "cot_evidence_constrained"
                baseline.latency_seconds += time.perf_counter() - llm_start
        except Exception as exc:
            baseline.metadata["llm_fallback"] = True
            baseline.metadata["llm_error"] = str(exc)
        return baseline


class OpenAICompatFullContextReasoner(ReasoningAdapter):
    name = "openai_compat_full_context_reasoner"

    def __init__(
        self,
        *,
        profile: LLMProfile | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        prompt_style: str = "full_context",
        prompt_top_k: int = 8,
        prompt_max_chars: int = 12000,
        system_prompt_override: str = "",
        instruction_override: str = "",
    ) -> None:
        profile = profile or LLMProfile(
            name="generic",
            model=_clean_text(model or os.getenv("TMCRA_REPLACEMENT_LLM_MODEL", "")),
            base_url=_clean_text(base_url or os.getenv("TMCRA_REPLACEMENT_LLM_BASE_URL", "")),
            api_key=_clean_text(api_key or os.getenv("TMCRA_REPLACEMENT_LLM_API_KEY", "")),
        )
        self.profile = profile
        self.name = f"openai_compat_{profile.name}_full_context"
        self.extractor = LocalConceptExtractor()
        self.prompt_style = _clean_text(prompt_style or "full_context") or "full_context"
        self.prompt_top_k = max(1, int(prompt_top_k or 8))
        self.prompt_max_chars = max(512, int(prompt_max_chars or 12000))
        self.system_prompt_override = _clean_text(system_prompt_override)
        self.instruction_override = _clean_text(instruction_override)
        self.client = None
        if OpenAI is not None and profile.base_url and profile.model:
            try:
                self.client = OpenAI(base_url=profile.base_url, api_key=profile.api_key or "EMPTY")
            except Exception:
                self.client = None

    def _compact_prompt_context(self, query: str, retrieval: MemoryRetrieval) -> Dict[str, Any]:
        snippets: List[Dict[str, Any]] = []
        total_chars = 0
        truncated = False
        ordered_hits = sorted(list(retrieval.hits or []), key=lambda hit: _snippet_priority(query, hit), reverse=True)
        for index, hit in enumerate(ordered_hits[: self.prompt_top_k], start=1):
            value = _clean_text(getattr(hit, "value", ""))
            if not value:
                continue
            metadata = getattr(hit, "metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            raw_support_value = _clean_text(metadata.get("raw_text", "")) or _clean_text(metadata.get("source_turn", ""))
            event_label = _clean_text(metadata.get("event_signature", "")) or _clean_text(metadata.get("event_text", ""))
            time_label = _clean_text(metadata.get("resolved_date", "")) or _clean_text(metadata.get("time_display_value", "")) or _clean_text(metadata.get("resolved_time_value", ""))
            status_label = _clean_text(metadata.get("target_status", ""))
            profile_label = _clean_text(metadata.get("profile_value", ""))
            semantic_slot = _clean_text(metadata.get("semantic_slot", ""))
            evidence_type = semantic_slot or _clean_text(getattr(hit, "source_kind", "")) or "memory"
            support_value = raw_support_value or value
            if time_label and raw_support_value:
                normalized_time = _normalize_text(time_label)
                normalized_support = _normalize_text(raw_support_value)
                if normalized_time and normalized_time not in normalized_support:
                    support_value = f"{time_label}: {raw_support_value}"
            snippet = {
                "rank": index,
                "memory_id": _clean_text(getattr(hit, "memory_id", "")),
                "slot_key": _clean_text(getattr(hit, "slot_key", "")),
                "source_kind": _clean_text(getattr(hit, "source_kind", "")),
                "event_id": _clean_text(metadata.get("event_id", "")),
                "path_id": _clean_text(metadata.get("path_id", "")),
                "dia_id": _clean_text(metadata.get("dia_id", "")),
                "evidence_type": evidence_type,
                "evidence": {
                    "speaker": _clean_text(metadata.get("speaker", "")),
                    "event": event_label or (_clean_text(metadata.get("event_id", "")) if _clean_text(metadata.get("event_id", "")) else value),
                    "time": time_label,
                    "status": status_label,
                    "profile": profile_label,
                    "source_turn": support_value,
                },
                "text": support_value,
            }
            rendered = _render_compact_evidence_tuple(snippet)
            snippet["rendered_tuple"] = rendered
            if snippets and (total_chars + len(rendered)) > self.prompt_max_chars:
                truncated = True
                break
            snippets.append(snippet)
            total_chars += len(rendered)
        return {
            "mode": "compact_retrieval",
            "top_k": int(self.prompt_top_k),
            "snippets": snippets,
            "char_count": int(total_chars),
            "truncated": bool(truncated),
        }

    async def answer(self, query: str, *, answer_mode: str, memory_adapter: MemoryAdapter) -> AdapterResponse:
        start = time.perf_counter()
        mode = _normalize_answer_mode(answer_mode)
        extraction = self.extractor.extract(query) or {"concepts": [], "relations": []}
        retrieval = memory_adapter.retrieve(query, top_k=self.prompt_top_k)
        if self.prompt_style == "compact_retrieval":
            prompt_context = self._compact_prompt_context(query, retrieval)
        else:
            prompt_context = memory_adapter.build_prompt_context(query, top_k=self.prompt_top_k)
        facts = _dedupe_relations([*_relations_from_memory(retrieval), *[
            {
                "from": _clean_text(item.get("from", "")),
                "to": _clean_text(item.get("to", "")),
                "relation": _clean_text(item.get("relation", "")),
                "weight": _safe_weight(item.get("weight", 0.5)),
                "source": "extraction",
            }
            for item in extraction.get("relations", []) or []
            if _clean_text(item.get("from", "")) and _clean_text(item.get("to", "")) and _clean_text(item.get("relation", ""))
        ]])[:10]
        memory_hits = _memory_hit_dicts(retrieval)
        answer = _render_natural_answer(summary="", facts=facts, paths=[], memory_hits=memory_hits)
        metadata: Dict[str, Any] = {
            "retrieval": retrieval.to_dict(),
            "extraction": extraction,
            "llm_profile": {"name": self.profile.name, "model": self.profile.model, "base_url": self.profile.base_url},
            "prompt_context": prompt_context,
            "prompt_style": self.prompt_style,
        }
        if self.client is not None:
            if self.prompt_style == "compact_retrieval":
                prompt = {
                    "question": query,
                    "context_snippets": [
                        _clean_text(item.get("rendered_tuple", "")) or json.dumps(item, ensure_ascii=False)
                        for item in list(prompt_context.get("snippets", []) or [])
                    ],
                    "instruction": self.instruction_override
                    or (
                        "Based on the above context snippets, answer with a short phrase copied from the context whenever possible. "
                        "Do not explain. If the answer is missing from the snippets, say 'Not mentioned in the conversation'."
                    ),
                }
                system_prompt = self.system_prompt_override or (
                    "You are answering LoCoMo-style long-conversation questions. Return only a short answer phrase. "
                    "Do not include timestamps, speaker names, or explanations unless they are the answer itself."
                )
            else:
                prompt = {
                    "query": query,
                    "full_context": prompt_context,
                    "instruction": self.instruction_override
                    or "Answer from the full context. You may reason freely over the entire provided conversation state. If there is insufficient information, say so.",
                }
                system_prompt = self.system_prompt_override or "You are evaluating a full-context free-reasoning baseline. Use the entire provided context and answer directly."
            try:
                llm_start = time.perf_counter()
                completion = await asyncio.to_thread(
                    self.client.chat.completions.create,
                    model=self.profile.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                    ],
                    temperature=float(self.profile.temperature),
                    max_tokens=int(self.profile.max_tokens),
                    timeout=float(self.profile.timeout_seconds),
                )
                usage = _completion_usage_dict(completion)
                if usage:
                    metadata["llm_usage"] = dict(usage)
                raw_content = _clean_text(completion.choices[0].message.content if completion.choices else "")
                if raw_content:
                    answer = raw_content
                    metadata["raw_llm_answer"] = raw_content
                    metadata["llm_fallback"] = False
                    latency_seconds = time.perf_counter() - start
                else:
                    metadata["llm_fallback"] = True
                    latency_seconds = time.perf_counter() - start
            except Exception as exc:
                metadata["llm_fallback"] = True
                metadata["llm_error"] = str(exc)
                latency_seconds = time.perf_counter() - start
        else:
            metadata["llm_fallback"] = True
            latency_seconds = time.perf_counter() - start

        rendered = _render_transparent_answer(answer, facts=facts, paths=[], memory_hits=memory_hits, candidate_scores=[], confidence=_response_confidence_from_text(answer)) if mode == "transparent" else answer
        return AdapterResponse(
            answer=rendered,
            answer_mode=mode,
            reasoner_name=self.name,
            memory_name=memory_adapter.name,
            confidence=_response_confidence_from_text(answer),
            facts=facts,
            paths=[],
            candidate_scores=[],
            memory_hits=memory_hits,
            evidence_consistent=bool(facts),
            unsupported_claims=[],
            pillar_scores={"evidence_consistency": 1.0 if facts else 0.0},
            latency_seconds=latency_seconds,
            trace={"intent": "full_context_free_reasoning"},
            metadata=metadata,
        )
