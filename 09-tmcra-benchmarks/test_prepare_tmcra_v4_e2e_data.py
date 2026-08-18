from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prepare_tmcra_v4_e2e_data import PreparationError, prepare


def source_row() -> dict:
    return {
        "question_id": "q1",
        "question": "What is remembered?",
        "question_date": "2026-01-03",
        "question_type": "single-session-user",
        "answer": "A",
        "answer_session_ids": ["session-a"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "I prefer tea."},
                {"role": "assistant", "content": "Noted."},
            ]
        ],
        "haystack_session_ids": ["session-a"],
        "haystack_dates": ["2026-01-01"],
    }


class PrepareV4Tests(unittest.TestCase):
    def test_gold_is_separate_and_writer_scope_is_v4(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data.json"
            qids = root / "qids.txt"
            output = root / "run"
            data.write_text(json.dumps([source_row()]), encoding="utf-8")
            qids.write_text("q1\n", encoding="utf-8")
            report = prepare(data_path=data, qid_path=qids, out_dir=output)
            writer = json.loads(
                (output / "writer/worker_000/input.json").read_text(encoding="utf-8")
            )[0]
            combined_writer = json.loads(
                (output / "writer_input.json").read_text(encoding="utf-8")
            )
            self.assertEqual(set(writer), {
                "question_id", "haystack_sessions", "haystack_session_ids", "haystack_dates"
            })
            self.assertNotIn("question", writer)
            self.assertEqual(combined_writer, [writer])
            scope = json.loads(
                (output / "scope_manifest.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(scope["scope_id"], "tmcra_v4:q1")
            reference = json.loads(
                (output / "evaluation_only/references.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(reference["answer"], "A")
            self.assertFalse(report["writer_inputs_have_query_or_evaluation_fields"])

    def test_rejects_mismatched_session_metadata(self) -> None:
        bad = source_row()
        bad["haystack_dates"] = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data.json"
            qids = root / "qids.txt"
            data.write_text(json.dumps([bad]), encoding="utf-8")
            qids.write_text("q1\n", encoding="utf-8")
            with self.assertRaises(PreparationError):
                prepare(data_path=data, qid_path=qids, out_dir=root / "run")

    def test_empty_message_carrier_is_counted_but_not_rewritten(self) -> None:
        row = source_row()
        row["haystack_sessions"][0].insert(1, {"role": "user", "content": ""})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data.json"
            qids = root / "qids.txt"
            output = root / "run"
            data.write_text(json.dumps([row]), encoding="utf-8")
            qids.write_text("q1\n", encoding="utf-8")
            report = prepare(data_path=data, qid_path=qids, out_dir=output)
            writer = json.loads(
                (output / "writer/worker_000/input.json").read_text(encoding="utf-8")
            )[0]
            self.assertEqual(writer["haystack_sessions"][0][1]["content"], "")
            self.assertEqual(report["input_message_count"], 3)
            self.assertEqual(report["nonempty_message_count"], 2)
            self.assertEqual(report["empty_message_count"], 1)

    def test_duplicate_session_id_occurrences_are_preserved_and_reported(self) -> None:
        row = source_row()
        duplicate = [
            {"role": "user", "content": "I prefer tea."},
            {"role": "assistant", "content": "Noted."},
        ]
        row["haystack_sessions"].append(duplicate)
        row["haystack_session_ids"].append("session-a")
        row["haystack_dates"].append("2026-01-02")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data.json"
            qids = root / "qids.txt"
            output = root / "run"
            data.write_text(json.dumps([row]), encoding="utf-8")
            qids.write_text("q1\n", encoding="utf-8")
            report = prepare(data_path=data, qid_path=qids, out_dir=output)
            writer = json.loads(
                (output / "writer/worker_000/input.json").read_text(encoding="utf-8")
            )[0]
            self.assertEqual(writer["haystack_session_ids"], ["session-a", "session-a"])
            self.assertEqual(writer["haystack_sessions"][1], duplicate)
            self.assertEqual(report["duplicate_session_id_occurrence_count"], 1)
            self.assertEqual(report["duplicate_session_id_qids"], ["q1"])
            self.assertEqual(
                report["workers"][0]["duplicate_session_ids"], {"session-a": 2}
            )


if __name__ == "__main__":
    unittest.main()
