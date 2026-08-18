#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping

from run_tmcra_v4_build import (
    DEFAULT_DATA,
    DEFAULT_REPO,
    DEFAULT_WRITER_ENV,
    _key_pool,
    _load_shell_environment,
    _run,
    _worker_environment,
)
from tmcra_v4_batch_writer import (
    build_batches,
    exact_source_tokens,
    normalize_source_inventory,
)
from tmcra_v4_cost_report import build_report, collect_calls


class PilotError(RuntimeError):
    pass


def _session_row(raw: Mapping[str, Any], session_index: int, pilot_id: str) -> dict[str, Any]:
    sessions = list(raw.get("haystack_sessions") or [])
    session_ids = list(raw.get("haystack_session_ids") or [])
    dates = list(raw.get("haystack_dates") or [])
    return {
        "question_id": pilot_id,
        "haystack_sessions": [sessions[session_index]],
        "haystack_session_ids": [
            str(session_ids[session_index])
            if session_index < len(session_ids)
            else f"session-{session_index:03d}"
        ],
        "haystack_dates": [
            dates[session_index] if session_index < len(dates) else ""
        ],
    }


def _inventory(data: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row_index, raw in enumerate(data):
        qid = str(raw.get("question_id") or f"row{row_index:04d}")
        for session_index, _ in enumerate(raw.get("haystack_sessions") or []):
            identity = f"{qid}:{session_index}"
            pilot_id = "cost_" + hashlib.sha256(identity.encode()).hexdigest()[:16]
            writer_row = _session_row(raw, session_index, pilot_id)
            messages, exclusions = normalize_source_inventory([writer_row])
            if not messages:
                continue
            result.append(
                {
                    "identity": identity,
                    "source_question_id": qid,
                    "source_session_index": session_index,
                    "pilot_id": pilot_id,
                    "writer_row": writer_row,
                    "messages": len(messages),
                    "excluded_empty_messages": len(exclusions),
                    "chars": sum(len(message.content) for message in messages),
                    "tokens": sum(
                        len(exact_source_tokens(message.content)) for message in messages
                    ),
                    "batches": len(build_batches(messages)),
                }
            )
    if not result:
        raise PilotError("dataset contains no non-empty sessions")
    result.sort(key=lambda item: (item["tokens"], item["identity"]))
    for index, item in enumerate(result):
        item["quartile"] = min(3, index * 4 // len(result))
    return result


def _sample(inventory: list[dict[str, Any]], per_quartile: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for quartile in range(4):
        candidates = [item for item in inventory if item["quartile"] == quartile]
        candidates.sort(
            key=lambda item: hashlib.sha256(item["identity"].encode()).hexdigest()
        )
        if len(candidates) < per_quartile:
            raise PilotError(f"quartile {quartile} has too few sessions")
        selected.extend(candidates[:per_quartile])
    return selected


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = args.out_dir.resolve()
    if out_dir.exists():
        raise PilotError(f"output directory already exists: {out_dir}")
    source = json.loads(args.data.read_text(encoding="utf-8"))
    if not isinstance(source, list) or not all(isinstance(row, Mapping) for row in source):
        raise PilotError("dataset must be an array of objects")
    inventory = _inventory(source)
    selected = _sample(inventory, args.samples_per_quartile)
    out_dir.mkdir(parents=True)
    shell_environment = _load_shell_environment(args.writer_env.resolve())
    base_environment = {**os.environ, **shell_environment}
    keys = _key_pool(base_environment)
    workers: list[dict[str, Any]] = []
    for index, item in enumerate(selected):
        worker_dir = out_dir / f"worker_{index:03d}"
        worker_dir.mkdir()
        input_path = worker_dir / "input.json"
        _write_json(input_path, [item["writer_row"]])
        workers.append(
            {
                **{key: value for key, value in item.items() if key != "writer_row"},
                "worker_index": index,
                "worker_dir": str(worker_dir),
                "input": str(input_path),
                "database": str(worker_dir / "native_memory.sqlite3"),
            }
        )
    _write_json(out_dir / "pilot_manifest.json", workers)

    def execute(worker: Mapping[str, Any]) -> None:
        index = int(worker["worker_index"])
        worker_dir = Path(str(worker["worker_dir"]))
        environment = _worker_environment(base_environment, keys, index)
        _run(
            [
                sys.executable,
                str(Path(__file__).with_name("tmcra_v4_batch_writer.py")),
                "--input",
                str(worker["input"]),
                "--out-dir",
                str(worker_dir),
                "--repo",
                str(args.repo.resolve()),
            ],
            worker_dir / "writer.log",
            environment,
        )
        _run(
            [
                sys.executable,
                str(Path(__file__).with_name("audit_tmcra_v4_chain.py")),
                "--run-dir",
                str(worker_dir),
                "--output",
                str(worker_dir / "chain_audit.json"),
                "--worker-db",
                f"worker={worker_dir / 'native_memory.sqlite3'}",
            ],
            worker_dir / "audit.log",
            environment,
        )

    try:
        with ThreadPoolExecutor(max_workers=min(args.concurrency, len(workers))) as pool:
            futures = {pool.submit(execute, worker): worker for worker in workers}
            for future in as_completed(futures):
                future.result()
    except Exception as exc:
        _write_json(
            out_dir / "FAILED.json",
            {"error": f"{exc.__class__.__name__}: {exc}"},
        )
        raise

    worker_costs: list[dict[str, Any]] = []
    for worker in workers:
        report = build_report(
            collect_calls([], [Path(str(worker["database"]))])
        )
        if report["exact_cost_cny"] is None:
            raise PilotError(f"worker {worker['worker_index']} has incomplete cost")
        worker_costs.append(
            {
                "worker_index": worker["worker_index"],
                "quartile": worker["quartile"],
                "chars": worker["chars"],
                "tokens": worker["tokens"],
                "messages": worker["messages"],
                "batches": worker["batches"],
                "physical_call_count": report["physical_call_count"],
                "exact_cost_cny": report["exact_cost_cny"],
            }
        )
    aggregate = build_report(
        collect_calls([], [Path(str(worker["database"])) for worker in workers])
    )
    quartiles: list[dict[str, Any]] = []
    estimate = 0.0
    estimate_low = 0.0
    estimate_high = 0.0
    for quartile in range(4):
        universe = [item for item in inventory if item["quartile"] == quartile]
        samples = [item for item in worker_costs if item["quartile"] == quartile]
        sample_chars = sum(int(item["chars"]) for item in samples)
        sample_cost = sum(float(item["exact_cost_cny"]) for item in samples)
        rates = [float(item["exact_cost_cny"]) / int(item["chars"]) for item in samples]
        universe_chars = sum(int(item["chars"]) for item in universe)
        weighted_rate = sample_cost / sample_chars
        projected = universe_chars * weighted_rate
        estimate += projected
        estimate_low += universe_chars * min(rates)
        estimate_high += universe_chars * max(rates)
        quartiles.append(
            {
                "quartile": quartile,
                "universe_sessions": len(universe),
                "universe_chars": universe_chars,
                "sample_sessions": len(samples),
                "sample_chars": sample_chars,
                "sample_cost_cny": round(sample_cost, 9),
                "projected_cost_cny": round(projected, 6),
            }
        )
    report = {
        "schema_version": "tmcra.v4.writer-cost-pilot.1",
        "status": "complete",
        "scope": "writer_only",
        "dataset_rows": len(source),
        "universe": {
            "sessions": len(inventory),
            "messages": sum(item["messages"] for item in inventory),
            "excluded_empty_messages": sum(
                item["excluded_empty_messages"] for item in inventory
            ),
            "batches": sum(item["batches"] for item in inventory),
            "chars": sum(item["chars"] for item in inventory),
            "tokens": sum(item["tokens"] for item in inventory),
        },
        "sample": {
            "sessions": len(workers),
            "messages": sum(item["messages"] for item in workers),
            "batches": sum(item["batches"] for item in workers),
            "chars": sum(item["chars"] for item in workers),
            "tokens": sum(item["tokens"] for item in workers),
            "physical_api_calls": aggregate["physical_call_count"],
            "exact_cost_cny": aggregate["exact_cost_cny"],
            "by_stage_model": aggregate["by_stage_model"],
        },
        "quartiles": quartiles,
        "projected_writer_cost_cny": round(estimate, 6),
        "projected_writer_cost_low_cny": round(estimate_low, 6),
        "projected_writer_cost_high_cny": round(estimate_high, 6),
        "projection_method": "four token-quantile strata, aggregate sample cost per source character",
        "does_not_include": ["slow_graph", "recall_planner", "answer", "judge"],
        "workers": worker_costs,
    }
    _write_json(out_dir / "cost_report.json", report)
    (out_dir / "COMPLETE").write_text("complete\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a stratified TMCRA V4 writer cost pilot")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--writer-env", type=Path, default=DEFAULT_WRITER_ENV)
    parser.add_argument("--samples-per-quartile", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=20)
    args = parser.parse_args()
    if args.samples_per_quartile <= 0 or args.concurrency <= 0:
        raise PilotError("sample size and concurrency must be positive")
    report = run(args)
    print(
        json.dumps(
            {
                "sample_cost_cny": report["sample"]["exact_cost_cny"],
                "projected_writer_cost_cny": report["projected_writer_cost_cny"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
