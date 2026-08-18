import json
import unittest
from unittest import mock

from test_tmcra_v4_evidence_operations import row
from tmcra_v4_evidence_operations import PLAN_SCHEMA, build_evidence_catalog
from tmcra_v4_evidence_planner import (
    DeepSeekEvidenceOperationPlanner,
    PROMPT_VERSION,
    REVIEW_INSTRUCTION,
    SYSTEM_PROMPT,
    _normalize_plan_ids,
    normalize_planner_output,
    planner_payload,
)


class EvidencePlannerTests(unittest.TestCase):
    def test_prompt_describes_contract_typed_semantics_and_memory_conditioning(self):
        self.assertTrue(PROMPT_VERSION.endswith(".8"))
        for text in (
            "task_contract",
            "tmcra.task-contract.v4",
            "premise_id",
            "grounded_constraints",
            "memory_conditioned_generation",
            "no historical recommendation",
            'typed_semantics is exactly {"observations":[],"proposals":[]} ',
            "event_status",
            "actual",
            "planned",
            "hypothetical",
            "mentioned",
            "count_distinct",
            "date_difference",
            "numeric_sum",
            "slow_context",
            "fast_context",
            "precedence=newer_fast_evidence",
        ):
            self.assertIn(text.strip(), SYSTEM_PROMPT)
        self.assertIn("structural risks", REVIEW_INSTRUCTION)
        self.assertIn("historical recommendation", REVIEW_INSTRUCTION)
        self.assertIn("newer Fast overrides", REVIEW_INSTRUCTION)

    def test_normalizer_deduplicates_and_quarantines_unknown_operands(self):
        catalog = build_evidence_catalog(row())
        raw = {
            "schema_version": PLAN_SCHEMA,
            "requirements": [{"requirement_id": "R1", "description": "x", "evidence_ids": ["E01", "E01", "E99"]}],
            "operations": [{"operation_id": "O1", "operation_type": "numeric_sum", "input_atom_ids": ["N999"], "input_evidence_ids": ["E01"], "parameters": {}}],
            "bundles": [{"bundle_id": "B1", "role": "direct", "evidence_ids": ["E01", "E01"]}],
        }
        normalized, warnings = _normalize_plan_ids(raw, catalog)
        self.assertEqual(normalized["requirements"][0]["evidence_ids"], ["E01"])
        self.assertEqual(normalized["operations"], [])
        self.assertEqual(normalized["bundles"][0]["evidence_ids"], ["E01"])
        self.assertTrue(any("quarantined_invalid_operands" in warning for warning in warnings))

    def test_normalizer_quarantines_untyped_legacy_multiply(self):
        catalog = build_evidence_catalog(row())
        numeric = [
            atom["atom_id"]
            for atom in catalog["atoms"]
            if atom["atom_type"] in {"number", "currency", "quantity"}
        ][:2]
        raw = {
            "schema_version": PLAN_SCHEMA,
            "requirements": [],
            "operations": [
                {
                    "operation_id": "O1",
                    "operation_type": "numeric_multiply",
                    "input_atom_ids": numeric,
                    "input_evidence_ids": ["E01", "E02"],
                    "parameters": {},
                }
            ],
            "bundles": [],
        }
        normalized, warnings = _normalize_plan_ids(raw, catalog)
        self.assertEqual(normalized["operations"], [])
        self.assertTrue(
            any("quarantined_unsupported_legacy_operation" in item for item in warnings)
        )

    def test_normalizer_quarantines_typed_unknown_evidence_and_references(self):
        catalog = build_evidence_catalog(row())
        observation = {
            "observation_id": "obs_cost",
            "evidence_ids": ["E01"],
            "entity_key": "item-1",
            "value_kind": "scalar",
            "value": 50,
            "unit": "$",
            "temporal_kind": "none",
            "polarity": "positive",
        }
        invented_observation = {**observation, "observation_id": "obs_invented", "evidence_ids": ["E99"]}
        raw = {
            "schema_version": PLAN_SCHEMA,
            "requirements": [],
            "operations": [],
            "bundles": [],
            "typed_semantics": {
                "observations": [observation, invented_observation],
                "proposals": [
                    {
                        "candidate_id": "candidate_valid",
                        "source_evidence_ids": ["E01"],
                        "operations": [
                            {"operation_id": "typed_sum", "operation": "numeric_sum", "input_ids": ["obs_cost"], "parameters": {}}
                        ],
                    },
                    {
                        "candidate_id": "candidate_bad_ref",
                        "operations": [
                            {"operation_id": "typed_sum_bad", "operation": "numeric_sum", "input_ids": ["obs_invented"], "parameters": {}}
                        ],
                    },
                    {
                        "candidate_id": "candidate_bad_source",
                        "source_evidence_ids": ["E99"],
                        "operations": [
                            {"operation_id": "typed_sum_source_bad", "operation": "numeric_sum", "input_ids": ["obs_cost"], "parameters": {}}
                        ],
                    },
                ],
            },
        }
        normalized, warnings = _normalize_plan_ids(raw, catalog)
        typed = normalized["typed_semantics"]
        self.assertEqual([item["observation_id"] for item in typed["observations"]], ["obs_cost"])
        self.assertEqual([item["candidate_id"] for item in typed["proposals"]], ["candidate_valid"])
        self.assertNotIn("missing_requirements", normalized)
        self.assertTrue(any("quarantined_unknown_evidence_id" in warning for warning in warnings))
        self.assertTrue(any("quarantined_invalid_reference" in warning for warning in warnings))

    def test_normalizer_requires_explicit_status_for_typed_count_inputs(self):
        catalog = build_evidence_catalog(row())
        entity = {
            "observation_id": "entity_1",
            "evidence_ids": ["E01"],
            "entity_key": "item-1",
            "value_kind": "entity_instance",
            "value": "item-1",
            "unit": None,
            "temporal_kind": "none",
            "polarity": "positive",
        }
        raw = {
            "schema_version": PLAN_SCHEMA,
            "requirements": [],
            "operations": [],
            "bundles": [],
            "typed_semantics": {
                "observations": [entity],
                "proposals": [
                    {
                        "candidate_id": "count_without_status",
                        "operations": [
                            {"operation_id": "count", "operation": "count_distinct", "input_ids": ["entity_1"], "parameters": {}}
                        ],
                    }
                ],
            },
        }
        normalized, warnings = _normalize_plan_ids(raw, catalog)
        self.assertEqual(len(normalized["typed_semantics"]["observations"]), 1)
        self.assertEqual(normalized["typed_semantics"]["proposals"], [])
        self.assertTrue(any("quarantined_missing_count_event_status" in warning for warning in warnings))

    def test_normalizer_promotes_only_unique_grounded_bound_memory_premise(self):
        catalog = build_evidence_catalog(row())
        raw = {
            "schema_version": PLAN_SCHEMA,
            "requirements": [
                {
                    "requirement_id": "R1",
                    "description": "current request",
                    "evidence_ids": [],
                },
                {
                    "requirement_id": "R2",
                    "description": "owned charging accessory",
                    "evidence_ids": ["E01"],
                },
            ],
            "operations": [],
            "bundles": [],
            "task_contract": {
                "schema_version": "tmcra.task-contract.v4",
                "output_origin": "memory_conditioned_generation",
                "target": {
                    "subject": "phone battery",
                    "relation": "advice",
                    "entity_constraints": [],
                    "temporal_constraints": [],
                    "state_constraints": [],
                },
                "output": {
                    "shape": "free_text",
                    "cardinality": "one_or_more",
                    "order": "none",
                },
                "premises": [
                    {
                        "premise_id": "R1",
                        "description": "current request",
                        "role": "constraint",
                        "necessity": "required",
                        "source": "query_context",
                        "grounded_constraints": [],
                        "context_quote": "",
                    },
                    {
                        "premise_id": "R2",
                        "description": "owned charging accessory",
                        "role": "constraint",
                        "necessity": "optional",
                        "source": "memory",
                        "grounded_constraints": ["User owns a charging accessory."],
                        "context_quote": "",
                    },
                ],
                "operations": [],
            },
            "typed_semantics": {"observations": [], "proposals": []},
        }
        normalized, warnings = normalize_planner_output(raw, catalog)
        self.assertEqual(
            normalized["task_contract"]["premises"][1]["necessity"],
            "required",
        )
        self.assertIn(
            "task_contract.premises:promoted_unique_grounded_memory_premise",
            warnings,
        )

    def test_normalizer_does_not_guess_between_multiple_optional_memory_premises(self):
        catalog = build_evidence_catalog(row())
        raw = {
            "requirements": [
                {"requirement_id": "R1", "evidence_ids": ["E01"]},
                {"requirement_id": "R2", "evidence_ids": ["E02"]},
            ],
            "task_contract": {
                "output_origin": "memory_conditioned_generation",
                "premises": [
                    {
                        "premise_id": "R1",
                        "source": "memory",
                        "necessity": "optional",
                        "grounded_constraints": ["one"],
                    },
                    {
                        "premise_id": "R2",
                        "source": "memory",
                        "necessity": "optional",
                        "grounded_constraints": ["two"],
                    },
                ],
            },
        }
        normalized, warnings = _normalize_plan_ids(raw, catalog)
        self.assertEqual(
            [p["necessity"] for p in normalized["task_contract"]["premises"]],
            ["optional", "optional"],
        )
        self.assertFalse(any("promoted_unique" in warning for warning in warnings))

    def test_payload_contains_only_runtime_question_evidence_and_atoms(self):
        value = row()
        value["evidence_windows"][0]["memory_contexts"] = [
            {
                "role": "slow_context",
                "capsule_id": "cap1",
                "claim_id": "clm1",
                "claim_text": "The user prefers rooftop pools.",
            }
        ]
        value["evidence_windows"][0]["attachments"] = [
            {
                "role": "override",
                "memory_id": "fast1",
                "text": "The user now prefers a balcony hot tub.",
            }
        ]
        value["evidence_windows"][0]["retrieval_metadata"] = {
            "priority_score": 99
        }
        payload = planner_payload(build_evidence_catalog(value))
        self.assertIn("question", payload)
        self.assertIn("evidence", payload)
        self.assertIn("atoms", payload)
        self.assertNotIn("answer", payload)
        self.assertNotIn("question_type", payload)
        self.assertEqual(
            payload["evidence"][0]["memory_contexts"][0]["claim_id"], "clm1"
        )
        self.assertEqual(
            payload["evidence"][0]["attachments"][0]["memory_id"], "fast1"
        )
        self.assertNotIn("retrieval_metadata", payload["evidence"][0])

    def test_one_valid_response_is_bound_to_catalog(self):
        catalog = build_evidence_catalog(row())
        plan = {
            "schema_version": PLAN_SCHEMA,
            "requirements": [
                {"requirement_id": "R1", "description": "remembered fact", "evidence_ids": ["E01"]}
            ],
            "operations": [],
            "bundles": [],
            "task_contract": {
                "schema_version": "tmcra.task-contract.v4",
                "output_origin": "memory_direct",
                "target": {
                    "subject": "remembered item",
                    "relation": "fact",
                    "entity_constraints": ["item"],
                    "temporal_constraints": [],
                    "state_constraints": [],
                },
                "output": {"shape": "scalar", "cardinality": "one", "order": "none"},
                "premises": [
                    {
                        "premise_id": "R1",
                        "description": "remembered fact",
                        "role": "fact",
                        "necessity": "required",
                        "source": "memory",
                        "grounded_constraints": ["the source states the remembered fact"],
                        "context_quote": "",
                    }
                ],
                "operations": [],
            },
            "typed_semantics": {"observations": [], "proposals": []},
        }
        body = {
            "id": "response-1",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(plan)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 100, "total_tokens": 120},
        }

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def getcode(self):
                return 200

            def read(self):
                return json.dumps(body).encode()

        planner = DeepSeekEvidenceOperationPlanner(base_url="https://planner.test/v1", api_keys=["k"])
        with mock.patch("tmcra_v4_evidence_planner.urllib.request.urlopen", return_value=Response()) as call:
            result, metadata = planner.plan(catalog)
        self.assertEqual(result, plan)
        self.assertEqual(metadata["physical_api_calls"], 1)
        self.assertEqual(call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
