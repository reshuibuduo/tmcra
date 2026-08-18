from __future__ import annotations

import argparse
import ast
import json
import os
import re
import socket
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.replacement.node_memory_runtime_utils import (
    answer_type_from_query,
    build_default_path_templates,
    clean_text,
    dedupe_texts,
    extract_question_features,
    json_dumps,
    normalize_text,
    tokenize_text,
    write_json,
    write_jsonl,
)
from experiments.replacement.public_event_signature import compute_public_event_signature
from experiments.replacement.teacher_audit import (
    build_teacher_audit_candidate,
    evaluate_teacher_annotation_consistency,
)
from scripts.controlled_teacher_extraction import (
    backfill_controlled_annotation,
    controlled_candidate_score,
    infer_controlled_annotation_from_payload,
)
TIME_GRANULARITY_VALUES = {"day", "month", "year", "relative_day_reference", "none", ""}
PROFILE_TYPE_VALUES = {"identity", "research_topic", "education", "occupation", ""}
STATUS_VALUES = {"past", "current", "planned", ""}
ERROR_CODES = {
    "teacher_not_configured",
    "teacher_unhealthy",
    "teacher_timeout",
    "teacher_invalid_json",
    "teacher_missing_fields",
    "teacher_semantic_mismatch",
    "teacher_runtime_exception",
}
SYNTHETIC_QUERY_KINDS = ("time", "profile", "event_text", "multi_evidence")
TEACHER_REQUIRED_FIELDS = (
    "event_phrase",
    "semantic_slot",
    "target_status",
    "time_expression_span",
    "time_granularity",
    "profile_type",
)
TEACHER_ENUM_FIELDS = {
    "target_status": STATUS_VALUES,
    "time_granularity": TIME_GRANULARITY_VALUES,
    "profile_type": PROFILE_TYPE_VALUES,
}
DEFAULT_TEACHER_TIMEOUT_SECONDS = 60.0
DEFAULT_TEACHER_MAX_TOKENS = 128
DEFAULT_TEACHER_REPAIR_MAX_TOKENS = 128
DEFAULT_TEACHER_BASE_URL = "http://127.0.0.1:18020/v1"
DEFAULT_TEACHER_MODEL = "Qwen/Qwen3.5-4B"
DEFAULT_TEACHER_API_KEY_ENV_CANDIDATES = (
    "TEACHER_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "ARK_API_KEY",
    "VOLCENGINE_API_KEY",
)
AUDIT_POLICY_STRICT_PAUSE = "strict_pause"
AUDIT_POLICY_REPAIR_OR_QUEUE_NONBLOCKING = "repair_or_queue_nonblocking"
AUDIT_POLICY_VALUES = {
    AUDIT_POLICY_STRICT_PAUSE,
    AUDIT_POLICY_REPAIR_OR_QUEUE_NONBLOCKING,
}
_AUDIT_REPAIR_SOURCE_PRIORITY = {
    "teacher_drop_time": 8,
    "teacher_clear_status": 8,
    "teacher_clear_profile": 8,
    "teacher_backfilled": 7,
    "teacher_force_current": 6,
    "teacher_with_heuristic_meta": 5,
    "heuristic": 4,
    "teacher_raw": 1,
}


def _log(event: str, **payload: Any) -> None:
    details = " ".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in sorted(payload.items()))
    stamp = datetime.now().isoformat(timespec="seconds")
    print(f"[export_locomo_node_training_data] {stamp} {event}" + (f" {details}" if details else ""), flush=True)


def _format_public_date(*args: Any, **kwargs: Any) -> Any:
    from scripts.run_public_benchmark_first_batch import _format_public_date as impl

    return impl(*args, **kwargs)


def _locomo_evidence_ids(*args: Any, **kwargs: Any) -> Any:
    from scripts.run_public_benchmark_first_batch import _locomo_evidence_ids as impl

    return impl(*args, **kwargs)


def _locomo_rows(*args: Any, **kwargs: Any) -> Any:
    from scripts.run_public_benchmark_first_batch import _locomo_rows as impl

    return impl(*args, **kwargs)


def _locomo_session_names(*args: Any, **kwargs: Any) -> Any:
    from scripts.run_public_benchmark_first_batch import _locomo_session_names as impl

    return impl(*args, **kwargs)


def _parse_public_timestamp(*args: Any, **kwargs: Any) -> Any:
    from scripts.run_public_benchmark_first_batch import _parse_public_timestamp as impl

    return impl(*args, **kwargs)


def _public_turn_payload(*args: Any, **kwargs: Any) -> Any:
    from scripts.run_public_benchmark_first_batch import _public_turn_payload as impl

    return impl(*args, **kwargs)


def _public_iso_from_display_date(*args: Any, **kwargs: Any) -> Any:
    from scripts.run_public_benchmark_first_batch import _public_iso_from_display_date as impl

    return impl(*args, **kwargs)


def _resolve_locomo_paths(dataset_root: Path, *, dataset_glob: str = "locomo*.json") -> List[Path]:
    if dataset_root.is_file():
        return [dataset_root]
    candidates: List[Path] = []
    search_roots = (
        dataset_root,
        dataset_root / "dataset",
        dataset_root / "source_repo" / "data",
    )
    for root in search_roots:
        if not root.exists():
            continue
        candidates.extend(sorted(path for path in root.glob(dataset_glob) if path.is_file()))
    if candidates:
        deduped: List[Path] = []
        seen = set()
        for candidate in candidates:
            key = str(candidate.resolve())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped
    legacy_candidates = (
        dataset_root / "source_repo" / "data" / "locomo10.json",
        dataset_root / "dataset" / "locomo10.json",
        dataset_root / "locomo10.json",
    )
    for candidate in legacy_candidates:
        if candidate.exists():
            return [candidate]
    raise FileNotFoundError(f"LoCoMo json not found under {dataset_root}")


def _coerce_mapping_payload(value: Any) -> Dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], Mapping):
        return dict(value[0])
    return None


def _teacher_placeholder_like(value: Any) -> bool:
    clean = clean_text(value)
    lowered = clean.lower()
    if not clean:
        return True
    return (
        clean.startswith("...")
        or lowered.startswith("thinking process")
        or lowered.startswith("analyze the request")
        or lowered in {"placeholder", "<empty>", "empty", "null", "n/a", "na"}
    )


def _normalize_teacher_candidate(parsed: Mapping[str, Any]) -> Dict[str, str]:
    normalized: Dict[str, str] = {}
    payload = dict(parsed)
    for field in TEACHER_REQUIRED_FIELDS:
        value = clean_text(payload.get(field, ""))
        if _teacher_placeholder_like(value):
            value = ""
        if field in TEACHER_ENUM_FIELDS and value not in TEACHER_ENUM_FIELDS[field]:
            value = ""
        normalized[field] = value
    return normalized


def _teacher_candidate_score(parsed: Mapping[str, Any]) -> tuple[int, int, int]:
    normalized = _normalize_teacher_candidate(parsed)
    filled = sum(1 for value in normalized.values() if value)
    enum_ok = sum(
        1
        for field, allowed in TEACHER_ENUM_FIELDS.items()
        if normalized.get(field, "") in allowed
    )
    bonus = 0
    if normalized.get("event_phrase"):
        bonus += 1
    if normalized.get("semantic_slot"):
        bonus += 1
    return (filled, enum_ok, bonus)


def _extract_fenced_blocks(text: str) -> List[str]:
    clean = clean_text(text)
    if not clean:
        return []
    return [
        clean_text(match.group(1))
        for match in re.finditer(r"```(?:json)?\s*(.*?)```", clean, flags=re.IGNORECASE | re.DOTALL)
        if clean_text(match.group(1))
    ]


def _extract_braced_objects(text: str) -> List[str]:
    clean = clean_text(text)
    if not clean:
        return []
    candidates: List[str] = []
    length = len(clean)
    for start in range(length):
        if clean[start] != "{":
            continue
        depth = 0
        in_string = False
        escape = False
        for end in range(start, length):
            char = clean[end]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = clean_text(clean[start : end + 1])
                    if candidate:
                        candidates.append(candidate)
                    break
    return candidates


def _extract_key_value_annotation(text: str) -> Dict[str, Any] | None:
    clean = clean_text(text)
    if not clean:
        return None
    parsed: Dict[str, Any] = {}
    field_set = {field.lower() for field in TEACHER_REQUIRED_FIELDS}
    key_pattern = "|".join(re.escape(field) for field in TEACHER_REQUIRED_FIELDS)
    for match in re.finditer(
        rf'["\']?(?P<key>{key_pattern})["\']?\s*[:=]\s*(?P<value>.*?)(?=(?:["\']?(?:{key_pattern})["\']?\s*[:=])|$)',
        clean,
        flags=re.IGNORECASE,
    ):
        key = clean_text(match.group("key")).lower()
        if key not in field_set:
            continue
        value = clean_text(match.group("value")).strip(" ,\"'")
        parsed[key] = value
    return parsed or None


def _parse_teacher_annotation(text: str) -> Dict[str, Any] | None:
    clean = clean_text(text)
    if not clean:
        return None
    candidates = [clean]
    candidates.extend(_extract_fenced_blocks(clean))
    candidates.extend(_extract_braced_objects(clean))
    best_payload: Dict[str, Any] | None = None
    best_score = (-1, -1, -1)
    seen = set()
    for candidate in candidates:
        normalized = clean_text(candidate)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        parsed_candidates: List[Dict[str, Any]] = []
        try:
            parsed = _coerce_mapping_payload(json.loads(normalized))
        except Exception:
            parsed = None
        if parsed is not None:
            parsed_candidates.append(parsed)
        try:
            parsed = _coerce_mapping_payload(ast.literal_eval(normalized))
        except Exception:
            parsed = None
        if parsed is not None:
            parsed_candidates.append(parsed)
        for parsed_candidate in parsed_candidates:
            score = _teacher_candidate_score(parsed_candidate)
            if score > best_score:
                best_score = score
                best_payload = dict(parsed_candidate)
    kv_candidate = _extract_key_value_annotation(clean)
    if kv_candidate is not None:
        score = _teacher_candidate_score(kv_candidate)
        if score > best_score:
            best_score = score
            best_payload = dict(kv_candidate)
    return best_payload


def _stable_conversation_id(sample: Mapping[str, Any], index: int, *, dataset_tag: str = "") -> str:
    for key in ("sample_id", "conversation_id", "id"):
        value = clean_text(sample.get(key, ""))
        if value:
            return f"{dataset_tag}__{value}" if dataset_tag else value
    default_id = f"conversation_{index:04d}"
    return f"{dataset_tag}__{default_id}" if dataset_tag else default_id


def _conversation_split_map(conversation_ids: Sequence[str]) -> Dict[str, str]:
    ordered = sorted({clean_text(item) for item in conversation_ids if clean_text(item)})
    total = len(ordered)
    train_cutoff = max(1, int(total * 0.8))
    val_cutoff = max(train_cutoff + 1, int(total * 0.9)) if total >= 3 else total
    result: Dict[str, str] = {}
    for index, conversation_id in enumerate(ordered):
        split = "train"
        if index >= val_cutoff:
            split = "test"
        elif index >= train_cutoff:
            split = "val"
        result[conversation_id] = split
    return result


def _normalize_shard_params(*, shard_index: int, shard_count: int) -> tuple[int, int]:
    normalized_count = int(shard_count or 1)
    normalized_index = int(shard_index or 0)
    if normalized_count <= 0:
        raise ValueError("shard_count must be >= 1")
    if normalized_index < 0 or normalized_index >= normalized_count:
        raise ValueError(f"shard_index must be in [0, {normalized_count - 1}]")
    return normalized_index, normalized_count


def _select_shard_entries(
    rows: Sequence[Mapping[str, Any]],
    *,
    shard_index: int,
    shard_count: int,
) -> List[tuple[int, Dict[str, Any]]]:
    normalized_index, normalized_count = _normalize_shard_params(
        shard_index=shard_index,
        shard_count=shard_count,
    )
    selected: List[tuple[int, Dict[str, Any]]] = []
    for row_index, row in enumerate(rows, start=1):
        if normalized_count > 1 and (row_index - 1) % normalized_count != normalized_index:
            continue
        selected.append((row_index, dict(row)))
    return selected


def _normalized_timeout(timeout_seconds: float | None) -> float | None:
    if timeout_seconds is None:
        return DEFAULT_TEACHER_TIMEOUT_SECONDS
    try:
        value = float(timeout_seconds)
    except Exception:
        return DEFAULT_TEACHER_TIMEOUT_SECONDS
    return DEFAULT_TEACHER_TIMEOUT_SECONDS if value <= 0 else value


def _clean_header_map(payload: Mapping[str, Any] | None) -> Dict[str, str]:
    source = dict(payload or {})
    normalized: Dict[str, str] = {}
    for key, value in source.items():
        name = clean_text(key)
        text = clean_text(value)
        if not name or not text:
            continue
        normalized[name] = text
    return normalized


def _resolve_teacher_api_key(
    *,
    explicit_api_key: str | None,
    api_key_env: str | None = None,
    env_candidates: Sequence[str] = DEFAULT_TEACHER_API_KEY_ENV_CANDIDATES,
) -> tuple[str, str]:
    direct = clean_text(explicit_api_key)
    if direct:
        return direct, "explicit"
    requested_env = clean_text(api_key_env)
    if requested_env:
        return clean_text(os.environ.get(requested_env, "")), requested_env
    for env_name in env_candidates:
        value = clean_text(os.environ.get(env_name, ""))
        if value:
            return value, env_name
    return "", ""


def _json_request(
    url: str,
    payload: Mapping[str, Any],
    *,
    timeout_seconds: float | None,
    headers: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(dict(payload)).encode("utf-8"),
        headers={"Content-Type": "application/json", **_clean_header_map(headers)},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_normalized_timeout(timeout_seconds)) as response:
        return dict(json.loads(response.read().decode("utf-8")))


def _json_get(url: str, *, timeout_seconds: float | None, headers: Mapping[str, str] | None = None) -> Dict[str, Any]:
    request = urllib.request.Request(url, headers=_clean_header_map(headers), method="GET")
    with urllib.request.urlopen(request, timeout=_normalized_timeout(timeout_seconds)) as response:
        return dict(json.loads(response.read().decode("utf-8")))


def _normalized_teacher_fields(payload: Mapping[str, Any] | None) -> Dict[str, str]:
    source = dict(payload or {})
    return {field: clean_text(source.get(field, "")) for field in TEACHER_REQUIRED_FIELDS}


def _teacher_request_messages(
    *,
    current_turn: str,
    previous_turn: str,
    next_turn: str,
    session_timestamp: str,
) -> List[Dict[str, str]]:
    return [
        {
            "role": "user",
            "content": json_dumps(
                {
                    "current_turn": clean_text(current_turn),
                    "previous_turn": clean_text(previous_turn),
                    "next_turn": clean_text(next_turn),
                    "session_timestamp": clean_text(session_timestamp),
                }
            ),
        }
    ]


def _score_audit_repair_candidate(
    *,
    source: str,
    teacher_fields: Mapping[str, Any],
    semantic_consistency: Mapping[str, Any],
) -> tuple[int, int, int, int, int, int, int, int]:
    normalized = _normalized_teacher_fields(teacher_fields)
    structure = controlled_candidate_score(normalized)
    event_token_count = len(tokenize_text(normalized.get("event_phrase", "")))
    return (
        1 if bool(dict(semantic_consistency).get("passed", False)) else 0,
        int(_AUDIT_REPAIR_SOURCE_PRIORITY.get(clean_text(source), 0)),
        int(structure[0]),
        int(structure[1]),
        int(structure[2]),
        1 if clean_text(normalized.get("time_expression_span", "")) else 0,
        1 if clean_text(normalized.get("target_status", "")) else 0,
        int(event_token_count),
    )


def _repair_semantic_mismatch(
    *,
    current_turn: str,
    previous_turn: str,
    next_turn: str,
    session_timestamp: str,
    teacher_fields: Mapping[str, Any],
) -> Dict[str, Any]:
    payload = {
        "current_turn": clean_text(current_turn),
        "previous_turn": clean_text(previous_turn),
        "next_turn": clean_text(next_turn),
        "session_timestamp": clean_text(session_timestamp),
    }
    messages = _teacher_request_messages(
        current_turn=current_turn,
        previous_turn=previous_turn,
        next_turn=next_turn,
        session_timestamp=session_timestamp,
    )
    original = _normalized_teacher_fields(teacher_fields)
    heuristic = _normalized_teacher_fields(infer_controlled_annotation_from_payload(payload))
    candidates: List[Dict[str, Any]] = []
    seen_signatures = set()

    def add_candidate(source: str, fields: Mapping[str, Any]) -> None:
        normalized = _normalized_teacher_fields(fields)
        signature = tuple(normalized[field] for field in TEACHER_REQUIRED_FIELDS)
        if signature in seen_signatures:
            return
        seen_signatures.add(signature)
        semantic_consistency = evaluate_teacher_annotation_consistency(
            current_turn=current_turn,
            teacher_fields=normalized,
        )
        candidates.append(
            {
                "source": clean_text(source),
                "teacher_fields": normalized,
                "semantic_consistency": semantic_consistency,
            }
        )

    add_candidate("teacher_raw", original)
    add_candidate("teacher_backfilled", backfill_controlled_annotation(messages, original))
    add_candidate("heuristic", heuristic)
    if any(clean_text(original.get(field, "")) for field in ("time_expression_span", "time_granularity")):
        candidate = dict(original)
        candidate["time_expression_span"] = ""
        candidate["time_granularity"] = "none"
        add_candidate("teacher_drop_time", candidate)
    if clean_text(original.get("target_status", "")):
        candidate = dict(original)
        candidate["target_status"] = ""
        add_candidate("teacher_clear_status", candidate)
        candidate = dict(original)
        candidate["target_status"] = "current"
        add_candidate("teacher_force_current", candidate)
    if clean_text(original.get("profile_type", "")):
        candidate = dict(original)
        candidate["profile_type"] = ""
        add_candidate("teacher_clear_profile", candidate)
    candidate = dict(original)
    for field in ("target_status", "time_expression_span", "time_granularity", "profile_type"):
        candidate[field] = clean_text(heuristic.get(field, ""))
    if not clean_text(candidate.get("semantic_slot", "")):
        candidate["semantic_slot"] = clean_text(heuristic.get("semantic_slot", "")) or "event"
    add_candidate("teacher_with_heuristic_meta", candidate)

    best = max(
        candidates,
        key=lambda item: _score_audit_repair_candidate(
            source=str(item.get("source", "")),
            teacher_fields=dict(item.get("teacher_fields", {}) or {}),
            semantic_consistency=dict(item.get("semantic_consistency", {}) or {}),
        ),
    )
    return {
        "selected_source": clean_text(best.get("source", "")),
        "teacher_fields": _normalized_teacher_fields(dict(best.get("teacher_fields", {}) or {})),
        "semantic_consistency": dict(best.get("semantic_consistency", {}) or {}),
        "candidates": candidates,
    }


@dataclass(slots=True)
class TeacherTurnError(RuntimeError):
    error_code: str
    error_stage: str
    teacher_request_excerpt: str
    teacher_raw_output: str = ""
    teacher_repair_output: str = ""
    session_name: str = ""
    turn_index: int = 0
    dia_id: str = ""
    audit_rows: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, f"{self.error_code}: {self.error_stage}")


class TeacherClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        api_key_env: str = "",
        timeout_seconds: float = DEFAULT_TEACHER_TIMEOUT_SECONDS,
        enable_thinking: bool = False,
        annotation_max_tokens: int = DEFAULT_TEACHER_MAX_TOKENS,
        repair_max_tokens: int = DEFAULT_TEACHER_REPAIR_MAX_TOKENS,
        extra_headers: Mapping[str, Any] | None = None,
    ) -> None:
        self.base_url = clean_text(base_url).rstrip("/")
        self.model = clean_text(model)
        self.api_key, self.api_key_source = _resolve_teacher_api_key(
            explicit_api_key=api_key,
            api_key_env=api_key_env,
        )
        self.auth_mode = "bearer" if self.api_key else "none"
        self.timeout_seconds = _normalized_timeout(timeout_seconds)
        self.enable_thinking = bool(enable_thinking)
        self.annotation_max_tokens = max(32, int(annotation_max_tokens or DEFAULT_TEACHER_MAX_TOKENS))
        self.repair_max_tokens = max(32, int(repair_max_tokens or DEFAULT_TEACHER_REPAIR_MAX_TOKENS))
        self.extra_headers = _clean_header_map(extra_headers)

    def _request_headers(self) -> Dict[str, str]:
        headers = dict(self.extra_headers)
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _chat_completion_payloads(self, payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
        base_payload = dict(payload)
        candidates: List[Dict[str, Any]] = []
        seen = set()

        def add_candidate(candidate: Mapping[str, Any]) -> None:
            normalized = dict(candidate)
            signature = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
            if signature in seen:
                return
            seen.add(signature)
            candidates.append(normalized)

        add_candidate(base_payload)
        if "enable_thinking" in base_payload:
            stripped = dict(base_payload)
            stripped.pop("enable_thinking", None)
            add_candidate(stripped)
        if "response_format" in base_payload:
            stripped = dict(base_payload)
            stripped.pop("response_format", None)
            add_candidate(stripped)
        if "enable_thinking" in base_payload or "response_format" in base_payload:
            stripped = dict(base_payload)
            stripped.pop("enable_thinking", None)
            stripped.pop("response_format", None)
            add_candidate(stripped)
        return candidates

    def _chat_json_content(
        self,
        *,
        system_content: str,
        user_payload: Mapping[str, Any],
        excerpt: str,
        max_tokens: int,
    ) -> str:
        payload = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": int(max_tokens),
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": clean_text(system_content)},
                {"role": "user", "content": json_dumps(user_payload)},
            ],
        }
        if self.enable_thinking:
            payload["enable_thinking"] = True
        response: Dict[str, Any] | None = None
        last_http_error: Exception | None = None
        for payload_candidate in self._chat_completion_payloads(payload):
            try:
                response = _json_request(
                    f"{self.base_url}/chat/completions",
                    payload_candidate,
                    timeout_seconds=self.timeout_seconds,
                    headers=self._request_headers(),
                )
                last_http_error = None
                break
            except urllib.error.HTTPError as exc:
                last_http_error = exc
                if int(getattr(exc, "code", 0) or 0) in {400, 404, 405, 415, 422}:
                    continue
                raise TeacherTurnError("teacher_runtime_exception", "teacher_request", excerpt, str(exc)) from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                raise TeacherTurnError("teacher_timeout", "teacher_request", excerpt, str(exc)) from exc
            except Exception as exc:  # pragma: no cover
                raise TeacherTurnError("teacher_runtime_exception", "teacher_request", excerpt, str(exc)) from exc
        if response is None:
            if last_http_error is not None:
                raise TeacherTurnError("teacher_runtime_exception", "teacher_request", excerpt, str(last_http_error)) from last_http_error
            raise TeacherTurnError("teacher_runtime_exception", "teacher_request", excerpt, "empty_teacher_response")
        try:
            choices = list(response.get("choices", []) or [])
            message = dict(choices[0].get("message", {}) or {}) if choices else {}
            content = message.get("content", "")
            if isinstance(content, list):
                return "".join(clean_text(item.get("text", "")) for item in content if isinstance(item, Mapping))
            return clean_text(content)
        except Exception as exc:  # pragma: no cover
            raise TeacherTurnError("teacher_runtime_exception", "teacher_response_decode", excerpt, str(exc)) from exc

    def _validate_annotation_payload(self, parsed: Mapping[str, Any], *, excerpt: str, raw_output: str) -> Dict[str, Any]:
        normalized = {field: clean_text(dict(parsed).get(field, "")) for field in TEACHER_REQUIRED_FIELDS}
        if not normalized["event_phrase"]:
            normalized["event_phrase"] = excerpt
        if not normalized["semantic_slot"]:
            normalized["semantic_slot"] = "event"
        if normalized["time_granularity"] not in TIME_GRANULARITY_VALUES:
            normalized["time_granularity"] = ""
        if normalized["profile_type"] not in PROFILE_TYPE_VALUES:
            normalized["profile_type"] = ""
        if normalized["target_status"] not in STATUS_VALUES:
            normalized["target_status"] = ""
        return normalized

    def _repair_annotation(
        self,
        *,
        excerpt: str,
        prompt: Mapping[str, Any],
        raw_output: str,
        issue: str,
    ) -> str:
        repair_prompt = {
            "issue": clean_text(issue),
            "original_prompt": dict(prompt),
            "invalid_output": clean_text(raw_output),
            "requirements": {
                "required_keys": [
                    "event_phrase",
                    "semantic_slot",
                    "target_status",
                    "time_expression_span",
                    "time_granularity",
                    "profile_type",
                ],
                "valid_target_status": ["past", "current", "planned", ""],
                "valid_time_granularity": ["day", "month", "year", "relative_day_reference", "none", ""],
                "valid_profile_type": ["identity", "research_topic", "education", "occupation", ""],
            },
            "rules": {
                "semantic_slot": "any concise label is allowed",
                "profile_type": "must be one of the allowed values or empty string",
                "target_status": "must be one of the allowed values or empty string",
                "time_granularity": "must be one of the allowed values or empty string",
                "unknown_or_not_applicable": "use empty string",
            },
        }
        return self._chat_json_content(
            system_content=(
                "Repair the invalid annotation and return only one strict JSON object. "
                "Keys must be exactly: event_phrase, semantic_slot, target_status, time_expression_span, "
                "time_granularity, profile_type. "
                "target_status must be past/current/planned or empty string. "
                "time_granularity must be day/month/year/relative_day_reference/none or empty string. "
                "profile_type must be identity/research_topic/education/occupation or empty string. "
                "Do not output reasoning, markdown, or extra text."
            ),
            user_payload=repair_prompt,
            excerpt=excerpt,
            max_tokens=self.repair_max_tokens,
        )

    def healthcheck(self) -> Dict[str, Any]:
        if not self.base_url or not self.model:
            return {
                "ok": False,
                "error_code": "teacher_not_configured",
                "error_stage": "startup_healthcheck",
                "detail": "base_url_or_model_missing",
            }
        try:
            payload = _json_get(
                f"{self.base_url}/models",
                timeout_seconds=self.timeout_seconds,
                headers=self._request_headers(),
            )
        except urllib.error.HTTPError as exc:
            if int(getattr(exc, "code", 0) or 0) in {404, 405}:
                return {
                    "ok": True,
                    "warning": "models_endpoint_unavailable",
                    "healthcheck_mode": "skipped_models_listing",
                }
            return {
                "ok": False,
                "error_code": "teacher_unhealthy",
                "error_stage": "startup_healthcheck",
                "detail": str(exc),
            }
        except urllib.error.URLError as exc:
            return {
                "ok": False,
                "error_code": "teacher_unhealthy",
                "error_stage": "startup_healthcheck",
                "detail": str(exc),
            }
        except Exception as exc:  # pragma: no cover
            return {
                "ok": False,
                "error_code": "teacher_unhealthy",
                "error_stage": "startup_healthcheck",
                "detail": str(exc),
            }
        models = list(payload.get("data", []) or [])
        available = {clean_text(item.get("id", "")) for item in models if isinstance(item, Mapping)}
        if available and self.model not in available:
            return {
                "ok": False,
                "error_code": "teacher_unhealthy",
                "error_stage": "startup_healthcheck",
                "detail": f"model_not_listed:{self.model}",
            }
        return {"ok": True}

    def annotate_turn_with_metadata(
        self,
        *,
        current_turn: str,
        previous_turn: str,
        next_turn: str,
        session_timestamp: str,
    ) -> Dict[str, Any]:
        excerpt = clean_text(current_turn)[:400]
        prompt = {
            "current_turn": clean_text(current_turn),
            "previous_turn": clean_text(previous_turn),
            "next_turn": clean_text(next_turn),
            "session_timestamp": clean_text(session_timestamp),
            "valid_target_status": ["past", "current", "planned", ""],
            "valid_time_granularity": ["day", "month", "year", "relative_day_reference", "none", ""],
            "valid_profile_type": ["identity", "research_topic", "education", "occupation", ""],
        }
        raw_output = self._chat_json_content(
            system_content=(
                "Return only one strict JSON object with keys: event_phrase, semantic_slot, target_status, "
                "time_expression_span, time_granularity, profile_type. "
                "Keep event_phrase concise. semantic_slot may be any short label. "
                "target_status must be past/current/planned or empty string. "
                "time_granularity must be day/month/year/relative_day_reference/none or empty string. "
                "profile_type must be identity/research_topic/education/occupation or empty string. "
                "Do not output reasoning, markdown, or extra text."
            ),
            user_payload=prompt,
            excerpt=excerpt,
            max_tokens=self.annotation_max_tokens,
        )
        parsed = _parse_teacher_annotation(raw_output)
        if parsed is None:
            repaired_output = self._repair_annotation(
                excerpt=excerpt,
                prompt=prompt,
                raw_output=raw_output,
                issue="invalid_json",
            )
            repaired = _parse_teacher_annotation(repaired_output)
            if repaired is None:
                raise TeacherTurnError(
                    "teacher_invalid_json",
                    "teacher_parse",
                    excerpt,
                    repaired_output or raw_output,
                )
            return {
                "teacher_fields": self._validate_annotation_payload(
                    repaired,
                    excerpt=excerpt,
                    raw_output=repaired_output,
                ),
                "teacher_raw_output": raw_output,
                "teacher_repair_output": repaired_output,
            }
        try:
            return {
                "teacher_fields": self._validate_annotation_payload(parsed, excerpt=excerpt, raw_output=raw_output),
                "teacher_raw_output": raw_output,
                "teacher_repair_output": "",
            }
        except TeacherTurnError as exc:
            repaired_output = self._repair_annotation(
                excerpt=excerpt,
                prompt=prompt,
                raw_output=raw_output,
                issue=f"{exc.error_code}:{exc.error_stage}",
            )
            repaired = _parse_teacher_annotation(repaired_output)
            if repaired is None:
                raise TeacherTurnError(
                    "teacher_invalid_json",
                    "teacher_parse",
                    excerpt,
                    repaired_output or raw_output,
                )
            return {
                "teacher_fields": self._validate_annotation_payload(repaired, excerpt=excerpt, raw_output=repaired_output),
                "teacher_raw_output": raw_output,
                "teacher_repair_output": repaired_output,
            }

    def annotate_turn(
        self,
        *,
        current_turn: str,
        previous_turn: str,
        next_turn: str,
        session_timestamp: str,
    ) -> Dict[str, Any]:
        payload = self.annotate_turn_with_metadata(
            current_turn=current_turn,
            previous_turn=previous_turn,
            next_turn=next_turn,
            session_timestamp=session_timestamp,
        )
        return dict(payload.get("teacher_fields", {}) or {})


def _token_overlap_score(question: str, event_text: str) -> float:
    left = set(tokenize_text(question))
    right = set(tokenize_text(event_text))
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def _time_granularity_matches(target: Any, value: Any) -> bool:
    normalized_target = clean_text(target)
    normalized_value = clean_text(value)
    if not normalized_target or not normalized_value:
        return False
    if normalized_target == "day_or_coarse":
        return normalized_value in {"day", "relative_day_reference", "month", "year"}
    return normalized_target == normalized_value


def _event_similarity(left_event: Mapping[str, Any], right_event: Mapping[str, Any]) -> float:
    left_fields = [
        clean_text(left_event.get("event_text", "")),
        clean_text(left_event.get("event_signature", "")),
        clean_text(left_event.get("profile_value", "")),
        clean_text(left_event.get("time_display_value", "")),
        clean_text(left_event.get("time_value", "")),
    ]
    right_fields = [
        clean_text(right_event.get("event_text", "")),
        clean_text(right_event.get("event_signature", "")),
        clean_text(right_event.get("profile_value", "")),
        clean_text(right_event.get("time_display_value", "")),
        clean_text(right_event.get("time_value", "")),
    ]
    best = 0.0
    for left_field in left_fields:
        if not left_field:
            continue
        for right_field in right_fields:
            if not right_field:
                continue
            best = max(best, _token_overlap_score(left_field, right_field))
    return best


def _candidate_hard_negative_profile(
    *,
    question: str,
    question_features: Mapping[str, Any],
    event_meta: Mapping[str, Any],
    positive_events: Sequence[Mapping[str, Any]],
    speaker_targets: set[str],
    positive_speakers: set[str],
    positive_profile_types: set[str],
    positive_statuses: set[str],
    positive_time_granularities: set[str],
    positive_sessions: set[str],
    positive_turn_indices: Sequence[int],
) -> Dict[str, Any]:
    event_text = clean_text(event_meta.get("event_text", ""))
    profile_value = clean_text(event_meta.get("profile_value", ""))
    time_display_value = clean_text(event_meta.get("time_display_value", ""))
    time_value = clean_text(event_meta.get("time_value", ""))
    speaker = normalize_text(event_meta.get("speaker", ""))
    semantic_target = clean_text(question_features.get("semantic_slot_target", ""))
    status_target = clean_text(question_features.get("target_status_target", ""))
    time_target = clean_text(question_features.get("time_granularity_target", ""))
    profile_type = clean_text(event_meta.get("profile_type", ""))
    target_status = clean_text(event_meta.get("target_status", ""))
    time_granularity = clean_text(event_meta.get("time_granularity", ""))
    lexical_overlap = max(
        _token_overlap_score(question, event_text),
        _token_overlap_score(question, profile_value),
    )
    time_overlap = max(
        _token_overlap_score(question, time_display_value),
        _token_overlap_score(question, time_value),
    )
    speaker_target_match = bool(speaker_targets and speaker in speaker_targets)
    speaker_positive_match = bool(positive_speakers and speaker in positive_speakers)
    profile_target_match = semantic_target in {"identity", "research_topic", "education", "occupation"} and profile_type == semantic_target
    generic_profile_match = semantic_target == "profile" and bool(profile_value)
    status_target_match = bool(status_target and target_status == status_target)
    status_positive_match = bool(positive_statuses and target_status and target_status in positive_statuses)
    time_target_match = bool(time_target and _time_granularity_matches(time_target, time_granularity))
    time_positive_match = bool(positive_time_granularities and time_granularity and time_granularity in positive_time_granularities)
    profile_positive_match = bool(positive_profile_types and profile_type and profile_type in positive_profile_types)
    session_match = bool(positive_sessions and clean_text(event_meta.get("session_name", "")) in positive_sessions)
    event_turn_index = int(event_meta.get("turn_index", 0) or 0)
    closest_delta: int | None = None
    if positive_turn_indices:
        closest_delta = min(abs(event_turn_index - turn_index) for turn_index in positive_turn_indices)
    event_similarity = 0.0
    if positive_events:
        event_similarity = max(_event_similarity(event_meta, positive_event) for positive_event in positive_events)

    score = 1.25 * lexical_overlap
    if speaker_target_match:
        score += 1.0
    elif speaker_positive_match:
        score += 0.35
    if profile_target_match:
        score += 0.9
    elif generic_profile_match:
        score += 0.55
    elif semantic_target == "event_time" and (time_display_value or time_value):
        score += 0.45
    if status_target_match:
        score += 0.45
    elif status_positive_match:
        score += 0.2
    if time_target_match:
        score += 0.7
    elif time_positive_match:
        score += 0.2
    if bool(question_features.get("is_temporal", False)):
        score += 0.35 * time_overlap
    if profile_positive_match:
        score += 0.25
    if session_match:
        score += 0.15
    if closest_delta is not None:
        if closest_delta <= 3:
            score += 0.3
        elif closest_delta <= 6:
            score += 0.15
    if event_similarity > 0.0:
        score += 0.8 * event_similarity

    bucket = "easy"
    if score > 0.0:
        if profile_target_match or generic_profile_match or profile_positive_match:
            bucket = "profile_confuser"
        elif bool(question_features.get("is_temporal", False)) and (
            time_target_match or time_positive_match or time_overlap > 0.0
        ):
            bucket = "temporal_confuser"
        elif speaker_target_match or speaker_positive_match:
            bucket = "speaker_confuser"
        elif lexical_overlap >= 0.2 or event_similarity >= 0.2:
            bucket = "lexical_confuser"
        elif session_match or (closest_delta is not None and closest_delta <= 6):
            bucket = "session_neighbor"
        else:
            bucket = "generic_confuser"
    return {
        "score": float(score),
        "bucket": bucket,
        "lexical_overlap": float(lexical_overlap),
        "event_similarity": float(event_similarity),
    }


def _candidate_hard_negative_score(
    *,
    question: str,
    question_features: Mapping[str, Any],
    event_meta: Mapping[str, Any],
    positive_events: Sequence[Mapping[str, Any]],
    speaker_targets: set[str],
    positive_speakers: set[str],
    positive_profile_types: set[str],
    positive_statuses: set[str],
    positive_time_granularities: set[str],
    positive_sessions: set[str],
    positive_turn_indices: Sequence[int],
) -> float:
    profile = _candidate_hard_negative_profile(
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
    return float(profile.get("score", 0.0) or 0.0)


def _select_diverse_hard_negative_ids(
    hard_candidates: Sequence[Mapping[str, Any]],
    *,
    limit: int = 8,
) -> List[str]:
    resolved_limit = max(0, int(limit))
    if resolved_limit <= 0:
        return []
    sorted_candidates = sorted(
        (
            {
                "event_id": clean_text(candidate.get("event_id", "")),
                "score": float(candidate.get("score", 0.0) or 0.0),
                "bucket": clean_text(candidate.get("bucket", "")) or "generic_confuser",
            }
            for candidate in hard_candidates
            if clean_text(candidate.get("event_id", ""))
        ),
        key=lambda item: (-item["score"], item["bucket"], item["event_id"]),
    )
    bucket_order = (
        "profile_confuser",
        "temporal_confuser",
        "speaker_confuser",
        "lexical_confuser",
        "session_neighbor",
        "generic_confuser",
    )
    candidates_by_bucket: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in sorted_candidates:
        candidates_by_bucket.setdefault(candidate["bucket"], []).append(dict(candidate))
    selected: List[str] = []
    selected_set: set[str] = set()
    while len(selected) < resolved_limit:
        progress = False
        for bucket in bucket_order:
            bucket_candidates = candidates_by_bucket.get(bucket, [])
            while bucket_candidates and bucket_candidates[0]["event_id"] in selected_set:
                bucket_candidates.pop(0)
            if not bucket_candidates:
                continue
            event_id = bucket_candidates.pop(0)["event_id"]
            if event_id in selected_set:
                continue
            selected.append(event_id)
            selected_set.add(event_id)
            progress = True
            if len(selected) >= resolved_limit:
                break
        if not progress:
            break
    for candidate in sorted_candidates:
        event_id = candidate["event_id"]
        if event_id in selected_set:
            continue
        selected.append(event_id)
        selected_set.add(event_id)
        if len(selected) >= resolved_limit:
            break
    return selected[:resolved_limit]


def _positive_path_types(*, answer_type: str, question_features: Mapping[str, Any]) -> set[str] | None:
    normalized_answer_type = clean_text(answer_type)
    if normalized_answer_type == "time" or bool(question_features.get("is_temporal", False)):
        return {"speaker_event_time"}
    if normalized_answer_type == "profile":
        return {"speaker_event_profile"}
    if normalized_answer_type == "event_text":
        return {"speaker_event_source_turn"}
    if normalized_answer_type == "abstain":
        return set()
    return None


def _positive_path_ids(
    paths: Sequence[Mapping[str, Any]],
    positive_event_ids: Sequence[str],
    *,
    answer_type: str,
    question_features: Mapping[str, Any],
) -> List[str]:
    positive_set = {clean_text(item) for item in positive_event_ids if clean_text(item)}
    allowed_path_types = _positive_path_types(answer_type=answer_type, question_features=question_features)
    return dedupe_texts(
        path.get("id", "")
        for path in paths
        if clean_text(path.get("event_id", "")) in positive_set
        and (allowed_path_types is None or clean_text(path.get("type", "")) in allowed_path_types)
    )


def _positive_time_node_ids(event_catalog: Mapping[str, Mapping[str, Any]], positive_event_ids: Sequence[str]) -> List[str]:
    positive_ids = [clean_text(item) for item in positive_event_ids if clean_text(item)]
    return dedupe_texts(
        time_node_id
        for event_id in positive_ids
        for time_node_id in list(event_catalog.get(event_id, {}).get("time_node_ids", []) or [])
        if clean_text(time_node_id)
    )


def _fallback_session_time_metadata(timestamp: str) -> Dict[str, str]:
    base_time = _parse_public_timestamp(clean_text(timestamp))
    if base_time is None:
        return {}
    display_value = _format_public_date(base_time)
    return {
        "time_display_value": display_value,
        "time_value": _public_iso_from_display_date(display_value) or base_time.strftime("%Y-%m-%d"),
        "time_granularity": "day",
        "time_source": "session_timestamp_fallback",
        "resolved_date": display_value,
    }


def _resolve_event_time_fields(
    *,
    time_metadata: Mapping[str, Any] | None,
    timestamp: str,
    allow_session_fallback: bool = True,
) -> Dict[str, str]:
    metadata = dict(time_metadata or {})
    time_value = clean_text(metadata.get("resolved_time_value", metadata.get("time_value", "")))
    time_display_value = clean_text(metadata.get("time_display_value", metadata.get("display_time_value", "")))
    time_granularity = clean_text(metadata.get("time_granularity", ""))
    time_source = clean_text(metadata.get("time_source", ""))
    resolved_date = clean_text(metadata.get("resolved_date", ""))
    if time_display_value or time_value:
        return {
            "time_display_value": time_display_value,
            "time_value": time_value,
            "time_granularity": time_granularity or "none",
            "time_source": time_source,
            "resolved_date": resolved_date,
        }
    fallback = _fallback_session_time_metadata(timestamp)
    if fallback and allow_session_fallback:
        return fallback
    return {
        "time_display_value": "",
        "time_value": "",
        "time_granularity": "none",
        "time_source": "",
        "resolved_date": "",
    }


def _temporal_target(
    question_features: Mapping[str, Any],
    event_catalog: Mapping[str, Mapping[str, Any]],
    positive_event_ids: Sequence[str],
    *,
    positive_time_node_ids: Sequence[str] = (),
    answer_type: str = "",
) -> Dict[str, Any]:
    positive_ids = [clean_text(item) for item in positive_event_ids if clean_text(item)]
    positive_events = [dict(event_catalog.get(event_id, {}) or {}) for event_id in positive_ids]
    question_is_temporal = bool(question_features.get("is_temporal", False))
    positive_time_ids = [clean_text(item) for item in positive_time_node_ids if clean_text(item)]
    normalized_answer_type = clean_text(answer_type)
    use_temporal_head = question_is_temporal and (bool(positive_time_ids) or normalized_answer_type == "abstain")
    return {
        "is_temporal": question_is_temporal,
        "question_is_temporal": question_is_temporal,
        "has_positive_time_supervision": bool(positive_time_ids),
        "use_temporal_head": use_temporal_head,
        "time_granularity_target": clean_text(question_features.get("time_granularity_target", "")),
        "positive_time_values": dedupe_texts(event.get("time_value", "") for event in positive_events),
        "positive_time_display_values": dedupe_texts(event.get("time_display_value", "") for event in positive_events),
    }


def _candidate_bundle(
    *,
    question: str,
    question_features: Mapping[str, Any],
    event_catalog: Mapping[str, Mapping[str, Any]],
    positive_event_ids: Sequence[str],
) -> Dict[str, List[str]]:
    hard_candidates: List[Dict[str, Any]] = []
    easy_candidates: List[str] = []
    speaker_targets = {
        normalize_text(item)
        for item in list(question_features.get("speaker_candidates", []) or [])
        if clean_text(item)
    }
    positive_set = {clean_text(item) for item in positive_event_ids if clean_text(item)}
    positive_events = [dict(event_catalog.get(event_id, {}) or {}) for event_id in positive_event_ids if clean_text(event_id)]
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
    positive_turn_indices = [int(event.get("turn_index", 0) or 0) for event in positive_events]
    for event_id, event_meta in event_catalog.items():
        if event_id in positive_set:
            continue
        profile = _candidate_hard_negative_profile(
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
        score = float(profile.get("score", 0.0) or 0.0)
        if score > 0.0:
            hard_candidates.append(
                {
                    "event_id": event_id,
                    "score": score,
                    "bucket": clean_text(profile.get("bucket", "")) or "generic_confuser",
                }
            )
        else:
            easy_candidates.append(event_id)
    easy_candidates.sort()
    selected_hard = _select_diverse_hard_negative_ids(hard_candidates, limit=8)
    selected_easy = [event_id for event_id in easy_candidates[:8]]
    candidate_event_ids = dedupe_texts([*positive_event_ids, *selected_hard, *selected_easy])
    hard_negative_event_ids = [
        event_id for event_id in selected_hard if event_id in candidate_event_ids and event_id not in positive_set
    ]
    easy_negative_event_ids = [
        event_id for event_id in selected_easy if event_id in candidate_event_ids and event_id not in positive_set
    ]
    negative_event_ids = dedupe_texts([*hard_negative_event_ids, *easy_negative_event_ids])
    return {
        "candidate_event_ids": candidate_event_ids,
        "hard_negative_event_ids": hard_negative_event_ids,
        "easy_negative_event_ids": easy_negative_event_ids,
        "negative_event_ids": negative_event_ids,
    }


def _event_hint(event_text: str, *, max_tokens: int = 6) -> str:
    tokens = tokenize_text(event_text)
    if not tokens:
        return clean_text(event_text)
    return clean_text(" ".join(tokens[: max(1, int(max_tokens))]))


def _event_signature_semantic_slot(event_node: Mapping[str, Any]) -> str:
    metadata = dict(event_node.get("metadata", {}) or {})
    teacher_fields = dict(event_node.get("teacher_fields", {}) or {})
    return (
        clean_text(event_node.get("profile_type", ""))
        or clean_text(metadata.get("profile_type", ""))
        or clean_text(teacher_fields.get("profile_type", ""))
        or clean_text(teacher_fields.get("semantic_slot", ""))
        or clean_text(metadata.get("semantic_slot", ""))
        or "event"
    )


def _derive_event_signature(*, event_text: str, speaker: str, semantic_slot: str) -> str:
    base_text = clean_text(event_text)
    if not base_text:
        return ""
    return compute_public_event_signature(
        base_text,
        speaker=clean_text(speaker),
        semantic_slot=clean_text(semantic_slot),
    ) or base_text


def _ensure_graph_event_signatures(graph: Mapping[str, Any]) -> int:
    nodes = list(graph.get("nodes", []) or [])
    edges = list(graph.get("edges", []) or [])
    node_by_id: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = clean_text(node.get("id", ""))
        if node_id:
            node_by_id[node_id] = node
    source_turn_text_by_event_id: Dict[str, str] = {}
    for edge in edges:
        if not isinstance(edge, Mapping):
            continue
        if clean_text(edge.get("type", "")) != "supported_by_turn":
            continue
        event_id = clean_text(edge.get("source", ""))
        source_turn_id = clean_text(edge.get("target", ""))
        source_turn_node = node_by_id.get(source_turn_id, {})
        if clean_text(source_turn_node.get("type", "")) != "source_turn":
            continue
        source_turn_text = clean_text(source_turn_node.get("text", ""))
        if source_turn_text:
            source_turn_text_by_event_id[event_id] = source_turn_text
    updated = 0
    for node in nodes:
        if not isinstance(node, dict) or clean_text(node.get("type", "")) != "event":
            continue
        node_id = clean_text(node.get("id", ""))
        metadata = dict(node.get("metadata", {}) or {})
        event_signature = clean_text(node.get("event_signature", metadata.get("event_signature", "")))
        event_text = (
            clean_text(node.get("text", ""))
            or clean_text(dict(node.get("teacher_fields", {}) or {}).get("event_phrase", ""))
            or clean_text(metadata.get("event_phrase", ""))
            or source_turn_text_by_event_id.get(node_id, "")
        )
        speaker = clean_text(node.get("speaker", metadata.get("speaker", "")))
        semantic_slot = _event_signature_semantic_slot(node)
        derived_signature = event_signature or _derive_event_signature(
            event_text=event_text,
            speaker=speaker,
            semantic_slot=semantic_slot,
        )
        changed = False
        if derived_signature and clean_text(node.get("event_signature", "")) != derived_signature:
            node["event_signature"] = derived_signature
            changed = True
        if derived_signature and clean_text(metadata.get("event_signature", "")) != derived_signature:
            metadata["event_signature"] = derived_signature
            changed = True
        if changed:
            node["metadata"] = metadata
            updated += 1
    return updated


def _profile_question(speaker: str, profile_type: str) -> str:
    normalized = clean_text(profile_type)
    if normalized == "identity":
        return f"What is {speaker}'s identity?"
    if normalized == "research_topic":
        return f"What did {speaker} research?"
    if normalized == "education":
        return f"What does {speaker} study?"
    if normalized == "occupation":
        return f"What is {speaker}'s occupation?"
    return f"What profile detail did {speaker} mention?"


def _event_catalog_from_graph(graph: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    nodes = [dict(node) for node in list(graph.get("nodes", []) or []) if isinstance(node, Mapping)]
    profiles_by_event: Dict[str, Dict[str, Any]] = {}
    times_by_event: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        node_id = clean_text(node.get("id", ""))
        if not node_id:
            continue
        if clean_text(node.get("type", "")) == "profile" and ":profile:" in node_id:
            event_id = node_id.split(":profile:", 1)[0]
            profiles_by_event.setdefault(event_id, dict(node))
        elif clean_text(node.get("type", "")) == "time" and node_id.endswith(":time"):
            event_id = node_id.rsplit(":time", 1)[0]
            times_by_event.setdefault(event_id, dict(node))

    catalog: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        if clean_text(node.get("type", "")) != "event":
            continue
        event_id = clean_text(node.get("id", ""))
        metadata = dict(node.get("metadata", {}) or {})
        profile_node = dict(profiles_by_event.get(event_id, {}) or {})
        time_node = dict(times_by_event.get(event_id, {}) or {})
        catalog[event_id] = {
            "event_id": event_id,
            "speaker": clean_text(node.get("speaker", metadata.get("speaker", ""))),
            "event_text": clean_text(node.get("text", "")),
            "event_signature": clean_text(node.get("event_signature", metadata.get("event_signature", "")))
            or _derive_event_signature(
                event_text=clean_text(node.get("text", "")),
                speaker=clean_text(node.get("speaker", metadata.get("speaker", ""))),
                semantic_slot=_event_signature_semantic_slot(node),
            ),
            "time_node_ids": [clean_text(time_node.get("id", ""))] if clean_text(time_node.get("id", "")) else [],
            "time_granularity": clean_text(node.get("time_granularity", metadata.get("time_granularity", ""))),
            "time_value": clean_text(time_node.get("time_value", metadata.get("time_value", ""))),
            "time_display_value": clean_text(time_node.get("time_display_value", metadata.get("time_display_value", ""))),
            "target_status": clean_text(node.get("target_status", metadata.get("target_status", ""))),
            "profile_type": clean_text(profile_node.get("profile_type", metadata.get("profile_type", ""))),
            "profile_value": clean_text(profile_node.get("profile_value", metadata.get("profile_value", ""))),
            "session_name": clean_text(node.get("session_name", metadata.get("session_name", ""))),
            "turn_index": int(node.get("turn_index", metadata.get("turn_index", 0)) or 0),
            "dia_id": clean_text(node.get("dia_id", metadata.get("dia_id", ""))),
        }
    return catalog


def _build_synthetic_query_rows(
    *,
    conversation_id: str,
    split: str,
    event_catalog: Mapping[str, Mapping[str, Any]],
    paths: Sequence[Mapping[str, Any]],
    existing_rows: Sequence[Mapping[str, Any]],
    max_queries_per_conversation: int = 0,
) -> List[Dict[str, Any]]:
    if split != "train" or int(max_queries_per_conversation or 0) <= 0:
        return []
    existing_keys = {
        (
            normalize_text(row.get("question", "")),
            tuple(sorted(clean_text(item) for item in list(row.get("positive_event_ids", []) or []) if clean_text(item))),
        )
        for row in existing_rows
    }
    event_items = sorted(
        [dict(item) for item in event_catalog.values()],
        key=lambda item: (int(item.get("turn_index", 0) or 0), clean_text(item.get("event_id", ""))),
    )
    synthetic_rows: List[Dict[str, Any]] = []
    synthetic_index = 0

    def append_query(question: str, *, positive_event_ids: Sequence[str], answer_type: str, synthetic_kind: str) -> None:
        nonlocal synthetic_index
        normalized_question = clean_text(question)
        positives = dedupe_texts(positive_event_ids)
        if not normalized_question or not positives:
            return
        key = (normalize_text(normalized_question), tuple(sorted(positives)))
        if key in existing_keys:
            return
        existing_keys.add(key)
        question_features = extract_question_features(normalized_question)
        bundle = _candidate_bundle(
            question=normalized_question,
            question_features=question_features,
            event_catalog=event_catalog,
            positive_event_ids=positives,
        )
        synthetic_rows.append(
            {
                "conversation_id": conversation_id,
                "question_id": f"{conversation_id}:synthetic:{synthetic_kind}:{synthetic_index}",
                "question": normalized_question,
                "question_features": question_features,
                "candidate_event_ids": bundle["candidate_event_ids"],
                "positive_event_ids": positives,
                "positive_path_ids": _positive_path_ids(
                    paths,
                    positives,
                    answer_type=answer_type,
                    question_features=question_features,
                ),
                "positive_time_node_ids": _positive_time_node_ids(event_catalog, positives),
                "hard_negative_event_ids": bundle["hard_negative_event_ids"],
                "easy_negative_event_ids": bundle["easy_negative_event_ids"],
                "negative_event_ids": bundle["negative_event_ids"],
                "answer_targets": {"answer_type": answer_type},
                "temporal_target": _temporal_target(
                    question_features,
                    event_catalog,
                    positives,
                    positive_time_node_ids=_positive_time_node_ids(event_catalog, positives),
                    answer_type=answer_type,
                ),
                "event_catalog_size": len(event_catalog),
                "metadata": {
                    "category": "synthetic",
                    "split": split,
                    "evidence_count": len(positives),
                    "dia_ids": dedupe_texts(event_catalog.get(event_id, {}).get("dia_id", "") for event_id in positives),
                    "synthetic": True,
                    "synthetic_kind": synthetic_kind,
                },
            }
        )
        synthetic_index += 1

    for event in event_items:
        event_id = clean_text(event.get("event_id", ""))
        speaker = clean_text(event.get("speaker", ""))
        event_text = clean_text(event.get("event_text", ""))
        hint = _event_hint(event_text)
        if event_id and speaker and clean_text(event.get("time_granularity", "")) not in {"", "none"} and (
            clean_text(event.get("time_display_value", "")) or clean_text(event.get("time_value", ""))
        ):
            append_query(
                f"When did {speaker} {hint}?",
                positive_event_ids=[event_id],
                answer_type="time",
                synthetic_kind="time",
            )
        profile_type = clean_text(event.get("profile_type", ""))
        profile_value = clean_text(event.get("profile_value", ""))
        if event_id and speaker and profile_type and profile_value:
            append_query(
                _profile_question(speaker, profile_type),
                positive_event_ids=[event_id],
                answer_type="profile",
                synthetic_kind="profile",
            )
        if event_id and speaker and hint:
            append_query(
                f"What event involved {speaker} and {hint}?",
                positive_event_ids=[event_id],
                answer_type="event_text",
                synthetic_kind="event_text",
            )

    speaker_to_events: Dict[str, List[str]] = {}
    for event in event_items:
        speaker = clean_text(event.get("speaker", ""))
        event_id = clean_text(event.get("event_id", ""))
        if speaker and event_id:
            speaker_to_events.setdefault(speaker, []).append(event_id)
    for speaker, event_ids in sorted(speaker_to_events.items()):
        positives = dedupe_texts(event_ids, max_items=3)
        if len(positives) < 2:
            continue
        append_query(
            f"What activities has {speaker} mentioned across the conversation?",
            positive_event_ids=positives,
            answer_type="multi_evidence",
            synthetic_kind="multi_evidence",
        )

    return synthetic_rows[: max(0, int(max_queries_per_conversation))]


def _build_status_node(event_id: str, target_status: str, *, turn_index: int) -> Dict[str, Any]:
    return {
        "id": f"{event_id}:status",
        "type": "status",
        "text": target_status,
        "turn_index": int(turn_index),
        "target_status": target_status,
        "metadata": {"target_status": target_status},
    }


def _conversation_graph_and_queries(
    *,
    conversation_id: str,
    sample: Mapping[str, Any],
    split: str,
    teacher_client: TeacherClient,
    audit_policy: str = AUDIT_POLICY_STRICT_PAUSE,
) -> Dict[str, Any]:
    conversation = dict(sample.get("conversation", {}) or {})
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    paths: List[Dict[str, Any]] = []
    node_ids = set()
    event_by_dia_id: Dict[str, str] = {}
    event_catalog: Dict[str, Dict[str, Any]] = {}
    previous_event_id = ""
    audit_rows: List[Dict[str, Any]] = []
    turn_extraction_rows: List[Dict[str, Any]] = []
    session_names = list(_locomo_session_names(conversation))
    total_turn_count = sum(
        len(list(conversation.get(session_name, []) or []))
        for session_name in session_names
    )
    processed_turn_count = 0

    def add_node(node: Mapping[str, Any]) -> None:
        node_id = clean_text(node.get("id", ""))
        if not node_id or node_id in node_ids:
            return
        node_ids.add(node_id)
        nodes.append(dict(node))

    for session_name in session_names:
        session_turns = list(conversation.get(session_name, []) or [])
        timestamp = clean_text(conversation.get(f"{session_name}_date_time", ""))
        for turn_offset, turn in enumerate(session_turns, start=1):
            turn_payload = dict(turn or {})
            speaker = clean_text(turn_payload.get("speaker", "")) or "speaker"
            turn_text = clean_text(turn_payload.get("text", ""))
            dia_id = clean_text(turn_payload.get("dia_id", "") or turn_payload.get("id", ""))
            previous_turn = clean_text(session_turns[turn_offset - 2].get("text", "")) if turn_offset > 1 else ""
            next_turn = clean_text(session_turns[turn_offset].get("text", "")) if turn_offset < len(session_turns) else ""
            teacher_started_at = time.time()
            try:
                if hasattr(teacher_client, "annotate_turn_with_metadata"):
                    teacher_annotation = teacher_client.annotate_turn_with_metadata(
                        current_turn=turn_text,
                        previous_turn=previous_turn,
                        next_turn=next_turn,
                        session_timestamp=timestamp,
                    )
                else:
                    teacher_annotation = {
                        "teacher_fields": teacher_client.annotate_turn(
                            current_turn=turn_text,
                            previous_turn=previous_turn,
                            next_turn=next_turn,
                            session_timestamp=timestamp,
                        ),
                        "teacher_raw_output": "",
                        "teacher_repair_output": "",
                    }
            except TeacherTurnError as exc:
                teacher_latency_ms = int(round((time.time() - teacher_started_at) * 1000))
                audit_rows.append(
                    build_teacher_audit_candidate(
                        conversation_id=conversation_id,
                        split=split,
                        session_name=session_name,
                        turn_index=int(turn_offset),
                        dia_id=dia_id,
                        speaker=speaker,
                        current_turn=turn_text,
                        previous_turn=previous_turn,
                        next_turn=next_turn,
                        session_timestamp=timestamp,
                        teacher_fields={},
                        semantic_consistency={"passed": False, "issues": [exc.error_code], "checks": {}},
                        teacher_latency_ms=teacher_latency_ms,
                        teacher_request_excerpt=exc.teacher_request_excerpt,
                        teacher_raw_output=exc.teacher_raw_output,
                        teacher_repair_output=exc.teacher_repair_output,
                        teacher_error_code=exc.error_code,
                        teacher_error_stage=exc.error_stage,
                    )
                )
                raise TeacherTurnError(
                    exc.error_code,
                    exc.error_stage,
                    exc.teacher_request_excerpt,
                    exc.teacher_raw_output,
                    teacher_repair_output=exc.teacher_repair_output,
                    session_name=session_name,
                    turn_index=turn_offset,
                    dia_id=dia_id,
                    audit_rows=list(audit_rows),
                ) from exc
            teacher_latency_ms = int(round((time.time() - teacher_started_at) * 1000))
            teacher_fields = dict(teacher_annotation.get("teacher_fields", {}) or {})
            teacher_raw_output = clean_text(teacher_annotation.get("teacher_raw_output", ""))
            teacher_repair_output = clean_text(teacher_annotation.get("teacher_repair_output", ""))
            semantic_consistency = evaluate_teacher_annotation_consistency(
                current_turn=turn_text,
                teacher_fields=teacher_fields,
            )
            repair_result: Dict[str, Any] | None = None
            if not bool(semantic_consistency.get("passed", False)):
                repair_result = _repair_semantic_mismatch(
                    current_turn=turn_text,
                    previous_turn=previous_turn,
                    next_turn=next_turn,
                    session_timestamp=timestamp,
                    teacher_fields=teacher_fields,
                )
            audit_row = build_teacher_audit_candidate(
                conversation_id=conversation_id,
                split=split,
                session_name=session_name,
                turn_index=int(turn_offset),
                dia_id=dia_id,
                speaker=speaker,
                current_turn=turn_text,
                previous_turn=previous_turn,
                next_turn=next_turn,
                session_timestamp=timestamp,
                teacher_fields=teacher_fields,
                semantic_consistency=semantic_consistency,
                teacher_latency_ms=teacher_latency_ms,
                teacher_request_excerpt=turn_text[:400],
                teacher_raw_output=teacher_raw_output,
                teacher_repair_output=teacher_repair_output,
                teacher_error_code="" if semantic_consistency.get("passed", False) else "teacher_semantic_mismatch",
                teacher_error_stage="" if semantic_consistency.get("passed", False) else "teacher_semantic_validation",
            )
            if repair_result is not None:
                audit_row["resolved_teacher_fields"] = dict(repair_result.get("teacher_fields", {}) or {})
                audit_row["resolution_semantic_consistency"] = dict(repair_result.get("semantic_consistency", {}) or {})
                audit_row["resolution_strategy"] = clean_text(repair_result.get("selected_source", ""))
                audit_row["resolution_status"] = (
                    "repaired"
                    if bool(dict(repair_result.get("semantic_consistency", {}) or {}).get("passed", False))
                    else "unresolved"
                )
            audit_rows.append(audit_row)
            if not bool(semantic_consistency.get("passed", False)):
                if (
                    clean_text(audit_policy) == AUDIT_POLICY_REPAIR_OR_QUEUE_NONBLOCKING
                    and repair_result is not None
                    and bool(dict(repair_result.get("semantic_consistency", {}) or {}).get("passed", False))
                ):
                    teacher_fields = dict(repair_result.get("teacher_fields", {}) or {})
                    semantic_consistency = dict(repair_result.get("semantic_consistency", {}) or {})
                else:
                    raise TeacherTurnError(
                        "teacher_semantic_mismatch",
                        "teacher_semantic_validation",
                        turn_text[:400],
                        teacher_raw_output,
                        teacher_repair_output=teacher_repair_output,
                        session_name=session_name,
                        turn_index=turn_offset,
                        dia_id=dia_id,
                        audit_rows=list(audit_rows),
                )
            processed_turn_count += 1
            label_source = "teacher"
            if repair_result is not None and bool(dict(repair_result.get("semantic_consistency", {}) or {}).get("passed", False)):
                label_source = clean_text(repair_result.get("selected_source", "")) or "teacher_repaired"
            turn_extraction_rows.append(
                {
                    "conversation_id": conversation_id,
                    "split": split,
                    "session_name": session_name,
                    "turn_index": int(turn_offset),
                    "dia_id": dia_id,
                    "speaker": speaker,
                    "current_turn": turn_text,
                    "previous_turn": previous_turn,
                    "next_turn": next_turn,
                    "session_timestamp": timestamp,
                    "annotation": dict(teacher_fields),
                    "label_source": label_source,
                    "metadata": {
                        "source_dataset": "locomo",
                        "teacher_latency_ms": int(teacher_latency_ms),
                        "semantic_consistency_passed": bool(semantic_consistency.get("passed", False)),
                        "repair_applied": bool(repair_result is not None and label_source != "teacher"),
                    },
                }
            )
            payload = _public_turn_payload(
                text=turn_text,
                raw_text=turn_text,
                speaker=speaker,
                session_key=session_name,
                turn_index=turn_offset,
                timestamp=timestamp,
                dia_id=dia_id,
            )
            structured_records = list(payload.get("replacement_memory_records", []) or [])
            profile_record = next((item for item in structured_records if clean_text(item.get("source_kind", "")) == "public_dialog_profile"), {})
            time_record = next((item for item in structured_records if clean_text(item.get("source_kind", "")) == "public_dialog_time"), {})
            event_record = next((item for item in structured_records if clean_text(item.get("source_kind", "")) == "public_dialog_event"), {})
            time_metadata = dict(time_record.get("metadata", {}) or {})
            profile_metadata = dict(profile_record.get("metadata", {}) or {})
            event_metadata = dict(event_record.get("metadata", {}) or {})
            speaker_node_id = f"{conversation_id}:speaker:{normalize_text(speaker).replace(' ', '_') or 'speaker'}"
            event_id = f"{conversation_id}:{session_name}:{turn_offset}:event"
            event_by_dia_id[dia_id] = event_id
            event_text = clean_text(teacher_fields.get("event_phrase", "")) or clean_text(event_record.get("value", "")) or turn_text
            profile_type = clean_text(teacher_fields.get("profile_type", "")) or clean_text(profile_metadata.get("semantic_slot", ""))
            profile_value = clean_text(profile_record.get("value", ""))
            semantic_slot = profile_type or clean_text(teacher_fields.get("semantic_slot", "")) or clean_text(event_metadata.get("semantic_slot", "")) or "event"
            event_signature = clean_text(event_metadata.get("event_signature", "")) or _derive_event_signature(
                event_text=event_text or turn_text,
                speaker=speaker,
                semantic_slot=semantic_slot,
            )
            target_status = clean_text(teacher_fields.get("target_status", "")) or clean_text(time_metadata.get("target_status", "")) or "current"
            teacher_time_span = clean_text(teacher_fields.get("time_expression_span", ""))
            teacher_time_granularity = clean_text(teacher_fields.get("time_granularity", ""))
            resolved_time = _resolve_event_time_fields(time_metadata=time_metadata, timestamp=timestamp)
            if teacher_time_span and teacher_time_granularity in {"", "none"}:
                resolved_time = _resolve_event_time_fields(
                    time_metadata=time_metadata,
                    timestamp=timestamp,
                    allow_session_fallback=False,
                )
            time_granularity = clean_text(resolved_time.get("time_granularity", "")) or clean_text(teacher_fields.get("time_granularity", "")) or "none"
            time_value = clean_text(resolved_time.get("time_value", ""))
            time_display_value = clean_text(resolved_time.get("time_display_value", ""))
            time_source = clean_text(resolved_time.get("time_source", ""))
            resolved_date = clean_text(resolved_time.get("resolved_date", ""))
            if processed_turn_count == 1 or processed_turn_count % 25 == 0 or processed_turn_count == total_turn_count:
                _log(
                    "conversation_turn_progress",
                    conversation_id=conversation_id,
                    split=split,
                    session_name=session_name,
                    session_turn_index=int(turn_offset),
                    session_turn_total=len(session_turns),
                    processed_turns=int(processed_turn_count),
                    total_turns=int(total_turn_count),
                    dia_id=dia_id,
                    teacher_latency_ms=int(teacher_latency_ms),
                    event_phrase=event_text[:96],
                )

            add_node(
                {
                    "id": speaker_node_id,
                    "type": "speaker",
                    "text": speaker,
                    "turn_index": int(turn_offset),
                    "metadata": {"speaker": speaker},
                }
            )
            add_node(
                {
                    "id": event_id,
                    "type": "event",
                    "text": event_text,
                    "turn_index": int(turn_offset),
                    "speaker": speaker,
                    "session_name": session_name,
                    "dia_id": dia_id,
                    "event_signature": event_signature,
                    "time_granularity": time_granularity,
                    "target_status": target_status,
                    "teacher_fields": dict(teacher_fields),
                    "metadata": {
                        "speaker": speaker,
                        "session_name": session_name,
                        "dia_id": dia_id,
                        "target_status": target_status,
                        "time_granularity": time_granularity,
                        "time_value": time_value,
                        "time_display_value": time_display_value,
                        "time_source": time_source,
                        "resolved_date": resolved_date,
                        "profile_type": profile_type,
                        "profile_value": profile_value,
                        "event_signature": event_signature,
                        "session_timestamp": timestamp,
                    },
                }
            )
            edges.append({"id": f"{speaker_node_id}->{event_id}:speaker_of", "source": speaker_node_id, "target": event_id, "type": "speaker_of"})
            if previous_event_id:
                edges.append({"id": f"{previous_event_id}->{event_id}:same_session_next", "source": previous_event_id, "target": event_id, "type": "same_session_next"})
            previous_event_id = event_id

            time_node_ids: List[str] = []
            profile_node_ids: List[str] = []
            status_node_ids: List[str] = []
            source_turn_node_ids: List[str] = []

            if time_display_value or time_value:
                time_node_id = f"{event_id}:time"
                add_node(
                    {
                        "id": time_node_id,
                        "type": "time",
                        "text": time_display_value or time_value,
                        "turn_index": int(turn_offset),
                        "time_display_value": time_display_value,
                        "time_value": time_value,
                        "time_granularity": time_granularity or "none",
                        "metadata": {
                            "time_display_value": time_display_value,
                            "time_value": time_value,
                            "time_granularity": time_granularity or "none",
                            "time_source": time_source,
                            "resolved_date": resolved_date,
                            "session_timestamp": timestamp,
                        },
                    }
                )
                edges.append({"id": f"{event_id}->{time_node_id}:time_of", "source": event_id, "target": time_node_id, "type": "time_of"})
                time_node_ids.append(time_node_id)

            if profile_type and profile_value:
                profile_node_id = f"{event_id}:profile:{profile_type}"
                add_node(
                    {
                        "id": profile_node_id,
                        "type": "profile",
                        "text": profile_value,
                        "turn_index": int(turn_offset),
                        "profile_type": profile_type,
                        "profile_value": profile_value,
                        "metadata": {"profile_type": profile_type, "profile_value": profile_value},
                    }
                )
                edges.append({"id": f"{event_id}->{profile_node_id}:profile_of", "source": event_id, "target": profile_node_id, "type": "profile_of"})
                profile_node_ids.append(profile_node_id)

            if target_status:
                status_node = _build_status_node(event_id, target_status, turn_index=turn_offset)
                add_node(status_node)
                status_node_id = clean_text(status_node["id"])
                edges.append({"id": f"{event_id}->{status_node_id}:status_of", "source": event_id, "target": status_node_id, "type": "status_of"})
                status_node_ids.append(status_node_id)

            source_turn_node_id = f"{event_id}:source_turn"
            add_node(
                {
                    "id": source_turn_node_id,
                    "type": "source_turn",
                    "text": turn_text,
                    "turn_index": int(turn_offset),
                    "metadata": {"speaker": speaker, "dia_id": dia_id},
                }
            )
            edges.append({"id": f"{event_id}->{source_turn_node_id}:supported_by_turn", "source": event_id, "target": source_turn_node_id, "type": "supported_by_turn"})
            source_turn_node_ids.append(source_turn_node_id)

            paths.extend(
                build_default_path_templates(
                    event_id=event_id,
                    speaker_node_id=speaker_node_id,
                    time_node_ids=time_node_ids,
                    profile_node_ids=profile_node_ids,
                    status_node_ids=status_node_ids,
                    source_turn_node_ids=source_turn_node_ids,
                )
            )
            event_catalog[event_id] = {
                "event_id": event_id,
                "speaker": speaker,
                "event_text": event_text,
                "event_signature": event_signature,
                "time_node_ids": list(time_node_ids),
                "time_granularity": time_granularity,
                "time_value": time_value,
                "time_display_value": time_display_value,
                "target_status": target_status,
                "session_name": session_name,
                "turn_index": int(turn_offset),
                "dia_id": dia_id,
                "profile_type": profile_type,
                "profile_value": profile_value,
            }

    graph_payload = {
        "conversation_id": conversation_id,
        "split": split,
        "nodes": nodes,
        "edges": edges,
        "paths": paths,
    }
    _ensure_graph_event_signatures(graph_payload)

    query_rows: List[Dict[str, Any]] = []
    for qa_index, qa in enumerate(list(sample.get("qa", []) or []), start=1):
        qa_payload = dict(qa or {})
        question = clean_text(qa_payload.get("question", ""))
        gold_evidence_ids = _locomo_evidence_ids(qa_payload.get("evidence", []))
        positive_event_ids = dedupe_texts(event_by_dia_id.get(item, "") for item in gold_evidence_ids)
        question_features = extract_question_features(question)
        answer_type = answer_type_from_query(
            question,
            category=qa_payload.get("category", ""),
            positive_event_ids=positive_event_ids,
            question_features=question_features,
            positive_event_payloads=[
                dict(event_catalog.get(event_id, {}) or {})
                for event_id in positive_event_ids
                if clean_text(event_id)
            ],
        )
        bundle = _candidate_bundle(
            question=question,
            question_features=question_features,
            event_catalog=event_catalog,
            positive_event_ids=positive_event_ids,
        )
        candidate_event_ids = bundle["candidate_event_ids"]
        hard_negative_event_ids = bundle["hard_negative_event_ids"]
        easy_negative_event_ids = bundle["easy_negative_event_ids"]
        negative_event_ids = bundle["negative_event_ids"]
        positive_path_ids = _positive_path_ids(
            paths,
            positive_event_ids,
            answer_type=answer_type,
            question_features=question_features,
        )
        positive_time_node_ids = _positive_time_node_ids(event_catalog, positive_event_ids)
        temporal_target = _temporal_target(
            question_features,
            event_catalog,
            positive_event_ids,
            positive_time_node_ids=positive_time_node_ids,
            answer_type=answer_type,
        )
        if answer_type == "abstain":
            positive_event_ids = []
            positive_path_ids = []
            positive_time_node_ids = []
        query_rows.append(
            {
                "conversation_id": conversation_id,
                "question_id": f"{conversation_id}:qa:{qa_index}",
                "question": question,
                "question_features": question_features,
                "candidate_event_ids": candidate_event_ids,
                "positive_event_ids": positive_event_ids,
                "positive_path_ids": positive_path_ids,
                "positive_time_node_ids": positive_time_node_ids,
                "hard_negative_event_ids": hard_negative_event_ids,
                "easy_negative_event_ids": easy_negative_event_ids,
                "negative_event_ids": negative_event_ids,
                "answer_targets": {"answer_type": answer_type},
                "temporal_target": temporal_target,
                "event_catalog_size": len(event_catalog),
                "metadata": {
                    "category": str(qa_payload.get("category", "")),
                    "split": split,
                    "evidence_count": len(gold_evidence_ids),
                    "dia_ids": gold_evidence_ids,
                },
            }
        )

    return {
        "graph": graph_payload,
        "queries": query_rows,
        "teacher_audit_rows": audit_rows,
        "turn_extraction_rows": turn_extraction_rows,
    }


def export_locomo_node_training_data(
    *,
    dataset_root: Path,
    output_dir: Path,
    teacher_client: TeacherClient,
    sample_limit: int = 0,
    dataset_glob: str = "locomo*.json",
    synthetic_train_max_queries_per_conversation: int = 0,
    shard_index: int = 0,
    shard_count: int = 1,
    audit_policy: str = AUDIT_POLICY_STRICT_PAUSE,
) -> Dict[str, Any]:
    shard_index, shard_count = _normalize_shard_params(shard_index=shard_index, shard_count=shard_count)
    audit_policy = clean_text(audit_policy) or AUDIT_POLICY_STRICT_PAUSE
    if audit_policy not in AUDIT_POLICY_VALUES:
        raise ValueError(f"Unsupported audit_policy: {audit_policy}")
    output_dir.mkdir(parents=True, exist_ok=True)
    graphs_dir = output_dir / "graphs"
    queries_dir = output_dir / "queries"
    turn_extraction_dir = output_dir / "turn_extraction"
    errors_path = output_dir / "errors.jsonl"
    teacher_audit_candidates_path = output_dir / "teacher_audit_candidates.jsonl"
    manifest_path = output_dir / "export_manifest.json"
    dataset_paths = _resolve_locomo_paths(dataset_root, dataset_glob=dataset_glob)
    primary_dataset_path = dataset_paths[0]

    health = teacher_client.healthcheck()
    if not bool(health.get("ok", False)):
        _log(
            "teacher_blocked",
            error_code=clean_text(health.get("error_code", "")) or "teacher_unhealthy",
            error_stage=clean_text(health.get("error_stage", "")) or "startup_healthcheck",
            detail=clean_text(health.get("detail", "")),
        )
        manifest = {
            "status": "blocked",
            "dataset_path": str(primary_dataset_path),
            "dataset_paths": [str(path) for path in dataset_paths],
            "output_dir": str(output_dir),
            "teacher_audit_candidates_path": str(teacher_audit_candidates_path),
            "shard": {
                "index": int(shard_index),
                "count": int(shard_count),
            },
            "error_code": clean_text(health.get("error_code", "")) or "teacher_unhealthy",
            "error_stage": clean_text(health.get("error_stage", "")) or "startup_healthcheck",
            "detail": clean_text(health.get("detail", "")),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        write_json(manifest_path, manifest)
        write_jsonl(errors_path, [])
        write_jsonl(teacher_audit_candidates_path, [])
        write_jsonl(turn_extraction_dir / "train.jsonl", [])
        write_jsonl(turn_extraction_dir / "val.jsonl", [])
        write_jsonl(turn_extraction_dir / "test.jsonl", [])
        return manifest

    all_rows: List[Dict[str, Any]] = []
    for dataset_path in dataset_paths:
        dataset_tag = dataset_path.stem if len(dataset_paths) > 1 else ""
        dataset_rows = _locomo_rows(dataset_path, sample_limit=0)
        for sample_index, sample in enumerate(dataset_rows, start=1):
            sample_payload = dict(sample)
            sample_payload["_dataset_path"] = str(dataset_path)
            sample_payload["_dataset_tag"] = dataset_tag
            sample_payload["_dataset_row_index"] = int(sample_index)
            all_rows.append(sample_payload)
    rows = all_rows[: max(0, int(sample_limit))] if int(sample_limit or 0) > 0 else all_rows
    conversation_ids = [
        _stable_conversation_id(
            sample,
            int(sample.get("_dataset_row_index", index) or index),
            dataset_tag=clean_text(sample.get("_dataset_tag", "")),
        )
        for index, sample in enumerate(rows, start=1)
    ]
    split_map = _conversation_split_map(conversation_ids)
    shard_entries = _select_shard_entries(rows, shard_index=shard_index, shard_count=shard_count)
    selected_conversation_ids = [
        _stable_conversation_id(
            sample,
            int(sample.get("_dataset_row_index", row_index) or row_index),
            dataset_tag=clean_text(sample.get("_dataset_tag", "")),
        )
        for row_index, sample in shard_entries
    ]
    split_queries: Dict[str, List[Dict[str, Any]]] = {"train": [], "val": [], "test": []}
    split_turn_extraction_rows: Dict[str, List[Dict[str, Any]]] = {"train": [], "val": [], "test": []}
    split_base_query_counts: Dict[str, int] = {"train": 0, "val": 0, "test": 0}
    split_synthetic_query_counts: Dict[str, int] = {"train": 0, "val": 0, "test": 0}
    error_rows: List[Dict[str, Any]] = []
    teacher_audit_rows: List[Dict[str, Any]] = []
    exported_graphs = 0
    paused_conversations = 0
    _log(
        "export_started",
        dataset_path=str(primary_dataset_path),
        dataset_paths=[str(path) for path in dataset_paths],
        output_dir=str(output_dir),
        sample_limit=int(sample_limit),
        conversations_total=len(shard_entries),
        conversations_universe=len(rows),
        shard_index=int(shard_index),
        shard_count=int(shard_count),
        synthetic_train_max_queries_per_conversation=int(synthetic_train_max_queries_per_conversation or 0),
        audit_policy=audit_policy,
        teacher_base_url=clean_text(getattr(teacher_client, "base_url", "")),
        teacher_model=clean_text(getattr(teacher_client, "model", "")),
    )

    for sample_index, (global_row_index, sample) in enumerate(shard_entries, start=1):
        conversation_id = _stable_conversation_id(
            sample,
            int(sample.get("_dataset_row_index", global_row_index) or global_row_index),
            dataset_tag=clean_text(sample.get("_dataset_tag", "")),
        )
        split = split_map.get(conversation_id, "train")
        _log(
            "conversation_started",
            index=sample_index,
            total=len(shard_entries),
            global_index=int(global_row_index),
            universe_total=len(rows),
            conversation_id=conversation_id,
            split=split,
            dataset_path=clean_text(sample.get("_dataset_path", "")),
        )
        try:
            exported = _conversation_graph_and_queries(
                conversation_id=conversation_id,
                sample=sample,
                split=split,
                teacher_client=teacher_client,
                audit_policy=audit_policy,
            )
        except TeacherTurnError as exc:
            paused_conversations += 1
            error_code = exc.error_code if exc.error_code in ERROR_CODES else "teacher_runtime_exception"
            teacher_audit_rows.extend(list(exc.audit_rows or []))
            _log(
                "conversation_paused",
                index=sample_index,
                total=len(shard_entries),
                global_index=int(global_row_index),
                universe_total=len(rows),
                conversation_id=conversation_id,
                split=split,
                error_code=error_code,
                error_stage=exc.error_stage,
                session_name=exc.session_name,
                turn_index=int(exc.turn_index),
                dia_id=exc.dia_id,
            )
            error_rows.append(
                {
                    "conversation_id": conversation_id,
                    "session_name": exc.session_name,
                    "turn_index": int(exc.turn_index),
                    "dia_id": exc.dia_id,
                    "error_code": error_code,
                    "error_stage": exc.error_stage,
                    "teacher_request_excerpt": exc.teacher_request_excerpt,
                    "teacher_raw_output": exc.teacher_raw_output,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
            )
            continue
        graph_payload = dict(exported["graph"])
        teacher_audit_rows.extend(list(exported.get("teacher_audit_rows", []) or []))
        split_turn_extraction_rows[split].extend(list(exported.get("turn_extraction_rows", []) or []))
        graph_payload["metadata"] = {
            **dict(graph_payload.get("metadata", {}) or {}),
            "source_dataset_path": clean_text(sample.get("_dataset_path", "")),
            "dataset_tag": clean_text(sample.get("_dataset_tag", "")),
        }
        base_queries = list(exported["queries"])
        synthetic_queries = _build_synthetic_query_rows(
            conversation_id=conversation_id,
            split=split,
            event_catalog=_event_catalog_from_graph(graph_payload),
            paths=list(graph_payload.get("paths", []) or []),
            existing_rows=base_queries,
            max_queries_per_conversation=int(synthetic_train_max_queries_per_conversation or 0),
        )
        write_json(graphs_dir / f"{conversation_id}.json", graph_payload)
        split_queries[split].extend(base_queries)
        split_queries[split].extend(synthetic_queries)
        split_base_query_counts[split] += len(base_queries)
        split_synthetic_query_counts[split] += len(synthetic_queries)
        exported_graphs += 1
        _log(
            "conversation_exported",
            index=sample_index,
            total=len(shard_entries),
            global_index=int(global_row_index),
            universe_total=len(rows),
            conversation_id=conversation_id,
            split=split,
            query_count=len(base_queries) + len(synthetic_queries),
            base_query_count=len(base_queries),
            synthetic_query_count=len(synthetic_queries),
            graphs_exported=exported_graphs,
        )

    for split, rows_for_split in split_queries.items():
        write_jsonl(queries_dir / f"{split}.jsonl", rows_for_split)
    for split, rows_for_split in split_turn_extraction_rows.items():
        write_jsonl(turn_extraction_dir / f"{split}.jsonl", rows_for_split)
    write_jsonl(errors_path, error_rows)
    write_jsonl(teacher_audit_candidates_path, teacher_audit_rows)
    manifest = {
        "status": "completed",
        "dataset_path": str(primary_dataset_path),
        "dataset_paths": [str(path) for path in dataset_paths],
        "output_dir": str(output_dir),
        "graphs_dir": str(graphs_dir),
        "queries_dir": str(queries_dir),
        "turn_extraction_dir": str(turn_extraction_dir),
        "counts": {
            "conversations_total": len(shard_entries),
            "conversations_universe": len(rows),
            "graphs_exported": exported_graphs,
            "paused_conversations": paused_conversations,
            "base_query_counts": dict(split_base_query_counts),
            "synthetic_query_counts": dict(split_synthetic_query_counts),
            "query_counts": {split: len(rows_for_split) for split, rows_for_split in split_queries.items()},
            "turn_extraction_counts": {split: len(rows_for_split) for split, rows_for_split in split_turn_extraction_rows.items()},
            "errors": len(error_rows),
            "teacher_audit_candidates": len(teacher_audit_rows),
            "teacher_audit_flagged": sum(
                1
                for row in teacher_audit_rows
                if list(dict(row.get("semantic_consistency", {}) or {}).get("issues", []) or [])
            ),
            "teacher_audit_repaired": sum(
                1 for row in teacher_audit_rows if clean_text(row.get("resolution_status", "")) == "repaired"
            ),
        },
        "teacher_audit_candidates_path": str(teacher_audit_candidates_path),
        "shard": {
            "index": int(shard_index),
            "count": int(shard_count),
            "selected_conversations": len(selected_conversation_ids),
            "selected_conversation_ids": list(selected_conversation_ids),
        },
        "synthetic": {
            "train_max_queries_per_conversation": int(synthetic_train_max_queries_per_conversation or 0),
            "kinds": list(SYNTHETIC_QUERY_KINDS),
        },
        "teacher": {
            "base_url": clean_text(getattr(teacher_client, "base_url", "")),
            "model": clean_text(getattr(teacher_client, "model", "")),
            "auth_mode": clean_text(getattr(teacher_client, "auth_mode", "")) or "none",
            "api_key_source": clean_text(getattr(teacher_client, "api_key_source", "")),
            "audit_policy": audit_policy,
        },
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(manifest_path, manifest)
    _log(
        "export_completed",
        output_dir=str(output_dir),
        graphs_exported=exported_graphs,
        paused_conversations=paused_conversations,
        errors=len(error_rows),
        teacher_audit_candidates=len(teacher_audit_rows),
        base_query_counts=dict(split_base_query_counts),
        synthetic_query_counts=dict(split_synthetic_query_counts),
        query_counts={split: len(rows_for_split) for split, rows_for_split in split_queries.items()},
        turn_extraction_counts={split: len(rows_for_split) for split, rows_for_split in split_turn_extraction_rows.items()},
    )
    return manifest


def augment_exported_locomo_node_training_data(
    *,
    data_dir: Path,
    output_dir: Path,
    synthetic_train_max_queries_per_conversation: int,
) -> Dict[str, Any]:
    if int(synthetic_train_max_queries_per_conversation or 0) <= 0:
        raise ValueError("synthetic_train_max_queries_per_conversation must be > 0 for augmentation")
    graphs_dir = data_dir / "graphs"
    queries_dir = data_dir / "queries"
    manifest_path = data_dir / "export_manifest.json"
    if not graphs_dir.exists() or not queries_dir.exists():
        raise FileNotFoundError(f"expected existing export directories under {data_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_graphs_dir = output_dir / "graphs"
    output_queries_dir = output_dir / "queries"
    output_turn_extraction_dir = output_dir / "turn_extraction"
    shutil.copytree(graphs_dir, output_graphs_dir, dirs_exist_ok=True)
    if (data_dir / "turn_extraction").exists():
        shutil.copytree(data_dir / "turn_extraction", output_turn_extraction_dir, dirs_exist_ok=True)
    if (data_dir / "errors.jsonl").exists():
        shutil.copyfile(data_dir / "errors.jsonl", output_dir / "errors.jsonl")
    if (data_dir / "teacher_audit_candidates.jsonl").exists():
        shutil.copyfile(data_dir / "teacher_audit_candidates.jsonl", output_dir / "teacher_audit_candidates.jsonl")

    split_queries: Dict[str, List[Dict[str, Any]]] = {}
    for split in ("train", "val", "test"):
        split_path = queries_dir / f"{split}.jsonl"
        split_queries[split] = [
            dict(json.loads(line))
            for line in split_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ] if split_path.exists() else []
    base_train_rows = list(split_queries["train"])
    by_conversation: Dict[str, List[Dict[str, Any]]] = {}
    for row in base_train_rows:
        by_conversation.setdefault(clean_text(row.get("conversation_id", "")), []).append(dict(row))

    synthetic_count = 0
    signature_backfill_count = 0
    for graph_path in sorted(output_graphs_dir.glob("*.json")):
        graph = dict(json.loads(graph_path.read_text(encoding="utf-8")))
        signature_backfill_count += _ensure_graph_event_signatures(graph)
        write_json(graph_path, graph)
        conversation_id = clean_text(graph.get("conversation_id", ""))
        split = clean_text(graph.get("split", "")) or "train"
        synthetic_rows = _build_synthetic_query_rows(
            conversation_id=conversation_id,
            split=split,
            event_catalog=_event_catalog_from_graph(graph),
            paths=list(graph.get("paths", []) or []),
            existing_rows=by_conversation.get(conversation_id, []),
            max_queries_per_conversation=int(synthetic_train_max_queries_per_conversation or 0),
        )
        if synthetic_rows:
            split_queries["train"].extend(synthetic_rows)
            synthetic_count += len(synthetic_rows)

    for split, rows_for_split in split_queries.items():
        write_jsonl(output_queries_dir / f"{split}.jsonl", rows_for_split)

    base_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    counts = dict(base_manifest.get("counts", {}) or {})
    base_query_counts = dict(counts.get("base_query_counts", {}) or {})
    if not base_query_counts:
        base_query_counts = {
            "train": len(base_train_rows),
            "val": len(split_queries["val"]),
            "test": len(split_queries["test"]),
        }
    synthetic_query_counts = dict(counts.get("synthetic_query_counts", {}) or {})
    synthetic_query_counts["train"] = int(synthetic_query_counts.get("train", 0) or 0) + synthetic_count
    synthetic_query_counts.setdefault("val", 0)
    synthetic_query_counts.setdefault("test", 0)
    counts["base_query_counts"] = base_query_counts
    counts["synthetic_query_counts"] = synthetic_query_counts
    counts["query_counts"] = {split: len(rows_for_split) for split, rows_for_split in split_queries.items()}
    updated_manifest = {
        **base_manifest,
        "output_dir": str(output_dir),
        "graphs_dir": str(output_graphs_dir),
        "queries_dir": str(output_queries_dir),
        "turn_extraction_dir": str(output_turn_extraction_dir),
        "teacher_audit_candidates_path": str(output_dir / "teacher_audit_candidates.jsonl"),
        "counts": counts,
        "synthetic": {
            "train_max_queries_per_conversation": int(synthetic_train_max_queries_per_conversation or 0),
            "kinds": list(SYNTHETIC_QUERY_KINDS),
            "augmented_from": str(data_dir),
        },
        "graph_event_signature_backfill_count": int(signature_backfill_count),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(output_dir / "export_manifest.json", updated_manifest)
    _log(
        "augmentation_completed",
        data_dir=str(data_dir),
        output_dir=str(output_dir),
        synthetic_train_queries_added=synthetic_count,
        graph_event_signature_backfill_count=signature_backfill_count,
        query_counts=counts["query_counts"],
    )
    return updated_manifest


def _event_id_by_dia_id_from_graph(graph: Mapping[str, Any]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for node in list(graph.get("nodes", []) or []):
        if not isinstance(node, Mapping):
            continue
        if clean_text(node.get("type", "")) != "event":
            continue
        metadata = dict(node.get("metadata", {}) or {})
        dia_id = clean_text(node.get("dia_id", metadata.get("dia_id", "")))
        event_id = clean_text(node.get("id", ""))
        if dia_id and event_id:
            mapping[dia_id] = event_id
    return mapping


def _query_rows_from_graph_and_sample(
    *,
    conversation_id: str,
    split: str,
    graph: Mapping[str, Any],
    sample: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    event_catalog = _event_catalog_from_graph(graph)
    paths = list(graph.get("paths", []) or [])
    event_by_dia_id = _event_id_by_dia_id_from_graph(graph)
    query_rows: List[Dict[str, Any]] = []
    for qa_index, qa in enumerate(list(sample.get("qa", []) or []), start=1):
        qa_payload = dict(qa or {})
        question = clean_text(qa_payload.get("question", ""))
        gold_evidence_ids = _locomo_evidence_ids(qa_payload.get("evidence", []))
        positive_event_ids = dedupe_texts(event_by_dia_id.get(item, "") for item in gold_evidence_ids)
        question_features = extract_question_features(question)
        answer_type = answer_type_from_query(
            question,
            category=qa_payload.get("category", ""),
            positive_event_ids=positive_event_ids,
            question_features=question_features,
            positive_event_payloads=[
                dict(event_catalog.get(event_id, {}) or {})
                for event_id in positive_event_ids
                if clean_text(event_id)
            ],
        )
        bundle = _candidate_bundle(
            question=question,
            question_features=question_features,
            event_catalog=event_catalog,
            positive_event_ids=positive_event_ids,
        )
        positive_path_ids = _positive_path_ids(
            paths,
            positive_event_ids,
            answer_type=answer_type,
            question_features=question_features,
        )
        positive_time_node_ids = _positive_time_node_ids(event_catalog, positive_event_ids)
        temporal_target = _temporal_target(
            question_features,
            event_catalog,
            positive_event_ids,
            positive_time_node_ids=positive_time_node_ids,
            answer_type=answer_type,
        )
        if answer_type == "abstain":
            positive_event_ids = []
            positive_path_ids = []
            positive_time_node_ids = []
        query_rows.append(
            {
                "conversation_id": conversation_id,
                "question_id": f"{conversation_id}:qa:{qa_index}",
                "question": question,
                "question_features": question_features,
                "candidate_event_ids": bundle["candidate_event_ids"],
                "positive_event_ids": positive_event_ids,
                "positive_path_ids": positive_path_ids,
                "positive_time_node_ids": positive_time_node_ids,
                "hard_negative_event_ids": bundle["hard_negative_event_ids"],
                "easy_negative_event_ids": bundle["easy_negative_event_ids"],
                "negative_event_ids": bundle["negative_event_ids"],
                "answer_targets": {"answer_type": answer_type},
                "temporal_target": temporal_target,
                "event_catalog_size": len(event_catalog),
                "metadata": {
                    "category": str(qa_payload.get("category", "")),
                    "split": split,
                    "evidence_count": len(gold_evidence_ids),
                    "dia_ids": gold_evidence_ids,
                },
            }
        )
    return query_rows


def rebuild_exported_locomo_queries(
    *,
    data_dir: Path,
    output_dir: Path,
    synthetic_train_max_queries_per_conversation: int | None = None,
) -> Dict[str, Any]:
    graphs_dir = data_dir / "graphs"
    queries_dir = data_dir / "queries"
    manifest_path = data_dir / "export_manifest.json"
    if not graphs_dir.exists() or not queries_dir.exists() or not manifest_path.exists():
        raise FileNotFoundError(f"expected existing export with graphs/ queries/ export_manifest.json under {data_dir}")
    base_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset_paths = [Path(path) for path in list(base_manifest.get("dataset_paths", []) or []) if clean_text(path)]
    if not dataset_paths:
        dataset_path = clean_text(base_manifest.get("dataset_path", ""))
        if dataset_path:
            dataset_paths = [Path(dataset_path)]
    if not dataset_paths:
        raise ValueError(f"dataset_paths missing from {manifest_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_graphs_dir = output_dir / "graphs"
    output_queries_dir = output_dir / "queries"
    shutil.copytree(graphs_dir, output_graphs_dir, dirs_exist_ok=True)
    if (data_dir / "errors.jsonl").exists():
        shutil.copyfile(data_dir / "errors.jsonl", output_dir / "errors.jsonl")
    if (data_dir / "teacher_audit_candidates.jsonl").exists():
        shutil.copyfile(data_dir / "teacher_audit_candidates.jsonl", output_dir / "teacher_audit_candidates.jsonl")

    all_rows: List[Dict[str, Any]] = []
    for dataset_path in dataset_paths:
        dataset_tag = dataset_path.stem if len(dataset_paths) > 1 else ""
        dataset_rows = _locomo_rows(dataset_path, sample_limit=0)
        for sample_index, sample in enumerate(dataset_rows, start=1):
            sample_payload = dict(sample)
            sample_payload["_dataset_path"] = str(dataset_path)
            sample_payload["_dataset_tag"] = dataset_tag
            sample_payload["_dataset_row_index"] = int(sample_index)
            all_rows.append(sample_payload)

    conversation_to_sample: Dict[str, Dict[str, Any]] = {}
    for sample_index, sample in enumerate(all_rows, start=1):
        conversation_id = _stable_conversation_id(
            sample,
            int(sample.get("_dataset_row_index", sample_index) or sample_index),
            dataset_tag=clean_text(sample.get("_dataset_tag", "")),
        )
        conversation_to_sample[conversation_id] = dict(sample)

    if synthetic_train_max_queries_per_conversation is None:
        synthetic_train_max_queries_per_conversation = int(
            dict(base_manifest.get("synthetic", {}) or {}).get("train_max_queries_per_conversation", 0) or 0
        )

    split_queries: Dict[str, List[Dict[str, Any]]] = {"train": [], "val": [], "test": []}
    split_base_query_counts: Dict[str, int] = {"train": 0, "val": 0, "test": 0}
    split_synthetic_query_counts: Dict[str, int] = {"train": 0, "val": 0, "test": 0}
    rebuilt_graphs = 0
    signature_backfill_count = 0

    for graph_path in sorted(output_graphs_dir.glob("*.json")):
        graph = dict(json.loads(graph_path.read_text(encoding="utf-8")))
        signature_backfill_count += _ensure_graph_event_signatures(graph)
        write_json(graph_path, graph)
        conversation_id = clean_text(graph.get("conversation_id", ""))
        split = clean_text(graph.get("split", "")) or "train"
        sample = conversation_to_sample.get(conversation_id)
        if sample is None:
            raise KeyError(f"sample missing for graph conversation_id={conversation_id}")
        base_queries = _query_rows_from_graph_and_sample(
            conversation_id=conversation_id,
            split=split,
            graph=graph,
            sample=sample,
        )
        synthetic_queries = _build_synthetic_query_rows(
            conversation_id=conversation_id,
            split=split,
            event_catalog=_event_catalog_from_graph(graph),
            paths=list(graph.get("paths", []) or []),
            existing_rows=base_queries,
            max_queries_per_conversation=int(synthetic_train_max_queries_per_conversation or 0),
        )
        split_queries[split].extend(base_queries)
        split_queries[split].extend(synthetic_queries)
        split_base_query_counts[split] += len(base_queries)
        split_synthetic_query_counts[split] += len(synthetic_queries)
        rebuilt_graphs += 1

    for split, rows_for_split in split_queries.items():
        write_jsonl(output_queries_dir / f"{split}.jsonl", rows_for_split)

    updated_manifest = {
        **base_manifest,
        "status": "completed",
        "output_dir": str(output_dir),
        "graphs_dir": str(output_graphs_dir),
        "queries_dir": str(output_queries_dir),
        "teacher_audit_candidates_path": str(output_dir / "teacher_audit_candidates.jsonl"),
        "counts": {
            **dict(base_manifest.get("counts", {}) or {}),
            "graphs_exported": int(rebuilt_graphs),
            "base_query_counts": dict(split_base_query_counts),
            "synthetic_query_counts": dict(split_synthetic_query_counts),
            "query_counts": {split: len(rows_for_split) for split, rows_for_split in split_queries.items()},
        },
        "synthetic": {
            "train_max_queries_per_conversation": int(synthetic_train_max_queries_per_conversation or 0),
            "kinds": list(SYNTHETIC_QUERY_KINDS),
            "rebuilt_from": str(data_dir),
        },
        "graph_event_signature_backfill_count": int(signature_backfill_count),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rebuild_source": str(data_dir),
    }
    write_json(output_dir / "export_manifest.json", updated_manifest)
    _log(
        "query_rebuild_completed",
        data_dir=str(data_dir),
        output_dir=str(output_dir),
        graphs_rebuilt=rebuilt_graphs,
        graph_event_signature_backfill_count=signature_backfill_count,
        query_counts=updated_manifest["counts"]["query_counts"],
    )
    return updated_manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export LoCoMo node-memory training data with required teacher annotations.")
    parser.add_argument("--dataset-root", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--augment-from-data-dir", type=Path, default=None)
    parser.add_argument("--rebuild-from-data-dir", type=Path, default=None)
    parser.add_argument("--teacher-base-url", default=os.environ.get("TEACHER_BASE_URL", DEFAULT_TEACHER_BASE_URL))
    parser.add_argument("--teacher-model", default=os.environ.get("TEACHER_MODEL", DEFAULT_TEACHER_MODEL))
    parser.add_argument("--teacher-api-key", default="")
    parser.add_argument("--teacher-api-key-env", default="")
    parser.add_argument("--teacher-enable-thinking", action="store_true")
    parser.add_argument("--teacher-max-tokens", type=int, default=DEFAULT_TEACHER_MAX_TOKENS)
    parser.add_argument("--teacher-repair-max-tokens", type=int, default=DEFAULT_TEACHER_REPAIR_MAX_TOKENS)
    parser.add_argument("--sample-limit", type=int, default=0)
    parser.add_argument("--dataset-glob", default="locomo*.json")
    parser.add_argument("--synthetic-train-max-queries-per-conversation", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TEACHER_TIMEOUT_SECONDS)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--audit-policy", choices=sorted(AUDIT_POLICY_VALUES), default=AUDIT_POLICY_STRICT_PAUSE)
    args = parser.parse_args(list(argv) if argv is not None else None)
    started_at = time.perf_counter()
    if args.augment_from_data_dir is not None and args.rebuild_from_data_dir is not None:
        raise SystemExit("--augment-from-data-dir and --rebuild-from-data-dir are mutually exclusive")
    if args.augment_from_data_dir is not None:
        manifest = augment_exported_locomo_node_training_data(
            data_dir=Path(args.augment_from_data_dir),
            output_dir=Path(args.output_dir),
            synthetic_train_max_queries_per_conversation=int(args.synthetic_train_max_queries_per_conversation),
        )
    elif args.rebuild_from_data_dir is not None:
        manifest = rebuild_exported_locomo_queries(
            data_dir=Path(args.rebuild_from_data_dir),
            output_dir=Path(args.output_dir),
            synthetic_train_max_queries_per_conversation=int(args.synthetic_train_max_queries_per_conversation),
        )
    else:
        if args.dataset_root is None:
            raise SystemExit("--dataset-root is required unless --augment-from-data-dir or --rebuild-from-data-dir is used")
        teacher_client = TeacherClient(
            base_url=str(args.teacher_base_url),
            model=str(args.teacher_model),
            api_key=str(args.teacher_api_key),
            api_key_env=str(args.teacher_api_key_env),
            timeout_seconds=float(args.timeout_seconds),
            enable_thinking=bool(args.teacher_enable_thinking),
            annotation_max_tokens=int(args.teacher_max_tokens),
            repair_max_tokens=int(args.teacher_repair_max_tokens),
        )
        manifest = export_locomo_node_training_data(
            dataset_root=Path(args.dataset_root),
            output_dir=Path(args.output_dir),
            teacher_client=teacher_client,
            sample_limit=int(args.sample_limit),
            dataset_glob=str(args.dataset_glob),
            synthetic_train_max_queries_per_conversation=int(args.synthetic_train_max_queries_per_conversation),
            shard_index=int(args.shard_index),
            shard_count=int(args.shard_count),
            audit_policy=str(args.audit_policy),
        )
    manifest["elapsed_seconds"] = round(time.perf_counter() - started_at, 3)
    write_json(Path(args.output_dir) / "export_manifest.json", manifest)
    return 1 if clean_text(manifest.get("status", "")) == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
