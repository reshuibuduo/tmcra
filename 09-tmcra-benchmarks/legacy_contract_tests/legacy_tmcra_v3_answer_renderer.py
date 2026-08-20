from __future__ import annotations

import unittest

try:
    from run_v3_gpt54_answers import (
        bind_existing_answers_to_evidence,
        evidence_sha256,
        ordered_completed_answers,
        render_hierarchical_evidence,
    )
except ModuleNotFoundError:
    from tmp_run_v3_gpt54_answers import (
        bind_existing_answers_to_evidence,
        evidence_sha256,
        ordered_completed_answers,
        render_hierarchical_evidence,
    )


class AnswerRendererTests(unittest.TestCase):
    def test_fast_override_is_rendered_with_explicit_precedence(self):
        rendered = render_hierarchical_evidence(
            {
                "text": "Original source window.",
                "memory_contexts": [
                    {
                        "role": "primary",
                        "canonical_slot": "preference.beverage.morning",
                        "claim_text": "Prefers coffee at 7 AM.",
                    }
                ],
                "attachments": [
                    {
                        "role": "override",
                        "canonical_slot": "preference.beverage.morning",
                        "text": "No longer drinks coffee at 7 AM.",
                    }
                ],
            }
        )
        self.assertIn("role=override", rendered)
        self.assertIn("precedence=newer_fast_evidence", rendered)
        self.assertIn("No longer drinks coffee at 7 AM.", rendered)
        self.assertLess(rendered.index("role=override"), rendered.index("immutable source"))

    def test_unknown_attachment_role_is_rejected(self):
        with self.assertRaises(RuntimeError):
            render_hierarchical_evidence(
                {
                    "text": "source",
                    "attachments": [
                        {
                            "role": "unexpected",
                            "canonical_slot": "slot",
                            "text": "value",
                        }
                    ],
                }
            )

    def test_legacy_answer_is_bound_once_and_rejects_changed_evidence(self):
        evidence = {
            "question_id": "q",
            "question": "What changed?",
            "selected_session_ids": ["s1"],
            "evidence_windows": [{"memory_id": "m1", "text": "new value"}],
        }
        existing = {
            "q": {
                "question_id": "q",
                "question": "What changed?",
                "selected_session_ids": ["s1"],
                "answer_model": "gpt-5.4",
                "hypothesis": "new value",
            }
        }
        hashes, backfills = bind_existing_answers_to_evidence(existing, [evidence])
        self.assertEqual(backfills, 1)
        self.assertEqual(existing["q"]["evidence_sha256"], evidence_sha256(evidence))
        self.assertEqual(hashes["q"], evidence_sha256(evidence))
        changed = {**evidence, "evidence_windows": [{"memory_id": "m2", "text": "other"}]}
        with self.assertRaisesRegex(RuntimeError, "different retrieval evidence"):
            bind_existing_answers_to_evidence(existing, [changed])

    def test_completed_answers_follow_frozen_qid_order(self):
        rows = [{"question_id": "z"}, {"question_id": "a"}]
        existing = {
            "a": {"question_id": "a", "hypothesis": "second"},
            "z": {"question_id": "z", "hypothesis": "first"},
        }
        ordered = ordered_completed_answers(existing, rows)
        self.assertEqual([row["question_id"] for row in ordered], ["z", "a"])
        with self.assertRaisesRegex(RuntimeError, "outside the frozen request"):
            ordered_completed_answers(
                {**existing, "extra": {"question_id": "extra"}}, rows
            )


if __name__ == "__main__":
    unittest.main()
