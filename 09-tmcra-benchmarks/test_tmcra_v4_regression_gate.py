import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tmcra_v4_regression_gate import evaluate_gate


ROOT = Path(__file__).resolve().parent


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class RegressionGateTests(unittest.TestCase):
    def make_inputs(
        self,
        root: Path,
        outcomes: dict[str, tuple[bool, bool]],
        *,
        missing_evidence_qid: str | None = None,
        missing_session_qid: str | None = None,
    ) -> tuple[Path, Path, Path, Path]:
        baseline = []
        candidate = []
        evidence = []
        reference = []
        for index, (qid, (baseline_correct, candidate_correct)) in enumerate(outcomes.items()):
            session_id = f"session-{index}"
            baseline.append({
                "question_id": qid,
                "error": "",
                "autoeval_label": {"label": baseline_correct},
            })
            candidate.append({
                "question_id": qid,
                "error": "",
                "autoeval_label": {"label": candidate_correct},
            })
            if qid != missing_evidence_qid:
                selected = [] if qid == missing_session_qid else [session_id]
                evidence.append({"qid": qid, "selected_session_ids": selected})
            reference.append({
                "qid": qid,
                "question_type": "knowledge-update" if index % 2 == 0 else "preference",
                "answer_session_ids": [session_id],
            })
        baseline_path = root / "baseline.jsonl"
        candidate_path = root / "candidate.jsonl"
        evidence_path = root / "evidence.jsonl"
        reference_path = root / "reference.json"
        write_jsonl(baseline_path, baseline)
        write_jsonl(candidate_path, candidate)
        write_jsonl(evidence_path, evidence)
        write_json(reference_path, reference)
        return baseline_path, candidate_path, evidence_path, reference_path

    def test_pairs_by_qid_and_reports_counts_by_type(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.make_inputs(Path(directory), {
                "kept": (True, True),
                "improved": (False, True),
                "regressed": (True, False),
                "wrong": (False, False),
            })
            report = evaluate_gate(*paths)
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["metrics"]["kept_correct"], 1)
            self.assertEqual(report["metrics"]["improved"], 1)
            self.assertEqual(report["metrics"]["regressed"], 1)
            self.assertEqual(report["metrics"]["kept_wrong"], 1)
            self.assertEqual(set(report["by_question_type"]), {"knowledge-update", "preference"})
            self.assertEqual(report["reference_backed_session_coverage"]["expected_session_count"], 4)
            self.assertEqual(report["reference_backed_session_coverage"]["complete_rate"], 1.0)
            self.assertEqual(report["known_corpus_outcomes"]["semantic100"]["expected_regressed_count"], 21)
            self.assertEqual(report["known_corpus_outcomes"]["semantic100"]["expected_improved_count"], 5)

    def test_missing_qid_blocks_exact_pairing(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.make_inputs(Path(directory), {"q1": (True, True), "q2": (False, True)})
            evidence = json.loads(paths[2].read_text(encoding="utf-8").splitlines()[0])
            paths[2].write_text(json.dumps(evidence) + "\n", encoding="utf-8")
            report = evaluate_gate(*paths)
            self.assertEqual(report["status"], "blocked")
            self.assertFalse(report["checks"]["exact_qid_pairing"])
            self.assertEqual(report["pairing"]["missing_by_source"]["candidate_evidence"], ["q2"])

    def test_reference_dataset_may_be_a_superset(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.make_inputs(Path(directory), {"q1": (True, True)})
            reference = json.loads(paths[3].read_text(encoding="utf-8"))
            reference.append(
                {
                    "qid": "unused-reference-qid",
                    "question_type": "other",
                    "answer_session_ids": ["unused-session"],
                }
            )
            write_json(paths[3], reference)
            report = evaluate_gate(*paths)
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["pairing"]["reference_is_superset"])
            self.assertNotIn("reference", report["pairing"]["extra_by_source"])

    def test_regressed_qid_requires_complete_source_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.make_inputs(
                Path(directory), {"q1": (True, False)}, missing_session_qid="q1"
            )
            report = evaluate_gate(*paths)
            self.assertEqual(report["status"], "blocked")
            self.assertFalse(report["checks"]["regressed_source_coverage"])
            self.assertEqual(report["source_coverage"]["regressed_failures"][0]["qid"], "q1")
            self.assertEqual(
                report["reference_backed_session_coverage"]["missing_session_ids_by_qid"]["q1"],
                ["session-0"],
            )

    def test_coverage_ignores_empty_judge_gold_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.make_inputs(Path(directory), {"q1": (True, True)})
            for path in paths[:2]:
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                rows[0]["gold_answer"] = ""
                write_jsonl(path, rows)
            report = evaluate_gate(*paths)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["reference_backed_session_coverage"]["complete_rate"], 1.0)

    def test_reports_present_known_corpus_outcomes_without_answers(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.make_inputs(Path(directory), {
                "dd2973ad": (True, False),
                "830ce83f": (False, True),
            })
            report = evaluate_gate(*paths)
            outcomes = report["known_corpus_outcomes"]["semantic100"]
            self.assertEqual(outcomes["present_count"], 2)
            self.assertEqual(
                {item["qid"] for item in outcomes["observed"]},
                {"dd2973ad", "830ce83f"},
            )
            self.assertTrue(all(item["matches_expected"] for item in outcomes["observed"]))

    def test_promotion_allowed_and_blocked_by_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            allowed = self.make_inputs(Path(directory), {"q1": (True, True), "q2": (False, True)})
            allowed_report = evaluate_gate(*allowed)
            self.assertEqual(allowed_report["status"], "passed")
            blocked = self.make_inputs(Path(directory), {"q1": (True, False), "q2": (False, True)})
            blocked_report = evaluate_gate(*blocked)
            self.assertEqual(blocked_report["status"], "blocked")
            self.assertFalse(blocked_report["checks"]["max_regressions"])

    def test_cli_writes_report_and_returns_nonzero_when_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.make_inputs(root, {"q1": (True, False)})
            output = root / "report.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tmcra_v4_regression_gate.py"),
                    "--baseline-judge", str(paths[0]),
                    "--candidate-judge", str(paths[1]),
                    "--candidate-evidence", str(paths[2]),
                    "--reference", str(paths[3]),
                    "--output", str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
