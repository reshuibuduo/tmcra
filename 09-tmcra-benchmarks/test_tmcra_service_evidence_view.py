from __future__ import annotations

import json
import unittest

from tmcra_service.evidence_view import EvidenceViewError, build_prompt_evidence


class EvidenceViewTests(unittest.TestCase):
    def test_raw_view_keeps_source_context_and_neighbor_without_internal_metadata(self) -> None:
        evidence = {
            "evidence_windows": [
                {
                    "memory_id": "m1",
                    "source_record_id": "s1",
                    "session_id": "day-1",
                    "timestamp": "2026-07-15T01:00:00Z",
                    "message_role": "user",
                    "text": "The immutable source.",
                    "db_path": "/secret/native.sqlite3",
                    "score": 9.5,
                    "memory_contexts": [
                        {
                            "role": "identity",
                            "canonical_slot": "memory.user.pet.name",
                            "claim_text": "The pet is named Tuanzi.",
                        }
                    ],
                    "attachments": [
                        {
                            "role": "override",
                            "canonical_slot": "memory.user.pet.allergy",
                            "text": "Beef supersedes chicken.",
                        }
                    ],
                    "source_group_context": [
                        {
                            "source_record_id": "s2",
                            "timestamp": "2026-07-15T02:00:00Z",
                            "message_role": "user",
                            "text": "The neighboring update.",
                        },
                        {
                            "source_record_id": "s2",
                            "timestamp": "2026-07-15T02:00:00Z",
                            "message_role": "user",
                            "text": "The neighboring update.",
                        },
                    ],
                }
            ]
        }
        rendered = build_prompt_evidence(evidence, selected_route="raw")
        self.assertEqual(rendered["mode"], "raw_hierarchical")
        self.assertIn("The immutable source.", rendered["content"])
        self.assertIn("The pet is named Tuanzi.", rendered["content"])
        self.assertIn("Beef supersedes chicken.", rendered["content"])
        self.assertEqual(rendered["content"].count("The neighboring update."), 1)
        self.assertNotIn("native.sqlite3", rendered["content"])
        self.assertNotIn("9.5", rendered["content"])
        self.assertEqual(rendered["neighbor_block_count"], 1)

    def test_compiled_view_exposes_only_the_bound_packet(self) -> None:
        packet = {
            "schema_version": "tmcra.v4.compiled-evidence-packet.1",
            "raw_evidence_reservoir": [{"evidence_id": "E1", "text": "source"}],
        }
        rendered = build_prompt_evidence(
            {
                "compiled_evidence_packet": packet,
                "planner": {"api_key": "must-not-leak"},
            },
            selected_route="compiled",
        )
        self.assertEqual(rendered["mode"], "compiled_evidence_packet")
        self.assertEqual(json.loads(rendered["content"]), packet)
        self.assertNotIn("must-not-leak", rendered["content"])

    def test_unknown_route_fails_closed(self) -> None:
        with self.assertRaises(EvidenceViewError):
            build_prompt_evidence({}, selected_route="unknown")


if __name__ == "__main__":
    unittest.main()
