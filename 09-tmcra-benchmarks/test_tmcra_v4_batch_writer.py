from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tmcra_v4_batch_writer as v4


def source_message(content: str, *, role: str = "user", message_index: int = 0) -> v4.SourceMessage:
    return v4.SourceMessage(
        scope_id="tmcra_v4:test-qid",
        session_id="session-0",
        session_index=0,
        message_index=message_index,
        message_id=f"s000_m{message_index:03d}",
        role=role,
        timestamp="2026-07-11T00:00:00Z",
        content=content,
    )


def empty_response(batch: v4.SourceBatch) -> dict[str, object]:
    return {
        "schema_version": v4.BATCH_SCHEMA_VERSION,
        "batch_id": batch.batch_id,
        "messages": [
            {
                "message_id": message.message_id,
                "message_role": message.role,
                "assertions": [],
                "interactions": [],
                "resolutions": [],
            }
            for message in batch.messages
            if message.role in {"user", "assistant"}
        ],
    }


class FakeFlash:
    def __init__(self, response_factory):
        self.response_factory = response_factory
        self.calls: list[dict[str, object]] = []

    def complete(self, payload):
        self.calls.append(dict(payload))
        return self.response_factory(payload), {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}


class FakePro:
    def __init__(self, decision: str = "replace_current") -> None:
        self.decision = decision
        self.calls: list[dict[str, object]] = []

    def reconcile(self, payload):
        self.calls.append(dict(payload))
        candidates = payload["candidate_cited_leaves"]
        return {
            "slot_decision": "bind_existing",
            "selected_memory_id": candidates[0]["memory_id"],
            "decision": self.decision,
        }, {
            "prompt_tokens": 2,
            "completion_tokens": 1,
            "total_tokens": 3,
        }


def raw_assertion(
    *,
    span_id: str,
    memory_type: str,
    entity_key: str,
    attribute_key: str,
    durability: str,
    relation: str,
    claim_text: str = "The user has the stated memory.",
) -> dict[str, object]:
    return {
        "memory_type": memory_type,
        "entity_key": entity_key,
        "attribute_key": attribute_key,
        "operation": "replace",
        "claim_text": claim_text,
        "evidence_span_id": span_id,
        "relation": relation,
        "temporal_status": "current",
        "polarity": "positive",
        "durability": durability,
        "facets": [],
    }


class FakeGraphBackend:
    def __init__(self, scope_id: str) -> None:
        self.scope_id = scope_id
        self.sources: dict[str, dict[str, object]] = {}
        self.leaves: dict[str, list[dict[str, object]]] = {}
        self.commits: list[dict[str, object]] = []
        self.provenance: list[dict[str, object]] = []
        self.candidate_outputs: dict[str, list[dict[str, object]]] = {}

    def ensure_source(self, message: v4.SourceMessage) -> tuple[str, int]:
        source_record_id = f"source:{message.scope_id}:{message.message_id}"
        turn_index = message.message_index + 1
        existing = self.sources.get(source_record_id)
        expected = {
            "message": message,
            "turn_index": turn_index,
            "status": "pending",
            "error": "",
        }
        if existing is None:
            self.sources[source_record_id] = expected
        elif existing["message"] != message or existing["turn_index"] != turn_index:
            raise v4.ProductWriterError("fake immutable source changed")
        return source_record_id, turn_index

    def verify_source(
        self,
        message: v4.SourceMessage,
        source_record_id: str,
        source_turn_index: int,
    ) -> None:
        existing = self.sources.get(source_record_id)
        if (
            existing is None
            or existing["message"] != message
            or existing["turn_index"] != source_turn_index
        ):
            raise v4.ProductWriterError("committed real source record is missing")

    def set_enrichment_status(self, source_record_id: str, status: str, error: str = "") -> None:
        self.sources[source_record_id]["status"] = status
        self.sources[source_record_id]["error"] = error

    def source_enrichment_statuses(self, source_record_ids) -> dict[str, str]:
        return {
            source_record_id: str(self.sources[source_record_id]["status"])
            for source_record_id in source_record_ids
        }

    def current_leaves(self, canonical_slot_key: str) -> list[dict[str, object]]:
        return [
            dict(item)
            for item in self.leaves.get(v4._graph_slot_key(canonical_slot_key), [])
            if str(item.get("record_state")) in {"active", "parallel_active", "promoted"}
        ]

    def leaf_by_id(self, memory_id: str) -> dict[str, object] | None:
        for leaves in self.leaves.values():
            for item in leaves:
                if str(item.get("memory_id")) == memory_id:
                    return dict(item)
        return None

    def leaf_for_source_assertion(
        self, source_record_id: str, assertion_index: int
    ) -> dict[str, object] | None:
        matches = []
        for leaves in self.leaves.values():
            for item in leaves:
                metadata = dict(item.get("metadata") or {})
                if (
                    str(metadata.get("source_record_id")) == source_record_id
                    and int(metadata.get("llm_write_proposal_index", -1))
                    == assertion_index
                ):
                    matches.append(dict(item))
        if len(matches) > 1:
            raise v4.ProductWriterError("multiple persisted semantic leaves")
        return matches[0] if matches else None

    def repair_partial_replacement(
        self, historical_memory_id: str, incoming_memory_id: str
    ) -> dict[str, object]:
        historical = self.leaf_by_id(historical_memory_id)
        incoming = self.leaf_by_id(incoming_memory_id)
        if historical is None or incoming is None:
            raise v4.ProductWriterError("partial replacement records missing")
        for leaves in self.leaves.values():
            for item in leaves:
                if item.get("memory_id") == historical_memory_id:
                    item["record_state"] = "superseded"
                    item.setdefault("metadata", {})["superseded_by"] = incoming_memory_id
                    item["metadata"]["superseded_reason"] = (
                        "v4_reconciliation_replace_current"
                    )
                if item.get("memory_id") == incoming_memory_id:
                    item["record_state"] = "active"
                    item.setdefault("metadata", {}).pop("superseded_by", None)
                    item["metadata"].pop("superseded_reason", None)
        return self.leaf_by_id(incoming_memory_id)

    def candidate_leaves(self, assertion, *, limit: int = 3) -> list[dict[str, object]]:
        return [
            dict(item)
            for item in self.candidate_outputs.get(str(assertion["canonical_key"]), [])[:limit]
        ]

    def add_provenance(
        self,
        leaf_id: str,
        *,
        source_record_id: str,
        source_turn_index: int,
        provenance,
    ) -> None:
        self.provenance.append(
            {
                "leaf_id": leaf_id,
                "source_record_id": source_record_id,
                "source_turn_index": source_turn_index,
                "provenance": dict(provenance),
            }
        )

    def commit_message(self, **kwargs) -> int:
        snapshot = dict(kwargs)
        snapshot["extraction"] = dict(kwargs["extraction"])
        snapshot["durabilities"] = list(kwargs["durabilities"])
        snapshot["decisions"] = dict(kwargs["decisions"])
        snapshot["current_by_index"] = dict(kwargs["current_by_index"])
        self.commits.append(snapshot)
        committed = 0
        for index, assertion in enumerate(snapshot["extraction"].get("assertions") or []):
            if snapshot["decisions"].get(index) == "quarantine":
                continue
            slot = v4._graph_slot_key(assertion["canonical_key"])
            self.leaves.setdefault(slot, []).append(
                {
                    "memory_id": f"leaf:{len(self.commits)}:{index}",
                    "value": assertion["claim_text"],
                    "claim_text": assertion["claim_text"],
                    "evidence_quote": assertion["evidence_quote"],
                    "canonical_slot_key": slot,
                    "durability": snapshot["durabilities"][index],
                    "record_state": "active",
                    "metadata": {
                        "durability": snapshot["durabilities"][index],
                        "source_record_id": snapshot["source_record_id"],
                    },
                }
            )
            committed += 1
        return committed


class FakeGraphFactory:
    def __init__(self) -> None:
        self.backends: dict[str, FakeGraphBackend] = {}

    def for_scope(self, scope_id: str) -> FakeGraphBackend:
        return self.backends.setdefault(scope_id, FakeGraphBackend(scope_id))


class TestV4BatchWriter(unittest.TestCase):
    def test_committed_batch_cannot_be_downgraded_to_failed(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            message = source_message("I prefer blue.")
            batch = v4.SourceBatch(
                message.scope_id, message.session_id, 0, 0, (message,)
            )
            store = v4.V4BatchStore(Path(raw_dir) / "native_memory.sqlite3")
            store.prepare(batch, v4.build_batch_request(batch))
            store.persist_response(
                batch.batch_id,
                empty_response(batch),
                {"status": "completed"},
            )
            store.commit_batch(batch.batch_id)

            store.fail_batch(batch.batch_id, "postcommit repair failed")

            row = store.batch_row(batch.batch_id)
            self.assertEqual(row["status"], "committed")
            self.assertEqual(row["error"], "")

    def test_graph_injected_empty_benchmark_metadata_is_removed(self):
        record = SimpleNamespace(
            memory_id="leaf.0",
            metadata={"origin_answer_ids": [], "durability": "durable"},
        )
        self.assertEqual(
            v4.RealGraphBackend._remove_empty_graph_benchmark_metadata(record),
            ["origin_answer_ids"],
        )
        self.assertEqual(record.metadata, {"durability": "durable"})

        record.metadata["origin_answer_ids"] = ["answer.0"]
        with self.assertRaisesRegex(
            v4.ProductWriterError, "non-empty benchmark metadata"
        ):
            v4.RealGraphBackend._remove_empty_graph_benchmark_metadata(record)

    def test_keep_parallel_restores_graph_auto_superseded_current_record(self):
        slot = "memory.user.preference.color"
        current = SimpleNamespace(
            memory_id="leaf.old",
            slot_key=slot,
            turn_index=1,
            state="superseded",
            supersedes=[],
            metadata={
                "superseded_by": "leaf.new",
                "superseded_reason": "same_state_revision",
            },
        )
        incoming = SimpleNamespace(
            memory_id="leaf.new",
            slot_key=slot,
            turn_index=3,
            state="parallel_active",
            supersedes=["leaf.old"],
            metadata={
                "reconciliation_decision": "keep_parallel",
                "conflict_action": "supersede",
                "conflict_reason": "same_state_revision",
            },
        )
        graph = SimpleNamespace(
            records_by_id={"leaf.old": current, "leaf.new": incoming}
        )

        restored = v4.RealGraphBackend._honor_keep_parallel_decision(
            graph,
            incoming,
            [{"memory_id": "leaf.old", "record_state": "active"}],
        )

        self.assertEqual(restored, ["leaf.old"])
        self.assertEqual(current.state, "active")
        self.assertNotIn("superseded_by", current.metadata)
        self.assertNotIn("superseded_reason", current.metadata)
        self.assertEqual(incoming.supersedes, [])
        self.assertEqual(incoming.metadata["conflict_action"], "keep_parallel")
        self.assertEqual(
            incoming.metadata["conflict_reason"],
            "v4_reconciliation_keep_parallel",
        )

    def test_keep_parallel_rejects_unrecognized_supersession(self):
        slot = "memory.user.preference.color"
        current = SimpleNamespace(
            memory_id="leaf.old",
            slot_key=slot,
            turn_index=1,
            state="superseded",
            supersedes=[],
            metadata={
                "superseded_by": "leaf.new",
                "superseded_reason": "external_mutation",
            },
        )
        incoming = SimpleNamespace(
            memory_id="leaf.new",
            slot_key=slot,
            turn_index=3,
            state="parallel_active",
            supersedes=["leaf.old"],
            metadata={},
        )
        graph = SimpleNamespace(
            records_by_id={"leaf.old": current, "leaf.new": incoming}
        )
        with self.assertRaisesRegex(
            v4.ProductWriterError, "unsupported reason"
        ):
            v4.RealGraphBackend._honor_keep_parallel_decision(
                graph,
                incoming,
                [{"memory_id": "leaf.old", "record_state": "active"}],
            )

    def test_challenge_restores_graph_auto_superseded_current_record(self):
        slot = "memory.user.preference.flight_class"
        current = SimpleNamespace(
            memory_id="leaf.old",
            slot_key=slot,
            turn_index=1,
            state="superseded",
            supersedes=[],
            metadata={
                "superseded_by": "leaf.new",
                "superseded_reason": "same_state_revision",
            },
        )
        incoming = SimpleNamespace(
            memory_id="leaf.new",
            slot_key=slot,
            turn_index=3,
            state="challenged",
            supersedes=["leaf.old"],
            metadata={"reconciliation_decision": "challenge"},
        )
        graph = SimpleNamespace(
            records_by_id={"leaf.old": current, "leaf.new": incoming}
        )

        restored = v4.RealGraphBackend._honor_challenge_decision(
            graph,
            incoming,
            [{"memory_id": "leaf.old", "record_state": "active"}],
        )

        self.assertEqual(restored, ["leaf.old"])
        self.assertEqual(current.state, "active")
        self.assertNotIn("superseded_by", current.metadata)
        self.assertNotIn("superseded_reason", current.metadata)
        self.assertEqual(incoming.state, "challenged")
        self.assertEqual(incoming.supersedes, [])
        self.assertEqual(incoming.metadata["conflict_action"], "challenge")
        self.assertEqual(
            incoming.metadata["conflict_reason"],
            "v4_reconciliation_challenge",
        )

    def test_replayed_existing_replace_restores_active_state_and_slot_head(self):
        slot = "memory.user.preference.color"
        metadata = {
            "content_variant": "product_semantic_memory",
            "memory_layer": "fast",
            "node_kind": "atomic_user_assertion",
            "message_id": "s000_m000",
            "source_record_id": "source.s000.m000:1",
            "llm_write_proposal_index": 0,
            "canonical_slot_key": slot,
            "event_signature": "event.0",
            "evidence_quote": "I prefer green.",
            "source_span": "I prefer green.",
        }
        persisted = SimpleNamespace(
            memory_id="leaf.0",
            slot_key=slot,
            value="I prefer green.",
            turn_index=1,
            state="superseded",
            metadata={
                **metadata,
                "superseded_by": "leaf.1",
                "superseded_reason": "v4_reconciliation_replace_current",
                "provenance": [{"message_id": "s000_m000"}],
            },
        )
        replayed = SimpleNamespace(
            memory_id="leaf.0",
            slot_key=slot,
            value="I prefer green.",
            turn_index=1,
            state="active",
            metadata={**metadata, "reconciliation_decision": "replace_current"},
        )
        graph = SimpleNamespace(slot_heads={slot: "stale-head"})
        v4.RealGraphBackend._restore_replayed_semantic_record(
            graph, persisted, replayed, "replace_current"
        )
        self.assertEqual(persisted.state, "active")
        self.assertEqual(graph.slot_heads[slot], "leaf.0")
        self.assertNotIn("superseded_by", persisted.metadata)
        self.assertNotIn("superseded_reason", persisted.metadata)
        self.assertEqual(
            persisted.metadata["reconciliation_decision"], "replace_current"
        )
        self.assertEqual(
            persisted.metadata["provenance"], [{"message_id": "s000_m000"}]
        )

        drifted = SimpleNamespace(
            **{
                **vars(replayed),
                "metadata": {**metadata, "source_record_id": "different-source"},
            }
        )
        with self.assertRaisesRegex(
            v4.ProductWriterError, "source_record_id changed"
        ):
            v4.RealGraphBackend._restore_replayed_semantic_record(
                graph, persisted, drifted, "replace_current"
            )

    def test_writer_prompt_excludes_generic_topic_reactions_and_turn_local_plans(self):
        prompt = v4.BATCH_SYSTEM_PROMPT.casefold()
        self.assertIn("that's interesting", prompt)
        self.assertIn("external topic", prompt)
        self.assertIn("current turn", prompt)
        self.assertIn("pasted or forwarded email", prompt)
        self.assertIn("local author and subject", prompt)
        self.assertIn("immutable source", prompt)

    def test_empty_carrier_is_excluded_without_renumbering_following_message(self):
        rows = [
            {
                "question_id": "test-qid",
                "session_id": "session-0",
                "messages": [
                    {"role": "user", "content": ""},
                    {"role": "assistant", "content": "Actual content."},
                ],
            }
        ]
        messages, exclusions = v4.normalize_source_inventory(rows)
        self.assertEqual([item.message_id for item in messages], ["s000_m001"])
        self.assertEqual(exclusions[0]["message_id"], "s000_m000")
        self.assertEqual(exclusions[0]["reason"], "empty_content")

    def test_repeated_session_id_occurrences_keep_unique_source_and_batch_ids(self):
        rows = [
            {
                "question_id": "test-qid",
                "haystack_session_ids": ["same", "middle", "same"],
                "haystack_sessions": [
                    [{"role": "user", "content": "First occurrence."}],
                    [{"role": "user", "content": "Middle occurrence."}],
                    [{"role": "user", "content": "Second occurrence."}],
                ],
            }
        ]
        messages = v4.normalize_source_rows(rows)
        batches = v4.build_batches(messages)
        self.assertEqual(
            [message.message_id for message in messages],
            ["s000_m000", "s001_m000", "s002_m000"],
        )
        self.assertEqual(
            [batch.batch_id for batch in batches],
            [
                "tmcra_v4:test-qid:same:b0000",
                "tmcra_v4:test-qid:middle:b0000",
                "tmcra_v4:test-qid:same:b0001",
            ],
        )
        self.assertEqual([batch.session_index for batch in batches], [0, 1, 2])

    @staticmethod
    def candidate_backend(*records):
        backend = v4.RealGraphBackend.__new__(v4.RealGraphBackend)
        backend.adapter = SimpleNamespace(
            _reload_graph=lambda: None,
            graph=SimpleNamespace(
                records_by_id={record.memory_id: record for record in records}
            ),
        )
        return backend

    @staticmethod
    def candidate_record(memory_id, slot, *, entity, attribute, family, value):
        return SimpleNamespace(
            memory_id=memory_id,
            value=value,
            relation="needs_to",
            state="active",
            turn_index=1,
            metadata={
                "content_variant": "product_semantic_memory",
                "memory_layer": "fast",
                "node_kind": "atomic_user_assertion",
                "write_operation": "replace",
                "canonical_slot_key": slot,
                "entity_key": entity,
                "graph_entity_key": entity,
                "attribute_key": attribute,
                "memory_family": family,
            },
        )

    def test_candidate_selector_rejects_broad_home_setup_collision(self):
        backend = self.candidate_backend(
            self.candidate_record(
                "cable",
                "memory.user.home.goal.setup.cable.tv",
                entity="home",
                attribute="setup.cable.tv",
                family="goal",
                value="I need to set up cable and TV services.",
            ),
            self.candidate_record(
                "backyard",
                "memory.user.home.possession.has.backyard",
                entity="home",
                attribute="has.backyard",
                family="possession",
                value="My home has a backyard.",
            ),
        )
        candidates = backend.candidate_leaves(
            {
                "canonical_key": "user.home.goal.setup.electricity.gas",
                "entity_key": "home",
                "attribute_key": "setup.electricity.gas",
                "relation": "needs_to",
                "memory_family": "goal",
                "evidence_quote": "I need to set up electricity and gas services.",
                "facets": [],
            }
        )
        self.assertEqual(candidates, [])

        utilities_backend = self.candidate_backend(
            self.candidate_record(
                "cable-utilities",
                "memory.user.utilities.goal.set.up.cable.tv",
                entity="utilities",
                attribute="set.up.cable.tv",
                family="goal",
                value="I need to set up cable and TV services.",
            )
        )
        utilities_candidates = utilities_backend.candidate_leaves(
            {
                "canonical_key": "user.utilities.goal.set.up.electricity.gas",
                "entity_key": "utilities",
                "attribute_key": "set.up.electricity.gas",
                "relation": "needs_to",
                "memory_family": "goal",
                "evidence_quote": "I need to set up electricity and gas services.",
                "facets": [],
            }
        )
        self.assertEqual(utilities_candidates, [])

    def test_candidate_selector_keeps_mortgage_preapproval_drift(self):
        backend = self.candidate_backend(
            self.candidate_record(
                "mortgage",
                "memory.user.mortgage.pre.approval.event.pre.approved.amount",
                entity="mortgage.pre.approval",
                attribute="pre.approved.amount",
                family="event",
                value="I was pre-approved for $350,000 by Wells Fargo.",
            )
        )
        candidates = backend.candidate_leaves(
            {
                "canonical_key": "user.home.event.pre.approval",
                "entity_key": "home",
                "attribute_key": "pre.approval",
                "relation": "experienced",
                "memory_family": "event",
                "evidence_quote": "I was pre-approved for $400,000 by Wells Fargo.",
                "facets": [],
            }
        )
        self.assertEqual([item["memory_id"] for item in candidates], ["mortgage"])

    def test_candidate_selector_rejects_cross_family_lexical_overlap(self):
        backend = self.candidate_backend(
            self.candidate_record(
                "purchase",
                "memory.user.purchase.history.fact.bought.dresses.from.asos",
                entity="purchase.history",
                attribute="bought.dresses.from.asos",
                family="fact",
                value="I bought several dresses from ASOS.",
            )
        )
        candidates = backend.candidate_leaves(
            {
                "canonical_key": "user.fashion.preference.likes.asos.dresses",
                "entity_key": "fashion",
                "attribute_key": "likes.asos.dresses",
                "relation": "likes",
                "memory_type": "preference",
                "memory_family": "preference",
                "evidence_quote": "I love ASOS dresses.",
                "facets": [],
            }
        )
        self.assertEqual(candidates, [])

    def test_real_graph_source_verifier_uses_exact_raw_content(self):
        content = "First line.\n\nSecond line."
        record = SimpleNamespace(
            value="First line. Second line.",
            metadata={
                "raw_content": content,
                "source_span": content,
                "source_turn_text": content,
            },
        )
        self.assertEqual(
            v4.RealGraphBackend._verified_source_content(record),
            content,
        )

    def test_wire_request_is_lossless_span_sequence_without_duplicate_content_or_tokens(self):
        message = source_message("First sentence.  Second sentence!")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        request = v4.build_batch_request(batch)
        wire_message = request["messages"][0]
        self.assertEqual(set(wire_message), {"message_id", "message_role", "timestamp", "source_spans"})
        self.assertNotIn("content", wire_message)
        self.assertNotIn("source_tokens", wire_message)
        spans = wire_message["source_spans"]
        self.assertTrue(all(set(span) == {"span_id", "text"} for span in spans))
        self.assertEqual("".join(span["text"] for span in spans), message.content)

    def test_facet_quote_is_verified_and_optional_ungrounded_facet_is_dropped(self):
        message = source_message("I prefer blue.")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        response["messages"][0]["assertions"] = [{
            "memory_type": "preference",
            "entity_key": "laptop.purchase",
            "attribute_key": "color",
            "operation": "replace",
            "claim_text": "The user prefers blue for the laptop purchase.",
            "evidence_span_id": "e0",
            "relation": "prefers",
            "temporal_status": "current",
            "polarity": "positive",
            "durability": "durable",
            "facets": [{"type": "state", "role": "value", "quote": "blue"}],
        }]
        validated = v4.validate_batch_response(response, batch)
        facet = validated["messages"][0]["v3"]["assertions"][0]["facets"][0]
        self.assertEqual(facet["quote"], "blue")
        self.assertEqual(facet["token_start"], facet["token_end"])

        response["messages"][0]["assertions"][0]["facets"][0]["quote"] = "green"
        validated = v4.validate_batch_response(response, batch)
        self.assertEqual(validated["messages"][0]["v3"]["assertions"][0]["facets"], [])
        self.assertEqual(
            validated["messages"][0]["v3"]["validation_warnings"][-1]["code"],
            "optional_facet_dropped",
        )

    def test_distinct_grounded_facets_keep_same_slot_assertions_separate(self):
        message = source_message("I need tea and coffee.")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        assertions = []
        for drink in ("tea", "coffee"):
            assertion = raw_assertion(
                span_id="e0",
                memory_type="goal",
                entity_key="user.shopping",
                attribute_key="items",
                durability="episodic",
                relation="needs",
                claim_text=f"The user needs {drink}.",
            )
            assertion["operation"] = "append"
            assertion["facets"] = [
                {"type": "entity", "role": "item", "quote": drink}
            ]
            assertions.append(assertion)
        response["messages"][0]["assertions"] = assertions

        validated = v4.validate_batch_response(response, batch)["messages"][0]["v3"]

        self.assertEqual(len(validated["assertions"]), 2)
        self.assertEqual(
            {assertion["claim_text"] for assertion in validated["assertions"]},
            {"The user needs tea.", "The user needs coffee."},
        )

    def test_equivalent_duplicate_claim_text_is_canonicalized_with_warning(self):
        message = source_message("I need tea.")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        first = raw_assertion(
            span_id="e0",
            memory_type="goal",
            entity_key="user.shopping",
            attribute_key="items",
            durability="episodic",
            relation="needs",
            claim_text="The user needs tea.",
        )
        first["operation"] = "append"
        first["facets"] = [{"type": "entity", "role": "item", "quote": "tea"}]
        second = {**first, "claim_text": "User needs tea."}
        response["messages"][0]["assertions"] = [first, second]

        validated = v4.validate_batch_response(response, batch)["messages"][0]["v3"]

        self.assertEqual(len(validated["assertions"]), 1)
        self.assertEqual(validated["assertions"][0]["claim_text"], "User needs tea.")
        self.assertIn(
            "duplicate_claim_text_canonicalized",
            [warning["code"] for warning in validated["validation_warnings"]],
        )

    def test_temporal_value_in_durability_defaults_to_uncertain(self):
        message = source_message("I am preparing dinner tonight.")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        assertion = raw_assertion(
            span_id="e0",
            memory_type="event",
            entity_key="user.dinner",
            attribute_key="preparation",
            durability="episodic",
            relation="preparing",
            claim_text="The user is preparing dinner tonight.",
        )
        assertion["durability"] = "current"
        response["messages"][0]["assertions"] = [assertion]

        validated = v4.validate_batch_response(response, batch)["messages"][0]

        self.assertEqual(validated["durability"], ["uncertain"])
        self.assertEqual(len(validated["v3"]["assertions"]), 1)
        self.assertIn(
            "temporal_durability_defaulted_uncertain",
            [warning["code"] for warning in validated["v3"]["validation_warnings"]],
        )

    def test_invalid_assertion_is_quarantined_without_losing_valid_sibling(self):
        message = source_message("I need tea and coffee.")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        invalid = raw_assertion(
            span_id="e0",
            memory_type="goal",
            entity_key="user.shopping",
            attribute_key="tea",
            durability="episodic",
            relation="needs",
            claim_text="The user needs tea.",
        )
        invalid["operation"] = "invent"
        valid = raw_assertion(
            span_id="e0",
            memory_type="goal",
            entity_key="user.shopping",
            attribute_key="coffee",
            durability="episodic",
            relation="needs",
            claim_text="The user needs coffee.",
        )
        response["messages"][0]["assertions"] = [invalid, valid]

        validated = v4.validate_batch_response(response, batch)["messages"][0]["v3"]

        self.assertEqual(
            [assertion["claim_text"] for assertion in validated["assertions"]],
            ["The user needs coffee."],
        )
        warning = next(
            warning
            for warning in validated["validation_warnings"]
            if warning["code"] == "invalid_assertion_quarantined"
        )
        self.assertEqual(warning["dropped_count"], 1)
        self.assertIn("operation", warning["detail"])

    def test_invalid_interaction_is_quarantined_without_losing_assertion(self):
        message = source_message("I live in Paris. Can you remember that?")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        response["messages"][0]["assertions"] = [
            raw_assertion(
                span_id="e1",
                memory_type="state",
                entity_key="user.residence",
                attribute_key="city",
                durability="durable",
                relation="lives_in",
                claim_text="The user lives in Paris.",
            )
        ]
        response["messages"][0]["interactions"] = [
            {
                "interaction_type": "request",
                "status": "nonsense",
                "evidence_span_id": "e2",
                "intent": "remember_residence",
                "about": [],
            }
        ]

        validated = v4.validate_batch_response(response, batch)["messages"][0]["v3"]

        self.assertEqual(len(validated["assertions"]), 1)
        self.assertEqual(validated["interactions"], [])
        self.assertIn(
            "invalid_interaction_quarantined",
            [warning["code"] for warning in validated["validation_warnings"]],
        )

    def test_extra_assertion_fields_are_ignored_with_audit_warning(self):
        message = source_message("I live in Paris.")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        assertion = raw_assertion(
            span_id="e0",
            memory_type="state",
            entity_key="user.residence",
            attribute_key="city",
            durability="durable",
            relation="lives_in",
            claim_text="The user lives in Paris.",
        )
        assertion["confidence"] = 0.99
        response["messages"][0]["assertions"] = [assertion]

        validated = v4.validate_batch_response(response, batch)["messages"][0]["v3"]

        self.assertEqual(len(validated["assertions"]), 1)
        self.assertIn(
            "extra_item_fields_ignored",
            [warning["code"] for warning in validated["validation_warnings"]],
        )

    def test_invalid_evidence_span_still_fails_the_batch(self):
        message = source_message("I live in Paris.")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        response["messages"][0]["assertions"] = [
            raw_assertion(
                span_id="e999",
                memory_type="state",
                entity_key="user.residence",
                attribute_key="city",
                durability="durable",
                relation="lives_in",
                claim_text="The user lives in Paris.",
            )
        ]

        with self.assertRaises(v4.GroundingIntegrityError):
            v4.validate_batch_response(response, batch)

    def test_missing_optional_facets_and_about_default_to_empty_with_warning(self):
        message = source_message("I am flexible with travel dates. Can you find a flight?")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        assertion = raw_assertion(
            span_id="e1",
            memory_type="state",
            entity_key="user.travel",
            attribute_key="flexibility",
            durability="episodic",
            relation="is_flexible",
        )
        assertion.pop("facets")
        response["messages"][0]["assertions"] = [assertion]
        response["messages"][0]["interactions"] = [{
            "interaction_type": "request",
            "status": "open",
            "evidence_span_id": "e2",
            "intent": "find_flight",
        }]
        validated = v4.validate_batch_response(response, batch)
        output = validated["messages"][0]["v3"]
        self.assertEqual(output["assertions"][0]["facets"], [])
        self.assertEqual(output["interactions"][0]["about"], [])
        self.assertTrue(
            {"optional_facets_defaulted_empty", "optional_about_defaulted_empty"}
            .issubset({warning["code"] for warning in output["validation_warnings"]})
        )

    def test_neutral_polarity_is_preserved_for_non_valenced_fact(self):
        message = source_message("I'm in the 78749 zip code.")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        response["messages"][0]["assertions"] = [{
            **raw_assertion(
                span_id="e0",
                memory_type="state",
                entity_key="user.location",
                attribute_key="zip_code",
                durability="durable",
                relation="is",
            ),
            "polarity": "neutral",
        }]
        validated = v4.validate_batch_response(response, batch)
        self.assertEqual(
            validated["messages"][0]["v3"]["assertions"][0]["polarity"],
            "neutral",
        )

    def test_explicit_belief_and_opinion_types_are_preserved(self):
        message = source_message("I believe practice matters and I think this design is good.")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        response["messages"][0]["assertions"] = [
            raw_assertion(
                span_id="e0",
                memory_type="belief",
                entity_key="user.learning",
                attribute_key="practice_matters",
                durability="durable",
                relation="believes",
                claim_text="The user believes practice matters.",
            ),
            raw_assertion(
                span_id="e0",
                memory_type="opinion",
                entity_key="user.design",
                attribute_key="assessment",
                durability="episodic",
                relation="thinks",
                claim_text="The user thinks this design is good.",
            ),
        ]
        validated = v4.validate_batch_response(response, batch)
        self.assertEqual(
            [item["memory_type"] for item in validated["messages"][0]["v3"]["assertions"]],
            ["belief", "opinion"],
        )

    def test_grounded_snake_case_memory_type_extension_is_preserved_and_warned(self):
        message = source_message("I noticed that the team uses long balls.")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        response["messages"][0]["assertions"] = [
            raw_assertion(
                span_id="e0",
                memory_type="observation",
                entity_key="football.tactics",
                attribute_key="long_ball_usage",
                durability="episodic",
                relation="observed",
            )
        ]
        validated = v4.validate_batch_response(response, batch)
        output = validated["messages"][0]["v3"]
        self.assertEqual(output["assertions"][0]["memory_type"], "observation")
        self.assertEqual(output["assertions"][0]["memory_family"], "fact")
        self.assertIn(
            "memory_type_extension_accepted",
            [warning["code"] for warning in output["validation_warnings"]],
        )

    def test_long_snake_case_interaction_intent_is_preserved(self):
        message = source_message("Can you help me keep using positive self-talk?")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        intent = "request_tips_for_sticking_with_positive_self_talk_when_things_get_tough"
        response["messages"][0]["interactions"] = [{
            "interaction_type": "request",
            "status": "open",
            "evidence_span_id": "e0",
            "intent": intent,
            "about": [],
        }]
        validated = v4.validate_batch_response(response, batch)
        self.assertEqual(
            validated["messages"][0]["v3"]["interactions"][0]["intent"], intent
        )

    def test_identifier_case_only_is_normalized_with_audit_warning(self):
        message = source_message("Show me more options from DSW.")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        response["messages"][0]["interactions"] = [{
            "interaction_type": "request",
            "status": "open",
            "evidence_span_id": "e0",
            "intent": "show_more_DSW_options",
            "about": [],
        }]
        validated = v4.validate_batch_response(response, batch)
        output = validated["messages"][0]["v3"]
        self.assertEqual(
            output["interactions"][0]["intent"], "show_more_dsw_options"
        )
        self.assertIn(
            "identifier_case_normalized",
            [warning["code"] for warning in output["validation_warnings"]],
        )

        response["messages"][0]["interactions"][0]["intent"] = "show more options"
        quarantined = v4.validate_batch_response(response, batch)["messages"][0]["v3"]
        self.assertEqual(quarantined["interactions"], [])
        self.assertIn(
            "invalid_interaction_quarantined",
            [warning["code"] for warning in quarantined["validation_warnings"]],
        )

    def test_brand_ampersand_in_intent_is_normalized_with_audit_warning(self):
        message = source_message("Tell me about T&T Supermarket.")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        response["messages"][0]["interactions"] = [{
            "interaction_type": "question",
            "status": "open",
            "evidence_span_id": "e0",
            "intent": "ask_about_T&T_supermarket",
            "about": [],
        }]
        validated = v4.validate_batch_response(response, batch)
        output = validated["messages"][0]["v3"]
        self.assertEqual(
            output["interactions"][0]["intent"], "ask_about_t_and_t_supermarket"
        )
        self.assertIn(
            "identifier_symbol_normalized",
            [warning["code"] for warning in output["validation_warnings"]],
        )

    def test_assistant_assertions_are_dropped_but_source_layers_are_retained(self):
        message = source_message(
            "You could try the blue option.", role="assistant"
        )
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        response["messages"][0]["assertions"] = [
            raw_assertion(
                span_id="e0",
                memory_type="preference",
                entity_key="user.color",
                attribute_key="favorite",
                durability="durable",
                relation="prefers",
            )
        ]
        response["messages"][0]["interactions"] = [{
            "interaction_type": "request",
            "status": "open",
            "evidence_span_id": "e0",
            "intent": "consider_blue_option",
            "about": [],
        }]
        validated = v4.validate_batch_response(response, batch)
        output = validated["messages"][0]["v3"]
        self.assertEqual(output["assertions"], [])
        self.assertEqual(len(output["interactions"]), 1)
        warning = next(
            item
            for item in output["validation_warnings"]
            if item["code"] == "assistant_assertions_dropped"
        )
        self.assertEqual(warning["dropped_count"], 1)

    def test_single_message_over_soft_target_is_not_split(self):
        message = source_message("word " * 5000)
        batches = v4.build_batches([message])
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].messages, (message,))

    def test_message_over_configured_hard_limit_fails_without_segmentation(self):
        message = source_message("word " * 11)
        with self.assertRaisesRegex(v4.ProductWriterError, "over hard limit"):
            v4.build_batches([message], hard_limit_tokens=10)

    def test_batch_local_resolution_is_validated_and_resolved_deterministically(self):
        first = source_message("Please remember that I like tea.", role="user", message_index=0)
        second = source_message("Yes, I will remember that.", role="assistant", message_index=1)
        batch = v4.SourceBatch(first.scope_id, first.session_id, 0, 0, (first, second))
        response = empty_response(batch)
        response["messages"][0]["interactions"] = [{
            "interaction_type": "request",
            "status": "open",
            "evidence_span_id": "e0",
            "intent": "remember_preference",
            "about": [],
        }]
        response["messages"][1]["resolutions"] = [{
            "target": {"kind": "batch", "message_id": first.message_id, "interaction_index": 0},
            "resolution": "resolved",
            "evidence_span_id": "e0",
        }]
        validated = v4.validate_batch_response(response, batch)
        self.assertEqual(
            validated["messages"][1]["v3"]["resolutions"][0]["interaction_id"],
            "interaction:tmcra_v4:test-qid:s000_m000:0",
        )

        response["messages"][1]["resolutions"][0]["target"]["interaction_index"] = 1
        validated = v4.validate_batch_response(response, batch)
        self.assertEqual(validated["messages"][1]["v3"]["resolutions"], [])
        self.assertEqual(
            validated["messages"][1]["v3"]["validation_warnings"][-1]["code"],
            "optional_resolution_dropped",
        )

    def test_resolution_to_merged_duplicate_interaction_is_remapped(self):
        first = source_message("Please help me remember this.", role="user", message_index=0)
        second = source_message("I will help with that.", role="assistant", message_index=1)
        batch = v4.SourceBatch(first.scope_id, first.session_id, 0, 0, (first, second))
        response = empty_response(batch)
        interaction = {
            "interaction_type": "request",
            "status": "open",
            "evidence_span_id": "e0",
            "intent": "remember_this",
            "about": [],
        }
        response["messages"][0]["interactions"] = [dict(interaction), dict(interaction)]
        response["messages"][1]["resolutions"] = [{
            "target": {"kind": "batch", "message_id": first.message_id, "interaction_index": 1},
            "resolution": "resolved",
            "evidence_span_id": "e0",
        }]
        validated = v4.validate_batch_response(response, batch)
        self.assertEqual(len(validated["messages"][0]["v3"]["interactions"]), 1)
        self.assertEqual(
            validated["messages"][1]["v3"]["resolutions"][0]["interaction_id"],
            "interaction:tmcra_v4:test-qid:s000_m000:0",
        )

    def test_writer_journals_sources_before_one_flash_and_emits_v3_compatible_files(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            messages = [source_message("I prefer blue.", message_index=0)]
            batch = v4.SourceBatch(messages[0].scope_id, messages[0].session_id, 0, 0, tuple(messages))
            store = v4.V4BatchStore(tmp_path / "native_memory.sqlite3")
            observed_statuses = []

            class CheckedFlash(FakeFlash):
                def complete(self, payload):
                    with closing(sqlite3.connect(store.path)) as connection:
                        observed_statuses.extend(row[0] for row in connection.execute("SELECT status FROM v4_source_journal"))
                    return super().complete(payload)

            flash = CheckedFlash(lambda payload: empty_response(batch))
            graph_factory = FakeGraphFactory()
            writer = v4.V4BatchWriter(store=store, flash_client=flash, graph_factory=graph_factory, log_dir=tmp_path)
            stats = writer.run([{
                "question_id": "test-qid",
                "haystack_session_ids": ["session-0"],
                "haystack_sessions": [[{"role": "user", "content": "I prefer blue."}]],
            }])
            self.assertEqual(observed_statuses, ["pending"])
            self.assertEqual(len(flash.calls), 1)
            self.assertEqual(stats["flash_calls"], 1)
            self.assertTrue((tmp_path / "product_writer_calls.jsonl").exists())
            raw_call = json.loads(
                (tmp_path / "product_writer_raw_responses.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(raw_call["call_key"], f"flash:{batch.batch_id}")
            self.assertEqual(json.loads(raw_call["raw_response"]), empty_response(batch))
            self.assertEqual(
                raw_call["raw_response_sha256"],
                v4.sha256_text(raw_call["raw_response"]),
            )
            self.assertTrue((tmp_path / "product_write_messages.jsonl").exists())
            backend = graph_factory.for_scope("tmcra_v4:test-qid")
            self.assertEqual(len(backend.sources), 1)
            self.assertEqual(next(iter(backend.sources.values()))["status"], "enriched")

    def test_api_failure_keeps_one_immutable_source_record_and_no_semantic_records(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            store = v4.V4BatchStore(tmp_path / "native_memory.sqlite3")

            class FailingFlash:
                def complete(self, payload):
                    raise RuntimeError("upstream unavailable")

            graph_factory = FakeGraphFactory()
            writer = v4.V4BatchWriter(store=store, flash_client=FailingFlash(), graph_factory=graph_factory)
            rows = [{
                "question_id": "test-qid",
                "session_id": "session-0",
                "messages": [{"role": "user", "timestamp": "2026-07-11T00:00:00Z", "content": "I prefer blue."}],
            }]
            with self.assertRaisesRegex(RuntimeError, "upstream unavailable"):
                writer.run(rows)
            with closing(sqlite3.connect(store.path)) as connection:
                journal = connection.execute("SELECT status, enrichment_error FROM v4_source_journal").fetchone()
            self.assertEqual(journal[0], "failed")
            self.assertIn("upstream unavailable", journal[1])
            backend = graph_factory.for_scope("tmcra_v4:test-qid")
            self.assertEqual(len(backend.sources), 1)
            source = next(iter(backend.sources.values()))
            self.assertEqual(source["message"].content, "I prefer blue.")
            self.assertEqual(source["status"], "failed")
            self.assertEqual(backend.leaves, {})
            self.assertEqual(backend.commits, [])

    def test_validation_failure_preserves_paid_raw_response(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message("I prefer blue.")
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            response = empty_response(batch)
            response["messages"][0]["assertions"] = [
                raw_assertion(
                    span_id="e999",
                    memory_type="fact",
                    entity_key="user.preference",
                    attribute_key="color",
                    durability="durable",
                    relation="prefers",
                )
            ]
            writer = v4.V4BatchWriter(
                store=v4.V4BatchStore(tmp_path / "native_memory.sqlite3"),
                flash_client=FakeFlash(lambda payload: response),
                graph_factory=FakeGraphFactory(),
                log_dir=tmp_path,
            )
            with self.assertRaisesRegex(v4.GroundingIntegrityError, "evidence"):
                writer.run(
                    [{
                        "question_id": "test-qid",
                        "session_id": "session-0",
                        "messages": [{"role": "user", "content": message.content}],
                    }]
                )
            raw_call = json.loads(
                (tmp_path / "product_writer_raw_responses.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(json.loads(raw_call["raw_response"]), response)
            self.assertEqual(raw_call["stage"], "batch_flash")

    def test_started_batch_is_not_retried(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message("No semantic output.")
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            store = v4.V4BatchStore(tmp_path / "native_memory.sqlite3")
            store.prepare(batch, v4.build_batch_request(batch))
            store.mark_api_started(batch.batch_id)
            flash = FakeFlash(lambda payload: empty_response(batch))
            writer = v4.V4BatchWriter(store=store, flash_client=flash)
            with self.assertRaisesRegex(v4.ProductWriterError, "refusing retry"):
                writer.run([{"question_id": "test-qid", "session_id": "session-0", "messages": [{"role": "user", "timestamp": message.timestamp, "content": message.content}]}])
            self.assertEqual(flash.calls, [])

    def test_interrupted_flash_call_requires_explicit_same_model_recovery(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message("No semantic output.")
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            response = empty_response(batch)
            store = v4.V4BatchStore(tmp_path / "native_memory.sqlite3")
            store.prepare(batch, v4.build_batch_request(batch))
            store.mark_api_started(batch.batch_id)
            flash = FakeFlash(lambda payload: response)
            writer = v4.V4BatchWriter(
                store=store,
                flash_client=flash,
                graph_factory=FakeGraphFactory(),
                log_dir=tmp_path,
                recover_interrupted_api_calls=True,
            )
            stats = writer.run([{
                "question_id": "test-qid",
                "session_id": "session-0",
                "messages": [{"role": "user", "content": message.content}],
            }])
            self.assertEqual(len(flash.calls), 1)
            self.assertEqual(stats["interrupted_call_recoveries"], 1)
            interrupted = json.loads(
                (tmp_path / "product_writer_interrupted_calls.jsonl")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(interrupted["model"], "deepseek-v4-flash")
            self.assertTrue(interrupted["physical_api_call"])
            self.assertTrue(interrupted["same_model_replacement"])
            self.assertEqual(store.batch_row(batch.batch_id)["status"], "committed")

    def test_interrupted_pro_call_is_replaced_once_after_process_loss_review(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message("I prefer green.")
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            response = empty_response(batch)
            response["messages"][0]["assertions"] = [
                raw_assertion(
                    span_id="e0",
                    memory_type="preference",
                    entity_key="laptop.purchase",
                    attribute_key="color",
                    durability="durable",
                    relation="prefers",
                )
            ]
            validated = v4.validate_batch_response(response, batch)
            slot = v4._graph_slot_key(
                validated["messages"][0]["v3"]["assertions"][0]["canonical_key"]
            )
            graph_factory = FakeGraphFactory()
            backend = graph_factory.for_scope(message.scope_id)
            backend.leaves[slot] = [{
                "memory_id": "existing-blue",
                "value": "I prefer blue.",
                "evidence_quote": "I prefer blue.",
                "canonical_slot_key": slot,
                "durability": "durable",
                "record_state": "active",
                "metadata": {"durability": "durable"},
            }]
            rows = [{
                "question_id": "test-qid",
                "session_id": "session-0",
                "messages": [{"role": "user", "content": message.content}],
            }]
            store = v4.V4BatchStore(tmp_path / "native_memory.sqlite3")
            first_writer = v4.V4BatchWriter(
                store=store,
                flash_client=FakeFlash(lambda payload: response),
                pro_client=FakePro(),
                graph_factory=graph_factory,
                log_dir=tmp_path,
            )
            original_start = store.start_reconciliation_job

            def interrupt_after_start(job_id):
                original_start(job_id)
                raise SystemExit("simulated process loss")

            with mock.patch.object(
                store, "start_reconciliation_job", side_effect=interrupt_after_start
            ):
                with self.assertRaisesRegex(SystemExit, "process loss"):
                    first_writer.run(rows)
            with closing(sqlite3.connect(store.path)) as connection:
                batch_status = connection.execute(
                    "SELECT status FROM v4_batch_journal"
                ).fetchone()[0]
                pro_status = connection.execute(
                    "SELECT status FROM v4_reconciliation_jobs"
                ).fetchone()[0]
            self.assertEqual(batch_status, "validated")
            self.assertEqual(pro_status, "pro_started")

            second_pro = FakePro()
            second_writer = v4.V4BatchWriter(
                store=store,
                flash_client=FakeFlash(lambda payload: self.fail("Flash must not be called")),
                pro_client=second_pro,
                graph_factory=graph_factory,
                log_dir=tmp_path,
                recover_interrupted_api_calls=True,
            )
            stats = second_writer.run(rows)
            self.assertEqual(len(second_pro.calls), 1)
            self.assertEqual(stats["interrupted_call_recoveries"], 1)
            self.assertEqual(store.batch_row(batch.batch_id)["status"], "committed")
            interrupted = json.loads(
                (tmp_path / "product_writer_interrupted_calls.jsonl")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(interrupted["model"], "deepseek-v4-pro")
            self.assertEqual(interrupted["stage"], "reconciliation_pro_interrupted")

    def test_clean_hashed_failed_response_can_be_revalidated_without_api_call(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message("I prefer blue.")
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            response = empty_response(batch)

            class DurableFlash(FakeFlash):
                def complete(self, payload):
                    self.calls.append(dict(payload))
                    raw = v4._json(response)
                    return response, {
                        "status": "completed",
                        "physical_api_call": True,
                        "physical_api_calls": 1,
                        "http_status": 200,
                        "response_sha256": v4.sha256_text(raw),
                    }

            store = v4.V4BatchStore(tmp_path / "native_memory.sqlite3")
            graph_factory = FakeGraphFactory()
            first_flash = DurableFlash(lambda payload: response)
            first_writer = v4.V4BatchWriter(
                store=store,
                flash_client=first_flash,
                graph_factory=graph_factory,
                log_dir=tmp_path,
            )
            rows = [{
                "question_id": "test-qid",
                "session_id": "session-0",
                "messages": [{"role": "user", "content": message.content}],
            }]
            with mock.patch.object(
                v4,
                "validate_batch_response",
                side_effect=v4.ProductWriterError("old validator rejected response"),
            ):
                with self.assertRaisesRegex(v4.ProductWriterError, "old validator"):
                    first_writer.run(rows)

            # Historical requests must remain frozen even if live interaction
            # state changes before the recovery process starts.
            store.insert_interaction(
                interaction_id="interaction:late",
                scope_id=message.scope_id,
                session_id=message.session_id,
                message_id="late-message",
                index=0,
                role="user",
                interaction={
                    "interaction_type": "request",
                    "status": "open",
                    "intent": "late_request",
                    "evidence_quote": "late",
                },
            )

            second_flash = FakeFlash(lambda payload: self.fail("API must not be called"))
            second_writer = v4.V4BatchWriter(
                store=store,
                flash_client=second_flash,
                graph_factory=graph_factory,
                log_dir=tmp_path,
                revalidate_failed_raw_response=True,
            )
            stats = second_writer.run(rows)
            self.assertEqual(second_flash.calls, [])
            self.assertEqual(stats["resumed_batches"], 1)
            with closing(sqlite3.connect(store.path)) as connection:
                status, metadata_json = connection.execute(
                    "SELECT status,response_metadata_json FROM v4_batch_journal"
                ).fetchone()
            self.assertEqual(status, "committed")
            self.assertTrue(json.loads(metadata_json)["raw_response_revalidated"])
            recovery = json.loads(
                (tmp_path / "product_writer_revalidations.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(recovery["physical_api_calls"], 0)

    def test_failed_validated_batch_reuses_verified_historical_binding_without_api_call(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message("I prefer green.")
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            response = empty_response(batch)
            response["messages"][0]["assertions"] = [
                raw_assertion(
                    span_id="e0",
                    memory_type="preference",
                    entity_key="laptop.purchase",
                    attribute_key="color",
                    durability="durable",
                    relation="prefers",
                )
            ]
            validated = v4.validate_batch_response(response, batch)
            slot = v4._graph_slot_key(
                validated["messages"][0]["v3"]["assertions"][0]["canonical_key"]
            )
            graph_factory = FakeGraphFactory()
            backend = graph_factory.for_scope(message.scope_id)
            backend.leaves[slot] = [{
                "memory_id": "existing-blue",
                "value": "I prefer blue.",
                "evidence_quote": "I prefer blue.",
                "canonical_slot_key": slot,
                "durability": "durable",
                "record_state": "active",
                "metadata": {"durability": "durable"},
            }]
            rows = [{
                "question_id": "test-qid",
                "session_id": "session-0",
                "messages": [{"role": "user", "content": message.content}],
            }]
            store = v4.V4BatchStore(tmp_path / "native_memory.sqlite3")
            first_pro = FakePro()
            first_writer = v4.V4BatchWriter(
                store=store,
                flash_client=FakeFlash(lambda payload: response),
                pro_client=first_pro,
                graph_factory=graph_factory,
                log_dir=tmp_path,
            )
            with mock.patch.object(
                backend,
                "commit_message",
                side_effect=v4.ProductWriterError("simulated graph boundary failure"),
            ):
                with self.assertRaisesRegex(
                    v4.ProductWriterError, "graph boundary failure"
                ):
                    first_writer.run(rows)
            self.assertEqual(len(first_pro.calls), 1)
            self.assertEqual(store.batch_row(batch.batch_id)["status"], "validated")
            self.assertEqual(
                [job["status"] for job in store.reconciliation_jobs_for_batch(batch.batch_id)],
                ["completed"],
            )
            historical = backend.leaves[slot][0]
            historical["record_state"] = "superseded"
            historical["metadata"] = {
                **dict(historical["metadata"]),
                "superseded_reason": "v4_reconciliation_replace_current",
            }
            backend.leaves[slot].append({
                "memory_id": "partial-red",
                "value": "I prefer red.",
                "evidence_quote": "I prefer red.",
                "canonical_slot_key": slot,
                "durability": "durable",
                "record_state": "active",
                "metadata": {"durability": "durable"},
            })

            second_pro = FakePro()
            second_writer = v4.V4BatchWriter(
                store=store,
                flash_client=FakeFlash(lambda payload: self.fail("Flash must not be called")),
                pro_client=second_pro,
                graph_factory=graph_factory,
                log_dir=tmp_path,
                revalidate_failed_raw_response=True,
            )
            stats = second_writer.run(rows)
            self.assertEqual(second_pro.calls, [])
            self.assertEqual(stats["validated_batch_recoveries"], 0)
            self.assertEqual(stats["historical_binding_recoveries"], 0)
            self.assertEqual(store.batch_row(batch.batch_id)["status"], "committed")
            self.assertFalse(
                (tmp_path / "product_writer_validated_batch_recoveries.jsonl").exists()
            )

    def test_validated_batch_repairs_partial_graph_commit_without_api_call(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message("I prefer green.")
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            response = empty_response(batch)
            response["messages"][0]["assertions"] = [
                raw_assertion(
                    span_id="e0",
                    memory_type="preference",
                    entity_key="laptop.purchase",
                    attribute_key="color",
                    durability="durable",
                    relation="prefers",
                    claim_text="The user prefers green.",
                )
            ]
            validated = v4.validate_batch_response(response, batch)
            slot = v4._graph_slot_key(
                validated["messages"][0]["v3"]["assertions"][0]["canonical_key"]
            )
            graph_factory = FakeGraphFactory()
            backend = graph_factory.for_scope(message.scope_id)
            old = {
                "memory_id": "existing-blue",
                "value": "The user prefers blue.",
                "claim_text": "The user prefers blue.",
                "evidence_quote": "I prefer blue.",
                "canonical_slot_key": slot,
                "durability": "durable",
                "record_state": "active",
                "metadata": {"durability": "durable"},
            }
            backend.leaves[slot] = [old]
            rows = [{
                "question_id": "test-qid",
                "session_id": "session-0",
                "messages": [{"role": "user", "content": message.content}],
            }]
            store = v4.V4BatchStore(tmp_path / "native_memory.sqlite3")
            first_pro = FakePro("replace_current")
            first_writer = v4.V4BatchWriter(
                store=store,
                flash_client=FakeFlash(lambda payload: response),
                pro_client=first_pro,
                graph_factory=graph_factory,
                log_dir=tmp_path,
            )

            def persist_then_terminate(**kwargs):
                assertion = dict(kwargs["extraction"]["assertions"][0])
                old["record_state"] = "superseded"
                old["metadata"] = {
                    **dict(old["metadata"]),
                    "superseded_reason": "v4_reconciliation_replace_current",
                }
                backend.leaves[slot].append({
                    "memory_id": "partial-green",
                    "value": assertion["claim_text"],
                    "claim_text": assertion["claim_text"],
                    "evidence_quote": assertion["evidence_quote"],
                    "canonical_slot_key": slot,
                    "durability": "durable",
                    "record_state": "superseded",
                    "metadata": {
                        "durability": "durable",
                        "source_record_id": kwargs["source_record_id"],
                        "message_id": message.message_id,
                        "llm_write_proposal_index": 0,
                    },
                })
                raise KeyboardInterrupt("simulated process termination")

            with mock.patch.object(
                backend, "commit_message", side_effect=persist_then_terminate
            ):
                with self.assertRaisesRegex(KeyboardInterrupt, "process termination"):
                    first_writer.run(rows)
            self.assertEqual(store.batch_row(batch.batch_id)["status"], "validated")
            self.assertEqual(len(first_pro.calls), 1)

            second_pro = FakePro()
            second_writer = v4.V4BatchWriter(
                store=store,
                flash_client=FakeFlash(lambda payload: self.fail("Flash must not be called")),
                pro_client=second_pro,
                graph_factory=graph_factory,
                log_dir=tmp_path,
            )
            second_writer.run(rows)

            self.assertEqual(second_pro.calls, [])
            self.assertEqual(store.batch_row(batch.batch_id)["status"], "committed")
            journal = store.prepare_message_commit(
                batch, message, validated["messages"][0]
            )
            self.assertEqual(journal["status"], "committed")

    def test_failed_validated_batch_recovers_one_interrupted_pro_with_same_model(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message("I prefer green.")
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            response = empty_response(batch)
            response["messages"][0]["assertions"] = [
                raw_assertion(
                    span_id="e0",
                    memory_type="preference",
                    entity_key="laptop.purchase",
                    attribute_key="color",
                    durability="durable",
                    relation="prefers",
                )
            ]
            validated = v4.validate_batch_response(response, batch)
            slot = v4._graph_slot_key(
                validated["messages"][0]["v3"]["assertions"][0]["canonical_key"]
            )
            graph_factory = FakeGraphFactory()
            backend = graph_factory.for_scope(message.scope_id)
            backend.leaves[slot] = [{
                "memory_id": "existing-blue",
                "value": "I prefer blue.",
                "evidence_quote": "I prefer blue.",
                "canonical_slot_key": slot,
                "durability": "durable",
                "record_state": "active",
                "metadata": {"durability": "durable"},
            }]
            rows = [{
                "question_id": "test-qid",
                "session_id": "session-0",
                "messages": [{"role": "user", "content": message.content}],
            }]
            store = v4.V4BatchStore(tmp_path / "native_memory.sqlite3")
            first_writer = v4.V4BatchWriter(
                store=store,
                flash_client=FakeFlash(lambda payload: response),
                pro_client=FakePro(),
                graph_factory=graph_factory,
                log_dir=tmp_path,
            )
            original_start = store.start_reconciliation_job

            def fail_after_start(job_id):
                original_start(job_id)
                raise v4.ProductWriterError("simulated process loss after Pro start")

            with mock.patch.object(
                store, "start_reconciliation_job", side_effect=fail_after_start
            ):
                with self.assertRaisesRegex(v4.ProductWriterError, "process loss"):
                    first_writer.run(rows)
            job = store.reconciliation_jobs_for_batch(batch.batch_id)[0]
            self.assertEqual(store.batch_row(batch.batch_id)["status"], "validated")
            self.assertEqual(job["status"], "pro_started")

            second_pro = FakePro()
            second_writer = v4.V4BatchWriter(
                store=store,
                flash_client=FakeFlash(lambda payload: self.fail("Flash must not be called")),
                pro_client=second_pro,
                graph_factory=graph_factory,
                log_dir=tmp_path,
                revalidate_failed_raw_response=True,
                recover_interrupted_api_calls=True,
            )
            stats = second_writer.run(rows)
            self.assertEqual(len(second_pro.calls), 1)
            self.assertEqual(stats["interrupted_call_recoveries"], 1)
            self.assertEqual(stats["validated_batch_recoveries"], 0)
            self.assertEqual(store.batch_row(batch.batch_id)["status"], "committed")
            interrupted = json.loads(
                (tmp_path / "product_writer_interrupted_calls.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(interrupted["model"], "deepseek-v4-pro")
            self.assertEqual(interrupted["replacement_model"], "deepseek-v4-pro")
            self.assertFalse(
                (tmp_path / "product_writer_validated_batch_recoveries.jsonl").exists()
            )

    def test_keep_parallel_recovery_rebinds_one_unique_active_semantic_equivalent(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message("I prefer green.")
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            response = empty_response(batch)
            response["messages"][0]["assertions"] = [
                raw_assertion(
                    span_id="e0",
                    memory_type="preference",
                    entity_key="laptop.purchase",
                    attribute_key="color",
                    durability="durable",
                    relation="prefers",
                )
            ]
            validated = v4.validate_batch_response(response, batch)
            slot = v4._graph_slot_key(
                validated["messages"][0]["v3"]["assertions"][0]["canonical_key"]
            )
            graph_factory = FakeGraphFactory()
            backend = graph_factory.for_scope(message.scope_id)
            backend.leaves[slot] = [{
                "memory_id": "existing-blue-old",
                "value": "I prefer blue.",
                "evidence_quote": "I prefer blue.",
                "canonical_slot_key": slot,
                "durability": "durable",
                "record_state": "active",
                "metadata": {"durability": "durable"},
            }]
            rows = [{
                "question_id": "test-qid",
                "session_id": "session-0",
                "messages": [{"role": "user", "content": message.content}],
            }]
            store = v4.V4BatchStore(tmp_path / "native_memory.sqlite3")
            first_pro = FakePro(decision="keep_parallel")
            first_writer = v4.V4BatchWriter(
                store=store,
                flash_client=FakeFlash(lambda payload: response),
                pro_client=first_pro,
                graph_factory=graph_factory,
                log_dir=tmp_path,
            )
            with mock.patch.object(
                backend,
                "commit_message",
                side_effect=v4.ProductWriterError("simulated graph boundary failure"),
            ):
                with self.assertRaisesRegex(
                    v4.ProductWriterError, "graph boundary failure"
                ):
                    first_writer.run(rows)
            old = backend.leaves[slot][0]
            old["record_state"] = "superseded"
            old["metadata"] = {
                **dict(old["metadata"]),
                "superseded_reason": "v4_reconciliation_replace_current",
            }
            backend.leaves[slot].append({
                **dict(old),
                "memory_id": "existing-blue-active-equivalent",
                "record_state": "active",
                "metadata": {"durability": "durable"},
            })

            second_pro = FakePro()
            second_writer = v4.V4BatchWriter(
                store=store,
                flash_client=FakeFlash(lambda payload: self.fail("Flash must not be called")),
                pro_client=second_pro,
                graph_factory=graph_factory,
                log_dir=tmp_path,
                revalidate_failed_raw_response=True,
            )
            stats = second_writer.run(rows)
            self.assertEqual(second_pro.calls, [])
            self.assertEqual(stats["historical_binding_recoveries"], 0)
            self.assertEqual(store.batch_row(batch.batch_id)["status"], "committed")
            self.assertFalse(
                (tmp_path / "product_writer_historical_binding_recoveries.jsonl").exists()
            )

    def test_invalid_reconciliation_response_quarantines_only_its_assertion(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message("I prefer green.")
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            response = empty_response(batch)
            response["messages"][0]["assertions"] = [
                raw_assertion(
                    span_id="e0",
                    memory_type="preference",
                    entity_key="laptop.purchase",
                    attribute_key="color",
                    durability="durable",
                    relation="prefers",
                )
            ]
            validated = v4.validate_batch_response(response, batch)
            slot = v4._graph_slot_key(
                validated["messages"][0]["v3"]["assertions"][0]["canonical_key"]
            )
            graph_factory = FakeGraphFactory()
            backend = graph_factory.for_scope(message.scope_id)
            backend.leaves[slot] = [{
                "memory_id": "existing-blue",
                "value": "I prefer blue.",
                "evidence_quote": "I prefer blue.",
                "canonical_slot_key": slot,
                "durability": "durable",
                "record_state": "active",
                "metadata": {"durability": "durable"},
            }]

            class InvalidPro:
                def __init__(self):
                    self.calls = []

                def reconcile(self, payload):
                    self.calls.append(dict(payload))
                    raw = {
                        "slot_decision": "unsupported",
                        "selected_memory_id": "",
                        "decision": "unsupported",
                    }
                    serialized = v4._json(raw)
                    return raw, {
                        "status": "completed",
                        "physical_api_call": True,
                        "physical_api_calls": 1,
                        "http_status": 200,
                        "response_sha256": v4.sha256_text(serialized),
                    }

            rows = [{
                "question_id": "test-qid",
                "session_id": "session-0",
                "messages": [{"role": "user", "content": message.content}],
            }]
            store = v4.V4BatchStore(tmp_path / "native_memory.sqlite3")
            pro = InvalidPro()
            writer = v4.V4BatchWriter(
                store=store,
                flash_client=FakeFlash(lambda payload: response),
                pro_client=pro,
                graph_factory=graph_factory,
                log_dir=tmp_path,
            )
            stats = writer.run(rows)

            self.assertEqual(len(pro.calls), 1)
            self.assertEqual(stats["reconciliation_response_quarantines"], 1)
            self.assertEqual(store.batch_row(batch.batch_id)["status"], "committed")
            self.assertEqual(len(backend.commits), 1)
            self.assertEqual(backend.commits[0]["decisions"], {0: "quarantine"})
            quarantine = json.loads(
                (tmp_path / "product_writer_reconciliation_quarantines.jsonl")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(quarantine["physical_api_calls"], 1)
            self.assertIn("unsupported", quarantine["error"])

    def test_duplicate_removal_remaps_durability_decisions_and_provenance(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message("I prefer blue. I live in Paris.")
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            response = empty_response(batch)
            response["messages"][0]["assertions"] = [
                raw_assertion(
                    span_id="e1",
                    memory_type="preference",
                    entity_key="laptop.purchase",
                    attribute_key="color",
                    durability="durable",
                    relation="prefers",
                ),
                raw_assertion(
                    span_id="e2",
                    memory_type="state",
                    entity_key="residence",
                    attribute_key="city",
                    durability="episodic",
                    relation="lives_in",
                ),
            ]
            validated = v4.validate_batch_response(response, batch)
            first = validated["messages"][0]["v3"]["assertions"][0]
            graph_factory = FakeGraphFactory()
            backend = graph_factory.for_scope(message.scope_id)
            first_slot = v4._graph_slot_key(first["canonical_key"])
            backend.leaves[first_slot] = [
                {
                    "memory_id": "existing-blue",
                    "value": first["claim_text"],
                    "claim_text": first["claim_text"],
                    "evidence_quote": first["evidence_quote"],
                    "canonical_slot_key": first_slot,
                    "durability": "durable",
                    "record_state": "active",
                    "metadata": {"durability": "durable"},
                }
            ]
            flash = FakeFlash(lambda payload: response)
            writer = v4.V4BatchWriter(
                store=v4.V4BatchStore(tmp_path / "native_memory.sqlite3"),
                flash_client=flash,
                graph_factory=graph_factory,
                log_dir=tmp_path,
            )
            stats = writer.run(
                [{"question_id": "test-qid", "session_id": "session-0", "messages": [{"role": "user", "timestamp": message.timestamp, "content": message.content}]}]
            )
            self.assertEqual(stats["fast_assertion_leaves"], 1)
            self.assertEqual(len(backend.commits), 1)
            commit = backend.commits[0]
            self.assertEqual(len(commit["extraction"]["assertions"]), 1)
            self.assertEqual(commit["durabilities"], ["episodic"])
            self.assertEqual(commit["decisions"], {0: "insert"})
            self.assertEqual(list(commit["current_by_index"]), [0])
            self.assertEqual(backend.provenance[0]["leaf_id"], "existing-blue")
            self.assertEqual(
                backend.provenance[0]["source_record_id"],
                f"source:{message.scope_id}:{message.message_id}",
            )

    def test_semantic_record_separates_atomic_claim_from_exact_evidence(self):
        message = source_message("I live in Paris.")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        response["messages"][0]["assertions"] = [
            raw_assertion(
                span_id="e0",
                memory_type="state",
                entity_key="residence",
                attribute_key="city",
                durability="durable",
                relation="lives_in",
                claim_text="The user lives in Paris.",
            )
        ]
        assertion = v4.validate_batch_response(response, batch)["messages"][0]["v3"]["assertions"][0]
        records, _ = v4.build_graph_records(
            SimpleNamespace,
            scope_id=message.scope_id,
            turn_index=1,
            session_id=message.session_id,
            session_index=0,
            message_id=message.message_id,
            message_index=0,
            date="2026-07-11",
            timestamp=message.timestamp,
            role=message.role,
            content=message.content,
            extraction={"assertions": [assertion], "interactions": [], "resolutions": []},
        )
        semantic = next(record for record in records if record.metadata["content_variant"] == "product_semantic_memory")
        self.assertEqual(semantic.value, "The user lives in Paris.")
        self.assertEqual(semantic.metadata["evidence_quote"], "I live in Paris.")
        self.assertEqual(semantic.metadata["raw_content"], "I live in Paris.")

    def test_semantic_record_preserves_noncanonical_source_whitespace_exactly(self):
        content = "I  prefer\u00a0green."
        message = source_message(content)
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        response["messages"][0]["assertions"] = [
            raw_assertion(
                span_id="e0",
                memory_type="preference",
                entity_key="color",
                attribute_key="favorite",
                durability="durable",
                relation="prefers",
                claim_text="The user prefers green.",
            )
        ]
        assertion = v4.validate_batch_response(response, batch)["messages"][0]["v3"][
            "assertions"
        ][0]
        self.assertEqual(assertion["evidence_quote"], content)
        records, _ = v4.build_graph_records(
            SimpleNamespace,
            scope_id=message.scope_id,
            turn_index=1,
            session_id=message.session_id,
            session_index=0,
            message_id=message.message_id,
            message_index=0,
            date="2026-07-11",
            timestamp=message.timestamp,
            role=message.role,
            content=message.content,
            extraction={
                "assertions": [assertion],
                "interactions": [],
                "resolutions": [],
            },
        )
        semantic = next(
            record
            for record in records
            if record.metadata["content_variant"] == "product_semantic_memory"
        )
        start = semantic.metadata["evidence_char_start"]
        end = semantic.metadata["evidence_char_end"]
        self.assertEqual(semantic.metadata["evidence_quote"], content)
        self.assertEqual(content[start:end], content)

    def test_same_grounded_claim_under_two_slots_is_merged_before_commit(self):
        message = source_message("I live in Paris.")
        batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
        response = empty_response(batch)
        first = raw_assertion(
            span_id="e0",
            memory_type="state",
            entity_key="residence",
            attribute_key="city",
            durability="durable",
            relation="lives_in",
            claim_text="The user lives in Paris.",
        )
        second = {
            **first,
            "entity_key": "user.location",
            "attribute_key": "home_city",
        }
        response["messages"][0]["assertions"] = [first, second]
        validated = v4.validate_batch_response(response, batch)["messages"][0]["v3"]
        self.assertEqual(len(validated["assertions"]), 1)
        self.assertIn(
            "duplicate_atomic_claim_merged",
            [warning["code"] for warning in validated["validation_warnings"]],
        )

    def test_merge_support_adds_provenance_without_creating_semantic_leaf(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message("I live in Paris.")
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            response = empty_response(batch)
            response["messages"][0]["assertions"] = [
                raw_assertion(
                    span_id="e0",
                    memory_type="state",
                    entity_key="residence",
                    attribute_key="city",
                    durability="durable",
                    relation="lives_in",
                    claim_text="The user lives in Paris.",
                )
            ]
            assertion = v4.validate_batch_response(response, batch)["messages"][0]["v3"]["assertions"][0]
            graph_factory = FakeGraphFactory()
            backend = graph_factory.for_scope(message.scope_id)
            slot = v4._graph_slot_key(assertion["canonical_key"])
            backend.leaves[slot] = [{
                "memory_id": "existing-paris",
                "value": "The user resides in Paris.",
                "claim_text": "The user resides in Paris.",
                "evidence_quote": "Earlier I said I reside in Paris.",
                "canonical_slot_key": slot,
                "durability": "durable",
                "record_state": "active",
                "metadata": {"durability": "durable"},
            }]
            writer = v4.V4BatchWriter(
                store=v4.V4BatchStore(tmp_path / "native_memory.sqlite3"),
                flash_client=FakeFlash(lambda payload: response),
                pro_client=FakePro("merge_support"),
                graph_factory=graph_factory,
                log_dir=tmp_path,
            )
            stats = writer.run([{
                "question_id": "test-qid",
                "session_id": "session-0",
                "messages": [{"role": "user", "timestamp": message.timestamp, "content": message.content}],
            }])
            self.assertEqual(stats["fast_assertion_leaves"], 0)
            self.assertEqual(backend.provenance[0]["leaf_id"], "existing-paris")
            self.assertEqual(backend.commits[0]["extraction"]["assertions"], [])

    def test_same_real_slot_calls_pro_once_and_does_not_send_full_leaf_metadata(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message("I prefer green.")
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            response = empty_response(batch)
            response["messages"][0]["assertions"] = [
                raw_assertion(
                    span_id="e0",
                    memory_type="preference",
                    entity_key="laptop.purchase",
                    attribute_key="color",
                    durability="durable",
                    relation="prefers",
                )
            ]
            validated = v4.validate_batch_response(response, batch)
            assertion = validated["messages"][0]["v3"]["assertions"][0]
            slot = v4._graph_slot_key(assertion["canonical_key"])
            graph_factory = FakeGraphFactory()
            backend = graph_factory.for_scope(message.scope_id)
            backend.leaves[slot] = [
                {
                    "memory_id": "existing-blue",
                    "value": "I prefer blue.",
                    "evidence_quote": "I prefer blue.",
                    "canonical_slot_key": slot,
                    "durability": "durable",
                    "record_state": "active",
                    "metadata": {
                        "durability": "durable",
                        "target_status": "current",
                        "polarity": "positive",
                        "source_record_id": "source-old",
                        "private_blob": "must-not-leave-controller",
                    },
                }
            ]
            pro = FakePro()
            writer = v4.V4BatchWriter(
                store=v4.V4BatchStore(tmp_path / "native_memory.sqlite3"),
                flash_client=FakeFlash(lambda payload: response),
                pro_client=pro,
                graph_factory=graph_factory,
                log_dir=tmp_path,
            )
            writer.run(
                [{"question_id": "test-qid", "session_id": "session-0", "messages": [{"role": "user", "timestamp": message.timestamp, "content": message.content}]}]
            )
            self.assertEqual(len(pro.calls), 1)
            self.assertEqual(pro.calls[0]["canonical_slot_key"], slot)
            self.assertNotIn("private_blob", json.dumps(pro.calls[0]))
            self.assertEqual(backend.commits[0]["decisions"], {0: "replace_current"})
            raw_calls = [
                json.loads(line)
                for line in (tmp_path / "product_writer_raw_responses.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                [item["stage"] for item in raw_calls],
                ["batch_flash", "reconciliation_pro"],
            )

    def test_exact_slot_keep_proposed_insert_is_normalized_to_parallel_binding(self):
        current = [
            {
                "memory_id": "parallel-old",
                "record_state": "parallel_active",
                "turn_index": 20,
            },
            {
                "memory_id": "active-head",
                "record_state": "active",
                "turn_index": 10,
            },
        ]
        result = v4.V4BatchWriter._validate_reconciliation_response(
            {
                "slot_decision": "keep_proposed",
                "selected_memory_id": "",
                "decision": "insert",
            },
            current_cited=current,
            exact_slot_match=True,
            path="reconciliation[test]",
        )
        self.assertEqual(
            result,
            {
                "slot_decision": "bind_existing",
                "selected_memory_id": "active-head",
                "decision": "keep_parallel",
            },
        )

    def test_misplaced_parallel_slot_enum_is_normalized_from_valid_candidate(self):
        current = [{"memory_id": "existing", "record_state": "active"}]
        result = v4.V4BatchWriter._validate_reconciliation_response(
            {
                "slot_decision": "keep_parallel",
                "selected_memory_id": "existing",
                "decision": "keep_parallel",
            },
            current_cited=current,
            exact_slot_match=False,
            path="reconciliation[test]",
        )
        self.assertEqual(
            result,
            {
                "slot_decision": "bind_existing",
                "selected_memory_id": "existing",
                "decision": "keep_parallel",
            },
        )
        with self.assertRaisesRegex(v4.ProductWriterError, "slot_decision"):
            v4.V4BatchWriter._validate_reconciliation_response(
                {
                    "slot_decision": "keep_parallel",
                    "selected_memory_id": "missing",
                    "decision": "keep_parallel",
                },
                current_cited=current,
                exact_slot_match=False,
                path="reconciliation[test]",
            )

    def test_reconciliation_request_replay_freezes_semantics_not_candidate_metadata(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            store = v4.V4BatchStore(Path(raw_dir) / "native_memory.sqlite3")
            request = {
                "schema_version": v4.RECONCILIATION_SCHEMA_VERSION,
                "candidate_selector_version": v4.CANDIDATE_SELECTOR_VERSION,
                "canonical_slot_key": "memory.user.color",
                "message_id": "s000_m000",
                "new_cited_assertion": {"evidence_quote": "I prefer green."},
                "candidate_cited_leaves": [
                    {"memory_id": "blue", "record_state": "active"}
                ],
                "exact_slot_match": True,
            }
            kwargs = {
                "job_id": "job",
                "scope_id": "scope",
                "batch_id": "batch",
                "message_id": "s000_m000",
                "slot": "memory.user.color",
                "assertion_index": 0,
            }
            store.create_reconciliation_job(**kwargs, request=request)
            drifted = {
                **request,
                "candidate_cited_leaves": [
                    {
                        "memory_id": "blue",
                        "record_state": "parallel_active",
                        "turn_index": 10,
                    }
                ],
            }
            store.create_reconciliation_job(**kwargs, request=drifted)
            changed = {
                **drifted,
                "new_cited_assertion": {"evidence_quote": "I prefer red."},
            }
            with self.assertRaisesRegex(v4.ProductWriterError, "new_cited_assertion"):
                store.create_reconciliation_job(**kwargs, request=changed)

    def test_compact_candidate_binding_stabilizes_drifted_cross_session_slot(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message(
                "Remember when I got pre-approved for $400,000 from Wells Fargo?"
            )
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            response = empty_response(batch)
            response["messages"][0]["assertions"] = [{
                **raw_assertion(
                    span_id="e0",
                    memory_type="event",
                    entity_key="finance",
                    attribute_key="pre_approved_mortgage",
                    durability="episodic",
                    relation="experienced",
                ),
                "operation": "append",
                "temporal_status": "past",
            }]
            validated = v4.validate_batch_response(response, batch)
            proposed = validated["messages"][0]["v3"]["assertions"][0]
            old_slot = "memory.user.mortgage.event.pre.approval"
            old_leaf = {
                "memory_id": "old-preapproval",
                "value": "I got pre-approved for $350,000 from Wells Fargo.",
                "evidence_quote": "I got pre-approved for $350,000 from Wells Fargo.",
                "canonical_slot_key": old_slot,
                "durability": "episodic",
                "record_state": "active",
                "metadata": {
                    "canonical_slot_key": old_slot,
                    "entity_key": "mortgage",
                    "graph_entity_key": "mortgage",
                    "attribute_key": "pre.approval",
                    "memory_type": "event",
                    "memory_family": "event",
                    "durability": "episodic",
                },
            }
            graph_factory = FakeGraphFactory()
            backend = graph_factory.for_scope(message.scope_id)
            backend.leaves[old_slot] = [old_leaf]
            backend.candidate_outputs[proposed["canonical_key"]] = [old_leaf]
            pro = FakePro("replace_current")
            writer = v4.V4BatchWriter(
                store=v4.V4BatchStore(tmp_path / "native_memory.sqlite3"),
                flash_client=FakeFlash(lambda payload: response),
                pro_client=pro,
                graph_factory=graph_factory,
            )
            writer.run(
                [{"question_id": "test-qid", "session_id": "session-0", "messages": [{"role": "user", "content": message.content}]}]
            )
            self.assertEqual(len(pro.calls), 1)
            self.assertEqual(len(pro.calls[0]["candidate_cited_leaves"]), 1)
            committed = backend.commits[0]
            assertion = committed["extraction"]["assertions"][0]
            self.assertEqual(assertion["canonical_key"], old_slot.removeprefix("memory."))
            self.assertEqual(assertion["graph_entity_key"], "mortgage")
            self.assertEqual(assertion["operation"], "replace")
            self.assertEqual(committed["decisions"], {0: "replace_current"})

    def test_persisted_reconciliation_precedes_proposed_slot_duplicate_shortcut(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message(
                "Remember when I got pre-approved for $400,000 from Wells Fargo?"
            )
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            response = empty_response(batch)
            response["messages"][0]["assertions"] = [{
                **raw_assertion(
                    span_id="e0",
                    memory_type="event",
                    entity_key="finance",
                    attribute_key="pre_approved_mortgage",
                    durability="episodic",
                    relation="experienced",
                ),
                "operation": "append",
                "temporal_status": "past",
            }]
            validated = v4.validate_batch_response(response, batch)
            proposed = validated["messages"][0]["v3"]["assertions"][0]
            proposed_slot = v4._graph_slot_key(proposed["canonical_key"])
            old_slot = "memory.user.mortgage.event.pre.approval"
            old_leaf = {
                "memory_id": "old-preapproval",
                "value": "I got pre-approved for $350,000 from Wells Fargo.",
                "evidence_quote": "I got pre-approved for $350,000 from Wells Fargo.",
                "canonical_slot_key": old_slot,
                "durability": "episodic",
                "record_state": "active",
                "metadata": {
                    "canonical_slot_key": old_slot,
                    "entity_key": "mortgage",
                    "graph_entity_key": "mortgage",
                    "attribute_key": "pre.approval",
                    "memory_type": "event",
                    "memory_family": "event",
                    "durability": "episodic",
                },
            }
            graph_factory = FakeGraphFactory()
            backend = graph_factory.for_scope(message.scope_id)
            backend.leaves[old_slot] = [old_leaf]
            backend.candidate_outputs[proposed["canonical_key"]] = [old_leaf]
            rows = [{
                "question_id": "test-qid",
                "session_id": "session-0",
                "messages": [{"role": "user", "content": message.content}],
            }]
            store = v4.V4BatchStore(tmp_path / "native_memory.sqlite3")
            first_pro = FakePro("replace_current")
            first_writer = v4.V4BatchWriter(
                store=store,
                flash_client=FakeFlash(lambda payload: response),
                pro_client=first_pro,
                graph_factory=graph_factory,
                log_dir=tmp_path,
            )
            with mock.patch.object(
                backend,
                "commit_message",
                side_effect=v4.ProductWriterError("simulated graph boundary failure"),
            ):
                with self.assertRaisesRegex(
                    v4.ProductWriterError, "graph boundary failure"
                ):
                    first_writer.run(rows)
            self.assertEqual(len(first_pro.calls), 1)
            backend.leaves[proposed_slot] = [{
                "memory_id": "proposed-slot-duplicate",
                "value": message.content,
                "evidence_quote": message.content,
                "canonical_slot_key": proposed_slot,
                "durability": "episodic",
                "record_state": "active",
                "metadata": {"durability": "episodic"},
            }]

            second_pro = FakePro()
            second_writer = v4.V4BatchWriter(
                store=store,
                flash_client=FakeFlash(lambda payload: self.fail("Flash must not be called")),
                pro_client=second_pro,
                graph_factory=graph_factory,
                log_dir=tmp_path,
                revalidate_failed_raw_response=True,
            )
            second_writer.run(rows)
            self.assertEqual(second_pro.calls, [])
            self.assertEqual(store.batch_row(batch.batch_id)["status"], "committed")
            self.assertEqual(len(backend.commits), 1)
            committed_assertion = backend.commits[0]["extraction"]["assertions"][0]
            self.assertEqual(
                committed_assertion["canonical_key"],
                old_slot.removeprefix("memory."),
            )
            self.assertEqual(backend.commits[0]["decisions"], {0: "replace_current"})

    def test_failed_physical_call_metadata_is_durable_and_cost_visible(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            store = v4.V4BatchStore(tmp_path / "native_memory.sqlite3")

            class FailingPhysicalFlash:
                def complete(self, payload):
                    raise v4.BatchAPIError(
                        "HTTP 503",
                        metadata={
                            "physical_call_id": "flash-failed-1",
                            "physical_api_call": True,
                            "model": "deepseek-v4-flash",
                            "stage": "batch_flash",
                            "status": "http_error",
                        },
                    )

            writer = v4.V4BatchWriter(
                store=store,
                flash_client=FailingPhysicalFlash(),
                graph_factory=FakeGraphFactory(),
                log_dir=tmp_path,
            )
            rows = [{"question_id": "test-qid", "session_id": "session-0", "messages": [{"role": "user", "content": "I prefer blue."}]}]
            with self.assertRaisesRegex(v4.BatchAPIError, "503"):
                writer.run(rows)
            with closing(sqlite3.connect(store.path)) as connection:
                metadata = json.loads(
                    connection.execute(
                        "SELECT response_metadata_json FROM v4_batch_journal"
                    ).fetchone()[0]
                )
            self.assertEqual(metadata["physical_call_id"], "flash-failed-1")
            logged = json.loads(
                (tmp_path / "product_writer_calls.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(logged["metadata"]["physical_call_id"], "flash-failed-1")

    def test_committed_resume_strictly_verifies_real_source(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            message = source_message("I prefer blue.")
            batch = v4.SourceBatch(message.scope_id, message.session_id, 0, 0, (message,))
            graph_factory = FakeGraphFactory()
            flash = FakeFlash(lambda payload: empty_response(batch))
            writer = v4.V4BatchWriter(
                store=v4.V4BatchStore(tmp_path / "native_memory.sqlite3"),
                flash_client=flash,
                graph_factory=graph_factory,
                log_dir=tmp_path,
            )
            rows = [{"question_id": "test-qid", "session_id": "session-0", "messages": [{"role": "user", "content": message.content}]}]
            writer.run(rows)
            backend = graph_factory.for_scope(message.scope_id)
            source_record_id = next(iter(backend.sources))
            backend.sources[source_record_id]["status"] = "pending"
            stats = writer.run(rows)
            self.assertEqual(backend.sources[source_record_id]["status"], "enriched")
            self.assertEqual(stats["committed_source_status_repairs"], 1)
            repair = json.loads(
                (tmp_path / "product_writer_committed_source_repairs.jsonl")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(repair["physical_api_calls"], 0)
            self.assertEqual(repair["source_record_ids"], [source_record_id])
            backend.sources.clear()
            with self.assertRaisesRegex(v4.ProductWriterError, "source record is missing"):
                writer.run(rows)
            self.assertEqual(len(flash.calls), 1)

    def test_graph_transaction_hook_rolls_back_graph_and_hook_together(self):
        code_root = Path(
            os.environ.get(
                "TMCRA_TEST_REAL_REPO",
                str(
                    Path(__file__).resolve().parent
                    / "tmcra_v112_actionunit_fullchain_20260602_164853"
                    / "tmcra_code"
                ),
            )
        )
        if not (code_root / "experiments" / "replacement" / "memory_graph.py").is_file():
            self.skipTest("real graph repository is unavailable")
        if str(code_root) not in sys.path:
            sys.path.insert(0, str(code_root))
        from experiments.replacement.memory_graph import (
            SQLiteSessionMemoryStore,
            SessionMemoryGraphV2,
            SessionMemoryRecordV2,
        )

        with tempfile.TemporaryDirectory() as raw_dir:
            database = Path(raw_dir) / "native_memory.sqlite3"
            store = SQLiteSessionMemoryStore(database)
            graph = SessionMemoryGraphV2()
            graph.add_records(
                [
                    SessionMemoryRecordV2(
                        memory_id="memory:test",
                        category="fact",
                        slot_key="memory.user.fact.test",
                        value="atomic",
                        relation="is",
                    )
                ]
            )

            def fail_hook(connection):
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS commit_probe(id TEXT PRIMARY KEY)"
                )
                connection.execute("INSERT INTO commit_probe VALUES ('message-1')")
                raise RuntimeError("crash before transaction commit")

            with self.assertRaisesRegex(RuntimeError, "before transaction commit"):
                store.save_graph("scope:test", graph, transaction_hook=fail_hook)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM records WHERE scope_id='scope:test'"
                    ).fetchone()[0],
                    0,
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT name FROM sqlite_master WHERE name='commit_probe'"
                    ).fetchone()
                )

            def commit_hook(connection):
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS commit_probe(id TEXT PRIMARY KEY)"
                )
                connection.execute("INSERT INTO commit_probe VALUES ('message-1')")

            store.save_graph("scope:test", graph, transaction_hook=commit_hook)
            with closing(sqlite3.connect(database)) as connection:
                self.assertGreater(
                    connection.execute(
                        "SELECT COUNT(*) FROM records WHERE scope_id='scope:test'"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM commit_probe").fetchone()[0],
                    1,
                )

    def test_message_commit_journal_rolls_back_all_local_side_effects(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            store = v4.V4BatchStore(Path(raw_dir) / "native_memory.sqlite3")
            message = source_message("Remember this.")
            batch = v4.SourceBatch(
                message.scope_id, message.session_id, 0, 0, (message,)
            )
            request = v4.build_batch_request(batch, [])
            store.prepare(batch, request)
            store.set_source_record(
                message.scope_id, message.message_id, "source:test", 1
            )
            response_message = empty_response(batch)["messages"][0]
            journal = store.prepare_message_commit(
                batch, message, response_message
            )
            commit_id = str(journal["commit_id"])
            store.freeze_message_commit_plan(
                commit_id,
                {
                    "schema_version": "tmcra.v4.message-commit-plan.1",
                    "batch_id": batch.batch_id,
                    "message_id": message.message_id,
                    "source_record_id": "source:test",
                },
            )
            interaction = {
                "interaction_type": "question",
                "intent": "request_information",
                "status": "open",
                "about": [],
                "evidence_span_id": "e0",
                "evidence_quote": "Remember this.",
                "evidence_char_start": 0,
                "evidence_char_end": len(message.content),
            }
            connection = store._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                store.finalize_message_commit(
                    connection,
                    commit_id=commit_id,
                    batch=batch,
                    message=message,
                    source_record_id="source:test",
                    interactions=[interaction],
                    resolutions=[],
                    semantic_committed=0,
                )
                raise RuntimeError("crash before commit")
            except RuntimeError:
                connection.rollback()
            finally:
                connection.close()
            with closing(store._connect()) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM v4_message_commit_journal WHERE commit_id=?",
                        (commit_id,),
                    ).fetchone()[0],
                    "prepared",
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM v4_interactions").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM v4_source_journal WHERE scope_id=? AND message_id=?",
                        (message.scope_id, message.message_id),
                    ).fetchone()[0],
                    "pending",
                )

            with closing(store._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                store.finalize_message_commit(
                    connection,
                    commit_id=commit_id,
                    batch=batch,
                    message=message,
                    source_record_id="source:test",
                    interactions=[interaction],
                    resolutions=[],
                    semantic_committed=0,
                )
                connection.commit()
            with closing(store._connect()) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM v4_message_commit_journal WHERE commit_id=?",
                        (commit_id,),
                    ).fetchone()[0],
                    "committed",
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM v4_interactions").fetchone()[0],
                    1,
                )


if __name__ == "__main__":
    unittest.main()
