import unittest

from run_tmcra_v4_select_evidence import SelectionError, _selection_payload, _validate_selection


class EvidenceSelectorTests(unittest.TestCase):
    def test_validates_selected_ids_roles_and_budget(self):
        value = {
            "schema_version": "tmcra.evidence-selection.v1",
            "selected": [
                {"id": "E02", "role": "operand"},
                {"id": "E01", "role": "temporal_anchor"},
            ],
            "confidence": "high",
            "needs_review": False,
        }
        self.assertEqual(
            _validate_selection(value, {"E01", "E02"}, 2)["selected"],
            value["selected"],
        )
        with self.assertRaisesRegex(SelectionError, "count"):
            _validate_selection(value, {"E01", "E02"}, 1)
        value["selected"][1]["id"] = "E02"
        with self.assertRaisesRegex(SelectionError, "identity"):
            _validate_selection(value, {"E01", "E02"}, 2)

        empty = {
            "schema_version": "tmcra.evidence-selection.v1",
            "selected": [],
            "confidence": "low",
            "needs_review": True,
        }
        with self.assertRaisesRegex(SelectionError, "count"):
            _validate_selection(empty, {"E01"}, 1)
        self.assertEqual(
            _validate_selection(empty, {"E01"}, 1, allow_empty=True)["selected"],
            [],
        )

    def test_payload_uses_exact_source_text_and_stable_ids(self):
        payload, by_id = _selection_payload(
            {
                "question": "When?",
                "question_date": "2026-01-01",
                "recall_plan": {
                    "query_kind": "historical",
                    "temporal_focus": "historical",
                    "conflict_policy": "compare",
                },
                "evidence_windows": [
                    {
                        "session_id": "s1",
                        "session_index": 3,
                        "parent_chunk_index": 4,
                        "text": "exact immutable source",
                    }
                ],
            },
            8,
        )
        self.assertEqual(list(by_id), ["E01"])
        self.assertEqual(payload["candidates"][0]["text"], "exact immutable source")
        self.assertNotIn("answer", payload)


if __name__ == "__main__":
    unittest.main()
