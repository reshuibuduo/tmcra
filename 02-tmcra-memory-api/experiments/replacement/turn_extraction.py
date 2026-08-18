from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import torch
import torch.nn.functional as F
from torch import Tensor, nn

from scripts.controlled_teacher_extraction import (
    _split_sentences,
    _trim_event_phrase,
    clean_text,
    extract_time_expression,
    normalize_controlled_annotation,
)

TURN_KIND_VALUES = ("other", "greeting", "profile", "event")
STATUS_VALUES = ("", "past", "current", "planned")
TIME_GRANULARITY_VALUES = ("none", "relative_day_reference", "day", "month", "year")
PROFILE_TYPE_VALUES = ("", "identity", "research_topic", "education", "occupation")

TEXT_HASH_BUCKETS = 4096
TEXT_EMBED_DIM = 96
HIDDEN_DIM = 192
MAX_SENTENCES = 6
MAX_SPAN_TOKENS = 48
MAX_EVENT_PHRASE_SPAN_TOKENS = 18
SPAN_WIDTH_EMBED_DIM = 16

DEFAULT_TURN_EXTRACTION_TRAINING_CONFIG: Dict[str, Any] = {
    "epochs": 8,
    "batch_size": 32,
    "lr": 2e-4,
    "weight_decay": 0.01,
    "dropout": 0.1,
    "sentence_loss_weight": 0.8,
    "span_loss_weight": 1.2,
    "span_joint_loss_weight": 1.0,
    "token_coverage_loss_weight": 0.35,
    "event_phrase_presence_loss_weight": 0.5,
    "patience": 2,
    "log_interval_batches": 100,
}

_TURN_KIND_TO_INDEX = {label: index for index, label in enumerate(TURN_KIND_VALUES)}
_STATUS_TO_INDEX = {label: index for index, label in enumerate(STATUS_VALUES)}
_TIME_TO_INDEX = {label: index for index, label in enumerate(TIME_GRANULARITY_VALUES)}
_PROFILE_TO_INDEX = {label: index for index, label in enumerate(PROFILE_TYPE_VALUES)}
_TURN_TOKEN_RE = re.compile(r"[A-Za-z0-9\+]+")
_RUNTIME_TOKEN_COVERAGE_THRESHOLD = 0.45


def _stable_hash_bucket(text: str, buckets: int) -> int:
    digest = hashlib.md5(clean_text(text).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % max(1, int(buckets))


def _text_hash_indices(text: str, buckets: int, *, max_tokens: int = 64) -> List[int]:
    lowered = clean_text(text).lower()
    tokens = re.findall(r"[a-z0-9]+", lowered)
    if not tokens:
        return [_stable_hash_bucket("__empty__", buckets)]
    return [_stable_hash_bucket(token, buckets) for token in tokens[:max(1, int(max_tokens))]]


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _emit_training_log(event: str, payload: Mapping[str, Any]) -> None:
    record = {"event": event, **dict(payload)}
    print(json.dumps(record, ensure_ascii=False), flush=True)


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        rows.append(dict(json.loads(line)))
    return rows


def _candidate_sentences(text: str) -> List[str]:
    sentences = [clean_text(sentence) for sentence in _split_sentences(text) if clean_text(sentence)]
    if not sentences:
        fallback = clean_text(text)
        return [fallback] if fallback else []
    return sentences[:MAX_SENTENCES]


def _token_spans(text: str, *, max_tokens: int = MAX_SPAN_TOKENS) -> List[tuple[str, int, int]]:
    spans: List[tuple[str, int, int]] = []
    for match in re.finditer(r"[A-Za-z0-9]+", clean_text(text)):
        token = clean_text(match.group(0))
        if not token:
            continue
        spans.append((token, match.start(), match.end()))
        if len(spans) >= max_tokens:
            break
    return spans


def _normalized_span_tokens(text: str) -> List[str]:
    return [token.lower() for token, _, _ in _token_spans(text, max_tokens=MAX_SPAN_TOKENS) if token]


def _char_span_to_token_span(token_spans: Sequence[tuple[str, int, int]], start_char: int, end_char: int) -> tuple[int, int] | None:
    indices = [
        index
        for index, (_, start, end) in enumerate(token_spans)
        if start < end_char and end > start_char
    ]
    if not indices:
        return None
    return (indices[0], indices[-1])


def _best_fuzzy_token_span(sentence_tokens: Sequence[str], phrase_tokens: Sequence[str]) -> tuple[int, int, float] | None:
    if not sentence_tokens or not phrase_tokens:
        return None
    best: tuple[int, int, float] | None = None
    max_width = min(len(sentence_tokens), max(len(phrase_tokens) + 4, MAX_EVENT_PHRASE_SPAN_TOKENS))
    phrase_set = set(phrase_tokens)
    for start in range(len(sentence_tokens)):
        for end in range(start, min(len(sentence_tokens), start + max_width)):
            window = list(sentence_tokens[start : end + 1])
            window_set = set(window)
            overlap = len(window_set & phrase_set)
            if overlap <= 0:
                continue
            precision = overlap / max(1, len(window_set))
            recall = overlap / max(1, len(phrase_set))
            f1 = (2.0 * precision * recall) / max(1e-6, precision + recall)
            length_penalty = abs(len(window) - len(phrase_tokens)) * 0.015
            score = f1 - length_penalty
            if best is None or score > best[2]:
                best = (start, end, score)
    if best is None or best[2] < 0.55:
        return None
    return best


def _align_event_phrase_span(sentence: str, annotation: Mapping[str, Any]) -> tuple[int, int, float]:
    normalized = normalize_controlled_annotation(annotation)
    event_phrase = clean_text(normalized.get("event_phrase", ""))
    if not event_phrase or event_phrase == "greeting":
        return (-1, -1, 0.0)
    clean_sentence = clean_text(sentence)
    token_spans = _token_spans(clean_sentence)
    if not token_spans:
        return (-1, -1, 0.0)
    match = re.search(re.escape(event_phrase), clean_sentence, flags=re.IGNORECASE)
    if match:
        token_span = _char_span_to_token_span(token_spans, match.start(), match.end())
        if token_span is not None:
            return (token_span[0], token_span[1], 1.0)
    sentence_tokens = [token.lower() for token, _, _ in token_spans]
    phrase_tokens = _normalized_span_tokens(event_phrase)
    fuzzy = _best_fuzzy_token_span(sentence_tokens, phrase_tokens)
    if fuzzy is None:
        return (-1, -1, 0.0)
    return fuzzy


def _text_from_token_span(sentence: str, start_index: int, end_index: int) -> str:
    token_spans = _token_spans(sentence)
    if start_index < 0 or end_index < start_index or end_index >= len(token_spans):
        return ""
    start_char = token_spans[start_index][1]
    end_char = token_spans[end_index][2]
    return clean_text(clean_text(sentence)[start_char:end_char]).strip(" \"'.,;:!?")


def _token_coverage_targets_for_example(example: "TurnExtractionExample") -> List[float]:
    targets = [0.0 for _ in range(MAX_SPAN_TOKENS)]
    if not _has_valid_span_supervision(example):
        return targets
    for index in range(int(example.span_start_index), int(example.span_end_index) + 1):
        if 0 <= index < MAX_SPAN_TOKENS:
            targets[index] = 1.0
    return targets


def _has_valid_span_supervision(example: "TurnExtractionExample") -> bool:
    sentence_index = int(example.sentence_target_index)
    start_index = int(example.span_start_index)
    end_index = int(example.span_end_index)
    if sentence_index < 0 or sentence_index >= len(example.sentences):
        return False
    if start_index < 0 or end_index < start_index or end_index >= MAX_SPAN_TOKENS:
        return False
    token_count = len(_token_spans(example.sentences[sentence_index]))
    return end_index < token_count


def _span_width_offset(start_index: int, end_index: int) -> int:
    return int(end_index) - int(start_index)


def _has_valid_joint_span_supervision(example: "TurnExtractionExample") -> bool:
    if not _has_valid_span_supervision(example):
        return False
    width_offset = _span_width_offset(int(example.span_start_index), int(example.span_end_index))
    return 0 <= width_offset < MAX_EVENT_PHRASE_SPAN_TOKENS


def _has_event_phrase_supervision(example: "TurnExtractionExample") -> bool:
    normalized = normalize_controlled_annotation(example.annotation)
    event_phrase = clean_text(normalized.get("event_phrase", ""))
    if not event_phrase or event_phrase.lower() == "greeting":
        return False
    return clean_text(example.turn_kind) not in {"other", "greeting"}


def _best_runtime_span(
    start_probs: Tensor,
    end_probs: Tensor,
    *,
    token_count: int,
    max_width: int = MAX_EVENT_PHRASE_SPAN_TOKENS,
) -> tuple[int, int, float]:
    if token_count <= 0:
        return (-1, -1, 0.0)
    best_start = 0
    best_end = 0
    best_score = -1.0
    limit = min(int(token_count), int(start_probs.numel()), int(end_probs.numel()))
    for start_index in range(limit):
        max_end = min(limit - 1, start_index + max(1, int(max_width)) - 1)
        for end_index in range(start_index, max_end + 1):
            score = float(start_probs[start_index].item()) * float(end_probs[end_index].item())
            if score > best_score:
                best_start = start_index
                best_end = end_index
                best_score = score
    return (best_start, best_end, max(0.0, min(1.0, best_score ** 0.5)))


def _best_runtime_joint_span(
    span_pair_logits: Tensor,
    *,
    token_count: int,
) -> tuple[int, int, float]:
    candidates = _runtime_joint_span_candidates(span_pair_logits, token_count=token_count, max_candidates=1)
    if not candidates:
        return (-1, -1, 0.0)
    return candidates[0]


def _runtime_joint_span_candidates(
    span_pair_logits: Tensor,
    *,
    token_count: int,
    max_candidates: int,
) -> List[tuple[int, int, float]]:
    if token_count <= 0:
        return []
    row_count = min(int(token_count), int(span_pair_logits.shape[0]))
    width_count = min(MAX_EVENT_PHRASE_SPAN_TOKENS, int(span_pair_logits.shape[1]))
    if row_count <= 0 or width_count <= 0:
        return []
    candidate_logits = span_pair_logits[:row_count, :width_count].clone()
    for start_index in range(row_count):
        max_width = min(width_count, row_count - start_index)
        if max_width < width_count:
            candidate_logits[start_index, max_width:] = -1e9
    flat_probs = F.softmax(candidate_logits.reshape(-1), dim=-1)
    selected: List[tuple[int, int, float]] = []
    for flat_index in torch.argsort(flat_probs, descending=True).tolist():
        start_index = int(flat_index) // width_count
        width_offset = int(flat_index) % width_count
        end_index = start_index + width_offset
        if end_index >= row_count:
            continue
        overlaps_selected = any(start_index <= selected_end and end_index >= selected_start for selected_start, selected_end, _ in selected)
        if overlaps_selected:
            continue
        confidence = float(flat_probs[int(flat_index)].item())
        selected.append((start_index, end_index, max(0.0, min(1.0, confidence))))
        if len(selected) >= max(1, int(max_candidates)):
            break
    return selected


def _best_runtime_token_coverage_span(
    coverage_probs: Tensor,
    *,
    token_count: int,
    threshold: float = _RUNTIME_TOKEN_COVERAGE_THRESHOLD,
) -> tuple[int, int, float]:
    if token_count <= 0:
        return (-1, -1, 0.0)
    limit = min(int(token_count), int(coverage_probs.numel()))
    if limit <= 0:
        return (-1, -1, 0.0)
    probs = coverage_probs[:limit]
    best: tuple[int, int, float] | None = None
    start_index = -1
    running_scores: List[float] = []
    for index, probability in enumerate(probs.tolist()):
        value = float(probability)
        if value >= float(threshold):
            if start_index < 0:
                start_index = index
                running_scores = []
            running_scores.append(value)
            continue
        if start_index >= 0:
            score = float(sum(running_scores)) / float(max(1, len(running_scores)))
            candidate = (start_index, index - 1, score)
            if best is None or (candidate[2], candidate[1] - candidate[0]) > (best[2], best[1] - best[0]):
                best = candidate
            start_index = -1
            running_scores = []
    if start_index >= 0:
        score = float(sum(running_scores)) / float(max(1, len(running_scores)))
        candidate = (start_index, limit - 1, score)
        if best is None or (candidate[2], candidate[1] - candidate[0]) > (best[2], best[1] - best[0]):
            best = candidate
    if best is not None:
        return best
    best_index = int(probs.argmax().item())
    confidence = float(probs[best_index].item())
    return (best_index, best_index, max(0.0, min(1.0, confidence)))


def _runtime_span_candidates_from_logits(
    *,
    sentence: str,
    sentence_index: int,
    outputs: Mapping[str, Tensor],
    span_enabled: bool,
    span_joint_enabled: bool,
    token_coverage_enabled: bool,
    max_candidates: int = 3,
) -> List[Dict[str, Any]]:
    token_count = len(_token_spans(sentence))
    if token_count <= 0:
        return []
    candidates: List[Dict[str, Any]] = []

    def add_candidate(start_index: int, end_index: int, confidence: float, source: str) -> None:
        text = _text_from_token_span(sentence, int(start_index), int(end_index))
        if not text:
            return
        key = (int(start_index), int(end_index), source)
        for existing in candidates:
            if (
                int(existing.get("span_start_index", -1)) == key[0]
                and int(existing.get("span_end_index", -1)) == key[1]
                and clean_text(existing.get("source", "")) == source
            ):
                if float(confidence) > float(existing.get("confidence", 0.0) or 0.0):
                    existing["confidence"] = max(0.0, min(1.0, float(confidence)))
                return
        candidates.append(
            {
                "sentence_index": int(sentence_index),
                "span_start_index": int(start_index),
                "span_end_index": int(end_index),
                "text": text,
                "confidence": max(0.0, min(1.0, float(confidence))),
                "source": source,
            }
        )

    if (
        span_joint_enabled
        and "span_pair_logits" in outputs
        and 0 <= sentence_index < int(outputs["span_pair_logits"].shape[1])
    ):
        joint_candidates = _runtime_joint_span_candidates(
            outputs["span_pair_logits"][0, sentence_index],
            token_count=token_count,
            max_candidates=max_candidates,
        )
        for start_index, end_index, confidence in joint_candidates:
            add_candidate(start_index, end_index, confidence, "learned_joint_span")
    if (
        span_enabled
        and "span_start_logits" in outputs
        and "span_end_logits" in outputs
        and 0 <= sentence_index < int(outputs["span_start_logits"].shape[1])
    ):
        start_probs = F.softmax(outputs["span_start_logits"][0, sentence_index, :token_count], dim=-1)
        end_probs = F.softmax(outputs["span_end_logits"][0, sentence_index, :token_count], dim=-1)
        start_index, end_index, confidence = _best_runtime_span(start_probs, end_probs, token_count=token_count)
        add_candidate(start_index, end_index, confidence, "learned_span")
    if (
        token_coverage_enabled
        and "token_coverage_logits" in outputs
        and 0 <= sentence_index < int(outputs["token_coverage_logits"].shape[1])
    ):
        coverage_probs = torch.sigmoid(outputs["token_coverage_logits"][0, sentence_index, :token_count])
        start_index, end_index, confidence = _best_runtime_token_coverage_span(
            coverage_probs,
            token_count=token_count,
        )
        add_candidate(start_index, end_index, confidence, "learned_token_coverage_span")

    candidates.sort(
        key=lambda item: (
            float(item.get("confidence", 0.0) or 0.0),
            int(item.get("span_end_index", -1)) - int(item.get("span_start_index", -1)),
        ),
        reverse=True,
    )
    return candidates[: max(1, int(max_candidates))]


def _sentence_overlap_score(sentence: str, phrase: str) -> float:
    sentence_tokens = set(re.findall(r"[a-z0-9]+", clean_text(sentence).lower()))
    phrase_tokens = [token for token in re.findall(r"[a-z0-9]+", clean_text(phrase).lower()) if token not in {"a", "an", "and", "the", "to"}]
    if not phrase_tokens:
        return 0.0
    overlap = sum(1 for token in phrase_tokens if token in sentence_tokens)
    return overlap / max(1, len(phrase_tokens))


def _target_sentence_index(sentences: Sequence[str], annotation: Mapping[str, Any]) -> int:
    if not sentences:
        return -1
    normalized = normalize_controlled_annotation(annotation)
    event_phrase = clean_text(normalized.get("event_phrase", ""))
    if not event_phrase or event_phrase == "greeting":
        return -1
    time_span = clean_text(normalized.get("time_expression_span", ""))
    best_index = -1
    best_score = 0.0
    lowered_phrase = event_phrase.lower()
    lowered_time = time_span.lower()
    for index, sentence in enumerate(sentences):
        lowered_sentence = clean_text(sentence).lower()
        score = _sentence_overlap_score(sentence, event_phrase)
        if lowered_phrase and lowered_phrase in lowered_sentence:
            score += 2.0
        if lowered_time and lowered_time in lowered_sentence:
            score += 1.5
        if score > best_score:
            best_index = index
            best_score = score
    if best_index >= 0:
        return best_index
    return 0


def _event_phrase_from_sentence(sentence: str, *, profile_type: str, time_expression_span: str) -> str:
    text = clean_text(sentence)
    if time_expression_span:
        text = re.sub(re.escape(time_expression_span), "", text, flags=re.IGNORECASE).strip(" ,.-")
    if profile_type == "education":
        match = re.search(r"\b(study|studying|studied)\s+(.+?)(?:\s+at\b|\s+in\b|[.,;!?]|$)", text, flags=re.IGNORECASE)
        if match:
            return clean_text(f"{match.group(1)} {match.group(2)}")
    if profile_type == "occupation":
        match = re.search(r"\bwork(?:ing)?\s+as\s+(.+?)(?:[.,;!?]|$)", text, flags=re.IGNORECASE)
        if match:
            return clean_text(match.group(0))
    return _trim_event_phrase(text)


def _recover_time_expression_span(sentence: str, *, predicted_time_granularity: str) -> str:
    clean_granularity = clean_text(predicted_time_granularity) or "none"
    if clean_granularity == "none":
        return ""
    recovered = dict(extract_time_expression(sentence) or {})
    recovered_span = clean_text(recovered.get("time_expression_span", ""))
    recovered_granularity = clean_text(recovered.get("time_granularity", "")) or "none"
    if not recovered_span:
        return ""
    if recovered_granularity == clean_granularity:
        return recovered_span
    compatible_granularity_groups = (
        {"relative_day_reference", "day"},
        {"month", "year"},
    )
    for group in compatible_granularity_groups:
        if clean_granularity in group and recovered_granularity in group:
            return recovered_span
    return ""


def _runtime_write_gate_confidence(
    *,
    turn_kind: str,
    turn_kind_confidence: float,
    sentence_confidence: float,
    status_label: str,
    status_confidence: float,
    predicted_time_granularity: str,
    time_confidence: float,
    predicted_profile: str,
    profile_confidence: float,
    selected_sentence: str,
) -> float:
    confidence = float(turn_kind_confidence)
    if turn_kind in {"event", "profile"} and clean_text(selected_sentence):
        support_confidences: List[float] = []
        if clean_text(predicted_time_granularity) not in {"", "none"}:
            support_confidences.append(float(time_confidence))
        if clean_text(predicted_profile):
            support_confidences.append(float(profile_confidence))
        if clean_text(status_label) in {"past", "planned"}:
            support_confidences.append(float(status_confidence))
        if support_confidences:
            confidence = min(
                1.0,
                confidence
                + (0.18 * float(sentence_confidence))
                + (0.22 * max(support_confidences)),
            )
    return max(0.0, min(1.0, confidence))


def _surface_tokens(text: str) -> set[str]:
    return {
        clean_text(token).lower()
        for token in _TURN_TOKEN_RE.findall(clean_text(text))
        if clean_text(token)
    }


def _annotation_turn_kind(current_turn: str, annotation: Mapping[str, Any]) -> str:
    normalized = normalize_controlled_annotation(annotation)
    semantic_slot = clean_text(normalized.get("semantic_slot", ""))
    profile_type = clean_text(normalized.get("profile_type", ""))
    event_phrase = clean_text(normalized.get("event_phrase", ""))
    time_granularity = clean_text(normalized.get("time_granularity", ""))
    time_span = clean_text(normalized.get("time_expression_span", ""))
    if event_phrase.lower() == "greeting" or semantic_slot == "greeting":
        return "greeting"
    if profile_type in PROFILE_TYPE_VALUES[1:] or semantic_slot in PROFILE_TYPE_VALUES[1:]:
        return "profile"
    if time_granularity not in {"", "none"} or time_span:
        return "event"
    if not event_phrase:
        return "other"
    turn_tokens = _surface_tokens(current_turn)
    phrase_tokens = _surface_tokens(event_phrase)
    if not turn_tokens or not phrase_tokens:
        return "event"
    surface_coverage = float(len(turn_tokens & phrase_tokens)) / float(max(1, len(turn_tokens)))
    if surface_coverage < 0.35 and len(phrase_tokens) <= 4:
        return "other"
    return "event"


@dataclass(slots=True)
class TurnExtractionExample:
    conversation_id: str
    split: str
    session_name: str
    turn_index: int
    dia_id: str
    speaker: str
    current_turn: str
    previous_turn: str
    next_turn: str
    session_timestamp: str
    annotation: Dict[str, str]
    label_source: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    sentences: List[str] = field(default_factory=list, repr=False)
    turn_kind: str = field(default="", repr=False)
    sentence_target_index: int = field(default=-1, repr=False)
    span_start_index: int = field(default=-1, repr=False)
    span_end_index: int = field(default=-1, repr=False)
    span_alignment_quality: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        self.annotation = normalize_controlled_annotation(self.annotation)
        self.sentences = _candidate_sentences(self.current_turn)
        self.turn_kind = _annotation_turn_kind(self.current_turn, self.annotation)
        self.sentence_target_index = _target_sentence_index(self.sentences, self.annotation)
        if self.turn_kind in {"other", "greeting"}:
            self.sentence_target_index = -1
        if 0 <= self.sentence_target_index < len(self.sentences):
            (
                self.span_start_index,
                self.span_end_index,
                self.span_alignment_quality,
            ) = _align_event_phrase_span(self.sentences[self.sentence_target_index], self.annotation)
        if self.turn_kind in {"other", "greeting"}:
            self.span_start_index = -1
            self.span_end_index = -1
            self.span_alignment_quality = 0.0

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TurnExtractionExample":
        return cls(
            conversation_id=clean_text(payload.get("conversation_id", "")),
            split=clean_text(payload.get("split", "")),
            session_name=clean_text(payload.get("session_name", "")),
            turn_index=int(payload.get("turn_index", 0) or 0),
            dia_id=clean_text(payload.get("dia_id", "")),
            speaker=clean_text(payload.get("speaker", "")),
            current_turn=clean_text(payload.get("current_turn", "")),
            previous_turn=clean_text(payload.get("previous_turn", "")),
            next_turn=clean_text(payload.get("next_turn", "")),
            session_timestamp=clean_text(payload.get("session_timestamp", "")),
            annotation=dict(payload.get("annotation", {}) or {}),
            label_source=clean_text(payload.get("label_source", "")),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "split": self.split,
            "session_name": self.session_name,
            "turn_index": int(self.turn_index),
            "dia_id": self.dia_id,
            "speaker": self.speaker,
            "current_turn": self.current_turn,
            "previous_turn": self.previous_turn,
            "next_turn": self.next_turn,
            "session_timestamp": self.session_timestamp,
            "annotation": dict(self.annotation),
            "label_source": self.label_source,
            "metadata": dict(self.metadata),
        }


def load_turn_extraction_examples(path: Path) -> List[TurnExtractionExample]:
    return [TurnExtractionExample.from_dict(row) for row in _load_jsonl(path)]


def _mean_pooled_hash_embedding(texts: Sequence[str], embedding: nn.Embedding, *, device: torch.device, max_tokens: int = 64) -> Tensor:
    if not texts:
        return torch.zeros((0, embedding.embedding_dim), dtype=embedding.weight.dtype, device=device)
    index_rows = [_text_hash_indices(text, TEXT_HASH_BUCKETS, max_tokens=max_tokens) for text in texts]
    max_len = max(len(indices) for indices in index_rows)
    token_ids = torch.zeros((len(index_rows), max_len), dtype=torch.long, device=device)
    mask = torch.zeros((len(index_rows), max_len), dtype=embedding.weight.dtype, device=device)
    for row_index, indices in enumerate(index_rows):
        token_ids[row_index, : len(indices)] = torch.tensor(indices, dtype=torch.long, device=device)
        mask[row_index, : len(indices)] = 1.0
    embedded = embedding(token_ids)
    masked = embedded * mask.unsqueeze(-1)
    denom = mask.sum(dim=1, keepdim=True).clamp_min(1.0)
    return masked.sum(dim=1) / denom


def _padded_hash_token_embedding(texts: Sequence[str], embedding: nn.Embedding, *, device: torch.device, max_tokens: int) -> tuple[Tensor, Tensor, List[int]]:
    if not texts:
        empty_emb = torch.zeros((0, max_tokens, embedding.embedding_dim), dtype=embedding.weight.dtype, device=device)
        empty_mask = torch.zeros((0, max_tokens), dtype=embedding.weight.dtype, device=device)
        return empty_emb, empty_mask, []
    index_rows = [_text_hash_indices(text, TEXT_HASH_BUCKETS, max_tokens=max_tokens) for text in texts]
    token_ids = torch.zeros((len(index_rows), max_tokens), dtype=torch.long, device=device)
    mask = torch.zeros((len(index_rows), max_tokens), dtype=embedding.weight.dtype, device=device)
    lengths: List[int] = []
    for row_index, indices in enumerate(index_rows):
        trimmed = indices[:max_tokens]
        lengths.append(len(trimmed))
        if not trimmed:
            continue
        token_ids[row_index, : len(trimmed)] = torch.tensor(trimmed, dtype=torch.long, device=device)
        mask[row_index, : len(trimmed)] = 1.0
    return embedding(token_ids), mask, lengths


class TurnExtractionModel(nn.Module):
    def __init__(self, *, dropout: float = 0.1) -> None:
        super().__init__()
        self.text_embedding = nn.Embedding(TEXT_HASH_BUCKETS, TEXT_EMBED_DIM)
        self.context_proj = nn.Sequential(
            nn.Linear(TEXT_EMBED_DIM * 5, HIDDEN_DIM),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.LayerNorm(HIDDEN_DIM),
        )
        self.turn_kind_head = nn.Sequential(nn.Linear(HIDDEN_DIM, HIDDEN_DIM), nn.ReLU(), nn.Linear(HIDDEN_DIM, len(TURN_KIND_VALUES)))
        self.status_head = nn.Sequential(nn.Linear(HIDDEN_DIM, HIDDEN_DIM), nn.ReLU(), nn.Linear(HIDDEN_DIM, len(STATUS_VALUES)))
        self.time_head = nn.Sequential(nn.Linear(HIDDEN_DIM, HIDDEN_DIM), nn.ReLU(), nn.Linear(HIDDEN_DIM, len(TIME_GRANULARITY_VALUES)))
        self.profile_head = nn.Sequential(nn.Linear(HIDDEN_DIM, HIDDEN_DIM), nn.ReLU(), nn.Linear(HIDDEN_DIM, len(PROFILE_TYPE_VALUES)))
        self.event_phrase_presence_head = nn.Sequential(nn.Linear(HIDDEN_DIM, HIDDEN_DIM), nn.ReLU(), nn.Linear(HIDDEN_DIM, 1))
        self.sentence_head = nn.Sequential(
            nn.Linear(HIDDEN_DIM + (TEXT_EMBED_DIM * 2), HIDDEN_DIM),
            nn.ReLU(),
            nn.Linear(HIDDEN_DIM, 1),
        )
        self.span_start_head = nn.Sequential(
            nn.Linear(HIDDEN_DIM + (TEXT_EMBED_DIM * 2), HIDDEN_DIM),
            nn.ReLU(),
            nn.Linear(HIDDEN_DIM, 1),
        )
        self.span_width_embedding = nn.Embedding(MAX_EVENT_PHRASE_SPAN_TOKENS, SPAN_WIDTH_EMBED_DIM)
        self.span_pair_head = nn.Sequential(
            nn.Linear(HIDDEN_DIM + (TEXT_EMBED_DIM * 4) + SPAN_WIDTH_EMBED_DIM, HIDDEN_DIM),
            nn.ReLU(),
            nn.Linear(HIDDEN_DIM, 1),
        )
        self.span_end_head = nn.Sequential(
            nn.Linear(HIDDEN_DIM + (TEXT_EMBED_DIM * 2), HIDDEN_DIM),
            nn.ReLU(),
            nn.Linear(HIDDEN_DIM, 1),
        )
        self.token_coverage_head = nn.Sequential(
            nn.Linear(HIDDEN_DIM + (TEXT_EMBED_DIM * 2), HIDDEN_DIM),
            nn.ReLU(),
            nn.Linear(HIDDEN_DIM, 1),
        )

    def forward(self, examples: Sequence[TurnExtractionExample], *, device: torch.device) -> Dict[str, Tensor]:
        current_emb = _mean_pooled_hash_embedding([example.current_turn for example in examples], self.text_embedding, device=device)
        previous_emb = _mean_pooled_hash_embedding([example.previous_turn for example in examples], self.text_embedding, device=device)
        next_emb = _mean_pooled_hash_embedding([example.next_turn for example in examples], self.text_embedding, device=device)
        context_input = torch.cat(
            [
                current_emb,
                previous_emb,
                next_emb,
                current_emb * previous_emb,
                current_emb * next_emb,
            ],
            dim=-1,
        )
        context = self.context_proj(context_input)
        turn_kind_logits = self.turn_kind_head(context)
        status_logits = self.status_head(context)
        time_logits = self.time_head(context)
        profile_logits = self.profile_head(context)
        event_phrase_presence_logits = self.event_phrase_presence_head(context).squeeze(-1)

        flat_sentences: List[str] = []
        spans: List[tuple[int, int]] = []
        for example in examples:
            sentences = list(example.sentences or [])[:MAX_SENTENCES]
            spans.append((len(flat_sentences), len(sentences)))
            flat_sentences.extend(sentences)
        sentence_logits = torch.full((len(examples), MAX_SENTENCES), -1e9, dtype=context.dtype, device=device)
        span_start_logits = torch.full((len(examples), MAX_SENTENCES, MAX_SPAN_TOKENS), -1e9, dtype=context.dtype, device=device)
        span_end_logits = torch.full((len(examples), MAX_SENTENCES, MAX_SPAN_TOKENS), -1e9, dtype=context.dtype, device=device)
        token_coverage_logits = torch.full((len(examples), MAX_SENTENCES, MAX_SPAN_TOKENS), -1e9, dtype=context.dtype, device=device)
        span_pair_logits = torch.full(
            (len(examples), MAX_SENTENCES, MAX_SPAN_TOKENS, MAX_EVENT_PHRASE_SPAN_TOKENS),
            -1e9,
            dtype=context.dtype,
            device=device,
        )
        if flat_sentences:
            sentence_emb = _mean_pooled_hash_embedding(flat_sentences, self.text_embedding, device=device)
            token_emb, token_mask, token_lengths = _padded_hash_token_embedding(
                flat_sentences,
                self.text_embedding,
                device=device,
                max_tokens=MAX_SPAN_TOKENS,
            )
            flat_index = 0
            for batch_index, (start, count) in enumerate(spans):
                if count <= 0:
                    continue
                candidate_emb = sentence_emb[start : start + count]
                repeated_context = context[batch_index].unsqueeze(0).expand(count, -1)
                repeated_current = current_emb[batch_index].unsqueeze(0).expand(count, -1)
                features = torch.cat([repeated_context, candidate_emb, candidate_emb * repeated_current], dim=-1)
                sentence_logits[batch_index, :count] = self.sentence_head(features).squeeze(-1)
                for sentence_offset in range(count):
                    token_count = min(int(token_lengths[flat_index]), MAX_SPAN_TOKENS)
                    if token_count <= 0:
                        flat_index += 1
                        continue
                    candidate_token_emb = token_emb[flat_index, :token_count]
                    token_context = context[batch_index].unsqueeze(0).expand(token_count, -1)
                    token_current = current_emb[batch_index].unsqueeze(0).expand(token_count, -1)
                    token_features = torch.cat([token_context, candidate_token_emb, candidate_token_emb * token_current], dim=-1)
                    valid_mask = token_mask[flat_index, :token_count] > 0
                    start_scores = self.span_start_head(token_features).squeeze(-1)
                    end_scores = self.span_end_head(token_features).squeeze(-1)
                    coverage_scores = self.token_coverage_head(token_features).squeeze(-1)
                    span_start_logits[batch_index, sentence_offset, :token_count] = start_scores.masked_fill(~valid_mask, -1e9)
                    span_end_logits[batch_index, sentence_offset, :token_count] = end_scores.masked_fill(~valid_mask, -1e9)
                    token_coverage_logits[batch_index, sentence_offset, :token_count] = coverage_scores.masked_fill(~valid_mask, -1e9)
                    start_indices = torch.arange(token_count, dtype=torch.long, device=device)
                    width_indices = torch.arange(MAX_EVENT_PHRASE_SPAN_TOKENS, dtype=torch.long, device=device)
                    end_indices = start_indices.unsqueeze(1) + width_indices.unsqueeze(0)
                    valid_pairs = end_indices < token_count
                    clamped_end_indices = end_indices.clamp(max=max(0, token_count - 1))
                    start_emb = candidate_token_emb[start_indices].unsqueeze(1).expand(-1, MAX_EVENT_PHRASE_SPAN_TOKENS, -1)
                    end_emb = candidate_token_emb[clamped_end_indices]
                    pair_context = context[batch_index].view(1, 1, -1).expand(token_count, MAX_EVENT_PHRASE_SPAN_TOKENS, -1)
                    pair_current = current_emb[batch_index].view(1, 1, -1)
                    width_emb = self.span_width_embedding(width_indices).unsqueeze(0).expand(token_count, -1, -1)
                    pair_features = torch.cat(
                        [
                            pair_context,
                            start_emb,
                            end_emb,
                            start_emb * pair_current,
                            end_emb * pair_current,
                            width_emb,
                        ],
                        dim=-1,
                    )
                    pair_scores = self.span_pair_head(pair_features).squeeze(-1)
                    span_pair_logits[batch_index, sentence_offset, :token_count, :] = pair_scores.masked_fill(~valid_pairs, -1e9)
                    flat_index += 1
        return {
            "turn_kind_logits": turn_kind_logits,
            "status_logits": status_logits,
            "time_logits": time_logits,
            "profile_logits": profile_logits,
            "event_phrase_presence_logits": event_phrase_presence_logits,
            "sentence_logits": sentence_logits,
            "span_start_logits": span_start_logits,
            "span_end_logits": span_end_logits,
            "token_coverage_logits": token_coverage_logits,
            "span_pair_logits": span_pair_logits,
        }


def _batch_iter(examples: Sequence[TurnExtractionExample], batch_size: int) -> Sequence[List[TurnExtractionExample]]:
    batch: List[TurnExtractionExample] = []
    for example in examples:
        batch.append(example)
        if len(batch) >= batch_size:
            yield list(batch)
            batch = []
    if batch:
        yield list(batch)


def _compute_loss(
    model: TurnExtractionModel,
    examples: Sequence[TurnExtractionExample],
    *,
    device: torch.device,
    sentence_loss_weight: float,
    span_loss_weight: float,
    span_joint_loss_weight: float = 1.0,
    token_coverage_loss_weight: float = 0.35,
    event_phrase_presence_loss_weight: float = 0.5,
) -> tuple[Tensor, Dict[str, float]]:
    outputs = model(examples, device=device)
    turn_kind_targets = torch.tensor([_TURN_KIND_TO_INDEX[example.turn_kind] for example in examples], dtype=torch.long, device=device)
    status_targets = torch.tensor([_STATUS_TO_INDEX[clean_text(example.annotation.get("target_status", ""))] for example in examples], dtype=torch.long, device=device)
    time_targets = torch.tensor([_TIME_TO_INDEX[clean_text(example.annotation.get("time_granularity", "")) or "none"] for example in examples], dtype=torch.long, device=device)
    profile_targets = torch.tensor([_PROFILE_TO_INDEX[clean_text(example.annotation.get("profile_type", ""))] for example in examples], dtype=torch.long, device=device)
    event_phrase_presence_targets = torch.tensor(
        [1.0 if _has_event_phrase_supervision(example) else 0.0 for example in examples],
        dtype=outputs["turn_kind_logits"].dtype,
        device=device,
    )

    turn_kind_loss = F.cross_entropy(outputs["turn_kind_logits"], turn_kind_targets)
    status_loss = F.cross_entropy(outputs["status_logits"], status_targets)
    time_loss = F.cross_entropy(outputs["time_logits"], time_targets)
    profile_loss = F.cross_entropy(outputs["profile_logits"], profile_targets)
    event_phrase_presence_loss = F.binary_cross_entropy_with_logits(
        outputs["event_phrase_presence_logits"],
        event_phrase_presence_targets,
    )

    sentence_loss = torch.zeros((), dtype=outputs["turn_kind_logits"].dtype, device=device)
    valid_sentence_indices = [index for index, example in enumerate(examples) if 0 <= int(example.sentence_target_index) < len(example.sentences)]
    if valid_sentence_indices:
        sentence_logits = outputs["sentence_logits"][valid_sentence_indices]
        sentence_targets = torch.tensor(
            [int(examples[index].sentence_target_index) for index in valid_sentence_indices],
            dtype=torch.long,
            device=device,
        )
        sentence_loss = F.cross_entropy(sentence_logits, sentence_targets)

    span_loss = torch.zeros((), dtype=outputs["turn_kind_logits"].dtype, device=device)
    valid_span_indices = [
        index
        for index, example in enumerate(examples)
        if _has_valid_span_supervision(example)
    ]
    if valid_span_indices:
        sentence_targets_for_span = torch.tensor(
            [int(examples[index].sentence_target_index) for index in valid_span_indices],
            dtype=torch.long,
            device=device,
        )
        batch_indices = torch.tensor(valid_span_indices, dtype=torch.long, device=device)
        span_start_logits = outputs["span_start_logits"][batch_indices, sentence_targets_for_span]
        span_end_logits = outputs["span_end_logits"][batch_indices, sentence_targets_for_span]
        span_start_targets = torch.tensor(
            [int(examples[index].span_start_index) for index in valid_span_indices],
            dtype=torch.long,
            device=device,
        )
        span_end_targets = torch.tensor(
            [int(examples[index].span_end_index) for index in valid_span_indices],
            dtype=torch.long,
            device=device,
        )
        span_loss = 0.5 * (
            F.cross_entropy(span_start_logits, span_start_targets)
            + F.cross_entropy(span_end_logits, span_end_targets)
        )

    token_coverage_loss = torch.zeros((), dtype=outputs["turn_kind_logits"].dtype, device=device)
    if valid_span_indices and "token_coverage_logits" in outputs:
        coverage_logits = outputs["token_coverage_logits"][batch_indices, sentence_targets_for_span]
        coverage_targets = torch.tensor(
            [_token_coverage_targets_for_example(examples[index]) for index in valid_span_indices],
            dtype=coverage_logits.dtype,
            device=device,
        )
        token_positions = torch.arange(MAX_SPAN_TOKENS, dtype=torch.long, device=device).unsqueeze(0)
        token_counts = torch.tensor(
            [len(_token_spans(examples[index].sentences[int(examples[index].sentence_target_index)])) for index in valid_span_indices],
            dtype=torch.long,
            device=device,
        ).unsqueeze(1)
        valid_token_mask = token_positions < token_counts
        masked_logits = coverage_logits[valid_token_mask]
        masked_targets = coverage_targets[valid_token_mask]
        positive_count = float(masked_targets.sum().detach().cpu().item())
        negative_count = float(masked_targets.numel() - positive_count)
        pos_weight = None
        if positive_count > 0.0 and negative_count > 0.0:
            pos_weight = torch.tensor(
                min(8.0, negative_count / max(1.0, positive_count)),
                dtype=coverage_logits.dtype,
                device=device,
            )
        token_coverage_loss = F.binary_cross_entropy_with_logits(
            masked_logits,
            masked_targets,
            pos_weight=pos_weight,
        )

    span_joint_loss = torch.zeros((), dtype=outputs["turn_kind_logits"].dtype, device=device)
    valid_joint_span_indices = [
        index
        for index, example in enumerate(examples)
        if _has_valid_joint_span_supervision(example)
    ]
    if valid_joint_span_indices:
        sentence_targets_for_joint_span = torch.tensor(
            [int(examples[index].sentence_target_index) for index in valid_joint_span_indices],
            dtype=torch.long,
            device=device,
        )
        joint_batch_indices = torch.tensor(valid_joint_span_indices, dtype=torch.long, device=device)
        joint_span_logits = outputs["span_pair_logits"][joint_batch_indices, sentence_targets_for_joint_span]
        joint_span_targets = torch.tensor(
            [
                (int(examples[index].span_start_index) * MAX_EVENT_PHRASE_SPAN_TOKENS)
                + _span_width_offset(int(examples[index].span_start_index), int(examples[index].span_end_index))
                for index in valid_joint_span_indices
            ],
            dtype=torch.long,
            device=device,
        )
        span_joint_loss = F.cross_entropy(
            joint_span_logits.reshape(len(valid_joint_span_indices), -1),
            joint_span_targets,
        )

    total_loss = (
        turn_kind_loss
        + status_loss
        + time_loss
        + profile_loss
        + (event_phrase_presence_loss * float(event_phrase_presence_loss_weight))
        + (sentence_loss * float(sentence_loss_weight))
        + (span_loss * float(span_loss_weight))
        + (span_joint_loss * float(span_joint_loss_weight))
        + (token_coverage_loss * float(token_coverage_loss_weight))
    )

    with torch.no_grad():
        turn_kind_accuracy = float((outputs["turn_kind_logits"].argmax(dim=-1) == turn_kind_targets).float().mean().item())
        status_accuracy = float((outputs["status_logits"].argmax(dim=-1) == status_targets).float().mean().item())
        time_accuracy = float((outputs["time_logits"].argmax(dim=-1) == time_targets).float().mean().item())
        profile_accuracy = float((outputs["profile_logits"].argmax(dim=-1) == profile_targets).float().mean().item())
        event_phrase_presence_predictions = (torch.sigmoid(outputs["event_phrase_presence_logits"]) >= 0.5).to(event_phrase_presence_targets.dtype)
        event_phrase_presence_accuracy = float((event_phrase_presence_predictions == event_phrase_presence_targets).float().mean().item())
        sentence_accuracy = 0.0
        if valid_sentence_indices:
            sentence_predictions = outputs["sentence_logits"][valid_sentence_indices].argmax(dim=-1)
            sentence_accuracy = float((sentence_predictions == sentence_targets).float().mean().item())
        span_start_accuracy = 0.0
        span_end_accuracy = 0.0
        span_exact_accuracy = 0.0
        span_joint_accuracy = 0.0
        token_coverage_accuracy = 0.0
        token_coverage_f1 = 0.0
        span_supervision_rate = float(len(valid_span_indices)) / float(max(1, len(examples)))
        if valid_span_indices:
            span_start_predictions = span_start_logits.argmax(dim=-1)
            span_end_predictions = span_end_logits.argmax(dim=-1)
            span_start_accuracy = float((span_start_predictions == span_start_targets).float().mean().item())
            span_end_accuracy = float((span_end_predictions == span_end_targets).float().mean().item())
            span_exact_accuracy = float(
                ((span_start_predictions == span_start_targets) & (span_end_predictions == span_end_targets)).float().mean().item()
            )
            if "token_coverage_logits" in outputs:
                coverage_predictions = (torch.sigmoid(coverage_logits) >= 0.5).to(coverage_targets.dtype)
                masked_predictions = coverage_predictions[valid_token_mask]
                masked_targets_for_metrics = coverage_targets[valid_token_mask]
                token_coverage_accuracy = float((masked_predictions == masked_targets_for_metrics).float().mean().item())
                true_positive = float(((masked_predictions == 1.0) & (masked_targets_for_metrics == 1.0)).float().sum().item())
                predicted_positive = float((masked_predictions == 1.0).float().sum().item())
                target_positive = float((masked_targets_for_metrics == 1.0).float().sum().item())
                precision = true_positive / max(1.0, predicted_positive)
                recall = true_positive / max(1.0, target_positive)
                token_coverage_f1 = 0.0 if precision + recall <= 0.0 else (2.0 * precision * recall) / (precision + recall)
        if valid_joint_span_indices:
            joint_span_predictions = outputs["span_pair_logits"][
                torch.tensor(valid_joint_span_indices, dtype=torch.long, device=device),
                torch.tensor([int(examples[index].sentence_target_index) for index in valid_joint_span_indices], dtype=torch.long, device=device),
            ].reshape(len(valid_joint_span_indices), -1).argmax(dim=-1)
            joint_span_targets_for_metrics = torch.tensor(
                [
                    (int(examples[index].span_start_index) * MAX_EVENT_PHRASE_SPAN_TOKENS)
                    + _span_width_offset(int(examples[index].span_start_index), int(examples[index].span_end_index))
                    for index in valid_joint_span_indices
                ],
                dtype=torch.long,
                device=device,
            )
            span_joint_accuracy = float((joint_span_predictions == joint_span_targets_for_metrics).float().mean().item())
            span_exact_accuracy = span_joint_accuracy

    return total_loss, {
        "loss": float(total_loss.detach().cpu().item()),
        "turn_kind_accuracy": turn_kind_accuracy,
        "status_accuracy": status_accuracy,
        "time_accuracy": time_accuracy,
        "profile_accuracy": profile_accuracy,
        "event_phrase_presence_accuracy": event_phrase_presence_accuracy,
        "sentence_accuracy": sentence_accuracy,
        "span_start_accuracy": span_start_accuracy,
        "span_end_accuracy": span_end_accuracy,
        "span_joint_accuracy": span_joint_accuracy,
        "span_exact_accuracy": span_exact_accuracy,
        "token_coverage_accuracy": token_coverage_accuracy,
        "token_coverage_f1": token_coverage_f1,
        "span_supervision_rate": span_supervision_rate,
    }


def evaluate_turn_extraction_examples(
    model: TurnExtractionModel,
    examples: Sequence[TurnExtractionExample],
    *,
    device: torch.device,
    batch_size: int,
    sentence_loss_weight: float,
    span_loss_weight: float,
    span_joint_loss_weight: float,
    token_coverage_loss_weight: float,
    event_phrase_presence_loss_weight: float,
) -> Dict[str, float]:
    if not examples:
        return {
            "loss": 0.0,
            "turn_kind_accuracy": 0.0,
            "status_accuracy": 0.0,
            "time_accuracy": 0.0,
            "profile_accuracy": 0.0,
            "event_phrase_presence_accuracy": 0.0,
            "sentence_accuracy": 0.0,
            "span_start_accuracy": 0.0,
            "span_end_accuracy": 0.0,
            "span_joint_accuracy": 0.0,
            "span_exact_accuracy": 0.0,
            "token_coverage_accuracy": 0.0,
            "token_coverage_f1": 0.0,
            "span_supervision_rate": 0.0,
        }
    model.eval()
    metrics: Dict[str, float] = {
        "loss": 0.0,
        "turn_kind_accuracy": 0.0,
        "status_accuracy": 0.0,
        "time_accuracy": 0.0,
        "profile_accuracy": 0.0,
        "event_phrase_presence_accuracy": 0.0,
        "sentence_accuracy": 0.0,
        "span_start_accuracy": 0.0,
        "span_end_accuracy": 0.0,
        "span_joint_accuracy": 0.0,
        "span_exact_accuracy": 0.0,
        "token_coverage_accuracy": 0.0,
        "token_coverage_f1": 0.0,
        "span_supervision_rate": 0.0,
    }
    batch_count = 0
    with torch.no_grad():
        for batch in _batch_iter(examples, batch_size):
            _, batch_metrics = _compute_loss(
                model,
                batch,
                device=device,
                sentence_loss_weight=sentence_loss_weight,
                span_loss_weight=span_loss_weight,
                span_joint_loss_weight=span_joint_loss_weight,
                token_coverage_loss_weight=token_coverage_loss_weight,
                event_phrase_presence_loss_weight=event_phrase_presence_loss_weight,
            )
            batch_count += 1
            for key, value in batch_metrics.items():
                metrics[key] += float(value)
    if batch_count <= 0:
        return metrics
    return {key: value / batch_count for key, value in metrics.items()}


def train_turn_extraction(
    *,
    train_examples: Sequence[TurnExtractionExample],
    val_examples: Sequence[TurnExtractionExample],
    output_dir: Path,
    config: Mapping[str, Any] | None = None,
    seed: int = 7,
) -> Dict[str, Any]:
    if not train_examples:
        raise RuntimeError("No extraction training examples provided")
    resolved_config = dict(DEFAULT_TURN_EXTRACTION_TRAINING_CONFIG)
    resolved_config.update(dict(config or {}))
    output_dir.mkdir(parents=True, exist_ok=True)

    random.seed(seed)
    torch.manual_seed(seed)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = TurnExtractionModel(dropout=float(resolved_config.get("dropout", 0.1) or 0.1)).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(resolved_config.get("lr", 2e-4) or 2e-4),
        weight_decay=float(resolved_config.get("weight_decay", 0.01) or 0.01),
    )

    batch_size = int(resolved_config.get("batch_size", 32) or 32)
    sentence_loss_weight = float(resolved_config.get("sentence_loss_weight", 0.8) or 0.8)
    span_loss_weight = float(resolved_config.get("span_loss_weight", 1.2) or 1.2)
    span_joint_loss_weight = float(resolved_config.get("span_joint_loss_weight", 1.0) or 1.0)
    token_coverage_loss_weight = float(resolved_config.get("token_coverage_loss_weight", 0.35) or 0.35)
    event_phrase_presence_loss_weight = float(resolved_config.get("event_phrase_presence_loss_weight", 0.5) or 0.5)
    patience = int(resolved_config.get("patience", 2) or 2)
    log_interval_batches = max(1, int(resolved_config.get("log_interval_batches", 100) or 100))
    best_val_loss = float("inf")
    best_state: Dict[str, Any] | None = None
    history: List[Dict[str, Any]] = []
    epochs_without_improvement = 0
    run_started_at = time.time()
    _emit_training_log(
        "turn_extraction_train_start",
        {
            "output_dir": str(output_dir),
            "device": str(device),
            "train_example_count": len(train_examples),
            "val_example_count": len(val_examples),
            "config": resolved_config,
        },
    )

    for epoch in range(1, int(resolved_config.get("epochs", 8) or 8) + 1):
        model.train()
        epoch_rows = list(train_examples)
        random.shuffle(epoch_rows)
        train_metrics = {
            "loss": 0.0,
            "turn_kind_accuracy": 0.0,
            "status_accuracy": 0.0,
            "time_accuracy": 0.0,
            "profile_accuracy": 0.0,
            "event_phrase_presence_accuracy": 0.0,
            "sentence_accuracy": 0.0,
            "span_start_accuracy": 0.0,
            "span_end_accuracy": 0.0,
            "span_joint_accuracy": 0.0,
            "span_exact_accuracy": 0.0,
            "token_coverage_accuracy": 0.0,
            "token_coverage_f1": 0.0,
            "span_supervision_rate": 0.0,
        }
        batch_count = 0
        total_batches = max(1, (len(epoch_rows) + batch_size - 1) // batch_size)
        for batch in _batch_iter(epoch_rows, batch_size):
            optimizer.zero_grad(set_to_none=True)
            loss, batch_metrics = _compute_loss(
                model,
                batch,
                device=device,
                sentence_loss_weight=sentence_loss_weight,
                span_loss_weight=span_loss_weight,
                span_joint_loss_weight=span_joint_loss_weight,
                token_coverage_loss_weight=token_coverage_loss_weight,
                event_phrase_presence_loss_weight=event_phrase_presence_loss_weight,
            )
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            batch_count += 1
            for key, value in batch_metrics.items():
                train_metrics[key] += float(value)
            if batch_count % log_interval_batches == 0 or batch_count == total_batches:
                running_train_metrics = {key: value / batch_count for key, value in train_metrics.items()}
                batch_progress = {
                    "epoch": epoch,
                    "batch": batch_count,
                    "total_batches": total_batches,
                    "elapsed_seconds": round(time.time() - run_started_at, 3),
                    "train": running_train_metrics,
                }
                _write_json(
                    output_dir / "train_summary.partial.json",
                    {
                        "device": str(device),
                        "train_example_count": len(train_examples),
                        "val_example_count": len(val_examples),
                        "best_val_loss": float(best_val_loss if best_val_loss < float("inf") else running_train_metrics.get("loss", 0.0)),
                        "history": history,
                        "config": resolved_config,
                        "status": "running",
                        "current_progress": batch_progress,
                    },
                )
                _emit_training_log("turn_extraction_train_batch", batch_progress)
        if batch_count > 0:
            train_metrics = {key: value / batch_count for key, value in train_metrics.items()}

        val_metrics = evaluate_turn_extraction_examples(
            model,
            val_examples or train_examples,
            device=device,
            batch_size=batch_size,
            sentence_loss_weight=sentence_loss_weight,
            span_loss_weight=span_loss_weight,
            span_joint_loss_weight=span_joint_loss_weight,
            token_coverage_loss_weight=token_coverage_loss_weight,
            event_phrase_presence_loss_weight=event_phrase_presence_loss_weight,
        )
        epoch_summary = {
            "epoch": epoch,
            "elapsed_seconds": round(time.time() - run_started_at, 3),
            "train": train_metrics,
            "val": val_metrics,
        }
        history.append(epoch_summary)
        partial_summary = {
            "device": str(device),
            "train_example_count": len(train_examples),
            "val_example_count": len(val_examples),
            "best_val_loss": float(best_val_loss if best_val_loss < float("inf") else val_metrics.get("loss", 0.0)),
            "history": history,
            "config": resolved_config,
            "status": "running",
        }
        _write_json(output_dir / "train_summary.partial.json", partial_summary)
        _emit_training_log("turn_extraction_train_epoch", epoch_summary)
        if float(val_metrics.get("loss", 0.0) or 0.0) < best_val_loss:
            best_val_loss = float(val_metrics.get("loss", 0.0) or 0.0)
            best_state = {
                "state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
                "config": resolved_config,
                "best_val_loss": best_val_loss,
            }
            torch.save(best_state, output_dir / "turn_extractor_best.pt")
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= patience:
                break

    last_state = {
        "state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
        "config": resolved_config,
        "best_val_loss": best_val_loss,
    }
    torch.save(last_state, output_dir / "turn_extractor_last.pt")
    if best_state is None:
        best_state = last_state
        torch.save(best_state, output_dir / "turn_extractor_best.pt")

    summary = {
        "device": str(device),
        "train_example_count": len(train_examples),
        "val_example_count": len(val_examples),
        "best_val_loss": float(best_val_loss),
        "history": history,
        "config": resolved_config,
        "status": "completed",
        "elapsed_seconds": round(time.time() - run_started_at, 3),
    }
    _write_json(output_dir / "train_summary.json", summary)
    _write_json(output_dir / "train_summary.partial.json", summary)
    _emit_training_log(
        "turn_extraction_train_complete",
        {
            "output_dir": str(output_dir),
            "best_val_loss": float(best_val_loss),
            "elapsed_seconds": summary["elapsed_seconds"],
            "best_checkpoint": str(output_dir / "turn_extractor_best.pt"),
            "last_checkpoint": str(output_dir / "turn_extractor_last.pt"),
        },
    )
    return summary


class LoadedTurnExtractionModel:
    def __init__(self, checkpoint_path: Path, *, device: torch.device | None = None) -> None:
        self.checkpoint_path = Path(checkpoint_path)
        payload = torch.load(self.checkpoint_path, map_location="cpu")
        self.config = dict(payload.get("config", {}) or {})
        self.device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = TurnExtractionModel(dropout=float(self.config.get("dropout", 0.1) or 0.1))
        state_dict = dict(payload.get("state_dict", {}) or {})
        load_result = self.model.load_state_dict(state_dict, strict=False)
        missing_keys = set(getattr(load_result, "missing_keys", []) or [])
        self.span_enabled = not any(key.startswith("span_start_head.") or key.startswith("span_end_head.") for key in missing_keys)
        self.span_joint_enabled = not any(key.startswith("span_pair_head.") or key.startswith("span_width_embedding.") for key in missing_keys)
        self.token_coverage_enabled = not any(key.startswith("token_coverage_head.") for key in missing_keys)
        self.event_phrase_presence_enabled = not any(key.startswith("event_phrase_presence_head.") for key in missing_keys)
        self.model.to(self.device)
        self.model.eval()

    def predict(
        self,
        *,
        current_turn: str,
        previous_turn: str = "",
        next_turn: str = "",
        session_timestamp: str = "",
        confidence_threshold: float = 0.55,
        other_threshold: float = 0.65,
        event_phrase_presence_threshold: float = 0.45,
        max_event_spans: int = 3,
    ) -> Dict[str, Any]:
        example = TurnExtractionExample(
            conversation_id="runtime",
            split="runtime",
            session_name="runtime",
            turn_index=0,
            dia_id="runtime",
            speaker="speaker",
            current_turn=current_turn,
            previous_turn=previous_turn,
            next_turn=next_turn,
            session_timestamp=session_timestamp,
            annotation={},
            label_source="runtime",
        )
        with torch.no_grad():
            outputs = self.model([example], device=self.device)
        turn_kind_probs = F.softmax(outputs["turn_kind_logits"][0], dim=-1)
        status_probs = F.softmax(outputs["status_logits"][0], dim=-1)
        time_probs = F.softmax(outputs["time_logits"][0], dim=-1)
        profile_probs = F.softmax(outputs["profile_logits"][0], dim=-1)
        event_phrase_presence_probability = 1.0
        if bool(getattr(self, "event_phrase_presence_enabled", True)) and "event_phrase_presence_logits" in outputs:
            event_phrase_presence_probability = float(torch.sigmoid(outputs["event_phrase_presence_logits"][0]).item())
        turn_kind_index = int(turn_kind_probs.argmax().item())
        status_index = int(status_probs.argmax().item())
        time_index = int(time_probs.argmax().item())
        profile_index = int(profile_probs.argmax().item())
        turn_kind = TURN_KIND_VALUES[turn_kind_index]
        turn_kind_confidence = float(turn_kind_probs[turn_kind_index].item())
        predicted_status = STATUS_VALUES[status_index]
        predicted_time_granularity = TIME_GRANULARITY_VALUES[time_index]
        predicted_profile = PROFILE_TYPE_VALUES[profile_index]
        sentence_index = -1
        sentence_confidence = 0.0
        if example.sentences:
            sentence_distribution = F.softmax(outputs["sentence_logits"][0, : len(example.sentences)], dim=-1)
            sentence_index = int(sentence_distribution.argmax().item())
            sentence_confidence = float(sentence_distribution[sentence_index].item())
        selected_sentence = ""
        if 0 <= sentence_index < len(example.sentences):
            selected_sentence = clean_text(example.sentences[sentence_index])
        provisional_time_expression_span = ""
        provisional_compact_event_phrase = ""
        provisional_surface_event_phrase = ""
        learned_span_event_phrase = ""
        learned_span_confidence = 0.0
        learned_span_start_index = -1
        learned_span_end_index = -1
        learned_span_source = "sentence_fallback"
        event_spans: List[Dict[str, Any]] = []
        if selected_sentence:
            span_available = (
                bool(getattr(self, "span_enabled", True))
                and "span_start_logits" in outputs
                and "span_end_logits" in outputs
                and 0 <= sentence_index < int(outputs["span_start_logits"].shape[1])
            )
            pair_span_available = (
                bool(getattr(self, "span_joint_enabled", True))
                and "span_pair_logits" in outputs
                and 0 <= sentence_index < int(outputs["span_pair_logits"].shape[1])
            )
            token_coverage_available = (
                bool(getattr(self, "token_coverage_enabled", True))
                and "token_coverage_logits" in outputs
                and 0 <= sentence_index < int(outputs["token_coverage_logits"].shape[1])
            )
            event_spans = _runtime_span_candidates_from_logits(
                sentence=selected_sentence,
                sentence_index=sentence_index,
                outputs=outputs,
                span_enabled=span_available,
                span_joint_enabled=pair_span_available,
                token_coverage_enabled=token_coverage_available,
                max_candidates=max_event_spans,
            )
            if event_spans:
                top_span = dict(event_spans[0])
                learned_span_start_index = int(top_span.get("span_start_index", -1))
                learned_span_end_index = int(top_span.get("span_end_index", -1))
                learned_span_confidence = float(top_span.get("confidence", 0.0) or 0.0)
                learned_span_event_phrase = clean_text(top_span.get("text", ""))
                learned_span_source = clean_text(top_span.get("source", "")) or "learned_span"
            provisional_time_expression_span = _recover_time_expression_span(
                selected_sentence,
                predicted_time_granularity=predicted_time_granularity,
            )
            provisional_compact_event_phrase = _event_phrase_from_sentence(
                selected_sentence,
                profile_type=predicted_profile,
                time_expression_span=provisional_time_expression_span,
            )
            provisional_surface_event_phrase = learned_span_event_phrase or clean_text(selected_sentence)
        metadata = {
            "turn_kind": turn_kind,
            "turn_kind_confidence": turn_kind_confidence,
            "status": predicted_status,
            "status_confidence": float(status_probs[status_index].item()),
            "time_granularity": predicted_time_granularity,
            "time_confidence": float(time_probs[time_index].item()),
            "profile_type": predicted_profile,
            "profile_confidence": float(profile_probs[profile_index].item()),
            "event_phrase_presence_probability": event_phrase_presence_probability,
            "event_phrase_presence_threshold": float(event_phrase_presence_threshold),
            "sentence_index": sentence_index,
            "sentence_confidence": sentence_confidence,
            "selected_sentence": selected_sentence,
            "span_start_index": learned_span_start_index,
            "span_end_index": learned_span_end_index,
            "span_confidence": learned_span_confidence,
            "event_spans": event_spans,
            "multi_event_span_interface_version": "span_candidates_v1",
            "time_expression_span": provisional_time_expression_span,
            "event_phrase_compact": provisional_compact_event_phrase,
            "event_phrase_surface": provisional_surface_event_phrase,
            "event_phrase_source": learned_span_source if learned_span_event_phrase else "sentence_fallback",
            "semantic_slot_model": predicted_profile or ("greeting" if turn_kind == "greeting" else turn_kind),
        }
        if turn_kind == "other" and turn_kind_confidence >= other_threshold:
            metadata["write_decision"] = "reject_other"
            return {"annotation": None, "metadata": metadata}
        if (
            bool(getattr(self, "event_phrase_presence_enabled", True))
            and "event_phrase_presence_logits" in outputs
            and event_phrase_presence_probability < float(event_phrase_presence_threshold)
            and clean_text(predicted_time_granularity) in {"", "none"}
            and not clean_text(predicted_profile)
            and turn_kind != "greeting"
        ):
            metadata["write_decision"] = "reject_no_event_phrase"
            return {"annotation": None, "metadata": metadata}
        write_gate_confidence = _runtime_write_gate_confidence(
            turn_kind=turn_kind,
            turn_kind_confidence=turn_kind_confidence,
            sentence_confidence=sentence_confidence,
            status_label=predicted_status,
            status_confidence=float(status_probs[status_index].item()),
            predicted_time_granularity=predicted_time_granularity,
            time_confidence=float(time_probs[time_index].item()),
            predicted_profile=predicted_profile,
            profile_confidence=float(profile_probs[profile_index].item()),
            selected_sentence=selected_sentence,
        )
        metadata["write_gate_confidence"] = write_gate_confidence
        metadata["write_gate_threshold"] = float(confidence_threshold)
        if write_gate_confidence < confidence_threshold:
            metadata["write_decision"] = "reject_low_confidence"
            return {"annotation": None, "metadata": metadata}
        if turn_kind == "greeting":
            metadata["write_decision"] = "accept_greeting"
            return {
                "annotation": {
                    "event_phrase": "greeting",
                    "semantic_slot": "greeting",
                    "target_status": "current",
                    "time_expression_span": "",
                    "time_granularity": "none",
                    "profile_type": "",
                },
                "metadata": metadata,
            }
        if sentence_index < 0 or sentence_index >= len(example.sentences):
            metadata["write_decision"] = "reject_missing_sentence"
            return {"annotation": None, "metadata": metadata}
        time_expression_span = provisional_time_expression_span
        selected_sentence = example.sentences[sentence_index]
        compact_event_phrase = provisional_compact_event_phrase or _event_phrase_from_sentence(
            selected_sentence,
            profile_type=predicted_profile,
            time_expression_span=time_expression_span,
        )
        surface_event_phrase = provisional_surface_event_phrase or clean_text(selected_sentence)
        event_phrase = learned_span_event_phrase or surface_event_phrase or compact_event_phrase
        if not event_phrase and compact_event_phrase:
            event_phrase = compact_event_phrase
        if not event_phrase:
            metadata["write_decision"] = "reject_empty_event_phrase"
            return {"annotation": None, "metadata": metadata}
        semantic_slot = "event"
        if turn_kind == "greeting":
            semantic_slot = "greeting"
        metadata["time_expression_span"] = time_expression_span
        metadata["event_phrase_compact"] = compact_event_phrase
        metadata["event_phrase_surface"] = surface_event_phrase
        metadata["event_phrase_source"] = learned_span_source if learned_span_event_phrase else "sentence_fallback"
        metadata["semantic_slot_model"] = predicted_profile or "event"
        annotation = {
            "event_phrase": event_phrase,
            "semantic_slot": semantic_slot,
            "target_status": predicted_status,
            "time_expression_span": time_expression_span,
            "time_granularity": predicted_time_granularity,
            "profile_type": predicted_profile,
        }
        metadata["write_decision"] = "accept_annotation"
        return {"annotation": normalize_controlled_annotation(annotation), "metadata": metadata}
