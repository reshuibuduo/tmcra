import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tmcra_service import subject_attribution


class FakeClient:
    def __init__(self, decision="quarantine_third_party"):
        self.decision = decision
        self.calls = []

    def complete(self, payload):
        self.calls.append(payload)
        memory_id = payload["candidates"][0]["memory_id"]
        actual_subject = "Example Reviewer" if self.decision == "quarantine_third_party" else ""
        return (
            json.dumps(
                {
                    "decisions": [
                        {
                            "memory_id": memory_id,
                            "decision": self.decision,
                            "actual_subject": actual_subject,
                            "chat_user_bridge_quote": "",
                            "reason": "The source signature names a third party.",
                        }
                    ]
                }
            ),
            {"usage": {"prompt_tokens": 100, "completion_tokens": 20}},
        )


def make_database(root):
    database = Path(root) / "native_memory.sqlite3"
    content = "From: Mcap MediaWire\nTo: me\nSubject: Project\n\nRegards,\nExample Reviewer\nCONFIDENTIALITY NOTICE"
    quote = "Example Reviewer"
    start = content.index(quote)
    metadata = {
        "content_variant": "product_semantic_memory",
        "memory_layer": "fast",
        "node_kind": "atomic_user_assertion",
        "message_id": "message-0",
        "canonical_slot_key": "memory.user.contact.identity.name",
        "source_span": quote,
        "evidence_char_start": start,
        "evidence_char_end": start + len(quote),
        "event_signature": "name:message-0:0",
    }
    facet_metadata = {
        "content_variant": "event_facet_write",
        "memory_layer": "fast",
        "facet_parent_event_signature": "name:message-0:0",
    }
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(
            """
            CREATE TABLE v4_source_journal(
              scope_id TEXT,session_id TEXT,message_id TEXT,session_index INTEGER,
              message_index INTEGER,message_role TEXT,timestamp TEXT,content TEXT,
              content_sha256 TEXT,status TEXT,source_record_id TEXT,source_turn_index INTEGER,
              source_persisted_at TEXT,enrichment_error TEXT,created_at TEXT,updated_at TEXT);
            CREATE TABLE records(
              scope_id TEXT,memory_id TEXT,value TEXT,slot_key TEXT,turn_index INTEGER,
              state TEXT,metadata_json TEXT);
            CREATE TABLE slot_heads(scope_id TEXT,slot_key TEXT,memory_id TEXT);
            CREATE TABLE slot_history(scope_id TEXT,slot_key TEXT,ordinal INTEGER,memory_id TEXT);
            """
        )
        connection.execute(
            "INSERT INTO v4_source_journal VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("scope", "session", "message-0", 0, 0, "user", "", content, "hash", "enriched", "source.0", 1, "", "", "", ""),
        )
        connection.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?,?)",
            ("scope", "leaf.0", "The user's name is Example Reviewer.", "memory.user.contact.identity.name", 1, "active", json.dumps(metadata)),
        )
        connection.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?,?)",
            ("scope", "facet.0", quote, "memory.user.contact.identity.name.facet.0", 1, "evidence", json.dumps(facet_metadata)),
        )
        connection.execute("INSERT INTO slot_heads VALUES(?,?,?)", ("scope", "memory.user.contact.identity.name", "leaf.0"))
        connection.execute("INSERT INTO slot_history VALUES(?,?,?,?)", ("scope", "memory.user.contact.identity.name", 0, "leaf.0"))
        connection.commit()
    return database


class SubjectAttributionServiceTests(unittest.TestCase):
    def test_scan_only_does_not_construct_provider_and_writes_report(self):
        with tempfile.TemporaryDirectory() as temp:
            database = make_database(temp)
            output = Path(temp) / "report.json"
            with mock.patch.object(subject_attribution, "DeepSeekProAttributionClient") as client_type:
                report = subject_attribution.run_subject_attribution(database, "scope", output)
            client_type.assert_not_called()
            self.assertEqual(report["mode"], "scan_only")
            self.assertEqual(report["routed_message_count"], 1)
            self.assertEqual(report["routed_candidate_count"], 1)
            self.assertFalse(report["gate_passed"])
            self.assertEqual(json.loads(output.read_text())["scope_id"], "scope")

    def test_apply_is_durable_and_idempotent_with_cascade(self):
        with tempfile.TemporaryDirectory() as temp:
            database = make_database(temp)
            output = Path(temp) / "report.json"
            client = FakeClient()
            first = subject_attribution.run_subject_attribution(database, "scope", output, apply=True, client=client)
            second = subject_attribution.run_subject_attribution(database, "scope", output, apply=True, client=client)
            self.assertTrue(first["gate_passed"])
            self.assertTrue(second["gate_passed"])
            self.assertEqual(first["physical_api_calls"], 1)
            self.assertEqual(second["physical_api_calls"], 0)
            self.assertEqual(first["usage"]["prompt_tokens"], 100)
            self.assertEqual(first["estimated_cost_cny"], 0.00042)
            self.assertEqual(first["resolved_routed_candidate_count"], 1)
            self.assertEqual(first["quarantined_count"], 2)
            self.assertEqual(len(client.calls), 1)

    def test_apply_fails_closed_and_persists_unresolved_report(self):
        with tempfile.TemporaryDirectory() as temp:
            database = make_database(temp)
            output = Path(temp) / "report.json"
            failing = mock.Mock()
            failing.complete.side_effect = RuntimeError("provider down")
            with self.assertRaises(subject_attribution.AttributionError):
                subject_attribution.run_subject_attribution(
                    database, "scope", output, apply=True, client=failing
                )
            report = json.loads(output.read_text())
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["unresolved_routed_candidate_count"], 1)
            self.assertFalse(report["gate_passed"])


if __name__ == "__main__":
    unittest.main()
