from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence


AUDIT_FIELD_NAMES = (
    "event_phrase",
    "semantic_slot",
    "target_status",
    "time_expression_span",
    "time_granularity",
    "profile_type",
)
AUDIT_DECISIONS = ("accept", "edit", "reject", "skip")
TIME_GRANULARITY_VALUES = {"day", "month", "year", "relative_day_reference", "none", ""}
PROFILE_TYPE_VALUES = {"identity", "research_topic", "education", "occupation", ""}
STATUS_VALUES = {"past", "current", "planned", ""}
_MONTH_NAMES = (
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
_RELATIVE_DAY_MARKERS = (
    "today",
    "yesterday",
    "tomorrow",
    "last night",
    "tonight",
    "this morning",
    "this afternoon",
    "this evening",
    "yesterday morning",
    "yesterday evening",
    "days ago",
    "weeks ago",
    "months ago",
    "years ago",
)
_RELATIVE_WEEKDAY_PREFIXES = ("last ", "next ", "this ")
_WEEKDAY_NAMES = (
    "monday",
    "mon",
    "tuesday",
    "tue",
    "tues",
    "wednesday",
    "wed",
    "thursday",
    "thu",
    "thur",
    "thurs",
    "friday",
    "fri",
    "saturday",
    "sat",
    "sunday",
    "sun",
)
_UNSUPPORTED_TIME_PATTERNS = (
    r"\blast week(?:end)?\b",
    r"\bthis week(?:end)?\b",
    r"\bnext week(?:end)?\b",
    r"\bsoon\b",
    r"\blater\b",
    r"\bsometime\b",
    r"\bsomeday\b",
    r"\bone day\b",
    r"\beventually\b",
    r"\brecently\b",
    r"\beach day\b",
    r"\bevery day\b",
    r"\bdaily\b",
    r"\beach week\b",
    r"\bevery week\b",
    r"\bweekly\b",
    r"\beach month\b",
    r"\bevery month\b",
    r"\bmonthly\b",
    r"\beach year\b",
    r"\bevery year\b",
    r"\byearly\b",
    r"\ba few years?\s+back\b",
    r"\b\d+\s+years?(?:\s+now|\s+back)?\b",
    r"\b\d+\s+months?(?:\s+now|\s+back)?\b",
    r"\b\d+\s+weeks?(?:\s+now|\s+back)?\b",
    r"\bduring\s+[^.?!,;]{0,60}\b(?:training|season|campaign|project|course|tour|internship|rehearsal)\b",
)
_ABSTRACT_EVENT_LABELS = {
    "greeting",
    "hello",
    "hi",
    "small talk",
    "smalltalk",
    "farewell",
    "goodbye",
    "catch up",
    "catch-up",
}
_TOKEN_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "be",
    "been",
    "being",
    "for",
    "from",
    "had",
    "has",
    "have",
    "he",
    "her",
    "hers",
    "him",
    "his",
    "i",
    "in",
    "into",
    "is",
    "it",
    "its",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "ours",
    "she",
    "that",
    "the",
    "their",
    "them",
    "they",
    "this",
    "those",
    "to",
    "us",
    "was",
    "we",
    "were",
    "will",
    "with",
    "you",
    "your",
    "yours",
}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ").split()).strip()


def normalize_text(value: Any) -> str:
    return clean_text(value).lower()


def _tokenize_text(value: Any) -> List[str]:
    return re.findall(r"[a-z0-9]+", normalize_text(value))


def _content_tokens(value: Any) -> List[str]:
    return [token for token in _tokenize_text(value) if token not in _TOKEN_STOPWORDS]


def _contains_relative_weekday(text: str) -> bool:
    lowered = normalize_text(text)
    return any(
        f"{prefix}{weekday}" in lowered
        for prefix in _RELATIVE_WEEKDAY_PREFIXES
        for weekday in _WEEKDAY_NAMES
    )


def _looks_like_relative_day(span: str) -> bool:
    lowered = normalize_text(span)
    if not lowered:
        return False
    if any(marker in lowered for marker in _RELATIVE_DAY_MARKERS):
        return True
    if _contains_relative_weekday(lowered):
        return True
    return bool(re.search(r"\b\d+\s+days?\s+ago\b", lowered))


def _looks_like_day(span: str) -> bool:
    lowered = normalize_text(span)
    if not lowered:
        return False
    if _looks_like_relative_day(lowered):
        return False
    if re.search(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", lowered):
        return True
    if any(month in lowered for month in _MONTH_NAMES) and re.search(r"\b\d{1,2}\b", lowered):
        return True
    return False


def _looks_like_month(span: str) -> bool:
    lowered = normalize_text(span)
    if not lowered:
        return False
    if lowered in {"next month", "last month", "this month"}:
        return True
    if any(month in lowered for month in _MONTH_NAMES):
        if re.search(r"\b\d{1,2}\b", lowered):
            return False
        return True
    return bool(re.fullmatch(r"\d{4}-\d{2}", lowered))


def _looks_like_year(span: str) -> bool:
    lowered = normalize_text(span)
    if not lowered:
        return False
    if lowered in {"next year", "last year", "this year"}:
        return True
    return bool(re.fullmatch(r"(?:in\s+)?\d{4}", lowered))


def _looks_like_unsupported_time_expression(span: str) -> bool:
    lowered = normalize_text(span)
    if not lowered:
        return False
    return any(re.search(pattern, lowered) for pattern in _UNSUPPORTED_TIME_PATTERNS)


def _time_granularity_matches_span(span: str, granularity: str) -> bool:
    clean_span = clean_text(span)
    clean_granularity = clean_text(granularity)
    if not clean_span:
        return clean_granularity in {"", "none"}
    if clean_granularity in {"", "none"}:
        return _looks_like_unsupported_time_expression(clean_span)
    if clean_granularity == "relative_day_reference":
        return _looks_like_relative_day(clean_span)
    if clean_granularity == "day":
        return _looks_like_day(clean_span)
    if clean_granularity == "month":
        return _looks_like_month(clean_span)
    if clean_granularity == "year":
        return _looks_like_year(clean_span)
    return False


def _phrase_grounded_in_turn(phrase: str, turn_text: str, *, semantic_slot: str) -> bool:
    clean_phrase = clean_text(phrase)
    clean_turn = clean_text(turn_text)
    if not clean_phrase:
        return True
    lowered_phrase = normalize_text(clean_phrase)
    lowered_slot = normalize_text(semantic_slot)
    if lowered_phrase in _ABSTRACT_EVENT_LABELS or lowered_slot in _ABSTRACT_EVENT_LABELS:
        return True
    if len(clean_phrase) >= 4 and lowered_phrase in normalize_text(clean_turn):
        return True
    phrase_tokens = _content_tokens(clean_phrase)
    if len(phrase_tokens) < 2:
        return True
    turn_tokens = set(_content_tokens(clean_turn))
    overlap = sum(1 for token in phrase_tokens if token in turn_tokens)
    if len(phrase_tokens) <= 2:
        return overlap == len(phrase_tokens)
    return (overlap / max(1, len(phrase_tokens))) >= 0.6


def _time_span_grounded_in_turn(span: str, turn_text: str) -> bool:
    clean_span = clean_text(span)
    clean_turn = clean_text(turn_text)
    if not clean_span:
        return True
    lowered_span = normalize_text(clean_span)
    lowered_turn = normalize_text(clean_turn)
    if len(clean_span) >= 4 and lowered_span in lowered_turn:
        return True
    span_tokens = _content_tokens(clean_span)
    if not span_tokens:
        return False
    turn_tokens = set(_content_tokens(clean_turn))
    overlap = sum(1 for token in span_tokens if token in turn_tokens)
    return overlap >= len(span_tokens)


def _profile_slot_consistent(*, profile_type: str, semantic_slot: str) -> bool:
    clean_profile = clean_text(profile_type)
    clean_slot = clean_text(semantic_slot)
    if not clean_profile or clean_profile not in PROFILE_TYPE_VALUES:
        return True
    if not clean_slot:
        return True
    if clean_slot in {clean_profile, "event", "profile"}:
        return True
    if clean_slot in PROFILE_TYPE_VALUES and clean_slot != clean_profile:
        return False
    return True


def _status_consistent(*, target_status: str, current_turn: str) -> bool:
    clean_status = clean_text(target_status)
    if clean_status not in STATUS_VALUES or not clean_status:
        return True
    lowered_turn = normalize_text(current_turn)
    future_markers = ("going to", "planning", "plan to", "plans to", "will ", "next ", "let's ", "lets ", "should ")
    past_markers = ("yesterday", "ago", "last night", "last week", "attended", "joined", "went")
    has_future = any(marker in lowered_turn for marker in future_markers)
    has_past = any(marker in lowered_turn for marker in past_markers) or bool(
        re.search(r"\b(?:\w+ed|was|were|had|did|felt|gave|shared|made|spoke|wrote|saw|met|came)\b", lowered_turn)
    )
    if clean_status == "past" and has_future and not has_past:
        return False
    if clean_status == "planned" and has_past and not has_future:
        return False
    return True


def evaluate_teacher_annotation_consistency(
    *,
    current_turn: str,
    teacher_fields: Mapping[str, Any],
) -> Dict[str, Any]:
    normalized = _normalized_fields(teacher_fields)
    issues: List[str] = []
    event_grounded = _phrase_grounded_in_turn(
        normalized.get("event_phrase", ""),
        current_turn,
        semantic_slot=normalized.get("semantic_slot", ""),
    )
    if not event_grounded:
        issues.append("event_phrase_not_grounded")

    span_grounded = _time_span_grounded_in_turn(
        normalized.get("time_expression_span", ""),
        current_turn,
    )
    if not span_grounded:
        issues.append("time_expression_not_grounded")

    granularity_consistent = _time_granularity_matches_span(
        normalized.get("time_expression_span", ""),
        normalized.get("time_granularity", ""),
    )
    if not granularity_consistent:
        issues.append("time_granularity_mismatch")

    profile_consistent = _profile_slot_consistent(
        profile_type=normalized.get("profile_type", ""),
        semantic_slot=normalized.get("semantic_slot", ""),
    )
    if not profile_consistent:
        issues.append("profile_semantic_slot_mismatch")

    status_consistent = _status_consistent(
        target_status=normalized.get("target_status", ""),
        current_turn=current_turn,
    )
    if not status_consistent:
        issues.append("target_status_mismatch")

    return {
        "passed": not issues,
        "issues": issues,
        "checks": {
            "event_phrase_grounded": event_grounded,
            "time_expression_grounded": span_grounded,
            "time_granularity_consistent": granularity_consistent,
            "profile_semantic_consistent": profile_consistent,
            "target_status_consistent": status_consistent,
        },
    }


def build_teacher_audit_candidate(
    *,
    conversation_id: str,
    split: str,
    session_name: str,
    turn_index: int,
    dia_id: str,
    speaker: str,
    current_turn: str,
    previous_turn: str,
    next_turn: str,
    session_timestamp: str,
    teacher_fields: Mapping[str, Any],
    semantic_consistency: Mapping[str, Any],
    teacher_latency_ms: int,
    teacher_request_excerpt: str = "",
    teacher_raw_output: str = "",
    teacher_repair_output: str = "",
    teacher_error_code: str = "",
    teacher_error_stage: str = "",
) -> Dict[str, Any]:
    return {
        "audit_id": (
            f"{clean_text(conversation_id)}:{clean_text(session_name)}:"
            f"{int(turn_index or 0)}:{clean_text(dia_id) or 'dia'}"
        ),
        "conversation_id": clean_text(conversation_id),
        "split": clean_text(split),
        "session_name": clean_text(session_name),
        "turn_index": int(turn_index or 0),
        "dia_id": clean_text(dia_id),
        "speaker": clean_text(speaker),
        "current_turn": clean_text(current_turn),
        "previous_turn": clean_text(previous_turn),
        "next_turn": clean_text(next_turn),
        "session_timestamp": clean_text(session_timestamp),
        "teacher_fields": _normalized_fields(teacher_fields),
        "semantic_consistency": {
            "passed": bool(dict(semantic_consistency).get("passed", False)),
            "issues": [clean_text(item) for item in list(dict(semantic_consistency).get("issues", []) or []) if clean_text(item)],
            "checks": dict(dict(semantic_consistency).get("checks", {}) or {}),
        },
        "teacher_latency_ms": int(teacher_latency_ms or 0),
        "teacher_request_excerpt": clean_text(teacher_request_excerpt),
        "teacher_raw_output": clean_text(teacher_raw_output),
        "teacher_repair_output": clean_text(teacher_repair_output),
        "teacher_error_code": clean_text(teacher_error_code),
        "teacher_error_stage": clean_text(teacher_error_stage),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            rows.append(dict(json.loads(line)))
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise RuntimeError(
                f"Failed to parse JSONL file {path} at line {line_number}: "
                f"line {exc.lineno} column {exc.colno} char {exc.pos}: {exc.msg}"
            ) from exc
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) for row in rows)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def _normalized_fields(payload: Mapping[str, Any]) -> Dict[str, str]:
    return {field: clean_text(dict(payload).get(field, "")) for field in AUDIT_FIELD_NAMES}


def _load_candidate_rows(inputs: Sequence[Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for input_path in inputs:
        path = Path(input_path)
        if path.is_dir():
            candidate_path = path / "teacher_audit_candidates.jsonl"
        else:
            candidate_path = path
        rows.extend(read_jsonl(candidate_path))
    return rows


def candidate_bucket(payload: Mapping[str, Any]) -> str:
    teacher_fields = _normalized_fields(dict(payload).get("teacher_fields", {}) or {})
    semantic_consistency = dict(payload).get("semantic_consistency", {}) or {}
    issues = list(semantic_consistency.get("issues", []) or [])
    return "|".join(
        [
            f"passed={0 if issues else 1}",
            f"time={teacher_fields.get('time_granularity', '') or 'none'}",
            f"profile={teacher_fields.get('profile_type', '') or 'none'}",
            f"slot={teacher_fields.get('semantic_slot', '') or 'event'}",
        ]
    )


def build_teacher_audit_queue(
    *,
    input_paths: Sequence[Path],
    output_path: Path,
    sample_size: int = 200,
    seed: int = 17,
) -> Dict[str, Any]:
    source_rows = _load_candidate_rows(input_paths)
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        bucket = candidate_bucket(row)
        buckets[bucket].append(dict(row))

    rng = random.Random(int(seed))
    for rows in buckets.values():
        rng.shuffle(rows)

    selected: List[Dict[str, Any]] = []
    bucket_names = sorted(buckets)
    while len(selected) < int(sample_size) and bucket_names:
        remaining_bucket_names: List[str] = []
        for bucket_name in bucket_names:
            rows = buckets.get(bucket_name, [])
            if not rows:
                continue
            row = dict(rows.pop())
            row["audit_bucket"] = bucket_name
            row["audit_id"] = clean_text(row.get("audit_id", "")) or (
                f"{clean_text(row.get('conversation_id', 'conversation'))}:"
                f"{clean_text(row.get('session_name', 'session'))}:"
                f"{int(row.get('turn_index', 0) or 0)}:"
                f"{clean_text(row.get('dia_id', 'dia'))}"
            )
            row["queue_created_at"] = datetime.now().isoformat(timespec="seconds")
            selected.append(row)
            if len(selected) >= int(sample_size):
                break
            if rows:
                remaining_bucket_names.append(bucket_name)
        bucket_names = remaining_bucket_names

    write_jsonl(output_path, selected)
    summary = {
        "status": "completed",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_path": str(output_path),
        "input_paths": [str(Path(path)) for path in input_paths],
        "sample_size_requested": int(sample_size),
        "sample_size_written": len(selected),
        "bucket_counts": dict(sorted(Counter(candidate_bucket(row) for row in selected).items())),
    }
    write_json(output_path.with_suffix(".summary.json"), summary)
    return summary


def load_label_map(path: Path) -> Dict[str, Dict[str, Any]]:
    labels = read_jsonl(path)
    result: Dict[str, Dict[str, Any]] = {}
    for row in labels:
        audit_id = clean_text(row.get("audit_id", ""))
        if audit_id:
            result[audit_id] = dict(row)
    return result


def upsert_teacher_audit_label(
    *,
    label_path: Path,
    label_row: Mapping[str, Any],
) -> Dict[str, Any]:
    audit_id = clean_text(label_row.get("audit_id", ""))
    if not audit_id:
        raise ValueError("audit_id is required")
    decision = clean_text(label_row.get("decision", ""))
    if decision and decision not in AUDIT_DECISIONS:
        raise ValueError(f"Unsupported decision: {decision}")
    label_map = load_label_map(label_path)
    normalized = dict(label_row)
    normalized["audit_id"] = audit_id
    normalized["decision"] = decision or "edit"
    human_fields = _normalized_fields(dict(normalized).get("human_fields", {}) or {})
    normalized["human_fields"] = human_fields
    normalized["notes"] = clean_text(normalized.get("notes", ""))
    normalized["labeler"] = clean_text(normalized.get("labeler", ""))
    normalized["labeled_at"] = clean_text(normalized.get("labeled_at", "")) or datetime.now().isoformat(timespec="seconds")
    label_map[audit_id] = normalized
    rows = [label_map[key] for key in sorted(label_map)]
    write_jsonl(label_path, rows)
    return normalized


def _scored_human_fields(candidate_row: Mapping[str, Any], label_row: Mapping[str, Any]) -> Dict[str, str]:
    decision = clean_text(label_row.get("decision", ""))
    human_fields = _normalized_fields(dict(label_row).get("human_fields", {}) or {})
    teacher_fields = _normalized_fields(dict(candidate_row).get("teacher_fields", {}) or {})
    if decision == "accept" and not any(human_fields.values()):
        return teacher_fields
    return human_fields


def score_teacher_audit(
    *,
    queue_rows: Sequence[Mapping[str, Any]],
    label_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    queue_map = {clean_text(row.get("audit_id", "")): dict(row) for row in queue_rows if clean_text(row.get("audit_id", ""))}
    label_map = {clean_text(row.get("audit_id", "")): dict(row) for row in label_rows if clean_text(row.get("audit_id", ""))}

    decision_counts: Counter[str] = Counter()
    field_match_counts: Counter[str] = Counter()
    field_total_counts: Counter[str] = Counter()
    full_match_count = 0
    scored_count = 0
    mismatch_examples: List[Dict[str, Any]] = []

    for audit_id, candidate in sorted(queue_map.items()):
        label = label_map.get(audit_id)
        if label is None:
            continue
        decision = clean_text(label.get("decision", ""))
        decision_counts[decision or "<missing>"] += 1
        if decision == "skip":
            continue
        teacher_fields = _normalized_fields(dict(candidate).get("teacher_fields", {}) or {})
        human_fields = _scored_human_fields(candidate, label)
        scored_count += 1
        full_match = True
        mismatch_payload = {
            "audit_id": audit_id,
            "decision": decision,
            "teacher_fields": teacher_fields,
            "human_fields": human_fields,
        }
        for field in AUDIT_FIELD_NAMES:
            field_total_counts[field] += 1
            if normalize_text(teacher_fields.get(field, "")) == normalize_text(human_fields.get(field, "")):
                field_match_counts[field] += 1
            else:
                full_match = False
        if full_match:
            full_match_count += 1
        elif len(mismatch_examples) < 20:
            mismatch_examples.append(mismatch_payload)

    field_accuracy = {
        field: round(field_match_counts[field] / max(1, field_total_counts[field]), 6)
        for field in AUDIT_FIELD_NAMES
    }
    return {
        "status": "completed",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "queue_count": len(queue_map),
        "label_count": len(label_map),
        "scored_count": scored_count,
        "decision_counts": dict(sorted(decision_counts.items())),
        "field_accuracy": field_accuracy,
        "full_exact_match_rate": round(full_match_count / max(1, scored_count), 6),
        "mismatch_examples": mismatch_examples,
    }
