import unittest

import tmcra_v4_evidence_operations as operations


def row():
    return {
        "question_id": "q1",
        "question": "How many days elapsed and what was the average cost?",
        "question_date": "2023-04-24",
        "evidence_windows": [
            {
                "session_id": "s1",
                "session_index": 1,
                "parent_chunk_index": 0,
                "subchunk_index": 0,
                "source_record_id": "src1",
                "text": "On April 3, 2023 the first item cost $50.",
                "retrieval_metadata": {},
            },
            {
                "session_id": "s2",
                "session_index": 2,
                "parent_chunk_index": 0,
                "subchunk_index": 0,
                "source_record_id": "src2",
                "text": "On April 24, 2023 the second item cost $70.",
                "retrieval_metadata": {},
            },
        ],
    }


class EvidenceOperationTests(unittest.TestCase):
    @staticmethod
    def task_contract():
        return {
            "schema_version": "tmcra.task-contract.v4",
            "output_origin": "memory_derived",
            "target": {
                "subject": "remembered item costs",
                "relation": "average",
                "entity_constraints": ["first item", "second item"],
                "temporal_constraints": [],
                "state_constraints": [],
            },
            "output": {"shape": "scalar", "cardinality": "one", "order": "none"},
            "premises": [
                {
                    "premise_id": "R1",
                    "description": "first item cost",
                    "role": "operand",
                    "necessity": "required",
                    "source": "memory",
                    "grounded_constraints": ["first item cost $50"],
                    "context_quote": "",
                },
                {
                    "premise_id": "R2",
                    "description": "second item cost",
                    "role": "operand",
                    "necessity": "required",
                    "source": "memory",
                    "grounded_constraints": ["second item cost $70"],
                    "context_quote": "",
                },
            ],
            "operations": [
                {
                    "operation_id": "TC1",
                    "operation_type": "average",
                    "input_premise_ids": ["R1", "R2"],
                    "output_ref": "TARGET",
                    "parameters": {},
                }
            ],
        }

    def test_catalog_and_graph_keep_source_provenance(self):
        catalog = operations.build_evidence_catalog(row())
        self.assertEqual([item["evidence_id"] for item in catalog["evidence"]], ["E01", "E02"])
        dates = [item for item in catalog["atoms"] if item["atom_type"] == "date"]
        currencies = [item for item in catalog["atoms"] if item["atom_type"] == "currency"]
        self.assertEqual([item["normalized_value"] for item in dates[:2]], ["2023-04-03", "2023-04-24"])
        self.assertEqual([item["normalized_value"] for item in currencies], ["5E+1", "7E+1"])
        graph = operations.build_query_evidence_graph(catalog)
        self.assertTrue(any(edge["edge_type"] == "derived_from" and edge["support_ids"] == ["E01"] for edge in graph["edges"]))

    def test_catalog_preserves_layered_memory_views(self):
        value = row()
        window = value["evidence_windows"][0]
        window["memory_contexts"] = [
            {
                "role": "slow_context",
                "capsule_id": "cap1",
                "claim_id": "clm1",
                "canonical_slot": "user.preference.hotel",
                "claim_text": "The user prefers rooftop pools.",
                "provenance": {"memory_layer": "slow"},
            }
        ]
        window["attachments"] = [
            {
                "role": "override",
                "memory_id": "fast1",
                "canonical_slot": "user.preference.hotel",
                "text": "The user now prefers a balcony hot tub.",
                "provenance": {"memory_layer": "fast"},
            }
        ]
        window["provenance"] = [{"memory_layer": "slow", "claim_id": "clm1"}]
        window["historical_date"] = "2023/04/03 (Mon) 09:00"
        window["timestamp"] = "2023-04-03T09:00:00+00:00"
        window["message_role"] = "user"
        evidence = operations.build_evidence_catalog(value)["evidence"][0]
        self.assertEqual(evidence["memory_contexts"], window["memory_contexts"])
        self.assertEqual(evidence["attachments"], window["attachments"])
        self.assertEqual(evidence["provenance"], window["provenance"])
        self.assertEqual(evidence["historical_date"], window["historical_date"])
        self.assertEqual(evidence["timestamp"], window["timestamp"])
        self.assertEqual(evidence["message_role"], "user")

    def test_catalog_preserves_composite_source_identity_and_offsets(self):
        value = row()
        window = value["evidence_windows"][0]
        window.update(
            {
                "scope_id": "tmcra_v4:q1",
                "db_path": "/frozen/q1.sqlite3",
                "source_char_start": 0,
                "source_char_end": len(window["text"]),
            }
        )
        evidence = operations.build_evidence_catalog(value)["evidence"][0]
        self.assertEqual(evidence["scope_id"], "tmcra_v4:q1")
        self.assertEqual(evidence["db_path"], "/frozen/q1.sqlite3")
        self.assertEqual(evidence["source_record_id"], "src1")
        self.assertEqual(evidence["source_char_start"], 0)
        self.assertEqual(evidence["source_char_end"], len(window["text"]))

    def test_catalog_preserves_exact_source_text_and_order(self):
        value = row()
        value["evidence_windows"] = [
            {
                "session_id": f"s{index}",
                "session_index": index,
                "parent_chunk_index": 0,
                "subchunk_index": 0,
                "source_record_id": f"src{index}",
                "text": f"  source {index}\n",
                "retrieval_metadata": {"rank": index},
            }
            for index in range(1, 25)
        ]
        catalog = operations.build_evidence_catalog(value)
        self.assertEqual(
            [item["text"] for item in catalog["evidence"]],
            [item["text"] for item in value["evidence_windows"]],
        )
        self.assertEqual([item["session_id"] for item in catalog["evidence"]], [f"s{index}" for index in range(1, 25)])

    def test_catalog_preserves_source_group_context_and_extracts_its_atoms(self):
        value = row()
        value["evidence_windows"][0]["source_group_id"] = "source-group::s1:0"
        value["evidence_windows"][0]["source_group_context"] = [
            {
                "relationship": "session_neighbor",
                "parent_distance": 2,
                "session_id": "s1",
                "session_index": 1,
                "parent_chunk_index": 2,
                "source_record_id": "src-neighbor",
                "source_char_start": 12,
                "source_char_end": 55,
                "historical_date": "2023/04/03 (Mon) 09:01",
                "timestamp": "2023-04-03T09:01:00+00:00",
                "message_role": "assistant",
                "text": "The nearby turn says the total was $25.",
            }
        ]
        catalog = operations.build_evidence_catalog(value)
        evidence = catalog["evidence"][0]
        self.assertEqual(evidence["source_group_id"], "source-group::s1:0")
        self.assertEqual(
            evidence["source_group_context"][0]["text"],
            "The nearby turn says the total was $25.",
        )
        self.assertEqual(evidence["source_group_context"][0]["source_char_start"], 12)
        self.assertEqual(evidence["source_group_context"][0]["source_char_end"], 55)
        self.assertEqual(
            evidence["source_group_context"][0]["timestamp"],
            "2023-04-03T09:01:00+00:00",
        )
        self.assertTrue(
            any(
                atom["evidence_id"] == "E01"
                and atom["atom_type"] == "currency"
                and atom["normalized_value"] == "25"
                for atom in catalog["atoms"]
            )
        )

    def test_lexical_anchors_surface_distinct_question_phrases_without_dropping_source(self):
        value = row()
        value["question"] = "How long between the baking class and my friend's birthday cake?"
        value["evidence_windows"][0]["text"] = "I attended a baking class at the culinary school."
        value["evidence_windows"][1]["text"] = "I baked my friend's birthday cake today."
        catalog = operations.build_evidence_catalog(value)
        self.assertEqual(catalog["lexical_anchor_ids"][:2], ["E02", "E01"])
        self.assertEqual(len(catalog["evidence"]), 2)

    def test_relative_day_atom_is_derived_from_source_timestamp(self):
        value = row()
        value["evidence_windows"][0]["text"] = (
            "TMCRA timestamp=2022-03-21T15:54:02+00:00 role=user\n"
            "I attended the baking class yesterday."
        )
        catalog = operations.build_evidence_catalog(value)
        relative = [item for item in catalog["atoms"] if item.get("derivation") == "source_timestamp_minus_1_day"]
        self.assertEqual(len(relative), 1)
        self.assertEqual(relative[0]["normalized_value"], "2022-03-20")

    def test_relative_day_atom_uses_structured_timestamp_without_rewriting_source(self):
        value = row()
        value["evidence_windows"][0]["text"] = (
            "I attended the baking class yesterday."
        )
        value["evidence_windows"][0]["timestamp"] = (
            "2022-03-21T15:54:02+00:00"
        )
        catalog = operations.build_evidence_catalog(value)
        evidence = catalog["evidence"][0]
        relative = [
            item
            for item in catalog["atoms"]
            if item.get("derivation") == "source_timestamp_minus_1_day"
        ]
        self.assertEqual(evidence["text"], "I attended the baking class yesterday.")
        self.assertEqual(evidence["timestamp"], "2022-03-21T15:54:02+00:00")
        self.assertEqual(len(relative), 1)
        self.assertEqual(relative[0]["normalized_value"], "2022-03-20")

    def test_relative_day_atom_falls_back_to_structured_historical_date(self):
        value = row()
        value["evidence_windows"][0]["text"] = "I planted the saplings today."
        value["evidence_windows"][0]["historical_date"] = (
            "2023/05/02 (Tue) 14:30"
        )
        catalog = operations.build_evidence_catalog(value)
        relative = [
            item
            for item in catalog["atoms"]
            if item.get("derivation") == "source_timestamp_same_day"
        ]
        self.assertEqual(len(relative), 1)
        self.assertEqual(relative[0]["normalized_value"], "2023-05-02")

    def test_iso_timestamp_and_today_are_available_as_event_dates(self):
        value = row()
        value["evidence_windows"][0]["text"] = (
            "TMCRA timestamp=2022-04-10T14:14:10+00:00 role=user\n"
            "I baked the birthday cake today."
        )
        catalog = operations.build_evidence_catalog(value)
        dates = [item for item in catalog["atoms"] if item["evidence_id"] == "E01" and item["atom_type"] == "date"]
        self.assertEqual([item["normalized_value"] for item in dates], ["2022-04-10", "2022-04-10"])
        self.assertEqual(dates[1]["derivation"], "source_timestamp_same_day")

    def test_date_difference_is_absolute_by_default(self):
        catalog = operations.build_evidence_catalog(row())
        dates = [item["atom_id"] for item in catalog["atoms"] if item["atom_type"] == "date" and item["evidence_id"] != "QUESTION"]
        operation = {"operation_id": "O1", "operation_type": "date_difference", "input_atom_ids": list(reversed(dates)), "input_evidence_ids": ["E02", "E01"], "parameters": {}}
        self.assertEqual(operations.execute_operation(operation, catalog)["result"]["value"], 21)

    def test_executes_date_difference_and_numeric_average(self):
        catalog = operations.build_evidence_catalog(row())
        dates = [item["atom_id"] for item in catalog["atoms"] if item["atom_type"] == "date" and item["evidence_id"] != "QUESTION"]
        money = [item["atom_id"] for item in catalog["atoms"] if item["atom_type"] == "currency"]
        plan = {
            "schema_version": operations.PLAN_SCHEMA,
            "requirements": [
                {"requirement_id": "R1", "description": "elapsed days", "evidence_ids": ["E01", "E02"]},
                {"requirement_id": "R2", "description": "average cost", "evidence_ids": ["E01", "E02"]},
            ],
            "operations": [
                {"operation_id": "O1", "operation_type": "date_difference", "input_atom_ids": dates, "input_evidence_ids": ["E01", "E02"], "parameters": {}},
                {"operation_id": "O2", "operation_type": "numeric_average", "input_atom_ids": money, "input_evidence_ids": ["E01", "E02"], "parameters": {}},
            ],
            "bundles": [
                {"bundle_id": "B1", "role": "temporal_sequence", "evidence_ids": ["E01", "E02"]}
            ],
        }
        packet = operations.compile_evidence_packet(row(), plan)
        by_id = {item["operation_id"]: item for item in packet["operation_results"]}
        self.assertEqual(by_id["O1"]["result"], {"value": 21, "unit": "days"})
        self.assertEqual(by_id["O2"]["result"], {"value": 60, "unit": "$"})
        self.assertEqual(packet["raw_evidence_reservoir"][0]["text"], row()["evidence_windows"][0]["text"])

    def test_certifies_unique_date_candidate_matching_explicit_duration(self):
        value = row()
        value["question"] = "Which event happened about a month ago?"
        value["question_date"] = "2023-04-18"
        value["evidence_windows"][0]["text"] = "The first event was on 2023-02-14."
        value["evidence_windows"][1]["text"] = "The second event was on 2023-03-19."
        catalog = operations.build_evidence_catalog(value)
        dates = {
            item["evidence_id"]: item["atom_id"]
            for item in catalog["atoms"]
            if item["atom_type"] == "date"
        }
        plan = {
            "schema_version": operations.PLAN_SCHEMA,
            "requirements": [],
            "operations": [
                {
                    "operation_id": "O1",
                    "operation_type": "date_difference",
                    "input_atom_ids": [dates["E01"], dates["QUESTION"]],
                    "input_evidence_ids": ["E01"],
                    "parameters": {},
                },
                {
                    "operation_id": "O2",
                    "operation_type": "date_difference",
                    "input_atom_ids": [dates["E02"], dates["QUESTION"]],
                    "input_evidence_ids": ["E02"],
                    "parameters": {},
                },
            ],
            "bundles": [],
        }

        packet = operations.compile_evidence_packet(value, plan)
        by_id = {item["operation_id"]: item for item in packet["operation_results"]}

        self.assertFalse(by_id["O1"]["answer_authoritative"])
        self.assertTrue(by_id["O2"]["answer_authoritative"])
        self.assertEqual(by_id["O2"]["result"]["value"], 30)

    def test_plan_rejects_unknown_evidence_and_atoms(self):
        catalog = operations.build_evidence_catalog(row())
        bad = {
            "schema_version": operations.PLAN_SCHEMA,
            "requirements": [{"requirement_id": "R1", "description": "x", "evidence_ids": ["E99"]}],
            "operations": [],
            "bundles": [],
        }
        with self.assertRaisesRegex(operations.EvidenceOperationError, "unknown"):
            operations.validate_operation_plan(bad, catalog)

    def test_plan_validates_task_contract_and_typed_semantics(self):
        catalog = operations.build_evidence_catalog(row())
        plan = {
            "schema_version": operations.PLAN_SCHEMA,
            "requirements": [
                {"requirement_id": "R1", "description": "first cost", "evidence_ids": ["E01"]},
                {"requirement_id": "R2", "description": "second cost", "evidence_ids": ["E02"]},
            ],
            "operations": [],
            "bundles": [],
            "task_contract": self.task_contract(),
            "typed_semantics": {
                "observations": [
                    {
                        "observation_id": "T1",
                        "evidence_ids": ["E01"],
                        "entity_key": "first_item_cost",
                        "value_kind": "scalar",
                        "value": 50,
                        "unit": "$",
                        "temporal_kind": "none",
                        "polarity": "positive",
                    },
                    {
                        "observation_id": "T2",
                        "evidence_ids": ["E02"],
                        "entity_key": "second_item_cost",
                        "value_kind": "scalar",
                        "value": 70,
                        "unit": "$",
                        "temporal_kind": "none",
                        "polarity": "positive",
                    },
                ],
                "proposals": [
                    {
                        "candidate_id": "average_cost",
                        "operations": [
                            {
                                "operation_id": "AVG",
                                "operation": "numeric_average",
                                "input_ids": ["T1", "T2"],
                                "parameters": {},
                            }
                        ],
                        "output_ref": "AVG",
                    }
                ],
            },
        }
        validated = operations.validate_operation_plan(plan, catalog)
        self.assertEqual(validated["task_contract"]["output_origin"], "memory_derived")
        packet = operations.compile_evidence_packet(row(), plan)
        self.assertEqual(packet["typed_semantics_report"]["accepted"][0]["result"]["value"], 60)
        typed_result = next(
            item for item in packet["operation_results"] if item["operation_id"].startswith("TS")
        )
        self.assertEqual(typed_result["support_ids"], ["E01", "E02"])
        self.assertFalse(typed_result["authoritative"])
        self.assertEqual(packet["question_contract"]["output_origin"], "memory_derived")

    def test_plan_rejects_task_contract_id_mismatch_and_typed_unknown_evidence(self):
        catalog = operations.build_evidence_catalog(row())
        base = {
            "schema_version": operations.PLAN_SCHEMA,
            "requirements": [
                {"requirement_id": "R9", "description": "unmapped", "evidence_ids": ["E01"]}
            ],
            "operations": [],
            "bundles": [],
            "task_contract": self.task_contract(),
        }
        with self.assertRaisesRegex(operations.EvidenceOperationError, "share an ID"):
            operations.validate_operation_plan(base, catalog)
        base.pop("task_contract")
        base["typed_semantics"] = {
            "observations": [
                {
                    "observation_id": "T1",
                    "evidence_ids": ["E99"],
                    "entity_key": "x",
                    "value_kind": "scalar",
                    "value": 1,
                    "unit": "count",
                    "temporal_kind": "none",
                    "polarity": "positive",
                }
            ],
            "proposals": [],
        }
        with self.assertRaisesRegex(operations.EvidenceOperationError, "unknown"):
            operations.validate_operation_plan(base, catalog)

    def test_legacy_requirement_may_bind_required_query_context_premise(self):
        catalog = operations.build_evidence_catalog(row())
        task_contract = self.task_contract()
        task_contract["premises"].append(
            {
                "premise_id": "R3",
                "description": "question date",
                "role": "operand",
                "necessity": "required",
                "source": "query_context",
                "grounded_constraints": [],
                "context_quote": "",
            }
        )
        plan = {
            "schema_version": operations.PLAN_SCHEMA,
            "requirements": [
                {"requirement_id": "R1", "description": "first cost", "evidence_ids": ["E01"]},
                {"requirement_id": "R2", "description": "second cost", "evidence_ids": ["E02"]},
                {"requirement_id": "R3", "description": "question date", "evidence_ids": []},
            ],
            "operations": [],
            "bundles": [],
            "task_contract": task_contract,
            "typed_semantics": {"observations": [], "proposals": []},
        }
        validated = operations.validate_operation_plan(plan, catalog)
        self.assertEqual(validated["task_contract"]["premises"][-1]["source"], "query_context")
        packet = operations.compile_evidence_packet(row(), plan)
        coverage = {
            item["requirement_id"]: item for item in packet["requirement_coverage"]
        }
        self.assertEqual(coverage["R3"]["state"], "satisfied")
        self.assertEqual(coverage["R3"]["coverage_origin"], "query_context")
        self.assertNotIn("R3", operations.unbound_memory_requirement_ids(plan))

    def test_answer_requires_valid_source_or_computation_support(self):
        value = row()
        value["question"] = "Did the second event occur 21 days ago?"
        catalog = operations.build_evidence_catalog(value)
        date_ids = [item["atom_id"] for item in catalog["atoms"] if item["atom_type"] == "date" and item["evidence_id"] != "QUESTION"]
        plan = {
            "schema_version": operations.PLAN_SCHEMA,
            "requirements": [{"requirement_id": "R1", "description": "elapsed", "evidence_ids": ["E01", "E02"]}],
            "operations": [{"operation_id": "O1", "operation_type": "date_difference", "input_atom_ids": date_ids, "input_evidence_ids": ["E01", "E02"], "parameters": {}}],
            "bundles": [],
        }
        packet = operations.compile_evidence_packet(value, plan)
        answer = {
            "schema_version": operations.ANSWER_SCHEMA,
            "answerability": "sufficient",
            "claims": [{"claim_id": "C1", "text": "It was 21 days.", "support_ids": ["E01", "E02"], "computation_ids": ["O1"]}],
            "missing_requirements": [],
            "answer": "21 days.",
        }
        self.assertEqual(operations.validate_evidence_bound_answer(answer, packet)["answer"], "21 days.")
        answer["claims"][0]["support_ids"] = ["E99"]
        with self.assertRaisesRegex(operations.EvidenceOperationError, "unknown"):
            operations.validate_evidence_bound_answer(answer, packet)

    def test_answer_rejects_unverified_computation_without_source_support(self):
        catalog = operations.build_evidence_catalog(row())
        date_ids = [
            item["atom_id"]
            for item in catalog["atoms"]
            if item["atom_type"] == "date" and item["evidence_id"] != "QUESTION"
        ]
        plan = {
            "schema_version": operations.PLAN_SCHEMA,
            "requirements": [],
            "operations": [
                {
                    "operation_id": "O1",
                    "operation_type": "date_difference",
                    "input_atom_ids": date_ids,
                    "input_evidence_ids": ["E01", "E02"],
                    "parameters": {},
                }
            ],
            "bundles": [],
        }
        packet = operations.compile_evidence_packet(row(), plan)
        answer = {
            "schema_version": operations.ANSWER_SCHEMA,
            "answerability": "sufficient",
            "claims": [
                {
                    "claim_id": "C1",
                    "text": "It was 21 days.",
                    "origin": "memory_derived",
                    "support_ids": [],
                    "computation_ids": ["O1"],
                }
            ],
            "missing_requirements": [],
            "answer": "21 days.",
        }

        with self.assertRaisesRegex(operations.EvidenceOperationError, "unknown"):
            operations.validate_evidence_bound_answer(answer, packet)

    def test_answer_model_can_correct_planner_coverage(self):
        plan = {
            "schema_version": operations.PLAN_SCHEMA,
            "requirements": [{"requirement_id": "R1", "description": "missing entity", "evidence_ids": []}],
            "operations": [],
            "bundles": [],
        }
        packet = operations.compile_evidence_packet(row(), plan)
        unsupported = {
            "schema_version": operations.ANSWER_SCHEMA,
            "answerability": "sufficient",
            "claims": [],
            "missing_requirements": [],
            "answer": "Guessed.",
        }
        with self.assertRaisesRegex(operations.EvidenceOperationError, "unresolved"):
            operations.validate_evidence_bound_answer(unsupported, packet)
        sufficient = {
            **unsupported,
            "claims": [{"claim_id": "C1", "text": "Supported", "support_ids": ["E01"], "computation_ids": []}],
            "answer": "Supported.",
        }
        self.assertEqual(operations.validate_evidence_bound_answer(sufficient, packet)["answerability"], "sufficient")
        packet["requirement_coverage"][0]["state"] = "satisfied"
        insufficient = {**unsupported, "answerability": "insufficient", "missing_requirements": ["R1"], "answer": "The evidence is insufficient."}
        self.assertEqual(operations.validate_evidence_bound_answer(insufficient, packet)["answerability"], "insufficient")

    def test_answer_normalizes_omitted_empty_claim_binding_list(self):
        plan = {
            "schema_version": operations.PLAN_SCHEMA,
            "requirements": [],
            "operations": [],
            "bundles": [],
        }
        packet = operations.compile_evidence_packet(row(), plan)
        answer = {
            "schema_version": operations.ANSWER_SCHEMA,
            "answerability": "sufficient",
            "claims": [{"claim_id": "C1", "text": "Supported", "support_ids": ["E01"]}],
            "missing_requirements": [],
            "answer": "Supported.",
        }
        validated = operations.validate_evidence_bound_answer(answer, packet)
        self.assertEqual(validated["claims"][0]["computation_ids"], [])

    def test_answer_accepts_source_bound_inference_without_computation(self):
        plan = {
            "schema_version": operations.PLAN_SCHEMA,
            "requirements": [],
            "operations": [],
            "bundles": [],
        }
        packet = operations.compile_evidence_packet(row(), plan)
        answer = {
            "schema_version": operations.ANSWER_SCHEMA,
            "answerability": "sufficient",
            "claims": [
                {
                    "claim_id": "C1",
                    "text": "The first remembered event happened earlier.",
                    "origin": "memory_inference",
                    "support_ids": ["E01", "E02"],
                    "computation_ids": [],
                }
            ],
            "missing_requirements": [],
            "answer": "The first event.",
        }
        validated = operations.validate_evidence_bound_answer(answer, packet)
        self.assertEqual(validated["claims"][0]["origin"], "memory_inference")

    def test_answer_normalizes_legacy_source_bound_derived_claim(self):
        plan = {
            "schema_version": operations.PLAN_SCHEMA,
            "requirements": [],
            "operations": [],
            "bundles": [],
        }
        packet = operations.compile_evidence_packet(row(), plan)
        answer = {
            "schema_version": operations.ANSWER_SCHEMA,
            "answerability": "sufficient",
            "claims": [
                {
                    "claim_id": "C1",
                    "text": "A source-grounded synthesis.",
                    "origin": "memory_derived",
                    "support_ids": ["E01"],
                    "computation_ids": ["O99"],
                }
            ],
            "missing_requirements": [],
            "answer": "A grounded synthesis.",
        }
        validated = operations.validate_evidence_bound_answer(answer, packet)
        self.assertEqual(validated["claims"][0]["origin"], "memory_inference")
        self.assertEqual(validated["claims"][0]["computation_ids"], [])

    def test_memory_conditioned_answer_allows_explicit_non_memory_claim_origins(self):
        plan = {
            "schema_version": operations.PLAN_SCHEMA,
            "requirements": [
                {
                    "requirement_id": "R1",
                    "description": "remembered preference",
                    "evidence_ids": ["E01"],
                }
            ],
            "operations": [],
            "bundles": [],
            "task_contract": {
                "schema_version": "tmcra.task-contract.v4",
                "output_origin": "memory_conditioned_generation",
                "target": {
                    "subject": "activity",
                    "relation": "recommendation",
                    "entity_constraints": ["fits remembered preference"],
                    "temporal_constraints": [],
                    "state_constraints": [],
                },
                "output": {
                    "shape": "free_text",
                    "cardinality": "one",
                    "order": "none",
                },
                "premises": [
                    {
                        "premise_id": "R1",
                        "description": "remembered preference",
                        "role": "constraint",
                        "necessity": "required",
                        "source": "memory",
                        "grounded_constraints": ["prefers a familiar activity"],
                        "context_quote": "",
                    }
                ],
                "operations": [],
            },
            "typed_semantics": {"observations": [], "proposals": []},
        }
        packet = operations.compile_evidence_packet(row(), plan)
        answer = {
            "schema_version": operations.ANSWER_SCHEMA,
            "answerability": "sufficient",
            "claims": [
                {
                    "claim_id": "C1",
                    "text": "A remembered constraint.",
                    "origin": "memory_fact",
                    "support_ids": ["E01"],
                    "computation_ids": [],
                },
                {
                    "claim_id": "C2",
                    "text": "The user asks for a recommendation.",
                    "origin": "query_context",
                    "support_ids": [],
                    "computation_ids": [],
                },
                {
                    "claim_id": "C3",
                    "text": "The recommendation is reasonable.",
                    "origin": "model_knowledge",
                    "support_ids": [],
                    "computation_ids": [],
                },
            ],
            "missing_requirements": [],
            "answer": "A recommendation shaped by the remembered constraint.",
        }

        validated = operations.validate_evidence_bound_answer(answer, packet)

        self.assertEqual(
            [claim["origin"] for claim in validated["claims"]],
            ["memory_fact", "query_context", "model_knowledge"],
        )

        without_memory = {**answer, "claims": answer["claims"][1:]}
        with self.assertRaisesRegex(
            operations.EvidenceOperationError, "lacks a memory-bound claim"
        ):
            operations.validate_evidence_bound_answer(without_memory, packet)

        direct_packet = operations.compile_evidence_packet(
            row(),
            {
                "schema_version": operations.PLAN_SCHEMA,
                "requirements": [],
                "operations": [],
                "bundles": [],
            },
        )
        with self.assertRaisesRegex(
            operations.EvidenceOperationError, "not allowed"
        ):
            operations.validate_evidence_bound_answer(answer, direct_packet)


if __name__ == "__main__":
    unittest.main()
