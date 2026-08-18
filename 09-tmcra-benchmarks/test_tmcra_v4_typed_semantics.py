import unittest

from tmcra_v4_typed_semantics import evaluate_proposals


def observation(observation_id, evidence_id, entity_key, value_kind, value, unit, temporal_kind="none", time=None, event_status=None):
    result = {
        "observation_id": observation_id,
        "evidence_ids": [evidence_id],
        "entity_key": entity_key,
        "value_kind": value_kind,
        "value": value,
        "unit": unit,
        "temporal_kind": temporal_kind,
        "polarity": "positive",
    }
    if time is not None:
        result["time"] = time
    if event_status is not None:
        result["event_status"] = event_status
    return result


def candidate(candidate_id, operation, input_ids, output_ref=None, parameters=None):
    operation_value = {
        "operation_id": "O1",
        "operation": operation,
        "input_ids": input_ids,
    }
    if parameters is not None:
        operation_value["parameters"] = parameters
    result = {"candidate_id": candidate_id, "operations": [operation_value]}
    if output_ref is not None:
        result["output_ref"] = output_ref
    return result


class TypedSemanticProposalTests(unittest.TestCase):
    def test_multiplication_then_sum_chain_preserves_provenance(self):
        observations = [
            observation("price", "E-price", "item-price", "rate", 3, "$/dozen"),
            observation("quantity", "E-quantity", "order", "aggregated_quantity", 40, "dozen"),
            observation("shipping", "E-shipping", "shipping", "scalar", 7, "$"),
        ]
        proposal = {
            "candidate_id": "total-with-shipping",
            "operations": [
                {"operation_id": "O1", "operation": "numeric_multiply", "input_ids": ["price", "quantity"]},
                {"operation_id": "O2", "operation": "numeric_sum", "input_ids": ["O1", "shipping"]},
            ],
        }

        result = evaluate_proposals(observations, [proposal])

        accepted = result["accepted"][0]
        self.assertEqual(accepted["value"], 127)
        self.assertEqual(accepted["unit"], "$")
        self.assertEqual(accepted["source_evidence_ids"], ["E-price", "E-quantity", "E-shipping"])

    def test_numeric_average_age_is_unit_checked(self):
        observations = [
            observation("age-a", "E-a", "a", "scalar", 20, "years"),
            observation("age-b", "E-b", "b", "scalar", 30, "years"),
            observation("age-c", "E-c", "c", "scalar", 40, "years"),
        ]

        result = evaluate_proposals(observations, [candidate("average-age", "numeric_average", ["age-a", "age-b", "age-c"])])

        accepted = result["accepted"][0]
        self.assertEqual(accepted["value"], 30)
        self.assertEqual(accepted["unit"], "years")
        self.assertEqual(accepted["source_evidence_ids"], ["E-a", "E-b", "E-c"])

    def test_relative_numeric_offset_computes_age_difference(self):
        result = evaluate_proposals(
            [
                observation("older", "E-older", "older", "scalar", 42, "years"),
                observation("younger", "E-younger", "younger", "scalar", 35, "years"),
            ],
            [candidate("relative-age", "relative_numeric_offset", ["older", "younger"])],
        )

        accepted = result["accepted"][0]
        self.assertEqual(accepted["value"], 7)
        self.assertEqual(accepted["unit"], "years")

    def test_duration_difference_reuses_numeric_unit_validation(self):
        result = evaluate_proposals(
            [
                observation("duration-a", "E-duration-a", "task-a", "aggregated_quantity", 9, "hours"),
                observation("duration-b", "E-duration-b", "task-b", "aggregated_quantity", 4, "hours"),
            ],
            [candidate("duration-gap", "duration_difference", ["duration-a", "duration-b"])],
        )

        accepted = result["accepted"][0]
        self.assertEqual(accepted["value"], 5)
        self.assertEqual(accepted["unit"], "hours")

    def test_count_distinct_filters_non_actual_events(self):
        observations = [
            observation("actual", "E-actual", "visit-1", "event", "visited", None, event_status="actual"),
            observation("planned", "E-planned", "visit-2", "event", "will visit", None, event_status="planned"),
            observation("hypothetical", "E-hypothetical", "visit-3", "event", "might visit", None, event_status="hypothetical"),
            observation("mentioned", "E-mentioned", "visit-4", "event", "mentioned visit", None, event_status="mentioned"),
        ]

        result = evaluate_proposals(observations, [candidate("actual-visits", "count_distinct", ["actual", "planned", "hypothetical", "mentioned"])])

        accepted = result["accepted"][0]
        self.assertEqual(accepted["value"], 1)
        self.assertEqual(accepted["source_evidence_ids"], ["E-actual", "E-planned", "E-hypothetical", "E-mentioned"])

    def test_count_distinct_rejects_event_without_explicit_status(self):
        result = evaluate_proposals(
            [observation("event", "E-event", "visit", "event", "visited", None)],
            [candidate("count", "count_distinct", ["event"])],
        )

        self.assertEqual(result["accepted"], [])
        codes = {item["code"] for item in result["rejected"][0]["diagnostics"]}
        self.assertIn("MISSING_EVENT_STATUS", codes)

    def test_numeric_difference_rejects_incompatible_units(self):
        result = evaluate_proposals(
            [
                observation("age", "E-age", "person", "scalar", 30, "years"),
                observation("height", "E-height", "person", "scalar", 180, "cm"),
            ],
            [candidate("bad-difference", "numeric_difference", ["age", "height"])],
        )

        self.assertEqual(result["accepted"], [])
        self.assertEqual(result["rejected"][0]["diagnostics"][0]["code"], "INCOMPATIBLE_UNITS")

    def test_counting_aggregated_twelve_fish_is_rejected(self):
        result = evaluate_proposals(
            [observation("fish-total", "E-fish", "fish", "aggregated_quantity", 12, "fish")],
            [candidate("count-fish", "count_distinct", ["fish-total"])],
        )

        self.assertEqual(len(result["accepted"]), 0)
        self.assertEqual(result["rejected"][0]["diagnostics"][0]["code"], "INVALID_COUNT_ROLE")
        self.assertEqual(result["rejected"][0]["source_evidence_ids"], ["E-fish"])
        self.assertFalse(result["rejected"][0]["authoritative"])

    def test_rate_times_dozen_quantity_is_accepted(self):
        result = evaluate_proposals(
            [
                observation("price", "E-price", "dozen-price", "rate", 3, "$/dozen"),
                observation("quantity", "E-quantity", "dozen-order", "aggregated_quantity", 40, "dozen"),
            ],
            [candidate("total-cost", "numeric_multiply", ["price", "quantity"])],
        )

        accepted = result["accepted"][0]
        self.assertEqual(accepted["value"], 120)
        self.assertEqual(accepted["unit"], "$")
        self.assertEqual(accepted["source_evidence_ids"], ["E-price", "E-quantity"])

    def test_cumulative_snapshots_require_latest_instead_of_count(self):
        observations = [
            observation("snap-3", "E3", "inventory", "cumulative_snapshot", 3, "fish", "absolute", "2024-01-01"),
            observation("snap-5", "E5", "inventory", "cumulative_snapshot", 5, "fish", "absolute", "2024-01-02"),
        ]
        result = evaluate_proposals(
            observations,
            [
                candidate("wrong-count", "count_distinct", ["snap-3", "snap-5"]),
                candidate("right-latest", "latest", ["snap-3", "snap-5"]),
            ],
        )

        self.assertEqual(result["rejected"][0]["diagnostics"][0]["code"], "INVALID_COUNT_ROLE")
        self.assertEqual(result["accepted"][0]["value"], 5)
        self.assertEqual(result["accepted"][0]["source_evidence_ids"], ["E3", "E5"])

    def test_tennis_and_table_tennis_are_not_exact_entity_matches(self):
        result = evaluate_proposals(
            [
                observation("tennis", "E-tennis", "tennis", "entity_instance", "tennis", None),
                observation("table-tennis", "E-table", "table tennis", "entity_instance", "table tennis", None),
            ],
            [candidate("exact-entity", "entity_exact_match", ["tennis", "table-tennis"])],
        )

        self.assertEqual(result["accepted"][0]["value"], False)

    def test_unresolved_relative_dates_are_rejected(self):
        result = evaluate_proposals(
            [
                observation("start", "E-start", "event-a", "date", "last Monday", None, "relative_unresolved"),
                observation("end", "E-end", "event-b", "date", "2024-01-10", None, "absolute"),
            ],
            [candidate("date-order", "date_order", ["start", "end"])],
        )

        self.assertEqual(result["accepted"], [])
        self.assertEqual(result["rejected"][0]["diagnostics"][0]["code"], "UNRESOLVED_RELATIVE_DATE")

    def test_unknown_or_untyped_proposals_are_rejected_with_diagnostics(self):
        result = evaluate_proposals([], [{"candidate_id": "unknown", "operations": [{"operation_id": "O1", "operation": "guess"}]}])

        self.assertEqual(result["accepted"], [])
        self.assertEqual(result["rejected"][0]["diagnostics"][0]["code"], "UNKNOWN_OPERATION")
        self.assertFalse(result["rejected"][0]["authoritative"])


if __name__ == "__main__":
    unittest.main()
