from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any


class ReceiptValidationError(ValueError):
    """Raised when the Memory API response is not a supported contract."""

    code = "invalid_response_contract"


TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
KNOWN_JOB_STATUSES = frozenset({"pending", "queued", "running", *TERMINAL_STATUSES})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RECEIPT_CONTRACT_SCHEMA = "tmcra.receipts.v1"


def _watermarks(value: Any) -> dict[str, Any]:
    """Normalize either a nested or flat service watermark projection."""

    keys = (
        "source_event_seq",
        "promoted_event_seq",
        "indexed_event_seq",
        "source_raw_token_estimate",
    )
    found: dict[str, int] = {}

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key in keys:
                candidate = item.get(key)
                if key not in found and isinstance(candidate, int) and not isinstance(candidate, bool):
                    found[key] = candidate
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    normalized = {key: found.get(key) for key in keys}
    normalized["available"] = any(item is not None for item in normalized.values())
    return normalized


def _receipt_status(status: str) -> str:
    return status if status in KNOWN_JOB_STATUSES else "submitted"


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReceiptValidationError(f"{label} must be an object")
    return dict(value)


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReceiptValidationError(f"{label} must be a non-empty string")
    return value


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ReceiptValidationError(f"{label} must be an integer >= {minimum}")
    return value


def _number(value: Any, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReceiptValidationError(f"{label} must be a number")
    return value


def validate_job(payload: Any) -> dict[str, Any]:
    """Validate and normalize the public JobView contract."""
    value = _object(payload, "job")
    for field in ("job_id", "tenant_id", "scope_name", "job_type", "status", "status_url"):
        _string(value.get(field), f"job.{field}")
    status = value["status"]
    if status not in KNOWN_JOB_STATUSES:
        raise ReceiptValidationError(f"job.status has unsupported value: {status}")
    _integer(value.get("attempts"), "job.attempts")
    _number(value.get("created_at"), "job.created_at")
    _number(value.get("updated_at"), "job.updated_at")
    normalized = dict(value)
    normalized["schema_version"] = "tmcra.mcp.job-receipt.v1"
    normalized["contract_schema_version"] = RECEIPT_CONTRACT_SCHEMA
    normalized["receipt_type"] = "job"
    normalized["observed_status"] = status
    normalized["submitted_status"] = "submitted"
    normalized["final_status"] = status if status in TERMINAL_STATUSES else None
    normalized["submitted"] = True
    normalized["final"] = status in TERMINAL_STATUSES
    normalized["watermarks"] = _watermarks(value)
    return normalized


def validate_recall(payload: Any) -> dict[str, Any]:
    """Validate the complete recall response before exposing evidence to a host."""
    value = _object(payload, "recall response")
    for field in ("query_id", "scope_name", "index_job_id"):
        _string(value.get(field), f"recall.{field}")

    route = _object(value.get("evidence_route"), "recall.evidence_route")
    requested = _string(route.get("requested"), "recall.evidence_route.requested")
    selected = _string(route.get("selected"), "recall.evidence_route.selected")
    if selected not in {"raw", "compiled"}:
        raise ReceiptValidationError("recall.evidence_route.selected must be raw or compiled")
    reasons = route.get("reasons")
    if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
        raise ReceiptValidationError("recall.evidence_route.reasons must be a string array")

    prompt = _object(value.get("prompt_evidence"), "recall.prompt_evidence")
    for field in ("schema_version", "format", "mode", "content_sha256", "trust_boundary"):
        _string(prompt.get(field), f"recall.prompt_evidence.{field}")
    if not isinstance(prompt.get("content"), str):
        raise ReceiptValidationError("recall.prompt_evidence.content must be a string")
    if prompt["format"] not in {"text/plain", "application/json"}:
        raise ReceiptValidationError("recall.prompt_evidence.format is unsupported")
    if prompt["mode"] not in {"raw_hierarchical", "compiled_evidence_packet"}:
        raise ReceiptValidationError("recall.prompt_evidence.mode is unsupported")
    if not SHA256_PATTERN.fullmatch(prompt["content_sha256"]):
        raise ReceiptValidationError("recall.prompt_evidence.content_sha256 is invalid")
    if prompt["content_character_count"] != len(prompt["content"]):
        raise ReceiptValidationError("recall.prompt_evidence.content_character_count is inconsistent")
    if not isinstance(prompt.get("source_text_verbatim"), bool):
        raise ReceiptValidationError("recall.prompt_evidence.source_text_verbatim must be boolean")

    evidence = _object(value.get("evidence"), "recall.evidence")
    normalized = dict(value)
    normalized["evidence_route"] = {
        "requested": requested,
        "selected": selected,
        "reasons": reasons,
    }
    normalized["prompt_evidence"] = prompt
    normalized["evidence"] = evidence
    normalized["schema_version"] = "tmcra.mcp.recall-receipt.v1"
    normalized["contract_schema_version"] = RECEIPT_CONTRACT_SCHEMA
    normalized["receipt_type"] = "recall"
    normalized["submitted_status"] = "completed"
    normalized["final_status"] = "completed"
    normalized["submitted"] = True
    normalized["final"] = True
    normalized["status_url"] = None
    normalized["watermarks"] = _watermarks(value)
    normalized["evidence_sha256"] = hashlib.sha256(
        prompt["content"].encode("utf-8")
    ).hexdigest()
    if normalized["evidence_sha256"] != prompt["content_sha256"]:
        raise ReceiptValidationError("recall prompt evidence hash does not match content")
    return normalized


def validate_bulk_ingest(payload: Any) -> dict[str, Any]:
    """Validate a 202 BulkIngestResponse and return an ingest receipt."""
    value = _object(payload, "ingest response")
    scope_name = _string(value.get("scope_name"), "ingest.scope_name")
    jobs = value.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ReceiptValidationError("ingest.jobs must be a non-empty array")
    normalized_jobs = [validate_job(job) for job in jobs]
    if len({job["job_id"] for job in normalized_jobs}) != len(normalized_jobs):
        raise ReceiptValidationError("ingest.jobs contains duplicate job IDs")
    if any(job["scope_name"] != scope_name for job in normalized_jobs):
        raise ReceiptValidationError("ingest job scope does not match response scope")
    statuses = {job["status"] for job in normalized_jobs}
    all_terminal = statuses.issubset(TERMINAL_STATUSES)
    if statuses <= {"succeeded"}:
        status = "succeeded"
    elif statuses <= {"cancelled"}:
        status = "cancelled"
    elif all_terminal and statuses & {"failed", "cancelled"}:
        status = "failed"
    else:
        status = "submitted"
    receipt = {
        "schema_version": "tmcra.mcp.ingest-receipt.v1",
        "contract_schema_version": RECEIPT_CONTRACT_SCHEMA,
        "receipt_type": "ingest",
        "scope_name": scope_name,
        "jobs": normalized_jobs,
        "job_ids": [job["job_id"] for job in normalized_jobs],
        "status": status,
        "submitted_status": "submitted",
        "observed_status": status,
        "final_status": status if status in TERMINAL_STATUSES else None,
        "submitted": True,
        "final": status in TERMINAL_STATUSES,
        "watermarks": _watermarks(value),
    }
    if len(normalized_jobs) == 1:
        receipt["job_id"] = normalized_jobs[0]["job_id"]
        receipt["status_url"] = normalized_jobs[0]["status_url"]
    return receipt


def validate_receipt_job_list(payload: Any) -> list[dict[str, Any]]:
    value = _object(payload, "job list")
    jobs = value.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ReceiptValidationError("job list must contain jobs")
    return [validate_job(job) for job in jobs]
