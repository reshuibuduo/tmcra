import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import run_tmcra_v4_compile_evidence as compiler
from run_tmcra_v4_compile_evidence import (
    EvidenceCompileError,
    _physical_calls,
    _recover_plan_from_failure,
    _replan_context_from_failure,
    _refresh_completed_artifact,
    _review_context_for_plan,
    _retrieval_debug_report,
    _stage_retrieval_debug,
    _artifact_binding,
    _identity,
)
from test_tmcra_v4_evidence_operations import row
from tmcra_v4_evidence_operations import (
    PACKET_COMPILER_VERSION,
    PLAN_SCHEMA,
    build_evidence_catalog,
    compile_evidence_packet,
)
from tmcra_v4_evidence_planner import PROMPT_VERSION
from tmcra_v4_route_policy import RETRIEVAL_CONTRACT_SCHEMA


class EvidenceCompilerTests(unittest.TestCase):
    @staticmethod
    def route_contract(lane: str) -> dict:
        return {
            "schema_version": RETRIEVAL_CONTRACT_SCHEMA,
            "execution_lane": lane,
            "composition_mode": "layered" if lane == "production" else "source-only-diagnostic",
            "inventory_counts": {
                "source": 2,
                "fast": 0,
                "fast_semantic": 0,
                "slow": 0,
                "slow_capsule_heads": 0,
                "slow_summaries": 0,
                "slow_claims": 0,
                "slow_ranked_claims": 0,
            },
            "candidate_paths_executed": {"source": True, "fast": False, "slow": False},
            "required_selected_layers": ["source"],
            "selected_layer_window_counts": {"source": 2, "fast": 0, "slow": 0},
            "packing_budget_mode": "fixed",
            "packing_budget": 8,
            "source_coverage_trace_k": 24,
            "final_window_count": 2,
        }

    def test_main_rejects_uncontracted_retrieval_before_loading_api_keys(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence.jsonl"
            evidence.write_text(json.dumps(row()) + "\n", encoding="utf-8")
            out_dir = root / "compiled"
            argv = [
                "run_tmcra_v4_compile_evidence.py",
                "--evidence",
                str(evidence),
                "--out-dir",
                str(out_dir),
            ]
            with mock.patch("sys.argv", argv), mock.patch.object(
                compiler, "_load_shell_environment"
            ) as load_environment:
                with self.assertRaisesRegex(
                    EvidenceCompileError, "before evidence planner calls"
                ):
                    compiler.main()
            load_environment.assert_not_called()
            self.assertFalse(out_dir.exists())

    def test_retrieval_debug_is_staged_byte_exact_with_frozen_qid_order(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "retrieval_debug.jsonl"
            raw = (
                '{"question_id":"q1","source_top24_session_ids":["s1"]}\n'
                '{"question_id":"q2","source_top24_session_ids":["s2"]}\n'
            ).encode("utf-8")
            source.write_bytes(raw)
            report = _retrieval_debug_report(source, ["q1", "q2"])
            out_dir = root / "compiled"
            out_dir.mkdir()
            staged = _stage_retrieval_debug(
                source,
                out_dir,
                ["q1", "q2"],
                expected_sha256=report["source_sha256"],
            )
            self.assertEqual((out_dir / "retrieval_debug.jsonl").read_bytes(), raw)
            self.assertEqual(staged["status"], "staged")
            self.assertEqual(staged["row_count"], 2)
            self.assertEqual(staged["artifact_sha256"], report["source_sha256"])

    def test_retrieval_debug_rejects_qid_order_drift(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "retrieval_debug.jsonl"
            source.write_text(
                '{"question_id":"q2"}\n{"question_id":"q1"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                EvidenceCompileError,
                "frozen evidence qid order",
            ):
                _retrieval_debug_report(source, ["q1", "q2"])

    def test_refreshes_stale_completed_packet_locally_from_persisted_plan(self):
        value = row()
        value["retrieval_contract"] = self.route_contract("production")
        plan = {
            "schema_version": PLAN_SCHEMA,
            "requirements": [
                {"requirement_id": "R1", "description": "fact", "evidence_ids": ["E01"]}
            ],
            "operations": [],
            "bundles": [],
        }
        packet = compile_evidence_packet(value, plan)
        packet.pop("packet_compiler_version")
        saved = {
            "question_id": "q1",
            "input_sha256": _identity(value),
            "status": "completed",
            "planner": {
                "physical_call_id": "call-1",
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "prompt_version": PROMPT_VERSION,
                "review_policy_version": compiler.REVIEW_POLICY_VERSION,
            },
            "row": {**value, "compiled_evidence_packet": packet},
        }
        refreshed, changed = _refresh_completed_artifact(
            saved,
            value,
            question_id="q1",
            input_sha256=_identity(value),
        )
        self.assertTrue(changed)
        self.assertEqual(
            refreshed["row"]["compiled_evidence_packet"]["packet_compiler_version"],
            PACKET_COMPILER_VERSION,
        )
        self.assertEqual(refreshed["planner"], saved["planner"])
        unchanged, changed_again = _refresh_completed_artifact(
            refreshed,
            value,
            question_id="q1",
            input_sha256=_identity(value),
        )
        self.assertFalse(changed_again)
        self.assertEqual(unchanged, refreshed)
        stale = {**refreshed, "planner": {"prompt_version": "stale"}}
        with self.assertRaisesRegex(EvidenceCompileError, "stale evidence planner"):
            _refresh_completed_artifact(
                stale,
                value,
                question_id="q1",
                input_sha256=_identity(value),
            )

    def test_legacy_artifact_can_receive_packet_only_local_recompile(self):
        value = row()
        value["retrieval_contract"] = self.route_contract("production")
        plan = {
            "schema_version": PLAN_SCHEMA,
            "requirements": [],
            "operations": [],
            "bundles": [],
        }
        packet = compile_evidence_packet(value, plan)
        packet.pop("packet_compiler_version")
        saved = {
            "question_id": "q1",
            "input_sha256": "legacy-pre-binding-identity",
            "status": "completed",
            "planner": {
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "prompt_version": PROMPT_VERSION,
                "review_policy_version": compiler.REVIEW_POLICY_VERSION,
            },
            "row": {**value, "compiled_evidence_packet": packet},
        }
        refreshed, changed = _refresh_completed_artifact(
            saved,
            value,
            question_id="q1",
            input_sha256=_identity(value),
            expected_binding=_artifact_binding(
                value,
                provider="deepseek",
                model="deepseek-v4-pro",
            ),
        )
        self.assertTrue(changed)
        self.assertEqual(
            refreshed["row"]["compiled_evidence_packet"]["packet_compiler_version"],
            PACKET_COMPILER_VERSION,
        )

    def test_production_artifact_cannot_be_reused_for_diagnostic_input(self):
        production = row()
        production["retrieval_contract"] = self.route_contract("production")
        diagnostic = {**production, "retrieval_contract": self.route_contract("diagnostic")}
        plan = {
            "schema_version": PLAN_SCHEMA,
            "requirements": [],
            "operations": [],
            "bundles": [],
        }
        saved = {
            "question_id": "q1",
            "input_sha256": _identity(production),
            "artifact_binding": _artifact_binding(
                production,
                provider="deepseek",
                model="deepseek-v4-pro",
            ),
            "planner": {
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "prompt_version": PROMPT_VERSION,
            },
            "row": {
                **production,
                "compiled_evidence_packet": compile_evidence_packet(production, plan),
            },
        }
        with self.assertRaisesRegex(EvidenceCompileError, "identity mismatch|route contract mismatch"):
            _refresh_completed_artifact(
                saved,
                diagnostic,
                question_id="q1",
                input_sha256=_identity(diagnostic),
                expected_binding=_artifact_binding(
                    diagnostic,
                    provider="deepseek",
                    model="deepseek-v4-pro",
                ),
            )

    def test_planner_provider_or_model_drift_cannot_reuse_current_artifact(self):
        value = row()
        value["retrieval_contract"] = self.route_contract("production")
        plan = {
            "schema_version": PLAN_SCHEMA,
            "requirements": [],
            "operations": [],
            "bundles": [],
        }
        saved = {
            "question_id": "q1",
            "input_sha256": _identity(value),
            "artifact_binding": _artifact_binding(
                value,
                provider="deepseek",
                model="deepseek-v4-pro",
            ),
            "planner": {
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "prompt_version": PROMPT_VERSION,
            },
            "row": {**value, "compiled_evidence_packet": compile_evidence_packet(value, plan)},
        }
        with self.assertRaisesRegex(EvidenceCompileError, "planner contract/provider/model mismatch"):
            _refresh_completed_artifact(
                saved,
                value,
                question_id="q1",
                input_sha256=_identity(value),
                expected_binding=_artifact_binding(
                    value,
                    provider="xiaomi_mimo",
                    model="mimo-v2.5",
                ),
            )

    def test_review_policy_drift_cannot_reuse_current_artifact(self):
        value = row()
        value["retrieval_contract"] = self.route_contract("production")
        plan = {
            "schema_version": PLAN_SCHEMA,
            "requirements": [],
            "operations": [],
            "bundles": [],
        }
        saved_binding = _artifact_binding(
            value,
            provider="deepseek",
            model="deepseek-v4-pro",
        )
        saved_binding["planner"]["review_policy_version"] = "stale"
        saved = {
            "question_id": "q1",
            "input_sha256": _identity(value),
            "artifact_binding": saved_binding,
            "planner": {
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "prompt_version": PROMPT_VERSION,
                "review_policy_version": "stale",
            },
            "row": {
                **value,
                "compiled_evidence_packet": compile_evidence_packet(value, plan),
            },
        }

        with self.assertRaisesRegex(
            EvidenceCompileError,
            "planner contract/provider/model mismatch",
        ):
            _refresh_completed_artifact(
                saved,
                value,
                question_id="q1",
                input_sha256=_identity(value),
                expected_binding=_artifact_binding(
                    value,
                    provider="deepseek",
                    model="deepseek-v4-pro",
                ),
            )

    def test_physical_call_accounting_deduplicates_nested_history(self):
        call = {
            "physical_api_call": True,
            "physical_call_id": "call-1",
            "provider": "deepseek",
            "usage": {"prompt_tokens": 3},
        }
        self.assertEqual(
            [item["physical_call_id"] for item in _physical_calls([call, {"calls": [call]}])],
            ["call-1"],
        )

    def test_recovers_persisted_raw_plan_without_a_new_model_call(self):
        catalog = build_evidence_catalog(row())
        raw_plan = {
            "schema_version": PLAN_SCHEMA,
            "requirements": [
                {"requirement_id": "R1", "description": "fact", "evidence_ids": ["E01"]}
            ],
            "operations": [],
            "bundles": [],
            "task_contract": {
                "schema_version": "tmcra.task-contract.v4",
                "output_origin": "memory_direct",
                "target": {
                    "subject": "remembered fact",
                    "relation": "value",
                    "entity_constraints": ["fact"],
                    "temporal_constraints": [],
                    "state_constraints": [],
                },
                "output": {"shape": "scalar", "cardinality": "one", "order": "none"},
                "premises": [
                    {
                        "premise_id": "R1",
                        "description": "fact",
                        "role": "fact",
                        "necessity": "required",
                        "source": "memory",
                        "grounded_constraints": ["fact"],
                        "context_quote": "",
                    }
                ],
                "operations": [],
            },
            "typed_semantics": {"observations": [], "proposals": []},
        }
        with TemporaryDirectory() as directory:
            failure = Path(directory) / "failure.json"
            failure.write_text(
                json.dumps(
                    {
                        "question_id": "q1",
                        "input_sha256": "input-1",
                        "planner": {
                            "raw_plan": raw_plan,
                            "physical_api_calls": 1,
                            "prompt_version": PROMPT_VERSION,
                            "review_policy_version": compiler.REVIEW_POLICY_VERSION,
                        },
                    }
                ),
                encoding="utf-8",
            )
            plan, metadata = _recover_plan_from_failure(
                failure,
                question_id="q1",
                input_sha256="input-1",
                catalog=catalog,
            )
            self.assertEqual(plan["requirements"][0]["evidence_ids"], ["E01"])
            self.assertTrue(metadata["recovered_from_persisted_raw_plan"])
            persisted = json.loads(failure.read_text(encoding="utf-8"))
            persisted["planner"]["prompt_version"] = "stale"
            failure.write_text(json.dumps(persisted), encoding="utf-8")
            self.assertIsNone(
                _recover_plan_from_failure(
                    failure,
                    question_id="q1",
                    input_sha256="input-1",
                    catalog=catalog,
                )
            )
            with self.assertRaisesRegex(EvidenceCompileError, "identity mismatch"):
                _recover_plan_from_failure(
                    failure,
                    question_id="q1",
                    input_sha256="different",
                    catalog=catalog,
                )

    def test_invalid_persisted_raw_plan_is_replanned(self):
        catalog = build_evidence_catalog(row())
        with TemporaryDirectory() as directory:
            failure = Path(directory) / "failure.json"
            failure.write_text(
                json.dumps(
                    {
                        "question_id": "q1",
                        "input_sha256": "input-1",
                        "planner": {
                            "raw_plan": {},
                            "prompt_version": PROMPT_VERSION,
                        },
                        "error": "memory output origin needs a required memory premise",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(
                compiler,
                "normalize_planner_output",
                side_effect=ValueError("invalid persisted plan"),
            ):
                self.assertIsNone(
                    _recover_plan_from_failure(
                        failure,
                        question_id="q1",
                        input_sha256="input-1",
                        catalog=catalog,
                    )
                )
            context = _replan_context_from_failure(
                failure,
                question_id="q1",
                input_sha256="input-1",
                catalog=catalog,
            )
            self.assertEqual(
                context["review_reasons"],
                ["persisted_plan_validation_failure"],
            )
            self.assertIn("required memory", context["instruction"])
            self.assertIn("owned tools", context["instruction"])
            self.assertIn("target domain", context["instruction"])
            self.assertEqual(
                context["candidate_memory_evidence_ids"],
                catalog["lexical_anchor_ids"],
            )

    def test_review_is_not_called_for_complete_legacy_plan(self):
        plan = {
            "requirements": [
                {"requirement_id": "R1", "description": "fact", "evidence_ids": ["E01"]}
            ]
        }
        self.assertIsNone(_review_context_for_plan(plan))

    def test_missing_requirement_is_reviewed_as_hypothesis(self):
        plan = {
            "requirements": [
                {"requirement_id": "R1", "description": "fact", "evidence_ids": []}
            ]
        }
        context = _review_context_for_plan(plan)
        self.assertIn("unbound_required_memory_premises", context["review_reasons"])
        self.assertIn("hypothesis", context["instruction"])
        self.assertEqual(context["missing_requirement_ids"], ["R1"])

    def test_empty_query_context_binding_does_not_trigger_missing_review(self):
        plan = {
            "requirements": [
                {"requirement_id": "R1", "description": "question date", "evidence_ids": []},
                {"requirement_id": "R2", "description": "fact", "evidence_ids": ["E01"]},
            ],
            "task_contract": {
                "output_origin": "memory_direct",
                "target": {
                    "subject": "remembered fact",
                    "relation": "value",
                    "entity_constraints": ["fact"],
                    "temporal_constraints": [],
                    "state_constraints": [],
                },
                "output": {"shape": "scalar", "cardinality": "one", "order": "none"},
                "premises": [
                    {
                        "premise_id": "R1",
                        "description": "question date",
                        "role": "operand",
                        "necessity": "required",
                        "source": "query_context",
                        "grounded_constraints": [],
                        "context_quote": "",
                    },
                    {
                        "premise_id": "R2",
                        "description": "fact",
                        "role": "fact",
                        "necessity": "required",
                        "source": "memory",
                        "grounded_constraints": ["fact"],
                        "context_quote": "",
                    },
                ],
                "operations": [],
            },
        }
        self.assertIsNone(_review_context_for_plan(plan))

    def test_structural_contract_risk_triggers_review_without_missing_binding(self):
        plan = {
            "requirements": [
                {"requirement_id": "R1", "description": "state", "evidence_ids": ["E01"]}
            ],
            "task_contract": {
                "output_origin": "memory_direct",
                "target": {
                    "subject": "account",
                    "relation": "current state after state history changed",
                    "entity_constraints": ["account"],
                    "temporal_constraints": [],
                    "state_constraints": ["old", "current"],
                },
                "output": {"shape": "scalar", "cardinality": "one", "order": "none"},
                "premises": [
                    {
                        "premise_id": "R1",
                        "description": "state",
                        "role": "state",
                        "necessity": "required",
                        "source": "memory",
                        "grounded_constraints": ["state changed"],
                        "context_quote": "",
                    }
                ],
                "operations": [],
            },
        }
        context = _review_context_for_plan(plan)
        self.assertIn("multi_state_without_latest", context["review_reasons"])

    def test_unrelated_required_memory_binding_triggers_semantic_review(self):
        catalog = {
            "question": "Any tips for improving my phone battery life?",
            "lexical_anchor_ids": ["E02"],
            "evidence": [
                {"evidence_id": "E01", "text": "I prefer a navy striped tie."},
                {
                    "evidence_id": "E02",
                    "text": "I bought a portable power bank for my phone.",
                },
            ],
        }
        plan = {
            "requirements": [
                {
                    "requirement_id": "R1",
                    "description": "remembered user context",
                    "evidence_ids": ["E01"],
                }
            ],
            "task_contract": {
                "premises": [
                    {
                        "premise_id": "R1",
                        "source": "memory",
                        "necessity": "required",
                    }
                ]
            },
        }
        context = _review_context_for_plan(plan, catalog)
        self.assertIn(
            "required_memory_premise_outside_query_anchors",
            context["review_reasons"],
        )
        self.assertEqual(
            context["memory_premise_relevance_signal"]["bound_evidence_ids"],
            ["E01"],
        )
        self.assertEqual(
            context["hard_constraints"][
                "forbidden_required_memory_evidence_ids"
            ],
            ["E01"],
        )
        self.assertIn("materially constrains", context["instruction"])
        self.assertIn(
            "kept the same out-of-anchor evidence binding",
            compiler._review_resolution_failure(plan, catalog, context),
        )

        corrected = {
            **plan,
            "requirements": [
                {
                    "requirement_id": "R1",
                    "description": "remembered user context",
                    "evidence_ids": ["E02"],
                }
            ],
        }
        self.assertEqual(
            compiler._review_resolution_failure(corrected, catalog, context),
            "",
        )
        self.assertIsNone(_review_context_for_plan(corrected, catalog))

    def test_review_resolution_failure_recovers_initial_plan_for_retry(self):
        catalog = build_evidence_catalog(row())
        initial_plan = {"requirements": [{"requirement_id": "R1"}]}
        with TemporaryDirectory() as directory:
            failure = Path(directory) / "failure.json"
            failure.write_text(
                json.dumps(
                    {
                        "question_id": "q1",
                        "input_sha256": "input-1",
                        "planner": {
                            "provider": "deepseek",
                            "model": "deepseek-v4-pro",
                            "prompt_version": PROMPT_VERSION,
                            "review_policy_version": compiler.REVIEW_POLICY_VERSION,
                            "review_resolution_failed": True,
                            "review_context": {"initial_plan": initial_plan},
                        },
                    }
                ),
                encoding="utf-8",
            )
            normalized_plan = {"requirements": [{"requirement_id": "R1"}]}
            with mock.patch.object(
                compiler,
                "normalize_planner_output",
                return_value=(normalized_plan, []),
            ) as normalize:
                recovered = _recover_plan_from_failure(
                    failure,
                    question_id="q1",
                    input_sha256="input-1",
                    catalog=catalog,
                )
            normalize.assert_called_once_with(initial_plan, catalog)
            self.assertEqual(recovered[0], normalized_plan)
            self.assertEqual(recovered[1]["raw_plan"], initial_plan)

    def test_relative_event_anchor_is_reviewed_before_defaulting_to_question_date(self):
        value = row()
        value["question"] = (
            "How many days ago did I attend a baking class when I made my "
            "friend's birthday cake?"
        )
        value["question_date"] = "2023-04-29"
        value["evidence_windows"][0].update(
            {
                "session_id": "class-session",
                "timestamp": "2023-04-03T09:00:00+00:00",
                "text": "I attended a baking class yesterday.",
            }
        )
        value["evidence_windows"][1].update(
            {
                "session_id": "cake-session",
                "timestamp": "2023-04-24T09:00:00+00:00",
                "text": "I made my friend's birthday cake today.",
            }
        )
        catalog = build_evidence_catalog(value)
        dates = {
            item["evidence_id"]: item["atom_id"]
            for item in catalog["atoms"]
            if item["atom_type"] == "date"
        }
        plan = {
            "requirements": [
                {
                    "requirement_id": "R1",
                    "description": "class date",
                    "evidence_ids": ["E01"],
                }
            ],
            "operations": [
                {
                    "operation_id": "O1",
                    "operation_type": "date_difference",
                    "input_atom_ids": [dates["E01"], dates["QUESTION"]],
                    "input_evidence_ids": ["E01"],
                    "parameters": {},
                }
            ],
        }
        context = _review_context_for_plan(plan, catalog)
        self.assertIn("relative_event_anchor_not_bound", context["review_reasons"])
        self.assertIn("distinct event anchors", context["instruction"])
        self.assertEqual(
            context["hard_constraints"],
            {
                "forbidden_date_difference_evidence_ids": ["QUESTION"],
                "minimum_distinct_source_event_anchors": 2,
            },
        )
        self.assertIn("QUESTION is forbidden", context["instruction"])
        self.assertIn(
            "still binds QUESTION",
            compiler._review_resolution_failure(plan, catalog, context),
        )

        corrected = {
            **plan,
            "operations": [
                {
                    "operation_id": "O1",
                    "operation_type": "date_difference",
                    "input_atom_ids": [dates["E01"], dates["E02"]],
                    "input_evidence_ids": ["E01", "E02"],
                    "parameters": {},
                }
            ],
        }
        self.assertEqual(
            compiler._review_resolution_failure(corrected, catalog, context),
            "",
        )

    def test_simple_days_ago_question_does_not_trigger_event_anchor_review(self):
        value = row()
        value["question"] = "How many days ago did I attend the class?"
        value["question_date"] = "2023-04-29"
        catalog = build_evidence_catalog(value)
        dates = {
            item["evidence_id"]: item["atom_id"]
            for item in catalog["atoms"]
            if item["atom_type"] == "date"
        }
        plan = {
            "requirements": [
                {
                    "requirement_id": "R1",
                    "description": "class date",
                    "evidence_ids": ["E01"],
                }
            ],
            "operations": [
                {
                    "operation_id": "O1",
                    "operation_type": "date_difference",
                    "input_atom_ids": [dates["E01"], dates["QUESTION"]],
                    "input_evidence_ids": ["E01"],
                    "parameters": {},
                }
            ],
        }
        self.assertIsNone(_review_context_for_plan(plan, catalog))


if __name__ == "__main__":
    unittest.main()
