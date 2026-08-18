import json
import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from run_tmcra_v4_evaluate import (
    EvaluationError,
    _build_evaluation_audit,
    _reference_rows,
    _source_top24_session_ids,
    _validate_answer_facing_evidence,
    _validate_judge_resume_rows,
    official_judge_correct,
)
from tmcra_v4_evidence_operations import (
    PACKET_COMPILER_VERSION,
    PACKET_SCHEMA,
    PLAN_SCHEMA,
)
from tmcra_v4_route_policy import (
    RETRIEVAL_CONTRACT_SCHEMA,
    RoutePolicyError,
    assert_production_answer_runner,
    validate_production_answers,
    validate_production_evidence,
)


class V4EvaluationTests(unittest.TestCase):
    def production_row(self):
        contribution = {
            "layer": "source",
            "role": "primary",
            "weight": 1.0,
            "normalized_priority": 1.0,
            "within_layer_score": 1.0,
            "priority_score": 1.0,
            "active_semantic": False,
        }
        window = {
            "session_id": "s1",
            "text": "Source fact.",
            "memory_contexts": [],
            "attachments": [],
            "provenance": [],
            "retrieval_metadata": {"layer_contributions": [contribution]},
        }
        return {
            "question_id": "q1",
            "retrieval_contract": {
                "schema_version": RETRIEVAL_CONTRACT_SCHEMA,
                "execution_lane": "production",
                "composition_mode": "layered",
                "inventory_counts": {
                    "source": 1,
                    "fast": 0,
                    "fast_semantic": 0,
                    "slow": 0,
                    "slow_capsule_heads": 0,
                    "slow_summaries": 0,
                    "slow_claims": 0,
                    "slow_ranked_claims": 0,
                },
                "candidate_paths_executed": {
                    "source": True,
                    "fast": False,
                    "slow": False,
                },
                "required_selected_layers": ["source"],
                "selected_layer_window_counts": {
                    "source": 1,
                    "fast": 0,
                    "slow": 0,
                },
                "packing_budget_mode": "fixed",
                "packing_budget": 8,
                "source_coverage_trace_k": 24,
                "final_window_count": 1,
            },
            "evidence_windows": [window],
            "compiled_evidence_packet": {
                "schema_version": PACKET_SCHEMA,
                "packet_compiler_version": PACKET_COMPILER_VERSION,
                "question_id": "q1",
                "question_contract": {
                    "question": "What is the fact?",
                    "question_date": "2026-07-13",
                    "requirement_count": 0,
                    "operation_count": 0,
                },
                "operation_plan": {
                    "schema_version": PLAN_SCHEMA,
                    "requirements": [],
                    "operations": [],
                    "bundles": [],
                },
                "requirement_coverage": [],
                "operation_results": [],
                "evidence_bundles": [],
                "raw_evidence_reservoir": [
                    {"evidence_id": "E01", **window}
                ],
            },
        }

    def test_production_route_is_locked_to_operation_bound_source_evidence(self):
        report = validate_production_evidence([self.production_row()])
        self.assertEqual(report["answer_protocol"], "evidence_operation_bound_v5")
        assert_production_answer_runner(Path("/opt/tmcra/run_tmcra_v4_gpt54_answers.py"))
        validate_production_answers(
            [
                {
                    "question_id": "q1",
                    "answer_protocol": "evidence_operation_bound_v5",
                    "answer_model": "gpt-5.4",
                }
            ]
        )

    def test_production_route_accepts_legacy_implicit_source_group_identity(self):
        row = self.production_row()
        row["compiled_evidence_packet"]["raw_evidence_reservoir"][0][
            "source_group_id"
        ] = "source-group::s1:0"
        report = validate_production_evidence([row])
        self.assertEqual(report["question_count"], 1)

    def test_terminal_whitespace_drift_is_rejected_for_all_production_packets(self):
        row = self.production_row()
        row["evidence_windows"][0]["text"] = "Source fact. "
        with self.assertRaisesRegex(RoutePolicyError, "preserve Source evidence"):
            validate_production_evidence([row])

    def test_production_route_rejects_semantic_shadow_or_wrong_runner(self):
        row = self.production_row()
        row["semantic_evidence_packet"] = {"schema_version": "shadow"}
        with self.assertRaisesRegex(RoutePolicyError, "shadow"):
            validate_production_evidence([row])
        with self.assertRaisesRegex(RoutePolicyError, "answer runner"):
            assert_production_answer_runner(Path("run_v3_gpt54_answers.py"))

    def test_production_route_rejects_diagnostic_or_missing_layer_contribution(self):
        row = self.production_row()
        row["retrieval_contract"]["execution_lane"] = "diagnostic"
        with self.assertRaisesRegex(RoutePolicyError, "diagnostic"):
            validate_production_evidence([row])

        row = self.production_row()
        row["retrieval_contract"]["required_selected_layers"] = ["source", "slow"]
        row["retrieval_contract"]["inventory_counts"]["slow"] = 1
        row["retrieval_contract"]["inventory_counts"].update(
            slow_capsule_heads=1,
            slow_summaries=1,
            slow_claims=1,
            slow_ranked_claims=1,
        )
        row["retrieval_contract"]["candidate_paths_executed"]["slow"] = True
        with self.assertRaisesRegex(RoutePolicyError, "omitted required layers"):
            validate_production_evidence([row])

    def test_production_route_derives_required_layers_from_actual_shortlists(self):
        row = self.production_row()
        row["retrieval_contract"]["inventory_counts"]["fast"] = 1
        row["retrieval_contract"]["candidate_paths_executed"]["fast"] = True
        with self.assertRaisesRegex(RoutePolicyError, "do not match nonempty inventories"):
            validate_production_evidence([row])

        row = self.production_row()
        row["retrieval_contract"]["inventory_counts"]["fast"] = 1
        row["retrieval_contract"]["inventory_counts"]["fast_semantic"] = 2
        row["retrieval_contract"]["candidate_paths_executed"]["fast"] = True
        row["retrieval_contract"]["required_selected_layers"].append("fast")
        with self.assertRaisesRegex(RoutePolicyError, "exceeds the Fast shortlist"):
            validate_production_evidence([row])

    def test_production_route_requires_typed_slow_inventory_counts(self):
        row = self.production_row()
        for field in (
            "slow_capsule_heads",
            "slow_summaries",
            "slow_claims",
            "slow_ranked_claims",
        ):
            row["retrieval_contract"]["inventory_counts"].pop(field)
        with self.assertRaisesRegex(RoutePolicyError, "inventory contract is invalid"):
            validate_production_evidence([row])

        row = self.production_row()
        row["retrieval_contract"]["inventory_counts"].update(
            slow_capsule_heads=1,
            slow_summaries=1,
            slow_claims=1,
            slow_ranked_claims=2,
            slow=2,
        )
        row["retrieval_contract"]["candidate_paths_executed"]["slow"] = True
        row["retrieval_contract"]["required_selected_layers"].append("slow")
        with self.assertRaisesRegex(
            RoutePolicyError, "Slow summary/claim inventory counts are inconsistent"
        ):
            validate_production_evidence([row])

    def test_production_route_rejects_source_rewrite_and_protocol_drift(self):
        row = self.production_row()
        row["compiled_evidence_packet"]["raw_evidence_reservoir"][0]["text"] = "rewritten"
        with self.assertRaisesRegex(RoutePolicyError, "preserve Source evidence"):
            validate_production_evidence([row])
        with self.assertRaisesRegex(RoutePolicyError, "protocol"):
            validate_production_answers(
                [
                    {
                        "question_id": "q1",
                        "answer_protocol": "semantic_evidence_bound_v1",
                        "answer_model": "gpt-5.4",
                    }
                ]
            )

    def test_production_route_rejects_stub_packet_before_answer_api(self):
        row = self.production_row()
        row["compiled_evidence_packet"] = {
            "schema_version": PACKET_SCHEMA,
            "packet_compiler_version": PACKET_COMPILER_VERSION,
            "question_id": "q1",
            "raw_evidence_reservoir": [
                {"evidence_id": "E01", **row["evidence_windows"][0]}
            ],
        }
        with self.assertRaisesRegex(RoutePolicyError, "question contract"):
            validate_production_evidence([row])

    def test_production_route_rejects_adaptive_or_non_top8_contract(self):
        row = self.production_row()
        row["retrieval_contract"]["packing_budget_mode"] = "adaptive"
        with self.assertRaisesRegex(RoutePolicyError, "fixed Top8"):
            validate_production_evidence([row])
        row = self.production_row()
        row["retrieval_contract"]["packing_budget"] = 16
        with self.assertRaisesRegex(RoutePolicyError, "fixed Top8"):
            validate_production_evidence([row])

    def test_production_route_rejects_source_group_context_loss(self):
        row = self.production_row()
        context = [
            {
                "relationship": "session_neighbor",
                "parent_distance": 1,
                "session_id": "s1",
                "session_index": 1,
                "parent_chunk_index": 1,
                "source_record_id": "src-neighbor",
                "source_char_start": 10,
                "source_char_end": 31,
                "text": "Adjacent source fact.",
            }
        ]
        row["evidence_windows"][0]["source_group_id"] = "source-group::s1:0"
        row["evidence_windows"][0]["source_group_context"] = context
        reservoir = row["compiled_evidence_packet"]["raw_evidence_reservoir"][0]
        reservoir["source_group_id"] = "source-group::s1:0"
        reservoir["source_group_context"] = [dict(context[0])]
        validate_production_evidence([row])
        reservoir["source_group_context"][0]["source_char_start"] = 11
        with self.assertRaisesRegex(RoutePolicyError, "preserve Source evidence"):
            validate_production_evidence([row])

    def test_production_route_preserves_slow_claim_context_exactly(self):
        row = self.production_row()
        context = {
            "role": "slow_context",
            "capsule_id": "cap1",
            "revision": 1,
            "status": "active",
            "claim_id": "clm1",
            "canonical_slot": "profile.hotel",
            "claim_text": "The user prefers rooftop pools.",
            "support": ["fast1"],
            "counterevidence": [],
            "source_parents": [
                {
                    "session_index": 0,
                    "parent_chunk_index": 0,
                    "evidence_char_start": 0,
                    "evidence_char_end": 12,
                }
            ],
            "provenance": {"memory_layer": "slow", "claim_id": "clm1"},
        }
        contribution = {
            "layer": "slow",
            "role": "bridge",
            "weight": 1.0,
            "normalized_priority": 1.0,
            "within_layer_score": 1.0,
            "priority_score": 1.0,
            "active_semantic": False,
        }
        window = row["evidence_windows"][0]
        window["memory_contexts"] = [context]
        window["retrieval_metadata"]["layer_contributions"].append(contribution)
        reservoir = row["compiled_evidence_packet"]["raw_evidence_reservoir"][0]
        reservoir["memory_contexts"] = [dict(context)]
        reservoir["retrieval_metadata"]["layer_contributions"].append(
            dict(contribution)
        )
        row["retrieval_contract"]["inventory_counts"]["slow"] = 1
        row["retrieval_contract"]["inventory_counts"].update(
            slow_capsule_heads=1,
            slow_summaries=1,
            slow_claims=1,
            slow_ranked_claims=1,
        )
        row["retrieval_contract"]["candidate_paths_executed"]["slow"] = True
        row["retrieval_contract"]["required_selected_layers"].append("slow")
        row["retrieval_contract"]["selected_layer_window_counts"]["slow"] = 1
        validate_production_evidence([row])
        reservoir["memory_contexts"][0]["claim_text"] = "rewritten"
        with self.assertRaisesRegex(RoutePolicyError, "layered memory context"):
            validate_production_evidence([row])

    def test_production_route_rejects_unverified_fast_override(self):
        row = self.production_row()
        contribution = {
            "layer": "fast",
            "role": "atomic",
            "weight": 1.0,
            "normalized_priority": 1.0,
            "within_layer_score": 1.0,
            "priority_score": 1.0,
            "active_semantic": True,
        }
        override = {
            "role": "override",
            "memory_id": "fast1",
            "canonical_slot": "profile.hotel",
            "text": "The user now prefers a balcony hot tub.",
            "source_parent": {"session_index": 1, "parent_chunk_index": 0},
            "provenance": {"memory_layer": "fast"},
        }
        window = row["evidence_windows"][0]
        window["attachments"] = [override]
        window["retrieval_metadata"]["layer_contributions"].append(contribution)
        reservoir = row["compiled_evidence_packet"]["raw_evidence_reservoir"][0]
        reservoir["attachments"] = [dict(override)]
        reservoir["retrieval_metadata"]["layer_contributions"].append(
            dict(contribution)
        )
        contract = row["retrieval_contract"]
        contract["inventory_counts"]["fast"] = 1
        contract["inventory_counts"]["fast_semantic"] = 1
        contract["candidate_paths_executed"]["fast"] = True
        contract["required_selected_layers"].append("fast")
        contract["selected_layer_window_counts"]["fast"] = 1
        with self.assertRaisesRegex(RoutePolicyError, "verified precedence"):
            validate_production_evidence([row])

        override["precedence"] = "newer_fast_evidence"
        reservoir["attachments"][0]["precedence"] = "newer_fast_evidence"
        validate_production_evidence([row])

    def test_reads_actual_official_judge_label(self):
        self.assertTrue(
            official_judge_correct(
                {"error": "", "autoeval_label": {"label": True, "model": "gpt-5.4"}}
            )
        )
        self.assertFalse(
            official_judge_correct(
                {"error": "", "autoeval_label": {"label": False, "model": "gpt-5.4"}}
            )
        )

    def test_rejects_old_nonexistent_score_shape(self):
        with self.assertRaisesRegex(EvaluationError, "autoeval_label.label"):
            official_judge_correct({"error": "", "score": 1})

    def test_rejects_judge_error(self):
        with self.assertRaisesRegex(EvaluationError, "contains an error"):
            official_judge_correct(
                {"error": "timeout", "autoeval_label": {"label": True}}
            )

    def test_rejects_benchmark_fields_in_answer_facing_evidence(self):
        with self.assertRaisesRegex(EvaluationError, "gold_answer"):
            _validate_answer_facing_evidence(
                [{"question_id": "q1", "gold_answer": "must stay in reference"}]
            )

    def test_rejects_bad_source_trace_before_environment_or_api_calls(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            retrieval_dir = root / "retrieval_1"
            retrieval_dir.mkdir()
            (retrieval_dir / "RETRIEVAL_COMPLETE").write_text(
                "done\n", encoding="utf-8"
            )
            evidence_row = self.production_row()
            evidence_row["selected_session_ids"] = ["s1"]
            (retrieval_dir / "evidence_windows.jsonl").write_text(
                json.dumps(evidence_row) + "\n", encoding="utf-8"
            )
            (retrieval_dir / "retrieval_debug.jsonl").write_text(
                json.dumps(
                    {
                        "question_id": "q1",
                        "source_candidate_count": 1,
                        "source_coverage_trace_k": 24,
                        "source_top24_candidates": [
                            {
                                "rank": 2,
                                "candidate_id": "c1",
                                "session_id": "s1",
                                "session_index": 0,
                                "parent_chunk_index": 0,
                                "subchunk_index": 0,
                            }
                        ],
                        "source_candidate_pool_trace": [
                            {
                                "rank": 1,
                                "candidate_id": "c1",
                                "session_id": "s1",
                                "session_index": 0,
                                "parent_chunk_index": 0,
                                "subchunk_index": 0,
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "qids.txt").write_text("q1\n", encoding="utf-8")
            args = Namespace(
                run_dir=root,
                retrieval_tag="retrieval_1",
                resume=False,
                answer_env=root / "missing-answer.env",
                answer_runner=Path("run_tmcra_v4_gpt54_answers.py"),
                harness=Path("harness.py"),
                judge=Path("official_judge.py"),
                data=root / "reference.json",
                qid_list=None,
                answer_workers=1,
                answer_attempts=1,
                answer_window_limit=8,
            )
            with (
                patch("run_tmcra_v4_evaluate._load_environment") as env_mock,
                patch("run_tmcra_v4_evaluate._run") as run_mock,
            ):
                with self.assertRaisesRegex(
                    EvaluationError, "Source Top24 coverage trace is malformed"
                ):
                    __import__("run_tmcra_v4_evaluate").evaluate(args)
            env_mock.assert_not_called()
            run_mock.assert_not_called()

    def test_source_top24_trace_requires_exact_pool_prefix_and_unique_location(self):
        first = {
            "rank": 1,
            "candidate_id": "c1",
            "session_id": "s1",
            "session_index": 0,
            "parent_chunk_index": 0,
            "subchunk_index": 0,
        }
        second = {
            "rank": 2,
            "candidate_id": "c2",
            "session_id": "s2",
            "session_index": 1,
            "parent_chunk_index": 0,
            "subchunk_index": 0,
        }
        base = {
            "source_candidate_count": 2,
            "source_coverage_trace_k": 24,
            "source_top24_candidates": [first, second],
            "source_candidate_pool_trace": [first, second],
        }
        self.assertEqual(_source_top24_session_ids(base, "q1"), ["s1", "s2"])

        short = {**base, "source_top24_candidates": [first]}
        with self.assertRaisesRegex(EvaluationError, "expected 2"):
            _source_top24_session_ids(short, "q1")

        candidate_id_collision = {
            **base,
            "source_top24_candidates": [
                first,
                {**second, "candidate_id": "c1"},
            ],
            "source_candidate_pool_trace": [
                first,
                {**second, "candidate_id": "c1"},
            ],
        }
        self.assertEqual(
            _source_top24_session_ids(candidate_id_collision, "q1"),
            ["s1", "s2"],
        )

        duplicate_location = {
            **base,
            "source_top24_candidates": [
                first,
                {
                    **second,
                    "session_id": "s1",
                    "session_index": 0,
                    "parent_chunk_index": 0,
                    "subchunk_index": 0,
                },
            ],
            "source_candidate_pool_trace": [
                first,
                {
                    **second,
                    "session_id": "s1",
                    "session_index": 0,
                    "parent_chunk_index": 0,
                    "subchunk_index": 0,
                },
            ],
        }
        with self.assertRaisesRegex(EvaluationError, "duplicate evidence"):
            _source_top24_session_ids(duplicate_location, "q1")

        swapped = {
            **base,
            "source_top24_candidates": [
                {**second, "rank": 1},
                {**first, "rank": 2},
            ],
        }
        with self.assertRaisesRegex(EvaluationError, "exact candidate-pool prefix"):
            _source_top24_session_ids(swapped, "q1")

    def test_builds_reference_backed_audit_without_using_judge_gold(self):
        reference = [
            {
                "question_id": "q1",
                "answer": "reference answer",
                "answer_session_ids": ["s1", "s2"],
                "question_type": "knowledge-update",
            },
            {
                "question_id": "unused-reference-qid",
                "answer": "unused",
                "answer_session_ids": ["unused-session"],
            },
        ]
        evidence = [{"question_id": "q1", "selected_session_ids": ["s1"]}]
        judged = [
            {
                "question_id": "q1",
                "error": "",
                "gold_answer": "",
                "autoeval_label": {"label": True},
            }
        ]
        retrieval_debug = [
            {
                "question_id": "q1",
                "source_candidate_count": 2,
                "source_coverage_trace_k": 24,
                "source_top24_candidates": [
                    {
                        "rank": 1,
                        "candidate_id": "c1",
                        "session_id": "s1",
                        "session_index": 0,
                        "parent_chunk_index": 0,
                        "subchunk_index": 0,
                    },
                    {
                        "rank": 2,
                        "candidate_id": "c2",
                        "session_id": "s2",
                        "session_index": 1,
                        "parent_chunk_index": 0,
                        "subchunk_index": 0,
                    },
                ],
                "source_candidate_pool_trace": [
                    {
                        "rank": 1,
                        "candidate_id": "c1",
                        "session_id": "s1",
                        "session_index": 0,
                        "parent_chunk_index": 0,
                        "subchunk_index": 0,
                    },
                    {
                        "rank": 2,
                        "candidate_id": "c2",
                        "session_id": "s2",
                        "session_index": 1,
                        "parent_chunk_index": 0,
                        "subchunk_index": 0,
                    },
                ],
            }
        ]
        audit = _build_evaluation_audit(
            ["q1"], _reference_rows(reference), evidence, retrieval_debug, judged
        )
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["gold_answer"], "reference answer")
        self.assertEqual(audit[0]["answer_session_ids"], ["s1", "s2"])
        self.assertEqual(audit[0]["selected_session_ids"], ["s1"])
        self.assertEqual(audit[0]["missing_session_ids"], ["s2"])
        self.assertFalse(audit[0]["session_coverage"]["complete"])
        self.assertTrue(audit[0]["source_top24_coverage"]["complete"])
        self.assertFalse(audit[0]["final_evidence_coverage"]["complete"])
        self.assertTrue(audit[0]["official_label"])

    def test_evaluate_writes_audit_after_judge_without_reference_in_answer_call(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            retrieval_dir = root / "retrieval_1"
            retrieval_dir.mkdir()
            (retrieval_dir / "RETRIEVAL_COMPLETE").write_text("done\n", encoding="utf-8")
            evidence_row = self.production_row()
            evidence_row["selected_session_ids"] = ["s1"]
            (retrieval_dir / "evidence_windows.jsonl").write_text(
                json.dumps(evidence_row) + "\n", encoding="utf-8"
            )
            (retrieval_dir / "retrieval_debug.jsonl").write_text(
                json.dumps(
                    {
                        "question_id": "q1",
                        "source_candidate_count": 1,
                        "source_coverage_trace_k": 24,
                        "source_top24_candidates": [
                            {
                                "rank": 1,
                                "candidate_id": "c1",
                                "session_id": "s1",
                                "session_index": 0,
                                "parent_chunk_index": 0,
                                "subchunk_index": 0,
                            }
                        ],
                        "source_candidate_pool_trace": [
                            {
                                "rank": 1,
                                "candidate_id": "c1",
                                "session_id": "s1",
                                "session_index": 0,
                                "parent_chunk_index": 0,
                                "subchunk_index": 0,
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "qids.txt").write_text("q1\n", encoding="utf-8")
            reference_path = root / "reference.json"
            reference_path.write_text(
                json.dumps(
                    [
                        {
                            "question_id": "q1",
                            "answer": "gold from reference",
                            "answer_session_ids": ["s1"],
                        },
                        {
                            "question_id": "unused",
                            "answer": "unused gold",
                            "answer_session_ids": ["s-unused"],
                        },
                    ]
                ),
                encoding="utf-8",
            )
            answer_dir = root / "answer_retrieval_1"
            answer_path = answer_dir / "answers.jsonl"
            judge_path = root / "retrieval_1.official_judge.jsonl"

            def fake_run(command, log_path, environment):
                if "--ref-file" in command:
                    answer = json.loads(answer_path.read_text(encoding="utf-8").splitlines()[0])
                    judge_path.write_text(
                        json.dumps(
                            {
                                "question_id": "q1",
                                "question": answer["question"],
                                "hypothesis": answer["hypothesis"],
                                "evidence_sha256": answer["evidence_sha256"],
                                "answer_model": answer["answer_model"],
                                "gold_answer": "",
                                "error": "",
                                "autoeval_label": {"label": True},
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                else:
                    answer_dir.mkdir(parents=True, exist_ok=True)
                    answer_path.write_text(
                        json.dumps(
                            {
                                "question_id": "q1",
                                "question": "question",
                                "hypothesis": "answer",
                                "evidence_sha256": "hash",
                                "answer_model": "gpt-5.4",
                                "answer_protocol": "evidence_operation_bound_v5",
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )

            args = Namespace(
                run_dir=root,
                retrieval_tag="retrieval_1",
                resume=False,
                answer_env=root / "answer.env",
                answer_runner=Path("run_tmcra_v4_gpt54_answers.py"),
                harness=Path("harness.py"),
                judge=Path("official_judge.py"),
                data=reference_path,
                qid_list=None,
                answer_workers=1,
                answer_attempts=1,
                answer_window_limit=8,
            )
            args.answer_env.write_text("", encoding="utf-8")
            answer_before = None
            judge_before = None
            with (
                patch(
                    "run_tmcra_v4_evaluate._load_environment",
                    return_value={
                        "TMCRA_ANSWER_MODEL": "gpt-5.4",
                        "TMCRA_ANSWER_BASE_URL": "https://answer.invalid/v1",
                    },
                ),
                patch("run_tmcra_v4_evaluate._run", side_effect=fake_run) as run_mock,
            ):
                summary = __import__("run_tmcra_v4_evaluate").evaluate(args)
                answer_before = answer_path.read_bytes()
                judge_before = judge_path.read_bytes()

            answer_command = run_mock.call_args_list[0].args[0]
            judge_command = run_mock.call_args_list[1].args[0]
            self.assertNotIn("--ref-file", answer_command)
            self.assertNotIn(str(reference_path), [str(item) for item in answer_command])
            self.assertEqual(judge_command[judge_command.index("--metric-model") + 1], "gpt-5.4")
            self.assertEqual(summary["evaluation_audit"]["reference_row_count"], 2)
            self.assertEqual(
                summary["evaluation_audit"]["source_top24_gold_session_hit_rate"],
                1.0,
            )
            self.assertEqual(
                summary["evaluation_audit"]["final_evidence_gold_session_hit_rate"],
                1.0,
            )
            self.assertTrue(summary["evaluation_quality_gate_passed"])
            self.assertFalse(summary["promotion_eligible"])
            self.assertEqual(summary["regression_gate"]["status"], "not_run")
            self.assertEqual(
                json.loads((answer_dir / "evaluation_audit.jsonl").read_text(encoding="utf-8"))["gold_answer"],
                "gold from reference",
            )
            self.assertEqual(answer_before, answer_path.read_bytes())
            self.assertEqual(judge_before, judge_path.read_bytes())

    def test_judge_resume_requires_exact_frozen_answer_prefix(self):
        answers = [
            {
                "question_id": "q2",
                "question": "second",
                "hypothesis": "answer 2",
                "evidence_sha256": "e2",
                "answer_model": "gpt-5.4",
            },
            {
                "question_id": "q1",
                "question": "first",
                "hypothesis": "answer 1",
                "evidence_sha256": "e1",
                "answer_model": "gpt-5.4",
            },
        ]
        _validate_judge_resume_rows(
            answers,
            [{**answers[0], "autoeval_label": {"label": True}}],
        )
        with self.assertRaisesRegex(EvaluationError, "frozen answer prefix"):
            _validate_judge_resume_rows(
                answers,
                [{**answers[1], "autoeval_label": {"label": True}}],
            )
        changed = {**answers[0], "hypothesis": "different"}
        with self.assertRaisesRegex(EvaluationError, "different answer evidence"):
            _validate_judge_resume_rows(answers, [changed])


if __name__ == "__main__":
    unittest.main()
