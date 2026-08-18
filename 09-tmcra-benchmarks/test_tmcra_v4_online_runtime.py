import json
import hashlib
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tmcra_v4_online_runtime as runtime


class LogicalPathIdentityTests(unittest.TestCase):
    def _symlinked_directory(self, directory):
        target = Path(directory) / "data-volume"
        logical = Path(directory) / "logical-run"
        target.mkdir()
        try:
            logical.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks are unavailable: {exc}")
        return target, logical

    def test_row_identity_does_not_follow_storage_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target, logical = self._symlinked_directory(directory)
            row = {
                "db_path": str(logical / "memory.sqlite3"),
                "index_path": str(logical / "index.pt"),
            }

            identity = runtime._row_identity(row)

            self.assertEqual(identity["db_path"], str((logical / "memory.sqlite3").absolute()))
            self.assertNotEqual(identity["db_path"], str((target / "memory.sqlite3").absolute()))

    def test_operation_id_does_not_follow_storage_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target, logical = self._symlinked_directory(directory)
            try:
                v3 = runtime._v3()
            except RuntimeError as exc:
                self.skipTest(str(exc))

            operation_id = v3.layered_retrieval_operation_id(
                logical / "retrieval", "scope", "question"
            )
            expected = hashlib.sha256(
                "\n".join(
                    (
                        "tmcra.v3.layered-retrieval",
                        str((logical / "retrieval").absolute()),
                        "scope",
                        "question",
                    )
                ).encode("utf-8")
            ).hexdigest()
            physical = v3.layered_retrieval_operation_id(
                target / "retrieval", "scope", "question"
            )

            self.assertEqual(operation_id, expected)
            self.assertNotEqual(operation_id, physical)


class GraphAdapterCacheTests(unittest.TestCase):
    def test_cache_reuses_the_current_scope(self):
        adapter = object()
        harness = mock.Mock()
        cache = {("scope-1", "/tmp/one.sqlite3"): adapter}

        result, reused = runtime._get_graph_adapter(
            harness=harness,
            scope_id="scope-1",
            db_path=Path("/tmp/one.sqlite3"),
            cache=cache,
        )

        self.assertIs(result, adapter)
        self.assertTrue(reused)
        harness.build_adapter.assert_not_called()
        self.assertEqual(len(cache), 1)

    def test_cache_evicts_the_previous_scope_before_building(self):
        old_adapter = object()
        new_adapter = object()
        harness = mock.Mock()
        harness.build_adapter.return_value = new_adapter
        cache = {("scope-1", "/tmp/one.sqlite3"): old_adapter}
        fake_torch = SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=mock.Mock(return_value=True),
                empty_cache=mock.Mock(),
            )
        )

        with mock.patch.object(runtime, "_v3", return_value=SimpleNamespace(torch=fake_torch)):
            result, reused = runtime._get_graph_adapter(
                harness=harness,
                scope_id="scope-2",
                db_path=Path("/tmp/two.sqlite3"),
                cache=cache,
            )

        self.assertIs(result, new_adapter)
        self.assertFalse(reused)
        harness.build_adapter.assert_called_once_with(
            "scope-2", Path("/tmp/two.sqlite3")
        )
        self.assertEqual(cache, {("scope-2", "/tmp/two.sqlite3"): new_adapter})
        fake_torch.cuda.empty_cache.assert_called_once_with()


class SourceParentTemporalMetadataTests(unittest.TestCase):
    def test_collapse_preserves_timestamp_while_restoring_exact_source_text(self):
        prefix = (
            "TMCRA conversation_id=session-1 "
            "timestamp=2022-03-21T15:54:02+00:00 message=003 role=user"
        )
        source_text = "I attended the baking class yesterday."
        candidate = {
            "candidate_id": "chunk::scope:s001_m003_p01",
            "text": f"{prefix}\n{source_text}",
            "session_id": "session-1",
            "session_index": 1,
            "parent_chunk_index": 3,
            "subchunk_index": 1,
            "source_char_start": 0,
            "source_char_end": len(source_text),
            "source_record_id": "source.s001.m003:4",
            "historical_date": "2022/03/21 (Mon) 15:54",
            "timestamp": "2022-03-21T15:54:02+00:00",
            "message_role": "user",
            "semantic_logit": 1.0,
        }

        collapsed = runtime._collapse_source_parents([candidate], [candidate])

        self.assertEqual(collapsed[0]["text"], source_text)
        self.assertEqual(
            collapsed[0]["timestamp"], "2022-03-21T15:54:02+00:00"
        )
        self.assertEqual(
            collapsed[0]["historical_date"], "2022/03/21 (Mon) 15:54"
        )
        self.assertEqual(collapsed[0]["message_role"], "user")

    def test_collapse_rejects_mixed_timestamps_inside_one_source_parent(self):
        base = {
            "candidate_id": "chunk-1",
            "text": "prefix\nfirst half",
            "session_id": "session-1",
            "session_index": 1,
            "parent_chunk_index": 3,
            "subchunk_index": 1,
            "source_char_start": 0,
            "source_char_end": 10,
            "historical_date": "2022/03/21",
            "timestamp": "2022-03-21T15:54:02+00:00",
            "message_role": "user",
        }
        other = {
            **base,
            "candidate_id": "chunk-2",
            "subchunk_index": 2,
            "source_char_start": 5,
            "source_char_end": 16,
            "text": "prefix\nhalf source",
            "timestamp": "2022-03-22T15:54:02+00:00",
        }

        with self.assertRaisesRegex(RuntimeError, "temporal metadata is inconsistent"):
            runtime._collapse_source_parents([base], [base, other])

    def test_collapse_deduplicates_repeated_source_session_copy(self):
        first = {
            "candidate_id": "chunk-1",
            "text": "prefix\nrepeated source",
            "session_id": "session-1",
            "session_index": 2,
            "parent_chunk_index": 3,
            "subchunk_index": 0,
            "source_char_start": 0,
            "source_char_end": 15,
            "historical_date": "2022-03-21",
            "timestamp": "2022-03-21T15:54:02+00:00",
            "message_role": "user",
        }
        repeated = {
            **first,
            "candidate_id": "chunk-2",
            "session_index": 9,
            "historical_date": "2022-04-01",
            "timestamp": "2022-04-01T15:54:02+00:00",
        }

        collapsed = runtime._collapse_source_parents(
            [first, repeated],
            [first, repeated],
        )

        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0]["candidate_id"], "parent::session-1:3")

    def test_collapse_rejects_source_parent_id_collision_with_new_content(self):
        first = {
            "candidate_id": "chunk-1",
            "text": "prefix\nfirst source",
            "session_id": "session-1",
            "session_index": 2,
            "parent_chunk_index": 3,
            "subchunk_index": 0,
            "source_char_start": 0,
            "source_char_end": 12,
            "historical_date": "2022-03-21",
            "timestamp": "2022-03-21T15:54:02+00:00",
            "message_role": "user",
        }
        collision = {
            **first,
            "candidate_id": "chunk-2",
            "text": "prefix\nother source",
            "session_index": 9,
            "source_char_end": 12,
            "historical_date": "2022-04-01",
            "timestamp": "2022-04-01T15:54:02+00:00",
        }

        with self.assertRaisesRegex(RuntimeError, "different content"):
            runtime._collapse_source_parents(
                [first, collision],
                [first, collision],
            )


class SourceSessionDiversityPackingTests(unittest.TestCase):
    @staticmethod
    def _candidate(session_index, rank):
        return {
            "candidate_id": f"candidate-{rank}",
            "session_id": f"session-{session_index}",
            "session_index": session_index,
            "parent_chunk_index": 0,
            "subchunk_index": 0,
            "source_char_start": 0,
            "source_char_end": 8,
            "text": f"source {rank}",
        }

    def test_temporal_event_plan_reserves_three_source_sessions(self):
        plan = {"query_kind": "event", "temporal_focus": "recent"}
        self.assertEqual(runtime.required_source_session_count(plan), 3)

    def test_packer_selects_distinct_source_sessions_within_fixed_budget(self):
        candidates = [
            self._candidate(1, 1),
            self._candidate(1, 2),
            self._candidate(2, 3),
            self._candidate(3, 4),
        ]
        units = [
            {
                "unit_type": "source_window",
                "layer": "source",
                "priority_score": 1.0 / rank,
                "canonical_slot": f"source-{rank}",
                "source_candidate": candidate,
            }
            for rank, candidate in enumerate(candidates, start=1)
        ]

        packed, stats = runtime.pack_recall_role_units(
            units,
            candidates,
            top_k=3,
            qid="q1",
            return_stats=True,
            required_layers=["source"],
            required_source_session_count=3,
        )

        selected_sessions = {
            window["session_id"] for _, windows in packed for window in windows
        }
        self.assertEqual(selected_sessions, {"session-1", "session-2", "session-3"})
        self.assertEqual(stats["source_session_diversity_selected"], 3)


class SourceTemporalIndexContractTests(unittest.TestCase):
    def test_current_source_candidate_requires_temporal_metadata(self):
        candidate = {
            "session_id": "s1",
            "source_record_id": "source.1",
            "historical_date": "2023-04-03",
            "timestamp": "2023-04-03T09:00:00+00:00",
            "message_role": "user",
        }
        runtime._validate_source_candidate_temporal_metadata([candidate])
        for field in ("historical_date", "timestamp", "message_role"):
            with self.subTest(field=field):
                invalid = {**candidate, field: ""}
                with self.assertRaisesRegex(
                    RuntimeError, "lacks production temporal metadata"
                ):
                    runtime._validate_source_candidate_temporal_metadata([invalid])

    def test_online_index_schema_is_bumped_for_temporal_candidates(self):
        self.assertEqual(
            runtime.ONLINE_INDEX_SCHEMA_VERSION, "tmcra.v4.online-index.3"
        )


class SlowSourceParentRuntimeContractTests(unittest.TestCase):
    def test_v3_normalizer_preserves_complete_slow_source_parent(self):
        parent = {
            "session_index": 2,
            "parent_chunk_index": 7,
            "message_index": 7,
            "source_record_id": "source.s002.m007:29",
            "event_id": "event::scope:s002_m007",
            "evidence_char_start": 3,
            "evidence_char_end": 12,
        }

        normalized = runtime._v3()._normalize_source_parents(
            [parent], valid_locations={(2, 7)}, label="slow claim"
        )

        self.assertEqual(normalized, [parent])

    def test_v3_normalizer_rejects_disagreeing_message_coordinates(self):
        parent = {
            "session_index": 2,
            "parent_chunk_index": 7,
            "message_index": 8,
            "source_record_id": "source.s002.m007:29",
            "event_id": "event::scope:s002_m007",
            "evidence_char_start": 3,
            "evidence_char_end": 12,
        }

        with self.assertRaisesRegex(
            RuntimeError, "message_index differs from parent_chunk_index"
        ):
            runtime._v3()._normalize_source_parents(
                [parent], valid_locations={(2, 7)}, label="slow claim"
            )


class FastSemanticRuntimeHydrationTests(unittest.TestCase):
    scope_id = "scope-1"
    source_id = "source.1"
    semantic_id = "fast.1"
    source_text = "The user lives in Paris."
    evidence_quote = "lives in Paris"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()

    def make_db(self, *, state="active", evidence_start=None, evidence_end=None):
        start = self.source_text.index(self.evidence_quote)
        end = start + len(self.evidence_quote)
        if evidence_start is not None:
            start = evidence_start
        if evidence_end is not None:
            end = evidence_end
        source_metadata = {
            "content_variant": "source_message",
            "node_kind": "immutable_source_message",
            "immutable_evidence_leaf": True,
            "raw_content": self.source_text,
            "source_span": self.source_text,
            "source_turn_text": self.source_text,
            "source_record_id": self.source_id,
            "session_id": "session-2",
            "session_index": 2,
            "message_id": "s2_m7",
            "message_index": 7,
            "event_id": "event::scope-1:s2_m7",
        }
        semantic_metadata = {
            "memory_layer": "fast",
            "content_variant": "product_semantic_memory",
            "node_kind": "atomic_user_assertion",
            "atomic_evidence_leaf": True,
            "authority": "user_assertion",
            "canonical_slot_key": "memory.user.residence.city",
            "memory_type": "state",
            "durability": "durable",
            "target_status": "current",
            "source_record_id": self.source_id,
            "session_id": "session-2",
            "session_index": 2,
            "message_id": "s2_m7",
            "message_index": 7,
            "parent_chunk_index": 7,
            "event_id": "event::scope-1:s2_m7",
            "evidence_char_start": start,
            "evidence_char_end": end,
            "raw_content": self.evidence_quote,
            "source_span": self.evidence_quote,
            "source_turn_text": self.source_text,
        }
        db = Path(self.tempdir.name) / "memory.sqlite3"
        db.unlink(missing_ok=True)
        connection = sqlite3.connect(db)
        try:
            connection.execute(
                "CREATE TABLE records(scope_id TEXT,memory_id TEXT,value TEXT,state TEXT,metadata_json TEXT)"
            )
            connection.executemany(
                "INSERT INTO records VALUES(?,?,?,?,?)",
                [
                    (
                        self.scope_id,
                        self.source_id,
                        self.source_text,
                        "evidence",
                        json.dumps(source_metadata),
                    ),
                    (
                        self.scope_id,
                        self.semantic_id,
                        "The user lives in Paris.",
                        state,
                        json.dumps(semantic_metadata),
                    ),
                ],
            )
            connection.commit()
        finally:
            connection.close()
        return db

    def tearDown(self):
        self.tempdir.cleanup()

    def test_hydration_rejects_tampered_index_identity(self):
        db = self.make_db()
        valid_source_parent = {
            "session_index": 2,
            "parent_chunk_index": 7,
            "source_record_id": self.source_id,
            "evidence_char_start": self.source_text.index(self.evidence_quote),
            "evidence_char_end": self.source_text.index(self.evidence_quote) + len(self.evidence_quote),
        }
        valid_provenance = {
            "memory_layer": "fast",
            "content_variant": "product_semantic_memory",
            "source_record_id": self.source_id,
            "semantic_memory_id": self.semantic_id,
        }
        for field, value in (
            ("canonical_slot", "memory.user.tampered"),
            ("source_parent", {**valid_source_parent, "source_record_id": "source.other"}),
            ("provenance", {**valid_provenance, "source_record_id": "source.other"}),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(RuntimeError, "identity differs"):
                    runtime._hydrate_fast_semantic_records(
                        db,
                        self.scope_id,
                        [{"memory_id": self.semantic_id, field: value}],
                    )

    def test_hydration_rebuilds_output_from_db_values(self):
        db = self.make_db()
        evidence_start = self.source_text.index(self.evidence_quote)
        hydrated = runtime._hydrate_fast_semantic_records(
            db,
            self.scope_id,
            [{"memory_id": self.semantic_id, "text": "tampered", "untrusted": True}],
        )
        self.assertEqual(
            hydrated,
            [
                {
                    "memory_id": self.semantic_id,
                    "text": "The user lives in Paris.",
                    "record_state": "active",
                    "canonical_slot": "memory.user.residence.city",
                    "source_parent": {
                        "session_index": 2,
                        "parent_chunk_index": 7,
                        "source_record_id": self.source_id,
                        "evidence_char_start": evidence_start,
                        "evidence_char_end": evidence_start + len(self.evidence_quote),
                    },
                    "provenance": {
                        "memory_layer": "fast",
                        "content_variant": "product_semantic_memory",
                        "source_record_id": self.source_id,
                        "semantic_memory_id": self.semantic_id,
                    },
                    "memory_type": "state",
                    "durability": "durable",
                    "temporal_status": "current",
                }
            ],
        )

    def test_hydration_rejects_evidence_span_that_does_not_match_source(self):
        db = self.make_db(evidence_start=0, evidence_end=len(self.evidence_quote))
        with self.assertRaisesRegex(RuntimeError, "does not match immutable source quote"):
            runtime._hydrate_fast_semantic_records(
                db, self.scope_id, [{"memory_id": self.semantic_id}]
            )

    def test_hydration_rejects_inactive_fast_record(self):
        db = self.make_db(state="superseded")
        with self.assertRaisesRegex(RuntimeError, "malformed or inactive"):
            runtime._hydrate_fast_semantic_records(
                db, self.scope_id, [{"memory_id": self.semantic_id}]
            )

    def test_hydration_accepts_other_current_fast_states(self):
        for state in ("parallel_active", "promoted", "challenged"):
            with self.subTest(state=state):
                db = self.make_db(state=state)
                hydrated = runtime._hydrate_fast_semantic_records(
                    db, self.scope_id, [{"memory_id": self.semantic_id}]
                )
                self.assertEqual(hydrated[0]["record_state"], state)

    def test_direct_runtime_rejects_invalid_production_budget_before_work(self):
        args = Namespace(
            composition_mode="layered",
            execution_lane="production",
            packing_budget_mode="adaptive",
            top_k=8,
        )
        planner = mock.Mock()
        with mock.patch.object(runtime, "_v3") as v3:
            with self.assertRaisesRegex(RuntimeError, "fixed Top8"):
                runtime.retrieve_one(
                    {"question_id": "q1", "question": "What changed?"},
                    args=args,
                    harness=None,
                    models=None,
                    planner=planner,
                )
        v3.assert_not_called()
        planner.plan.assert_not_called()


class SlowSummaryInventoryTests(unittest.TestCase):
    @staticmethod
    def claim_candidate(candidate_id, capsule_id, summary_id, text, *, parent_index):
        return {
            "inventory_schema_version": runtime.SLOW_INVENTORY_SCHEMA_VERSION,
            "candidate_kind": "capsule_claim",
            "candidate_id": candidate_id,
            "memory_id": f"slow.{capsule_id}.r1",
            "capsule_id": capsule_id,
            "revision": 1,
            "status": "active",
            "canonical_slot": f"slot.{candidate_id}",
            "capsule_summary_candidate_id": summary_id,
            "capsule_summary_text": "User lives in Paris and commutes by train.",
            "text": text,
            "claims": [
                {
                    "claim_id": f"claim.{candidate_id}",
                    "canonical_slot": f"slot.{candidate_id}",
                    "text": text,
                    "support": [f"fast.{candidate_id}"],
                    "counterevidence": [],
                }
            ],
            "source_parents": [
                {
                    "session_index": 1,
                    "parent_chunk_index": parent_index,
                    "source_record_id": f"source.{parent_index}",
                    "evidence_char_start": 0,
                    "evidence_char_end": 10,
                }
            ],
            "provenance": {
                "memory_layer": "slow",
                "claim_id": f"claim.{candidate_id}",
            },
        }

    @classmethod
    def summary_candidate(cls, summary_id, capsule_id, children, claims):
        return {
            "inventory_schema_version": runtime.SLOW_INVENTORY_SCHEMA_VERSION,
            "candidate_kind": "capsule_summary",
            "candidate_id": summary_id,
            "memory_id": f"slow.{capsule_id}.r1",
            "capsule_id": capsule_id,
            "revision": 1,
            "status": "active",
            "canonical_slot": "slow.summary",
            "text": "User lives in Paris and commutes by train.",
            "claims": [dict(item["claims"][0]) for item in claims],
            "source_parents": [
                dict(parent)
                for item in claims
                for parent in item["source_parents"]
            ],
            "child_claim_candidate_ids": children,
            "provenance": {"memory_layer": "slow"},
        }

    def test_inventory_builds_one_summary_entry_and_atomic_claim_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "memory.sqlite3"
            con = sqlite3.connect(db)
            try:
                con.execute(
                    "CREATE TABLE records(scope_id TEXT,memory_id TEXT,value TEXT)"
                )
                con.execute(
                    "INSERT INTO records VALUES(?,?,?)",
                    ("scope", "slow.cap.r1", "User lives in Paris and commutes by train."),
                )
                con.commit()
            finally:
                con.close()
            raw_claims = [
                {
                    "candidate_id": "capsule::cap:r1:c0",
                    "memory_id": "slow.cap.r1",
                    "capsule_id": "cap",
                    "revision": 1,
                    "status": "active",
                    "canonical_slot": "residence.city",
                    "claims": [
                        {
                            "claim_id": "claim.0",
                            "canonical_slot": "residence.city",
                            "text": "User lives in Paris.",
                            "support": ["fast.0"],
                            "counterevidence": [],
                        }
                    ],
                    "source_parents": [
                        {
                            "session_index": 1,
                            "parent_chunk_index": 2,
                            "source_record_id": "source.2",
                            "evidence_char_start": 0,
                            "evidence_char_end": 10,
                        }
                    ],
                    "text": "User lives in Paris.",
                    "provenance": {"memory_layer": "slow", "claim_id": "claim.0"},
                }
            ]
            fake_v3 = SimpleNamespace(
                load_layered_inventory=lambda *args: (raw_claims, [{"memory_id": "fast.0"}])
            )
            with mock.patch.object(runtime, "_v3", return_value=fake_v3):
                inventory, semantic = runtime.load_v4_layered_inventory(
                    db, "scope", []
                )
        self.assertEqual([item["candidate_kind"] for item in inventory], ["capsule_summary", "capsule_claim"])
        self.assertEqual(
            inventory[0]["child_claim_candidate_ids"],
            ["capsule::cap:r1:c0"],
        )
        self.assertEqual(
            inventory[1]["capsule_summary_text"],
            "User lives in Paris and commutes by train.",
        )
        self.assertEqual(semantic, [{"memory_id": "fast.0"}])
        self.assertEqual(
            runtime._validated_slow_inventory_counts(inventory),
            {
                "slow_candidate_count": 2,
                "slow_capsule_head_count": 1,
                "slow_summary_candidate_count": 1,
                "slow_claim_candidate_count": 1,
            },
        )

    def test_inventory_rejects_operational_stored_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "memory.sqlite3"
            con = sqlite3.connect(db)
            try:
                con.execute(
                    "CREATE TABLE records(scope_id TEXT,memory_id TEXT,value TEXT)"
                )
                con.execute(
                    "INSERT INTO records VALUES(?,?,?)",
                    ("scope", "slow.cap.r1", "Create a memory capsule from evidence."),
                )
                con.commit()
            finally:
                con.close()
            raw = {
                "candidate_id": "capsule::cap:r1:c0",
                "memory_id": "slow.cap.r1",
                "capsule_id": "cap",
                "revision": 1,
                "status": "active",
                "canonical_slot": "residence.city",
                "claims": [
                    {
                        "claim_id": "claim.0",
                        "canonical_slot": "residence.city",
                        "text": "User lives in Paris.",
                        "support": ["fast.0"],
                        "counterevidence": [],
                    }
                ],
                "source_parents": [
                    {
                        "session_index": 1,
                        "parent_chunk_index": 2,
                        "source_record_id": "source.2",
                        "evidence_char_start": 0,
                        "evidence_char_end": 10,
                    }
                ],
                "text": "User lives in Paris.",
                "provenance": {"memory_layer": "slow", "claim_id": "claim.0"},
            }
            fake_v3 = SimpleNamespace(
                load_layered_inventory=lambda *args: ([raw], [])
            )
            with mock.patch.object(runtime, "_v3", return_value=fake_v3):
                with self.assertRaisesRegex(RuntimeError, "summary violates"):
                    runtime.load_v4_layered_inventory(db, "scope", [])

    def test_current_v47_inventory_allows_multiple_heads_and_requires_exact_summary(self):
        def claim(candidate_id, capsule_id, slot, text, evidence):
            return {
                "inventory_schema_version": runtime.SLOW_INVENTORY_SCHEMA_VERSION,
                "candidate_kind": "capsule_claim",
                "candidate_id": candidate_id,
                "memory_id": f"slow.{capsule_id}.r1",
                "capsule_id": capsule_id,
                "revision": 1,
                "status": "active",
                "canonical_slot": slot,
                "region_key": "preferences",
                "summary_contract_version": runtime.SLOW_SUMMARY_CONTRACT_VERSION,
                "partition_contract_version": runtime.CURRENT_SLOW_PARTITION_CONTRACT_VERSION,
                "capsule_summary_candidate_id": f"capsule-summary::{capsule_id}:r1",
                "capsule_summary_text": text,
                "text": text,
                "claims": [{
                    "claim_id": f"claim.{candidate_id}",
                    "canonical_slot": slot,
                    "text": text,
                    "support": [evidence],
                    "counterevidence": [],
                }],
                "source_parents": [{"session_index": 0, "parent_chunk_index": 0}],
            }

        claims_a = [
            claim("a0", "cap.a", "preference.coffee", "Prefers coffee.", "fast.a"),
            claim("a1", "cap.a", "preference.coffee", "Prefers coffee.", "fast.b"),
        ]
        claims_b = [
            claim("b0", "cap.b", "preference.coffee", "Drinks tea.", "fast.c"),
        ]

        def summary(capsule_id, claims):
            summary_id = f"capsule-summary::{capsule_id}:r1"
            result = {
                "inventory_schema_version": runtime.SLOW_INVENTORY_SCHEMA_VERSION,
                "candidate_kind": "capsule_summary",
                "candidate_id": summary_id,
                "memory_id": f"slow.{capsule_id}.r1",
                "capsule_id": capsule_id,
                "revision": 1,
                "status": "active",
                "region_key": "preferences",
                "summary_contract_version": runtime.SLOW_SUMMARY_CONTRACT_VERSION,
                "partition_contract_version": runtime.CURRENT_SLOW_PARTITION_CONTRACT_VERSION,
                "canonical_slot": "slow.summary",
                "claims": [dict(item["claims"][0]) for item in claims],
                "text": runtime._lossless_summary_projection(
                    [dict(item["claims"][0]) for item in claims]
                ),
                "child_claim_candidate_ids": [item["candidate_id"] for item in claims],
            }
            for item in claims:
                item["capsule_summary_text"] = result["text"]
            return result

        inventory = [summary("cap.a", claims_a), summary("cap.b", claims_b), *claims_a, *claims_b]
        counts = runtime._validated_slow_inventory_counts(inventory)
        self.assertEqual(counts["slow_capsule_head_count"], 2)
        self.assertEqual(counts["slow_claim_candidate_count"], 3)

    def test_current_v47_inventory_rejects_summary_projection_and_cross_head_collisions(self):
        claim = {
            "inventory_schema_version": runtime.SLOW_INVENTORY_SCHEMA_VERSION,
            "candidate_kind": "capsule_claim",
            "candidate_id": "claim.a",
            "memory_id": "slow.cap.a.r1",
            "capsule_id": "cap.a",
            "revision": 1,
            "status": "active",
            "region_key": "preferences",
            "summary_contract_version": runtime.SLOW_SUMMARY_CONTRACT_VERSION,
            "partition_contract_version": runtime.CURRENT_SLOW_PARTITION_CONTRACT_VERSION,
            "capsule_summary_candidate_id": "summary.a",
            "capsule_summary_text": "Prefers coffee.",
            "text": "Prefers coffee.",
            "claims": [{
                "claim_id": "claim.a",
                "canonical_slot": "preference.beverage",
                "text": "Prefers coffee.",
                "support": ["fast.same"],
                "counterevidence": [],
            }],
            "source_parents": [{"session_index": 0, "parent_chunk_index": 0}],
        }
        summary = {
            "inventory_schema_version": runtime.SLOW_INVENTORY_SCHEMA_VERSION,
            "candidate_kind": "capsule_summary",
            "candidate_id": "summary.a",
            "memory_id": "slow.cap.a.r1",
            "capsule_id": "cap.a",
            "revision": 1,
            "status": "active",
            "region_key": "preferences",
            "summary_contract_version": runtime.SLOW_SUMMARY_CONTRACT_VERSION,
            "partition_contract_version": runtime.CURRENT_SLOW_PARTITION_CONTRACT_VERSION,
            "claims": [dict(claim["claims"][0])],
            "text": "wrong summary",
            "child_claim_candidate_ids": ["claim.a"],
        }
        with self.assertRaisesRegex(RuntimeError, "exact lossless"):
            runtime._validated_slow_inventory_counts([summary, claim])

        summary["text"] = "Prefers coffee."
        claim_b = json.loads(json.dumps(claim))
        claim_b.update({
            "candidate_id": "claim.b",
            "memory_id": "slow.cap.b.r1",
            "capsule_id": "cap.b",
            "capsule_summary_candidate_id": "summary.b",
        })
        summary_b = json.loads(json.dumps(summary))
        summary_b.update({
            "candidate_id": "summary.b",
            "memory_id": "slow.cap.b.r1",
            "capsule_id": "cap.b",
            "child_claim_candidate_ids": ["claim.b"],
        })
        with self.assertRaisesRegex(RuntimeError, "duplicate evidence citation"):
            runtime._validated_slow_inventory_counts([summary, summary_b, claim, claim_b])

        claim_b["claims"][0]["text"] = "Prefers espresso."
        claim_b["text"] = "Prefers espresso."
        claim_b["capsule_summary_text"] = "Prefers espresso."
        summary_b["claims"][0]["text"] = "Prefers espresso."
        summary_b["text"] = "Prefers espresso."
        counts = runtime._validated_slow_inventory_counts(
            [summary, summary_b, claim, claim_b]
        )
        self.assertEqual(counts["slow_capsule_head_count"], 2)
        self.assertEqual(counts["slow_claim_candidate_count"], 2)

        claim_b["claims"][0]["text"] = "Prefers coffee."
        claim_b["text"] = "Prefers coffee."
        claim_b["capsule_summary_text"] = "Prefers coffee."
        summary_b["claims"][0]["text"] = "Prefers coffee."
        summary_b["text"] = "Prefers coffee."
        summary_b["claims"][0]["support"] = ["fast.other"]
        claim_b["claims"][0]["support"] = ["fast.other"]
        with self.assertRaisesRegex(RuntimeError, "semantic claim appears"):
            runtime._validated_slow_inventory_counts([summary, summary_b, claim, claim_b])

    def test_summary_hit_expands_claims_but_only_claims_reach_reranking(self):
        c1 = self.claim_candidate("c1", "cap1", "s1", "Paris fact", parent_index=1)
        c2 = self.claim_candidate("c2", "cap1", "s1", "Train fact", parent_index=2)
        c3 = self.claim_candidate("c3", "cap2", "s2", "Tokyo fact", parent_index=3)
        s1 = self.summary_candidate("s1", "cap1", ["c1", "c2"], [c1, c2])
        s2 = self.summary_candidate("s2", "cap2", ["c3"], [c3])
        inventory = [s1, s2, c1, c2, c3]
        class Matrix:
            values = [10.0, 0.0, 0.1, 0.2, 9.0]

            def __len__(self):
                return len(self.values)

            def __matmul__(self, query):
                return list(self.values)

        vectors = Matrix()

        class Logits(list):
            def detach(self):
                return self

            def cpu(self):
                return self

            def tolist(self):
                return list(self)

        class Dense:
            @staticmethod
            def encode_one(question):
                return [1.0, 0.0]

        class Models:
            dense = Dense()

            @staticmethod
            def encode_cross(question, texts):
                return None, Logits(float(index) for index in range(len(texts)))

        ranked, _, _ = runtime._slow_local_path(
            runtime_question="Where does the user live?",
            slow=inventory,
            slow_vectors=vectors,
            models=Models(),
            args=Namespace(slow_dense_k=1),
        )
        by_id = {item["candidate_id"]: item for item in ranked}
        self.assertEqual(set(by_id), {"c1", "c2", "c3"})
        self.assertTrue(by_id["c1"]["summary_expansion_hit"])
        self.assertFalse(by_id["c1"]["direct_claim_hit"])
        self.assertTrue(by_id["c3"]["direct_claim_hit"])
        self.assertFalse(by_id["c3"]["summary_expansion_hit"])
        self.assertTrue(
            all(item["candidate_kind"] == "capsule_claim" for item in ranked)
        )
        context = runtime._slow_memory_contexts([by_id["c1"]])[0]
        self.assertEqual(context["capsule_summary"], c1["capsule_summary_text"])
        self.assertEqual(
            context["retrieval_trace"]["claim_candidate_id"], "c1"
        )

    def test_loader_rejects_claim_only_v3_index(self):
        fake_v3 = SimpleNamespace(
            torch=SimpleNamespace(
                load=lambda *args, **kwargs: {
                    "schema_version": "tmcra.v3.online-index.3"
                }
            )
        )
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            runtime, "_v3", return_value=fake_v3
        ):
            path = Path(temp) / "old.pt"
            with self.assertRaisesRegex(RuntimeError, "claim-only V3 indexes"):
                runtime.load_online_index(path, Path(temp) / "memory.sqlite3", "scope")


if __name__ == "__main__":
    unittest.main()
