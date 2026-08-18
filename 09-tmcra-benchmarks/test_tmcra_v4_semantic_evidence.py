import unittest

from tmcra_v4_evidence_operations import build_evidence_catalog
from test_tmcra_v4_evidence_operations import row as operation_row
from tmcra_v4_semantic_evidence import (
    RESOLUTION_PLAN_SCHEMA,
    SEMANTIC_ANSWER_SCHEMA,
    TASK_CONTRACT_SCHEMA,
    SemanticEvidenceError,
    compile_semantic_evidence_packet,
    normalize_semantic_answer_output,
    normalize_task_contract_sources,
    normalize_resolution_output,
    task_contract_source_review_reasons,
    validate_resolution_plan,
    validate_semantic_answer,
    validate_task_contract,
)


def row():
    return {
        "question_id": "q1",
        "question": "What should I look for in a hotel?",
        "question_date": "2023-04-24",
        "evidence_windows": [
            {
                "session_id": "s1",
                "session_index": 1,
                "parent_chunk_index": 0,
                "subchunk_index": 0,
                "source_record_id": "src1",
                "text": "I loved the ocean view and the rooftop pool at the last hotel.",
                "retrieval_metadata": {},
            },
            {
                "session_id": "s2",
                "session_index": 2,
                "parent_chunk_index": 0,
                "subchunk_index": 0,
                "source_record_id": "src2",
                "text": "The budget room felt too basic for this trip.",
                "retrieval_metadata": {},
            },
        ],
    }


def contract(origin="memory_conditioned"):
    return {
        "schema_version": TASK_CONTRACT_SCHEMA,
        "target": {
            "description": "hotel selection criteria",
            "subject": "user",
            "relation": "preferred hotel features",
        },
        "output": {
            "shape": "list",
            "cardinality": "one_or_more",
            "ordering": "none",
            "unit": "",
            "origin": origin,
        },
        "scope": {"temporal": "", "entity": "hotel", "context": "current trip"},
        "premises": [
            {
                "premise_id": "P01",
                "description": "remembered positive hotel features",
                "role": "constraint",
                "necessity": "required",
                "source": "memory",
                "context_quote": "",
            }
        ],
        "allowed_derivations": ["constraint_application"],
    }


def resolution(coverage="complete"):
    supported = coverage != "absent"
    return {
        "schema_version": RESOLUTION_PLAN_SCHEMA,
        "claims": (
            [
                {
                    "claim_id": "M01",
                    "subject": "user",
                    "predicate": "liked_feature",
                    "object": "ocean view",
                    "valid_time": "",
                    "polarity": "positive",
                    "modality": "experienced",
                    "evidence_id": "E01",
                    "source_quote": "loved the ocean view",
                }
            ]
            if supported
            else []
        ),
        "bindings": [
            {
                "premise_id": "P01",
                "claim_ids": ["M01"] if supported else [],
                "evidence_ids": ["E01"] if supported else [],
                "operation_ids": [],
                "coverage": coverage,
                "relation": "constraint",
            }
        ],
        "operations": [],
    }


class SemanticEvidenceTests(unittest.TestCase):
    def test_task_contract_is_compositional_and_rejects_route_labels(self):
        value = validate_task_contract(contract())
        self.assertEqual(value["output"]["origin"], "memory_conditioned")
        invalid = {**contract(), "question_type": "single-session-preference"}
        with self.assertRaisesRegex(SemanticEvidenceError, "root fields"):
            validate_task_contract(invalid)

    def test_resolution_requires_exact_source_quote_and_semantic_claim(self):
        catalog = build_evidence_catalog(row())
        value = validate_resolution_plan(resolution(), contract(), catalog)
        self.assertEqual(value["claims"][0]["evidence_id"], "E01")
        invalid = resolution()
        invalid["claims"][0]["source_quote"] = "a paraphrase not in Source"
        with self.assertRaisesRegex(SemanticEvidenceError, "exact Source span"):
            validate_resolution_plan(invalid, contract(), catalog)

    def test_memory_conditioned_packet_is_not_misclassified_as_missing_answer(self):
        packet = compile_semantic_evidence_packet(row(), contract(), resolution())
        certificate = packet["answerability_certificate"]
        self.assertEqual(certificate["memory_coverage"], "complete")
        self.assertEqual(certificate["task_executability"], "memory_conditioned_generation")
        self.assertEqual(certificate["answer_policy"], "answer_with_memory_context")
        self.assertIn("model_knowledge", certificate["allowed_claim_origins"])

    def test_non_memory_premises_do_not_enter_source_resolution_or_memory_coverage(self):
        task = contract()
        task["premises"].extend(
            [
                {
                    "premise_id": "P02",
                    "description": "the current request is about a hotel",
                    "role": "scope",
                    "necessity": "required",
                    "source": "query_context",
                    "context_quote": "hotel",
                },
                {
                    "premise_id": "P03",
                    "description": "general hotel candidates",
                    "role": "operand",
                    "necessity": "required",
                    "source": "model_knowledge",
                    "context_quote": "",
                },
            ]
        )
        packet = compile_semantic_evidence_packet(row(), task, resolution())
        self.assertEqual([item["premise_id"] for item in packet["premise_bindings"]], ["P01"])
        by_id = {
            item["premise_id"]: item
            for item in packet["answerability_certificate"]["premise_status"]
        }
        self.assertEqual(by_id["P02"]["coverage"], "complete")
        self.assertEqual(by_id["P03"]["coverage"], "available")
        self.assertEqual(packet["answerability_certificate"]["memory_coverage"], "complete")

    def test_source_review_detects_duplicate_memory_and_query_context_premises(self):
        task = contract()
        task["premises"].append(
            {
                "premise_id": "P02",
                "description": "hotel selection criteria in the current request",
                "role": "scope",
                "necessity": "required",
                "source": "query_context",
                "context_quote": "What should I look for in a hotel?",
            }
        )
        task["premises"][0]["description"] = "hotel selection criteria"
        task["premises"].append(
            {
                "premise_id": "P03",
                "description": "remembered hotel feature preferences",
                "role": "constraint",
                "necessity": "required",
                "source": "memory",
                "context_quote": "",
            }
        )
        self.assertTrue(task_contract_source_review_reasons(task))
        normalized, warnings = normalize_task_contract_sources(task)
        self.assertEqual([item["premise_id"] for item in normalized["premises"]], ["P02", "P03"])
        self.assertTrue(warnings)
        self.assertEqual(task_contract_source_review_reasons(normalized), [])

    def test_resolution_normalization_drops_only_unused_or_non_memory_output(self):
        task = contract()
        task["premises"].append(
            {
                "premise_id": "P02",
                "description": "hotel in current request",
                "role": "scope",
                "necessity": "required",
                "source": "query_context",
                "context_quote": "hotel",
            }
        )
        raw = resolution()
        raw["claims"].append(
            {
                "claim_id": "M99",
                "subject": "unused",
                "predicate": "unused",
                "object": "unused",
                "valid_time": "",
                "polarity": "positive",
                "modality": "asserted",
                "evidence_id": "E02",
                "source_quote": "not an exact quote",
            }
        )
        raw["bindings"].append(
            {"premise_id": "P02", "claim_ids": [], "evidence_ids": [], "operation_ids": [], "coverage": "complete", "relation": "context"}
        )
        raw["operations"] = [
            {"operation_id": "O99", "operation_type": "ordered_unique_list", "input_atom_ids": [], "input_claim_ids": ["M99"], "input_evidence_ids": ["E02"], "parameters": {"field": "object"}, "output_refs": ["TARGET"]}
        ]
        normalized, warnings = normalize_resolution_output(raw, task)
        self.assertEqual([item["premise_id"] for item in normalized["bindings"]], ["P01"])
        self.assertEqual([item["claim_id"] for item in normalized["claims"]], ["M01"])
        self.assertEqual(normalized["operations"], [])
        self.assertTrue(any("dropped_non_memory_premise" in item for item in warnings))

    def test_absent_required_premise_produces_auditable_abstention(self):
        packet = compile_semantic_evidence_packet(row(), contract(), resolution("absent"))
        certificate = packet["answerability_certificate"]
        self.assertEqual(certificate["memory_coverage"], "absent")
        self.assertEqual(certificate["answer_policy"], "abstain")
        self.assertEqual(certificate["unresolved_premise_ids"], ["P01"])

    def test_normalization_safely_downgrades_evidence_only_complete_binding(self):
        raw = resolution("absent")
        raw["bindings"][0].update(
            coverage="complete", evidence_ids=["E01"], relation="direct"
        )
        normalized, warnings = normalize_resolution_output(
            raw, contract(), build_evidence_catalog(row())
        )
        binding = normalized["bindings"][0]
        self.assertEqual(binding["coverage"], "absent")
        self.assertEqual(binding["evidence_ids"], [])
        self.assertTrue(any("downgraded_unsupported_complete" in item for item in warnings))
        validate_resolution_plan(normalized, contract(), build_evidence_catalog(row()))

    def test_normalization_removes_target_only_operation_from_premise_binding(self):
        task = contract("memory_derived")
        task["allowed_derivations"] = ["count_distinct"]
        raw = resolution()
        raw["bindings"][0]["operation_ids"] = ["O01"]
        raw["operations"] = [
            {
                "operation_id": "O01",
                "operation_type": "count_distinct",
                "input_atom_ids": [],
                "input_claim_ids": ["M01"],
                "input_evidence_ids": ["E01"],
                "parameters": {"field": "object"},
                "output_refs": ["TARGET"],
            }
        ]
        catalog = build_evidence_catalog(row())
        normalized, warnings = normalize_resolution_output(raw, task, catalog)
        self.assertEqual(normalized["bindings"][0]["operation_ids"], [])
        self.assertTrue(any("not_outputting_premise" in item for item in warnings))
        validate_resolution_plan(normalized, task, catalog)

    def test_partial_direct_memory_answers_only_supported_portion(self):
        packet = compile_semantic_evidence_packet(
            row(), contract("memory_direct"), resolution("partial")
        )
        certificate = packet["answerability_certificate"]
        self.assertEqual(certificate["task_executability"], "partially_answerable")
        self.assertEqual(certificate["answer_policy"], "answer_partial")
        answer = {
            "schema_version": SEMANTIC_ANSWER_SCHEMA,
            "response_status": "partial",
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
            "unresolved_premise_ids": ["P01"],
            "answer": "I can confirm the ocean view preference, but the record is incomplete.",
        }
        validated = validate_semantic_answer(answer, packet)
        self.assertEqual(validated["response_status"], "partial")

    def test_partial_derived_memory_still_abstains(self):
        packet = compile_semantic_evidence_packet(
            row(), contract("memory_derived"), resolution("partial")
        )
        self.assertEqual(packet["answerability_certificate"]["answer_policy"], "abstain")

    def test_partial_memory_conditioned_answer_must_use_bound_memory(self):
        task = contract("memory_conditioned")
        task["premises"].append(
            {
                "premise_id": "P02",
                "description": "general hotel candidates",
                "role": "operand",
                "necessity": "required",
                "source": "model_knowledge",
                "context_quote": "",
            }
        )
        packet = compile_semantic_evidence_packet(row(), task, resolution("partial"))
        answer = {
            "schema_version": SEMANTIC_ANSWER_SCHEMA,
            "response_status": "partial",
            "claims": [
                {
                    "claim_id": "C01",
                    "text": "Consider a hotel.",
                    "origin": "model_knowledge",
                    "premise_ids": ["P02"],
                    "source_claim_ids": [],
                    "computation_ids": [],
                }
            ],
            "unresolved_premise_ids": ["P01"],
            "answer": "Consider a hotel, but the preference record is incomplete.",
        }
        with self.assertRaisesRegex(SemanticEvidenceError, "does not use any memory claim"):
            validate_semantic_answer(answer, packet)

    def test_answer_claims_are_bound_through_premises_not_arbitrary_evidence(self):
        packet = compile_semantic_evidence_packet(row(), contract(), resolution())
        answer = {
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
                },
                {
                    "claim_id": "C02",
                    "text": "Look for a hotel with a strong view.",
                    "origin": "model_knowledge",
                    "premise_ids": [],
                    "source_claim_ids": [],
                    "computation_ids": [],
                },
            ],
            "unresolved_premise_ids": [],
            "answer": "Look for a hotel with an ocean view.",
        }
        validated = validate_semantic_answer(answer, packet)
        self.assertEqual(validated["claims"][0]["support_ids"], ["E01"])
        invalid = dict(answer)
        invalid["claims"] = [dict(answer["claims"][0], premise_ids=[])]
        with self.assertRaisesRegex(SemanticEvidenceError, "lacks premise"):
            validate_semantic_answer(invalid, packet)

    def test_answer_output_normalizer_repairs_only_verifiable_binding_metadata(self):
        task = contract()
        task["premises"].append(
            {
                "premise_id": "P02",
                "description": "current request",
                "role": "scope",
                "necessity": "required",
                "source": "query_context",
                "context_quote": "What should I look for in a hotel?",
            }
        )
        packet = compile_semantic_evidence_packet(row(), task, resolution())
        raw = {
            "schema_version": SEMANTIC_ANSWER_SCHEMA,
            "response_status": "answered",
            "claims": [
                {
                    "claim_id": "C01",
                    "text": "You liked an ocean view.",
                    "origin": "memory_derived",
                    "premise_ids": ["P01", "P02"],
                    "source_claim_ids": ["M01", "M99"],
                    "computation_ids": ["O99"],
                }
            ],
            "unresolved_premise_ids": [],
            "answer": "Look for a hotel with an ocean view.",
        }
        normalized = normalize_semantic_answer_output(raw, packet)
        self.assertEqual(normalized["response_status"], "answered_with_context")
        self.assertEqual(normalized["claims"][0]["origin"], "memory_fact")
        self.assertEqual(normalized["claims"][0]["premise_ids"], ["P01"])
        self.assertEqual(normalized["claims"][0]["source_claim_ids"], ["M01"])
        self.assertEqual(normalized["claims"][0]["computation_ids"], [])
        validate_semantic_answer(normalized, packet)

    def test_answer_output_normalizer_does_not_invent_answerability(self):
        packet = compile_semantic_evidence_packet(row(), contract(), resolution())
        raw = {
            "schema_version": SEMANTIC_ANSWER_SCHEMA,
            "response_status": "not_answerable",
            "claims": [],
            "unresolved_premise_ids": [],
            "answer": "I cannot answer.",
        }
        normalized = normalize_semantic_answer_output(raw, packet)
        self.assertEqual(normalized["response_status"], "not_answerable")
        self.assertEqual(normalized["claims"], [])

    def test_model_knowledge_is_forbidden_for_memory_direct_output(self):
        packet = compile_semantic_evidence_packet(row(), contract("memory_direct"), resolution())
        answer = {
            "schema_version": SEMANTIC_ANSWER_SCHEMA,
            "response_status": "answered",
            "claims": [
                {
                    "claim_id": "C01",
                    "text": "Unbound generated fact",
                    "origin": "model_knowledge",
                    "premise_ids": [],
                    "source_claim_ids": [],
                    "computation_ids": [],
                }
            ],
            "unresolved_premise_ids": [],
            "answer": "Unbound generated fact",
        }
        with self.assertRaisesRegex(SemanticEvidenceError, "origin"):
            validate_semantic_answer(answer, packet)

    def test_resolution_program_executes_only_contract_allowed_operations(self):
        value = operation_row()
        catalog = build_evidence_catalog(value)
        date_ids = [
            item["atom_id"]
            for item in catalog["atoms"]
            if item["atom_type"] == "date" and item["evidence_id"] != "QUESTION"
        ]
        task = {
            "schema_version": TASK_CONTRACT_SCHEMA,
            "target": {"description": "elapsed days", "subject": "two events", "relation": "date difference"},
            "output": {"shape": "duration", "cardinality": "one", "ordering": "none", "unit": "days", "origin": "memory_derived"},
            "scope": {"temporal": "between the two events", "entity": "", "context": ""},
            "premises": [{"premise_id": "P01", "description": "elapsed days between remembered dates", "role": "operand", "necessity": "required", "source": "memory", "context_quote": ""}],
            "allowed_derivations": ["date_difference"],
        }
        plan = {
            "schema_version": RESOLUTION_PLAN_SCHEMA,
            "claims": [],
            "bindings": [{"premise_id": "P01", "claim_ids": [], "evidence_ids": ["E01", "E02"], "operation_ids": ["O01"], "coverage": "complete", "relation": "derived"}],
            "operations": [{"operation_id": "O01", "operation_type": "date_difference", "input_atom_ids": date_ids, "input_claim_ids": [], "input_evidence_ids": ["E01", "E02"], "parameters": {}, "output_refs": ["P01", "TARGET"]}],
        }
        packet = compile_semantic_evidence_packet(value, task, plan)
        result = packet["resolution_program"]["operation_results"][0]
        self.assertEqual(result["result"], {"value": 21, "unit": "days"})
        self.assertEqual(packet["answerability_certificate"]["task_executability"], "derivable")
        invalid = dict(plan)
        invalid["operations"] = [dict(plan["operations"][0], operation_type="numeric_sum")]
        with self.assertRaisesRegex(SemanticEvidenceError, "output premises"):
            validate_resolution_plan(invalid, task, catalog)

    def test_semantic_composition_accepts_verified_numeric_claim_operation(self):
        value = row()
        value["evidence_windows"][0]["text"] = "The first amount was $20."
        value["evidence_windows"][1]["text"] = "The second amount was $5."
        task = contract("memory_derived")
        task["output"].update(shape="scalar", cardinality="one", unit="currency")
        task["allowed_derivations"] = ["semantic_composition"]
        plan = {
            "schema_version": RESOLUTION_PLAN_SCHEMA,
            "claims": [
                {
                    "claim_id": "M01", "subject": "first amount", "predicate": "value",
                    "object": "$20", "valid_time": "", "polarity": "positive",
                    "modality": "asserted", "evidence_id": "E01", "source_quote": "$20",
                },
                {
                    "claim_id": "M02", "subject": "second amount", "predicate": "value",
                    "object": "$5", "valid_time": "", "polarity": "positive",
                    "modality": "asserted", "evidence_id": "E02", "source_quote": "$5",
                },
            ],
            "bindings": [
                {
                    "premise_id": "P01", "claim_ids": ["M01", "M02"],
                    "evidence_ids": ["E01", "E02"], "operation_ids": [],
                    "coverage": "complete", "relation": "direct",
                }
            ],
            "operations": [
                {
                    "operation_id": "O01", "operation_type": "numeric_difference",
                    "input_atom_ids": [], "input_claim_ids": ["M01", "M02"],
                    "input_evidence_ids": ["E01", "E02"],
                    "parameters": {"field": "object", "unit": "currency"},
                    "output_refs": ["TARGET"],
                }
            ],
        }
        packet = compile_semantic_evidence_packet(value, task, plan)
        result = packet["resolution_program"]["operation_results"][0]
        self.assertEqual(result["result"], {"value": 15, "unit": "currency"})

    def test_claim_ledger_can_be_counted_without_preextracted_entity_atoms(self):
        value = row()
        task = contract("memory_derived")
        task["output"] = {"shape": "count", "cardinality": "one", "ordering": "none", "unit": "items", "origin": "memory_derived"}
        task["allowed_derivations"] = ["count_distinct"]
        plan = resolution()
        plan["claims"].append(
            {
                "claim_id": "M02",
                "subject": "user",
                "predicate": "disliked_feature",
                "object": "budget room",
                "valid_time": "",
                "polarity": "negative",
                "modality": "experienced",
                "evidence_id": "E02",
                "source_quote": "budget room felt too basic",
            }
        )
        plan["bindings"][0].update(
            {
                "claim_ids": ["M01", "M02"],
                "evidence_ids": ["E01", "E02"],
                "operation_ids": ["O01"],
                "relation": "derived",
            }
        )
        plan["operations"] = [
            {
                "operation_id": "O01",
                "operation_type": "count_distinct",
                "input_atom_ids": [],
                "input_claim_ids": ["M01", "M02"],
                "input_evidence_ids": ["E01", "E02"],
                "parameters": {"field": "object"},
                "output_refs": ["P01", "TARGET"],
            }
        ]
        packet = compile_semantic_evidence_packet(value, task, plan)
        result = packet["resolution_program"]["operation_results"][0]
        self.assertEqual(result["result"], {"value": 2, "values": ["ocean view", "budget room"]})
        answer = {
            "schema_version": SEMANTIC_ANSWER_SCHEMA,
            "response_status": "answered",
            "claims": [
                {
                    "claim_id": "C01",
                    "text": "There were two distinct remembered features.",
                    "origin": "memory_derived",
                    "premise_ids": ["P01"],
                    "source_claim_ids": [],
                    "computation_ids": ["O01"],
                }
            ],
            "unresolved_premise_ids": [],
            "answer": "2",
        }
        validated = validate_semantic_answer(answer, packet)
        self.assertEqual(validated["claims"][0]["support_ids"], ["E01", "E02"])

    def test_operator_failure_does_not_become_missing_memory(self):
        value = row()
        task = contract("memory_derived")
        task["output"] = {"shape": "scalar", "cardinality": "one", "ordering": "none", "unit": "", "origin": "memory_derived"}
        task["allowed_derivations"] = ["numeric_difference"]
        plan = resolution()
        plan["claims"] = [
            {
                "claim_id": f"M0{index}",
                "subject": "user",
                "predicate": "value",
                "object": str(number),
                "valid_time": "",
                "polarity": "positive",
                "modality": "asserted",
                "evidence_id": "E01",
                "source_quote": "ocean view",
            }
            for index, number in enumerate((10, 5, 3), start=1)
        ]
        plan["bindings"][0].update(
            {
                "claim_ids": ["M01", "M02", "M03"],
                "evidence_ids": ["E01"],
                "operation_ids": ["O01"],
                "coverage": "complete",
                "relation": "derived",
            }
        )
        plan["operations"] = [
            {
                "operation_id": "O01",
                "operation_type": "numeric_difference",
                "input_atom_ids": [],
                "input_claim_ids": ["M01", "M02", "M03"],
                "input_evidence_ids": ["E01"],
                "parameters": {"field": "object"},
                "output_refs": ["P01", "TARGET"],
            }
        ]
        packet = compile_semantic_evidence_packet(value, task, plan)
        certificate = packet["answerability_certificate"]
        self.assertEqual(certificate["memory_coverage"], "complete")
        self.assertEqual(certificate["operation_status"], "failed")
        self.assertEqual(certificate["failed_operation_ids"], ["O01"])
        self.assertEqual(certificate["unresolved_premise_ids"], [])
        self.assertEqual(certificate["unresolved_reasons"], ["operator_failure:O01"])


if __name__ == "__main__":
    unittest.main()
