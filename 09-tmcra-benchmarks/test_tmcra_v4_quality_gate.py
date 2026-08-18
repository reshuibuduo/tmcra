import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from plan_tmcra_v4_quality_gates import PLAN_VERSION
from score_tmcra_v4_quality_gate import score_gate


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in values), encoding="utf-8"
    )


class V4QualityGateTests(unittest.TestCase):
    def make_gate(self, root, *, miss_source=False):
        dataset = []
        qids = []
        debug = []
        evidence = []
        judged = []
        for index in range(20):
            qid = f"q{index:02d}"
            session_id = f"session-{index}"
            qids.append(qid)
            dataset.append(
                {
                    "question_id": qid,
                    "question_type": "knowledge-update",
                    "answer_session_ids": [session_id],
                }
            )
            traced_session = "wrong-session" if miss_source and index == 0 else session_id
            debug.append(
                {
                    "question_id": qid,
                    "source_coverage_trace_k": 24,
                    "source_top24_candidates": [{"session_id": traced_session}],
                }
            )
            evidence.append(
                {"question_id": qid, "selected_session_ids": [session_id]}
            )
            judged.append(
                {
                    "question_id": qid,
                    "error": "",
                    "autoeval_label": {"label": True, "model": "gpt-5.4"},
                }
            )
        data_path = root / "data.json"
        write_json(data_path, dataset)
        digest = hashlib.sha256(data_path.read_bytes()).hexdigest()
        plan = {
            "schema_version": PLAN_VERSION,
            "dataset": str(data_path),
            "dataset_sha256": digest,
            "ordered_rows": [{"question_id": qid} for qid in qids],
            "thresholds": {
                "20": {
                    "official_accuracy_min": 0.9,
                    "source_top24_complete_rate_min": 1.0,
                    "final_top8_any_rate_min": 0.9,
                    "structural_pass_required": True,
                }
            },
        }
        plan_path = root / "gate_plan.json"
        write_json(plan_path, plan)

        run = root / "run"
        (run / "retrieval_1").mkdir(parents=True)
        (run / "answer_retrieval_1").mkdir()
        for marker in (
            run / "BUILD_COMPLETE",
            run / "retrieval_1" / "RETRIEVAL_COMPLETE",
            run / "answer_retrieval_1" / "ANSWER_COMPLETE",
        ):
            marker.write_text("complete\n", encoding="utf-8")
        write_json(run / "build_chain_audit.json", {"passed": True, "issues": []})
        write_json(
            run / "retrieval_1.chain_audit.json", {"passed": True, "issues": []}
        )
        write_json(run / "answer_retrieval_1" / "v4_evaluation_report.json", {})
        write_json(run / "retrieval_1.cost_report.json", {"exact_cost_cny": 1.5, "physical_call_count": 40})
        (run / "qids.txt").write_text("\n".join(qids) + "\n", encoding="utf-8")
        write_jsonl(run / "retrieval_1" / "retrieval_debug.jsonl", debug)
        write_jsonl(run / "retrieval_1" / "evidence_windows.jsonl", evidence)
        write_jsonl(run / "retrieval_1.official_judge.jsonl", judged)
        return plan_path, run

    def test_scores_passing_gate_and_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, run = self.make_gate(Path(directory))
            report = score_gate(plan, 20, [run])
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["metrics"]["official_accuracy"], 1.0)
            self.assertEqual(report["deepseek_chain_exact_cost_cny"], 1.5)

    def test_source_coverage_miss_fails_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, run = self.make_gate(Path(directory), miss_source=True)
            report = score_gate(plan, 20, [run])
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["checks"]["source_top24_complete_rate"])


if __name__ == "__main__":
    unittest.main()
