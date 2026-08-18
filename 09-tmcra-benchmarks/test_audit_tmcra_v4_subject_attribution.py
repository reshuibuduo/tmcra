import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from ops import audit_tmcra_v4_subject_attribution as attribution


class FakeClient:
    def __init__(self, decision="quarantine_third_party"):
        self.decision = decision
        self.calls = []

    def complete(self, payload):
        self.calls.append(payload)
        memory_id = payload["candidates"][0]["memory_id"]
        if self.decision == "quarantine_third_party":
            actual_subject = "Example Reviewer"
        elif self.decision == "keep_user":
            actual_subject = "chat_user"
        else:
            actual_subject = ""
        return (
            json.dumps(
                {
                    "decisions": [
                        {
                            "memory_id": memory_id,
                            "decision": self.decision,
                            "actual_subject": actual_subject,
                            "chat_user_bridge_quote": "I wrote the email below."
                            if self.decision == "keep_user"
                            else "",
                            "reason": "The email signature attributes the address to Example Reviewer.",
                        }
                    ]
                }
            ),
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 100,
                }
            },
        )


class SubjectAttributionTests(unittest.TestCase):
    def test_pro_client_accepts_canonical_writer_pool_environment(self):
        with mock.patch.dict(
            attribution.os.environ,
            {"TMCRA_DEEPSEEK_WRITER_KEY_POOL": "key-a,key-b"},
            clear=True,
        ), mock.patch.object(attribution, "DeepSeekBatchClient") as client_type:
            attribution.DeepSeekProAttributionClient()
        self.assertEqual(
            client_type.call_args.kwargs["api_keys"], ["key-a", "key-b"]
        )
        self.assertEqual(
            client_type.call_args.kwargs["model"], "deepseek-v4-pro"
        )

    def test_direct_script_entrypoint_imports_repo_modules(self):
        script = Path(attribution.__file__).resolve()
        with tempfile.TemporaryDirectory() as temp:
            completed = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=temp,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--run-dir", completed.stdout)

    def make_database(self, root):
        database = Path(root) / "native_memory.sqlite3"
        content = (
            "From: Mcap MediaWire\nTo: me\nSubject: Project\n\nRegards,\n"
            "Example Reviewer\nCOO\ncs@example.invalid\n"
            "CONFIDENTIALITY NOTICE TO RECIPIENT"
        )
        quote = "cs@example.invalid"
        start = content.index(quote)
        end = start + len(quote)
        metadata = {
            "content_variant": "product_semantic_memory",
            "memory_layer": "fast",
            "node_kind": "atomic_user_assertion",
            "message_id": "s000_m000",
            "canonical_slot_key": "memory.user.contact.identity.email",
            "source_span": quote,
            "raw_content": quote,
            "evidence_char_start": start,
            "evidence_char_end": end,
            "event_signature": "contact.email:s000_m000:0",
        }
        facet_metadata = {
            "content_variant": "event_facet_write",
            "memory_layer": "fast",
            "facet_parent_event_signature": "contact.email:s000_m000:0",
        }
        with closing(sqlite3.connect(database)) as connection:
            connection.row_factory = sqlite3.Row
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
                (
                    "scope",
                    "session",
                    "s000_m000",
                    0,
                    0,
                    "user",
                    "",
                    content,
                    "hash",
                    "enriched",
                    "source.0",
                    1,
                    "",
                    "",
                    "",
                    "",
                ),
            )
            connection.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?,?)",
                (
                    "scope",
                    "leaf.0",
                    "The user's email address is cs@example.invalid.",
                    "memory.user.contact.identity.email",
                    1,
                    "active",
                    json.dumps(metadata),
                ),
            )
            connection.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?,?)",
                (
                    "scope",
                    "facet.0",
                    quote,
                    "memory.user.contact.identity.email.facet.0",
                    1,
                    "evidence",
                    json.dumps(facet_metadata),
                ),
            )
            connection.execute(
                "INSERT INTO slot_heads VALUES(?,?,?)",
                ("scope", "memory.user.contact.identity.email", "leaf.0"),
            )
            connection.execute(
                "INSERT INTO slot_history VALUES(?,?,?,?)",
                ("scope", "memory.user.contact.identity.email", 0, "leaf.0"),
            )
            connection.commit()
        return database

    def test_router_selects_structured_email_not_every_attachment_mention(self):
        self.assertTrue(
            attribution.document_route_reasons(
                "From: A\nTo: me\nSubject: X\nRegards,\nA\nCONFIDENTIALITY NOTICE"
            )
        )
        self.assertEqual(
            attribution.document_route_reasons(
                "I like the whisk attachment on my stand mixer."
            ),
            [],
        )

    def test_pro_decision_quarantines_fast_record_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            database = self.make_database(temp)
            jobs = attribution.scan_database(database, "scope")
            self.assertEqual(len(jobs), 1)
            self.assertEqual(len(jobs[0]["payload"]["candidates"]), 1)
            self.assertNotIn("claim_text", jobs[0]["payload"]["candidates"][0])
            self.assertNotIn(
                "canonical_slot", jobs[0]["payload"]["candidates"][0]
            )
            self.assertIn("claim_text", jobs[0]["review_candidates"][0])
            client = FakeClient()
            result = attribution.execute_job(database, jobs[0], client)
            self.assertEqual(result["quarantined_memory_ids"], ["leaf.0"])
            self.assertEqual(
                result["cascaded_quarantined_memory_ids"], ["facet.0"]
            )
            self.assertEqual(len(client.calls), 1)
            with closing(sqlite3.connect(database)) as connection:
                state, metadata_json = connection.execute(
                    "SELECT state,metadata_json FROM records WHERE memory_id='leaf.0'"
                ).fetchone()
                self.assertEqual(state, "quarantined")
                metadata = json.loads(metadata_json)
                self.assertEqual(
                    metadata["subject_attribution_decision"],
                    "quarantine_third_party",
                )
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM slot_heads").fetchone()[0],
                    0,
                )
                facet_state, facet_metadata_json = connection.execute(
                    "SELECT state,metadata_json FROM records WHERE memory_id='facet.0'"
                ).fetchone()
                self.assertEqual(facet_state, "quarantined")
                facet_metadata = json.loads(facet_metadata_json)
                self.assertEqual(
                    facet_metadata["subject_attribution_decision"],
                    "quarantine_with_parent",
                )
                self.assertEqual(
                    facet_metadata["subject_attribution_parent_memory_id"],
                    "leaf.0",
                )
            reused = attribution.execute_job(database, jobs[0], client)
            self.assertEqual(reused["status"], "reused")
            self.assertEqual(reused["physical_api_calls"], 0)
            self.assertEqual(reused["quarantined_memory_ids"], ["leaf.0"])
            self.assertEqual(
                reused["cascaded_quarantined_memory_ids"], ["facet.0"]
            )
            self.assertEqual(len(client.calls), 1)

    def test_response_must_cover_exact_candidate_set(self):
        with self.assertRaises(attribution.AttributionError):
            attribution.validate_decisions(
                {"decisions": []},
                {
                    "candidates": [
                        {"memory_id": "leaf.0", "evidence_quote": "email"}
                    ],
                    "source_segments": [{"text": "email"}],
                },
            )

    def test_new_prompt_points_all_prior_audits_to_current_completed_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            database = self.make_database(temp)
            jobs = attribution.scan_database(database, "scope")
            with closing(sqlite3.connect(database)) as connection:
                attribution._initialize(connection)
                connection.executemany(
                    "INSERT INTO v4_subject_attribution_audits("
                    "audit_id,scope_id,message_id,prompt_version,model,request_json,"
                    "request_sha256,status,response_json,response_sha256,call_metadata_json,"
                    "decisions_json,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            "saa_v1",
                            "scope",
                            "s000_m000",
                            "tmcra-v4-subject-attribution-2026-07-14.1",
                            "deepseek-v4-pro",
                            "{}",
                            "v1-request",
                            "superseded",
                            "{}",
                            "v1-response",
                            "{}",
                            "[]",
                            "superseded_by:saa_v2",
                            "old",
                            "old",
                        ),
                        (
                            "saa_v2",
                            "scope",
                            "s000_m000",
                            "tmcra-v4-subject-attribution-2026-07-14.2",
                            "deepseek-v4-pro",
                            "{}",
                            "v2-request",
                            "completed",
                            "{}",
                            "v2-response",
                            "{}",
                            "[]",
                            "",
                            "old",
                            "old",
                        ),
                    ],
                )
                connection.commit()
            result = attribution.execute_job(database, jobs[0], FakeClient())
            self.assertEqual(result["superseded_audit_count"], 2)
            with closing(sqlite3.connect(database)) as connection:
                prior = connection.execute(
                    "SELECT audit_id,status,error FROM v4_subject_attribution_audits "
                    "WHERE audit_id IN ('saa_v1','saa_v2') ORDER BY audit_id"
                ).fetchall()
            self.assertEqual(
                prior,
                [
                    ("saa_v1", "superseded", f"superseded_by:{result['audit_id']}"),
                    ("saa_v2", "superseded", f"superseded_by:{result['audit_id']}"),
                ],
            )

    def test_keep_user_cannot_bind_to_embedded_named_subject(self):
        with self.assertRaisesRegex(attribution.AttributionError, "chat_user"):
            attribution.validate_decisions(
                {
                    "decisions": [
                        {
                            "memory_id": "leaf.0",
                            "decision": "keep_user",
                            "actual_subject": "Example Reviewer",
                            "chat_user_bridge_quote": "I wrote the email below.",
                            "reason": "The sender signed the embedded email.",
                        }
                    ]
                },
                {
                    "candidates": [
                        {"memory_id": "leaf.0", "evidence_quote": "email"}
                    ],
                    "source_segments": [
                        {"text": "I wrote the email below. email"}
                    ],
                },
            )

    def test_keep_user_requires_exact_bridge_distinct_from_evidence(self):
        payload = {
            "candidates": [
                {"memory_id": "leaf.0", "evidence_quote": "Example Reviewer"}
            ],
            "source_segments": [{"text": "Example Reviewer"}],
        }
        with self.assertRaisesRegex(attribution.AttributionError, "bridge"):
            attribution.validate_decisions(
                {
                    "decisions": [
                        {
                            "memory_id": "leaf.0",
                            "decision": "keep_user",
                            "actual_subject": "chat_user",
                            "chat_user_bridge_quote": "Example Reviewer",
                            "reason": "The signature names Example Reviewer.",
                        }
                    ]
                },
                payload,
            )


if __name__ == "__main__":
    unittest.main()
