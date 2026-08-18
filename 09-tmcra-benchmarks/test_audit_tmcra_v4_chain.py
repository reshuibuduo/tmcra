import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import audit_tmcra_v4_chain as audit
import migrate_tmcra_v4_challenge_lifecycle as challenge_migration
import migrate_tmcra_v4_keep_parallel as keep_parallel_migration


class SlowContextProvenanceContractTests(unittest.TestCase):
    def test_current_summary_contract_is_part_of_expected_provenance(self):
        provenance = audit._expected_slow_context_provenance(
            {
                "capsule_id": "cap.1",
                "revision": 2,
                "patch_id": "patch.1",
                "summary_contract_version": audit.SLOW_SUMMARY_CONTRACT_VERSION,
            },
            {"claim_id": "claim.1", "canonical_slot": "preference.color"},
            [{"source_record_id": "source.1"}],
            "capsule-summary::cap.1:r2",
        )

        self.assertEqual(
            provenance["summary_contract_version"],
            audit.SLOW_SUMMARY_CONTRACT_VERSION,
        )

    def test_legacy_summary_contract_does_not_gain_current_marker(self):
        provenance = audit._expected_slow_context_provenance(
            {"capsule_id": "cap.1", "revision": 1, "patch_id": "patch.1"},
            {"claim_id": "claim.1", "canonical_slot": "preference.color"},
            [{"source_record_id": "source.1"}],
            "capsule-summary::cap.1:r1",
        )

        self.assertNotIn("summary_contract_version", provenance)


class V4ChainAuditTests(unittest.TestCase):
    def test_exact_offsets_allow_repeated_source_quote(self):
        issues = []
        self.assertTrue(
            audit._source_span_quote(
                {"content": "same and same"},
                9,
                13,
                "same",
                label="repeated quote",
                issues=issues,
            )
        )
        self.assertEqual(issues, [])

    def test_source_message_index_keeps_database_identity_and_ambiguity(self):
        first_db = Path("first.sqlite3")
        second_db = Path("second.sqlite3")
        first = audit._identity(first_db, "scope", "source.1")
        duplicate = audit._identity(first_db, "scope", "source.2")
        isolated = audit._identity(second_db, "scope", "source.3")
        by_database, by_scope = audit._source_message_indices(
            {
                first: {"message_id": "message.1"},
                duplicate: {"message_id": "message.1"},
                isolated: {"message_id": "message.1"},
            }
        )

        self.assertIsNone(
            audit._source_for_message(
                by_database,
                db_path=first_db,
                scope_id="scope",
                message_id="message.1",
            )
        )
        self.assertIsNotNone(
            audit._source_for_message(
                by_database,
                db_path=second_db,
                scope_id="scope",
                message_id="message.1",
            )
        )
        self.assertEqual(len(by_scope[("scope", "message.1")]), 3)

    def test_semantic_identifier_named_fallback_is_not_an_operational_marker(self):
        self.assertEqual(
            audit._artifact_markers(
                {
                    "ignored_evidence_ids": ["memory.user.yoga.membership.plan.fallback.plan:302:0"],
                    "route": "deterministic_noop",
                }
            ),
            [],
        )
        self.assertTrue(audit._artifact_markers({"route_reason": "fallback to another model"}))
        self.assertTrue(audit._artifact_markers({"status": "retryable"}))
        self.assertTrue(audit._artifact_markers("retryable"))
        self.assertEqual(
            audit._artifact_markers(
                {
                    "content": "The user has a fallback plan.",
                    "raw_response": '{"claim":"fallback plan"}',
                    "request": {
                        "messages": [
                            {"role": "user", "content": "My fallback plan is yoga."}
                        ]
                    },
                }
            ),
            [],
        )
        self.assertEqual(
            audit._artifact_markers({"messages": [{"source_spans": [{"text": "retry the user's request"}]}]}),
            [],
        )

    def test_slow_pro_route_requires_conflict_or_exact_partition_contract(self):
        self.assertTrue(
            audit._slow_pro_route_is_justified(
                {"route": "pro", "route_reason": "same_slot_correction"}
            )
        )
        self.assertTrue(
            audit._slow_pro_route_is_justified(
                {
                    "route": "pro",
                    "route_reason": "same_slot_distinct_support_semantics",
                }
            )
        )
        self.assertTrue(
            audit._slow_pro_route_is_justified(
                {
                    "route": "pro",
                    "route_reason": "initial_multi_slot_semantic_partition",
                    "semantic_partition_contract_version": (
                        audit.CURRENT_SLOW_PARTITION_CONTRACT_VERSION
                    ),
                    "semantic_partition_mode": "manage",
                    "required_operation_evidence_ids": ["leaf.0", "leaf.1"],
                }
            )
        )
        partition = {
            "route": "pro",
            "route_reason": "semantic_partition_migration",
            "semantic_partition_contract_version": (
                audit.CURRENT_SLOW_PARTITION_CONTRACT_VERSION
            ),
            "semantic_partition_mode": "migrate",
            "semantic_partition_capsule_ids": ["cap_one"],
        }
        self.assertTrue(audit._slow_pro_route_is_justified(partition))
        for field in (
            "semantic_partition_contract_version",
            "semantic_partition_mode",
            "semantic_partition_capsule_ids",
        ):
            invalid = dict(partition)
            del invalid[field]
            self.assertFalse(audit._slow_pro_route_is_justified(invalid))
        self.assertFalse(
            audit._slow_pro_route_is_justified(
                {
                    **partition,
                    "route": "flash_to_pro",
                }
            )
        )
        self.assertTrue(
            audit._slow_pro_route_is_justified(
                {
                    "route": "pro",
                    "route_reason": "generic_region_semantic_management",
                    "semantic_partition_contract_version": (
                        audit.CURRENT_SLOW_PARTITION_CONTRACT_VERSION
                    ),
                    "semantic_partition_mode": "manage",
                    "required_operation_evidence_ids": ["leaf.0", "leaf.1"],
                }
            )
        )

    def test_legacy_no_gold_sentinel_must_be_literal_false(self):
        self.assertEqual(
            audit._retrieval_forbidden({"runtime_input_has_gold": False}), []
        )
        self.assertTrue(
            audit._retrieval_forbidden({"runtime_input_has_gold": True})
        )
        self.assertTrue(
            audit._retrieval_forbidden({"runtime_input_has_gold": "false"})
        )

    def test_retrieval_leak_scan_does_not_reject_user_hard_mode_text(self):
        self.assertEqual(
            audit._retrieval_forbidden(
                {
                    "evidence_windows": [
                        {"text": "I finished the game on hard mode."}
                    ]
                }
            ),
            [],
        )
        self.assertTrue(audit._retrieval_forbidden({"hard_mode": True}))

    def test_audit_tracks_current_online_index_contract(self):
        self.assertEqual(
            audit.ONLINE_INDEX_SCHEMA_VERSION,
            "tmcra.v4.online-index.3",
        )

    def make_run(self, *, bad=False, shadow_only=False, retrieval=False, content="I prefer tea.", stored_value=None):
        root = Path(tempfile.mkdtemp())
        worker = root / "worker_000"
        worker.mkdir()
        scope = "tmcra_v4:q1"
        message_id = "s000_m000"
        source_id = "source.0"
        (root / "writer_input.json").write_text(json.dumps([{
            "question_id": "q1",
            "session_id": "session-0",
            "messages": [{"role": "user", "content": content}],
        }]), encoding="utf-8")
        db = worker / "native_memory.sqlite3"
        with sqlite3.connect(db) as con:
            con.executescript("""
            CREATE TABLE records(
                scope_id TEXT,memory_id TEXT,category TEXT,slot_key TEXT,value TEXT,
                relation TEXT,anchor_concepts_json TEXT,evidence_anchors_json TEXT,
                salience REAL,confidence REAL,source_kind TEXT,turn_index INTEGER,
                state TEXT,supersedes_json TEXT,metadata_json TEXT,
                PRIMARY KEY(scope_id,memory_id));
            CREATE TABLE memory_edges(
                scope_id TEXT,edge_id TEXT,source_memory_id TEXT,target_memory_id TEXT,
                edge_type TEXT,score REAL,model_score REAL,evidence_turn INTEGER,
                evidence TEXT,metadata_json TEXT,PRIMARY KEY(scope_id,edge_id));
            CREATE TABLE slot_heads(scope_id TEXT,slot_key TEXT,memory_id TEXT,
                PRIMARY KEY(scope_id,slot_key));
            CREATE TABLE audit_turn_log(scope_id TEXT,event_index INTEGER,payload_json TEXT);
            CREATE TABLE v4_batch_journal(
                batch_id TEXT,scope_id TEXT,session_id TEXT,batch_index INTEGER,
                request_json TEXT,request_sha256 TEXT,status TEXT,api_started_at TEXT,
                response_json TEXT,response_sha256 TEXT,response_metadata_json TEXT,
                error TEXT,created_at TEXT,updated_at TEXT);
            CREATE TABLE v4_source_journal(
                scope_id TEXT,message_id TEXT,status TEXT,source_record_id TEXT,
                source_persisted_at TEXT);
            """)
            source_meta = {
                "content_variant": "source_message",
                "memory_layer": "fast",
                "node_kind": "immutable_source_message",
                "immutable_evidence_leaf": True,
                "raw_content": content,
                "source_span": content,
                "source_turn_text": content,
                "message_id": message_id,
                "session_id": "session-0",
                "session_index": 0,
                "message_index": 0,
                "event_id": "event.0",
                "speaker": "user",
                "source_record_id": source_id,
                "enrichment_status": "pending" if bad else "enriched",
            }
            if not shadow_only:
                con.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    scope, source_id, "source", "source.s000.m000", stored_value if stored_value is not None else content,
                    "dialogue_source", "[]", "[]", .7, 1.0, "writer", 0,
                    "evidence", "[]", json.dumps(source_meta),
                ))
                fast_meta = {
                    "content_variant": "product_semantic_memory",
                    "memory_layer": "fast",
                    "node_kind": "atomic_user_assertion",
                    "atomic_evidence_leaf": True,
                    "authority": "user_assertion",
                    "message_id": message_id,
                    "session_index": 0,
                    "message_index": 0,
                    "event_id": "event.0",
                    "parent_chunk_index": 0,
                    "source_record_id": source_id,
                    "evidence_char_start": 0,
                    "evidence_char_end": len(content),
                    "source_span": content,
                    "evidence_quote": content,
                    "durability": "durable",
                    "canonical_slot_key": "preference.beverage",
                    "provenance": [{
                        "source_record_id": source_id,
                        "message_id": message_id,
                        "source_char_start": 0,
                        "source_char_end": len(content),
                        "evidence_quote": content,
                    }],
                }
                con.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    scope, "leaf.0", "fact", "preference.beverage", content,
                    "states", "[]", "[]", .7, .8, "writer", 0,
                    "active", "[]", json.dumps(fast_meta),
                ))
                if not bad:
                    con.execute("INSERT INTO memory_edges VALUES(?,?,?,?,?,?,?,?,?,?)", (
                        scope, "edge.0", "leaf.0", source_id, "grounded_in", 1.0,
                        0.0, 0, content, json.dumps({"edge_source": "product_writer_provenance"}),
                    ))
                con.execute("INSERT INTO slot_heads VALUES(?,?,?)", (scope, "preference.beverage", "leaf.0"))
                con.execute("INSERT INTO audit_turn_log VALUES(?,?,?)", (
                    scope, 0, json.dumps({"text": content, "speaker": "user",
                        "record_ids": [source_id], "metadata": {
                            "message_id": message_id, "source_record_id": source_id,
                        }}),
                ))
            request = {
                "schema_version": "tmcra.memory-write-batch.v4",
                "batch_id": f"{scope}:session-0:b0000",
                "messages": [{
                    "message_id": message_id, "message_role": "user", "timestamp": "",
                    "source_spans": [{"span_id": "e0", "text": content}],
                }],
                "unresolved_interactions": [],
            }
            con.execute("INSERT INTO v4_batch_journal VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                request["batch_id"], scope, "session-0", 0, json.dumps(request),
                audit._sha(json.dumps(request)), "committed", "2026-01-01T00:00:00Z",
                "", "", "{}", "", "", "",
            ))
            con.execute("INSERT INTO v4_source_journal VALUES(?,?,?,?,?)", (
                scope, message_id, "pending" if bad else "enriched", source_id,
                "2025-12-31T23:59:59Z",
            ))
            if shadow_only:
                con.execute("CREATE TABLE v4_source_records(source_record_id TEXT,scope_id TEXT,message_id TEXT,content TEXT)")
                con.execute("INSERT INTO v4_source_records VALUES(?,?,?,?)", (source_id, scope, message_id, content))
                con.execute("CREATE TABLE v4_fast_assertion_leaves(leaf_id TEXT,scope_id TEXT,message_id TEXT,evidence_quote TEXT,metadata_json TEXT)")
                con.execute("INSERT INTO v4_fast_assertion_leaves VALUES(?,?,?,?,?)", ("leaf.0", scope, message_id, content, "{}"))
            con.commit()
        if retrieval:
            out = root / "retrieval"
            out.mkdir()
            (out / "retrieval_debug.jsonl").write_text(json.dumps({
                "source_inventory_count": 1, "fast_inventory_count": 1,
                "slow_capsule_count": 1,
                "candidate_paths_executed": {"source": True, "fast": True, "slow": False},
            }) + "\n", encoding="utf-8")
            (out / "evidence_windows.jsonl").write_text(json.dumps({
                "evidence_windows": [{"memory_id": source_id, "text": content}],
            }) + "\n", encoding="utf-8")
            (out / "report.json").write_text(json.dumps({"status": "complete"}), encoding="utf-8")
        return root, db

    def add_subject_audit_history(self, db, *, missing_successor=False):
        scope = "tmcra_v4:q1"
        message_id = "s000_m000"
        active_id = "saa_current"
        decisions = [
            {
                "memory_id": "leaf.0",
                "decision": "keep_user",
                "actual_subject": "chat_user",
                "chat_user_bridge_quote": "I",
                "reason": "Outside conversational voice explicitly states the preference.",
            }
        ]
        with sqlite3.connect(db) as con:
            con.execute(
                """
                CREATE TABLE v4_subject_attribution_audits (
                  audit_id TEXT PRIMARY KEY,scope_id TEXT,message_id TEXT,
                  prompt_version TEXT,model TEXT,request_json TEXT,
                  request_sha256 TEXT,status TEXT,response_json TEXT,
                  response_sha256 TEXT,call_metadata_json TEXT,decisions_json TEXT,
                  error TEXT,created_at TEXT,updated_at TEXT)
                """
            )
            con.execute(
                "INSERT INTO v4_subject_attribution_audits VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "saa_old",
                    scope,
                    message_id,
                    "tmcra-v4-subject-attribution-2026-07-14.1",
                    "deepseek-v4-pro",
                    "{}",
                    "old-request",
                    "superseded",
                    "{}",
                    "old-response",
                    "{}",
                    "[]",
                    "superseded_by:saa_missing"
                    if missing_successor
                    else f"superseded_by:{active_id}",
                    "old",
                    "old",
                ),
            )
            con.execute(
                "INSERT INTO v4_subject_attribution_audits VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    active_id,
                    scope,
                    message_id,
                    audit.SUBJECT_ATTRIBUTION_PROMPT_VERSION,
                    "deepseek-v4-pro",
                    "{}",
                    "current-request",
                    "completed",
                    "{}",
                    "current-response",
                    "{}",
                    json.dumps(decisions),
                    "",
                    "new",
                    "new",
                ),
            )
            metadata = json.loads(
                con.execute(
                    "SELECT metadata_json FROM records WHERE memory_id='leaf.0'"
                ).fetchone()[0]
            )
            metadata.update(
                {
                    "subject_attribution_audit_id": active_id,
                    "subject_attribution_decision": "keep_user",
                    "subject_attribution_chat_user_bridge_quote": "I",
                    "subject_attribution_prompt_version": audit.SUBJECT_ATTRIBUTION_PROMPT_VERSION,
                }
            )
            con.execute(
                "UPDATE records SET metadata_json=? WHERE memory_id='leaf.0'",
                (json.dumps(metadata),),
            )
            con.commit()

    def add_slow_revision(
        self,
        db,
        *,
        scope="tmcra_v4:q1",
        capsule_id="capsule.0",
        revision=1,
        state="active",
        status="active",
        claims=None,
        source_id="source.0",
    ):
        with sqlite3.connect(db) as con:
            fast_meta = json.loads(
                con.execute(
                    "SELECT metadata_json FROM records WHERE scope_id=? AND memory_id='leaf.0'",
                    ("tmcra_v4:q1",),
                ).fetchone()[0]
            )
            parent = {
                key: fast_meta[key]
                    for key in audit.SLOW_SOURCE_PARENT_KEYS
            }
            slow_claims = claims
            if slow_claims is None:
                slow_claims = [{
                    "claim_id": "claim.0",
                    "canonical_slot": "preference.beverage",
                    "text": "The user prefers tea.",
                    "support": ["leaf.0"],
                    "counterevidence": [],
                    "source_parents": [parent],
                }]
            metadata = {
                "memory_layer": "slow",
                "content_variant": "slow_memory_capsule",
                "capsule_id": capsule_id,
                "revision": revision,
                "status": status,
                "claims": slow_claims,
                "source_parents": [parent],
            }
            con.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    scope,
                    f"slow.{capsule_id}.r{revision}",
                    "slow",
                    "preference.beverage",
                    "The user prefers tea.",
                    "states",
                    "[]",
                    "[]",
                    .7,
                    .8,
                    "slow_graph",
                    revision,
                    state,
                    "[]",
                    json.dumps(metadata),
                ),
            )
            con.commit()

    def add_current_v47_patch(self, db):
        scope = "tmcra_v4:q1"
        region_key = "preferences"
        capsule_id = audit._current_v47_capsule_id(
            scope, region_key, "preference.beverage"
        )
        patch_id = "patch.current.0"
        job_id = "job.current.0"
        self.add_slow_revision(db, capsule_id=capsule_id)
        operation = {
            "action": "create",
            "capsule_key": "preference.beverage",
            "claims": [{
                "claim_id": "claim.0",
                "canonical_slot": "preference.beverage",
                "text": "The user prefers tea.",
                "support": ["leaf.0"],
                "counterevidence": [],
            }],
        }
        with sqlite3.connect(db) as con:
            con.executescript("""
            CREATE TABLE slow_graph_jobs(
                job_id TEXT, idempotency_key TEXT, scope_id TEXT, region_key TEXT,
                evidence_ids_json TEXT, metadata_json TEXT, status TEXT,
                attempts INTEGER, last_error TEXT, created_at INTEGER, updated_at INTEGER
            );
            CREATE TABLE slow_graph_patches(
                patch_id TEXT, job_id TEXT, scope_id TEXT, region_key TEXT,
                manager_model TEXT, patch_json TEXT, call_metadata_json TEXT,
                applied_at INTEGER
            );
            CREATE TABLE slow_graph_patch_operations(
                operation_id TEXT, patch_id TEXT, ordinal INTEGER, capsule_id TEXT,
                action TEXT, base_revision INTEGER, result_revision INTEGER,
                operation_json TEXT, created_at INTEGER
            );
            """)
            metadata = json.loads(
                con.execute(
                    "SELECT metadata_json FROM records WHERE memory_id=?",
                    (f"slow.{capsule_id}.r1",),
                ).fetchone()[0]
            )
            metadata.update({
                "patch_id": patch_id,
                "capsule_id": capsule_id,
                "revision": 1,
                "region_key": region_key,
                "action": "create",
                "prompt_version": audit.CURRENT_SLOW_PROMPT_VERSION,
                "summary_contract_version": audit.SLOW_SUMMARY_CONTRACT_VERSION,
                "partition_contract_version": audit.CURRENT_SLOW_PARTITION_CONTRACT_VERSION,
            })
            con.execute(
                "UPDATE records SET metadata_json=? WHERE memory_id=?",
                (json.dumps(metadata), f"slow.{capsule_id}.r1"),
            )
            con.execute(
                "INSERT INTO slow_graph_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id, "idem.current.0", scope, region_key, "[\"leaf.0\"]",
                    json.dumps({"model_config": {"prompt_version": audit.CURRENT_SLOW_PROMPT_VERSION}}),
                    "completed", 1, "", 0, 0,
                ),
            )
            con.execute(
                "INSERT INTO slow_graph_patches VALUES(?,?,?,?,?,?,?,?)",
                (
                    patch_id, job_id, scope, region_key, "deepseek-v4-flash",
                    json.dumps({"operations": [operation]}),
                    json.dumps({
                        "route": "flash",
                        "prompt_version": audit.CURRENT_SLOW_PROMPT_VERSION,
                    }),
                    1,
                ),
            )
            con.execute(
                "INSERT INTO slow_graph_patch_operations VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "operation.current.0", patch_id, 0, capsule_id, "create",
                    None, 1, json.dumps(operation), 1,
                ),
            )
            con.commit()
        return {
            "patch_id": patch_id,
            "job_id": job_id,
            "capsule_id": capsule_id,
            "operation": operation,
        }

    def add_second_scope_without_fast(self, root, db):
        input_path = root / "writer_input.json"
        rows = json.loads(input_path.read_text(encoding="utf-8"))
        rows.append({
            "question_id": "q2",
            "scope_id": "tmcra_v4:q2",
            "session_id": "session-0",
            "messages": [{"role": "user", "content": "I prefer coffee."}],
        })
        input_path.write_text(json.dumps(rows), encoding="utf-8")
        content = "I prefer coffee."
        scope = "tmcra_v4:q2"
        message_id = "s000_m000"
        source_meta = {
            "content_variant": "source_message",
            "memory_layer": "fast",
            "node_kind": "immutable_source_message",
            "immutable_evidence_leaf": True,
            "raw_content": content,
            "source_span": content,
            "source_turn_text": content,
            "message_id": message_id,
            "session_id": "session-0",
            "session_index": 0,
            "message_index": 0,
            "event_id": "event.0",
            "speaker": "user",
            "source_record_id": "source.0",
            "enrichment_status": "enriched",
        }
        with sqlite3.connect(db) as con:
            con.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    scope, "source.0", "source", "source.s000.m000", content,
                    "dialogue_source", "[]", "[]", .7, 1.0, "writer", 0,
                    "evidence", "[]", json.dumps(source_meta),
                ),
            )
            parent = {
                "session_index": 0,
                "parent_chunk_index": 0,
                "message_index": 0,
                "source_record_id": "source.0",
                "event_id": "event.0",
                "evidence_char_start": 0,
                "evidence_char_end": len(content),
            }
            slow_meta = {
                "memory_layer": "slow",
                "content_variant": "slow_memory_capsule",
                "capsule_id": "capsule.cross-scope",
                "revision": 1,
                "status": "active",
                "claims": [{
                    "claim_id": "claim.cross-scope",
                    "canonical_slot": "preference.beverage",
                    "text": "The user prefers coffee.",
                    "support": ["leaf.0"],
                    "counterevidence": [],
                    "source_parents": [parent],
                }],
                "source_parents": [parent],
            }
            con.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    scope, "slow.capsule.cross-scope.r1", "slow", "preference.beverage",
                    "The user prefers coffee.", "states", "[]", "[]", .7, .8,
                    "slow_graph", 1, "active", "[]", json.dumps(slow_meta),
                ),
            )
            request = {
                "schema_version": "tmcra.memory-write-batch.v4",
                "batch_id": f"{scope}:session-0:b0000",
                "messages": [{
                    "message_id": message_id,
                    "message_role": "user",
                    "timestamp": "",
                    "source_spans": [{"span_id": "e0", "text": content}],
                }],
                "unresolved_interactions": [],
            }
            con.execute(
                "INSERT INTO v4_batch_journal VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    request["batch_id"], scope, "session-0", 0, json.dumps(request),
                    audit._sha(json.dumps(request)), "committed", "", "", "", "{}", "", "", "",
                ),
            )
            con.execute(
                "INSERT INTO v4_source_journal VALUES(?,?,?,?,?)",
                (scope, message_id, "enriched", "source.0", "2025-12-31T23:59:59Z"),
            )
            con.execute(
                "INSERT INTO audit_turn_log VALUES(?,?,?)",
                (scope, 0, json.dumps({
                    "text": content,
                    "speaker": "user",
                    "record_ids": ["source.0"],
                    "metadata": {"message_id": message_id, "source_record_id": "source.0"},
                })),
            )
            con.commit()

    def test_incorrect_fast_span_is_rejected(self):
        root, db = self.make_run()
        with sqlite3.connect(db) as con:
            metadata = json.loads(
                con.execute(
                    "SELECT metadata_json FROM records WHERE scope_id=? AND memory_id=?",
                    ("tmcra_v4:q1", "leaf.0"),
                ).fetchone()[0]
            )
            metadata["evidence_char_end"] -= 1
            con.execute(
                "UPDATE records SET metadata_json=? WHERE scope_id=? AND memory_id=?",
                (json.dumps(metadata), "tmcra_v4:q1", "leaf.0"),
            )
            con.commit()
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(
            any("source slice does not equal quote exactly" in item for item in report["issues"])
        )

    def test_cross_scope_bare_fast_id_is_not_resolved(self):
        root, db = self.make_run()
        self.add_second_scope_without_fast(root, db)
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(any("composite identity" in item for item in report["issues"]))

    def test_only_unique_latest_slow_revision_is_audited(self):
        root, db = self.make_run()
        self.add_slow_revision(db, revision=1, claims=[])
        self.add_slow_revision(db, revision=2)
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertTrue(report["passed"], report["issues"])
        self.assertEqual(report["counts"]["slow_records"], 1)

    def test_active_slow_cross_slot_support_fails_semantic_audit(self):
        root, db = self.make_run()
        self.add_slow_revision(db)
        with sqlite3.connect(db) as con:
            metadata = json.loads(
                con.execute(
                    "SELECT metadata_json FROM records "
                    "WHERE memory_id='slow.capsule.0.r1'"
                ).fetchone()[0]
            )
            metadata["claims"][0]["canonical_slot"] = "business.ownership"
            con.execute(
                "UPDATE records SET metadata_json=? "
                "WHERE memory_id='slow.capsule.0.r1'",
                (json.dumps(metadata),),
            )
            con.commit()

        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertFalse(report["slow_promotion_coverage"]["complete"])
        self.assertEqual(
            report["slow_promotion_coverage"][
                "semantic_integrity_issue_count"
            ],
            1,
        )
        self.assertEqual(
            report["slow_promotion_coverage"]["semantic_integrity_issues"][0][
                "code"
            ],
            "support_canonical_slot_mismatch",
        )

    def test_active_slow_claim_merging_distinct_fast_values_fails_semantic_audit(self):
        content = "The user prefers tea and coffee."
        root, db = self.make_run(content=content)
        coffee_start = content.index("coffee")
        with sqlite3.connect(db) as con:
            metadata = json.loads(
                con.execute(
                    "SELECT metadata_json FROM records WHERE memory_id='leaf.0'"
                ).fetchone()[0]
            )
            metadata.update(
                {
                    "evidence_char_start": coffee_start,
                    "evidence_char_end": coffee_start + len("coffee"),
                    "source_span": "coffee",
                    "evidence_quote": "coffee",
                    "provenance": [
                        {
                            "source_record_id": "source.0",
                            "message_id": "s000_m000",
                            "source_char_start": coffee_start,
                            "source_char_end": coffee_start + len("coffee"),
                            "evidence_quote": "coffee",
                        }
                    ],
                }
            )
            con.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "tmcra_v4:q1",
                    "leaf.1",
                    "fact",
                    "preference.beverage.detail",
                    "coffee",
                    "states",
                    "[]",
                    "[]",
                    0.7,
                    0.8,
                    "writer",
                    0,
                    "parallel_active",
                    "[]",
                    json.dumps(metadata),
                ),
            )
            con.execute(
                "INSERT INTO memory_edges VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    "tmcra_v4:q1",
                    "edge.1",
                    "leaf.1",
                    "source.0",
                    "grounded_in",
                    1.0,
                    0.0,
                    0,
                    "coffee",
                    json.dumps({"edge_source": "product_writer_provenance"}),
                ),
            )
            con.execute(
                "INSERT INTO slot_heads VALUES(?,?,?)",
                (
                    "tmcra_v4:q1",
                    "preference.beverage.detail",
                    "leaf.1",
                ),
            )
            con.commit()
        self.add_slow_revision(
            db,
            claims=[
                {
                    "claim_id": "claim.merged",
                    "canonical_slot": "preference.beverage",
                    "text": "The user has beverage preferences.",
                    "support": ["leaf.0", "leaf.1"],
                    "counterevidence": [],
                    "source_parents": [],
                }
            ],
        )

        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])

        self.assertFalse(report["passed"])
        self.assertIn(
            "support_distinct_fast_values_merged",
            {
                issue["code"]
                for issue in report["slow_promotion_coverage"][
                    "semantic_integrity_issues"
                ]
            },
        )

    def test_complementary_parent_and_subslot_support_bundle_is_allowed(self):
        db_path = "/tmp/worker.sqlite3"
        scope_id = "scope"
        common = {
            "durability": "durable",
            "subject_signature": "user",
            "graph_entity_key": "volunteering.box-office",
            "memory_family": "schedule",
            "polarity": "positive",
        }

        def leaf(memory_id, slot, text):
            return {
                "memory_id": memory_id,
                "value": text,
                "state": "active",
                "relation": "routine",
                "metadata": {**common, "canonical_slot_key": slot},
            }

        parent = (db_path, scope_id, "fast.parent")
        child = (db_path, scope_id, "fast.child")
        evidence = {
            parent: leaf(
                "fast.parent",
                "memory.user.schedule.routine.volunteering",
                "The user volunteers at the box office on Fridays.",
            ),
            child: leaf(
                "fast.child",
                "memory.user.schedule.routine.volunteering.time",
                "The shift runs from 5 PM to 8 PM.",
            ),
        }

        self.assertTrue(
            audit._controlled_complementary_support_bundle(
                "memory.user.schedule.routine.volunteering",
                {parent, child},
                evidence,
            )
        )
        evidence[child]["metadata"]["graph_entity_key"] = "different-entity"
        self.assertFalse(
            audit._controlled_complementary_support_bundle(
                "memory.user.schedule.routine.volunteering",
                {parent, child},
                evidence,
            )
        )

    def test_current_v47_allows_multiple_capsule_heads_in_one_region(self):
        base = {
            "db": "/tmp/worker.sqlite3",
            "scope_id": "scope",
            "slow_revision": 1,
        }
        def record(capsule_id, slot, evidence):
            return {
                **base,
                "metadata": {
                    "capsule_id": capsule_id,
                    "revision": 1,
                    "status": "active",
                    "region_key": "preferences",
                    "summary_contract_version": audit.SLOW_SUMMARY_CONTRACT_VERSION,
                    "partition_contract_version": audit.CURRENT_SLOW_PARTITION_CONTRACT_VERSION,
                    "claims": [{
                        "claim_id": f"claim.{capsule_id}",
                        "canonical_slot": slot,
                        "text": f"Claim for {slot}.",
                        "support": [evidence],
                        "counterevidence": [],
                    }],
                },
            }
        issues = audit._active_slow_partition_issues([
            record("cap.a", "preference.coffee", "fast.a"),
            record("cap.b", "preference.tea", "fast.b"),
        ])
        self.assertEqual(issues, [])

    def test_current_v47_allows_same_slot_for_different_referents(self):
        base = {
            "db": "/tmp/worker.sqlite3",
            "scope_id": "scope",
            "slow_revision": 1,
        }
        def record(capsule_id, text, evidence):
            return {
                **base,
                "metadata": {
                    "capsule_id": capsule_id,
                    "revision": 1,
                    "status": "active",
                    "region_key": "subscriptions",
                    "summary_contract_version": audit.SLOW_SUMMARY_CONTRACT_VERSION,
                    "partition_contract_version": audit.CURRENT_SLOW_PARTITION_CONTRACT_VERSION,
                    "claims": [{
                        "claim_id": f"claim.{capsule_id}",
                        "canonical_slot": "subscription.streaming.service",
                        "text": text,
                        "support": [evidence],
                        "counterevidence": [],
                    }],
                },
            }
        issues = audit._active_slow_partition_issues([
            record("cap.prime", "The user has Prime Video.", "fast.prime"),
            record("cap.netflix", "The user has Netflix.", "fast.netflix"),
        ])
        self.assertEqual(issues, [])

    def test_current_v47_audits_cross_capsule_evidence_and_claim_collisions(self):
        base = {
            "db": "/tmp/worker.sqlite3",
            "scope_id": "scope",
            "slow_revision": 1,
        }
        def record(capsule_id):
            return {
                **base,
                "metadata": {
                    "capsule_id": capsule_id,
                    "revision": 1,
                    "status": "active",
                    "region_key": "preferences",
                    "summary_contract_version": audit.SLOW_SUMMARY_CONTRACT_VERSION,
                    "partition_contract_version": audit.CURRENT_SLOW_PARTITION_CONTRACT_VERSION,
                    "claims": [{
                        "claim_id": f"claim.{capsule_id}",
                        "canonical_slot": "preference.beverage",
                        "text": "A beverage preference.",
                        "support": ["fast.same"],
                        "counterevidence": [],
                    }],
                },
            }
        codes = {
            item["code"]
            for item in audit._active_slow_partition_issues(
                [record("cap.a"), record("cap.b")]
            )
        }
        self.assertEqual(
            codes,
            {
                "duplicate_evidence_across_active_capsules",
                "semantic_claim_in_multiple_active_capsules",
            },
        )

    def test_current_v47_allows_support_fanout_to_distinct_claims(self):
        base = {
            "db": "/tmp/worker.sqlite3",
            "scope_id": "scope",
            "slow_revision": 1,
        }

        def record(capsule_id, slot, text):
            return {
                **base,
                "metadata": {
                    "capsule_id": capsule_id,
                    "revision": 1,
                    "status": "active",
                    "region_key": "hobbies",
                    "summary_contract_version": audit.SLOW_SUMMARY_CONTRACT_VERSION,
                    "partition_contract_version": audit.CURRENT_SLOW_PARTITION_CONTRACT_VERSION,
                    "claims": [{
                        "claim_id": f"claim.{capsule_id}",
                        "canonical_slot": slot,
                        "text": text,
                        "support": ["fast.shared"],
                        "counterevidence": [],
                    }],
                },
            }

        issues = audit._active_slow_partition_issues([
            record("cap.hobby", "memory.user.hobbies.preference", "The user enjoys painting."),
            record("cap.source", "memory.user.hobbies.inspiration", "Nature inspires the user's painting."),
        ])

        self.assertEqual(issues, [])

    def test_collect_jsonl_ignores_recovery_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_worker = root / "writer" / "worker_000"
            canonical_worker.mkdir(parents=True)
            backup = root / "writer_recovery_backups" / "worker_000_pre_smoke"
            backup.mkdir(parents=True)
            name = "product_writer_raw_responses.jsonl"
            (root / name).write_text('{"call_key":"root"}\n', encoding="utf-8")
            (canonical_worker / name).write_text(
                '{"call_key":"worker"}\n', encoding="utf-8"
            )
            (backup / name).write_text(
                '{"call_key":"root"}\n', encoding="utf-8"
            )

            rows = audit._collect_jsonl(root, (name,))

        self.assertEqual(
            [record["call_key"] for _, record in rows],
            ["root", "worker"],
        )

    def test_current_v47_summary_preserves_every_final_claim_text(self):
        claims = [
            {"text": "First claim."},
            {"text": "First claim."},
            {"text": "Second claim."},
        ]
        self.assertEqual(
            audit._lossless_summary_projection(claims),
            "First claim. First claim. Second claim.",
        )

    def test_current_v47_patch_reconciliation_accepts_consistent_artifact(self):
        root, db = self.make_run()
        self.add_current_v47_patch(db)
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertTrue(report["passed"], report["issues"])

    def test_current_v47_patch_reconciliation_rejects_operation_drift(self):
        root, db = self.make_run()
        self.add_current_v47_patch(db)
        with sqlite3.connect(db) as con:
            operation = json.loads(
                con.execute(
                    "SELECT operation_json FROM slow_graph_patch_operations"
                ).fetchone()[0]
            )
            operation["action"] = "revise"
            con.execute(
                "UPDATE slow_graph_patch_operations SET operation_json=?,action=?",
                (json.dumps(operation), "revise"),
            )
            con.commit()
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(any("differs from patch_json" in issue for issue in report["issues"]))

    def test_current_v47_patch_reconciliation_rejects_duplicate_and_orphan_rows(self):
        root, db = self.make_run()
        artifact = self.add_current_v47_patch(db)
        with sqlite3.connect(db) as con:
            operation_json = json.dumps(artifact["operation"])
            con.execute(
                "INSERT INTO slow_graph_patch_operations VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "operation.duplicate", artifact["patch_id"], 0,
                    artifact["capsule_id"], "create", None, 1, operation_json, 2,
                ),
            )
            con.execute(
                "INSERT INTO slow_graph_patch_operations VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "operation.orphan", "patch.missing", 0,
                    artifact["capsule_id"], "create", None, 1, operation_json, 3,
                ),
            )
            con.commit()
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(any("duplicate operation ordinals" in issue for issue in report["issues"]))
        self.assertTrue(any("orphan slow graph patch operation" in issue for issue in report["issues"]))

    def test_current_v47_patch_reconciliation_rejects_multiple_patches_and_result_drift(self):
        root, db = self.make_run()
        artifact = self.add_current_v47_patch(db)
        with sqlite3.connect(db) as con:
            con.execute(
                "INSERT INTO slow_graph_patches VALUES(?,?,?,?,?,?,?,?)",
                (
                    "patch.current.duplicate", artifact["job_id"], "tmcra_v4:q1",
                    "preferences", "deepseek-v4-flash", "{\"operations\": []}",
                    json.dumps({"prompt_version": audit.CURRENT_SLOW_PROMPT_VERSION}), 2,
                ),
            )
            metadata = json.loads(
                con.execute(
                    "SELECT metadata_json FROM records WHERE memory_id LIKE 'slow.%'"
                ).fetchone()[0]
            )
            metadata["capsule_id"] = "wrong.capsule"
            con.execute(
                "UPDATE records SET metadata_json=? WHERE memory_id LIKE 'slow.%'",
                (json.dumps(metadata),),
            )
            con.commit()
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(any("expected exactly one" in issue for issue in report["issues"]))
        self.assertTrue(any("resulting record metadata match" in issue for issue in report["issues"]))

    def test_active_slow_supporting_quarantined_fast_fails_semantic_audit(self):
        root, db = self.make_run()
        self.add_slow_revision(db)
        with sqlite3.connect(db) as con:
            con.execute(
                "UPDATE records SET state='quarantined' WHERE memory_id='leaf.0'"
            )
            con.execute(
                "DELETE FROM slot_heads WHERE memory_id='leaf.0'"
            )
            con.commit()
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertIn(
            "support_noncurrent_fast_leaf",
            {
                issue["code"]
                for issue in report["slow_promotion_coverage"][
                    "semantic_integrity_issues"
                ]
            },
        )

    def test_passes_using_actual_records_edges_and_slot_heads(self):
        root, db = self.make_run()
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertTrue(report["passed"], report["issues"])
        self.assertEqual(report["counts"]["source_records"], 1)
        self.assertFalse(report["slow_promotion_coverage"]["enforced"])
        self.assertFalse(report["slow_promotion_coverage"]["complete"])

        with sqlite3.connect(db) as con:
            con.execute("UPDATE records SET state='superseded' WHERE memory_id='leaf.0'")
            con.commit()
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(
            any("slot_heads targets non-active" in item for item in report["issues"])
        )

    def test_build_audit_requires_active_durable_future_memory_in_slow(self):
        root, db = self.make_run()
        with sqlite3.connect(db) as con:
            metadata = json.loads(
                con.execute(
                    "SELECT metadata_json FROM records WHERE memory_id='leaf.0'"
                ).fetchone()[0]
            )
            metadata["target_status"] = "future"
            con.execute(
                "UPDATE records SET metadata_json=? WHERE memory_id='leaf.0'",
                (json.dumps(metadata),),
            )
            con.commit()
        report = audit.audit_run(
            root,
            worker_db_specs=[f"worker={db}"],
            build_only=True,
        )
        self.assertFalse(report["passed"])
        self.assertIn(
            "leaf.0",
            [item[2] for item in report["slow_promotion_coverage"]["uncited_current_durable_ids"]],
        )

    def test_active_durable_slow_support_allows_noncurrent_target_time(self):
        for target_status in ("past", "planned", "future"):
            with self.subTest(target_status=target_status):
                root, db = self.make_run()
                self.add_slow_revision(db)
                with sqlite3.connect(db) as con:
                    metadata = json.loads(
                        con.execute(
                            "SELECT metadata_json FROM records WHERE memory_id='leaf.0'"
                        ).fetchone()[0]
                    )
                    metadata["target_status"] = target_status
                    con.execute(
                        "UPDATE records SET metadata_json=? WHERE memory_id='leaf.0'",
                        (json.dumps(metadata),),
                    )
                    con.commit()
                report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
                self.assertTrue(report["passed"], report["issues"])

    def test_subject_audit_history_requires_one_named_active_successor(self):
        root, db = self.make_run()
        self.add_subject_audit_history(db)
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertTrue(report["passed"], report["issues"])
        self.assertEqual(report["subject_attribution"]["superseded_count"], 1)
        self.assertEqual(report["subject_attribution"]["decision_count"], 1)

        root, db = self.make_run()
        self.add_subject_audit_history(db, missing_successor=True)
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(
            any("no active successor" in issue for issue in report["issues"])
        )

    def test_session_ordering_audit_rejects_interleaved_session(self):
        def window(session_index, parent_index, pre_rank, session_rank, count, rrf):
            return {
                "session_index": session_index,
                "parent_chunk_index": parent_index,
                "subchunk_index": 1,
                "retrieval_metadata": {
                    "pre_session_order_rank": pre_rank,
                    "session_order_rank": session_rank,
                    "session_selected_window_count": count,
                    "session_support_rrf": rrf,
                },
            }

        issues = []
        audit._audit_session_ordering(
            [
                window(1, 0, 1, 1, 2, 1.5),
                window(2, 0, 3, 2, 1, round(1 / 3, 8)),
                window(1, 1, 2, 1, 2, 1.5),
            ],
            0,
            issues,
        )
        self.assertTrue(any("interleaved" in issue for issue in issues))

    def test_multiline_raw_source_is_canonical_when_graph_value_is_whitespace_normalized(self):
        content = "First line.\n\nSecond line."
        root, db = self.make_run(
            content=content,
            stored_value="First line. Second line.",
        )
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertTrue(report["passed"], report["issues"])

    def test_empty_input_carrier_requires_exact_exclusion_artifact(self):
        root, db = self.make_run()
        input_path = root / "writer_input.json"
        rows = json.loads(input_path.read_text(encoding="utf-8"))
        rows[0]["messages"].append({"role": "assistant", "content": ""})
        input_path.write_text(json.dumps(rows), encoding="utf-8")
        exclusion = {
            "scope_id": "tmcra_v4:q1",
            "session_id": "session-0",
            "session_index": 0,
            "message_index": 1,
            "message_id": "s000_m001",
            "message_role": "assistant",
            "reason": "empty_content",
            "content_sha256": audit._sha(""),
        }
        (root / "source_exclusions.json").write_text(
            json.dumps(
                {
                    "schema_version": "tmcra.v4.source-exclusions.1",
                    "reason_policy": "exclude_only_whitespace_empty_message_carriers",
                    "count": 1,
                    "messages": [exclusion],
                }
            ),
            encoding="utf-8",
        )
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertTrue(report["passed"], report["issues"])
        self.assertEqual(report["counts"]["input_messages"], 2)
        self.assertEqual(report["counts"]["excluded_empty_input_messages"], 1)

        (root / "source_exclusions.json").unlink()
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(
            any(
                "source exclusion artifact lacks" in item
                for item in report["issues"]
            )
        )

    def test_shadow_tables_do_not_satisfy_persistence(self):
        root, db = self.make_run(shadow_only=True)
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(any("immutable source" in item or "source record count" in item for item in report["issues"]))

    def test_pending_actual_source_is_retained_but_missing_edge_fails(self):
        root, db = self.make_run(bad=True)
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(any("grounded_in" in item for item in report["issues"]))
        self.assertEqual(report["counts"]["source_records"], 1)

    def test_completed_writer_call_requires_hashed_raw_response(self):
        root, db = self.make_run()
        call = {
            "call_key": "flash:batch-1",
            "model": "deepseek-v4-flash",
            "stage": "batch_flash",
            "metadata": {"status": "completed", "physical_api_call": True},
        }
        (root / "product_writer_calls.jsonl").write_text(
            json.dumps(call) + "\n", encoding="utf-8"
        )
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(
            any("lacks raw response" in item for item in report["issues"])
        )

        raw_response = '{"schema_version":"tmcra.memory-write-batch.v4"}'
        raw_call = {
            "call_key": "flash:batch-1",
            "raw_response": raw_response,
            "raw_response_sha256": audit._sha(raw_response),
            "metadata_response_sha256": "",
        }
        (root / "product_writer_raw_responses.jsonl").write_text(
            json.dumps(raw_call) + "\n", encoding="utf-8"
        )
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertTrue(report["passed"], report["issues"])

    def test_validated_batch_recovery_requires_matching_artifact(self):
        root, db = self.make_run()
        response = '{"messages":[],"schema_version":"tmcra.memory-write-batch.v4"}'
        response_sha256 = audit._sha(response)
        with sqlite3.connect(db) as con:
            batch_id = con.execute(
                "SELECT batch_id FROM v4_batch_journal"
            ).fetchone()[0]
            con.execute(
                "UPDATE v4_batch_journal SET response_json=?,response_sha256=?,response_metadata_json=?",
                (
                    response,
                    response_sha256,
                    json.dumps({"validated_batch_commit_recovered": True}),
                ),
            )
            con.commit()

        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(
            any("validated batch recovery lacks" in item for item in report["issues"])
        )

        artifact = {
            "schema_version": "tmcra.v4.validated-batch-recovery.1",
            "batch_id": batch_id,
            "response_sha256": response_sha256,
            "prior_error_sha256": audit._sha("prior failure"),
            "physical_api_calls": 0,
        }
        (root / "product_writer_validated_batch_recoveries.jsonl").write_text(
            json.dumps(artifact) + "\n", encoding="utf-8"
        )
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertTrue(report["passed"], report["issues"])

    def test_historical_binding_recovery_matches_frozen_completed_job(self):
        root, db = self.make_run()
        with sqlite3.connect(db) as con:
            batch_id = con.execute(
                "SELECT batch_id FROM v4_batch_journal"
            ).fetchone()[0]
            con.execute("""
                CREATE TABLE v4_reconciliation_jobs(
                    job_id TEXT,scope_id TEXT,batch_id TEXT,message_id TEXT,
                    assertion_index INTEGER,request_json TEXT,response_json TEXT,
                    response_metadata_json TEXT,status TEXT,decision TEXT)
            """)
            request = {
                "candidate_cited_leaves": [{"memory_id": "leaf.0"}],
            }
            response = {
                "slot_decision": "bind_existing",
                "selected_memory_id": "leaf.0",
                "decision": "replace_current",
            }
            con.execute(
                "INSERT INTO v4_reconciliation_jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    "job.0",
                    "tmcra_v4:q1",
                    batch_id,
                    "s000_m000",
                    0,
                    json.dumps(request),
                    json.dumps(response),
                    "{}",
                    "completed",
                    "replace_current",
                ),
            )
            con.commit()
        artifact = {
            "schema_version": "tmcra.v4.historical-binding-recovery.1",
            "job_id": "job.0",
            "batch_id": batch_id,
            "selected_memory_id": "leaf.0",
            "decision": "replace_current",
            "superseded_reason": "v4_reconciliation_replace_current",
            "frozen_binding_identity_sha256": "same-hash",
            "historical_binding_identity_sha256": "same-hash",
            "physical_api_calls": 0,
        }
        path = root / "product_writer_historical_binding_recoveries.jsonl"
        path.write_text(json.dumps(artifact) + "\n", encoding="utf-8")
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertTrue(report["passed"], report["issues"])

        with sqlite3.connect(db) as con:
            con.execute("""
                INSERT INTO records
                SELECT scope_id,'leaf.1',category,slot_key,value,relation,
                       anchor_concepts_json,evidence_anchors_json,salience,confidence,
                       source_kind,turn_index,state,supersedes_json,metadata_json
                FROM records WHERE memory_id='leaf.0'
            """)
            con.execute("""
                INSERT INTO memory_edges
                SELECT scope_id,'edge.1','leaf.1',target_memory_id,edge_type,score,
                       model_score,evidence_turn,evidence,metadata_json
                FROM memory_edges WHERE edge_id='edge.0'
            """)
            response = {
                "slot_decision": "bind_existing",
                "selected_memory_id": "leaf.0",
                "decision": "keep_parallel",
            }
            con.execute(
                "UPDATE v4_reconciliation_jobs SET response_json=?,decision=? WHERE job_id='job.0'",
                (json.dumps(response), "keep_parallel"),
            )
            con.commit()
        artifact.update({
            "binding_mode": "unique_active_semantic_equivalent",
            "resolved_memory_id": "leaf.1",
            "decision": "keep_parallel",
            "frozen_semantic_identity_sha256": "same-semantic-hash",
            "resolved_semantic_identity_sha256": "same-semantic-hash",
        })
        path.write_text(json.dumps(artifact) + "\n", encoding="utf-8")
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertTrue(report["passed"], report["issues"])

        with sqlite3.connect(db) as con:
            metadata = json.loads(
                con.execute(
                    "SELECT metadata_json FROM records WHERE memory_id='leaf.1'"
                ).fetchone()[0]
            )
            metadata["superseded_reason"] = "v4_reconciliation_replace_current"
            metadata["superseded_by"] = "leaf.2"
            con.execute(
                "UPDATE records SET state='superseded',metadata_json=? WHERE memory_id='leaf.1'",
                (json.dumps(metadata),),
            )
            con.execute("""
                INSERT INTO records
                SELECT scope_id,'leaf.2',category,slot_key,value,relation,
                       anchor_concepts_json,evidence_anchors_json,salience,confidence,
                       source_kind,turn_index+1,'active',supersedes_json,metadata_json
                FROM records WHERE memory_id='leaf.0'
            """)
            con.execute("""
                INSERT INTO memory_edges
                SELECT scope_id,'edge.2','leaf.2',target_memory_id,edge_type,score,
                       model_score,evidence_turn,evidence,metadata_json
                FROM memory_edges WHERE edge_id='edge.0'
            """)
            con.execute(
                "UPDATE slot_heads SET memory_id='leaf.2' WHERE slot_key='preference.beverage'"
            )
            con.commit()
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertTrue(report["passed"], report["issues"])

        artifact["selected_memory_id"] = "different-leaf"
        path.write_text(json.dumps(artifact) + "\n", encoding="utf-8")
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(
            any("differs from frozen job" in item for item in report["issues"])
        )

    def test_keep_parallel_graph_overwrite_requires_audited_migration(self):
        root, db = self.make_run()
        worker = db.parent
        with sqlite3.connect(db) as con:
            scope = "tmcra_v4:q1"
            batch_id = con.execute(
                "SELECT batch_id FROM v4_batch_journal"
            ).fetchone()[0]
            old_metadata = json.loads(
                con.execute(
                    "SELECT metadata_json FROM records WHERE memory_id='leaf.0'"
                ).fetchone()[0]
            )
            old_metadata.update(
                {
                    "state_signature": "preference|beverage",
                    "reconciliation_decision": "insert",
                    "superseded_by": "leaf.1",
                    "superseded_reason": "same_state_revision",
                }
            )
            con.execute(
                "UPDATE records SET state='superseded',metadata_json=? WHERE memory_id='leaf.0'",
                (json.dumps(old_metadata),),
            )
            incoming_metadata = {
                **old_metadata,
                "message_id": "s000_m000",
                "llm_write_proposal_index": 0,
                "reconciliation_decision": "keep_parallel",
                "conflict_action": "supersede",
                "conflict_reason": "same_state_revision",
            }
            incoming_metadata.pop("superseded_by", None)
            incoming_metadata.pop("superseded_reason", None)
            con.execute("""
                INSERT INTO records
                SELECT scope_id,'leaf.1',category,slot_key,value,relation,
                       anchor_concepts_json,evidence_anchors_json,salience,confidence,
                       source_kind,turn_index+1,'parallel_active',?,?
                FROM records WHERE memory_id='leaf.0'
            """, (json.dumps(["leaf.0"]), json.dumps(incoming_metadata)))
            con.execute("""
                INSERT INTO memory_edges
                SELECT scope_id,'edge.1','leaf.1',target_memory_id,edge_type,score,
                       model_score,evidence_turn,evidence,metadata_json
                FROM memory_edges WHERE edge_id='edge.0'
            """)
            con.execute(
                "UPDATE slot_heads SET memory_id='leaf.1' WHERE slot_key='preference.beverage'"
            )
            con.execute("""
                CREATE TABLE v4_reconciliation_jobs(
                    job_id TEXT,scope_id TEXT,batch_id TEXT,message_id TEXT,
                    assertion_index INTEGER,request_json TEXT,response_json TEXT,
                    response_metadata_json TEXT,status TEXT,decision TEXT,
                    created_at TEXT)
            """)
            request = {
                "candidate_cited_leaves": [
                    {"memory_id": "leaf.0", "record_state": "active"}
                ]
            }
            response = {
                "slot_decision": "bind_existing",
                "selected_memory_id": "leaf.0",
                "decision": "keep_parallel",
            }
            con.execute(
                "INSERT INTO v4_reconciliation_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "job.keep_parallel",
                    scope,
                    batch_id,
                    "s000_m000",
                    0,
                    json.dumps(request),
                    json.dumps(response),
                    "{}",
                    "completed",
                    "keep_parallel",
                    "2026-07-12T00:00:00Z",
                ),
            )
            con.commit()

        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(
            any("keep_parallel decision was overwritten" in item for item in report["issues"])
        )

        keep_parallel_migration.migrate_worker(worker, apply=True)
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertTrue(report["passed"], report["issues"])

    def test_challenge_graph_overwrite_requires_audited_migration(self):
        root, db = self.make_run()
        worker = db.parent
        with sqlite3.connect(db) as con:
            scope = "tmcra_v4:q1"
            batch_id = con.execute(
                "SELECT batch_id FROM v4_batch_journal"
            ).fetchone()[0]
            old_metadata = json.loads(
                con.execute(
                    "SELECT metadata_json FROM records WHERE memory_id='leaf.0'"
                ).fetchone()[0]
            )
            old_metadata.update(
                {
                    "reconciliation_decision": "insert",
                    "superseded_by": "leaf.1",
                    "superseded_reason": "same_state_revision",
                }
            )
            con.execute(
                "UPDATE records SET state='superseded',metadata_json=? "
                "WHERE memory_id='leaf.0'",
                (json.dumps(old_metadata),),
            )
            incoming_metadata = {
                **old_metadata,
                "message_id": "s000_m000",
                "llm_write_proposal_index": 0,
                "reconciliation_decision": "challenge",
                "conflict_action": "supersede",
                "conflict_reason": "same_state_revision",
            }
            incoming_metadata.pop("superseded_by", None)
            incoming_metadata.pop("superseded_reason", None)
            con.execute(
                """
                INSERT INTO records
                SELECT scope_id,'leaf.1',category,slot_key,value,relation,
                       anchor_concepts_json,evidence_anchors_json,salience,confidence,
                       source_kind,turn_index+1,'challenged',?,?
                FROM records WHERE memory_id='leaf.0'
                """,
                (json.dumps(["leaf.0"]), json.dumps(incoming_metadata)),
            )
            con.execute(
                """
                INSERT INTO memory_edges
                SELECT scope_id,'edge.1','leaf.1',target_memory_id,edge_type,score,
                       model_score,evidence_turn,evidence,metadata_json
                FROM memory_edges WHERE edge_id='edge.0'
                """
            )
            con.execute(
                "UPDATE slot_heads SET memory_id='leaf.1' "
                "WHERE slot_key='preference.beverage'"
            )
            con.execute(
                """
                CREATE TABLE v4_reconciliation_jobs(
                    job_id TEXT,scope_id TEXT,batch_id TEXT,message_id TEXT,
                    assertion_index INTEGER,request_json TEXT,response_json TEXT,
                    response_metadata_json TEXT,status TEXT,decision TEXT,
                    created_at TEXT)
                """
            )
            request = {
                "canonical_slot_key": "preference.beverage",
                "candidate_cited_leaves": [
                    {"memory_id": "leaf.0", "record_state": "active"}
                ],
            }
            response = {
                "slot_decision": "bind_existing",
                "selected_memory_id": "leaf.0",
                "decision": "challenge",
            }
            con.execute(
                "INSERT INTO v4_reconciliation_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "job.challenge",
                    scope,
                    batch_id,
                    "s000_m000",
                    0,
                    json.dumps(request),
                    json.dumps(response),
                    "{}",
                    "completed",
                    "challenge",
                    "2026-07-12T00:00:00Z",
                ),
            )
            con.commit()

        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(
            any(
                "challenge decision was overwritten" in item
                for item in report["issues"]
            )
        )

        challenge_migration.migrate_worker(worker, apply=True)
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertTrue(report["passed"], report["issues"])

    def test_retrieval_requires_every_available_path(self):
        root, db = self.make_run(retrieval=True)
        report = audit.audit_run(root, worker_db_specs=[f"worker={db}"])
        self.assertFalse(report["passed"])
        self.assertTrue(any("retrieval row" in item for item in report["issues"]))
        self.assertTrue(
            any("attachments must be an object list" in item for item in report["issues"])
        )

    def test_production_retrieval_contract_rejects_missing_slow_contribution(self):
        contribution = {"layer": "source"}
        windows = [
            {"retrieval_metadata": {"layer_contributions": [contribution]}}
        ]
        paths = {"source": True, "fast": True, "slow": True}
        evidence = {
            "retrieval_contract": {
                "schema_version": audit.RETRIEVAL_CONTRACT_SCHEMA_VERSION,
                "execution_lane": "production",
                "composition_mode": "layered",
                "required_selected_layers": ["source", "slow"],
                "selected_layer_window_counts": {
                    "source": 1,
                    "fast": 0,
                    "slow": 0,
                },
                "candidate_paths_executed": paths,
            }
        }
        issues = []
        audit._audit_retrieval_contract(
            evidence,
            {"candidate_paths_executed": paths},
            windows,
            0,
            issues,
        )
        self.assertTrue(any("production layers are missing" in item for item in issues))

    def test_build_only_audit_does_not_select_historical_retrieval(self):
        root, db = self.make_run(retrieval=True)
        report = audit.audit_run(
            root,
            worker_db_specs=[f"worker={db}"],
            build_only=True,
        )
        self.assertFalse(report["passed"])
        self.assertTrue(report["slow_promotion_coverage"]["enforced"])
        self.assertTrue(
            any("missing from active Slow claims" in item for item in report["issues"])
        )
        self.assertEqual(
            report["retrieval"],
            {
                "present": True,
                "passed": None,
                "skipped": True,
                "reason": "build_only_audit",
            },
        )

    def test_input_rejects_evaluation_fields(self):
        root, _ = self.make_run()
        (root / "writer_input.json").write_text(json.dumps([{
            "question_id": "q1", "messages": [], "gold_answer": "bad",
        }]), encoding="utf-8")
        report = audit.audit_run(root)
        self.assertFalse(report["passed"])
        self.assertIn("non-history", report["issues"][0])

    def test_discovers_worker_databases(self):
        root, db = self.make_run()
        discovered = audit.discover_worker_databases(root)
        self.assertEqual(discovered["worker_000"], [db.resolve()])

    def test_frozen_manifest_excludes_backup_databases(self):
        root, db = self.make_run()
        backup = root / "pre_repair_snapshot" / "worker_000" / "native_memory.sqlite3"
        backup.parent.mkdir(parents=True)
        shutil.copy2(db, backup)
        (root / "scope_manifest.jsonl").write_text(
            json.dumps(
                {
                    "question_id": "q1",
                    "scope_id": "scope-1",
                    "db_path": str(db),
                    "index_path": str(root / "indexes" / "q1.pt"),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        discovered = audit.discover_worker_databases(root)
        self.assertEqual(discovered, {"worker_000": [db.resolve()]})

    def test_frozen_manifest_rejects_duplicate_worker(self):
        root, db = self.make_run()
        second = db.with_name("memory.sqlite3")
        shutil.copy2(db, second)
        rows = [
            {"db_path": str(db)},
            {"db_path": str(second)},
        ]
        (root / "scope_manifest.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(audit.AuditError, "repeats worker"):
            audit.discover_worker_databases(root)


if __name__ == "__main__":
    unittest.main()
