import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import run_tmcra_v4_gpt54_answers as answers
import tmcra_v4_evidence_operations as operations
from test_tmcra_v4_evidence_operations import row
from test_tmcra_v4_semantic_evidence import contract as semantic_contract
from test_tmcra_v4_semantic_evidence import resolution as semantic_resolution
from test_tmcra_v4_semantic_evidence import row as semantic_row
from tmcra_v4_semantic_evidence import (
    SEMANTIC_ANSWER_SCHEMA,
    compile_semantic_evidence_packet,
    semantic_packet_to_advisory_packet,
)


def packet():
    catalog = operations.build_evidence_catalog(row())
    dates = [item["atom_id"] for item in catalog["atoms"] if item["atom_type"] == "date" and item["evidence_id"] != "QUESTION"]
    plan = {
        "schema_version": operations.PLAN_SCHEMA,
        "requirements": [{"requirement_id": "R1", "description": "elapsed days", "evidence_ids": ["E01", "E02"]}],
        "operations": [{"operation_id": "O1", "operation_type": "date_difference", "input_atom_ids": dates, "input_evidence_ids": ["E01", "E02"], "parameters": {}}],
        "bundles": [{"bundle_id": "B1", "role": "temporal_sequence", "evidence_ids": ["E01", "E02"]}],
    }
    return operations.compile_evidence_packet(row(), plan)


def certified_packet():
    value = row()
    value["question"] = "Did the first item occur 21 days ago?"
    catalog = operations.build_evidence_catalog(value)
    dates = [
        item["atom_id"]
        for item in catalog["atoms"]
        if item["atom_type"] == "date" and item["evidence_id"] != "QUESTION"
    ]
    plan = {
        "schema_version": operations.PLAN_SCHEMA,
        "requirements": [
            {
                "requirement_id": "R1",
                "description": "elapsed days",
                "evidence_ids": ["E01", "E02"],
            }
        ],
        "operations": [
            {
                "operation_id": "O1",
                "operation_type": "date_difference",
                "input_atom_ids": dates,
                "input_evidence_ids": ["E01", "E02"],
                "parameters": {},
            }
        ],
        "bundles": [
            {
                "bundle_id": "B1",
                "role": "temporal_sequence",
                "evidence_ids": ["E01", "E02"],
            }
        ],
    }
    return operations.compile_evidence_packet(value, plan)


class V4OperationAnswerTests(unittest.TestCase):
    def test_fixed_answer_environment_forbids_hidden_http_retries(self):
        environment = answers.fixed_answer_environment(8)
        self.assertEqual(environment["TMCRA_HTTP_JSON_MAX_ATTEMPTS"], "1")
        self.assertEqual(environment["TMCRA_ANSWER_EVIDENCE_WINDOW_LIMIT"], "8")

    def test_production_main_rejects_uncontracted_rows_before_loading_harness(self):
        value = row()
        value["compiled_evidence_packet"] = packet()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence.jsonl"
            evidence.write_text(json.dumps(value) + "\n", encoding="utf-8")
            argv = [
                "run_tmcra_v4_gpt54_answers.py",
                "--evidence",
                str(evidence),
                "--harness",
                str(root / "harness.py"),
                "--out-dir",
                str(root / "answers"),
                "--lane",
                "production",
            ]
            environment = {
                "TMCRA_ANSWER_MODEL": "gpt-5.4",
                "TMCRA_ANSWER_BASE_URL": "https://answer.invalid/v1",
                "TMCRA_ANSWER_API_KEY": "test-key",
            }
            with mock.patch("sys.argv", argv), mock.patch.dict(
                "os.environ", environment, clear=False
            ), mock.patch.object(answers, "load_harness") as load_harness:
                with self.assertRaisesRegex(
                    RuntimeError, "before GPT-5.4 calls"
                ):
                    answers.main()
            load_harness.assert_not_called()
            self.assertFalse((root / "answers").exists())

    def test_renderer_exposes_neutral_answer_intent_without_planner_verdicts(self):
        compiled = packet()
        task_contract = {
            "schema_version": "tmcra.task-contract.v4",
            "output_origin": "memory_conditioned_generation",
            "target": {"subject": "user", "relation": "recommendation"},
        }
        compiled["question_contract"]["task_contract"] = task_contract
        compiled["task_contract"] = task_contract
        compiled["structural_risk_signals"] = ["PLANNER_MISSING_WITH_PLAUSIBLE_SOURCE"]
        compiled["typed_semantics_report"] = {
            "status": "evaluated",
            "advisory": True,
            "authoritative": False,
            "accepted": [{"candidate_id": "C1"}],
            "rejected": [],
        }

        rendered = answers.render_compiled_packet(compiled)

        self.assertEqual(rendered["task_intent"]["output_origin"], "memory_conditioned_generation")
        self.assertNotIn("task_contract", rendered["question_contract"])
        self.assertNotIn("structural_risk_signals", rendered)
        self.assertNotIn("typed_semantics_report", rendered)

    def test_renderer_remains_compatible_with_packets_without_optional_metadata(self):
        compiled = packet()
        compiled.pop("task_contract", None)
        compiled.pop("structural_risk_signals", None)
        compiled.pop("typed_semantics_report", None)
        rendered = answers.render_compiled_packet(compiled)
        self.assertEqual(rendered["task_intent"]["output_origin"], "")
        self.assertNotIn("structural_risk_signals", rendered)
        self.assertNotIn("typed_semantics_report", rendered)

    def test_prompt_requires_task_contract_state_and_source_order_review(self):
        prompt = answers.EVIDENCE_OPERATION_ANSWER_PROMPT
        for required_text in (
            "task_intent.output_origin",
            "memory_conditioned_generation",
            "user_memory_cues",
            "a ready-made historical answer is not required",
            "owned tools",
            "preserve the narrower grounded detail",
            "The final answer itself must explicitly show",
            "Do not declare the task insufficient",
            "Query context never requires Source evidence IDs",
            "query_context claims may have empty binding lists in every task",
            "Only memory_conditioned_generation may use model_knowledge claims",
            "label their origin explicitly",
            "source_group_context",
            "historical_date and timestamp",
            "never against the question date",
            "candidate temporal endpoint",
            "event date or stated relative time",
            "actual, planned, hypothetical, or mentioned",
            "latest update precedence",
            "expose no result",
        ):
            self.assertIn(required_text, prompt)

    def test_renderer_quarantines_uncertified_results_but_keeps_sources(self):
        rendered = answers.render_compiled_packet(packet())
        self.assertEqual(rendered["verified_operation_results"], [])
        self.assertEqual(rendered["planner_operation_candidates"][0]["operation_id"], "O1")
        self.assertNotIn("result", rendered["planner_operation_candidates"][0])
        self.assertEqual(rendered["planner_source_groups"][0]["evidence_ids"], ["E01", "E02"])
        self.assertEqual(rendered["source_evidence"][0]["text"], row()["evidence_windows"][0]["text"])

    def test_renderer_exposes_slow_claim_and_fast_override_with_source(self):
        compiled = packet()
        source = compiled["raw_evidence_reservoir"][0]
        source["memory_contexts"] = [
            {
                "role": "slow_context",
                "capsule_id": "cap1",
                "claim_id": "clm1",
                "canonical_slot": "user.preference.hotel",
                "claim_text": "The user prefers rooftop pools.",
                "provenance": {"memory_layer": "slow"},
            }
        ]
        source["attachments"] = [
            {
                "role": "override",
                "memory_id": "fast1",
                "canonical_slot": "user.preference.hotel",
                "text": "The user now prefers a balcony hot tub.",
                "precedence": "newer_fast_evidence",
                "provenance": {"memory_layer": "fast"},
            }
        ]
        source["provenance"] = [{"memory_layer": "slow", "claim_id": "clm1"}]
        source["historical_date"] = "2023/04/03 (Mon) 09:00"
        source["timestamp"] = "2023-04-03T09:00:00+00:00"
        source["message_role"] = "user"
        rendered = answers.render_compiled_packet(compiled)["source_evidence"][0]
        self.assertEqual(rendered["memory_contexts"], source["memory_contexts"])
        self.assertEqual(rendered["attachments"], source["attachments"])
        self.assertEqual(rendered["provenance"], source["provenance"])
        self.assertEqual(rendered["historical_date"], source["historical_date"])
        self.assertEqual(rendered["timestamp"], source["timestamp"])
        self.assertEqual(rendered["message_role"], "user")
        self.assertNotIn("retrieval_metadata", rendered)

    def test_renderer_indexes_slow_fast_and_user_authored_memory_cues(self):
        compiled = packet()
        first, second = compiled["raw_evidence_reservoir"][:2]
        first["message_role"] = "assistant"
        first["memory_contexts"] = [
            {
                "claim_text": "The user prefers a rooftop pool.",
                "canonical_slot": "user.preference.hotel",
                "role": "slow_context",
            }
        ]
        first["attachments"] = [
            {
                "role": "override",
                "text": "The user now prefers a balcony hot tub.",
            }
        ]
        first["source_group_context"] = [
            {
                "message_role": "user",
                "source_record_id": "source.user.powerbank",
                "text": "I already own a portable power bank.",
            },
            {
                "message_role": "assistant",
                "source_record_id": "source.assistant.generic",
                "text": "Here are generic battery tips.",
            },
        ]
        second["message_role"] = "user"
        second["text"] = "I want to branch out into history podcasts."

        rendered = answers.render_compiled_packet(compiled)
        cues = rendered["user_memory_cues"]
        cue_texts = [item["text"] for item in cues]

        self.assertEqual(rendered["answer_view_schema"], "tmcra.v4.answer-evidence-view.5")
        self.assertEqual(cues[0]["kind"], "slow_claim")
        self.assertEqual(cues[1]["kind"], "user_source")
        self.assertIn("I already own a portable power bank.", cue_texts)
        self.assertIn("The user now prefers a balcony hot tub.", cue_texts)
        self.assertNotIn("Here are generic battery tips.", cue_texts)
        self.assertTrue(
            all(item["evidence_id"] in {first["evidence_id"], second["evidence_id"]} for item in cues)
        )

    def test_renderer_preserves_frozen_retrieval_order_and_source_group_context(self):
        compiled = packet()
        first, second = compiled["raw_evidence_reservoir"][:2]
        first["session_index"], first["parent_chunk_index"] = 5, 1
        second["session_index"], second["parent_chunk_index"] = 2, 3
        first["question_overlap_score"] = 99
        first["source_group_context"] = [
            {
                "relationship": "session_neighbor",
                "session_id": first["session_id"],
                "session_index": 5,
                "parent_chunk_index": 0,
                "source_record_id": "neighbor",
                "text": "nearby source",
            }
        ]
        rendered = answers.render_compiled_packet(compiled)
        self.assertEqual(rendered["source_evidence"][0]["evidence_id"], first["evidence_id"])
        self.assertEqual(rendered["source_evidence"][0]["source_order"], 1)
        rendered_first = next(
            item
            for item in rendered["source_evidence"]
            if item["evidence_id"] == first["evidence_id"]
        )
        self.assertEqual(
            rendered_first["source_group_context"][0]["text"], "nearby source"
        )

    def test_renderer_does_not_expose_planner_missing_state(self):
        compiled = packet()
        compiled["requirement_coverage"][0]["state"] = "missing"
        compiled["requirement_coverage"][0]["evidence_ids"] = []
        rendered = answers.render_compiled_packet(compiled)
        encoded = json.dumps(rendered, sort_keys=True)
        self.assertNotIn('"state": "missing"', encoded)
        self.assertNotIn("candidate_source_ids", rendered["requirement_catalog"][0])

    def test_parser_requires_valid_claim_and_computation_ids(self):
        value = {
            "schema_version": operations.ANSWER_SCHEMA,
            "answerability": "sufficient",
            "claims": [{"claim_id": "C1", "text": "It was 21 days.", "support_ids": ["E01", "E02"], "computation_ids": ["O1"]}],
            "missing_requirements": [],
            "answer": "21 days.",
        }
        self.assertEqual(answers.parse_operation_bound_answer(json.dumps(value), certified_packet())["answer"], "21 days.")
        value["claims"][0]["computation_ids"] = ["O99"]
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            answers.parse_operation_bound_answer(json.dumps(value), certified_packet())

    def test_parser_tolerates_harmless_extra_fields(self):
        value = {
            "schema_version": operations.ANSWER_SCHEMA,
            "answerability": "sufficient",
            "claims": [
                {
                    "claim_id": "C1",
                    "text": "It was 21 days.",
                    "support_ids": ["E01", "E02"],
                    "computation_ids": ["O1"],
                    "role": "calculated_result",
                }
            ],
            "missing_requirements": [],
            "answer": "21 days.",
            "confidence": "high",
        }
        parsed = answers.parse_operation_bound_answer(json.dumps(value), certified_packet())
        self.assertNotIn("confidence", parsed)
        self.assertNotIn("role", parsed["claims"][0])

    def test_semantic_renderer_exposes_bound_premises_not_the_unbound_reservoir(self):
        semantic_packet = compile_semantic_evidence_packet(
            semantic_row(), semantic_contract(), semantic_resolution()
        )
        rendered = answers.render_semantic_packet(semantic_packet)
        self.assertEqual(len(rendered["resolved_premises"]), 1)
        self.assertEqual(
            rendered["resolved_premises"][0]["source_claims"][0]["claim_id"],
            "M01",
        )
        self.assertEqual(
            [item["evidence_id"] for item in rendered["resolved_premises"][0]["source_evidence"]],
            ["E01"],
        )
        self.assertNotIn("raw_evidence_reservoir", rendered)

    def test_semantic_parser_enforces_certificate_and_premise_bindings(self):
        semantic_packet = compile_semantic_evidence_packet(
            semantic_row(), semantic_contract(), semantic_resolution()
        )
        value = {
            "schema_version": SEMANTIC_ANSWER_SCHEMA,
            "response_status": "answered_with_context",
            "claims": [
                {
                    "claim_id": "C01",
                    "text": "You liked an ocean view.",
                    "origin": "memory_fact",
                    "premise_ids": ["P01"],
                    "source_claim_ids": ["M01"],
                    "computation_ids": [],
                }
            ],
            "unresolved_premise_ids": [],
            "answer": "Look for a hotel with an ocean view.",
        }
        parsed = answers.parse_semantic_bound_answer(json.dumps(value), semantic_packet)
        self.assertEqual(parsed["claims"][0]["support_ids"], ["E01"])
        value["response_status"] = "not_answerable"
        with self.assertRaisesRegex(RuntimeError, "certificate"):
            answers.parse_semantic_bound_answer(json.dumps(value), semantic_packet)

    def test_semantic_advisory_keeps_full_source_and_does_not_enforce_certificate(self):
        semantic_packet = compile_semantic_evidence_packet(
            semantic_row(), semantic_contract(), semantic_resolution("absent")
        )
        advisory = semantic_packet_to_advisory_packet(semantic_packet)
        rendered = answers.render_compiled_packet(advisory)
        self.assertEqual(
            [item["text"] for item in rendered["source_evidence"]],
            [item["text"] for item in semantic_packet["raw_evidence_reservoir"]],
        )
        self.assertEqual(rendered["semantic_advisory"]["authority"], "advisory_not_answerability")
        self.assertEqual(rendered["verified_operation_results"], [])
        sufficient = {
            "schema_version": operations.ANSWER_SCHEMA,
            "answerability": "sufficient",
            "claims": [
                {
                    "claim_id": "C01",
                    "text": "The source is still available.",
                    "support_ids": ["E01"],
                    "computation_ids": [],
                }
            ],
            "missing_requirements": [],
            "answer": "The source is still available.",
        }
        parsed = answers.parse_operation_bound_answer(json.dumps(sufficient), advisory)
        self.assertEqual(parsed["answerability"], "sufficient")

    def test_semantic_advisory_exposes_only_valid_typed_results(self):
        semantic_packet = compile_semantic_evidence_packet(
            semantic_row(), semantic_contract(), semantic_resolution()
        )
        semantic_packet["typed_semantics"] = {
            "observations": [
                {
                    "observation_id": "price_a",
                    "evidence_ids": ["E01"],
                    "entity_key": "hotel_budget",
                    "value_kind": "scalar",
                    "value": 20,
                    "unit": "USD",
                    "temporal_kind": "none",
                    "polarity": "positive",
                },
                {
                    "observation_id": "price_b",
                    "evidence_ids": ["E02"],
                    "entity_key": "hotel_budget",
                    "value_kind": "delta",
                    "value": 5,
                    "unit": "USD",
                    "temporal_kind": "none",
                    "polarity": "positive",
                },
            ],
            "proposals": [
                {
                    "candidate_id": "budget_total",
                    "operations": [
                        {
                            "operation_id": "sum",
                            "operation": "numeric_sum",
                            "input_ids": ["price_a", "price_b"],
                        }
                    ],
                }
            ],
        }
        advisory = semantic_packet_to_advisory_packet(semantic_packet)
        self.assertEqual(advisory["operation_result_ids"], ["TS_budget_total"])
        self.assertEqual(advisory["operation_results"][0]["result"]["value"], 25)
        self.assertFalse(advisory["operation_results"][0]["authoritative"])

    def test_semantic_advisory_rejects_typed_results_outside_source_reservoir(self):
        semantic_packet = compile_semantic_evidence_packet(
            semantic_row(), semantic_contract(), semantic_resolution()
        )
        semantic_packet["typed_semantics"] = {
            "observations": [
                {
                    "observation_id": "outside",
                    "evidence_ids": ["E99"],
                    "entity_key": "outside",
                    "value_kind": "entity_instance",
                    "value": "outside",
                    "unit": None,
                    "temporal_kind": "none",
                    "polarity": "positive",
                }
            ],
            "proposals": [
                {
                    "candidate_id": "outside_check",
                    "operations": [
                        {
                            "operation_id": "match",
                            "operation": "entity_exact_match",
                            "input_ids": ["outside", "outside"],
                        }
                    ],
                }
            ],
        }
        advisory = semantic_packet_to_advisory_packet(semantic_packet)
        self.assertEqual(advisory["operation_results"], [])
        rejected = advisory["semantic_advisory"]["typed_semantics"]["rejected"]
        self.assertEqual(rejected[0]["diagnostics"][0]["code"], "UNKNOWN_SOURCE_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
