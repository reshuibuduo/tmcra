#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from plan_tmcra_v4_quality_gates import PLAN_VERSION
from run_tmcra_v4_evaluate import official_judge_correct


class GateScoreError(RuntimeError):
    pass


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ids(value: Any) -> set[str]:
    if not isinstance(value, list):
        raise GateScoreError("answer_session_ids must be a list")
    result = {str(item).strip() for item in value if str(item).strip()}
    if not result:
        raise GateScoreError("answer_session_ids must be non-empty")
    return result


def _passed_audit(path: Path) -> bool:
    report = _read_json(path)
    return report.get("passed") is True and not list(report.get("issues") or [])


def _load_run(run_dir: Path, retrieval_tag: str) -> dict[str, Any]:
    required = [
        run_dir / "BUILD_COMPLETE",
        run_dir / "build_chain_audit.json",
        run_dir / retrieval_tag / "RETRIEVAL_COMPLETE",
        run_dir / f"{retrieval_tag}.chain_audit.json",
        run_dir / f"answer_{retrieval_tag}" / "ANSWER_COMPLETE",
        run_dir / f"answer_{retrieval_tag}" / "v4_evaluation_report.json",
        run_dir / f"{retrieval_tag}.official_judge.jsonl",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise GateScoreError(f"run is incomplete: {missing}")
    if not _passed_audit(run_dir / "build_chain_audit.json"):
        raise GateScoreError(f"build chain audit failed: {run_dir}")
    if not _passed_audit(run_dir / f"{retrieval_tag}.chain_audit.json"):
        raise GateScoreError(f"retrieval chain audit failed: {run_dir}")
    qids = [
        line.strip()
        for line in (run_dir / "qids.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    debug = _read_jsonl(run_dir / retrieval_tag / "retrieval_debug.jsonl")
    evidence = _read_jsonl(run_dir / retrieval_tag / "evidence_windows.jsonl")
    judged = _read_jsonl(run_dir / f"{retrieval_tag}.official_judge.jsonl")
    for name, rows in (("debug", debug), ("evidence", evidence), ("judge", judged)):
        if [str(row.get("question_id") or "") for row in rows] != qids:
            raise GateScoreError(f"{name} qid order differs from frozen qid list")
    cost_path = run_dir / f"{retrieval_tag}.cost_report.json"
    cost = _read_json(cost_path) if cost_path.is_file() else {}
    return {
        "run_dir": str(run_dir),
        "qids": qids,
        "debug": debug,
        "evidence": evidence,
        "judged": judged,
        "cost": cost,
    }


def score_gate(
    plan_path: Path,
    stage: int,
    run_dirs: Sequence[Path],
    *,
    retrieval_tag: str = "retrieval_1",
) -> dict[str, Any]:
    plan = _read_json(plan_path)
    if plan.get("schema_version") != PLAN_VERSION:
        raise GateScoreError("quality gate plan schema is missing or stale")
    if stage not in {20, 50, 500}:
        raise GateScoreError("stage must be 20, 50, or 500")
    data_path = Path(str(plan["dataset"])).resolve()
    if _sha256_file(data_path) != plan.get("dataset_sha256"):
        raise GateScoreError("benchmark dataset changed after gate plan freeze")
    dataset = _read_json(data_path)
    by_qid = {str(row["question_id"]): row for row in dataset}
    expected = [str(row["question_id"]) for row in plan["ordered_rows"][:stage]]

    runs = [_load_run(path.resolve(), retrieval_tag) for path in run_dirs]
    actual = [qid for run in runs for qid in run["qids"]]
    if actual != expected:
        raise GateScoreError("run shards do not exactly match the frozen cumulative gate prefix")
    if len(actual) != len(set(actual)):
        raise GateScoreError("run shards contain duplicate question IDs")

    rows: list[dict[str, Any]] = []
    for run in runs:
        for qid, debug, evidence, judged in zip(
            run["qids"], run["debug"], run["evidence"], run["judged"]
        ):
            reference = by_qid[qid]
            gold_sessions = _ids(reference.get("answer_session_ids"))
            trace = debug.get("source_top24_candidates")
            if not isinstance(trace, list) or debug.get("source_coverage_trace_k") != 24:
                raise GateScoreError(f"{qid}: source Top24 trace is missing")
            source_sessions = {
                str(item.get("session_id") or "").strip()
                for item in trace
                if isinstance(item, Mapping) and str(item.get("session_id") or "").strip()
            }
            final_sessions = {
                str(item).strip()
                for item in evidence.get("selected_session_ids", [])
                if str(item).strip()
            }
            rows.append(
                {
                    "question_id": qid,
                    "question_type": str(reference.get("question_type") or ""),
                    "gold_session_count": len(gold_sessions),
                    "source_top24_any": bool(gold_sessions & source_sessions),
                    "source_top24_complete": gold_sessions.issubset(source_sessions),
                    "final_top8_any": bool(gold_sessions & final_sessions),
                    "final_top8_complete": gold_sessions.issubset(final_sessions),
                    "official_correct": official_judge_correct(judged),
                }
            )

    count = len(rows)
    metrics = {
        "evaluated_count": count,
        "official_correct_count": sum(row["official_correct"] for row in rows),
        "official_accuracy": round(
            sum(row["official_correct"] for row in rows) / count, 6
        ),
        "source_top24_any_count": sum(row["source_top24_any"] for row in rows),
        "source_top24_any_rate": round(
            sum(row["source_top24_any"] for row in rows) / count, 6
        ),
        "source_top24_complete_count": sum(
            row["source_top24_complete"] for row in rows
        ),
        "source_top24_complete_rate": round(
            sum(row["source_top24_complete"] for row in rows) / count, 6
        ),
        "final_top8_any_count": sum(row["final_top8_any"] for row in rows),
        "final_top8_any_rate": round(
            sum(row["final_top8_any"] for row in rows) / count, 6
        ),
        "final_top8_complete_count": sum(row["final_top8_complete"] for row in rows),
        "final_top8_complete_rate": round(
            sum(row["final_top8_complete"] for row in rows) / count, 6
        ),
    }
    by_type: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["question_type"]].append(row)
    for question_type, values in sorted(grouped.items()):
        by_type[question_type] = {
            "count": len(values),
            "official_accuracy": round(
                sum(row["official_correct"] for row in values) / len(values), 6
            ),
            "source_top24_complete_rate": round(
                sum(row["source_top24_complete"] for row in values) / len(values), 6
            ),
            "final_top8_any_rate": round(
                sum(row["final_top8_any"] for row in values) / len(values), 6
            ),
        }

    threshold = plan["thresholds"][str(stage)]
    checks = {
        "structural": all(
            _passed_audit(Path(run["run_dir"]) / "build_chain_audit.json")
            and _passed_audit(
                Path(run["run_dir"]) / f"{retrieval_tag}.chain_audit.json"
            )
            for run in runs
        ),
        "official_accuracy": metrics["official_accuracy"]
        >= float(threshold["official_accuracy_min"]),
        "source_top24_complete_rate": metrics["source_top24_complete_rate"]
        >= float(threshold["source_top24_complete_rate_min"]),
        "final_top8_any_rate": metrics["final_top8_any_rate"]
        >= float(threshold["final_top8_any_rate_min"]),
    }
    total_cost = round(
        sum(float(run["cost"].get("exact_cost_cny", 0.0) or 0.0) for run in runs),
        9,
    )
    physical_calls = sum(
        int(run["cost"].get("physical_call_count", 0) or 0) for run in runs
    )
    failures = [
        row
        for row in rows
        if not row["official_correct"]
        or not row["source_top24_complete"]
        or not row["final_top8_any"]
    ]
    return {
        "schema_version": "tmcra.v4.quality-gate-score.1",
        "status": "passed" if all(checks.values()) else "failed",
        "stage": stage,
        "retrieval_tag": retrieval_tag,
        "run_dirs": [run["run_dir"] for run in runs],
        "thresholds": threshold,
        "checks": checks,
        "metrics": metrics,
        "by_question_type": by_type,
        "deepseek_chain_physical_calls": physical_calls,
        "deepseek_chain_exact_cost_cny": total_cost,
        "failure_count": len(failures),
        "failures": failures,
        "question_type_counts": dict(sorted(Counter(row["question_type"] for row in rows).items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Score cumulative TMCRA V4 quality gates")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--stage", type=int, choices=(20, 50, 500), required=True)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--retrieval-tag", default="retrieval_1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = score_gate(
        args.plan.resolve(),
        args.stage,
        args.run_dir,
        retrieval_tag=args.retrieval_tag,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
