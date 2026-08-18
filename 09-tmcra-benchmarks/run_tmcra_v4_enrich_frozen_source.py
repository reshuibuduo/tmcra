#!/usr/bin/env python3
"""Attach deterministic same-session context to a frozen Source reservoir.

This stage never reranks, adds a new selected Source member, calls a model, or
writes the graph. It is used when the retrieval membership is frozen but a
newer packet contract needs immutable neighbor context from the same index.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import tmcra_v4_online_runtime as online


class FrozenSourceEnrichmentError(RuntimeError):
    pass


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise FrozenSourceEnrichmentError(f"invalid or empty JSONL: {path}")
    return rows


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    temporary.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _index(rows: Sequence[Mapping[str, Any]], label: str) -> dict[str, Mapping[str, Any]]:
    output: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        qid = _text(row.get("question_id"))
        if not qid or qid in output:
            raise FrozenSourceEnrichmentError(f"{label} has a missing or duplicate question_id")
        output[qid] = row
    return output


def _source_inventory(
    manifest_row: Mapping[str, Any], *, v3: Any
) -> tuple[list[dict[str, Any]], str]:
    qid = _text(manifest_row.get("question_id"))
    db_path = Path(str(manifest_row.get("db_path") or "")).resolve()
    index_path = Path(str(manifest_row.get("index_path") or "")).resolve()
    scope_id = _text(manifest_row.get("scope_id"))
    if not qid or not db_path.is_file() or not index_path.is_file() or not scope_id:
        raise FrozenSourceEnrichmentError(f"{qid or '<unknown>'}: manifest identity is incomplete")
    fast, _fast_vectors, _slow, _slow_vectors, _semantic_records, payload = (
        v3.load_online_index(index_path, db_path, scope_id)
    )
    fingerprint = v3.scope_fingerprint(db_path, scope_id)
    if fingerprint != _text(payload.get("graph_fingerprint")):
        raise FrozenSourceEnrichmentError(
            f"{qid}: graph fingerprint changed after frozen index creation"
        )
    parent_representatives: list[Mapping[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for candidate in fast:
        location = (
            int(candidate["session_index"]),
            int(candidate["parent_chunk_index"]),
        )
        if location not in seen:
            seen.add(location)
            parent_representatives.append(candidate)
    return online._collapse_source_parents(parent_representatives, fast), fingerprint


def enrich_rows(
    evidence_rows: Sequence[Mapping[str, Any]],
    manifest_rows: Sequence[Mapping[str, Any]],
    qids: Sequence[str],
    *,
    v3: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not qids or len(qids) != len(set(qids)):
        raise FrozenSourceEnrichmentError("qid list is empty or duplicated")
    evidence_by_qid = _index(evidence_rows, "evidence")
    manifest_by_qid = _index(manifest_rows, "query manifest")
    missing = [
        qid
        for qid in qids
        if qid not in evidence_by_qid or qid not in manifest_by_qid
    ]
    if missing:
        raise FrozenSourceEnrichmentError(f"qid list contains unknown questions: {missing[:10]}")
    runtime = v3 or online._v3()
    output: list[dict[str, Any]] = []
    attached_context_parent_count = 0
    groups_with_context_count = 0
    graph_fingerprints: dict[str, str] = {}
    for qid in qids:
        row = evidence_by_qid[qid]
        windows = row.get("evidence_windows")
        if not isinstance(windows, list) or not windows:
            raise FrozenSourceEnrichmentError(f"{qid}: Source evidence is missing")
        original_identity = [
            (
                _text(window.get("session_id")),
                int(window.get("parent_chunk_index", 0)),
                _text(window.get("source_record_id")),
                window.get("text"),
            )
            for window in windows
            if isinstance(window, Mapping)
        ]
        if len(original_identity) != len(windows):
            raise FrozenSourceEnrichmentError(f"{qid}: Source evidence contains a non-object")
        inventory, fingerprint = _source_inventory(manifest_by_qid[qid], v3=runtime)
        enriched, stats = online._attach_source_group_context(windows, inventory)
        selected_identity = [
            (
                _text(window.get("session_id")),
                int(window.get("parent_chunk_index", 0)),
                _text(window.get("source_record_id")),
                window.get("text"),
            )
            for window in enriched
        ]
        if selected_identity != original_identity:
            raise FrozenSourceEnrichmentError(
                f"{qid}: enrichment changed Source membership, order, identity, or text"
            )
        current = dict(row)
        current["evidence_windows"] = enriched
        current["source_group_enrichment"] = {
            "schema_version": "tmcra.v4.frozen-source-enrichment.1",
            "physical_api_calls": 0,
            "graph_mutated": False,
            **stats,
        }
        output.append(current)
        graph_fingerprints[qid] = fingerprint
        attached_context_parent_count += stats["attached_context_parent_count"]
        groups_with_context_count += stats["groups_with_context_count"]
    return output, {
        "schema_version": "tmcra.v4.frozen-source-enrichment-run.1",
        "status": "complete",
        "question_count": len(output),
        "source_window_count": sum(len(row["evidence_windows"]) for row in output),
        "groups_with_context_count": groups_with_context_count,
        "attached_context_parent_count": attached_context_parent_count,
        "physical_api_calls": 0,
        "graph_mutated": False,
        "graph_fingerprints": graph_fingerprints,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Attach immutable neighbor context to frozen Source evidence"
    )
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--query-manifest", type=Path, required=True)
    parser.add_argument("--qid-list", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    out_dir = args.out_dir.resolve()
    if out_dir.exists():
        raise FrozenSourceEnrichmentError(f"output already exists: {out_dir}")
    qids = [
        line.strip()
        for line in args.qid_list.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    enriched, report = enrich_rows(
        _read_jsonl(args.evidence.resolve()),
        _read_jsonl(args.query_manifest.resolve()),
        qids,
    )
    out_dir.mkdir(parents=True)
    _atomic_jsonl(out_dir / "evidence_windows.jsonl", enriched)
    _atomic_json(out_dir / "report.json", report)
    (out_dir / "RETRIEVAL_COMPLETE").write_text(
        time.strftime("%Y-%m-%dT%H:%M:%S%z") + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
