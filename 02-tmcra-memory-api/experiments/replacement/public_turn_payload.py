from __future__ import annotations

import re
import string
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from experiments.replacement.public_event_signature import compute_public_event_signature
from scripts.controlled_teacher_extraction import infer_controlled_annotation_from_payload

_LEARNED_EVENT_SENTENCE_CONFIDENCE_FLOOR = 0.6
_LEARNED_SPAN_EVENT_PHRASE_SOURCES = {
    "learned_span",
    "learned_joint_span",
    "learned_token_coverage_span",
}

_PUBLIC_WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "weds": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

_PUBLIC_NUMBER_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

_PUBLIC_MONTH_ALIASES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _safe_text(value).lower()).strip("_") or "item"


def _dedupe_texts(items: Iterable[Any]) -> List[str]:
    results: List[str] = []
    seen = set()
    for item in items:
        text = _safe_text(item)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(text)
    return results


def _looks_like_url(value: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://", _safe_text(value), flags=re.IGNORECASE))


def _looks_like_natural_language(value: str) -> bool:
    text = _safe_text(value)
    if not text or _looks_like_url(text):
        return False
    if len(text) < 3:
        return False
    alpha_count = len(re.findall(r"[A-Za-z]", text))
    if alpha_count < 3:
        return False
    return True


def _flatten_auxiliary_text_values(value: Any) -> List[str]:
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return []
    if isinstance(value, str):
        return [value] if _looks_like_natural_language(value) else []
    if isinstance(value, Mapping):
        results: List[str] = []
        for nested_value in value.values():
            results.extend(_flatten_auxiliary_text_values(nested_value))
        return results
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        results = []
        for item in value:
            results.extend(_flatten_auxiliary_text_values(item))
        return results
    return []


def _flatten_auxiliary_url_values(value: Any) -> List[str]:
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, str):
        return [value] if _looks_like_url(value) else []
    if isinstance(value, Mapping):
        results: List[str] = []
        for nested_value in value.values():
            results.extend(_flatten_auxiliary_url_values(nested_value))
        return results
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        results = []
        for item in value:
            results.extend(_flatten_auxiliary_url_values(item))
        return results
    return []


def collect_public_auxiliary_evidence(turn_payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Preserve non-dialogue evidence fields, such as captions or retrieval queries.

    This is schema preservation rather than a scoring rule: any non-core textual
    turn field can become auxiliary evidence, while URL-like values remain metadata.
    """

    core_keys = {"speaker", "text", "dia_id", "id"}
    url_values: List[str] = []
    evidence_texts: List[str] = []
    evidence_sources: List[str] = []
    for key, value in dict(turn_payload or {}).items():
        key_text = _safe_text(key)
        normalized_key = key_text.lower().replace("-", "_")
        if normalized_key in core_keys:
            continue
        if normalized_key.endswith("url") or normalized_key.endswith("urls") or "url" in normalized_key:
            url_values.extend(_flatten_auxiliary_url_values(value))
            continue
        flattened_texts = _flatten_auxiliary_text_values(value)
        if not flattened_texts:
            continue
        for text in flattened_texts:
            if not _looks_like_natural_language(text):
                continue
            evidence_texts.append(text)
            evidence_sources.append(key_text)
    return {
        "texts": _dedupe_texts(evidence_texts),
        "sources": _dedupe_texts(evidence_sources),
        "urls": _dedupe_texts(url_values),
    }


def _compose_source_context_text(raw_text: str, auxiliary_evidence_texts: Sequence[Any]) -> str:
    base = _safe_text(raw_text)
    auxiliary_texts = _dedupe_texts(auxiliary_evidence_texts)
    if not auxiliary_texts:
        return base
    pieces = [base] if base else []
    pieces.append("Auxiliary evidence: " + " | ".join(auxiliary_texts))
    return "\n".join(piece for piece in pieces if _safe_text(piece))


def _format_public_date(value: datetime) -> str:
    return f"{int(value.day)} {value.strftime('%B %Y')}"


def _format_public_month_year(*, year: int, month: int) -> str:
    return datetime(year=year, month=month, day=1).strftime("%B %Y")


def _format_public_month_day_without_year(*, month: int, day: int) -> str:
    try:
        month_label = datetime(year=2000, month=month, day=day).strftime("%B")
    except ValueError:
        return ""
    return f"{month_label} {day}"


def _parse_public_timestamp(value: str) -> datetime | None:
    text = _safe_text(value)
    if not text:
        return None
    normalized = re.sub(r"\b(am|pm)\b", lambda match: match.group(1).upper(), text, flags=re.IGNORECASE)
    for fmt in ("%I:%M %p on %d %B, %Y", "%I %p on %d %B, %Y", "%d %B, %Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def _public_iso_from_display_date(value: str) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    for fmt in ("%d %B %Y", "%B %d %Y"):
        try:
            return datetime.strptime(text.replace(",", ""), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _resolve_public_relative_date(text: str, timestamp: str) -> str:
    base_time = _parse_public_timestamp(timestamp)
    if base_time is None:
        return ""
    lowered = _safe_text(text).lower()
    if not lowered:
        return ""
    if "last week" in lowered or "next month" in lowered or "last month" in lowered or "recently" in lowered:
        return ""
    if "yesterday" in lowered or "last night" in lowered:
        return _format_public_date(base_time - timedelta(days=1))
    if (
        "today" in lowered
        or "this morning" in lowered
        or "this afternoon" in lowered
        or "this evening" in lowered
        or "tonight" in lowered
    ):
        return _format_public_date(base_time)
    days_ago = re.search(r"\b(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s+days?\s+ago\b", lowered)
    if days_ago:
        token = days_ago.group(1)
        amount = int(token) if token.isdigit() else _PUBLIC_NUMBER_WORDS.get(token, 0)
        if amount > 0:
            return _format_public_date(base_time - timedelta(days=amount))
    weekday_match = re.search(
        r"\b(last|next)\s+(mon|monday|tue|tues|tuesday|wed|weds|wednesday|thu|thur|thurs|thursday|fri|friday|sat|saturday|sun|sunday)\b",
        lowered,
    )
    if weekday_match:
        direction = weekday_match.group(1)
        weekday = _PUBLIC_WEEKDAY_ALIASES.get(weekday_match.group(2), -1)
        if weekday >= 0:
            current = int(base_time.weekday())
            if direction == "last":
                delta = (current - weekday) % 7 or 7
                return _format_public_date(base_time - timedelta(days=delta))
            delta = (weekday - current) % 7 or 7
            return _format_public_date(base_time + timedelta(days=delta))
    return ""


def _public_lemmatize_token(token: str) -> str:
    lowered = _safe_text(token).lower().strip(string.punctuation)
    if not lowered:
        return ""
    if lowered in {"m", "re", "ve", "ll", "d", "s", "t"}:
        return ""
    if lowered.endswith("ies") and len(lowered) > 4:
        return f"{lowered[:-3]}y"
    if lowered.endswith("ing") and len(lowered) > 5:
        return lowered[:-3]
    if lowered.endswith("ed") and len(lowered) > 4:
        return lowered[:-2]
    if lowered.endswith("s") and len(lowered) > 4 and not lowered.endswith("ss"):
        return lowered[:-1]
    return lowered


def _public_content_tokens(text: str, *, speaker: str = "", keep_time_tokens: bool = False) -> List[str]:
    speaker_tokens = {_public_lemmatize_token(part) for part in re.findall(r"[A-Za-z0-9]+", _safe_text(speaker))}
    tokens: List[str] = []
    for raw in re.findall(r"[A-Za-z0-9\+]+", _safe_text(text)):
        token = _public_lemmatize_token(raw)
        if not token:
            continue
        if token in speaker_tokens:
            continue
        if not keep_time_tokens and token.isdigit():
            continue
        tokens.append(token)
    return _dedupe_texts(tokens)


def _public_target_status(text: str) -> str:
    current_turn = _safe_text(text)
    if not current_turn:
        return ""
    try:
        inferred = infer_controlled_annotation_from_payload({"current_turn": current_turn})
    except Exception:
        return ""
    return _safe_text(dict(inferred or {}).get("target_status", ""))


def _trim_public_span(value: str) -> str:
    text = _safe_text(value).strip(" .,:;!?")
    if not text:
        return ""
    text = re.sub(r"^(?:that|about|for|to|even)\s+", "", text, flags=re.IGNORECASE)
    text = re.split(r"\b(?:and|but|because|while|so)\b", text, maxsplit=1, flags=re.IGNORECASE)[0]
    text = re.sub(
        r"\b(?:today|tomorrow|yesterday|last night|this morning|this afternoon|this evening|tonight|"
        r"\d+\s+days?\s+ago|a\s+few\s+days\s+ago|an?\s+\w+\s+ago)\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b(?:in|on)\s+(?:\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{4}|\d{4})\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .,:;!?")
    return text


def _public_event_candidate_clauses(text: str) -> List[str]:
    raw = _safe_text(text)
    if not raw:
        return []
    segments = re.split(r"(?<=[.!?])\s+|\s+-\s+", raw)
    clauses: List[str] = []
    for segment in segments:
        clean_segment = _safe_text(segment)
        if not clean_segment:
            continue
        subclauses = re.split(
            r";|,\s+(?=(?:i|i'm|i am|i've|i have|we|we're|we are)\b)",
            clean_segment,
            flags=re.IGNORECASE,
        )
        for clause in subclauses:
            clean_clause = _safe_text(clause).strip(" .,:;!?")
            if clean_clause:
                clauses.append(clean_clause)
    return clauses or [raw]


def _score_public_event_candidate(candidate: str) -> tuple[float, int, int]:
    lowered = _safe_text(candidate).lower()
    if not lowered:
        return (-999.0, 0, 0)
    evaluation_text = _trim_public_span(
        re.sub(
            r"^\s*(?:I|I'm|I am|I've been|I have been|I was|I will be|I will|We|We're|We are)\s+",
            "",
            candidate,
            flags=re.IGNORECASE,
        )
    ) or _safe_text(candidate)
    content_count = len(_public_content_tokens(evaluation_text, keep_time_tokens=True))
    token_count = len(re.findall(r"[A-Za-z0-9\+]+", evaluation_text))
    density = float(content_count) / float(max(1, token_count))
    question_penalty = 1.0 if "?" in candidate else 0.0
    score = float(content_count) + density + min(1.5, float(token_count) * 0.08) - question_penalty
    if re.search(r"\b(?:i|we|my|our)\b", lowered):
        score += 1.25
    elif re.search(r"\b(?:you|your|it|this|that)\b", lowered):
        score -= 0.75
    return (score, content_count, token_count)


def _public_event_phrase(text: str) -> str:
    cleaned = _safe_text(text)
    if not cleaned:
        return ""
    candidates = _public_event_candidate_clauses(cleaned)
    _, best_candidate = max(
        enumerate(candidates),
        key=lambda item: (_score_public_event_candidate(item[1]), item[0]),
    )
    cleaned = re.sub(
        r"^\s*(?:I|I'm|I am|I've been|I have been|I was|I will be|I will|We|We're|We are)\s+",
        "",
        best_candidate,
        flags=re.IGNORECASE,
    )
    cleaned = _trim_public_span(cleaned)
    if not cleaned:
        return ""
    parts = cleaned.split()
    return " ".join(parts[:16]).strip(" .,:;!?")


def _public_event_signature(text: str, *, speaker: str = "", semantic_slot: str = "") -> str:
    return compute_public_event_signature(text, speaker=speaker, semantic_slot=semantic_slot)


def _public_profile_fact(text: str, *, speaker: str = "") -> Dict[str, str]:
    if not _safe_text(text):
        return {}
    identity_match = re.search(r"\b(?:i am|i'm)\s+(?:a\s+|an\s+)?(?P<value>[^.!?;]+)", text, flags=re.IGNORECASE)
    if not identity_match:
        return {}
    identity_value = _trim_public_span(identity_match.group("value"))
    if not identity_value:
        return {}
    identity_tokens = identity_value.split()
    if len(identity_tokens) > 6:
        return {}
    if re.match(r"^\w+ing\b", identity_value, flags=re.IGNORECASE):
        return {}
    if re.search(r"\b(?:i|we|you|they|he|she)\b", identity_value, flags=re.IGNORECASE):
        return {}
    return {
        "semantic_slot": "identity",
        "value": identity_value,
        "event_signature": _public_event_signature(identity_value, speaker=speaker, semantic_slot="identity"),
    }


def _public_time_signal(text: str, timestamp: str) -> Dict[str, str]:
    raw_text = _safe_text(text)
    if not raw_text:
        return {}
    lowered = raw_text.lower()
    base_time = _parse_public_timestamp(timestamp)
    life_stage_match = re.search(
        r"\bsince\s+(?:i\s+was\s+(?:in\s+)?)?(?P<stage>high school|college|university|middle school|elementary school)\b",
        raw_text,
        flags=re.IGNORECASE,
    )
    if life_stage_match:
        stage = _safe_text(life_stage_match.group("stage"))
        return {
            "display_time_value": stage,
            "resolved_time_value": "",
            "time_granularity": "relative_day_reference",
            "time_source": "relative_life_stage",
            "resolved_date": "",
        }
    if "last week" in lowered and base_time is not None:
        base_display = _format_public_date(base_time)
        return {
            "display_time_value": f"The week before {base_display}",
            "resolved_time_value": "",
            "time_granularity": "relative_week_reference",
            "time_source": "relative_week_before_anchor",
            "resolved_date": "",
        }
    if "next week" in lowered and base_time is not None:
        base_display = _format_public_date(base_time)
        return {
            "display_time_value": f"The week after {base_display}",
            "resolved_time_value": "",
            "time_granularity": "relative_week_reference",
            "time_source": "relative_week_after_anchor",
            "resolved_date": "",
        }
    weekday_before_match = re.search(
        r"\b(?:the\s+)?(?P<weekday>mon(?:day)?|tue(?:s|sday)?|wed(?:nesday|s)?|thu(?:r|rs|rsday|rsdays)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\s+before\s+(?P<date>\d{1,2}\s+[A-Za-z]+\s+\d{4})\b",
        raw_text,
        flags=re.IGNORECASE,
    )
    if weekday_before_match:
        weekday_name = weekday_before_match.group("weekday")
        target_weekday = _PUBLIC_WEEKDAY_ALIASES.get(weekday_name.lower(), -1)
        explicit_date = _safe_text(weekday_before_match.group("date")).replace(",", "")
        try:
            explicit_dt = datetime.strptime(explicit_date, "%d %B %Y")
        except ValueError:
            explicit_dt = None
        resolved_value = ""
        if explicit_dt is not None and target_weekday >= 0:
            delta = (explicit_dt.weekday() - target_weekday) % 7 or 7
            resolved_value = (explicit_dt - timedelta(days=delta)).strftime("%Y-%m-%d")
        weekday_label = weekday_name[0].upper() + weekday_name[1:].lower()
        return {
            "display_time_value": f"The {weekday_label} before {explicit_date}",
            "resolved_time_value": resolved_value,
            "time_granularity": "relative_day_reference",
            "time_source": "weekday_before_explicit_date",
            "resolved_date": "",
        }
    resolved_date = _resolve_public_relative_date(raw_text, timestamp)
    if resolved_date:
        return {
            "display_time_value": resolved_date,
            "resolved_time_value": _public_iso_from_display_date(resolved_date),
            "time_granularity": "day",
            "time_source": "single_day_relative",
            "resolved_date": resolved_date,
        }
    weekday_with_time_match = re.search(
        r"\b(?P<phrase>(?:next|last|this)\s+(?:mon(?:day)?|tue(?:s|sday)?|wed(?:nesday|s)?|thu(?:r|rs|rsday|rsdays)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)(?:\s*[A-Z]{2,4})?)?)\b",
        raw_text,
        flags=re.IGNORECASE,
    )
    if weekday_with_time_match:
        phrase = _safe_text(weekday_with_time_match.group("phrase"))
        return {
            "display_time_value": phrase,
            "resolved_time_value": "",
            "time_granularity": "relative_day_reference",
            "time_source": "weekday_with_clock_time",
            "resolved_date": "",
        }
    recurring_clock_match = re.search(
        r"\b(?P<phrase>\d{1,2}(?::\d{2})?\s*(?:am|pm)(?:\s+on\s+weekdays)?)\b",
        raw_text,
        flags=re.IGNORECASE,
    )
    if recurring_clock_match:
        phrase = _safe_text(recurring_clock_match.group("phrase"))
        normalized_phrase = re.sub(r"\b(am|pm)\b", lambda match: match.group(1).upper(), phrase, flags=re.IGNORECASE)
        return {
            "display_time_value": normalized_phrase,
            "resolved_time_value": "",
            "time_granularity": "relative_day_reference",
            "time_source": "clock_time_unresolved",
            "resolved_date": "",
        }
    explicit_date_match = re.search(
        r"\b(?P<date>\d{1,2}\s+[A-Za-z]+\s+\d{4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4})\b",
        raw_text,
        flags=re.IGNORECASE,
    )
    if explicit_date_match:
        display_value = _safe_text(explicit_date_match.group("date")).replace(",", "")
        normalized = _public_iso_from_display_date(display_value)
        if normalized:
            normalized_dt = datetime.strptime(normalized, "%Y-%m-%d")
            display_value = _format_public_date(normalized_dt)
        return {
            "display_time_value": display_value,
            "resolved_time_value": normalized,
            "time_granularity": "day",
            "time_source": "explicit_day",
            "resolved_date": display_value,
        }
    explicit_month_day_match = re.search(
        r"\b(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\b",
        raw_text,
        flags=re.IGNORECASE,
    )
    if explicit_month_day_match:
        month_name = _safe_text(explicit_month_day_match.group("month"))
        month_num = _PUBLIC_MONTH_ALIASES.get(month_name.lower(), 0)
        day_value = int(explicit_month_day_match.group("day"))
        if month_num and 1 <= day_value <= 31:
            display_value = _format_public_month_day_without_year(month=month_num, day=day_value)
            if display_value:
                return {
                    "display_time_value": display_value,
                    "resolved_time_value": f"--{month_num:02d}-{day_value:02d}",
                    "time_granularity": "day",
                    "time_source": "explicit_day_without_year",
                    "resolved_date": "",
                }
    month_match = re.search(r"\b(?P<month>[A-Za-z]+),?\s+(?P<year>\d{4})\b", raw_text)
    if month_match:
        month_num = _PUBLIC_MONTH_ALIASES.get(month_match.group("month").lower(), 0)
        year_num = int(month_match.group("year"))
        if month_num:
            return {
                "display_time_value": _format_public_month_year(year=year_num, month=month_num),
                "resolved_time_value": f"{year_num:04d}-{month_num:02d}",
                "time_granularity": "month",
                "time_source": "month_year",
                "resolved_date": "",
            }
    relative_year_match = re.search(r"\b(?P<count>\d+)\s+years?\s+ago\b", raw_text, flags=re.IGNORECASE)
    if relative_year_match and base_time is not None:
        year_value = base_time.year - int(relative_year_match.group("count"))
        return {
            "display_time_value": str(year_value),
            "resolved_time_value": str(year_value),
            "time_granularity": "year",
            "time_source": "relative_year_offset",
            "resolved_date": "",
        }
    relative_month_match = re.search(r"\b(?P<count>\d+)\s+months?\s+ago\b", raw_text, flags=re.IGNORECASE)
    if relative_month_match and base_time is not None:
        month_offset = int(relative_month_match.group("count"))
        total_month_index = (base_time.year * 12 + (base_time.month - 1)) - month_offset
        year_value = total_month_index // 12
        month_value = total_month_index % 12 + 1
        return {
            "display_time_value": _format_public_month_year(year=year_value, month=month_value),
            "resolved_time_value": f"{year_value:04d}-{month_value:02d}",
            "time_granularity": "month",
            "time_source": "relative_month_offset",
            "resolved_date": "",
        }
    if any(marker in lowered for marker in ("last week", "next month", "last month", "recently")):
        return {}
    year_match = re.search(r"\b(?P<year>(?:19|20)\d{2})\b", raw_text)
    if year_match:
        year_value = year_match.group("year")
        return {
            "display_time_value": year_value,
            "resolved_time_value": year_value,
            "time_granularity": "year",
            "time_source": "year_only",
            "resolved_date": "",
        }
    return {}


def _profile_fact_from_annotation(
    annotation: Mapping[str, Any],
    *,
    speaker: str = "",
    fallback_text: str = "",
    selected_sentence: str = "",
) -> Dict[str, str]:
    semantic_slot = _safe_text(annotation.get("profile_type", "")) or _safe_text(annotation.get("semantic_slot", ""))
    if semantic_slot not in {"identity", "research_topic", "education", "occupation"}:
        return {}
    value = _safe_text(annotation.get("event_phrase", "")) or _safe_text(selected_sentence) or _safe_text(fallback_text)
    value = _trim_public_span(value)
    if semantic_slot == "identity":
        value = re.sub(r"^(?:i am|i'm|i identify as)\s+", "", value, flags=re.IGNORECASE)
        value = re.sub(r"^(?:a|an)\s+", "", value, flags=re.IGNORECASE)
        value = _trim_public_span(value)
    if not value or value.lower() == "greeting":
        return {}
    return {
        "semantic_slot": semantic_slot,
        "value": value,
        "event_signature": _public_event_signature(value, speaker=speaker, semantic_slot=semantic_slot),
    }


def _time_signal_from_annotation(
    annotation: Mapping[str, Any],
    *,
    text: str,
    timestamp: str,
) -> Dict[str, str]:
    annotation_granularity = _safe_text(annotation.get("time_granularity", ""))
    normalized = _public_time_signal(text, timestamp)
    if annotation_granularity in {"", "none"}:
        if normalized:
            fallback = dict(normalized)
            fallback["time_source"] = "rule_time_fallback_after_learned_none"
            return fallback
        return {}
    if normalized:
        normalized_granularity = _safe_text(normalized.get("time_granularity", ""))
        if normalized_granularity and normalized_granularity != annotation_granularity:
            normalized["time_granularity"] = annotation_granularity
        return normalized
    time_expression_span = _safe_text(annotation.get("time_expression_span", ""))
    if not time_expression_span:
        return {}
    return {
        "display_time_value": time_expression_span,
        "resolved_time_value": "",
        "time_granularity": annotation_granularity,
        "time_source": "learned_turn_extractor_unresolved",
        "resolved_date": "",
    }


def _select_profile_fact(
    annotation: Mapping[str, Any],
    *,
    speaker: str,
    compact_text: str,
    selected_sentence: str = "",
) -> tuple[Dict[str, str], str]:
    learned_profile_fact = _profile_fact_from_annotation(
        annotation,
        speaker=speaker,
        fallback_text=compact_text,
        selected_sentence=selected_sentence,
    )
    if learned_profile_fact:
        return learned_profile_fact, "learned_annotation"
    rule_profile_fact = _public_profile_fact(compact_text, speaker=speaker)
    if rule_profile_fact:
        return rule_profile_fact, "rule_profile_fallback_after_learned_miss"
    return {}, ""


def _select_event_phrase(
    annotation: Mapping[str, Any],
    *,
    compact_text: str,
    annotation_metadata: Mapping[str, Any],
) -> tuple[str, str]:
    annotation_event_phrase = _trim_public_span(_safe_text(annotation.get("event_phrase", "")))
    selected_sentence = _trim_public_span(_safe_text(annotation_metadata.get("selected_sentence", "")))
    rule_event_phrase = _public_event_phrase(compact_text)
    sentence_confidence = float(annotation_metadata.get("sentence_confidence", 0.0) or 0.0)
    annotation_phrase_source = _safe_text(annotation_metadata.get("event_phrase_source", ""))
    if annotation_event_phrase:
        if annotation_phrase_source in _LEARNED_SPAN_EVENT_PHRASE_SOURCES:
            return annotation_event_phrase, annotation_phrase_source
        if sentence_confidence and sentence_confidence < _LEARNED_EVENT_SENTENCE_CONFIDENCE_FLOOR and rule_event_phrase:
            return rule_event_phrase, "rule_event_fallback_low_sentence_confidence"
        return annotation_event_phrase, "learned_annotation"
    if selected_sentence:
        if sentence_confidence and sentence_confidence < _LEARNED_EVENT_SENTENCE_CONFIDENCE_FLOOR and rule_event_phrase:
            return rule_event_phrase, "rule_event_fallback_low_sentence_confidence"
        return selected_sentence, "learned_selected_sentence"
    if rule_event_phrase:
        return rule_event_phrase, "rule_event_phrase"
    return compact_text, "raw_text"


def build_public_turn_payload(
    *,
    text: str,
    raw_text: str = "",
    speaker: str,
    session_key: str,
    turn_index: int,
    timestamp: str = "",
    dia_id: str = "",
    extraction_annotation: Mapping[str, Any] | None = None,
    extraction_metadata: Mapping[str, Any] | None = None,
    auxiliary_evidence_texts: Sequence[Any] | None = None,
    auxiliary_evidence_metadata: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    rendered_text = _safe_text(text)
    original_compact_text = _safe_text(raw_text) or rendered_text
    auxiliary_texts = _dedupe_texts(auxiliary_evidence_texts or [])
    auxiliary_metadata = dict(auxiliary_evidence_metadata or {})
    source_context_text = _compose_source_context_text(original_compact_text, auxiliary_texts)
    compact_text = original_compact_text
    annotation = dict(extraction_annotation or {})
    annotation_metadata = dict(extraction_metadata or {})
    learned_turn_kind = _safe_text(annotation_metadata.get("turn_kind", ""))
    selected_sentence = _safe_text(annotation_metadata.get("selected_sentence", ""))
    event_id = f"event::{_safe_text(dia_id)}" if _safe_text(dia_id) else ""
    if annotation:
        time_signal = _time_signal_from_annotation(annotation, text=compact_text, timestamp=timestamp)
    else:
        time_signal = _public_time_signal(compact_text, timestamp)
    resolved_date = _safe_text(time_signal.get("resolved_date", ""))
    profile_fact, profile_source = _select_profile_fact(
        annotation,
        speaker=speaker,
        compact_text=compact_text,
        selected_sentence=selected_sentence,
    )
    base_semantic_slot = (
        _safe_text(profile_fact.get("semantic_slot", ""))
        or _safe_text(annotation.get("profile_type", ""))
        or _safe_text(annotation.get("semantic_slot", ""))
        or "event"
    )
    event_phrase, event_phrase_source = _select_event_phrase(
        annotation,
        compact_text=compact_text,
        annotation_metadata=annotation_metadata,
    )
    if event_phrase.lower() == "greeting":
        event_phrase = ""
    target_status = _safe_text(annotation.get("target_status", "")) or _public_target_status(compact_text)
    signature_input = _compose_source_context_text(event_phrase or compact_text, auxiliary_texts)
    event_signature = _public_event_signature(
        signature_input,
        speaker=speaker,
        semantic_slot=base_semantic_slot,
    )
    anchors = [item for item in (speaker, timestamp) if _safe_text(item)]
    slot_key = f"{_slug(session_key)}.turn_{max(1, int(turn_index))}"
    metadata = {
        "speaker": _safe_text(speaker),
        "timestamp": _safe_text(timestamp),
        "session_key": _safe_text(session_key),
        "dia_id": _safe_text(dia_id),
        "event_id": event_id,
        "source": "public_benchmark",
        "raw_text": compact_text,
        "source_turn_text": source_context_text,
        "auxiliary_evidence_text": " | ".join(auxiliary_texts),
        "auxiliary_evidence_texts": list(auxiliary_texts),
        "auxiliary_evidence_sources": list(auxiliary_metadata.get("sources", []) or []),
        "auxiliary_evidence_urls": list(auxiliary_metadata.get("urls", []) or []),
        "auxiliary_evidence_present": bool(auxiliary_texts),
        "event_signature": event_signature,
        "event_text": event_phrase,
        "semantic_slot": base_semantic_slot,
        "target_status": target_status,
        "event_phrase": event_phrase,
        "time_expression_span": _safe_text(annotation.get("time_expression_span", "")),
        "time_granularity": _safe_text(time_signal.get("time_granularity", "")),
        "resolved_time_value": _safe_text(time_signal.get("resolved_time_value", "")),
        "time_display_value": _safe_text(time_signal.get("display_time_value", "")),
        "resolved_date": resolved_date,
        "turn_kind": learned_turn_kind,
        "turn_extractor_applied": bool(annotation_metadata),
        "turn_extractor_confidence": float(annotation_metadata.get("turn_kind_confidence", 0.0) or 0.0),
        "turn_extractor_sentence_confidence": float(annotation_metadata.get("sentence_confidence", 0.0) or 0.0),
        "event_phrase_source": event_phrase_source,
        "profile_source": profile_source,
        "time_source": _safe_text(time_signal.get("time_source", "")),
    }
    replacement_memory_records: List[Dict[str, Any]] = [
        {
            "category": "fact",
            "slot_key": slot_key,
            "value": rendered_text,
            "anchors": anchors,
            "relation": "conversation_fact",
            "source_kind": "public_dialog_turn",
            "metadata": dict(metadata),
        }
    ]
    if compact_text and compact_text != rendered_text:
        replacement_memory_records.append(
            {
                "category": "fact",
                "slot_key": f"{slot_key}.compact",
                "value": compact_text,
                "anchors": anchors,
                "relation": "conversation_fact",
                "source_kind": "public_dialog_text",
                "salience": 0.92,
                "confidence": 0.86,
                "metadata": {**metadata, "content_variant": "compact"},
            }
        )
    if auxiliary_texts:
        auxiliary_context = " | ".join(auxiliary_texts)
        replacement_memory_records.append(
            {
                "category": "fact",
                "slot_key": f"{slot_key}.auxiliary_evidence",
                "value": auxiliary_context,
                "anchors": _dedupe_texts([speaker, *anchors, *event_signature.split()[:4]]),
                "relation": "auxiliary_evidence_context",
                "source_kind": "public_dialog_auxiliary_evidence",
                "salience": 0.96,
                "confidence": 0.9,
                "metadata": {
                    **metadata,
                    "content_variant": "auxiliary_evidence",
                    "raw_text": compact_text,
                    "source_turn_text": source_context_text,
                    "auxiliary_evidence_text": auxiliary_context,
                    "event_signature": event_signature,
                },
            }
        )
    structured_write_enabled = learned_turn_kind not in {"other", "greeting"}
    if structured_write_enabled and event_signature:
        replacement_memory_records.append(
            {
                "category": "event",
                "slot_key": f"{slot_key}.event",
                "value": f"{_safe_text(speaker)} {event_phrase}".strip(),
                "anchors": _dedupe_texts([speaker, *anchors, *event_signature.split()[:4]]),
                "relation": "event_fact",
                "source_kind": "public_dialog_event",
                "salience": 0.95,
                "confidence": 0.91,
                "metadata": {
                    **metadata,
                    "content_variant": "event_fact",
                    "semantic_slot": "event",
                    "event_signature": event_signature,
                },
            }
        )
    if structured_write_enabled and profile_fact:
        profile_slot = _safe_text(profile_fact.get("semantic_slot", "")) or "profile"
        profile_value = _safe_text(profile_fact.get("value", ""))
        replacement_memory_records.append(
            {
                "category": "profile",
                "slot_key": f"{slot_key}.profile.{_slug(profile_slot)}",
                "value": profile_value,
                "anchors": _dedupe_texts([speaker, profile_slot, *anchors]),
                "relation": f"{profile_slot}_fact",
                "source_kind": "public_dialog_profile",
                "salience": 0.97,
                "confidence": 0.94,
                "metadata": {
                    **metadata,
                    "content_variant": "profile_fact",
                    "semantic_slot": profile_slot,
                    "event_signature": _safe_text(profile_fact.get("event_signature", "")) or event_signature,
                    "target_status": target_status or "current",
                },
            }
        )
    if structured_write_enabled and time_signal and event_phrase:
        time_display_value = _safe_text(time_signal.get("display_time_value", ""))
        time_granularity = _safe_text(time_signal.get("time_granularity", ""))
        time_relation = "event_date" if time_granularity in {"day", "relative_day_reference"} else "event_time"
        time_slot_suffix = "resolved_date" if time_granularity == "day" else "time"
        replacement_memory_records.append(
            {
                "category": "fact",
                "slot_key": f"{slot_key}.{time_slot_suffix}",
                "value": f"{time_display_value}: {(_safe_text(speaker) + ' ' + event_phrase).strip()}",
                "anchors": _dedupe_texts([speaker, time_display_value, timestamp, *event_signature.split()[:4]]),
                "relation": time_relation,
                "source_kind": "public_dialog_time",
                "salience": 0.98,
                "confidence": 0.96,
                "metadata": {
                    **metadata,
                    "content_variant": "resolved_date" if time_granularity == "day" else "time_fact",
                    "semantic_slot": "event_time",
                    "resolved_date": resolved_date,
                    "event_signature": event_signature,
                    **time_signal,
                },
            }
        )
    return {
        "metadata": {"memory_write": True, "source": "public_benchmark"},
        "replacement_memory_records": replacement_memory_records,
    }


def _public_turn_payload(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return build_public_turn_payload(*args, **kwargs)


__all__ = [
    "_format_public_date",
    "_parse_public_timestamp",
    "_public_iso_from_display_date",
    "_public_turn_payload",
    "build_public_turn_payload",
    "collect_public_auxiliary_evidence",
]
