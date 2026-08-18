import copy
import unittest

from tmcra_v4_task_contract import (
    RISK_AGGREGATE_WITHOUT_TYPED_INVENTORY,
    RISK_MEMORY_CONDITIONED_WITHOUT_GROUNDED_CONSTRAINTS,
    RISK_MULTI_STATE_WITHOUT_LATEST,
    RISK_PLANNER_MISSING_WITH_PLAUSIBLE_SOURCE,
    RISK_TEMPORAL_WITHOUT_OPERATION,
    TaskContractError,
    structural_risk_signals,
    validate_task_contract,
)


def premise(
    premise_id="P01",
    *,
    source="memory",
    role="fact",
    necessity="required",
    grounded_constraints=None,
    context_quote="",
    description="a grounded premise",
):
    return {
        "premise_id": premise_id,
        "description": description,
        "role": role,
        "necessity": necessity,
        "source": source,
        "grounded_constraints": list(grounded_constraints or []),
        "context_quote": context_quote,
    }


def contract(**overrides):
    value = {
        "schema_version": "tmcra.task-contract.v4",
        "output_origin": "memory_direct",
        "target": {
            "subject": "user",
            "relation": "preferred color",
            "entity_constraints": ["the user's preference"],
        },
        "output": {"shape": "scalar", "cardinality": "one", "order": "none"},
        "premises": [premise()],
    }
    for key, replacement in overrides.items():
        if isinstance(replacement, dict) and isinstance(value.get(key), dict):
            value[key] = {**value[key], **replacement}
        else:
            value[key] = replacement
    return value


class TaskContractValidationTests(unittest.TestCase):
    def test_valid_contract_normalizes_optional_operations_without_question_type(self):
        value = validate_task_contract(contract())
        self.assertEqual(value["operations"], [])
        self.assertNotIn("question_type", value)
        self.assertEqual(value["output_origin"], "memory_direct")

    def test_output_origin_is_a_strict_four_value_enum(self):
        for origin in (
            "memory_direct",
            "memory_derived",
            "memory_conditioned_generation",
            "external_required",
        ):
            value = contract(output_origin=origin)
            if origin == "memory_conditioned_generation":
                value["premises"] = [
                    premise(role="constraint", grounded_constraints=["a preference"])
                ]
            if origin == "external_required":
                value["premises"] = [premise(source="external_tool")]
            self.assertEqual(validate_task_contract(value)["output_origin"], origin)
        with self.assertRaises(TaskContractError):
            validate_task_contract(contract(output_origin="memory_conditioned"))

    def test_target_constraints_and_output_enums_are_strict(self):
        bad_target = contract(target={"entity_constraints": "user preference"})
        with self.assertRaises(TaskContractError):
            validate_task_contract(bad_target)
        collection = contract(output={"shape": "list", "cardinality": "one", "order": "none"})
        self.assertEqual(validate_task_contract(collection)["output"]["cardinality"], "one")
        bad_output = contract(output={"shape": "list", "cardinality": "many", "order": "none"})
        with self.assertRaises(TaskContractError):
            validate_task_contract(bad_output)
        bad_order = contract(output={"shape": "scalar", "cardinality": "one", "order": "random"})
        with self.assertRaises(TaskContractError):
            validate_task_contract(bad_order)

    def test_premise_sources_are_closed_and_context_quotes_are_optional_grounding(self):
        sources = {"memory", "query_context", "model_knowledge", "external_tool"}
        for source in sources:
            items = [premise()]
            if source != "memory":
                items.append(
                    premise(
                        "P02",
                        source=source,
                        necessity="required" if source == "external_tool" else "optional",
                        context_quote="current request" if source == "query_context" else "",
                    )
                )
            value = contract(premises=items)
            if source == "external_tool":
                value["output_origin"] = "external_required"
            validate_task_contract(value)
        query = validate_task_contract(
            contract(
                premises=[
                    premise(),
                    premise("P02", source="query_context", necessity="optional"),
                ]
            )
        )
        self.assertEqual(query["premises"][1]["context_quote"], "")
        memory_quote = validate_task_contract(
            contract(premises=[premise(context_quote="exact remembered quote")])
        )
        self.assertEqual(memory_quote["premises"][0]["context_quote"], "exact remembered quote")
        with self.assertRaises(TaskContractError):
            validate_task_contract(contract(premises=[premise(source="other")]))

    def test_operations_are_optional_but_references_and_types_are_checked(self):
        value = contract(
            output={"shape": "duration", "cardinality": "one", "order": "none"},
            target={"temporal_constraints": ["between two dates"]},
            operations=[
                {
                    "operation_id": "O01",
                    "operation_type": "date_difference",
                    "input_premise_ids": ["P01"],
                    "output_ref": "TARGET",
                    "parameters": {"unit": "days"},
                }
            ],
        )
        self.assertEqual(validate_task_contract(value)["operations"][0]["operation_type"], "date_difference")
        bad = copy.deepcopy(value)
        bad["operations"][0]["input_premise_ids"] = ["P99"]
        with self.assertRaises(TaskContractError):
            validate_task_contract(bad)

        typed = copy.deepcopy(value)
        typed["operations"][0]["operation_type"] = "numeric_average"
        self.assertEqual(
            validate_task_contract(typed)["operations"][0]["operation_type"],
            "numeric_average",
        )

    def test_recommendation_requires_memory_conditioned_generation(self):
        advice = contract(
            target={"relation": "book recommendation"},
            output_origin="memory_direct",
        )
        with self.assertRaisesRegex(TaskContractError, "memory_conditioned_generation"):
            validate_task_contract(advice)

        conditioned = contract(
            target={"relation": "book recommendation"},
            output_origin="memory_conditioned_generation",
            premises=[
                premise(
                    role="constraint",
                    grounded_constraints=["prefers short nonfiction"],
                    description="user preference constraints",
                )
            ],
        )
        self.assertEqual(validate_task_contract(conditioned)["output_origin"], "memory_conditioned_generation")

    def test_recommendation_does_not_require_a_historical_answer(self):
        value = contract(
            target={"relation": "advice for a study plan"},
            output_origin="memory_conditioned_generation",
            premises=[
                premise(
                    role="constraint",
                    grounded_constraints=["prefers morning sessions"],
                    description="user preference only; no historical answer is required",
                ),
                premise(
                    "P02",
                    source="model_knowledge",
                    role="fact",
                    necessity="optional",
                    description="general study planning knowledge",
                ),
            ],
        )
        self.assertEqual(validate_task_contract(value)["premises"][0]["role"], "constraint")

    def test_historical_recommendation_payload_is_memory_direct(self):
        value = contract(
            target={
                "relation": "recommended ratio",
                "temporal_constraints": ["past recommendation"],
            },
            output_origin="memory_direct",
            premises=[premise(role="fact", grounded_constraints=["one remembered ratio"])],
        )
        self.assertEqual(validate_task_contract(value)["output_origin"], "memory_direct")

    def test_historical_recommendation_target_without_recommendation_relation_is_direct(self):
        value = contract(
            target={
                "subject": "hostel recommended last time",
                "relation": "name",
                "temporal_constraints": ["last time"],
            },
            output_origin="memory_direct",
            premises=[premise(role="fact", grounded_constraints=["one remembered hostel"])],
        )
        self.assertEqual(validate_task_contract(value)["output_origin"], "memory_direct")

    def test_past_tense_recommended_fact_is_direct_without_extra_temporal_marker(self):
        value = contract(
            target={"relation": "recommended ratio"},
            output_origin="memory_direct",
            premises=[premise(role="fact", grounded_constraints=["one remembered ratio"])],
        )
        self.assertEqual(validate_task_contract(value)["output_origin"], "memory_direct")

    def test_memory_conditioned_missing_constraints_is_a_risk_not_schema_failure(self):
        value = contract(
            target={"relation": "recommendation"},
            output_origin="memory_conditioned_generation",
            premises=[premise(role="constraint")],
        )
        normalized = validate_task_contract(value)
        self.assertEqual(normalized["output_origin"], "memory_conditioned_generation")
        self.assertIn(
            RISK_MEMORY_CONDITIONED_WITHOUT_GROUNDED_CONSTRAINTS,
            structural_risk_signals(normalized),
        )

    def test_preference_role_alias_normalizes_to_constraint(self):
        value = contract(
            target={"relation": "book recommendation"},
            output_origin="memory_conditioned_generation",
            premises=[premise(role="preference", grounded_constraints=["short books"])],
        )
        self.assertEqual(validate_task_contract(value)["premises"][0]["role"], "constraint")

    def test_context_role_alias_normalizes_to_scope(self):
        value = contract(
            premises=[premise(role="context", necessity="required")],
        )
        self.assertEqual(validate_task_contract(value)["premises"][0]["role"], "scope")

    def test_operations_may_be_omitted_but_not_null(self):
        value = contract(operations=None)
        with self.assertRaises(TaskContractError):
            validate_task_contract(value)

    def test_unknown_root_fields_and_question_type_are_rejected(self):
        value = contract(question_type="list")
        with self.assertRaises(TaskContractError):
            validate_task_contract(value)
        value = contract(extra="not allowed")
        with self.assertRaises(TaskContractError):
            validate_task_contract(value)


class StructuralRiskTests(unittest.TestCase):
    def test_aggregate_without_typed_inventory(self):
        value = contract(
            target={"relation": "count items"},
            output={"shape": "count", "cardinality": "one", "order": "none"},
        )
        self.assertIn(RISK_AGGREGATE_WITHOUT_TYPED_INVENTORY, structural_risk_signals(value))
        value["premises"] = [premise(role="inventory", grounded_constraints=["item type"])]
        self.assertNotIn(RISK_AGGREGATE_WITHOUT_TYPED_INVENTORY, structural_risk_signals(value))
        numeric_total = contract(
            target={"relation": "total amount earned"},
            output={"shape": "scalar", "cardinality": "one", "order": "none"},
        )
        self.assertNotIn(
            RISK_AGGREGATE_WITHOUT_TYPED_INVENTORY,
            structural_risk_signals(numeric_total),
        )

    def test_temporal_without_operation(self):
        value = contract(
            target={"temporal_constraints": ["between start and end"]},
            output={"shape": "duration", "cardinality": "one", "order": "none"},
        )
        self.assertIn(RISK_TEMPORAL_WITHOUT_OPERATION, structural_risk_signals(value))
        value["operations"] = [
            {
                "operation_id": "O01",
                "operation_type": "date_difference",
                "input_premise_ids": ["P01"],
                "output_ref": "TARGET",
                "parameters": {},
            }
        ]
        self.assertNotIn(RISK_TEMPORAL_WITHOUT_OPERATION, structural_risk_signals(value))
        scoped = contract(
            target={"temporal_constraints": ["this month"]},
            output={"shape": "scalar", "cardinality": "one", "order": "none"},
        )
        self.assertNotIn(RISK_TEMPORAL_WITHOUT_OPERATION, structural_risk_signals(scoped))

    def test_multi_state_without_latest(self):
        value = contract(target={"state_constraints": ["old state", "current state"]})
        self.assertIn(RISK_MULTI_STATE_WITHOUT_LATEST, structural_risk_signals(value))
        value["operations"] = [
            {
                "operation_id": "O01",
                "operation_type": "latest_state",
                "input_premise_ids": ["P01"],
                "output_ref": "TARGET",
                "parameters": {},
            }
        ]
        self.assertNotIn(RISK_MULTI_STATE_WITHOUT_LATEST, structural_risk_signals(value))

    def test_memory_conditioned_without_grounded_constraints(self):
        value = contract(output_origin="memory_conditioned_generation")
        self.assertIn(
            RISK_MEMORY_CONDITIONED_WITHOUT_GROUNDED_CONSTRAINTS,
            structural_risk_signals(value),
        )
        value["premises"][0]["grounded_constraints"] = ["a preference"]
        self.assertNotIn(
            RISK_MEMORY_CONDITIONED_WITHOUT_GROUNDED_CONSTRAINTS,
            structural_risk_signals(value),
        )

    def test_planner_missing_with_plausible_source_and_override(self):
        value = contract()
        self.assertIn(
            RISK_PLANNER_MISSING_WITH_PLAUSIBLE_SOURCE,
            structural_risk_signals(value, planner_present=False),
        )
        self.assertNotIn(
            RISK_PLANNER_MISSING_WITH_PLAUSIBLE_SOURCE,
            structural_risk_signals(value, planner_present=False, plausible_source=False),
        )

    def test_risk_function_is_non_throwing_for_draft_contracts(self):
        risks = structural_risk_signals(
            {
                "output_origin": "memory_conditioned_generation",
                "target": {
                    "subject": "user",
                    "relation": "recommendation",
                    "state_constraints": ["a", "b"],
                },
                "output": {"shape": "list"},
                "premises": [],
            },
            planner_present=False,
        )
        self.assertIn(RISK_PLANNER_MISSING_WITH_PLAUSIBLE_SOURCE, risks)
        self.assertIn(RISK_MEMORY_CONDITIONED_WITHOUT_GROUNDED_CONSTRAINTS, risks)


if __name__ == "__main__":
    unittest.main()
