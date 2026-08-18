#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from run_tmcra_v4_build import (
    DEFAULT_DATA,
    DEFAULT_REPO,
    DEFAULT_WRITER_ENV,
    _key_pool,
    _load_shell_environment,
    _now,
    _worker_environment,
    _writer_worker,
    prepare,
)


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = args.out_dir.resolve()
    if out_dir.exists():
        raise RuntimeError(f"output directory already exists: {out_dir}")
    manifest = prepare(
        data_path=args.data.resolve(),
        qid_path=args.qid_list.resolve(),
        out_dir=out_dir,
    )
    workers = list(manifest["workers"])
    base_environment = {
        **os.environ,
        **_load_shell_environment(args.writer_env.resolve()),
    }
    keys = _key_pool(base_environment)
    environments = [
        _worker_environment(base_environment, keys, index)
        for index in range(len(workers))
    ]
    started_at = _now()
    (out_dir / "WRITER_SOAK_STARTED").write_text(started_at + "\n", encoding="utf-8")

    def execute(index: int) -> dict[str, Any]:
        worker = workers[index]
        try:
            _writer_worker(
                worker,
                repo=args.repo.resolve(),
                environment=environments[index],
            )
            return {
                "index": index,
                "question_id": worker["question_id"],
                "status": "completed",
                "error": "",
            }
        except BaseException as exc:
            return {
                "index": index,
                "question_id": worker["question_id"],
                "status": "failed",
                "error": f"{exc.__class__.__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as executor:
        futures = {executor.submit(execute, index): index for index in range(len(workers))}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            with (out_dir / "writer_soak_results.jsonl").open(
                "a", encoding="utf-8"
            ) as handle:
                handle.write(json.dumps(result, sort_keys=True) + "\n")

    results.sort(key=lambda item: int(item["index"]))
    completed = sum(item["status"] == "completed" for item in results)
    failed = len(results) - completed
    report = {
        "schema_version": "tmcra.v4.writer-soak.1",
        "status": "complete",
        "started_at": started_at,
        "completed_at": _now(),
        "question_count": len(workers),
        "worker_concurrency": args.concurrency,
        "completed_workers": completed,
        "failed_workers": failed,
        "results": results,
    }
    _atomic_json(out_dir / "writer_soak_report.json", report)
    (out_dir / "WRITER_SOAK_COMPLETE").write_text(_now() + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run independent V4 writer workers without slow graph or retrieval"
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--qid-list", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--writer-env", type=Path, default=DEFAULT_WRITER_ENV)
    parser.add_argument("--concurrency", type=int, default=20)
    args = parser.parse_args()
    report = run(args)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
