#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


PLAN_VERSION = "tmcra.v4.quality-gate-plan.1"
SUPPORTED_STAGES = (20, 50, 500)
DEFAULT_THRESHOLDS = {
    "official_accuracy_min": 0.90,
    "source_top24_complete_rate_min": 1.00,
    "final_top8_any_rate_min": 0.90,
    "structural_pass_required": True,
}


class GatePlanError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_dataset(path: Path) -> list[Mapping[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GatePlanError(f"dataset is unreadable: {path}") from exc
    if not isinstance(value, list) or not value:
        raise GatePlanError("dataset must be a non-empty JSON array")
    rows: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise GatePlanError(f"dataset row {index} must be an object")
        qid = str(item.get("question_id") or "").strip()
        if not qid:
            raise GatePlanError(f"dataset row {index} has no question_id")
        if qid in seen:
            raise GatePlanError(f"dataset has duplicate question_id: {qid}")
        seen.add(qid)
        rows.append(item)
    return rows


def build_plan(
    dataset: Path,
    *,
    stages: Sequence[int] = SUPPORTED_STAGES,
    thresholds: Mapping[str, Any] = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    dataset = dataset.resolve()
    rows = _load_dataset(dataset)
    selected_stages = sorted({int(stage) for stage in stages})
    if not selected_stages or any(stage not in SUPPORTED_STAGES for stage in selected_stages):
        raise GatePlanError("stages must be selected from 20, 50, and 500")
    if selected_stages[-1] > len(rows):
        raise GatePlanError(
            f"dataset has {len(rows)} rows but stage {selected_stages[-1]} was requested"
        )
    required_thresholds = {
        "official_accuracy_min",
        "source_top24_complete_rate_min",
        "final_top8_any_rate_min",
        "structural_pass_required",
    }
    if set(thresholds) != required_thresholds:
        raise GatePlanError("thresholds do not match the quality-gate contract")
    frozen_thresholds = {
        str(stage): dict(thresholds) for stage in selected_stages
    }
    return {
        "schema_version": PLAN_VERSION,
        "dataset": str(dataset),
        "dataset_sha256": _sha256_file(dataset),
        "dataset_row_count": len(rows),
        "ordered_rows": [
            {
                "question_id": str(row["question_id"]).strip(),
                "question_type": str(row.get("question_type") or "").strip(),
            }
            for row in rows
        ],
        "thresholds": frozen_thresholds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze a deterministic TMCRA V4 cumulative quality-gate plan"
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--stage",
        type=int,
        choices=SUPPORTED_STAGES,
        action="append",
        dest="stages",
        help="Cumulative gate stage; repeat for multiple stages (default: all that fit)",
    )
    parser.add_argument("--official-accuracy-min", type=float, default=0.90)
    parser.add_argument(
        "--source-top24-complete-rate-min", type=float, default=1.00
    )
    parser.add_argument("--final-top8-any-rate-min", type=float, default=0.90)
    args = parser.parse_args()

    row_count = len(_load_dataset(args.dataset.resolve()))
    stages = args.stages or [stage for stage in SUPPORTED_STAGES if stage <= row_count]
    thresholds = {
        "official_accuracy_min": args.official_accuracy_min,
        "source_top24_complete_rate_min": args.source_top24_complete_rate_min,
        "final_top8_any_rate_min": args.final_top8_any_rate_min,
        "structural_pass_required": True,
    }
    plan = build_plan(args.dataset, stages=stages, thresholds=thresholds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
