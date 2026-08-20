#!/usr/bin/env python3
"""Read-only diagnosis for current Fast-to-Slow promotion coverage."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping


LEAF_VARIANT = "product_semantic_memory"
CAPSULE_VARIANT = "slow_memory_capsule"
ELIGIBLE_DURABILITY = {"durable", "long_term", "long-term", "hard", "persistent"}
ELIGIBLE_STATE = {"active", "parallel_active", "promoted"}
ELIGIBLE_TEMPORAL = {"", "current", "timeless"}


class SlowCoverageAnalysisError(RuntimeError):
    """Raised when Slow control-plane state is ambiguous or inconsistent."""


def _json(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
    return parsed


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_eligible(row: sqlite3.Row, metadata: Mapping[str, Any]) -> bool:
    return (
        metadata.get("content_variant") == LEAF_VARIANT
        and metadata.get("memory_layer") == "fast"
        and metadata.get("node_kind") == "atomic_user_assertion"
        and metadata.get("atomic_evidence_leaf") is True
        and metadata.get("authority") == "user_assertion"
        and _text(metadata.get("durability") or metadata.get("durability_class")).casefold()
        in ELIGIBLE_DURABILITY
        and _text(row["state"]).casefold() in ELIGIBLE_STATE
        and _text(metadata.get("temporal_status") or metadata.get("target_status")).casefold()
        in ELIGIBLE_TEMPORAL
    )


def _current_slow_citations(
    rows: list[sqlite3.Row],
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    by_capsule: dict[
        tuple[str, str], list[tuple[int, sqlite3.Row, Mapping[str, Any]]]
    ] = defaultdict(list)
    for row in rows:
        metadata = _json(row["metadata_json"], {})
        if metadata.get("content_variant") != CAPSULE_VARIANT:
            continue
        scope_id = _text(row["scope_id"])
        capsule_id = _text(metadata.get("capsule_id"))
        revision = metadata.get("revision")
        if (
            scope_id
            and capsule_id
            and isinstance(revision, int)
            and not isinstance(revision, bool)
        ):
            by_capsule[(scope_id, capsule_id)].append((revision, row, metadata))
    cited: set[tuple[str, str]] = set()
    current_regions: set[tuple[str, str]] = set()
    for revisions in by_capsule.values():
        latest_revision = max(item[0] for item in revisions)
        latest = [item for item in revisions if item[0] == latest_revision]
        if len(latest) != 1:
            continue
        _, row, metadata = latest[0]
        if row["state"] != "active" or metadata.get("status") not in {"active", "challenged"}:
            continue
        scope_id = _text(row["scope_id"])
        region_key = _text(metadata.get("region_key"))
        if scope_id and region_key:
            current_regions.add((scope_id, region_key))
        for claim in metadata.get("claims") or []:
            if not isinstance(claim, Mapping):
                continue
            for item in (claim.get("support") or []) + (claim.get("counterevidence") or []):
                memory_id = _text(item)
                if memory_id and scope_id:
                    cited.add((scope_id, memory_id))
    return cited, current_regions


def _analyze_db(path: Path) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        records = list(
            con.execute(
                "SELECT scope_id,memory_id,state,value,metadata_json FROM records"
            )
        )
        eligible: dict[tuple[str, str], dict[str, Any]] = {}
        for row in records:
            metadata = _json(row["metadata_json"], {})
            if _is_eligible(row, metadata):
                scope_id = _text(row["scope_id"])
                memory_id = _text(row["memory_id"])
                eligible[(scope_id, memory_id)] = {
                    "scope_id": scope_id,
                    "region_key": _text(
                        metadata.get("graph_entity_key")
                        or metadata.get("entity_key")
                        or metadata.get("domain")
                    ),
                    "slot": _text(
                        metadata.get("canonical_slot_key")
                        or metadata.get("canonical_slot")
                    ),
                    "value": _text(row["value"]),
                    "explicit_counterevidence": bool(
                        metadata.get("counterevidence")
                        or metadata.get("is_counterevidence")
                    ),
                }
        cited, current_capsule_regions = _current_slow_citations(records)
        uncited = set(eligible) - cited

        jobs = []
        if "slow_graph_jobs" in tables:
            jobs = list(con.execute("SELECT * FROM slow_graph_jobs ORDER BY created_at,job_id"))
        patches_by_job: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
        if "slow_graph_patches" in tables:
            for row in con.execute("SELECT * FROM slow_graph_patches ORDER BY applied_at,patch_id"):
                job_key = (_text(row["scope_id"]), _text(row["job_id"]))
                patches_by_job[job_key].append(row)
        duplicate_patch_jobs = {
            job_key: rows
            for job_key, rows in patches_by_job.items()
            if len(rows) > 1
        }
        if duplicate_patch_jobs:
            details = ", ".join(
                f"{scope_id}/{job_id} ({len(rows)} patches)"
                for (scope_id, job_id), rows in sorted(duplicate_patch_jobs.items())
            )
            raise SlowCoverageAnalysisError(
                "duplicate Slow patches per job: " + details
            )

        latest_job_for_evidence: dict[tuple[str, str], sqlite3.Row] = {}
        job_status = Counter()
        for job in jobs:
            job_status[_text(job["status"])] += 1
            for memory_id in _json(job["evidence_ids_json"], []):
                evidence_id = _text(memory_id)
                scope_id = _text(job["scope_id"])
                if scope_id and evidence_id:
                    latest_job_for_evidence[(scope_id, evidence_id)] = job

        uncited_reason = Counter()
        uncited_action = Counter()
        uncited_route_action = Counter()
        uncited_with_job = 0
        uncited_without_job = 0
        affected_job_ids: set[tuple[str, str]] = set()
        affected_regions: dict[tuple[str, str], set[str]] = defaultdict(set)
        affected_job_routes: dict[tuple[str, str], str] = {}
        samples: list[dict[str, Any]] = []
        for evidence_key in sorted(uncited):
            scope_id, memory_id = evidence_key
            job = latest_job_for_evidence.get(evidence_key)
            if job is None:
                uncited_without_job += 1
                route = "missing_job"
                reason = "missing_job"
                actions = ["missing_patch"]
                status = "missing"
            else:
                uncited_with_job += 1
                job_key = (_text(job["scope_id"]), _text(job["job_id"]))
                affected_job_ids.add(job_key)
                affected_regions[
                    (scope_id, eligible[evidence_key]["region_key"])
                ].add(memory_id)
                status = _text(job["status"])
                patch = patches_by_job.get(job_key, [None])[0]
                call_metadata = _json(patch["call_metadata_json"], {}) if patch else {}
                patch_json = _json(patch["patch_json"], {}) if patch else {}
                route = _text(call_metadata.get("route")) or "missing_route"
                affected_job_routes[job_key] = route
                reason = _text(call_metadata.get("route_reason")) or "missing_reason"
                actions = [
                    _text(operation.get("action")) or "missing_action"
                    for operation in patch_json.get("operations") or []
                    if isinstance(operation, Mapping)
                ] or ["missing_patch"]
            uncited_reason[reason] += 1
            for action in actions:
                uncited_action[action] += 1
                uncited_route_action[f"{route}:{action}"] += 1
            if len(samples) < 30:
                samples.append(
                    {
                        "database": str(path),
                        "memory_id": memory_id,
                        **eligible[evidence_key],
                        "job_status": status,
                        "route": route,
                        "reason": reason,
                        "actions": actions,
                    }
                )

        affected_with_capsule = sum(
            1 for region_key in affected_regions if region_key in current_capsule_regions
        )
        deterministic_create_regions = sum(
            1
            for region_key, memory_ids in affected_regions.items()
            if region_key not in current_capsule_regions
            and len(memory_ids) == 1
            and not eligible[(region_key[0], next(iter(memory_ids)))]["explicit_counterevidence"]
        )
        return {
            "database": str(path),
            "eligible": len(eligible),
            "cited": len(set(eligible) & cited),
            "uncited": len(uncited),
            "job_status": job_status,
            "uncited_with_job": uncited_with_job,
            "uncited_without_job": uncited_without_job,
            "uncited_reason": uncited_reason,
            "uncited_action": uncited_action,
            "uncited_route_action": uncited_route_action,
            "affected_latest_job_count": len(affected_job_ids),
            "affected_latest_job_route": Counter(affected_job_routes.values()),
            "affected_region_count": len(affected_regions),
            "affected_region_with_current_capsule_count": affected_with_capsule,
            "affected_region_without_current_capsule_count": len(affected_regions)
            - affected_with_capsule,
            "predicted_zero_call_deterministic_create_region_count": deterministic_create_regions,
            "predicted_physical_repair_call_upper_bound": len(affected_regions)
            - deterministic_create_regions,
            "samples": samples,
        }
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sample-limit", type=int, default=40)
    args = parser.parse_args()

    databases = sorted(args.run_dir.glob("writer/worker_*/native_memory.sqlite3"))
    totals = Counter()
    job_status = Counter()
    reasons = Counter()
    actions = Counter()
    route_actions = Counter()
    affected_job_routes = Counter()
    samples: list[dict[str, Any]] = []
    per_db: list[dict[str, Any]] = []
    for path in databases:
        result = _analyze_db(path)
        per_db.append(
            {
                "database": result["database"],
                "eligible": result["eligible"],
                "cited": result["cited"],
                "uncited": result["uncited"],
                "affected_regions": result["affected_region_count"],
                "affected_regions_with_current_capsule": result[
                    "affected_region_with_current_capsule_count"
                ],
                "predicted_zero_call_creates": result[
                    "predicted_zero_call_deterministic_create_region_count"
                ],
                "predicted_physical_repair_call_upper_bound": result[
                    "predicted_physical_repair_call_upper_bound"
                ],
                "affected_latest_job_route_counts": dict(
                    result["affected_latest_job_route"].most_common()
                ),
            }
        )
        for key in ("eligible", "cited", "uncited", "uncited_with_job", "uncited_without_job"):
            totals[key] += int(result[key])
        for key in (
            "affected_latest_job_count",
            "affected_region_count",
            "affected_region_with_current_capsule_count",
            "affected_region_without_current_capsule_count",
            "predicted_zero_call_deterministic_create_region_count",
            "predicted_physical_repair_call_upper_bound",
        ):
            totals[key] += int(result[key])
        job_status.update(result["job_status"])
        reasons.update(result["uncited_reason"])
        actions.update(result["uncited_action"])
        route_actions.update(result["uncited_route_action"])
        affected_job_routes.update(result["affected_latest_job_route"])
        for sample in result["samples"]:
            if len(samples) >= args.sample_limit:
                break
            samples.append(sample)

    eligible_count = totals["eligible"]
    report = {
        "schema_version": "tmcra.v4.slow-coverage-diagnosis.1",
        "read_only": True,
        "database_count": len(databases),
        "eligible_current_durable_count": eligible_count,
        "cited_current_durable_count": totals["cited"],
        "uncited_current_durable_count": totals["uncited"],
        "coverage_ratio": round(totals["cited"] / eligible_count, 6) if eligible_count else 1.0,
        "uncited_with_slow_job": totals["uncited_with_job"],
        "uncited_without_slow_job": totals["uncited_without_job"],
        "affected_latest_job_count": totals["affected_latest_job_count"],
        "affected_latest_job_route_counts": dict(affected_job_routes.most_common()),
        "affected_region_count": totals["affected_region_count"],
        "affected_region_with_current_capsule_count": totals[
            "affected_region_with_current_capsule_count"
        ],
        "affected_region_without_current_capsule_count": totals[
            "affected_region_without_current_capsule_count"
        ],
        "predicted_zero_call_deterministic_create_region_count": totals[
            "predicted_zero_call_deterministic_create_region_count"
        ],
        "predicted_physical_repair_call_upper_bound": totals[
            "predicted_physical_repair_call_upper_bound"
        ],
        "job_status_counts": dict(sorted(job_status.items())),
        "uncited_latest_job_reason_counts": dict(reasons.most_common()),
        "uncited_latest_patch_action_counts": dict(actions.most_common()),
        "uncited_latest_route_action_counts": dict(route_actions.most_common()),
        "samples": samples,
        "per_database": per_db,
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
