import json
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import tmcra_v4_online_runtime as runtime
from tmcra_v4_recall_planner import (
    DEEPSEEK_FLASH_MODEL,
    PLANNER_VERSION,
    RecallPlannerError,
    RecallPlannerResponseError,
    DeepSeekFlashRecallRolePlanner,
    apply_recall_role_plan,
    validate_recall_role_plan,
)


ROOT = Path(__file__).resolve().parent


def plan(*, weights=(0.0, 1.0, 0.0)):
    return {
        "schema_version": PLANNER_VERSION,
        "resolved_query": "What is the current preference?",
        "query_kind": "preference",
        "temporal_focus": "current",
        "conflict_policy": "prefer_recent",
        "layers": {
            "source": {"role": "evidence", "weight": weights[0]},
            "fast": {"role": "atomic", "weight": weights[1]},
            "slow": {"role": "bridge", "weight": weights[2]},
        },
    }


def source(candidate_id="source.0"):
    return {
        "candidate_id": candidate_id,
        "text": "immutable source text",
        "session_id": "session",
        "session_index": 0,
        "parent_chunk_index": 0,
        "subchunk_index": 0,
        "source_char_start": 0,
        "source_char_end": 100,
        "source_record_id": "record.0",
        "canonical_slot": "preference.color",
    }


class V4RecallTests(unittest.TestCase):
    def test_source_group_context_attaches_nearest_unselected_parent_once(self):
        selected = [
            {
                **source("selected.4"),
                "session_index": 3,
                "parent_chunk_index": 4,
                "rank": 1,
                "retrieval_metadata": {},
            },
            {
                **source("selected.8"),
                "session_index": 3,
                "parent_chunk_index": 8,
                "rank": 2,
                "retrieval_metadata": {},
            },
        ]
        inventory = [
            {
                **source(f"parent.{index}"),
                "session_index": 3,
                "parent_chunk_index": index,
                "source_record_id": f"record.{index}",
                "text": f"source parent {index}",
            }
            for index in range(1, 11)
        ]
        grouped, stats = runtime._attach_source_group_context(
            selected, inventory, max_parent_distance=2, max_context_members=2
        )
        attached = [
            member["parent_chunk_index"]
            for item in grouped
            for member in item["source_group_context"]
        ]
        self.assertEqual(len(attached), len(set(attached)))
        self.assertEqual(grouped[0]["source_group_id"], "source-group::session:4")
        self.assertEqual(attached, [3, 5, 7, 9])
        self.assertEqual(stats["attached_context_parent_count"], 4)

    def test_source_group_context_respects_char_budget_and_session_boundary(self):
        selected = [
            {
                **source("selected"),
                "session_index": 1,
                "parent_chunk_index": 2,
                "retrieval_metadata": {},
            }
        ]
        inventory = [
            {**source("same"), "session_index": 1, "parent_chunk_index": 1, "text": "12345"},
            {**source("other"), "session_index": 2, "parent_chunk_index": 1, "text": "x"},
        ]
        grouped, stats = runtime._attach_source_group_context(
            selected, inventory, max_context_chars=4
        )
        self.assertEqual(grouped[0]["source_group_context"], [])
        self.assertEqual(stats["attached_context_parent_count"], 0)

    def test_explicit_planner_replay_is_identity_checked_and_api_free(self):
        row = {
            "question_id": "q1",
            "question": "What is current?",
            "question_date": "2026-07-11",
        }
        replay_plan = plan()
        with tempfile.TemporaryDirectory() as raw_dir:
            replay_dir = Path(raw_dir)
            (replay_dir / "evidence_windows.jsonl").write_text(
                json.dumps({**row, "recall_plan": replay_plan}) + "\n",
                encoding="utf-8",
            )
            (replay_dir / "retrieval_debug.jsonl").write_text(
                json.dumps({
                    "question_id": "q1",
                    "graph_fingerprint": "fingerprint",
                    "recall_plan": replay_plan,
                }) + "\n",
                encoding="utf-8",
            )
            fake_v3 = types.SimpleNamespace(
                read_jsonl=lambda path: [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]
            )
            with mock.patch.object(runtime, "_v3", return_value=fake_v3):
                replay = runtime._load_explicit_planner_replays(
                    replay_dir, [row], {"q1": "fingerprint"}
                )
            self.assertEqual(replay["q1"]["recall_plan"], replay_plan)

    def test_source_subchunks_collapse_to_one_lossless_parent_evidence_unit(self):
        members = [
            {
                **source("chunk.1"),
                "text": "prefix\nabcdefghij",
                "subchunk_index": 1,
                "source_char_start": 0,
                "source_char_end": 10,
            },
            {
                **source("chunk.2"),
                "text": "prefix\nhijklmnop",
                "subchunk_index": 2,
                "source_char_start": 7,
                "source_char_end": 16,
            },
        ]
        collapsed = runtime._collapse_source_parents([members[1]], members)
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0]["text"], "abcdefghijklmnop")
        self.assertEqual(collapsed[0]["subchunk_index"], 0)
        self.assertEqual(collapsed[0]["member_subchunk_indexes"], [1, 2])

    def test_packing_budget_counts_parents_not_subchunks_or_sessions(self):
        first = {**source("parent.0"), "session_id": "same-session"}
        second = {
            **source("parent.1"),
            "session_id": "same-session",
            "parent_chunk_index": 1,
        }
        units = apply_recall_role_plan(
            plan(weights=(1.0, 0.0, 0.0)), [first, second], [], []
        )
        packed = runtime.pack_recall_role_units(
            units, [first, second], top_k=2, qid="q"
        )
        self.assertEqual(len(packed), 2)

    def test_recent_dialogue_projection_omits_oversized_pair_without_truncation(self):
        projected, metadata = runtime.project_recent_dialogue(
            [
                {"turn_index": 1, "speaker": "user", "text": "x" * 4001},
                {"turn_index": 2, "speaker": "assistant", "text": "summary"},
                {"turn_index": 3, "speaker": "user", "text": "my pet"},
                {"turn_index": 4, "speaker": "assistant", "text": "your dog"},
            ]
        )
        self.assertEqual(
            [item["turn_index"] for item in projected],
            [3, 4],
        )
        self.assertEqual(metadata["text_truncation_count"], 0)
        self.assertEqual(metadata["excluded_by_reason"]["text_over_limit"], 1)
        self.assertEqual(
            metadata["excluded_by_reason"]["paired_with_excluded_turn"], 1
        )
        self.assertNotIn("x" * 100, json.dumps(metadata))

    def test_recent_dialogue_projection_retains_valid_trailing_user(self):
        projected, metadata = runtime.project_recent_dialogue(
            [
                {"turn_index": 1, "speaker": "assistant", "text": "old tail"},
                {"turn_index": 2, "speaker": "user", "text": "what about mine?"},
            ]
        )
        self.assertEqual(projected, [{"turn_index": 2, "speaker": "user", "text": "what about mine?"}])
        self.assertEqual(metadata["excluded_by_reason"], {"orphan_assistant": 1})

    def test_recent_dialogue_projection_rejects_corrupt_turn_order(self):
        with self.assertRaisesRegex(RuntimeError, "strictly increasing"):
            runtime.project_recent_dialogue(
                [
                    {"turn_index": 2, "speaker": "user", "text": "later"},
                    {"turn_index": 1, "speaker": "assistant", "text": "earlier"},
                ]
            )

    def test_persisted_retrieval_audit_requires_exact_frozen_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "memory.sqlite3"
            out_dir = Path(directory) / "retrieval_4"
            row = {
                "question_id": "q1",
                "question": "What is current?",
                "question_date": "2026-07-12",
                "scope_id": "scope:q1",
                "db_path": str(db),
                "index_path": str(Path(directory) / "q1.pt"),
            }
            operation_id = "op-q1"
            payload = {
                "event_kind": "tmcra.v3.layered_retrieval",
                "operation_id": operation_id,
                "idempotency_key": operation_id,
                "question_id": "q1",
                "query": row["question"],
                "question_date": row["question_date"],
                "recall_plan": plan(),
                "runtime_input_has_gold": False,
                "graph_fingerprint": "graph-1",
                "evidence_sha256": "a" * 64,
                "query_id": "query:1",
            }
            connection = sqlite3.connect(db)
            try:
                connection.execute(
                    "CREATE TABLE audit_retrieval_log(scope_id TEXT,event_index INTEGER,payload_json TEXT)"
                )
                connection.execute(
                    "INSERT INTO audit_retrieval_log VALUES(?,?,?)",
                    (row["scope_id"], 0, json.dumps(payload)),
                )
                connection.commit()
            finally:
                connection.close()
            fake_v3 = types.SimpleNamespace(
                layered_retrieval_operation_id=lambda *_args: operation_id
            )
            with mock.patch.object(runtime, "_v3", return_value=fake_v3):
                recovered = runtime._load_persisted_retrieval_audit(
                    row=row,
                    out_dir=out_dir,
                    graph_fingerprint="graph-1",
                )
                self.assertEqual(recovered["recall_plan"], plan())
                with self.assertRaisesRegex(RuntimeError, "graph_fingerprint"):
                    runtime._load_persisted_retrieval_audit(
                        row=row,
                        out_dir=out_dir,
                        graph_fingerprint="graph-2",
                    )

    def test_durable_planner_decision_preserves_original_call_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decision.json"
            row = {
                "question_id": "q1",
                "question": "question",
                "question_date": "2026-07-12",
                "scope_id": "scope:q1",
                "db_path": str(Path(directory) / "memory.sqlite3"),
                "index_path": str(Path(directory) / "index.pt"),
            }
            metadata = {
                "physical_api_call": True,
                "physical_api_calls": 1,
                "stage": "recall_planner",
                "model": DEEPSEEK_FLASH_MODEL,
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
            runtime._write_planner_decision(
                path=path,
                row_index=1,
                row=row,
                plan=plan(),
                planner_metadata=metadata,
                context={
                    "graph_fingerprint": "graph-1",
                    "recent_dialogue_projection": {},
                    "available_layers": {},
                },
            )
            recovered = runtime._load_planner_decision(
                path=path,
                row_index=1,
                row=row,
                graph_fingerprint="graph-1",
            )
            self.assertEqual(recovered["planner_metadata"], metadata)

    def test_source_coverage_trace_is_ranked_bounded_and_text_free(self):
        candidates = []
        for index in range(30):
            candidate = source(f"source.{index}")
            candidate.update(
                {
                    "session_id": f"session-{index // 2}",
                    "session_index": index // 2,
                    "parent_chunk_index": index,
                    "subchunk_index": index % 2,
                }
            )
            candidates.append(candidate)
        trace = runtime.source_coverage_trace(candidates)
        self.assertEqual(len(trace), 24)
        self.assertEqual([item["rank"] for item in trace], list(range(1, 25)))
        self.assertEqual(trace[0]["candidate_id"], "source.0")
        self.assertTrue(all("text" not in item for item in trace))

    def test_source_coverage_trace_rejects_missing_identity(self):
        candidate = source()
        candidate["session_id"] = ""
        with self.assertRaisesRegex(RuntimeError, "auditable identity"):
            runtime.source_coverage_trace([candidate])

    def test_index_inventory_excludes_superseded_fast_semantics(self):
        try:
            v3_runtime = runtime._v3()
        except RuntimeError:
            self.skipTest("local workspace does not include the PyTorch V3 runtime")
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "memory.sqlite3"
            con = sqlite3.connect(db)
            try:
                con.execute(
                    "CREATE TABLE records(scope_id TEXT,memory_id TEXT,value TEXT,state TEXT,metadata_json TEXT)"
                )
                base = {
                    "memory_layer": "fast",
                    "content_variant": "product_semantic_memory",
                    "node_kind": "atomic_user_assertion",
                    "atomic_evidence_leaf": True,
                    "authority": "user_assertion",
                    "canonical_slot_key": "memory.user.mortgage.amount",
                    "session_index": 0,
                    "message_index": 0,
                    "source_record_id": "source.0",
                    "evidence_char_start": 0,
                    "evidence_char_end": 20,
                }
                con.execute(
                    "INSERT INTO records VALUES(?,?,?,?,?)",
                    ("scope", "current", "$400,000", "active", json.dumps(base)),
                )
                con.execute(
                    "INSERT INTO records VALUES(?,?,?,?,?)",
                    ("scope", "old", "$350,000", "superseded", json.dumps(base)),
                )
                con.commit()
            finally:
                con.close()
            _, semantic = v3_runtime.load_layered_inventory(
                db,
                "scope",
                [{"session_index": 0, "parent_chunk_index": 0}],
            )
            self.assertEqual([item["memory_id"] for item in semantic], ["current"])
            self.assertEqual(semantic[0]["record_state"], "active")

    def test_fast_semantic_hydration_batches_large_index_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "memory.sqlite3"
            source_id = "source.0000"
            source_text = "grounded quote"
            source_metadata = {
                "content_variant": "source_message",
                "node_kind": "immutable_source_message",
                "immutable_evidence_leaf": True,
                "raw_content": source_text,
                "source_span": source_text,
                "source_turn_text": source_text,
                "source_record_id": source_id,
                "session_id": "session-0",
                "session_index": 0,
                "message_id": "s0_m0",
                "message_index": 0,
                "event_id": "event::scope:s0_m0",
            }
            metadata = {
                "memory_layer": "fast",
                "content_variant": "product_semantic_memory",
                "node_kind": "atomic_user_assertion",
                "atomic_evidence_leaf": True,
                "authority": "user_assertion",
                "memory_type": "preference",
                "durability": "durable",
                "temporal_status": "current",
                "canonical_slot_key": "memory.preference.test",
                "source_record_id": source_id,
                "session_id": "session-0",
                "session_index": 0,
                "message_id": "s0_m0",
                "message_index": 0,
                "parent_chunk_index": 0,
                "event_id": "event::scope:s0_m0",
                "evidence_char_start": 0,
                "evidence_char_end": len(source_text),
                "raw_content": source_text,
                "source_span": source_text,
                "source_turn_text": source_text,
            }
            indexed = [
                {"memory_id": f"memory.{index:04d}"} for index in range(405)
            ]
            con = sqlite3.connect(db)
            try:
                con.execute(
                    "CREATE TABLE records(scope_id TEXT,memory_id TEXT,value TEXT,state TEXT,metadata_json TEXT)"
                )
                con.execute(
                    "INSERT INTO records VALUES(?,?,?,?,?)",
                    (
                        "scope",
                        source_id,
                        source_text,
                        "evidence",
                        json.dumps(source_metadata),
                    ),
                )
                con.executemany(
                    "INSERT INTO records VALUES(?,?,?,?,?)",
                    [
                        (
                            "scope",
                            item["memory_id"],
                            f"preference {index}",
                            "active",
                            json.dumps(metadata),
                        )
                        for index, item in enumerate(indexed)
                    ],
                )
                con.commit()
            finally:
                con.close()

            hydrated = runtime._hydrate_fast_semantic_records(db, "scope", indexed)

            self.assertEqual(len(hydrated), 405)
            self.assertEqual(hydrated[0]["memory_id"], "memory.0000")
            self.assertEqual(hydrated[-1]["text"], "preference 404")
            self.assertTrue(all(item["record_state"] == "active" for item in hydrated))

    def test_prefer_recent_promotes_window_with_current_fast_support(self):
        source_contribution = {
            "layer": "source",
            "within_layer_score": 1.0,
            "priority_score": 1.0,
            "active_semantic": False,
        }
        old_fast_contribution = {
            "layer": "fast",
            "within_layer_score": 1.0,
            "priority_score": 0.5,
            "active_semantic": True,
        }
        updated_fast_contribution = {
            "layer": "fast",
            "within_layer_score": 0.5,
            "priority_score": 0.25,
            "active_semantic": True,
        }
        old = {
            "session_index": 1,
            "parent_chunk_index": 0,
            "retrieval_metadata": {
                "layer_contributions": [source_contribution, old_fast_contribution]
            },
        }
        updated = {
            "session_index": 2,
            "parent_chunk_index": 0,
            "retrieval_metadata": {
                "layer_contributions": [
                    source_contribution,
                    updated_fast_contribution,
                ]
            },
        }
        ordered = runtime._order_evidence_windows(
            {(1, 0, 0): old, (2, 0, 0): updated},
            conflict_policy="prefer_recent",
        )
        self.assertIs(ordered[0], updated)
        self.assertTrue(ordered[0]["retrieval_metadata"]["active_fast_support"])
        self.assertEqual([item["rank"] for item in ordered], [1, 2])

    def test_selected_windows_are_grouped_by_session_support_then_chronology(self):
        def window(session_index, parent_chunk_index):
            return {
                "session_index": session_index,
                "parent_chunk_index": parent_chunk_index,
                "subchunk_index": 1,
                "retrieval_metadata": {
                    "layer_contributions": [
                        {
                            "layer": "source",
                            "within_layer_score": 1.0,
                            "priority_score": 1.0,
                            "active_semantic": False,
                        }
                    ]
                },
            }

        unrelated = window(0, 0)
        session_late = window(1, 3)
        session_early = window(1, 0)
        session_middle = window(1, 2)
        other = window(2, 0)
        ordered = runtime._order_evidence_windows(
            {
                (0, 0, 1): unrelated,
                (1, 3, 1): session_late,
                (1, 0, 1): session_early,
                (1, 2, 1): session_middle,
                (2, 0, 1): other,
            },
            conflict_policy="preserve_parallel",
        )

        self.assertEqual(
            [(item["session_index"], item["parent_chunk_index"]) for item in ordered],
            [(1, 0), (1, 2), (1, 3), (0, 0), (2, 0)],
        )
        for rank, item in enumerate(ordered[:3], start=1):
            self.assertEqual(item["rank"], rank)
            self.assertEqual(
                item["retrieval_metadata"]["session_ordering_policy"],
                runtime.SESSION_ORDERING_POLICY,
            )
            self.assertEqual(
                item["retrieval_metadata"]["session_selected_window_count"], 3
            )
            self.assertEqual(item["retrieval_metadata"]["session_order_rank"], 1)

    def test_exact_schema_and_bounded_weights(self):
        normalized = validate_recall_role_plan(plan())
        self.assertEqual(normalized["layers"]["source"]["weight"], 0.0)
        invalid = plan()
        invalid["layers"]["fast"]["weight"] = 1.01
        with self.assertRaisesRegex(RecallPlannerError, r"\[0, 1\]"):
            validate_recall_role_plan(invalid)
        invalid = plan()
        invalid["layers"]["slow"]["role"] = "disabled"
        with self.assertRaises(RecallPlannerError):
            validate_recall_role_plan(invalid)
        invalid = plan()
        invalid["unexpected"] = True
        with self.assertRaisesRegex(RecallPlannerError, "exactly"):
            validate_recall_role_plan(invalid)
        with self.assertRaisesRegex(RecallPlannerError, "zero weight"):
            validate_recall_role_plan(plan(weights=(0.0, 0.0, 0.0)))

    def test_query_kind_supports_decision_and_bounded_extensions(self):
        decision = plan()
        decision["query_kind"] = "decision"
        self.assertEqual(validate_recall_role_plan(decision)["query_kind"], "decision")

        future = plan()
        future["temporal_focus"] = "future"
        self.assertEqual(validate_recall_role_plan(future)["temporal_focus"], "future")

        extension = plan()
        extension["query_kind"] = "recommendation_request"
        self.assertEqual(
            validate_recall_role_plan(extension)["query_kind"],
            "recommendation_request",
        )

        for invalid_kind in ("bad kind!", "A", "x" * 65):
            invalid = plan()
            invalid["query_kind"] = invalid_kind
            with self.assertRaisesRegex(RecallPlannerError, "bounded snake_case"):
                validate_recall_role_plan(invalid)

    def test_planner_input_requires_exactly_three_layers(self):
        planner = DeepSeekFlashRecallRolePlanner(base_url="https://planner.test/v1", model=DEEPSEEK_FLASH_MODEL, api_keys=["k"])
        with self.assertRaisesRegex(RecallPlannerError, "exactly source"):
            planner.plan(query="q", question_date="2026-01-01", available_layers={"fast": {}, "slow": {}})

    def test_role_composition_keeps_all_layers_even_at_extreme_weights(self):
        source_candidate = source()
        fast_candidate = {**source_candidate, "candidate_id": "fast.0", "score": 0.1}
        slow_candidate = {
            "candidate_id": "slow.0",
            "canonical_slot": "preference.color",
            "text": "slow capsule",
            "capsule_id": "capsule.0",
            "provenance": {"memory_layer": "slow"},
            "source_parents": [{"session_index": 0, "parent_chunk_index": 0, "evidence_char_start": 0, "evidence_char_end": 20}],
        }
        units = apply_recall_role_plan(plan(weights=(0.0, 1.0, 0.0)), [source_candidate], [fast_candidate], [slow_candidate])
        self.assertEqual({unit["layer"] for unit in units}, {"source", "fast", "slow"})
        self.assertEqual({unit["layer_weight"] for unit in units}, {0.0, 1.0})

    def test_all_available_local_paths_execute_under_extreme_weights(self):
        calls = []

        def runner(layer):
            def run():
                calls.append(layer)
                return [layer]

            return run

        result = runtime.execute_local_candidate_paths(
            inventories={"source": [1], "fast": [2], "slow": [3]},
            source_runner=runner("source"),
            fast_runner=runner("fast"),
            slow_runner=runner("slow"),
        )
        self.assertEqual(calls, ["source", "fast", "slow"])
        self.assertEqual(result, {"source": ["source"], "fast": ["fast"], "slow": ["slow"]})

    def test_execution_spies_keep_source_and_fast_conceptually_separate(self):
        calls = []
        result = runtime.execute_local_candidate_paths(
            inventories={"source": [1], "fast": [2], "slow": []},
            source_runner=lambda: calls.append("source:bge_dense_cross") or ["source"],
            fast_runner=lambda: calls.append("fast:graph_node_fusion") or ["fast"],
            slow_runner=lambda: calls.append("slow:dense_cross") or ["slow"],
        )
        self.assertEqual(calls, ["source:bge_dense_cross", "fast:graph_node_fusion"])
        self.assertEqual(result["source"], ["source"])
        self.assertEqual(result["fast"], ["fast"])
        self.assertEqual(result["slow"], [])

    def test_graph_fast_windows_retain_distinct_semantic_slots_and_record_ids(self):
        graph_windows = [
            {**source("graph.0"), "text": "first window"},
            {**source("graph.1"), "text": "second window", "subchunk_index": 1},
        ]
        semantic_records = [
            {"memory_id": "semantic.0", "canonical_slot": "profile.color", "source_parent": {"session_index": 0, "parent_chunk_index": 0, "evidence_char_start": 0, "evidence_char_end": 100}},
            {"memory_id": "semantic.1", "canonical_slot": "profile.food", "source_parent": {"session_index": 0, "parent_chunk_index": 0, "evidence_char_start": 100, "evidence_char_end": 200}},
        ]

        def mapper(candidates, records):
            mapped = []
            for candidate, record in zip(candidates, records):
                item = dict(candidate)
                item["canonical_slot"] = record["canonical_slot"]
                item["semantic_record_ids"] = [record["memory_id"]]
                mapped.append(item)
            return mapped

        fake_v3 = types.SimpleNamespace(_fast_candidates_with_slots=mapper)
        with mock.patch.object(runtime, "_v3", return_value=fake_v3):
            mapped = runtime._map_fast_candidates_with_slots(graph_windows, semantic_records)
        self.assertEqual([item["canonical_slot"] for item in mapped], ["profile.color", "profile.food"])
        self.assertEqual([item["semantic_record_ids"] for item in mapped], [["semantic.0"], ["semantic.1"]])

    def test_raw_negative_scores_do_not_control_cross_layer_priority(self):
        negative_first = {**source("fast.negative"), "score": -1000.0}
        positive_second = {**source("fast.positive"), "score": 1000.0, "subchunk_index": 1}
        units = apply_recall_role_plan(plan(weights=(0.0, 1.0, 0.0)), [], [negative_first, positive_second], [])
        self.assertGreater(units[0]["priority_score"], units[1]["priority_score"])
        self.assertEqual(units[0]["within_layer_score"], 1.0)
        self.assertEqual(units[1]["within_layer_score"], 0.5)

    def test_role_prior_is_normalized_and_affects_composition(self):
        role_plan = plan(weights=(1.0, 1.0, 1.0))
        role_plan["layers"]["source"]["role"] = "primary"
        role_plan["layers"]["fast"]["role"] = "context"
        role_plan["layers"]["slow"]["role"] = "support"
        units = apply_recall_role_plan(role_plan, [source("source")], [source("fast")], [{"candidate_id": "slow", "canonical_slot": "x", "text": "slow", "source_parents": [{"session_index": 0, "parent_chunk_index": 0, "evidence_char_start": 0, "evidence_char_end": 10}]}])
        by_layer = {unit["layer"]: unit for unit in units}
        self.assertGreater(by_layer["source"]["normalized_priority"], by_layer["fast"]["normalized_priority"])
        self.assertEqual(sum(unit["normalized_priority"] for unit in by_layer.values()), 1.0)

    def test_packing_emits_immutable_source_text_and_slow_provenance(self):
        source_candidate = source()
        slow_candidate = {
            "candidate_id": "slow.0",
            "canonical_slot": "preference.color",
            "text": "slow capsule must not become evidence",
            "capsule_id": "capsule.0",
            "provenance": {"memory_layer": "slow"},
            "source_parents": [{"session_index": 0, "parent_chunk_index": 0, "evidence_char_start": 0, "evidence_char_end": 20}],
        }
        units = apply_recall_role_plan(plan(weights=(0.0, 0.0, 1.0)), [source_candidate], [], [slow_candidate])
        packed = runtime.pack_recall_role_units(units, [source_candidate], top_k=1, qid="q")
        entries = [entry for _, windows in packed for entry in windows]
        self.assertTrue(entries)
        self.assertTrue(all(entry["text"] == source_candidate["text"] for entry in entries))
        self.assertTrue(any(entry["capsules"] for entry in entries))

    def test_fast_unit_is_mapped_back_to_immutable_source_window(self):
        source_candidate = source("source.0")
        transformed_fast = {**source_candidate, "candidate_id": "fast.0", "text": "learned fusion annotation"}
        units = apply_recall_role_plan(plan(weights=(0.0, 1.0, 0.0)), [source_candidate], [transformed_fast], [])
        packed = runtime.pack_recall_role_units(units, [source_candidate], top_k=1, qid="q")
        entries = [entry for _, windows in packed for entry in windows]
        self.assertTrue(entries)
        self.assertTrue(all(entry["text"] == source_candidate["text"] for entry in entries))

    def test_duplicate_physical_window_retains_source_and_fast_units(self):
        source_candidate = source("source.0")
        fast_candidate = {
            **source_candidate,
            "candidate_id": "fast.0",
            "semantic_record_ids": ["semantic.current"],
        }
        units = apply_recall_role_plan(
            plan(weights=(1.0, 1.0, 0.0)),
            [source_candidate],
            [fast_candidate],
            [],
        )
        packed, stats = runtime.pack_recall_role_units(
            units,
            [source_candidate],
            top_k=1,
            qid="q",
            return_stats=True,
        )
        self.assertEqual({item[0]["layer"] for item in packed}, {"source", "fast"})
        self.assertEqual(stats["duplicate_unit_count"], 1)

    def test_production_packing_guarantees_all_required_layers(self):
        source_candidate = source("source.0")
        fast_candidate = {
            **source_candidate,
            "candidate_id": "fast.0",
            "canonical_slot": "profile.hotel",
            "semantic_record_ids": ["semantic.current"],
        }
        slow_candidate = {
            "candidate_id": "slow.0",
            "canonical_slot": "profile.hotel",
            "text": "The user prefers rooftop pools.",
            "capsule_id": "cap.0",
            "revision": 1,
            "status": "active",
            "claims": [
                {
                    "claim_id": "clm.0",
                    "canonical_slot": "profile.hotel",
                    "text": "The user prefers rooftop pools.",
                    "support": ["semantic.old"],
                    "counterevidence": [],
                }
            ],
            "provenance": {"memory_layer": "slow", "claim_id": "clm.0"},
            "source_parents": [
                {
                    "session_index": 0,
                    "parent_chunk_index": 0,
                    "evidence_char_start": 0,
                    "evidence_char_end": 20,
                }
            ],
        }
        units = apply_recall_role_plan(
            plan(weights=(1.0, 0.01, 0.01)),
            [source_candidate],
            [fast_candidate],
            [slow_candidate],
        )
        packed, stats = runtime.pack_recall_role_units(
            units,
            [source_candidate],
            top_k=1,
            qid="q",
            required_layers=["source", "fast", "slow"],
            return_stats=True,
        )
        self.assertEqual({unit["layer"] for unit, _ in packed}, {"source", "fast", "slow"})
        self.assertEqual(stats["required_layers"], ["source", "fast", "slow"])

    def test_newer_fast_memory_is_marked_as_slow_override(self):
        windows = [
            {
                "memory_contexts": [
                    {
                        "canonical_slot": "profile.hotel",
                        "support": ["semantic.old"],
                        "source_parents": [
                            {"session_index": 1, "parent_chunk_index": 0}
                        ],
                    }
                ],
                "attachments": [],
            },
            {
                "memory_contexts": [],
                "attachments": [
                    {
                        "role": "fast_context",
                        "memory_id": "semantic.new",
                        "canonical_slot": "profile.hotel",
                        "source_parent": {
                            "session_index": 2,
                            "parent_chunk_index": 0,
                        },
                    }
                ],
            },
        ]
        runtime._mark_newer_fast_overrides(windows)
        attachment = windows[1]["attachments"][0]
        self.assertEqual(attachment["role"], "override")
        self.assertEqual(attachment["precedence"], "newer_fast_evidence")

    def test_packing_continues_after_later_nonfitting_unit(self):
        source_zero = source("source.0")
        source_one = {**source("source.1"), "parent_chunk_index": 1}
        source_two = {**source("source.2"), "parent_chunk_index": 2}
        oversized_slow = {"candidate_id": "slow", "canonical_slot": "slow", "text": "slow", "source_parents": [{"session_index": 0, "parent_chunk_index": 1, "evidence_char_start": 0, "evidence_char_end": 20}, {"session_index": 0, "parent_chunk_index": 2, "evidence_char_start": 0, "evidence_char_end": 20}]}
        units = [
            {"unit_type": "source_window", "layer": "source", "canonical_slot": "source.0", "source_candidate": source_zero, "priority_score": 1.0, "layer_weight": 1.0, "layer_rank": 1, "within_layer_score": 1.0, "layer_role": "primary", "role_prior": 1.0, "normalized_priority": 1.0},
            {"unit_type": "slow_capsule", "layer": "slow", "canonical_slot": "slow", "slow_candidate": oversized_slow, "priority_score": 0.5, "layer_weight": 0.5, "layer_rank": 1, "within_layer_score": 1.0, "layer_role": "support", "role_prior": 0.75, "normalized_priority": 0.5},
            {"unit_type": "source_window", "layer": "source", "canonical_slot": "source.2", "source_candidate": source_two, "priority_score": 0.4, "layer_weight": 0.4, "layer_rank": 2, "within_layer_score": 0.5, "layer_role": "primary", "role_prior": 1.0, "normalized_priority": 0.8},
        ]
        packed, stats = runtime.pack_recall_role_units(units, [source_zero, source_one, source_two], top_k=2, qid="q", return_stats=True)
        self.assertEqual([item[0]["canonical_slot"] for item in packed], ["source.0", "source.2"])
        self.assertEqual(stats["budget_excluded_unit_count"], 1)

    def test_adaptive_packing_budget_uses_plan_semantics(self):
        simple = plan()
        simple["query_kind"] = "fact"
        simple["temporal_focus"] = "timeless"
        simple["conflict_policy"] = "surface_uncertainty"
        budget, decision = runtime.resolve_packing_budget(
            simple,
            mode="adaptive",
            fixed_k=8,
            simple_k=8,
            standard_k=12,
            complex_k=16,
        )
        self.assertEqual((budget, decision["tier"]), (16, "complex"))

        simple["conflict_policy"] = "preserve_parallel"
        budget, decision = runtime.resolve_packing_budget(
            simple,
            mode="fixed",
            fixed_k=8,
            simple_k=8,
            standard_k=12,
            complex_k=16,
        )
        self.assertEqual((budget, decision["tier"]), (8, "fixed"))

        simple["conflict_policy"] = "prefer_durable"
        budget, decision = runtime.resolve_packing_budget(
            simple,
            mode="adaptive",
            fixed_k=8,
            simple_k=8,
            standard_k=12,
            complex_k=16,
        )
        self.assertEqual((budget, decision["tier"]), (8, "simple"))

        simple["conflict_policy"] = "compare"
        budget, decision = runtime.resolve_packing_budget(
            simple,
            mode="adaptive",
            fixed_k=8,
            simple_k=8,
            standard_k=12,
            complex_k=16,
        )
        self.assertEqual((budget, decision["tier"]), (16, "complex"))

    def test_adaptive_packing_budget_rejects_invalid_order(self):
        with self.assertRaisesRegex(RuntimeError, "simple <= standard <= complex"):
            runtime.resolve_packing_budget(
                plan(),
                mode="adaptive",
                fixed_k=8,
                simple_k=12,
                standard_k=8,
                complex_k=16,
            )

    def test_active_v3_runtime_is_preferred_over_tmp_fallback(self):
        fake_active = types.ModuleType("tmcra_v3_online_runtime")
        old = runtime._V3
        runtime._V3 = None
        try:
            with mock.patch.dict(sys.modules, {"tmcra_v3_online_runtime": fake_active}):
                self.assertIs(runtime._v3(), fake_active)
        finally:
            runtime._V3 = old

    def test_invalid_api_output_fails_without_retry_or_fallback(self):
        planner = DeepSeekFlashRecallRolePlanner(base_url="https://planner.test/v1", model=DEEPSEEK_FLASH_MODEL, api_keys=["k1", "k2"])
        body = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"schema_version": PLANNER_VERSION})}}]}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(body).encode("utf-8")

        with mock.patch("tmcra_v4_recall_planner.urllib.request.urlopen", return_value=Response()) as call:
            with self.assertRaises(RecallPlannerResponseError):
                planner.plan(query="q", question_date="2026-01-01", available_layers={"source": {}, "fast": {}, "slow": {}})
        self.assertEqual(call.call_count, 1)

    def test_noncanonical_query_kind_is_preserved_with_warning(self):
        planner = DeepSeekFlashRecallRolePlanner(base_url="https://planner.test/v1", model=DEEPSEEK_FLASH_MODEL, api_keys=["k"])
        extension = plan()
        extension["query_kind"] = "recommendation_request"
        body = {
            "id": "response.1",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(extension)},
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def getcode(self):
                return 200

            def read(self):
                return json.dumps(body).encode("utf-8")

        with mock.patch("tmcra_v4_recall_planner.urllib.request.urlopen", return_value=Response()) as call:
            normalized, metadata = planner.plan(
                query="Should I buy it?",
                question_date="2026-01-01",
                available_layers={"source": {}, "fast": {}, "slow": {}},
            )
        self.assertEqual(call.call_count, 1)
        self.assertEqual(normalized["query_kind"], "recommendation_request")
        self.assertEqual(
            metadata["validation_warnings"],
            [
                {
                    "code": "noncanonical_query_kind",
                    "query_kind": "recommendation_request",
                    "disposition": "preserved_as_standard_query",
                }
            ],
        )

    def test_cli_keeps_v3_subcommands_and_arguments(self):
        completed = subprocess.run([sys.executable, str(ROOT / "tmcra_v4_online_runtime.py"), "--help"], capture_output=True, text=True, check=True)
        self.assertIn("build-index", completed.stdout)
        self.assertIn("retrieve", completed.stdout)


if __name__ == "__main__":
    unittest.main()
