from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, MutableMapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.replacement.node_memory_runtime_utils import (
    answer_type_from_query,
    build_path_id,
    clean_text,
    dedupe_texts,
    extract_question_features,
    normalize_text,
    write_json,
)
from scripts.controlled_teacher_extraction import _extract_braced_objects
from scripts.export_locomo_node_training_data import (
    DEFAULT_TEACHER_BASE_URL,
    DEFAULT_TEACHER_MODEL,
    TeacherClient,
    TeacherTurnError,
    _candidate_bundle,
    _candidate_hard_negative_score,
    _derive_event_signature,
    _event_catalog_from_graph,
    _event_signature_semantic_slot,
    _positive_path_ids,
    _positive_time_node_ids,
    _temporal_target,
    _token_overlap_score,
)

ALLOWED_ANSWER_TYPES = {"time", "profile", "event_text", "multi_evidence", "abstain"}
ALLOWED_PATH_TYPES = {
    "speaker_event_time",
    "speaker_event_profile",
    "speaker_event_status",
    "speaker_event_source_turn",
}
TEACHER_DECISIONS = {"accept", "edit", "reject"}
DEFAULT_GRAPH_CACHE_SIZE = 64


def _log(event: str, **payload: Any) -> None:
    details = " ".join(f"{key}={json.dumps(payload[key], ensure_ascii=False)}" for key in sorted(payload))
    stamp = datetime.now().isoformat(timespec="seconds")
    print(f"[filter_node_training_quality] {stamp} {event}" + (f" {details}" if details else ""), flush=True)


def _read_json(path: Path) -> Dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _clear_output(output_dir: Path) -> None:
    for relative in (
        "graphs",
        "queries",
        "turn_extraction",
        "graph_index.jsonl",
        "query_index.json",
        "filter_manifest.json",
        "export_manifest.json",
        "dropped_rows.jsonl",
        "repaired_rows.jsonl",
        "teacher_review_decisions.jsonl",
    ):
        path = output_dir / relative
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(_jsonl_bytes(payload))


def _iter_jsonl(path: Path) -> Iterator[tuple[int, str]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            yield int(line_number), line


def _jsonl_bytes(payload: Mapping[str, Any]) -> bytes:
    text = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
    encoded = text.encode("utf-8", errors="strict")
    json.loads(encoded.decode("utf-8"))
    return encoded + b"\n"


def _materialize_path(*, source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        destination.unlink()
    try:
        os.symlink(source.resolve(), destination)
        return "symlink"
    except OSError:
        shutil.copy2(source, destination)
        return "copy"


def _stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _fallback_question_id(
    *,
    conversation_id: str,
    question: str,
    split: str,
    line_number: int,
    positive_event_ids: Sequence[str],
) -> str:
    digest = _stable_hash(
        "\t".join(
            [
                clean_text(conversation_id),
                normalize_text(question),
                clean_text(split),
                str(int(line_number)),
                "|".join(clean_text(item) for item in positive_event_ids if clean_text(item)),
            ]
        )
    )[:16]
    return f"{clean_text(conversation_id)}:filtered:{clean_text(split)}:{digest}"


def _row_signature(
    *,
    conversation_id: str,
    question: str,
    answer_type: str,
    positive_event_ids: Sequence[str],
) -> str:
    return _stable_hash(
        "\t".join(
            [
                clean_text(conversation_id),
                normalize_text(question),
                clean_text(answer_type),
                "|".join(sorted(clean_text(item) for item in positive_event_ids if clean_text(item))),
            ]
        )
    )


def _normalized_answer_type(value: str, *, question: str, positive_event_ids: Sequence[str]) -> str:
    normalized = clean_text(value)
    if normalized in ALLOWED_ANSWER_TYPES:
        return normalized
    return answer_type_from_query(
        question,
        positive_event_ids=list(positive_event_ids),
        question_features=extract_question_features(question),
    )


def _best_anchor_coverage(question_features: Mapping[str, Any], positive_events: Sequence[Mapping[str, Any]]) -> float:
    anchor_tokens = [
        normalize_text(item)
        for item in list(question_features.get("question_anchor_tokens", []) or [])
        if clean_text(item)
    ]
    if not anchor_tokens:
        return 0.0
    best = 0.0
    for event in positive_events:
        values = [
            clean_text(event.get("event_signature", "")),
            clean_text(event.get("event_text", "")),
            clean_text(event.get("profile_value", "")),
            clean_text(event.get("time_display_value", "")),
            clean_text(event.get("time_value", "")),
        ]
        for value in values:
            if not value:
                continue
            candidate_tokens = {normalize_text(token) for token in clean_text(value).split() if clean_text(token)}
            if not candidate_tokens:
                continue
            covered = 0
            for token in anchor_tokens:
                if token in candidate_tokens:
                    covered += 1
            best = max(best, float(covered) / float(len(anchor_tokens)))
    return best


def _build_abstain_candidates(
    *,
    question: str,
    event_catalog: Mapping[str, Mapping[str, Any]],
    max_candidates: int = 16,
) -> Dict[str, List[str]]:
    scored: List[tuple[float, str]] = []
    for event_id, event_meta in event_catalog.items():
        score = max(
            _token_overlap_score(question, clean_text(event_meta.get("event_text", ""))),
            _token_overlap_score(question, clean_text(event_meta.get("event_signature", ""))),
            _token_overlap_score(question, clean_text(event_meta.get("profile_value", ""))),
            _token_overlap_score(question, clean_text(event_meta.get("time_display_value", ""))),
            _token_overlap_score(question, clean_text(event_meta.get("time_value", ""))),
        )
        if score <= 0.0:
            continue
        scored.append((float(score), clean_text(event_id)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    event_ids = [event_id for _, event_id in scored[: max(1, int(max_candidates))]]
    return {
        "candidate_event_ids": list(event_ids),
        "negative_event_ids": list(event_ids),
    }


@dataclass(slots=True)
class QualityFilterConfig:
    input_dir: Path
    output_dir: Path
    teacher_review_mode: str = "off"
    teacher_base_url: str = DEFAULT_TEACHER_BASE_URL
    teacher_model: str = DEFAULT_TEACHER_MODEL
    teacher_api_key: str = ""
    teacher_api_key_env: str = ""
    teacher_timeout_seconds: float = 60.0
    teacher_enable_thinking: bool = False
    teacher_max_tokens: int = 256
    min_event_anchor_coverage: float = 0.34
    graph_cache_size: int = DEFAULT_GRAPH_CACHE_SIZE


@dataclass(slots=True)
class GraphSummary:
    conversation_id: str
    source_path: Path
    graph_payload: Dict[str, Any]
    event_catalog: Dict[str, Dict[str, Any]]
    event_ids: set[str]
    time_node_ids: set[str]
    path_ids: set[str]
    graph_repair_codes: List[str] = field(default_factory=list)
    materialized: bool = False


class TeacherQueryReviewer:
    def __init__(
        self,
        *,
        teacher_client: TeacherClient | None = None,
        teacher_base_url: str,
        teacher_model: str,
        teacher_api_key: str,
        teacher_api_key_env: str,
        teacher_timeout_seconds: float,
        teacher_enable_thinking: bool,
        teacher_max_tokens: int,
    ) -> None:
        self._teacher_client = teacher_client or TeacherClient(
            base_url=teacher_base_url,
            model=teacher_model,
            api_key=teacher_api_key,
            api_key_env=teacher_api_key_env,
            timeout_seconds=float(teacher_timeout_seconds),
            enable_thinking=bool(teacher_enable_thinking),
            annotation_max_tokens=int(teacher_max_tokens),
            repair_max_tokens=int(teacher_max_tokens),
        )
        self.base_url = clean_text(teacher_base_url)
        self.model = clean_text(teacher_model)
        self.max_tokens = int(teacher_max_tokens)
        self.auth_mode = clean_text(getattr(self._teacher_client, "auth_mode", "")) or "none"
        self.api_key_source = clean_text(getattr(self._teacher_client, "api_key_source", ""))

    def healthcheck(self) -> Dict[str, Any]:
        return dict(self._teacher_client.healthcheck() or {})

    def review_query_row(
        self,
        *,
        question: str,
        answer_type: str,
        question_features: Mapping[str, Any],
        current_positive_event_ids: Sequence[str],
        event_options: Sequence[Mapping[str, Any]],
        review_flags: Sequence[str],
    ) -> Dict[str, Any]:
        allowed_event_ids = [clean_text(item.get("event_id", "")) for item in event_options if clean_text(item.get("event_id", ""))]
        prompt = {
            "question": clean_text(question),
            "current_answer_type": clean_text(answer_type),
            "question_features": dict(question_features or {}),
            "current_positive_event_ids": [clean_text(item) for item in current_positive_event_ids if clean_text(item)],
            "allowed_event_ids": list(allowed_event_ids),
            "event_options": [dict(item) for item in event_options],
            "review_flags": [clean_text(item) for item in review_flags if clean_text(item)],
            "allowed_answer_types": sorted(ALLOWED_ANSWER_TYPES),
        }
        excerpt = clean_text(question)[:400]
        raw_output = self._teacher_client._chat_json_content(
            system_content=(
                "You are reviewing node-memory training supervision. "
                "Return exactly one JSON object with keys: decision, answer_type, selected_positive_event_ids, reject_reason, notes. "
                "decision must be accept, edit, or reject. "
                "answer_type must be one of: time, profile, event_text, multi_evidence, abstain. "
                "selected_positive_event_ids must contain only ids from allowed_event_ids. "
                "Use reject when none of the listed events support the question. "
                "Do not invent ids, do not output markdown, and do not include extra text."
            ),
            user_payload=prompt,
            excerpt=excerpt,
            max_tokens=self.max_tokens,
        )
        parsed = None
        try:
            parsed = json.loads(raw_output)
        except Exception:
            for candidate in _extract_braced_objects(raw_output):
                try:
                    parsed = json.loads(candidate)
                    break
                except Exception:
                    continue
        if not isinstance(parsed, Mapping):
            raise TeacherTurnError("teacher_invalid_json", "teacher_query_review_parse", excerpt, clean_text(raw_output))
        decision = clean_text(parsed.get("decision", ""))
        answer_type_value = clean_text(parsed.get("answer_type", ""))
        selected_positive_event_ids = [
            clean_text(item)
            for item in list(parsed.get("selected_positive_event_ids", []) or [])
            if clean_text(item)
        ]
        reject_reason = clean_text(parsed.get("reject_reason", ""))
        notes = clean_text(parsed.get("notes", ""))
        if decision not in TEACHER_DECISIONS:
            raise TeacherTurnError("teacher_invalid_json", "teacher_query_review_decision", excerpt, clean_text(raw_output))
        if answer_type_value not in ALLOWED_ANSWER_TYPES:
            raise TeacherTurnError("teacher_invalid_json", "teacher_query_review_answer_type", excerpt, clean_text(raw_output))
        invalid_ids = [item for item in selected_positive_event_ids if item not in set(allowed_event_ids)]
        if invalid_ids:
            raise TeacherTurnError("teacher_invalid_json", "teacher_query_review_event_ids", excerpt, clean_text(raw_output))
        return {
            "decision": decision,
            "answer_type": answer_type_value,
            "selected_positive_event_ids": dedupe_texts(selected_positive_event_ids),
            "reject_reason": reject_reason,
            "notes": notes,
            "raw_output": clean_text(raw_output),
        }


class GraphSummaryCache:
    def __init__(self, *, graphs_dir: Path, output_graphs_dir: Path, max_size: int) -> None:
        self._graphs_dir = graphs_dir
        self._output_graphs_dir = output_graphs_dir
        self._max_size = max(1, int(max_size))
        self._cache: OrderedDict[str, GraphSummary] = OrderedDict()
        self._failures: Dict[str, str] = {}

    def failure(self, conversation_id: str) -> str:
        return clean_text(self._failures.get(clean_text(conversation_id), ""))

    def get(self, conversation_id: str) -> GraphSummary | None:
        normalized_id = clean_text(conversation_id)
        if not normalized_id:
            return None
        if normalized_id in self._cache:
            item = self._cache.pop(normalized_id)
            self._cache[normalized_id] = item
            return item
        if normalized_id in self._failures:
            return None
        source_path = self._graphs_dir / f"{normalized_id}.json"
        if not source_path.exists():
            self._failures[normalized_id] = "missing_graph_file"
            return None
        try:
            payload = dict(json.loads(source_path.read_text(encoding="utf-8")))
            summary = _sanitize_graph_payload(
                payload=payload,
                expected_conversation_id=normalized_id,
                source_path=source_path,
            )
        except Exception as exc:
            self._failures[normalized_id] = f"{type(exc).__name__}:{clean_text(str(exc))}"
            return None
        self._cache[normalized_id] = summary
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)
        return summary

    def materialize(self, summary: GraphSummary) -> str:
        destination = self._output_graphs_dir / f"{summary.conversation_id}.json"
        if summary.materialized and destination.exists():
            return "existing"
        if summary.graph_repair_codes:
            destination.parent.mkdir(parents=True, exist_ok=True)
            write_json(destination, summary.graph_payload)
            summary.materialized = True
            return "rewritten"
        mode = _materialize_path(source=summary.source_path, destination=destination)
        summary.materialized = True
        return mode


def _sanitize_graph_payload(
    *,
    payload: Mapping[str, Any],
    expected_conversation_id: str,
    source_path: Path,
) -> GraphSummary:
    graph = dict(payload or {})
    repair_codes: List[str] = []
    conversation_id = clean_text(graph.get("conversation_id", "")) or clean_text(expected_conversation_id)
    if conversation_id != clean_text(expected_conversation_id):
        graph["conversation_id"] = clean_text(expected_conversation_id)
        conversation_id = clean_text(expected_conversation_id)
        repair_codes.append("graph_conversation_id_rewritten")

    nodes: List[Dict[str, Any]] = []
    node_ids: set[str] = set()
    for raw_node in list(graph.get("nodes", []) or []):
        if not isinstance(raw_node, Mapping):
            repair_codes.append("graph_non_mapping_node_dropped")
            continue
        node = dict(raw_node)
        node_id = clean_text(node.get("id", ""))
        if not node_id:
            repair_codes.append("graph_empty_node_id_dropped")
            continue
        if node_id in node_ids:
            repair_codes.append("graph_duplicate_node_dropped")
            continue
        node["id"] = node_id
        node_type = clean_text(node.get("type", ""))
        if node_type:
            node["type"] = node_type
        metadata = dict(node.get("metadata", {}) or {})
        if node_type == "event":
            event_signature = clean_text(node.get("event_signature", metadata.get("event_signature", "")))
            if not event_signature:
                event_signature = _derive_event_signature(
                    event_text=clean_text(node.get("text", "")),
                    speaker=clean_text(node.get("speaker", metadata.get("speaker", ""))),
                    semantic_slot=_event_signature_semantic_slot(node),
                )
                if event_signature:
                    node["event_signature"] = event_signature
                    metadata["event_signature"] = event_signature
                    node["metadata"] = metadata
                    repair_codes.append("graph_event_signature_added")
        nodes.append(node)
        node_ids.add(node_id)

    edges: List[Dict[str, Any]] = []
    seen_edge_keys: set[str] = set()
    for raw_edge in list(graph.get("edges", []) or []):
        if not isinstance(raw_edge, Mapping):
            repair_codes.append("graph_non_mapping_edge_dropped")
            continue
        edge = dict(raw_edge)
        source = clean_text(edge.get("source", ""))
        target = clean_text(edge.get("target", ""))
        edge_type = clean_text(edge.get("type", ""))
        if not source or not target or not edge_type:
            repair_codes.append("graph_incomplete_edge_dropped")
            continue
        if source not in node_ids or target not in node_ids:
            repair_codes.append("graph_orphan_edge_dropped")
            continue
        edge_id = clean_text(edge.get("id", "")) or f"{source}->{target}:{edge_type}"
        if edge_id in seen_edge_keys:
            repair_codes.append("graph_duplicate_edge_dropped")
            continue
        edge["id"] = edge_id
        edge["source"] = source
        edge["target"] = target
        edge["type"] = edge_type
        edges.append(edge)
        seen_edge_keys.add(edge_id)

    node_by_id = {clean_text(node.get("id", "")): dict(node) for node in nodes}
    paths: List[Dict[str, Any]] = []
    seen_path_ids: set[str] = set()
    existing_path_keys: set[tuple[str, str, str]] = set()
    for raw_path in list(graph.get("paths", []) or []):
        if not isinstance(raw_path, Mapping):
            repair_codes.append("graph_non_mapping_path_dropped")
            continue
        path = dict(raw_path)
        event_id = clean_text(path.get("event_id", ""))
        node_ids_in_path = [clean_text(item) for item in list(path.get("node_ids", []) or []) if clean_text(item)]
        path_type = clean_text(path.get("type", ""))
        if path_type not in ALLOWED_PATH_TYPES or len(node_ids_in_path) < 3:
            repair_codes.append("graph_invalid_path_dropped")
            continue
        if event_id not in node_by_id or clean_text(node_by_id[event_id].get("type", "")) != "event":
            repair_codes.append("graph_orphan_path_dropped")
            continue
        if any(item not in node_by_id for item in node_ids_in_path):
            repair_codes.append("graph_orphan_path_dropped")
            continue
        if clean_text(node_ids_in_path[1]) != event_id:
            repair_codes.append("graph_misaligned_path_dropped")
            continue
        support_node_id = clean_text(node_ids_in_path[2])
        path_id = clean_text(path.get("id", "")) or build_path_id(event_id, path_type, support_node_id)
        if path_id in seen_path_ids:
            repair_codes.append("graph_duplicate_path_dropped")
            continue
        path["id"] = path_id
        path["event_id"] = event_id
        path["type"] = path_type
        path["node_ids"] = node_ids_in_path
        paths.append(path)
        seen_path_ids.add(path_id)
        existing_path_keys.add((event_id, path_type, support_node_id))

    for node in nodes:
        if clean_text(node.get("type", "")) != "event":
            continue
        event_id = clean_text(node.get("id", ""))
        speaker = clean_text(node.get("speaker", dict(node.get("metadata", {}) or {}).get("speaker", "")))
        speaker_node_id = f"{conversation_id}:speaker:{normalize_text(speaker).replace(' ', '_') or 'speaker'}"
        if speaker_node_id not in node_by_id:
            continue
        default_supports = {
            "speaker_event_time": [f"{event_id}:time"] if f"{event_id}:time" in node_by_id else [],
            "speaker_event_profile": sorted(
                node_id for node_id in node_by_id if node_id.startswith(f"{event_id}:profile:")
            ),
            "speaker_event_status": [f"{event_id}:status"] if f"{event_id}:status" in node_by_id else [],
            "speaker_event_source_turn": [f"{event_id}:source_turn"] if f"{event_id}:source_turn" in node_by_id else [],
        }
        for path_type, support_node_ids in default_supports.items():
            for support_node_id in support_node_ids:
                key = (event_id, path_type, support_node_id)
                if key in existing_path_keys:
                    continue
                paths.append(
                    {
                        "id": build_path_id(event_id, path_type, support_node_id),
                        "type": path_type,
                        "event_id": event_id,
                        "node_ids": [speaker_node_id, event_id, support_node_id],
                    }
                )
                existing_path_keys.add(key)
                seen_path_ids.add(build_path_id(event_id, path_type, support_node_id))
                repair_codes.append("graph_default_path_rebuilt")

    graph["conversation_id"] = conversation_id
    graph["nodes"] = nodes
    graph["edges"] = edges
    graph["paths"] = paths
    event_catalog = _event_catalog_from_graph(graph)
    if not event_catalog:
        raise RuntimeError(f"no_event_nodes:{source_path}")
    return GraphSummary(
        conversation_id=conversation_id,
        source_path=source_path,
        graph_payload=graph,
        event_catalog=event_catalog,
        event_ids={clean_text(item) for item in event_catalog if clean_text(item)},
        time_node_ids={
            clean_text(time_node_id)
            for event_meta in event_catalog.values()
            for time_node_id in list(dict(event_meta).get("time_node_ids", []) or [])
            if clean_text(time_node_id)
        },
        path_ids={clean_text(path.get("id", "")) for path in paths if clean_text(path.get("id", ""))},
        graph_repair_codes=dedupe_texts(repair_codes),
    )


def _candidate_event_options(
    *,
    question: str,
    question_features: Mapping[str, Any],
    graph_summary: GraphSummary,
    positive_event_ids: Sequence[str],
    existing_candidate_event_ids: Sequence[str],
    max_negative_options: int = 4,
) -> List[Dict[str, Any]]:
    positive_ids = [clean_text(item) for item in positive_event_ids if clean_text(item)]
    positive_events = [dict(graph_summary.event_catalog.get(event_id, {}) or {}) for event_id in positive_ids if clean_text(event_id)]
    option_ids = list(positive_ids)
    negative_scores: List[tuple[float, str]] = []
    positive_speakers = {
        normalize_text(event.get("speaker", ""))
        for event in positive_events
        if clean_text(event.get("speaker", ""))
    }
    positive_profile_types = {
        clean_text(event.get("profile_type", ""))
        for event in positive_events
        if clean_text(event.get("profile_type", ""))
    }
    positive_statuses = {
        clean_text(event.get("target_status", ""))
        for event in positive_events
        if clean_text(event.get("target_status", ""))
    }
    positive_time_granularities = {
        clean_text(event.get("time_granularity", ""))
        for event in positive_events
        if clean_text(event.get("time_granularity", ""))
    }
    positive_sessions = {
        clean_text(event.get("session_name", ""))
        for event in positive_events
        if clean_text(event.get("session_name", ""))
    }
    positive_turn_indices = [int(event.get("turn_index", 0) or 0) for event in positive_events if int(event.get("turn_index", 0) or 0) > 0]
    speaker_targets = {
        normalize_text(item)
        for item in list(question_features.get("speaker_candidates", []) or [])
        if clean_text(item)
    }
    candidate_pool = dedupe_texts(
        list(existing_candidate_event_ids)
        + [
            event_id
            for event_id in graph_summary.event_catalog
            if clean_text(event_id) not in set(positive_ids)
        ]
    )
    for event_id in candidate_pool:
        normalized_id = clean_text(event_id)
        if not normalized_id or normalized_id in set(positive_ids):
            continue
        event_meta = dict(graph_summary.event_catalog.get(normalized_id, {}) or {})
        if not event_meta:
            continue
        score = _candidate_hard_negative_score(
            question=question,
            question_features=question_features,
            event_meta=event_meta,
            positive_events=positive_events,
            speaker_targets=speaker_targets,
            positive_speakers=positive_speakers,
            positive_profile_types=positive_profile_types,
            positive_statuses=positive_statuses,
            positive_time_granularities=positive_time_granularities,
            positive_sessions=positive_sessions,
            positive_turn_indices=positive_turn_indices,
        )
        negative_scores.append((float(score), normalized_id))
    negative_scores.sort(key=lambda item: (-item[0], item[1]))
    for _, event_id in negative_scores[: max(0, int(max_negative_options))]:
        if event_id not in option_ids:
            option_ids.append(event_id)
    return [
        {
            "event_id": clean_text(event_id),
            "speaker": clean_text(event_meta.get("speaker", "")),
            "event_signature": clean_text(event_meta.get("event_signature", "")),
            "event_text": clean_text(event_meta.get("event_text", "")),
            "time_display_value": clean_text(event_meta.get("time_display_value", "")),
            "time_value": clean_text(event_meta.get("time_value", "")),
            "time_granularity": clean_text(event_meta.get("time_granularity", "")),
            "target_status": clean_text(event_meta.get("target_status", "")),
            "profile_type": clean_text(event_meta.get("profile_type", "")),
            "profile_value": clean_text(event_meta.get("profile_value", "")),
            "session_name": clean_text(event_meta.get("session_name", "")),
            "turn_index": int(event_meta.get("turn_index", 0) or 0),
        }
        for event_id in option_ids
        for event_meta in [dict(graph_summary.event_catalog.get(event_id, {}) or {})]
        if event_meta
    ]


def _normalize_row(payload: Mapping[str, Any], *, split: str, line_number: int) -> Dict[str, Any]:
    conversation_id = clean_text(payload.get("conversation_id", ""))
    question = clean_text(payload.get("question", ""))
    positive_event_ids = dedupe_texts(payload.get("positive_event_ids", []) or [])
    question_id = clean_text(payload.get("question_id", ""))
    question_features = {
        **dict(payload.get("question_features", {}) or {}),
        **extract_question_features(question),
    }
    answer_targets = dict(payload.get("answer_targets", {}) or {})
    normalized_answer = _normalized_answer_type(
        clean_text(answer_targets.get("answer_type", payload.get("answer_type", ""))),
        question=question,
        positive_event_ids=positive_event_ids,
    )
    answer_targets["answer_type"] = normalized_answer
    if not question_id:
        question_id = _fallback_question_id(
            conversation_id=conversation_id,
            question=question,
            split=split,
            line_number=line_number,
            positive_event_ids=positive_event_ids,
        )
    return {
        "conversation_id": conversation_id,
        "question_id": question_id,
        "question": question,
        "question_features": question_features,
        "candidate_event_ids": dedupe_texts(payload.get("candidate_event_ids", []) or []),
        "positive_event_ids": list(positive_event_ids),
        "positive_path_ids": dedupe_texts(payload.get("positive_path_ids", []) or []),
        "positive_time_node_ids": dedupe_texts(payload.get("positive_time_node_ids", []) or []),
        "negative_event_ids": dedupe_texts(payload.get("negative_event_ids", []) or []),
        "answer_targets": answer_targets,
        "temporal_target": dict(payload.get("temporal_target", {}) or {}),
        "event_catalog_size": int(payload.get("event_catalog_size", 0) or 0),
        "metadata": dict(payload.get("metadata", {}) or {}),
    }


def _repair_row_against_graph(
    *,
    row: MutableMapping[str, Any],
    graph_summary: GraphSummary,
) -> tuple[Dict[str, Any] | None, List[str], List[str]]:
    repair_codes: List[str] = []
    fatal_codes: List[str] = []
    question = clean_text(row.get("question", ""))
    answer_targets = dict(row.get("answer_targets", {}) or {})
    answer_type = _normalized_answer_type(
        clean_text(answer_targets.get("answer_type", "")),
        question=question,
        positive_event_ids=list(row.get("positive_event_ids", []) or []),
    )
    answer_targets["answer_type"] = answer_type
    row["answer_targets"] = answer_targets

    if not clean_text(row.get("conversation_id", "")):
        fatal_codes.append("missing_conversation_id")
        return None, repair_codes, fatal_codes
    if not question:
        fatal_codes.append("missing_question")
        return None, repair_codes, fatal_codes
    if not clean_text(row.get("question_id", "")):
        fatal_codes.append("missing_question_id")
        return None, repair_codes, fatal_codes

    positive_event_ids = [
        event_id
        for event_id in list(row.get("positive_event_ids", []) or [])
        if clean_text(event_id) in graph_summary.event_ids
    ]
    if positive_event_ids != list(row.get("positive_event_ids", []) or []):
        repair_codes.append("invalid_positive_event_ids_removed")
        row["positive_event_ids"] = list(positive_event_ids)

    if answer_type != "abstain" and not positive_event_ids:
        fatal_codes.append("missing_positive_event_supervision")
        return None, repair_codes, fatal_codes

    if answer_type == "abstain" and positive_event_ids:
        answer_type = answer_type_from_query(
            question,
            positive_event_ids=positive_event_ids,
            question_features=extract_question_features(question),
        )
        row["answer_targets"] = {**answer_targets, "answer_type": answer_type}
        repair_codes.append("answer_type_recomputed_from_positive_events")

    question_features = {
        **dict(row.get("question_features", {}) or {}),
        **extract_question_features(question),
    }
    row["question_features"] = question_features

    existing_candidates = [
        event_id
        for event_id in list(row.get("candidate_event_ids", []) or [])
        if clean_text(event_id) in graph_summary.event_ids
    ]
    existing_negatives = [
        event_id
        for event_id in list(row.get("negative_event_ids", []) or [])
        if clean_text(event_id) in graph_summary.event_ids and clean_text(event_id) not in set(positive_event_ids)
    ]
    if answer_type == "abstain":
        abstain_bundle = _build_abstain_candidates(question=question, event_catalog=graph_summary.event_catalog)
        if not existing_candidates and abstain_bundle["candidate_event_ids"]:
            existing_candidates = list(abstain_bundle["candidate_event_ids"])
            existing_negatives = list(abstain_bundle["negative_event_ids"])
            repair_codes.append("abstain_candidates_built")
    else:
        rebuilt_bundle = _candidate_bundle(
            question=question,
            question_features=question_features,
            event_catalog=graph_summary.event_catalog,
            positive_event_ids=positive_event_ids,
        )
        rebuilt_candidates = [item for item in list(rebuilt_bundle.get("candidate_event_ids", []) or []) if clean_text(item)]
        rebuilt_negatives = [item for item in list(rebuilt_bundle.get("negative_event_ids", []) or []) if clean_text(item)]
        if (
            not existing_candidates
            or any(item not in existing_candidates for item in positive_event_ids)
            or not existing_negatives
        ):
            existing_candidates = list(rebuilt_candidates)
            existing_negatives = list(rebuilt_negatives)
            repair_codes.append("candidate_bundle_rebuilt")
    existing_candidates = dedupe_texts(list(positive_event_ids) + list(existing_candidates))
    existing_negatives = [item for item in dedupe_texts(existing_negatives) if item in set(existing_candidates) and item not in set(positive_event_ids)]
    row["candidate_event_ids"] = list(existing_candidates)
    row["negative_event_ids"] = list(existing_negatives)
    if answer_type != "abstain" and not row["candidate_event_ids"]:
        fatal_codes.append("empty_candidate_event_ids")
        return None, repair_codes, fatal_codes

    canonical_positive_time_node_ids = _positive_time_node_ids(graph_summary.event_catalog, positive_event_ids)
    if dedupe_texts(row.get("positive_time_node_ids", []) or []) != list(canonical_positive_time_node_ids):
        row["positive_time_node_ids"] = list(canonical_positive_time_node_ids)
        repair_codes.append("positive_time_node_ids_rebuilt")

    canonical_positive_path_ids = _positive_path_ids(
        list(graph_summary.graph_payload.get("paths", []) or []),
        positive_event_ids,
        answer_type=answer_type,
        question_features=question_features,
    )
    if dedupe_texts(row.get("positive_path_ids", []) or []) != list(canonical_positive_path_ids):
        row["positive_path_ids"] = list(canonical_positive_path_ids)
        repair_codes.append("positive_path_ids_rebuilt")

    if answer_type == "time" and not row["positive_time_node_ids"]:
        fatal_codes.append("time_answer_without_time_support")
        return None, repair_codes, fatal_codes
    if answer_type in {"time", "profile", "event_text"} and not row["positive_path_ids"]:
        fatal_codes.append("typed_answer_without_positive_paths")
        return None, repair_codes, fatal_codes
    if answer_type == "multi_evidence" and len(positive_event_ids) < 2:
        fatal_codes.append("multi_evidence_without_multiple_events")
        return None, repair_codes, fatal_codes

    canonical_temporal_target = _temporal_target(
        question_features,
        graph_summary.event_catalog,
        positive_event_ids,
        positive_time_node_ids=row.get("positive_time_node_ids", []) or [],
        answer_type=answer_type,
    )
    if dict(row.get("temporal_target", {}) or {}) != dict(canonical_temporal_target):
        row["temporal_target"] = dict(canonical_temporal_target)
        repair_codes.append("temporal_target_rebuilt")

    graph_event_count = len(graph_summary.event_catalog)
    if int(row.get("event_catalog_size", 0) or 0) != graph_event_count:
        row["event_catalog_size"] = int(graph_event_count)
        repair_codes.append("event_catalog_size_rewritten")

    row["metadata"] = dict(row.get("metadata", {}) or {})
    row["metadata"]["answer_type"] = answer_type
    return dict(row), dedupe_texts(repair_codes), fatal_codes


def _teacher_review_flags(
    *,
    row: Mapping[str, Any],
    graph_summary: GraphSummary,
    repair_codes: Sequence[str],
    min_event_anchor_coverage: float,
) -> List[str]:
    flags = list(repair_codes)
    question_features = dict(row.get("question_features", {}) or {})
    positive_events = [
        dict(graph_summary.event_catalog.get(event_id, {}) or {})
        for event_id in list(row.get("positive_event_ids", []) or [])
        if clean_text(event_id) in graph_summary.event_catalog
    ]
    answer_type = clean_text(dict(row.get("answer_targets", {}) or {}).get("answer_type", ""))
    metadata = dict(row.get("metadata", {}) or {})
    synthetic_kind = clean_text(metadata.get("synthetic_kind", ""))
    if answer_type == "event_text" and synthetic_kind == "event_text":
        best_anchor = _best_anchor_coverage(question_features, positive_events)
        if best_anchor < float(min_event_anchor_coverage):
            flags.append("weak_synthetic_event_anchor")
    speaker_candidates = {
        normalize_text(item)
        for item in list(question_features.get("speaker_candidates", []) or [])
        if clean_text(item) and normalize_text(item) not in {"speaker", "user", "assistant", "person", "someone"}
    }
    positive_speakers = {
        normalize_text(event.get("speaker", ""))
        for event in positive_events
        if clean_text(event.get("speaker", ""))
    }
    if speaker_candidates and positive_speakers and positive_speakers.isdisjoint(speaker_candidates):
        flags.append("speaker_positive_conflict")
    return dedupe_texts(flags)


def _filter_auxiliary_jsonl(
    *,
    source_path: Path,
    destination_path: Path,
    kept_conversation_ids: set[str],
) -> int:
    kept_count = 0
    if not source_path.exists():
        return 0
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with destination_path.open("wb") as handle:
        for _, line in _iter_jsonl(source_path):
            payload = dict(json.loads(line))
            conversation_id = clean_text(payload.get("conversation_id", ""))
            if conversation_id not in kept_conversation_ids:
                continue
            handle.write(_jsonl_bytes(payload))
            kept_count += 1
    return kept_count


def filter_node_training_dataset(
    *,
    input_dir: Path,
    output_dir: Path,
    teacher_review_mode: str = "off",
    teacher_base_url: str = DEFAULT_TEACHER_BASE_URL,
    teacher_model: str = DEFAULT_TEACHER_MODEL,
    teacher_api_key: str = "",
    teacher_api_key_env: str = "",
    teacher_timeout_seconds: float = 60.0,
    teacher_enable_thinking: bool = False,
    teacher_max_tokens: int = 256,
    min_event_anchor_coverage: float = 0.34,
    graph_cache_size: int = DEFAULT_GRAPH_CACHE_SIZE,
    teacher_client: Any | None = None,
) -> Dict[str, Any]:
    source_dir = Path(input_dir)
    output_dir = Path(output_dir)
    queries_dir = source_dir / "queries"
    graphs_dir = source_dir / "graphs"
    if not queries_dir.exists() or not graphs_dir.exists():
        raise FileNotFoundError(f"Expected graphs/ and queries/ under {source_dir}")

    config = QualityFilterConfig(
        input_dir=source_dir,
        output_dir=output_dir,
        teacher_review_mode=clean_text(teacher_review_mode) or "off",
        teacher_base_url=teacher_base_url,
        teacher_model=teacher_model,
        teacher_api_key=teacher_api_key,
        teacher_api_key_env=teacher_api_key_env,
        teacher_timeout_seconds=float(teacher_timeout_seconds),
        teacher_enable_thinking=bool(teacher_enable_thinking),
        teacher_max_tokens=int(teacher_max_tokens),
        min_event_anchor_coverage=float(min_event_anchor_coverage),
        graph_cache_size=int(graph_cache_size),
    )
    if config.teacher_review_mode not in {"off", "flagged", "all"}:
        raise ValueError(f"Unsupported teacher_review_mode: {config.teacher_review_mode}")

    output_dir.mkdir(parents=True, exist_ok=True)
    _clear_output(output_dir)
    output_graphs_dir = output_dir / "graphs"
    output_queries_dir = output_dir / "queries"
    output_graphs_dir.mkdir(parents=True, exist_ok=True)
    output_queries_dir.mkdir(parents=True, exist_ok=True)
    _log(
        "filter_started",
        input_dir=str(source_dir),
        output_dir=str(output_dir),
        teacher_review_mode=config.teacher_review_mode,
    )

    source_manifest = _read_json(source_dir / "export_manifest.json") if (source_dir / "export_manifest.json").exists() else {}
    graph_cache = GraphSummaryCache(
        graphs_dir=graphs_dir,
        output_graphs_dir=output_graphs_dir,
        max_size=int(config.graph_cache_size),
    )

    reviewer: Any | None = None
    if config.teacher_review_mode != "off":
        reviewer = teacher_client or TeacherQueryReviewer(
            teacher_client=None,
            teacher_base_url=config.teacher_base_url,
            teacher_model=config.teacher_model,
            teacher_api_key=config.teacher_api_key,
            teacher_api_key_env=config.teacher_api_key_env,
            teacher_timeout_seconds=config.teacher_timeout_seconds,
            teacher_enable_thinking=config.teacher_enable_thinking,
            teacher_max_tokens=config.teacher_max_tokens,
        )
        health = dict(reviewer.healthcheck() or {})
        if not bool(health.get("ok", False)):
            manifest = {
                "status": "blocked",
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "input_dir": str(source_dir),
                "output_dir": str(output_dir),
                "teacher_review_mode": config.teacher_review_mode,
                "teacher_auth_mode": clean_text(getattr(reviewer, "auth_mode", "")) or "none",
                "teacher_api_key_source": clean_text(getattr(reviewer, "api_key_source", "")),
                "teacher_healthcheck": health,
            }
            write_json(output_dir / "filter_manifest.json", manifest)
            write_json(output_dir / "export_manifest.json", manifest)
            return manifest

    source_query_counts: Dict[str, int] = {}
    kept_query_counts: Dict[str, int] = {}
    dropped_query_counts: Dict[str, int] = {}
    repaired_query_counts: Dict[str, int] = {}
    auxiliary_counts: Dict[str, int] = {}
    graph_materialization_modes: Counter[str] = Counter()
    drop_reason_counts: Counter[str] = Counter()
    repair_reason_counts: Counter[str] = Counter()
    teacher_decision_counts: Counter[str] = Counter()
    teacher_reject_reason_counts: Counter[str] = Counter()
    graph_repair_reason_counts: Counter[str] = Counter()
    kept_conversation_ids: set[str] = set()
    seen_question_ids: set[str] = set()
    seen_row_signatures: set[str] = set()
    kept_graph_count = 0
    dropped_graph_references = 0

    dropped_rows_path = output_dir / "dropped_rows.jsonl"
    repaired_rows_path = output_dir / "repaired_rows.jsonl"
    teacher_review_path = output_dir / "teacher_review_decisions.jsonl"

    started_at = time.time()
    for split in ("train", "val", "test"):
        source_query_path = queries_dir / f"{split}.jsonl"
        source_query_counts[split] = 0
        kept_query_counts[split] = 0
        dropped_query_counts[split] = 0
        repaired_query_counts[split] = 0
        destination_path = output_queries_dir / f"{split}.jsonl"
        with destination_path.open("wb") as output_handle:
            for line_number, line in _iter_jsonl(source_query_path):
                source_query_counts[split] += 1
                if source_query_counts[split] == 1 or source_query_counts[split] % 5000 == 0:
                    _log(
                        "query_filter_progress",
                        split=split,
                        processed=int(source_query_counts[split]),
                        kept=int(kept_query_counts[split]),
                        dropped=int(dropped_query_counts[split]),
                        repaired=int(repaired_query_counts[split]),
                        teacher_reviews=int(sum(teacher_decision_counts.values())),
                    )
                try:
                    payload = dict(json.loads(line))
                except Exception as exc:
                    drop_reason_counts["malformed_query_json"] += 1
                    dropped_query_counts[split] += 1
                    _append_jsonl(
                        dropped_rows_path,
                        {
                            "split": split,
                            "line_number": int(line_number),
                            "reason_codes": ["malformed_query_json"],
                            "error_type": type(exc).__name__,
                            "error_message": clean_text(str(exc)),
                        },
                    )
                    continue
                row = _normalize_row(payload, split=split, line_number=line_number)
                conversation_id = clean_text(row.get("conversation_id", ""))
                question_id = clean_text(row.get("question_id", ""))
                summary = graph_cache.get(conversation_id)
                if summary is None:
                    reason = graph_cache.failure(conversation_id) or "graph_unavailable"
                    drop_reason_counts[reason] += 1
                    dropped_query_counts[split] += 1
                    dropped_graph_references += 1
                    _append_jsonl(
                        dropped_rows_path,
                        {
                            "split": split,
                            "line_number": int(line_number),
                            "conversation_id": conversation_id,
                            "question_id": question_id,
                            "question": clean_text(row.get("question", ""))[:240],
                            "reason_codes": [reason],
                        },
                    )
                    continue
                for code in summary.graph_repair_codes:
                    graph_repair_reason_counts[code] += 1
                repaired_row, repair_codes, fatal_codes = _repair_row_against_graph(row=row, graph_summary=summary)
                if repaired_row is None:
                    for code in fatal_codes:
                        drop_reason_counts[code] += 1
                    dropped_query_counts[split] += 1
                    _append_jsonl(
                        dropped_rows_path,
                        {
                            "split": split,
                            "line_number": int(line_number),
                            "conversation_id": conversation_id,
                            "question_id": question_id,
                            "question": clean_text(row.get("question", ""))[:240],
                            "reason_codes": list(fatal_codes),
                            "repair_codes": list(repair_codes),
                        },
                    )
                    continue

                row = dict(repaired_row)
                question_id = clean_text(row.get("question_id", ""))
                if question_id in seen_question_ids:
                    drop_reason_counts["duplicate_question_id"] += 1
                    dropped_query_counts[split] += 1
                    _append_jsonl(
                        dropped_rows_path,
                        {
                            "split": split,
                            "line_number": int(line_number),
                            "conversation_id": conversation_id,
                            "question_id": question_id,
                            "question": clean_text(row.get("question", ""))[:240],
                            "reason_codes": ["duplicate_question_id"],
                        },
                    )
                    continue

                row_signature = _row_signature(
                    conversation_id=conversation_id,
                    question=clean_text(row.get("question", "")),
                    answer_type=clean_text(dict(row.get("answer_targets", {}) or {}).get("answer_type", "")),
                    positive_event_ids=list(row.get("positive_event_ids", []) or []),
                )
                if row_signature in seen_row_signatures:
                    drop_reason_counts["duplicate_question_signature"] += 1
                    dropped_query_counts[split] += 1
                    _append_jsonl(
                        dropped_rows_path,
                        {
                            "split": split,
                            "line_number": int(line_number),
                            "conversation_id": conversation_id,
                            "question_id": question_id,
                            "question": clean_text(row.get("question", ""))[:240],
                            "reason_codes": ["duplicate_question_signature"],
                        },
                    )
                    continue

                teacher_flags = _teacher_review_flags(
                    row=row,
                    graph_summary=summary,
                    repair_codes=repair_codes,
                    min_event_anchor_coverage=config.min_event_anchor_coverage,
                )
                if reviewer is not None and (
                    config.teacher_review_mode == "all"
                    or (config.teacher_review_mode == "flagged" and bool(teacher_flags))
                ):
                    event_options = _candidate_event_options(
                        question=clean_text(row.get("question", "")),
                        question_features=dict(row.get("question_features", {}) or {}),
                        graph_summary=summary,
                        positive_event_ids=list(row.get("positive_event_ids", []) or []),
                        existing_candidate_event_ids=list(row.get("candidate_event_ids", []) or []),
                    )
                    try:
                        teacher_decision_payload = dict(
                            reviewer.review_query_row(
                                question=clean_text(row.get("question", "")),
                                answer_type=clean_text(dict(row.get("answer_targets", {}) or {}).get("answer_type", "")),
                                question_features=dict(row.get("question_features", {}) or {}),
                                current_positive_event_ids=list(row.get("positive_event_ids", []) or []),
                                event_options=event_options,
                                review_flags=teacher_flags,
                            )
                        )
                    except TeacherTurnError as exc:
                        reason = clean_text(exc.error_code) or "teacher_review_error"
                        drop_reason_counts[reason] += 1
                        dropped_query_counts[split] += 1
                        _append_jsonl(
                            dropped_rows_path,
                            {
                                "split": split,
                                "line_number": int(line_number),
                                "conversation_id": conversation_id,
                                "question_id": question_id,
                                "question": clean_text(row.get("question", ""))[:240],
                                "reason_codes": [reason],
                                "teacher_error_stage": clean_text(exc.error_stage),
                            },
                        )
                        continue
                    decision = clean_text(teacher_decision_payload.get("decision", ""))
                    teacher_decision_counts[decision] += 1
                    if decision == "reject":
                        reject_reason = clean_text(teacher_decision_payload.get("reject_reason", "")) or "teacher_reject"
                        teacher_reject_reason_counts[reject_reason] += 1
                        drop_reason_counts["teacher_reject"] += 1
                        dropped_query_counts[split] += 1
                        _append_jsonl(
                            dropped_rows_path,
                            {
                                "split": split,
                                "line_number": int(line_number),
                                "conversation_id": conversation_id,
                                "question_id": question_id,
                                "question": clean_text(row.get("question", ""))[:240],
                                "reason_codes": ["teacher_reject"],
                                "teacher_reject_reason": reject_reason,
                                "teacher_flags": list(teacher_flags),
                            },
                        )
                        _append_jsonl(
                            teacher_review_path,
                            {
                                "split": split,
                                "line_number": int(line_number),
                                "conversation_id": conversation_id,
                                "question_id": question_id,
                                "teacher_flags": list(teacher_flags),
                                "teacher_review": teacher_decision_payload,
                            },
                        )
                        continue
                    selected_ids = list(teacher_decision_payload.get("selected_positive_event_ids", []) or [])
                    teacher_answer_type = clean_text(teacher_decision_payload.get("answer_type", ""))
                    if decision == "edit" or (
                        teacher_answer_type
                        and teacher_answer_type != clean_text(dict(row.get("answer_targets", {}) or {}).get("answer_type", ""))
                    ) or (
                        selected_ids
                        and selected_ids != list(row.get("positive_event_ids", []) or [])
                    ):
                        if selected_ids:
                            row["positive_event_ids"] = list(selected_ids)
                        row["answer_targets"] = {
                            **dict(row.get("answer_targets", {}) or {}),
                            "answer_type": teacher_answer_type or clean_text(dict(row.get("answer_targets", {}) or {}).get("answer_type", "")),
                        }
                        repaired_row, post_teacher_repairs, post_teacher_fatal = _repair_row_against_graph(
                            row=row,
                            graph_summary=summary,
                        )
                        if repaired_row is None:
                            for code in post_teacher_fatal:
                                drop_reason_counts[code] += 1
                            dropped_query_counts[split] += 1
                            _append_jsonl(
                                dropped_rows_path,
                                {
                                    "split": split,
                                    "line_number": int(line_number),
                                    "conversation_id": conversation_id,
                                    "question_id": question_id,
                                    "question": clean_text(row.get("question", ""))[:240],
                                    "reason_codes": list(post_teacher_fatal),
                                    "teacher_flags": list(teacher_flags),
                                    "teacher_review": teacher_decision_payload,
                                },
                            )
                            _append_jsonl(
                                teacher_review_path,
                                {
                                    "split": split,
                                    "line_number": int(line_number),
                                    "conversation_id": conversation_id,
                                    "question_id": question_id,
                                    "teacher_flags": list(teacher_flags),
                                    "teacher_review": teacher_decision_payload,
                                },
                            )
                            continue
                        row = dict(repaired_row)
                        repair_codes = dedupe_texts(list(repair_codes) + ["teacher_edit_applied"] + list(post_teacher_repairs))
                    _append_jsonl(
                        teacher_review_path,
                        {
                            "split": split,
                            "line_number": int(line_number),
                            "conversation_id": conversation_id,
                            "question_id": question_id,
                            "teacher_flags": list(teacher_flags),
                                "teacher_review": teacher_decision_payload,
                            },
                        )

                final_row_signature = _row_signature(
                    conversation_id=conversation_id,
                    question=clean_text(row.get("question", "")),
                    answer_type=clean_text(dict(row.get("answer_targets", {}) or {}).get("answer_type", "")),
                    positive_event_ids=list(row.get("positive_event_ids", []) or []),
                )
                if final_row_signature != row_signature and final_row_signature in seen_row_signatures:
                    drop_reason_counts["duplicate_question_signature"] += 1
                    dropped_query_counts[split] += 1
                    _append_jsonl(
                        dropped_rows_path,
                        {
                            "split": split,
                            "line_number": int(line_number),
                            "conversation_id": conversation_id,
                            "question_id": question_id,
                            "question": clean_text(row.get("question", ""))[:240],
                            "reason_codes": ["duplicate_question_signature"],
                            "teacher_flags": list(teacher_flags),
                        },
                    )
                    continue
                row_signature = final_row_signature

                graph_mode = graph_cache.materialize(summary)
                if conversation_id not in kept_conversation_ids:
                    kept_graph_count += 1
                graph_materialization_modes[graph_mode] += 1
                kept_conversation_ids.add(conversation_id)
                seen_question_ids.add(question_id)
                seen_row_signatures.add(row_signature)
                if repair_codes:
                    for code in repair_codes:
                        repair_reason_counts[code] += 1
                    repaired_query_counts[split] += 1
                    _append_jsonl(
                        repaired_rows_path,
                        {
                            "split": split,
                            "line_number": int(line_number),
                            "conversation_id": conversation_id,
                            "question_id": question_id,
                            "repair_codes": list(repair_codes),
                            "teacher_flags": list(teacher_flags),
                        },
                    )
                try:
                    output_handle.write(_jsonl_bytes(row))
                except Exception as exc:
                    drop_reason_counts["output_serialization_error"] += 1
                    dropped_query_counts[split] += 1
                    _append_jsonl(
                        dropped_rows_path,
                        {
                            "split": split,
                            "line_number": int(line_number),
                            "conversation_id": conversation_id,
                            "question_id": question_id,
                            "reason_codes": ["output_serialization_error"],
                            "error_type": type(exc).__name__,
                            "error_message": clean_text(str(exc)),
                            "question": clean_text(row.get("question", ""))[:240],
                        },
                    )
                    continue
                kept_query_counts[split] += 1

    turn_extraction_dir = source_dir / "turn_extraction"
    if turn_extraction_dir.exists():
        output_turn_extraction_dir = output_dir / "turn_extraction"
        for split in ("train", "val", "test"):
            auxiliary_counts[f"turn_extraction_{split}"] = _filter_auxiliary_jsonl(
                source_path=turn_extraction_dir / f"{split}.jsonl",
                destination_path=output_turn_extraction_dir / f"{split}.jsonl",
                kept_conversation_ids=kept_conversation_ids,
            )

    source_graph_index_path = source_dir / "graph_index.jsonl"
    if source_graph_index_path.exists():
        kept_graph_index_rows = 0
        output_graph_index_path = output_dir / "graph_index.jsonl"
        with output_graph_index_path.open("w", encoding="utf-8", newline="") as handle:
            for _, line in _iter_jsonl(source_graph_index_path):
                payload = dict(json.loads(line))
                conversation_id = clean_text(payload.get("conversation_id", ""))
                if conversation_id not in kept_conversation_ids:
                    continue
                payload["graph_path"] = str(output_dir / "graphs" / f"{conversation_id}.json")
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
                kept_graph_index_rows += 1
        auxiliary_counts["graph_index_rows"] = kept_graph_index_rows

    query_index = {
        split: {
            "row_count": int(kept_query_counts.get(split, 0)),
        }
        for split in ("train", "val", "test")
    }
    write_json(output_dir / "query_index.json", query_index)

    manifest = {
        "status": "completed",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.time() - started_at, 3),
        "input_dir": str(source_dir),
        "output_dir": str(output_dir),
        "teacher_review_mode": config.teacher_review_mode,
        "teacher_base_url": clean_text(config.teacher_base_url),
        "teacher_model": clean_text(config.teacher_model),
        "teacher_auth_mode": clean_text(getattr(reviewer, "auth_mode", "")) if reviewer is not None else "none",
        "teacher_api_key_source": clean_text(getattr(reviewer, "api_key_source", "")) if reviewer is not None else "",
        "settings": {
            "min_event_anchor_coverage": float(config.min_event_anchor_coverage),
            "graph_cache_size": int(config.graph_cache_size),
        },
        "counts": {
            "source_graph_files": len(list(graphs_dir.glob("*.json"))),
            "kept_graph_files": int(kept_graph_count),
            "source_query_counts": source_query_counts,
            "kept_query_counts": kept_query_counts,
            "dropped_query_counts": dropped_query_counts,
            "repaired_query_counts": repaired_query_counts,
            "dropped_graph_references": int(dropped_graph_references),
            "auxiliary_counts": auxiliary_counts,
        },
        "drop_reason_counts": dict(sorted(drop_reason_counts.items())),
        "repair_reason_counts": dict(sorted(repair_reason_counts.items())),
        "graph_repair_reason_counts": dict(sorted(graph_repair_reason_counts.items())),
        "teacher_decision_counts": dict(sorted(teacher_decision_counts.items())),
        "teacher_reject_reason_counts": dict(sorted(teacher_reject_reason_counts.items())),
        "graph_materialization_modes": dict(sorted(graph_materialization_modes.items())),
        "source_export_manifest": source_manifest,
    }
    _log(
        "filter_completed",
        output_dir=str(output_dir),
        kept_graph_files=int(kept_graph_count),
        kept_train=int(kept_query_counts.get("train", 0)),
        kept_val=int(kept_query_counts.get("val", 0)),
        kept_test=int(kept_query_counts.get("test", 0)),
        teacher_reviews=int(sum(teacher_decision_counts.values())),
    )
    write_json(output_dir / "filter_manifest.json", manifest)
    write_json(output_dir / "export_manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Filter and teacher-clean node-memory training exports.")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--teacher-review-mode", default="off", choices=["off", "flagged", "all"])
    parser.add_argument("--teacher-base-url", default=os.environ.get("TEACHER_BASE_URL", DEFAULT_TEACHER_BASE_URL))
    parser.add_argument("--teacher-model", default=os.environ.get("TEACHER_MODEL", DEFAULT_TEACHER_MODEL))
    parser.add_argument("--teacher-api-key", default="")
    parser.add_argument("--teacher-api-key-env", default="")
    parser.add_argument("--teacher-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--teacher-enable-thinking", action="store_true")
    parser.add_argument("--teacher-max-tokens", type=int, default=256)
    parser.add_argument("--min-event-anchor-coverage", type=float, default=0.34)
    parser.add_argument("--graph-cache-size", type=int, default=DEFAULT_GRAPH_CACHE_SIZE)
    args = parser.parse_args(list(argv) if argv is not None else None)

    manifest = filter_node_training_dataset(
        input_dir=Path(args.input_dir),
        output_dir=Path(args.output_dir),
        teacher_review_mode=str(args.teacher_review_mode),
        teacher_base_url=str(args.teacher_base_url),
        teacher_model=str(args.teacher_model),
        teacher_api_key=str(args.teacher_api_key),
        teacher_api_key_env=str(args.teacher_api_key_env),
        teacher_timeout_seconds=float(args.teacher_timeout_seconds),
        teacher_enable_thinking=bool(args.teacher_enable_thinking),
        teacher_max_tokens=int(args.teacher_max_tokens),
        min_event_anchor_coverage=float(args.min_event_anchor_coverage),
        graph_cache_size=int(args.graph_cache_size),
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest.get("status") == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
