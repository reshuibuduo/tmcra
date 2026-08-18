import argparse
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from run_tmcra_v4_parallel_official_judge import (
    ParallelJudgeError,
    run_parallel_judge,
)


FAKE_JUDGE = r'''import argparse, json, os
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--hyp-file", type=Path)
p.add_argument("--result-file", type=Path)
p.add_argument("--metric-model")
p.add_argument("--ref-file")
p.add_argument("--base-url")
p.add_argument("--api-key-env")
p.add_argument("--timeout")
p.add_argument("--max-retries")
p.add_argument("--quiet", action="store_true")
a = p.parse_args()
row = json.loads(a.hyp_file.read_text().strip())
with Path(os.environ["FAKE_CALLS"]).open("a") as h:
    h.write(json.dumps({"qid": row["question_id"], "pid": os.getpid()}) + "\n")
row["autoeval_label"] = {"model": "gpt-5.4", "label": True, "raw_response": "yes"}
a.result_file.write_text(json.dumps(row) + "\n")
'''


class IsolatedOfficialJudgeTests(unittest.TestCase):
    def _args(self, root: Path, *, resume: bool = False) -> argparse.Namespace:
        return argparse.Namespace(
            judge=root / "fake_judge.py",
            metric_model="gpt-5.4",
            base_url="https://example.test/v1",
            api_key_env="TEST_JUDGE_KEY",
            hyp_file=root / "hyp.jsonl",
            ref_file=root / "ref.json",
            result_file=root / "result.jsonl",
            work_dir=root / "work",
            limit=0,
            resume=resume,
            timeout=1,
            process_timeout=10,
            max_retries=0,
            workers=3,
        )

    def test_one_process_per_uncached_question_and_ordered_commit(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                {
                    "question_id": f"q{index}",
                    "question": f"question-{index}",
                    "hypothesis": f"answer-{index}",
                    "evidence_sha256": f"hash-{index}",
                    "answer_model": "gpt-5.4",
                }
                for index in range(4)
            ]
            (root / "hyp.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            (root / "ref.json").write_text("[]", encoding="utf-8")
            (root / "fake_judge.py").write_text(FAKE_JUDGE, encoding="utf-8")
            calls = root / "calls.jsonl"
            environment = {**os.environ, "TEST_JUDGE_KEY": "secret", "FAKE_CALLS": str(calls)}
            summary = run_parallel_judge(self._args(root), environment=environment)
            written = [json.loads(line) for line in (root / "result.jsonl").read_text().splitlines()]
            call_rows = [json.loads(line) for line in calls.read_text().splitlines()]
            self.assertEqual([row["question_id"] for row in written], ["q0", "q1", "q2", "q3"])
            self.assertEqual(len({row["pid"] for row in call_rows}), 4)
            self.assertEqual(summary["physical_call_count"], 4)

    def test_resume_reuses_existing_prefix_and_per_question_cache(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                {"question_id": f"q{i}", "question": "q", "hypothesis": "a", "evidence_sha256": f"h{i}", "answer_model": "gpt-5.4"}
                for i in range(3)
            ]
            (root / "hyp.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            (root / "ref.json").write_text("[]")
            (root / "fake_judge.py").write_text(FAKE_JUDGE)
            labeled = lambda row: {**row, "autoeval_label": {"model": "gpt-5.4", "label": True, "raw_response": "yes"}}
            (root / "result.jsonl").write_text(json.dumps(labeled(rows[0])) + "\n")
            from run_tmcra_v4_parallel_official_judge import _question_dir
            cached_dir = _question_dir(root / "work", 2, "q2")
            cached_dir.mkdir(parents=True)
            (cached_dir / "official_judge.jsonl").write_text(json.dumps(labeled(rows[2])) + "\n")
            calls = root / "calls.jsonl"
            environment = {**os.environ, "TEST_JUDGE_KEY": "secret", "FAKE_CALLS": str(calls)}
            summary = run_parallel_judge(self._args(root, resume=True), environment=environment)
            call_rows = [json.loads(line) for line in calls.read_text().splitlines()]
            self.assertEqual([row["qid"] for row in call_rows], ["q1"])
            self.assertEqual(summary["existing_count"], 1)
            self.assertEqual(summary["cached_count"], 1)
            self.assertEqual(summary["physical_call_count"], 1)

    def test_rejects_result_bound_to_different_answer(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            row = {"question_id": "q1", "question": "q", "hypothesis": "a", "evidence_sha256": "h", "answer_model": "gpt-5.4"}
            (root / "hyp.jsonl").write_text(json.dumps(row) + "\n")
            (root / "ref.json").write_text("[]")
            (root / "fake_judge.py").write_text(FAKE_JUDGE)
            wrong = {**row, "hypothesis": "wrong", "autoeval_label": {"model": "gpt-5.4", "label": True, "raw_response": "yes"}}
            (root / "result.jsonl").write_text(json.dumps(wrong) + "\n")
            with self.assertRaisesRegex(ParallelJudgeError, "different hypothesis"):
                run_parallel_judge(
                    self._args(root, resume=True),
                    environment={**os.environ, "TEST_JUDGE_KEY": "secret"},
                )


if __name__ == "__main__":
    unittest.main()
