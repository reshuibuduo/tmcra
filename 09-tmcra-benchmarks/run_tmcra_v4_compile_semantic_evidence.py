#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from run_tmcra_v4_build import DEFAULT_WRITER_ENV, _key_pool, _load_shell_environment
from tmcra_v4_evidence_operations import build_evidence_catalog
from tmcra_v4_semantic_evidence import (
    compile_semantic_evidence_packet,
    normalize_task_contract_sources,
    task_contract_payload,
    task_contract_source_review_reasons,
    validate_resolution_plan,
    requires_exhaustive_resolution_review,
    validate_task_contract,
)
from tmcra_v4_semantic_planner import (
    RESOLUTION_PROMPT_VERSION,
    COMPLETENESS_REVIEW_PROMPT_VERSION,
    TASK_PROMPT_VERSION,
    SemanticJsonPlanner,
    SemanticPlannerError,
)


COMPILER_VERSION = "tmcra-semantic-evidence-compiler-2026-07-13.8"
TASK_STAGE_VERSION = "tmcra-semantic-task-stage-2026-07-13.1"
RESOLUTION_STAGE_VERSION = "tmcra-semantic-resolution-stage-2026-07-13.4"


class SemanticCompileError(RuntimeError):
    pass


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    temporary.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    temporary.write_text("".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    os.replace(temporary, path)


def _identity(row: Mapping[str, Any]) -> str:
    payload = {
        "task_stage_version": TASK_STAGE_VERSION,
        "resolution_stage_version": RESOLUTION_STAGE_VERSION,
        "compiler_version": COMPILER_VERSION,
        "task_prompt_version": TASK_PROMPT_VERSION,
        "resolution_prompt_version": RESOLUTION_PROMPT_VERSION,
        "completeness_review_prompt_version": COMPLETENESS_REVIEW_PROMPT_VERSION,
        "question_id": row.get("question_id"),
        "question": row.get("question"),
        "question_date": row.get("question_date"),
        "dialogue_state": row.get("dialogue_state"),
        "evidence_windows": row.get("evidence_windows"),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _task_identity(row: Mapping[str, Any]) -> str:
    payload = {
        "task_stage_version": TASK_STAGE_VERSION,
        "task_prompt_version": TASK_PROMPT_VERSION,
        "question_id": row.get("question_id"),
        "question": row.get("question"),
        "question_date": row.get("question_date"),
        "dialogue_state": row.get("dialogue_state"),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _resolution_identity(row: Mapping[str, Any], contract: Mapping[str, Any]) -> str:
    payload = {
        "resolution_stage_version": RESOLUTION_STAGE_VERSION,
        "resolution_prompt_version": RESOLUTION_PROMPT_VERSION,
        "completeness_review_prompt_version": COMPLETENESS_REVIEW_PROMPT_VERSION,
        "task_contract": dict(contract),
        "question_id": row.get("question_id"),
        "question": row.get("question"),
        "question_date": row.get("question_date"),
        "evidence_windows": row.get("evidence_windows"),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _cost_cny(calls: Sequence[Mapping[str, Any]]) -> float | None:
    if any(call.get("provider") != "deepseek" for call in calls):
        return None
    total = 0.0
    for call in calls:
        usage = dict(call.get("usage") or {})
        total += (
            int(usage.get("prompt_cache_miss_tokens", 0)) * 3.0
            + int(usage.get("completion_tokens", 0)) * 6.0
            + int(usage.get("prompt_cache_hit_tokens", 0)) * 0.025
        ) / 1_000_000.0
    return round(total, 8)


def _dedupe_calls(calls: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, call in enumerate(calls):
        current = dict(call)
        identity = _text(current.get("physical_call_id")) or (
            f"{_text(current.get('stage'))}:{_text(current.get('request_sha256'))}:"
            f"{_text(current.get('response_sha256'))}:{index}"
        )
        if identity in seen:
            continue
        seen.add(identity)
        output.append(current)
    return output


def _call_with_schema_repair(
    initial: Callable[[], tuple[dict[str, Any], dict[str, Any]]],
    repair: Callable[[Mapping[str, Any]], tuple[dict[str, Any], dict[str, Any]]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        value, metadata = initial()
        return value, [metadata]
    except SemanticPlannerError as exc:
        metadata = dict(exc.metadata)
        if metadata.get("status") != "completed" or not metadata.get("validation_error"):
            raise
        context = {
            "validation_error": metadata["validation_error"],
            "invalid_output": metadata.get("raw_response"),
        }
        metadata.pop("raw_response", None)
        try:
            value, repaired_metadata = repair(context)
        except SemanticPlannerError as repair_exc:
            repair_exc.metadata["prior_calls"] = [metadata]
            raise
        return value, [metadata, repaired_metadata]


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile immutable Source evidence through semantic task and resolution contracts")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--qid-list", type=Path)
    parser.add_argument("--writer-env", type=Path, default=DEFAULT_WRITER_ENV)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--planner-provider", default="deepseek")
    parser.add_argument("--planner-model")
    parser.add_argument("--planner-base-url")
    parser.add_argument("--planner-key-file", type=Path)
    args = parser.parse_args()
    if args.workers <= 0 or args.max_tokens <= 0:
        raise SemanticCompileError("workers and max tokens must be positive")
    rows = _read_jsonl(args.evidence.resolve())
    if args.qid_list:
        qids = [line.strip() for line in args.qid_list.read_text(encoding="utf-8").splitlines() if line.strip()]
        by_qid = {_text(row.get("question_id")): row for row in rows}
        if not qids or len(qids) != len(set(qids)) or any(qid not in by_qid for qid in qids):
            raise SemanticCompileError("qid list is empty, duplicated, or absent from evidence")
        rows = [by_qid[qid] for qid in qids]
    if args.planner_provider == "deepseek":
        environment = _load_shell_environment(args.writer_env.resolve())
        keys = _key_pool(environment)
        base_url = args.planner_base_url or environment.get("TMCRA_DEEPSEEK_WRITER_BASE_URL") or environment.get("TMCRA_WRITER_BASE_URL") or "https://api.deepseek.com/v1"
        model = args.planner_model or environment.get("TMCRA_WRITER_REVIEWER_MODEL") or environment.get("TMCRA_DEEPSEEK_PRO_MODEL") or "deepseek-v4-pro"
    else:
        if not args.planner_key_file or not args.planner_key_file.is_file():
            raise SemanticCompileError("non-DeepSeek planners require --planner-key-file")
        key = args.planner_key_file.read_text(encoding="utf-8").strip()
        if not key:
            raise SemanticCompileError("planner key file is empty")
        keys = [key]
        base_url = args.planner_base_url or (
            "https://api.xiaomimimo.com/v1" if args.planner_provider == "xiaomi_mimo" else ""
        )
        model = args.planner_model or (
            "mimo-v2.5" if args.planner_provider == "xiaomi_mimo" else ""
        )
        if not base_url or not model:
            raise SemanticCompileError(
                "custom planner providers require --planner-base-url and --planner-model"
            )
    out_dir = args.out_dir.resolve()
    journal_dir = out_dir / "rows"
    journal_dir.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    def compile_one(index: int, row: Mapping[str, Any]) -> dict[str, Any]:
        qid = _text(row.get("question_id"))
        identity = _identity(row)
        task_identity = _task_identity(row)
        artifact = journal_dir / f"{index:06d}_{qid}.json"
        failure_artifact = journal_dir / f"{index:06d}_{qid}.failure.json"
        if artifact.is_file():
            saved = json.loads(artifact.read_text(encoding="utf-8"))
            if saved.get("question_id") != qid or saved.get("input_sha256") != identity:
                raise SemanticCompileError(f"{qid}: persisted semantic compiler identity mismatch")
            return saved
        planner = SemanticJsonPlanner(
            base_url=base_url,
            api_keys=[keys[index % len(keys)]],
            timeout=args.timeout,
            max_tokens=args.max_tokens,
            model=model,
            provider=args.planner_provider,
        )
        contract_artifact = journal_dir / f"{index:06d}_{qid}.task_contract.json"
        resolution_artifact = journal_dir / f"{index:06d}_{qid}.resolution.json"
        prior_failure: dict[str, Any] = {}
        if failure_artifact.is_file():
            prior_failure = json.loads(failure_artifact.read_text(encoding="utf-8"))
            if prior_failure.get("question_id") != qid or prior_failure.get("input_sha256") != identity:
                raise SemanticCompileError(f"{qid}: persisted failure identity mismatch")
        calls: list[dict[str, Any]] = [
            dict(item) for item in prior_failure.get("completed_planner_calls") or []
        ]
        try:
            if contract_artifact.is_file():
                saved_contract = json.loads(contract_artifact.read_text(encoding="utf-8"))
                if saved_contract.get("question_id") != qid or saved_contract.get("stage_input_sha256") != task_identity:
                    raise SemanticCompileError(f"{qid}: persisted task contract identity mismatch")
                contract = validate_task_contract(saved_contract["task_contract"], task_contract_payload(row))
                contract_calls = [dict(item) for item in saved_contract.get("planner_calls") or []]
                calls.extend(contract_calls)
            else:
                contract, contract_calls = _call_with_schema_repair(
                    lambda: planner.plan_task_contract(row),
                    lambda context: planner.plan_task_contract(row, repair_context=context),
                )
                calls.extend(contract_calls)
                contract, source_normalization_warnings = normalize_task_contract_sources(contract)
                source_review_reasons = task_contract_source_review_reasons(contract)
                if source_review_reasons:
                    contract, review_metadata = planner.plan_task_contract(
                        row,
                        repair_context={
                            "initial_contract": contract,
                            "review_reason": "duplicate semantic content across memory and query_context premises",
                            "source_review_findings": source_review_reasons,
                        },
                    )
                    contract_calls.append(review_metadata)
                    calls.append(review_metadata)
                    contract, review_normalization_warnings = normalize_task_contract_sources(contract)
                    source_normalization_warnings.extend(review_normalization_warnings)
                    remaining_reasons = task_contract_source_review_reasons(contract)
                    if remaining_reasons:
                        source_normalization_warnings.append(
                            "unresolved_source_review:" + " | ".join(remaining_reasons)
                        )
                with lock:
                    _atomic_json(
                        contract_artifact,
                        {
                            "question_id": qid,
                            "input_sha256": identity,
                            "stage_input_sha256": task_identity,
                            "status": "completed",
                            "task_contract": contract,
                            "planner_calls": contract_calls,
                            "source_normalization_warnings": source_normalization_warnings,
                        },
                    )
            catalog = build_evidence_catalog(row)
            resolution_identity = _resolution_identity(row, contract)
            if resolution_artifact.is_file():
                saved_resolution = json.loads(resolution_artifact.read_text(encoding="utf-8"))
                if saved_resolution.get("question_id") != qid or saved_resolution.get("stage_input_sha256") != resolution_identity:
                    raise SemanticCompileError(f"{qid}: persisted resolution identity mismatch")
                resolution = validate_resolution_plan(saved_resolution["resolution_plan"], contract, catalog)
                resolution_calls = [dict(item) for item in saved_resolution.get("planner_calls") or []]
            else:
                failing_planner = dict(prior_failure.get("failing_planner") or {})
                prior_resolution_calls = [
                    dict(item)
                    for item in [
                        *(failing_planner.get("prior_calls") or []),
                        {
                            key: value
                            for key, value in failing_planner.items()
                            if key not in {"prior_calls", "raw_response"}
                        },
                    ]
                    if item and item.get("stage") == "semantic_evidence_resolver"
                ]
                prior_resolution_calls.extend(
                    dict(item)
                    for item in prior_failure.get("completed_planner_calls") or []
                    if item.get("stage") == "semantic_evidence_resolver"
                )
                prior_resolution_calls = _dedupe_calls(prior_resolution_calls)
                failed_raw = failing_planner.get("raw_response")
                if (
                    failing_planner.get("stage") == "semantic_evidence_resolver"
                    and isinstance(failed_raw, Mapping)
                ):
                    resume_context = {
                        "previous_invalid_output": failed_raw,
                        "validation_error": failing_planner.get("validation_error")
                        or prior_failure.get("error"),
                        "strict_repair_guidance": (
                            "Use only IDs present in the supplied payload. A TARGET-only operation must not be "
                            "attached to a premise binding unless that premise is also listed in output_refs. "
                            "Complete coverage requires a grounded claim or a legal operation. Absent coverage "
                            "must not carry positive support. Every source_quote must be copied exactly. Use an "
                            "operation only when its type is permitted by the task contract and its operand kind "
                            "is supported; otherwise omit it and represent the remaining coverage honestly."
                        ),
                    }
                    resolution, new_resolution_calls = _call_with_schema_repair(
                        lambda: planner.plan_resolution(
                            row, contract, catalog, repair_context=resume_context
                        ),
                        lambda context: planner.plan_resolution(
                            row, contract, catalog, repair_context=context
                        ),
                    )
                    resolution_calls = _dedupe_calls(
                        [*prior_resolution_calls, *new_resolution_calls]
                    )
                else:
                    resolution, resolution_calls = _call_with_schema_repair(
                        lambda: planner.plan_resolution(row, contract, catalog),
                        lambda context: planner.plan_resolution(
                            row, contract, catalog, repair_context=context
                        ),
                    )
                if requires_exhaustive_resolution_review(contract):
                    reviewed_resolution, review_calls = _call_with_schema_repair(
                        lambda: planner.review_resolution_completeness(
                            row, contract, catalog, resolution
                        ),
                        lambda context: planner.review_resolution_completeness(
                            row,
                            contract,
                            catalog,
                            resolution,
                            repair_context=context,
                        ),
                    )
                    resolution = reviewed_resolution
                    resolution_calls.extend(review_calls)
                with lock:
                    _atomic_json(
                        resolution_artifact,
                        {
                            "question_id": qid,
                            "input_sha256": identity,
                            "stage_input_sha256": resolution_identity,
                            "status": "completed",
                            "resolution_plan": resolution,
                            "planner_calls": resolution_calls,
                        },
                    )
            calls.extend(resolution_calls)
            calls = _dedupe_calls(calls)
            packet = compile_semantic_evidence_packet(row, contract, resolution)
        except Exception as exc:
            planner_metadata = exc.metadata if isinstance(exc, SemanticPlannerError) else {}
            failure = {
                "question_id": qid,
                "input_sha256": identity,
                "status": "failed",
                "error": str(exc),
                "completed_planner_calls": calls,
                "failing_planner": planner_metadata,
            }
            with lock:
                _atomic_json(journal_dir / f"{index:06d}_{qid}.failure.json", failure)
            raise
        output_row = dict(row)
        output_row["semantic_evidence_packet"] = packet
        result = {
            "question_id": qid,
            "input_sha256": identity,
            "status": "completed",
            "planner_calls": calls,
            "row": output_row,
        }
        with lock:
            _atomic_json(artifact, result)
            (journal_dir / f"{index:06d}_{qid}.failure.json").unlink(missing_ok=True)
        return result

    results: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(compile_one, index, row): _text(row.get("question_id")) for index, row in enumerate(rows)}
        for future in as_completed(futures):
            qid = futures[future]
            try:
                results[qid] = future.result()
            except Exception as exc:
                failures.append({"question_id": qid, "error": f"{exc.__class__.__name__}: {exc}"})
    if failures:
        _atomic_json(out_dir / "failures.json", {"failures": failures})
        raise SemanticCompileError(f"semantic evidence compilation failed for {len(failures)} rows")
    ordered = [results[_text(row.get("question_id"))] for row in rows]
    output_rows = [item["row"] for item in ordered]
    all_calls = [call for item in ordered for call in item["planner_calls"]]
    certificates = [row["semantic_evidence_packet"]["answerability_certificate"] for row in output_rows]
    _atomic_jsonl(out_dir / "evidence_windows.jsonl", output_rows)
    _atomic_json(
        out_dir / "report.json",
        {
            "schema_version": "tmcra.v4.semantic-evidence-compiler-run.1",
            "compiler_version": COMPILER_VERSION,
            "task_prompt_version": TASK_PROMPT_VERSION,
            "resolution_prompt_version": RESOLUTION_PROMPT_VERSION,
            "completeness_review_prompt_version": COMPLETENESS_REVIEW_PROMPT_VERSION,
            "task_stage_version": TASK_STAGE_VERSION,
            "resolution_stage_version": RESOLUTION_STAGE_VERSION,
            "status": "complete",
            "row_count": len(output_rows),
            "physical_call_count": len(all_calls),
            "repair_call_count": sum(bool(call.get("repair_call")) for call in all_calls),
            "stage_call_counts": dict(Counter(_text(call.get("stage")) for call in all_calls)),
            "planner_provider": args.planner_provider,
            "planner_model": model,
            "claim_count": sum(len(row["semantic_evidence_packet"]["claim_ledger"]) for row in output_rows),
            "operation_count": sum(len(row["semantic_evidence_packet"]["resolution_program"]["operation_results"]) for row in output_rows),
            "memory_coverage_counts": dict(Counter(item["memory_coverage"] for item in certificates)),
            "executability_counts": dict(Counter(item["task_executability"] for item in certificates)),
            "exact_cost_cny": _cost_cny(all_calls),
        },
    )
    (out_dir / "failures.json").unlink(missing_ok=True)
    (out_dir / "COMPILE_COMPLETE").write_text(time.strftime("%Y-%m-%dT%H:%M:%S%z") + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
