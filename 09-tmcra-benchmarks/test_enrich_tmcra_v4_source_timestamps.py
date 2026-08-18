import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ops.enrich_tmcra_v4_source_timestamps import (
    TimestampEnrichmentError,
    enrich_row,
)


class TimestampEnrichmentTests(unittest.TestCase):
    def _fixture(self, root: Path):
        db = root / "memory.sqlite3"
        metadata = {
            "content_variant": "source_message",
            "source_record_id": "source.1",
            "scope_id": "scope-1",
            "session_id": "session-1",
            "session_index": 2,
            "message_index": 3,
            "speaker": "user",
            "historical_date": "2022/03/21 (Mon) 15:54",
            "timestamp": "2022-03-21T15:54:02+00:00",
            "raw_content": "I attended the baking class yesterday.",
        }
        connection = sqlite3.connect(db)
        try:
            connection.execute(
                "CREATE TABLE records(scope_id TEXT,memory_id TEXT,metadata_json TEXT)"
            )
            connection.execute(
                "INSERT INTO records VALUES(?,?,?)",
                ("scope-1", "source.1", json.dumps(metadata)),
            )
            connection.commit()
        finally:
            connection.close()
        row = {
            "question_id": "q1",
            "question": "When was the class?",
            "evidence_windows": [
                {
                    "db_path": str(db),
                    "scope_id": "scope-1",
                    "session_id": "session-1",
                    "session_index": 2,
                    "parent_chunk_index": 3,
                    "source_record_id": "source.1",
                    "text": "I attended the baking class yesterday.",
                    "source_group_context": [],
                }
            ],
        }
        return row

    def test_enriches_exact_source_without_rewriting_text(self):
        with TemporaryDirectory() as directory:
            row = self._fixture(Path(directory))
            enriched = enrich_row(row)
        source = enriched["evidence_windows"][0]
        self.assertEqual(source["text"], row["evidence_windows"][0]["text"])
        self.assertEqual(source["timestamp"], "2022-03-21T15:54:02+00:00")
        self.assertEqual(source["historical_date"], "2022/03/21 (Mon) 15:54")
        self.assertEqual(source["message_role"], "user")

    def test_uses_database_scope_when_metadata_omits_redundant_scope(self):
        with TemporaryDirectory() as directory:
            row = self._fixture(Path(directory))
            db_path = Path(row["evidence_windows"][0]["db_path"])
            connection = sqlite3.connect(db_path)
            try:
                raw = connection.execute(
                    "SELECT metadata_json FROM records WHERE scope_id=? AND memory_id=?",
                    ("scope-1", "source.1"),
                ).fetchone()[0]
                metadata = json.loads(raw)
                metadata.pop("scope_id")
                connection.execute(
                    "UPDATE records SET metadata_json=? "
                    "WHERE scope_id=? AND memory_id=?",
                    (json.dumps(metadata), "scope-1", "source.1"),
                )
                connection.commit()
            finally:
                connection.close()
            enriched = enrich_row(row)

        self.assertEqual(
            enriched["evidence_windows"][0]["timestamp"],
            "2022-03-21T15:54:02+00:00",
        )

    def test_rejects_source_text_that_differs_from_persisted_record(self):
        with TemporaryDirectory() as directory:
            row = self._fixture(Path(directory))
            row["evidence_windows"][0]["text"] = "tampered"
            with self.assertRaisesRegex(
                TimestampEnrichmentError, "identity or temporal metadata differs"
            ):
                enrich_row(row)


if __name__ == "__main__":
    unittest.main()
