from __future__ import annotations

from functools import lru_cache
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence


_TOKEN_RE = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_MONTH_MARKERS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_NON_SPEAKER_CAPITALIZED_WORDS = {
    "What",
    "When",
    "Which",
    "Where",
    "Who",
    "Why",
    "How",
    "Today",
    "Tomorrow",
    "Yesterday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
}
_QUESTION_SPEAKER_PREPOSITIONS = {
    "at",
    "about",
    "around",
    "by",
    "during",
    "for",
    "from",
    "in",
    "inside",
    "into",
    "near",
    "of",
    "on",
    "over",
    "through",
    "to",
    "under",
    "with",
}
_QUESTION_SPEAKER_AUXILIARIES = (
    "did",
    "does",
    "do",
    "is",
    "was",
    "were",
    "has",
    "have",
    "had",
    "will",
    "would",
    "can",
    "could",
    "should",
    "may",
    "might",
)
_WEEKDAY_MARKERS = {
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "mon",
    "tue",
    "wed",
    "thu",
    "fri",
    "sat",
    "sun",
}
_TMCRA_TEMPORAL_TOKEN_HINTS = {
    "today",
    "tomorrow",
    "yesterday",
    "tonight",
    "morning",
    "afternoon",
    "evening",
    "night",
    "week",
    "weekend",
    "month",
    "year",
    "day",
    *_MONTH_MARKERS,
    *_WEEKDAY_MARKERS,
}
_QUESTION_EDGE_GLUE_TOKENS = {
    "a",
    "an",
    "the",
    "to",
    "as",
    "at",
    "in",
    "on",
    "of",
    "for",
    "with",
    "from",
    "about",
    "around",
    "into",
    "over",
    "under",
}
_QUESTION_LEADING_FUNCTION_RE = re.compile(
    rf"^\s*(?:what|when|which|where|who|why|how)\b(?:\s+(?:{'|'.join(_QUESTION_SPEAKER_AUXILIARIES)}))?",
    re.IGNORECASE,
)
_TEMPORAL_REFERENCE_RE = re.compile(
    r"\b(?:when|date|day|month|year|today|tomorrow|yesterday|tonight|morning|afternoon|evening|night|week(?:end)?|last|next|before|after)\b",
    re.IGNORECASE,
)
_TEMPORAL_ANSWER_PREFIX_RE = re.compile(r"^\s*(?:when|since\s+when)\b", re.IGNORECASE)
_TEMPORAL_ANSWER_WH_RE = re.compile(r"^\s*(?:what|which)\s+(?:date|day|month|year|time)\b", re.IGNORECASE)
_TEMPORAL_ANSWER_DURATION_RE = re.compile(
    r"^\s*how\s+(?:long|many\s+(?:days?|weeks?|months?|years?))\b",
    re.IGNORECASE,
)
_EDUCATION_YEAR_RE = re.compile(
    r"\b(?:year|semester|grade)\s+(?:in|of|at)\s+(?:\w+\s+){0,3}(?:school|college|university|uni)\b",
    re.IGNORECASE,
)
_PROFILE_COPULAR_RE = re.compile(r"^\s*who\s+is\b", re.IGNORECASE)
_PROFILE_ATTRIBUTE_RE = re.compile(r"^\s*(?:what|where)\s+(?:does|do|is|are|was|were|has|have|had)\b", re.IGNORECASE)
_PROFILE_QUERY_HINT_RE = re.compile(
    r"\b(?:occupation|job|work(?:ing)?|study|studying|research|major(?:ing)?|profession|career|school|college|university)\b",
    re.IGNORECASE,
)
_PROFILE_POSSESSIVE_RE = re.compile(r"\b[A-Za-z][A-Za-z'-]+(?:\s+[A-Za-z][A-Za-z'-]+)?'s\b", re.IGNORECASE)
_PLANNED_STATUS_RE = re.compile(
    r"\b(?:will|going\s+to|plan(?:ning)?\s+to|scheduled\s+to|hoping\s+to|preparing\s+to)\b",
    re.IGNORECASE,
)
_CURRENT_STATUS_RE = re.compile(r"\b(?:currently|now|still)\b|\b(?:is|are|am)\s+\w+ing\b", re.IGNORECASE)
_PAST_STATUS_RE = re.compile(r"\b(?:did|was|were|had|has)\b", re.IGNORECASE)


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return " ".join(text.split())


def normalize_text(value: Any) -> str:
    return clean_text(value).lower()


@lru_cache(maxsize=32768)
def _tokenize_normalized_text_cached(text: str) -> tuple[str, ...]:
    if not text:
        return ()
    english = tuple(_TOKEN_RE.findall(text))
    cjk = tuple(char for char in text if "\u4e00" <= char <= "\u9fff")
    if english or cjk:
        return tuple([*english, *cjk])
    return tuple(char for char in text if char.strip())


def tokenize_text(value: Any) -> List[str]:
    text = normalize_text(value)
    if not text:
        return []
    return list(_tokenize_normalized_text_cached(text))


def dedupe_texts(values: Iterable[Any], *, max_items: int | None = None) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        text = clean_text(value)
        if not text:
            continue
        key = normalize_text(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if max_items is not None and len(result) >= max_items:
            break
    return result


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, json_dumps(payload))


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    text = "\n".join(json_dumps(dict(row)) for row in rows)
    _atomic_write_text(path, text + ("\n" if text else ""))


def _extract_question_speakers(question: str) -> List[str]:
    clean = clean_text(question)
    if not clean:
        return []

    candidates: List[str] = []
    seen = set()

    def add_candidate(raw_value: str) -> None:
        candidate = clean_text(raw_value).removesuffix("'s")
        if not candidate:
            return
        parts = [part for part in candidate.split() if clean_text(part)]
        if not parts or any(part in _NON_SPEAKER_CAPITALIZED_WORDS for part in parts):
            return
        key = normalize_text(candidate)
        if key in seen:
            return
        seen.add(key)
        candidates.append(candidate)

    name_pattern = r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"
    anchored_patterns = (
        rf"\b({name_pattern})'s\b",
        rf"\b(?:{'|'.join(_QUESTION_SPEAKER_AUXILIARIES)})\s+({name_pattern})\b",
        rf"^\s*(?:What|When|Which|Where|Why|How)\s+({name_pattern})\b",
    )
    for pattern in anchored_patterns:
        for match in re.finditer(pattern, clean):
            add_candidate(match.group(1))

    for match in re.finditer(rf"\b({name_pattern})\b", clean):
        prefix = clean[: match.start()].rstrip()
        previous_word_match = re.search(r"([A-Za-z]+)$", prefix)
        previous_word = normalize_text(previous_word_match.group(1)) if previous_word_match else ""
        if previous_word in _QUESTION_SPEAKER_PREPOSITIONS:
            continue
        add_candidate(match.group(1))
    return candidates


def _normalized_token_list(value: Sequence[str] | str) -> List[str]:
    if isinstance(value, str):
        return list(_tokenize_normalized_text_cached(normalize_text(value)))
    tokens: List[str] = []
    for item in value:
        for token in tokenize_text(item):
            normalized = normalize_text(token)
            if normalized:
                tokens.append(normalized)
    return tokens


def _trim_question_anchor_edge_tokens(tokens: Sequence[str]) -> List[str]:
    trimmed = [normalize_text(clean_text(token)) for token in list(tokens or []) if clean_text(token)]
    while trimmed and trimmed[0] in _QUESTION_EDGE_GLUE_TOKENS:
        trimmed.pop(0)
    while trimmed and trimmed[-1] in _QUESTION_EDGE_GLUE_TOKENS:
        trimmed.pop()
    return trimmed


def _question_has_subject_reference(question: str, speaker_candidates: Sequence[str]) -> bool:
    if bool(list(speaker_candidates or [])) or bool(_PROFILE_POSSESSIVE_RE.search(question)):
        return True
    clean = clean_text(question)
    if not clean:
        return False
    content_text = clean
    prefix_match = _QUESTION_LEADING_FUNCTION_RE.match(content_text)
    if prefix_match:
        content_text = content_text[prefix_match.end() :]
    content_tokens = _trim_question_anchor_edge_tokens(_normalized_token_list(content_text))
    if not content_tokens:
        return False
    first_token = normalize_text(content_tokens[0])
    if not first_token or first_token in _TMCRA_TEMPORAL_TOKEN_HINTS:
        return False
    return True


def _question_requests_profile_detail(question: str, lowered: str, speaker_candidates: Sequence[str]) -> bool:
    has_subject_reference = _question_has_subject_reference(question, speaker_candidates)
    if not has_subject_reference:
        return False
    if _PROFILE_COPULAR_RE.search(question):
        return True
    if _PROFILE_QUERY_HINT_RE.search(lowered):
        return True
    return bool(_PROFILE_ATTRIBUTE_RE.search(question) and _PROFILE_QUERY_HINT_RE.search(lowered))


def _question_has_temporal_reference(question: str, lowered: str) -> bool:
    question_tokens = tokenize_text(question)
    first_token = normalize_text(question_tokens[0]) if question_tokens else ""
    if first_token == "when":
        return True
    if _TEMPORAL_REFERENCE_RE.search(lowered):
        return True
    if any(marker in lowered for marker in _MONTH_MARKERS):
        return True
    if re.search(r"\b(?:mon|tue|wed|thu|fri|sat|sun)(?:day)?\b", lowered):
        return True
    if re.search(r"\b\d{4}\b", lowered):
        return True
    return False


def _question_requests_temporal_answer(question: str, lowered: str) -> bool:
    clean = clean_text(question)
    if not clean:
        return False
    if _EDUCATION_YEAR_RE.search(clean):
        return False
    if _TEMPORAL_ANSWER_PREFIX_RE.search(clean):
        return True
    if _TEMPORAL_ANSWER_WH_RE.search(clean):
        return True
    if _TEMPORAL_ANSWER_DURATION_RE.search(clean):
        return True
    return False


def _question_is_temporal(question: str, lowered: str) -> bool:
    return _question_requests_temporal_answer(question, lowered)


def _question_time_granularity(question: str, lowered: str) -> str:
    if not _question_requests_temporal_answer(question, lowered):
        return ""
    if "month" in lowered or any(marker in lowered for marker in _MONTH_MARKERS):
        return "month"
    if "year" in lowered or re.search(r"\b\d{4}\b", lowered):
        return "year"
    return "day_or_coarse"


def _question_target_status(question: str, lowered: str) -> str:
    if _PLANNED_STATUS_RE.search(question):
        return "planned"
    if _CURRENT_STATUS_RE.search(question):
        return "current"
    if _PAST_STATUS_RE.search(lowered):
        return "past"
    return ""


def _question_semantic_slot(question: str, lowered: str, speaker_candidates: Sequence[str]) -> str:
    if _question_is_temporal(question, lowered):
        return "event_time"
    if _question_requests_profile_detail(question, lowered, speaker_candidates):
        return "profile"
    return "event"


def _strip_question_speaker_mentions(text: str, speaker_candidates: Sequence[str]) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    for candidate in sorted(
        (clean_text(item) for item in list(speaker_candidates or []) if clean_text(item)),
        key=len,
        reverse=True,
    ):
        cleaned = re.sub(rf"\b{re.escape(candidate)}\b", " ", cleaned, flags=re.IGNORECASE)
    return clean_text(cleaned)


def _normalized_token_set(value: Sequence[str] | str) -> set[str]:
    if isinstance(value, str):
        return set(_tokenize_normalized_text_cached(normalize_text(value)))
    return set(_normalized_token_list(value))


def _question_anchor_tokens(question: str, question_features: Mapping[str, Any]) -> List[str]:
    existing = [
        normalize_text(clean_text(token))
        for token in list(question_features.get("question_anchor_tokens", []) or [])
        if clean_text(token)
    ]
    if existing:
        return dedupe_texts(existing)
    clean = clean_text(question)
    if not clean:
        return []
    speaker_tokens = _normalized_token_set(list(question_features.get("speaker_candidates", []) or []))
    content_text = clean
    prefix_match = _QUESTION_LEADING_FUNCTION_RE.match(content_text)
    if prefix_match:
        content_text = content_text[prefix_match.end() :]
    content_text = _strip_question_speaker_mentions(
        content_text,
        list(question_features.get("speaker_candidates", []) or []),
    )
    if bool(question_features.get("is_temporal", False)):
        content_text = _TEMPORAL_REFERENCE_RE.sub(" ", content_text)
        content_text = re.sub(r"\b\d{1,4}\b", " ", content_text)
    target_status = clean_text(question_features.get("target_status_target", ""))
    if target_status == "planned":
        content_text = _PLANNED_STATUS_RE.sub(" ", content_text)
    elif target_status == "current":
        content_text = _CURRENT_STATUS_RE.sub(" ", content_text)
    elif target_status == "past":
        content_text = _PAST_STATUS_RE.sub(" ", content_text)
    anchors = _trim_question_anchor_edge_tokens(
        [
            token
            for token in _normalized_token_list(content_text)
            if token and token not in speaker_tokens and not re.fullmatch(r"\d+", token)
        ]
    )
    if anchors:
        return dedupe_texts(anchors)
    fallback_tokens = _trim_question_anchor_edge_tokens(
        [
            token
            for token in _normalized_token_list(clean)
            if token and token not in speaker_tokens and not re.fullmatch(r"\d+", token)
        ]
    )
    return dedupe_texts(fallback_tokens)


def _positive_event_payloads_have_profile(payloads: Sequence[Mapping[str, Any]]) -> bool:
    for payload in payloads:
        metadata = dict(payload.get("metadata", {}) or {})
        if any(
            clean_text(payload.get(key, metadata.get(key, "")))
            for key in ("profile_type", "profile_value")
        ):
            return True
    return False


def answer_type_from_query(
    question: str,
    *,
    category: Any = "",
    positive_event_ids: Sequence[str] | None = None,
    question_features: Mapping[str, Any] | None = None,
    positive_event_payloads: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    positives = [clean_text(item) for item in list(positive_event_ids or []) if clean_text(item)]
    if str(category or "").strip() == "5" or not positives:
        return "abstain"
    resolved_question_features = dict(question_features or extract_question_features(question))
    if len(positives) > 1:
        return "multi_evidence"
    if bool(resolved_question_features.get("is_temporal", False)):
        return "time"
    if _positive_event_payloads_have_profile(list(positive_event_payloads or [])):
        return "profile"
    if clean_text(resolved_question_features.get("semantic_slot_target", "")) == "profile":
        return "profile"
    return "event_text"


def extract_question_features(question: str) -> Dict[str, Any]:
    clean = clean_text(question)
    lowered = normalize_text(clean)
    speaker_candidates = _extract_question_speakers(clean)
    semantic_slot = _question_semantic_slot(clean, lowered, speaker_candidates)
    target_status = _question_target_status(clean, lowered)
    time_granularity = _question_time_granularity(clean, lowered)
    is_temporal = _question_is_temporal(clean, lowered)
    has_temporal_reference = _question_has_temporal_reference(clean, lowered)
    question_anchor_tokens = _question_anchor_tokens(
        clean,
        {"speaker_candidates": list(speaker_candidates)},
    )
    return {
        "speaker_candidates": list(speaker_candidates),
        "semantic_slot_target": semantic_slot,
        "target_status_target": target_status,
        "time_granularity_target": time_granularity,
        "is_temporal": is_temporal,
        "has_temporal_reference": has_temporal_reference,
        "question_anchor_tokens": list(question_anchor_tokens),
    }


def build_path_id(event_id: str, path_type: str, support_node_id: str) -> str:
    return f"{clean_text(event_id)}::{clean_text(path_type)}::{clean_text(support_node_id)}"


def build_default_path_templates(
    *,
    event_id: str,
    speaker_node_id: str,
    time_node_ids: Sequence[str] = (),
    profile_node_ids: Sequence[str] = (),
    status_node_ids: Sequence[str] = (),
    source_turn_node_ids: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    paths: List[Dict[str, Any]] = []
    for support_node_id in time_node_ids:
        paths.append(
            {
                "id": build_path_id(event_id, "speaker_event_time", support_node_id),
                "type": "speaker_event_time",
                "event_id": event_id,
                "node_ids": [speaker_node_id, event_id, support_node_id],
            }
        )
    for support_node_id in profile_node_ids:
        paths.append(
            {
                "id": build_path_id(event_id, "speaker_event_profile", support_node_id),
                "type": "speaker_event_profile",
                "event_id": event_id,
                "node_ids": [speaker_node_id, event_id, support_node_id],
            }
        )
    for support_node_id in status_node_ids:
        paths.append(
            {
                "id": build_path_id(event_id, "speaker_event_status", support_node_id),
                "type": "speaker_event_status",
                "event_id": event_id,
                "node_ids": [speaker_node_id, event_id, support_node_id],
            }
        )
    for support_node_id in source_turn_node_ids:
        paths.append(
            {
                "id": build_path_id(event_id, "speaker_event_source_turn", support_node_id),
                "type": "speaker_event_source_turn",
                "event_id": event_id,
                "node_ids": [speaker_node_id, event_id, support_node_id],
            }
        )
    return paths
