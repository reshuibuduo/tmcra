import tempfile
import unittest
from pathlib import Path

from run_tmcra_v4_enrich_frozen_source import (
    FrozenSourceEnrichmentError,
    enrich_rows,
)


def candidate(parent, message, payload):
    prefix = f"TMCRA timestamp=2023-01-0{parent + 1}T00:00:00+00:00 role=user"
    return {
        "candidate_id": f"source.s001.m{message:03d}:0",
        "session_id": "s1",
        "session_index": 1,
        "parent_chunk_index": parent,
        "subchunk_index": 0,
        "source_record_id": f"source.s001.m{message:03d}:0",
        "source_char_start": 0,
        "source_char_end": len(payload),
        "text": f"{prefix}\n{payload}",
    }


class FakeV3:
    def __init__(self, fast, fingerprint="graph-1"):
        self.fast = fast
        self.fingerprint = fingerprint

    def load_online_index(self, _index_path, _db_path, _scope_id):
        return self.fast, None, [], None, [], {"graph_fingerprint": self.fingerprint}

    def scope_fingerprint(self, _db_path, _scope_id):
        return self.fingerprint


class FrozenSourceEnrichmentTests(unittest.TestCase):
    def test_attaches_context_without_changing_selected_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path, index_path = root / "db.sqlite3", root / "index.pt"
            db_path.write_bytes(b"db")
            index_path.write_bytes(b"index")
            first = candidate(0, 0, "Selected fact.")
            neighbor = candidate(1, 1, "Adjacent decisive fact.")
            selected = dict(first)
            selected["text"] = "Selected fact."
            evidence = [
                {
                    "question_id": "q1",
                    "question": "What happened?",
                    "evidence_windows": [selected],
                }
            ]
            manifest = [
                {
                    "question_id": "q1",
                    "scope_id": "scope:q1",
                    "db_path": str(db_path),
                    "index_path": str(index_path),
                }
            ]
            enriched, report = enrich_rows(
                evidence, manifest, ["q1"], v3=FakeV3([first, neighbor])
            )
            window = enriched[0]["evidence_windows"][0]
            self.assertEqual(window["text"], "Selected fact.")
            self.assertEqual(window["source_record_id"], first["source_record_id"])
            self.assertEqual(
                window["source_group_context"][0]["text"], "Adjacent decisive fact."
            )
            self.assertEqual(window["source_group_context"][0]["source_char_start"], 0)
            self.assertEqual(
                window["source_group_context"][0]["source_char_end"],
                len("Adjacent decisive fact."),
            )
            self.assertEqual(report["physical_api_calls"], 0)
            self.assertFalse(report["graph_mutated"])

    def test_fails_closed_when_graph_fingerprint_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path, index_path = root / "db.sqlite3", root / "index.pt"
            db_path.write_bytes(b"db")
            index_path.write_bytes(b"index")
            source = candidate(0, 0, "Selected fact.")
            manifest = [
                {
                    "question_id": "q1",
                    "scope_id": "scope:q1",
                    "db_path": str(db_path),
                    "index_path": str(index_path),
                }
            ]

            class Changed(FakeV3):
                def load_online_index(self, *args):
                    value = list(super().load_online_index(*args))
                    value[-1] = {"graph_fingerprint": "old"}
                    return tuple(value)

            with self.assertRaisesRegex(
                FrozenSourceEnrichmentError, "fingerprint changed"
            ):
                enrich_rows(
                    [
                        {
                            "question_id": "q1",
                            "question": "What happened?",
                            "evidence_windows": [source],
                        }
                    ],
                    manifest,
                    ["q1"],
                    v3=Changed([source], fingerprint="new"),
                )


if __name__ == "__main__":
    unittest.main()
