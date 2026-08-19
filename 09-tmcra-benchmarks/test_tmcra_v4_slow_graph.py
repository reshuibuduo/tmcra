from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import tmcra_v4_slow_graph as slow_graph


@dataclass
class Record:
    memory_id: str
    category: str
    slot_key: str
    value: str
    relation: str
    anchor_concepts: list[str]
    evidence_anchors: list[str]
    salience: float
    confidence: float
    source_kind: str
    turn_index: int
    state: str
    supersedes: list[str]
    metadata: dict

    def to_dict(self):
        return self.__dict__.copy()


@dataclass
class Edge:
    edge_id: str
    source_memory_id: str
    target_memory_id: str
    edge_type: str
    score: float
    model_score: float
    evidence_turn: int
    evidence: str
    metadata: dict


class RecordingClient:
    def __init__(self, patch):
        self.patch = json.loads(json.dumps(patch))
        for operation in self.patch.get("operations", []):
            if (
                operation.get("action") not in {"noop", "escalate"}
                and not str(operation.get("summary") or "").strip()
            ):
                operation["summary"] = " ".join(
                    str(claim.get("text") or "").strip()
                    for claim in operation.get("claims", [])
                    if isinstance(claim, dict)
                )
        self.calls = []
        self.last_call_metadata = {}

    def propose(self, region, capsules):
        self.calls.append((region, capsules))
        self.last_call_metadata = {
            "physical_call_id": "physical-test-call",
            "status": "completed",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "cache_read_input_tokens": 2,
                "total_tokens": 14,
            },
            "cost_audit": {"estimated_cost": 0.25},
        }
        return self.patch


class SummarylessRecordingClient:
    def __init__(self, patch):
        self.patch = json.loads(json.dumps(patch))
        self.calls = []
        self.last_call_metadata = {}

    def propose(self, region, capsules):
        self.calls.append((region, capsules))
        self.last_call_metadata = {
            "physical_call_id": "physical-summaryless-test-call",
            "status": "completed",
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            "cost_audit": {"estimated_cost": 0.25},
        }
        return json.loads(json.dumps(self.patch))


class CorrectingRecordingClient(SummarylessRecordingClient):
    def __init__(self, initial_patch, corrected_patch):
        super().__init__(initial_patch)
        self.corrected_patch = json.loads(json.dumps(corrected_patch))
        self.corrections = []

    def correct(
        self,
        region,
        capsules,
        *,
        rejected_patch,
        validation_error,
    ):
        self.corrections.append(
            {
                "region": region,
                "capsules": capsules,
                "rejected_patch": json.loads(json.dumps(rejected_patch)),
                "validation_error": validation_error,
            }
        )
        self.last_call_metadata = {
            "physical_call_id": "physical-correction-test-call",
            "status": "completed",
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 5,
                "total_tokens": 17,
            },
            "cost_audit": {"estimated_cost": 0.30},
        }
        return json.loads(json.dumps(self.corrected_patch))


def leaf(memory_id, *, value="prefers coffee", durability="durable", slot="preference.beverage", polarity="positive", operation="append", state="active", temporal="current", counterevidence=False):
    return {
        "memory_id": memory_id,
        "value": value,
        "record_state": state,
        "turn_index": 1,
        "metadata": {
            "canonical_slot_key": slot,
            "durability": durability,
            "temporal_status": temporal,
            "polarity": polarity,
            "write_operation": operation,
            "counterevidence": counterevidence,
        },
    }


def region(*leaves):
    return {"region_key": "coffee.preference", "evidence": list(leaves)}


def claim(memory_id, *, slot="preference.beverage", text="prefers coffee"):
    return {
        "canonical_slot": slot,
        "text": text,
        "support": [memory_id],
        "counterevidence": [],
    }


class V4SlowGraphTests(unittest.TestCase):
    def insert_capsule(
        self,
        con,
        memory_id,
        *,
        capsule_id="cap_x",
        revision=1,
        state="active",
        status="active",
        support=("a",),
    ):
        metadata = {
            "content_variant": slow_graph.CAPSULE_VARIANT,
            "capsule_id": capsule_id,
            "revision": revision,
            "status": status,
            "region_key": "coffee.preference",
            "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
            "claims": [
                {
                    "claim_id": f"claim.{memory_id}",
                    "canonical_slot": "preference.beverage",
                    "text": "prefers coffee",
                    "support": list(support),
                    "counterevidence": [],
                    "source_parents": [
                        {
                            "session_index": 1,
                            "parent_chunk_index": 1,
                            "message_index": 1,
                            "source_record_id": "source.a",
                            "event_id": "event.a",
                            "evidence_char_start": 0,
                            "evidence_char_end": 5,
                        }
                    ],
                }
            ],
        }
        con.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "scope",
                memory_id,
                slow_graph.CAPSULE_VARIANT,
                "slow." + capsule_id,
                "prefers coffee",
                "capsule_revision",
                "[]",
                "[]",
                0.0,
                0.0,
                "slow_graph",
                revision,
                state,
                "[]",
                json.dumps(metadata),
            ),
        )

    def make_seeded_store(self, path: Path):
        store = slow_graph.SlowGraphStore(path, schema=(Record, Edge))
        with sqlite3.connect(path) as con:
            con.execute("CREATE TABLE records(scope_id TEXT,memory_id TEXT,category TEXT,slot_key TEXT,value TEXT,relation TEXT,anchor_concepts_json TEXT,evidence_anchors_json TEXT,salience REAL,confidence REAL,source_kind TEXT,turn_index INTEGER,state TEXT,supersedes_json TEXT,metadata_json TEXT,PRIMARY KEY(scope_id,memory_id))")
            for index, memory_id in enumerate(("a", "b"), 1):
                metadata = {
                    "content_variant": "product_semantic_memory",
                    "memory_layer": "fast",
                    "node_kind": "atomic_user_assertion",
                    "atomic_evidence_leaf": True,
                    "authority": "user_assertion",
                    "graph_entity_key": "coffee.preference",
                    "canonical_slot_key": "preference.beverage",
                    "durability": "durable",
                    "temporal_status": "current",
                    "polarity": "positive",
                    "session_index": 1,
                    "message_index": index,
                    "source_record_id": "source." + memory_id,
                    "event_id": "event." + memory_id,
                    "evidence_char_start": 0,
                    "evidence_char_end": 5,
                }
                con.execute(
                    "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "scope",
                        memory_id,
                        "fact",
                        memory_id,
                        "prefers coffee",
                        "states",
                        "[]",
                        "[]",
                        .7,
                        .8,
                        "writer",
                        index,
                        "active",
                        "[]",
                        json.dumps(metadata),
                    ),
                )
        con.close()
        return store

    def test_process_loss_recovery_is_atomic_explicit_and_auditable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            manager = slow_graph.TieredGraphPatchManager()
            job_id = store.enqueue(
                "scope", "coffee.preference", ["a", "b"], manager=manager
            )
            claim = store._claim_pending_job(
                job_id, owner="pid:999999:dead-owner"
            )
            self.assertIsNotNone(claim)
            with sqlite3.connect(path) as con:
                con.execute(
                    "UPDATE slow_graph_jobs SET lease_expires_at=1 WHERE job_id=?",
                    (job_id,),
                )
            con.close()

            with self.assertRaisesRegex(
                slow_graph.SlowGraphError, "explicit process-loss journal"
            ):
                store.recover_interrupted_attempts()
            recovery = store.recover_interrupted_process_loss(
                job_id, expected_attempt_id=claim.attempt_id
            )
            self.assertEqual(recovery["physical_api_calls_during_recovery"], 0)
            self.assertEqual(recovery["external_call_outcome"], "uncertain")
            self.assertEqual(
                recovery["potential_duplicate_physical_calls_max"], 3
            )
            with sqlite3.connect(path) as con:
                self.assertEqual(
                    con.execute(
                        "SELECT status,attempts,claim_token FROM slow_graph_jobs "
                        "WHERE job_id=?",
                        (job_id,),
                    ).fetchone(),
                    ("pending", 1, None),
                )
                self.assertEqual(
                    con.execute(
                        "SELECT status,error FROM slow_graph_attempts "
                        "WHERE attempt_id=?",
                        (claim.attempt_id,),
                    ).fetchone(),
                    ("expired", slow_graph.PROCESS_LOSS_INTERRUPTION_ERROR),
                )
                self.assertEqual(
                    con.execute(
                        "SELECT count(*) FROM slow_graph_process_loss_recoveries"
                    ).fetchone()[0],
                    1,
                )
            con.close()

            flash = RecordingClient(
                {
                    "operations": [
                        {
                            "action": "create",
                            "claims": [
                                {
                                    "canonical_slot": "preference.beverage",
                                    "text": "prefers coffee",
                                    "support": ["a", "b"],
                                    "counterevidence": [],
                                }
                            ],
                        }
                    ]
                }
            )
            store.run_job(
                job_id, slow_graph.TieredGraphPatchManager(flash=flash)
            )
            audit = store.audit("scope")
            self.assertEqual(audit["process_loss_recoveries"], 1)
            self.assertEqual(
                audit["process_loss_potential_duplicate_physical_calls_max"], 3
            )

    def test_zero_call_configuration_failure_has_one_explicit_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            unavailable = slow_graph.TieredGraphPatchManager()
            job_id = store.enqueue(
                "scope", "coffee.preference", ["a", "b"], manager=unavailable
            )
            with self.assertRaisesRegex(
                slow_graph.TieredAPIError, "flash client is not configured"
            ):
                store.run_job(job_id, unavailable)

            recovery = slow_graph.resume_zero_call_configuration_failure(
                store, job_id
            )
            self.assertEqual(recovery["physical_api_calls"], 0)
            self.assertEqual(recovery["route"], "flash")
            with sqlite3.connect(path) as con:
                self.assertEqual(
                    con.execute(
                        "SELECT status FROM slow_graph_jobs WHERE job_id=?",
                        (job_id,),
                    ).fetchone()[0],
                    "pending",
                )
                self.assertEqual(
                    con.execute(
                        "SELECT count(*) FROM slow_graph_zero_call_recoveries"
                    ).fetchone()[0],
                    1,
                )
            con.close()
            with self.assertRaisesRegex(
                slow_graph.SlowGraphError, "one unclaimed failed job"
            ):
                slow_graph.resume_zero_call_configuration_failure(store, job_id)

            flash = RecordingClient(
                {
                    "operations": [
                        {
                            "action": "create",
                            "claims": [
                                {
                                    "canonical_slot": "preference.beverage",
                                    "text": "prefers coffee",
                                    "support": ["a", "b"],
                                    "counterevidence": [],
                                }
                            ],
                        }
                    ]
                }
            )
            store.run_job(
                job_id, slow_graph.TieredGraphPatchManager(flash=flash)
            )
            audit = store.audit("scope")
            self.assertEqual(audit["zero_call_configuration_recoveries"], 1)
            self.assertEqual(audit["usage"]["physical_api_calls"], 1)

    def test_zero_call_promotion_routing_failure_has_one_explicit_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            con = sqlite3.connect(path)
            con.execute("UPDATE records SET state='challenged' WHERE memory_id='b'")
            con.commit()
            con.close()

            class LegacyNoopManager:
                model_config = {"model": "deepseek-v4-tiered-slow-graph"}
                prompt_hash = "legacy-noop-promotion-bug"
                last_call_metadata = {}

                def propose(self, current_region, current_capsules):
                    self.last_call_metadata = {
                        "route": "deterministic_noop",
                        "route_reason": "new capsule blocked by unresolved fast challenge",
                        "physical_api_call": False,
                        "physical_api_calls": 0,
                        "attempt_count": 0,
                        "eligible_evidence_ids": ["a"],
                        "challenged_evidence_ids": ["b"],
                        "delta_evidence_ids": ["a", "b"],
                        "usage": {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                        },
                    }
                    raise slow_graph.PatchValidationError(
                        'noop cannot consume uncited current durable Fast evidence: ["a"]'
                    )

            legacy = LegacyNoopManager()
            job_id = store.enqueue(
                "scope", "coffee.preference", ["a", "b"], manager=legacy
            )
            with self.assertRaises(slow_graph.PatchValidationError):
                store.run_job(job_id, legacy)

            recovery = slow_graph.resume_zero_call_promotion_failure(store, job_id)
            self.assertEqual(recovery["physical_api_calls"], 0)
            self.assertEqual(recovery["status"], "pending")
            with self.assertRaisesRegex(
                slow_graph.SlowGraphError, "one unclaimed failed job"
            ):
                slow_graph.resume_zero_call_promotion_failure(store, job_id)

            pro = RecordingClient(
                {
                    "operations": [
                        {
                            "action": "create",
                            "capsule_key": "coffee.preference",
                            "claims": [claim("a")],
                        }
                    ]
                }
            )
            store.run_job(job_id, slow_graph.TieredGraphPatchManager(pro=pro))
            audit = store.audit("scope")
            self.assertEqual(audit["zero_call_promotion_recoveries"], 1)
            self.assertTrue(audit["promotion_coverage"]["complete"])

    def test_zero_call_empty_internal_origin_field_projection_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            con = sqlite3.connect(path)
            metadata = json.loads(
                con.execute(
                    "SELECT metadata_json FROM records WHERE memory_id='a'"
                ).fetchone()[0]
            )
            metadata["origin_answer_ids"] = []
            con.execute(
                "UPDATE records SET metadata_json=? WHERE memory_id='a'",
                (json.dumps(metadata),),
            )
            con.commit()
            con.close()

            class LegacyProjectionManager:
                model_config = {"model": "deepseek-v4-tiered-slow-graph"}
                prompt_hash = "legacy-raw-region-policy"
                last_call_metadata = {}

                def propose(self, current_region, current_capsules):
                    self.last_call_metadata = {
                        "route": "deterministic_create",
                        "route_reason": "one current durable leaf",
                        "physical_api_call": False,
                        "physical_api_calls": 0,
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                    }
                    raise slow_graph.EvidencePolicyError(
                        "benchmark field is forbidden in slow-graph request: "
                        "payload.evidence[0].metadata.origin_answer_ids"
                    )

            legacy = LegacyProjectionManager()
            job_id = store.enqueue(
                "scope", "coffee.preference", ["a", "b"], manager=legacy
            )
            with self.assertRaisesRegex(
                slow_graph.EvidencePolicyError, "origin_answer_ids"
            ):
                store.run_job(job_id, legacy)
            recovery = slow_graph.resume_zero_call_projection_failure(
                store, job_id
            )
            self.assertEqual(recovery["physical_api_calls"], 0)
            self.assertEqual(
                recovery["offending_paths"],
                ["payload.evidence[0].metadata.origin_answer_ids"],
            )

            flash = RecordingClient(
                {
                    "operations": [
                        {
                            "action": "create",
                            "claims": [
                                {
                                    "canonical_slot": "preference.beverage",
                                    "text": "prefers coffee",
                                    "support": ["a", "b"],
                                    "counterevidence": [],
                                }
                            ],
                        }
                    ]
                }
            )
            store.run_job(
                job_id, slow_graph.TieredGraphPatchManager(flash=flash)
            )
            audit = store.audit("scope")
            self.assertEqual(audit["zero_call_projection_recoveries"], 1)
            self.assertEqual(audit["usage"]["physical_api_calls"], 1)

    def test_drain_collects_complete_invalid_responses_and_processes_other_jobs(self):
        manager = type("Manager", (), {"last_call_metadata": {}})()

        class FakeStore:
            def __init__(self):
                self.claims = [
                    slow_graph.JobClaim("bad", "a1", "t1", "o1"),
                    slow_graph.JobClaim("good", "a2", "t2", "o2"),
                ]
                self.processed = []

            def recover_interrupted_attempts(self):
                return 0

            def _claim_owner(self):
                return "owner"

            def _claim_pending_job(self, job_id, *, owner):
                return self.claims.pop(0) if self.claims else None

            def _run_claimed_job(self, claim, current_manager):
                self.processed.append(claim.job_id)
                if claim.job_id == "bad":
                    current_manager.last_call_metadata = {
                        "physical_api_call": True,
                        "physical_api_calls": 2,
                        "http_status": 200,
                        "finish_reason": "stop",
                        "status": "completed",
                        "raw_response": '{"choices":[]}',
                    }
                    raise slow_graph.TieredAPIError("invalid GraphPatch")
                return "patch-good"

        store = FakeStore()
        with self.assertRaisesRegex(
            slow_graph.SlowGraphError, "retained complete invalid model responses"
        ):
            slow_graph.V4SlowGraphStore.drain(store, manager)
        self.assertEqual(store.processed, ["bad", "good"])

    def test_store_rejects_missing_summary_before_opening_apply_transaction(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)

            class MissingSummaryManager:
                model_config = {"model": "test-manager"}
                prompt_hash = "test-manager"
                last_call_metadata = {
                    "physical_api_call": False,
                    "physical_api_calls": 0,
                    "route": "test",
                }

                def propose(self, current_region, current_capsules):
                    return {
                        "operations": [
                            {
                                "action": "create",
                                "capsule_key": "preference.beverage",
                                "claims": [
                                    {
                                        "canonical_slot": "preference.beverage",
                                        "text": "prefers coffee",
                                        "support": ["a", "b"],
                                        "counterevidence": [],
                                    }
                                ],
                            }
                        ]
                    }

            manager = MissingSummaryManager()
            job_id = store.enqueue(
                "scope", "coffee.preference", ["a", "b"], manager=manager
            )
            with self.assertRaisesRegex(
                slow_graph.PatchValidationError, "summary is required"
            ):
                store.run_job(job_id, manager)
            con = sqlite3.connect(path)
            try:
                self.assertEqual(
                    con.execute(
                        "SELECT count(*) FROM slow_graph_patches WHERE job_id=?",
                        (job_id,),
                    ).fetchone()[0],
                    0,
                )
            finally:
                con.close()

    def test_drain_stops_immediately_on_transport_failure(self):
        manager = type("Manager", (), {"last_call_metadata": {}})()

        class FakeStore:
            def __init__(self):
                self.claims = [
                    slow_graph.JobClaim("bad", "a1", "t1", "o1"),
                    slow_graph.JobClaim("never", "a2", "t2", "o2"),
                ]
                self.processed = []

            def recover_interrupted_attempts(self):
                return 0

            def _claim_owner(self):
                return "owner"

            def _claim_pending_job(self, job_id, *, owner):
                return self.claims.pop(0) if self.claims else None

            def _run_claimed_job(self, claim, current_manager):
                self.processed.append(claim.job_id)
                current_manager.last_call_metadata = {
                    "physical_api_call": True,
                    "physical_api_calls": 1,
                    "status": "request_error",
                    "raw_response": "",
                }
                raise slow_graph.TieredAPIError("transport failure")

        store = FakeStore()
        with self.assertRaisesRegex(slow_graph.TieredAPIError, "transport failure"):
            slow_graph.V4SlowGraphStore.drain(store, manager)
        self.assertEqual(store.processed, ["bad"])

    def test_deterministic_create_ignores_episodic_and_exposes_uncertain(self):
        manager = slow_graph.TieredGraphPatchManager()
        patch = manager.propose(region(leaf("durable"), leaf("event", durability="episodic"), leaf("unknown", durability="uncertain")), [])
        self.assertEqual(patch["operations"][0]["action"], "create")
        self.assertEqual(patch["operations"][0]["summary"], "prefers coffee")
        self.assertEqual(patch["operations"][0]["claims"][0]["support"], ["durable"])
        self.assertEqual(manager.last_call_metadata["route"], "deterministic_create")
        self.assertEqual(manager.last_call_metadata["physical_api_calls"], 0)
        self.assertEqual(manager.last_call_metadata["episodic_evidence_ids"], ["event"])
        self.assertEqual(manager.last_call_metadata["uncertain_evidence_ids"], ["unknown"])

    def test_semantic_summary_contract_rejects_only_high_confidence_failures(self):
        claims = [{"text": "User lives in Paris."}]
        for summary, message in (
            ("", "is required"),
            ('[{"text":"User lives in Paris"}]', "must not be JSON"),
            ("Create a memory capsule for the supplied evidence.", "graph operation"),
            ("Challenge conflicting location evidence", "graph operation"),
            ("User commute details", "generic heading"),
        ):
            with self.subTest(summary=summary):
                with self.assertRaisesRegex(
                    slow_graph.PatchValidationError, message
                ):
                    slow_graph.validate_semantic_summary(summary, claims)
        self.assertEqual(
            slow_graph.validate_semantic_summary(
                "User lives in Paris and commutes by train.", claims
            ),
            "User lives in Paris and commutes by train.",
        )
        self.assertEqual(
            slow_graph.validate_semantic_summary(
                "User takes one capsule daily and owns a game controller.", claims
            ),
            "User takes one capsule daily and owns a game controller.",
        )

    def test_invalid_stored_summary_is_migrated_without_api(self):
        capsule = [
            {
                "capsule_id": "cap_x",
                "revision": 1,
                "status": "active",
                "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
                "value": "Create reading memory capsule with all supplied evidence.",
                "claims": [
                    {
                        "claim_id": "c1",
                        "canonical_slot": "preference.beverage",
                        "text": "prefers coffee",
                        "support": ["a"],
                        "counterevidence": [],
                    }
                ],
            }
        ]
        manager = slow_graph.TieredGraphPatchManager()
        patch = manager.propose(region(leaf("a")), capsule)
        self.assertEqual(patch["operations"][0]["action"], "revise")
        self.assertEqual(patch["operations"][0]["summary"], "prefers coffee")
        self.assertEqual(
            manager.last_call_metadata["route"],
            "deterministic_summary_migration",
        )
        self.assertEqual(manager.last_call_metadata["physical_api_calls"], 0)

    def test_valid_stored_summary_with_no_delta_remains_noop(self):
        capsule = [
            {
                "capsule_id": "cap_x",
                "revision": 1,
                "status": "active",
                "value": "prefers coffee",
                "summary_contract_version": slow_graph.SLOW_SUMMARY_CONTRACT_VERSION,
                "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
                "claims": [
                    {
                        "claim_id": "c1",
                        "canonical_slot": "preference.beverage",
                        "text": "prefers coffee",
                        "support": ["a"],
                        "counterevidence": [],
                    }
                ],
            }
        ]
        manager = slow_graph.TieredGraphPatchManager()
        self.assertEqual(
            manager.propose(region(leaf("a")), capsule),
            {"operations": [{"action": "noop", "capsule_id": "cap_x"}]},
        )
        self.assertEqual(manager.last_call_metadata["route"], "deterministic_noop")

    def test_no_eligible_delta_is_deterministic_noop(self):
        manager = slow_graph.TieredGraphPatchManager()
        patch = manager.propose(region(leaf("event", durability="episodic"), leaf("unknown", durability="uncertain")), [])
        self.assertEqual(patch, {"operations": [{"action": "noop"}]})
        self.assertEqual(manager.last_call_metadata["route"], "deterministic_noop")
        self.assertEqual(manager.last_call_metadata["physical_api_calls"], 0)

    def test_active_durable_memory_is_eligible_for_every_target_time(self):
        for temporal in ("past", "planned", "future"):
            with self.subTest(temporal=temporal):
                manager = slow_graph.TieredGraphPatchManager()
                patch = manager.propose(
                    region(leaf("a", temporal=temporal)), []
                )
                self.assertEqual(
                    patch["operations"][0]["claims"][0]["support"],
                    ["a"],
                )
                self.assertEqual(
                    manager.last_call_metadata["route"], "deterministic_create"
                )
                self.assertEqual(
                    manager.last_call_metadata["eligible_evidence_ids"], ["a"]
                )

    def test_compatible_multiple_durable_leaves_use_flash_once(self):
        flash = RecordingClient({"operations": [{"action": "create", "claims": [{"canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["a", "b"], "counterevidence": []}]}]})
        pro = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        manager.propose(region(leaf("a"), leaf("b")), [])
        self.assertEqual(len(flash.calls), 1)
        self.assertEqual(len(pro.calls), 0)
        self.assertEqual(manager.last_call_metadata["route"], "flash")
        self.assertEqual(manager.last_call_metadata["physical_api_calls"], 1)
        self.assertEqual(
            flash.calls[0][0]["required_evidence_ids"], ["a", "b"]
        )

    def test_one_region_can_commit_multiple_create_operations(self):
        pro = RecordingClient(
            {
                "operations": [
                    {
                        "action": "create",
                        "capsule_key": "beverage",
                        "summary": "prefers coffee",
                        "claims": [
                            {
                                "canonical_slot": "preference.beverage",
                                "text": "prefers coffee",
                                "support": ["a"],
                                "counterevidence": [],
                            }
                        ],
                    },
                    {
                        "action": "create",
                        "capsule_key": "snack",
                        "summary": "prefers toast",
                        "claims": [
                            {
                                "canonical_slot": "preference.snack",
                                "text": "prefers toast",
                                "support": ["b"],
                                "counterevidence": [],
                            }
                        ],
                    },
                ]
            }
        )
        manager = slow_graph.TieredGraphPatchManager(pro=pro)
        patch = manager.propose(
            region(
                leaf("a"),
                leaf("b", value="prefers toast", slot="preference.snack"),
            ),
            [],
        )
        self.assertEqual([item["action"] for item in patch["operations"]], ["create", "create"])
        self.assertEqual(
            [item["capsule_key"] for item in patch["operations"]],
            ["beverage", "snack"],
        )
        self.assertEqual(
            sorted(
                evidence_id
                for operation in patch["operations"]
                for claim in operation["claims"]
                for evidence_id in claim["support"]
            ),
            ["a", "b"],
        )

    def test_create_requires_a_unique_capsule_key(self):
        claim = {
            "canonical_slot": "preference.beverage",
            "text": "prefers coffee",
            "support": ["a"],
            "counterevidence": [],
        }
        with self.subTest(case="missing"), self.assertRaisesRegex(
            slow_graph.PatchValidationError, "capsule_key"
        ):
            slow_graph.validate_patch(
                {"operations": [{"action": "create", "claims": [claim]}]}
            )
        with self.subTest(case="duplicate"), self.assertRaisesRegex(
            slow_graph.PatchValidationError, "duplicate create capsule_key"
        ):
            slow_graph.validate_patch(
                {
                    "operations": [
                        {"action": "create", "capsule_key": "beverage", "claims": [claim]},
                        {"action": "create", "capsule_key": "beverage", "claims": [claim]},
                    ]
                }
            )

    def test_model_summary_is_materialized_from_final_claim_projection(self):
        flash = SummarylessRecordingClient(
            {
                "operations": [
                    {
                        "action": "create",
                        "capsule_key": "beverage",
                        "summary": "model summary must be replaced",
                        "claims": [
                            {
                                "canonical_slot": "preference.beverage",
                                "text": "prefers coffee",
                                "support": ["a", "b"],
                                "counterevidence": [],
                            }
                        ],
                    }
                ]
            }
        )
        manager = slow_graph.TieredGraphPatchManager(flash=flash)
        patch = manager.propose(region(leaf("a"), leaf("b")), [])
        self.assertEqual(patch["operations"][0]["summary"], "prefers coffee")
        slow_graph._validate_patch_summary_contract(patch)

    def test_flash_can_revise_and_create_while_covering_every_delta(self):
        capsule = [
            {
                "capsule_id": "cap_x",
                "revision": 1,
                "status": "active",
                "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
                "claims": [
                    {
                        "claim_id": "c1",
                        "canonical_slot": "preference.beverage",
                        "text": "prefers coffee",
                        "support": ["a"],
                        "counterevidence": [],
                    }
                ],
            }
        ]
        flash = SummarylessRecordingClient(
            {
                "operations": [
                    {
                        "action": "revise",
                        "capsule_id": "cap_x",
                        "base_revision": 1,
                        "claims": [
                            {
                                "canonical_slot": "preference.beverage",
                                "text": "prefers coffee",
                                "support": ["b"],
                                "counterevidence": [],
                            }
                        ],
                    },
                    {
                        "action": "create",
                        "capsule_key": "snack",
                        "claims": [
                            {
                                "canonical_slot": "preference.snack",
                                "text": "prefers toast",
                                "support": ["c"],
                                "counterevidence": [],
                            }
                        ],
                    },
                ]
            }
        )
        manager = slow_graph.TieredGraphPatchManager(flash=flash)
        patch = manager.propose(
            region(
                leaf("a"),
                leaf("b"),
                leaf("c", value="prefers toast", slot="preference.snack"),
            ),
            capsule,
        )
        self.assertEqual([item["action"] for item in patch["operations"]], ["revise", "create"])
        self.assertEqual(patch["operations"][0]["claims"][0]["support"], ["a", "b"])
        self.assertEqual(patch["operations"][0]["summary"], "prefers coffee")
        self.assertEqual(patch["operations"][1]["summary"], "prefers toast")
        self.assertEqual(
            sorted(
                evidence_id
                for operation in patch["operations"]
                for claim in operation["claims"]
                for evidence_id in claim["support"]
            ),
            ["a", "b", "c"],
        )

    def test_multi_operation_identity_and_evidence_integrity_fail_closed(self):
        claim_a = {
            "canonical_slot": "preference.beverage",
            "text": "prefers coffee",
            "support": ["a"],
            "counterevidence": [],
        }
        claim_b = {
            "canonical_slot": "preference.snack",
            "text": "prefers toast",
            "support": ["b"],
            "counterevidence": [],
        }
        with self.subTest(case="duplicate target"), self.assertRaisesRegex(
            slow_graph.PatchValidationError, "duplicate capsule target"
        ):
            slow_graph.validate_patch(
                {
                    "operations": [
                        {
                            "action": "revise",
                            "capsule_id": "cap_x",
                            "base_revision": 1,
                            "claims": [claim_a],
                        },
                        {
                            "action": "challenge",
                            "capsule_id": "cap_x",
                            "base_revision": 1,
                            "claims": [claim_b],
                        },
                    ]
                }
            )

        duplicate_support = {
            "operations": [
                {"action": "create", "capsule_key": "beverage", "claims": [claim_a]},
                {
                    "action": "create",
                    "capsule_key": "snack",
                    "claims": [
                        {
                            **claim_b,
                            "support": ["a", "b"],
                        }
                    ],
                },
            ]
        }
        manager = slow_graph.TieredGraphPatchManager(
            pro=RecordingClient(duplicate_support)
        )
        with self.assertRaisesRegex(slow_graph.PatchValidationError, "support"):
            manager.propose(
                region(
                    leaf("a"),
                    leaf("b", value="prefers toast", slot="preference.snack"),
                ),
                [],
            )

        omitted_evidence = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "beverage",
                    "summary": "prefers coffee",
                    "claims": [claim_a],
                }
            ]
        }
        manager = slow_graph.TieredGraphPatchManager(
            flash=RecordingClient(omitted_evidence)
        )
        with self.assertRaisesRegex(slow_graph.PatchValidationError, "evidence|support"):
            manager.propose(region(leaf("a"), leaf("b")), [])

    def test_stale_revision_is_rejected_for_flash_delta(self):
        capsule = [
            {
                "capsule_id": "cap_x",
                "revision": 2,
                "status": "active",
                "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
                "claims": [
                    {
                        "claim_id": "c1",
                        "canonical_slot": "preference.beverage",
                        "text": "prefers coffee",
                        "support": ["a"],
                        "counterevidence": [],
                    }
                ],
            }
        ]
        flash = RecordingClient(
            {
                "operations": [
                    {
                        "action": "revise",
                        "capsule_id": "cap_x",
                        "base_revision": 1,
                        "summary": "prefers coffee",
                        "claims": [
                            {
                                "canonical_slot": "preference.beverage",
                                "text": "prefers coffee",
                                "support": ["b"],
                                "counterevidence": [],
                            }
                        ],
                    }
                ]
            }
        )
        manager = slow_graph.TieredGraphPatchManager(flash=flash)
        with self.assertRaisesRegex(slow_graph.PatchValidationError, "base_revision"):
            manager.propose(region(leaf("a"), leaf("b")), capsule)

    def test_multiple_legacy_capsule_summary_migrations_share_one_patch(self):
        capsules = [
            {
                "capsule_id": "cap_beverage",
                "revision": 3,
                "status": "active",
                "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
                "value": "Memory revision for preferences.",
                "claims": [
                    {
                        "claim_id": "c1",
                        "canonical_slot": "preference.beverage",
                        "text": "prefers coffee",
                        "support": ["a"],
                        "counterevidence": [],
                    }
                ],
            },
            {
                "capsule_id": "cap_snack",
                "revision": 4,
                "status": "active",
                "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
                "value": "Create a memory capsule for snacks.",
                "claims": [
                    {
                        "claim_id": "c2",
                        "canonical_slot": "preference.snack",
                        "text": "prefers toast",
                        "support": ["b"],
                        "counterevidence": [],
                    }
                ],
            },
        ]
        manager = slow_graph.TieredGraphPatchManager()
        patch = manager.propose(
            region(
                leaf("a"),
                leaf("b", value="prefers toast", slot="preference.snack"),
            ),
            capsules,
        )
        self.assertEqual(
            [(item["capsule_id"], item["base_revision"]) for item in patch["operations"]],
            [("cap_beverage", 3), ("cap_snack", 4)],
        )
        self.assertEqual(
            [item["summary"] for item in patch["operations"]],
            ["prefers coffee", "prefers toast"],
        )
        self.assertEqual(manager.last_call_metadata["route"], "deterministic_summary_migration")

    def test_generic_legacy_capsule_is_repartitioned_by_one_pro_call(self):
        capsules = [
            {
                "capsule_id": "cap_legacy",
                "revision": 2,
                "status": "active",
                "value": "prefers coffee prefers toast",
                "summary_contract_version": slow_graph.SLOW_SUMMARY_CONTRACT_VERSION,
                "claims": [
                    {
                        "claim_id": "c1",
                        "canonical_slot": "preference.beverage",
                        "text": "prefers coffee",
                        "support": ["a"],
                        "counterevidence": [],
                    },
                    {
                        "claim_id": "c2",
                        "canonical_slot": "preference.snack",
                        "text": "prefers toast",
                        "support": ["b"],
                        "counterevidence": [],
                    },
                ],
            }
        ]
        pro = SummarylessRecordingClient(
            {
                "operations": [
                    {
                        "action": "revise",
                        "capsule_id": "cap_legacy",
                        "base_revision": 2,
                        "claims": [
                            {
                                "canonical_slot": "preference.beverage",
                                "text": "prefers coffee",
                                "support": ["a"],
                                "counterevidence": [],
                            }
                        ],
                    },
                    {
                        "action": "create",
                        "capsule_key": "snack",
                        "claims": [
                            {
                                "canonical_slot": "preference.snack",
                                "text": "prefers toast",
                                "support": ["b"],
                                "counterevidence": [],
                            }
                        ],
                    },
                ]
            }
        )
        manager = slow_graph.TieredGraphPatchManager(pro=pro)
        patch = manager.propose(
            {
                "region_key": "preferences",
                "evidence": [
                    leaf("a"),
                    leaf("b", value="prefers toast", slot="preference.snack"),
                ],
            },
            capsules,
        )
        self.assertEqual(len(pro.calls), 1)
        self.assertTrue(pro.calls[0][0]["semantic_partition_required"])
        self.assertEqual(pro.calls[0][0]["semantic_partition_mode"], "migrate")
        self.assertEqual(
            pro.calls[0][0]["partition_capsule_ids"], ["cap_legacy"]
        )
        self.assertEqual(
            [operation["summary"] for operation in patch["operations"]],
            ["prefers coffee", "prefers toast"],
        )
        self.assertEqual(manager.last_call_metadata["route"], "pro")
        self.assertEqual(
            manager.last_call_metadata["semantic_partition_capsule_ids"],
            ["cap_legacy"],
        )

    def test_non_generic_legacy_multi_claim_capsule_requires_pro_partition(self):
        capsules = [
            {
                "capsule_id": "cap_business",
                "revision": 1,
                "status": "active",
                "value": "User owns a clothing brand. User supplies food ingredients.",
                "summary_contract_version": slow_graph.SLOW_SUMMARY_CONTRACT_VERSION,
                "claims": [
                    {
                        "claim_id": "c1",
                        "canonical_slot": "memory.user.business.identity.clothing.brand",
                        "text": "User owns a clothing brand.",
                        "support": ["a"],
                        "counterevidence": [],
                    },
                    {
                        "claim_id": "c2",
                        "canonical_slot": "memory.user.business.identity.food.supplier",
                        "text": "User supplies food ingredients.",
                        "support": ["b"],
                        "counterevidence": [],
                    },
                ],
            }
        ]
        pro = SummarylessRecordingClient(
            {
                "operations": [
                    {
                        "action": "revise",
                        "capsule_id": "cap_business",
                        "base_revision": 1,
                        "claims": [
                            claim(
                                "a",
                                slot="memory.user.business.identity.clothing.brand",
                                text="User owns a clothing brand.",
                            )
                        ],
                    },
                    {
                        "action": "create",
                        "capsule_key": "business.food.supplier",
                        "claims": [
                            claim(
                                "b",
                                slot="memory.user.business.identity.food.supplier",
                                text="User supplies food ingredients.",
                            )
                        ],
                    },
                ]
            }
        )
        manager = slow_graph.TieredGraphPatchManager(pro=pro)
        patch = manager.propose(
            {
                "region_key": "business",
                "evidence": [
                    leaf(
                        "a",
                        value="User owns a clothing brand.",
                        slot="memory.user.business.identity.clothing.brand",
                    ),
                    leaf(
                        "b",
                        value="User supplies food ingredients.",
                        slot="memory.user.business.identity.food.supplier",
                    ),
                ],
            },
            capsules,
        )
        self.assertEqual([item["action"] for item in patch["operations"]], ["revise", "create"])
        self.assertEqual(len(pro.calls), 1)
        self.assertEqual(pro.calls[0][0]["semantic_partition_mode"], "migrate")

    def test_single_claim_legacy_partition_migrates_without_api(self):
        capsule = [
            {
                "capsule_id": "cap_legacy",
                "revision": 1,
                "status": "active",
                "value": "prefers coffee",
                "summary_contract_version": slow_graph.SLOW_SUMMARY_CONTRACT_VERSION,
                "claims": [
                    {
                        "claim_id": "c1",
                        "canonical_slot": "preference.beverage",
                        "text": "prefers coffee",
                        "support": ["a"],
                        "counterevidence": [],
                    }
                ],
            }
        ]
        manager = slow_graph.TieredGraphPatchManager()
        patch = manager.propose(region(leaf("a")), capsule)
        self.assertEqual(patch["operations"][0]["action"], "revise")
        self.assertEqual(
            manager.last_call_metadata["route"],
            "deterministic_contract_migration",
        )
        self.assertEqual(manager.last_call_metadata["physical_api_calls"], 0)
        self.assertEqual(
            manager.last_call_metadata["semantic_partition_capsule_ids"],
            ["cap_legacy"],
        )

    def test_generic_multi_slot_region_uses_pro_semantic_management(self):
        flash = SummarylessRecordingClient({"operations": []})
        pro = SummarylessRecordingClient(
            {
                "operations": [
                    {
                        "action": "create",
                        "capsule_key": "user.preferences.cocktails",
                        "claims": [
                            claim(
                                "a",
                                slot="memory.user.preferences.preference.cocktail.base",
                                text="User prefers gin-based cocktails.",
                            )
                        ],
                    },
                    {
                        "action": "create",
                        "capsule_key": "user.preferences.vehicles",
                        "claims": [
                            claim(
                                "b",
                                slot="memory.user.preferences.preference.vehicle.type",
                                text="User prefers compact cars.",
                            )
                        ],
                    },
                ]
            }
        )
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        patch = manager.propose(
            {
                "region_key": "preferences",
                "evidence": [
                    leaf(
                        "a",
                        value="User prefers gin-based cocktails.",
                        slot="memory.user.preferences.preference.cocktail.base",
                    ),
                    leaf(
                        "b",
                        value="User prefers compact cars.",
                        slot="memory.user.preferences.preference.vehicle.type",
                    ),
                ],
            },
            [],
        )
        self.assertEqual(len(flash.calls), 0)
        self.assertEqual(len(pro.calls), 1)
        self.assertEqual(len(patch["operations"]), 2)
        self.assertEqual(pro.calls[0][0]["semantic_partition_mode"], "manage")
        self.assertNotIn("partition_capsule_ids", pro.calls[0][0])

    def test_generic_multi_slot_create_rejects_generic_capsule_key(self):
        pro = SummarylessRecordingClient(
            {
                "operations": [
                    {
                        "action": "create",
                        "capsule_key": "preferences",
                        "claims": [
                            claim(
                                "a",
                                slot="memory.user.preferences.preference.cocktail.base",
                                text="User prefers gin-based cocktails.",
                            ),
                            claim(
                                "b",
                                slot="memory.user.preferences.preference.vehicle.type",
                                text="User prefers compact cars.",
                            ),
                        ],
                    }
                ]
            }
        )
        manager = slow_graph.TieredGraphPatchManager(pro=pro)
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError, "concrete semantic topic"
        ):
            manager.propose(
                {
                    "region_key": "preferences",
                    "evidence": [
                        leaf(
                            "a",
                            value="User prefers gin-based cocktails.",
                            slot="memory.user.preferences.preference.cocktail.base",
                        ),
                        leaf(
                            "b",
                            value="User prefers compact cars.",
                            slot="memory.user.preferences.preference.vehicle.type",
                        ),
                    ],
                },
                [],
            )

    def test_generic_multi_slot_create_allows_region_plus_semantic_axis(self):
        pro = SummarylessRecordingClient(
            {
                "operations": [
                    {
                        "action": "create",
                        "capsule_key": "communication.preferences",
                        "claims": [
                            claim(
                                "a",
                                slot="memory.user.communication.preference.answer.format",
                                text="User wants brief step-by-step answers.",
                            ),
                            claim(
                                "b",
                                slot="memory.user.communication.preference.detail.level",
                                text="User will ask when more detail is needed.",
                            ),
                        ],
                    }
                ]
            }
        )
        manager = slow_graph.TieredGraphPatchManager(pro=pro)
        result = manager.propose(
            {
                "region_key": "communication",
                "evidence": [
                    leaf(
                        "a",
                        value="User wants brief step-by-step answers.",
                        slot="memory.user.communication.preference.answer.format",
                    ),
                    leaf(
                        "b",
                        value="User will ask when more detail is needed.",
                        slot="memory.user.communication.preference.detail.level",
                    ),
                ],
            },
            [],
        )
        self.assertEqual(
            result["operations"][0]["capsule_key"],
            "communication.preferences",
        )
        self.assertEqual(len(pro.calls), 1)

    def test_generic_pro_patch_gets_one_bounded_semantic_correction(self):
        initial = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "preferences",
                    "claims": [
                        claim(
                            "a",
                            slot="memory.user.preferences.preference.cocktail.base",
                            text="User prefers gin-based cocktails.",
                        ),
                        claim(
                            "b",
                            slot="memory.user.preferences.preference.vehicle.type",
                            text="User prefers compact cars.",
                        ),
                    ],
                }
            ]
        }
        corrected = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "user.cocktail.preferences",
                    "claims": [
                        claim(
                            "a",
                            slot="memory.user.preferences.preference.cocktail.base",
                            text="User prefers gin-based cocktails.",
                        )
                    ],
                },
                {
                    "action": "create",
                    "capsule_key": "user.vehicle.preferences",
                    "claims": [
                        claim(
                            "b",
                            slot="memory.user.preferences.preference.vehicle.type",
                            text="User prefers compact cars.",
                        )
                    ],
                },
            ]
        }
        pro = CorrectingRecordingClient(initial, corrected)
        manager = slow_graph.TieredGraphPatchManager(pro=pro)
        patch = manager.propose(
            {
                "region_key": "preferences",
                "evidence": [
                    leaf(
                        "a",
                        value="User prefers gin-based cocktails.",
                        slot="memory.user.preferences.preference.cocktail.base",
                    ),
                    leaf(
                        "b",
                        value="User prefers compact cars.",
                        slot="memory.user.preferences.preference.vehicle.type",
                    ),
                ],
            },
            [],
        )
        self.assertEqual(len(pro.calls), 1)
        self.assertEqual(len(pro.corrections), 1)
        self.assertEqual(len(patch["operations"]), 2)
        self.assertIn(
            "concrete semantic topic",
            pro.corrections[0]["validation_error"],
        )
        metadata = manager.last_call_metadata
        self.assertEqual(metadata["route"], "pro")
        self.assertTrue(metadata["semantic_correction_attempted"])
        self.assertTrue(metadata["semantic_correction_applied"])
        self.assertEqual(metadata["physical_api_calls"], 2)
        self.assertEqual(metadata["attempt_count"], 2)
        self.assertEqual(metadata["usage"]["prompt_tokens"], 22)
        self.assertEqual(metadata["usage"]["completion_tokens"], 9)
        self.assertEqual(metadata["cost_audit"]["estimated_cost"], 0.55)
        self.assertEqual(
            [item["tier_stage"] for item in metadata["tier_calls"]],
            ["initial_pro", "semantic_correction"],
        )

    def test_distinct_same_slot_supports_get_one_bounded_lossless_correction(self):
        initial = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "user.judicial.appointment.concerns",
                    "claims": [
                        {
                            "canonical_slot": "belief.judicial.appointment.concern",
                            "text": "The user is concerned that judicial diversity quotas may reduce appointment quality.",
                            "support": ["a", "b"],
                            "counterevidence": [],
                        }
                    ],
                }
            ]
        }
        corrected = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "user.judicial.appointment.concerns",
                    "claims": [
                        claim(
                            "a",
                            slot="belief.judicial.appointment.concern",
                            text="The user believes judicial diversity quotas may produce less qualified judges.",
                        ),
                        claim(
                            "b",
                            slot="belief.judicial.appointment.concern",
                            text="The user believes prioritizing judicial diversity may undermine fairness and impartiality.",
                        ),
                    ],
                }
            ]
        }
        pro = CorrectingRecordingClient(initial, corrected)
        flash = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)

        result = manager.propose(
            region(
                leaf(
                    "a",
                    value="The user believes judicial diversity quotas may produce less qualified judges.",
                    slot="belief.judicial.appointment.concern",
                ),
                leaf(
                    "b",
                    value="The user believes prioritizing judicial diversity may undermine fairness and impartiality.",
                    slot="belief.judicial.appointment.concern",
                ),
            ),
            [],
        )

        self.assertEqual(len(flash.calls), 0)
        self.assertEqual(len(pro.calls), 1)
        self.assertEqual(len(pro.corrections), 1)
        self.assertIn(
            "distinct supplied Fast evidence texts cannot share one claim",
            pro.corrections[0]["validation_error"],
        )
        self.assertEqual(len(result["operations"][0]["claims"]), 2)
        self.assertEqual(
            manager.last_call_metadata["route_reason"],
            "same_slot_distinct_support_semantics",
        )
        self.assertTrue(manager.last_call_metadata["semantic_correction_applied"])
        self.assertEqual(manager.last_call_metadata["physical_api_calls"], 2)

    def test_identical_same_slot_support_texts_may_share_one_claim(self):
        flash = RecordingClient(
            {
                "operations": [
                    {
                        "action": "create",
                        "capsule_key": "user.beverage.preference",
                        "claims": [
                            {
                                "canonical_slot": "preference.beverage",
                                "text": "The user prefers coffee.",
                                "support": ["a", "b"],
                                "counterevidence": [],
                            }
                        ],
                    }
                ]
            }
        )
        pro = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)

        result = manager.propose(
            region(
                leaf("a", value="The user prefers coffee."),
                leaf("b", value="  the USER prefers   coffee.  "),
            ),
            [],
        )

        self.assertEqual(len(flash.calls), 1)
        self.assertEqual(len(pro.calls), 0)
        self.assertEqual(result["operations"][0]["claims"][0]["support"], ["a", "b"])

    def test_same_slot_different_referents_can_use_distinct_capsules(self):
        patch = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "amazon.prime.video.subscription",
                    "claims": [
                        claim(
                            "a",
                            slot="memory.user.subscriptions.possession.streaming.service",
                            text="The user has an Amazon Prime Video subscription.",
                        )
                    ],
                },
                {
                    "action": "create",
                    "capsule_key": "netflix.subscription",
                    "claims": [
                        claim(
                            "b",
                            slot="memory.user.subscriptions.possession.streaming.service",
                            text="The user has a Netflix subscription.",
                        )
                    ],
                },
            ]
        }
        current_region = region(
            leaf(
                "a",
                value="The user has an Amazon Prime Video subscription.",
                slot="memory.user.subscriptions.possession.streaming.service",
            ),
            leaf(
                "b",
                value="The user has a Netflix subscription.",
                slot="memory.user.subscriptions.possession.streaming.service",
            ),
        )

        slow_graph._validate_promotion_patch(current_region, [], patch)

    def test_identical_semantic_claim_cannot_span_distinct_capsules(self):
        patch = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "coffee.preference.primary",
                    "claims": [claim("a", text="The user prefers coffee.")],
                },
                {
                    "action": "create",
                    "capsule_key": "coffee.preference.duplicate",
                    "claims": [claim("b", text="The user prefers coffee.")],
                },
            ]
        }
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError,
            "self-contained semantic claim",
        ):
            slow_graph._validate_promotion_patch(
                region(
                    leaf("a", value="The user prefers coffee."),
                    leaf("b", value="The user prefers coffee."),
                ),
                [],
                patch,
            )

    def test_authoritative_leaf_with_challenged_sibling_routes_to_pro(self):
        pro = RecordingClient(
            {
                "operations": [
                    {
                        "action": "create",
                        "capsule_key": "personal.responsibility.opinion",
                        "claims": [
                            claim(
                                "a",
                                slot="opinion.personal.responsibility",
                                text="The user values personal responsibility.",
                            )
                        ],
                    }
                ]
            }
        )
        manager = slow_graph.TieredGraphPatchManager(pro=pro)

        result = manager.propose(
            {
                "region_key": "opinions",
                "evidence": [
                    leaf(
                        "a",
                        value="The user values personal responsibility.",
                        slot="opinion.personal.responsibility",
                    ),
                    leaf(
                        "b",
                        value="The user's opinion about literature is unresolved.",
                        slot="opinion.literature.impact",
                        state="challenged",
                    ),
                ],
            },
            [],
        )

        self.assertEqual(len(pro.calls), 1)
        self.assertEqual(result["operations"][0]["claims"][0]["support"], ["a"])
        self.assertEqual(
            manager.last_call_metadata["required_operation_evidence_ids"], ["a"]
        )
        self.assertNotEqual(manager.last_call_metadata["route"], "deterministic_noop")

    def test_semantic_correction_is_rejected_without_third_call(self):
        invalid = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "preferences",
                    "claims": [
                        claim(
                            "a",
                            slot="memory.user.preferences.preference.cocktail.base",
                            text="User prefers gin-based cocktails.",
                        ),
                        claim(
                            "b",
                            slot="memory.user.preferences.preference.vehicle.type",
                            text="User prefers compact cars.",
                        ),
                    ],
                }
            ]
        }
        pro = CorrectingRecordingClient(invalid, invalid)
        manager = slow_graph.TieredGraphPatchManager(pro=pro)
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError, "concrete semantic topic"
        ):
            manager.propose(
                {
                    "region_key": "preferences",
                    "evidence": [
                        leaf(
                            "a",
                            value="User prefers gin-based cocktails.",
                            slot="memory.user.preferences.preference.cocktail.base",
                        ),
                        leaf(
                            "b",
                            value="User prefers compact cars.",
                            slot="memory.user.preferences.preference.vehicle.type",
                        ),
                    ],
                },
                [],
            )
        self.assertEqual(len(pro.calls), 1)
        self.assertEqual(len(pro.corrections), 1)
        self.assertEqual(manager.last_call_metadata["physical_api_calls"], 2)
        self.assertEqual(
            manager.last_call_metadata["status"], "semantic_correction_rejected"
        )
        self.assertFalse(manager.last_call_metadata["semantic_correction_applied"])

    def test_multi_operation_apply_is_atomic_when_a_later_operation_is_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            existing_capsule_id = store._capsule_id("scope", "coffee.preference")
            con = sqlite3.connect(path)
            try:
                self.insert_capsule(
                    con,
                    "existing",
                    capsule_id=existing_capsule_id,
                    revision=2,
                    support=("a",),
                )
                before_records = con.execute(
                    "SELECT count(*) FROM records WHERE scope_id='scope'"
                ).fetchone()[0]
                con.commit()
            finally:
                con.close()

            job_id = store.enqueue("scope", "coffee.preference", ["a", "b"])
            claim = store._claim_pending_job(job_id, owner="atomic-test")
            self.assertIsNotNone(claim)
            patch = {
                "operations": [
                    {
                        "action": "create",
                        "capsule_key": "new.capsule",
                        "summary": "prefers toast",
                        "claims": [
                            {
                                "canonical_slot": "preference.snack",
                                "text": "prefers toast",
                                "support": ["b"],
                                "counterevidence": [],
                            }
                        ],
                    },
                    {
                        "action": "revise",
                        "capsule_id": existing_capsule_id,
                        "base_revision": 1,
                        "summary": "prefers coffee",
                        "claims": [
                            {
                                "canonical_slot": "preference.beverage",
                                "text": "prefers coffee",
                                "support": ["a"],
                                "counterevidence": [],
                            }
                        ],
                    },
                ]
            }
            with self.assertRaises(slow_graph.StaleRevisionError):
                store.apply_patch(
                    job_id,
                    patch,
                    manager_model="test-manager",
                    claim=claim,
                )

            con = sqlite3.connect(path)
            try:
                self.assertEqual(
                    con.execute(
                        "SELECT count(*) FROM records WHERE scope_id='scope'"
                    ).fetchone()[0],
                    before_records,
                )
                self.assertEqual(
                    con.execute("SELECT count(*) FROM slow_graph_patches").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    con.execute(
                        "SELECT status FROM slow_graph_jobs WHERE job_id=?", (job_id,)
                    ).fetchone()[0],
                    "pending",
                )
            finally:
                con.close()

    def test_multi_create_commit_persists_two_independent_capsule_heads(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            con = sqlite3.connect(path)
            try:
                row = con.execute(
                    "SELECT metadata_json FROM records WHERE memory_id='b'"
                ).fetchone()
                metadata = json.loads(row[0])
                metadata["canonical_slot_key"] = "preference.snack"
                con.execute(
                    "UPDATE records SET value='prefers toast',metadata_json=? "
                    "WHERE memory_id='b'",
                    (json.dumps(metadata),),
                )
                con.commit()
            finally:
                con.close()

            job_id = store.enqueue("scope", "coffee.preference", ["a", "b"])
            claim = store._claim_pending_job(job_id, owner="multi-create-test")
            self.assertIsNotNone(claim)
            patch = slow_graph._materialize_lossless_summaries(
                {
                    "operations": [
                        {
                            "action": "create",
                            "capsule_key": "beverage",
                            "claims": [
                                {
                                    "canonical_slot": "preference.beverage",
                                    "text": "prefers coffee",
                                    "support": ["a"],
                                    "counterevidence": [],
                                }
                            ],
                        },
                        {
                            "action": "create",
                            "capsule_key": "snack",
                            "claims": [
                                {
                                    "canonical_slot": "preference.snack",
                                    "text": "prefers toast",
                                    "support": ["b"],
                                    "counterevidence": [],
                                }
                            ],
                        },
                    ]
                }
            )
            patch_id = store.apply_patch(
                job_id,
                patch,
                manager_model="test-manager",
                call_metadata={
                    "summary_contract_version": slow_graph.SLOW_SUMMARY_CONTRACT_VERSION
                },
                claim=claim,
            )
            con = sqlite3.connect(path)
            con.row_factory = sqlite3.Row
            try:
                self.assertEqual(
                    con.execute(
                        "SELECT count(*) FROM slow_graph_patch_operations "
                        "WHERE patch_id=?",
                        (patch_id,),
                    ).fetchone()[0],
                    2,
                )
                capsules = store._capsules(con, "scope", "coffee.preference")
                self.assertEqual(len(capsules), 2)
                self.assertEqual(
                    {item["capsule_key"] for item in capsules},
                    {"beverage", "snack"},
                )
            finally:
                con.close()
            self.assertTrue(store.promotion_coverage("scope")["complete"])

    def test_apply_rejects_fast_evidence_snapshot_drift_before_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            job_id = store.enqueue("scope", "coffee.preference", ["a", "b"])
            claim = store._claim_pending_job(job_id, owner="snapshot-test")
            self.assertIsNotNone(claim)
            con = sqlite3.connect(path)
            try:
                con.execute(
                    "UPDATE records SET value='changed after enqueue' "
                    "WHERE memory_id='b'"
                )
                con.commit()
            finally:
                con.close()
            patch = slow_graph._materialize_lossless_summaries(
                {
                    "operations": [
                        {
                            "action": "create",
                            "capsule_key": "beverage",
                            "claims": [
                                {
                                    "canonical_slot": "preference.beverage",
                                    "text": "prefers coffee",
                                    "support": ["a", "b"],
                                    "counterevidence": [],
                                }
                            ],
                        }
                    ]
                }
            )
            with self.assertRaisesRegex(
                slow_graph.StaleRevisionError, "Fast evidence changed"
            ):
                store.apply_patch(
                    job_id,
                    patch,
                    manager_model="test-manager",
                    claim=claim,
                )

    def test_changed_capsule_state_gets_a_fresh_enqueue_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            first = store.enqueue("scope", "coffee.preference", ["a", "b"])
            con = sqlite3.connect(path)
            try:
                self.insert_capsule(con, "existing", revision=1, support=("a",))
                con.commit()
            finally:
                con.close()
            second = store.enqueue("scope", "coffee.preference", ["a", "b"])
            self.assertNotEqual(first, second)

    def test_negative_wording_is_not_counterevidence(self):
        pro = RecordingClient(
            {
                "operations": [
                    {
                        "action": "create",
                        "capsule_key": "user.coffee.preferences",
                        "claims": [
                            {
                                "canonical_slot": "preference.beverage",
                                "text": "likes coffee",
                                "support": ["a"],
                                "counterevidence": [],
                            },
                            {
                                "canonical_slot": "preference.beverage",
                                "text": "does not want coffee to disrupt sleep",
                                "support": ["b"],
                                "counterevidence": [],
                            },
                        ],
                    }
                ]
            }
        )
        flash = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        manager.propose(
            region(
                leaf("a", value="likes coffee"),
                leaf(
                    "b",
                    value="does not want coffee to disrupt sleep",
                    polarity="negative",
                ),
            ),
            [],
        )
        self.assertEqual(len(flash.calls), 0)
        self.assertEqual(len(pro.calls), 1)
        self.assertEqual(manager.last_call_metadata["route"], "pro")
        self.assertIn(
            "same_slot_distinct_support_semantics",
            manager.last_call_metadata["route_reason"],
        )

    def test_public_negative_leaf_remains_support_role(self):
        public = slow_graph._public_leaf(
            leaf(
                "negative",
                value="does not have a favorite post-run snack",
                polarity="negative",
            )
        )
        self.assertEqual(public["polarity"], "negative")
        self.assertEqual(public["evidence_role"], "support")

    def test_flash_cross_slot_support_is_rejected(self):
        flash = RecordingClient(
            {
                "operations": [
                    {
                        "action": "create",
                        "claims": [
                            {
                                "canonical_slot": "preference.beverage",
                                "text": "prefers coffee and owns a shop",
                                "support": ["a", "b"],
                                "counterevidence": [],
                            }
                        ],
                    }
                ]
            }
        )
        pro = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        capsule = [
            {
                "capsule_id": "cap_seed",
                "revision": 1,
                "status": "active",
                "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
                "claims": [
                    {
                        "claim_id": "seed_claim",
                        "canonical_slot": "seed.slot",
                        "text": "seed fact",
                        "support": ["seed"],
                        "counterevidence": [],
                    }
                ],
            }
        ]
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError, "canonical slot mismatch"
        ):
            manager.propose(
                region(
                    leaf("seed", value="seed fact", slot="seed.slot"),
                    leaf("a"),
                    leaf("b", value="owns a shop", slot="business.ownership"),
                ),
                capsule,
            )
        self.assertEqual(len(flash.calls), 1)
        self.assertEqual(len(pro.calls), 0)

    def test_flash_cannot_turn_negative_wording_into_counterevidence(self):
        flash = RecordingClient(
            {
                "operations": [
                    {
                        "action": "create",
                        "claims": [
                            {
                                "canonical_slot": "activity.running",
                                "text": "likes running",
                                "support": ["a"],
                                "counterevidence": ["b"],
                            }
                        ],
                    }
                ]
            }
        )
        pro = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        capsule = [
            {
                "capsule_id": "cap_seed",
                "revision": 1,
                "status": "active",
                "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
                "claims": [
                    {
                        "claim_id": "seed_claim",
                        "canonical_slot": "seed.slot",
                        "text": "seed fact",
                        "support": ["seed"],
                        "counterevidence": [],
                    }
                ],
            }
        ]
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError,
            "Flash cannot create new counterevidence",
        ):
            manager.propose(
                region(
                    leaf("seed", value="seed fact", slot="seed.slot"),
                    leaf("a", value="likes running", slot="activity.running"),
                    leaf(
                        "b",
                        value="does not have a favorite post-run snack",
                        polarity="negative",
                        slot="preference.snack",
                    ),
                ),
                capsule,
            )
        self.assertEqual(len(flash.calls), 1)
        self.assertEqual(len(pro.calls), 0)

    def test_model_noop_cannot_consume_uncited_durable_delta(self):
        manager = slow_graph.TieredGraphPatchManager(
            flash=RecordingClient({"operations": [{"action": "noop"}]}),
            pro=RecordingClient({"operations": []}),
        )
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError, "does not allow action 'noop'"
        ):
            manager.propose(region(leaf("a"), leaf("b")), [])

    def test_api_prompt_forbids_noop_when_durable_delta_is_required(self):
        client = slow_graph.DeepSeekFlashGraphPatchManager(
            slow_graph.DeepSeekFlashConfig(
                "http://example.invalid", ("key",), 100
            )
        )
        messages = client._messages(
            {
                "region_key": "coffee.preference",
                "evidence": [slow_graph._public_leaf(leaf("a"))],
                "required_evidence_ids": ["a"],
            },
            [],
        )
        system = messages[0]["content"]
        self.assertIn("one or more create operations", system)
        self.assertIn("noop is forbidden", system)
        self.assertIn("region.required_evidence_ids", system)
        self.assertIn("evidence_role is authoritative", system)
        self.assertIn("cross_slot_conflict", system)
        self.assertIn("Do not return summary", system)
        self.assertIn("committed lossless summary", system)
        self.assertIn(
            "multiple support IDs only when their normalized evidence texts are identical",
            system,
        )
        self.assertIn(
            "One indivisible Fast evidence ID may itself name multiple parallel concrete referents",
            system,
        )
        self.assertIn("Never duplicate or split that evidence ID", system)
        self.assertIn(
            "record_state is challenged, superseded, or otherwise non-current",
            system,
        )
        self.assertIn(
            "A capsule is one reusable retrieval concept centered on the same real-world",
            system,
        )
        self.assertIn(
            "Different canonical slots, claim types, or timestamps alone do not justify separate capsules",
            system,
        )
        self.assertIn(
            "Use a singleton capsule only when no other supplied current claim shares its real-world topic",
            system,
        )
        self.assertIn(
            "a shared broad region such as business, work, schedule, goals, or reading does not justify grouping",
            system,
        )
        self.assertIn(
            "there must be a natural memory-retrieval question for which every claim",
            system,
        )

    def test_initial_multi_slot_partition_uses_pro_without_flash(self):
        pro = RecordingClient(
            {
                "operations": [
                    {
                        "action": "create",
                        "capsule_key": "bakery.pricing",
                        "claims": [
                            claim("a", slot="business.bakery.product"),
                            claim("b", slot="business.bakery.pricing"),
                        ],
                    },
                    {
                        "action": "create",
                        "capsule_key": "consulting.clients",
                        "claims": [claim("c", slot="business.consulting.clients")],
                    },
                ]
            }
        )
        flash = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)

        patch = manager.propose(
            region(
                leaf("a", slot="business.bakery.product"),
                leaf("b", slot="business.bakery.pricing"),
                leaf("c", slot="business.consulting.clients"),
            ),
            [],
        )

        self.assertEqual(len(flash.calls), 0)
        self.assertEqual(len(pro.calls), 1)
        self.assertEqual(len(patch["operations"]), 2)
        self.assertEqual(manager.last_call_metadata["route"], "pro")
        self.assertIn(
            "initial_multi_slot_semantic_partition",
            manager.last_call_metadata["route_reason"],
        )

    def test_existing_flash_prompt_is_delta_only(self):
        client = slow_graph.DeepSeekFlashGraphPatchManager(
            slow_graph.DeepSeekFlashConfig(
                "http://example.invalid", ("key",), 100
            )
        )
        messages = client._messages(
            {
                "region_key": "coffee.preference",
                "evidence": [slow_graph._public_leaf(leaf("b"))],
                "required_evidence_ids": ["b"],
            },
            [
                {
                    "capsule_id": "cap_x",
                    "revision": 1,
                    "claims": [
                        {
                            "canonical_slot": "preference.beverage",
                            "text": "prefers coffee",
                            "support": ["a"],
                            "counterevidence": [],
                        }
                    ],
                }
            ],
        )
        system = messages[0]["content"]
        self.assertIn("additive delta proposal", system)
        self.assertIn("Do not repeat or cite existing capsule claims", system)
        self.assertIn("controller will merge", system)
        self.assertIn("different semantic topic", system)

    def test_flash_delta_cannot_drop_current_durable_evidence(self):
        capsule = [
            {
                "capsule_id": "cap_x",
                "revision": 1,
                "status": "active",
                "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
                "claims": [
                    {
                        "claim_id": "c1",
                        "canonical_slot": "preference.beverage",
                        "text": "prefers coffee",
                        "support": ["a"],
                        "counterevidence": [],
                    }
                ],
            }
        ]
        flash = RecordingClient(
            {
                "operations": [
                    {
                        "action": "revise",
                        "capsule_id": "cap_x",
                        "base_revision": 1,
                        "claims": [
                            {
                                "canonical_slot": "preference.tea",
                                "text": "prefers tea",
                                "support": ["b"],
                                "counterevidence": [],
                            }
                        ],
                    }
                ]
            }
        )
        manager = slow_graph.TieredGraphPatchManager(
            flash=flash, pro=RecordingClient({"operations": []})
        )
        patch = manager.propose(
            region(
                leaf("a"),
                leaf("b", value="prefers tea", slot="preference.tea"),
            ),
            capsule,
        )
        claims = patch["operations"][0]["claims"]
        self.assertEqual(
            [(item["canonical_slot"], item["support"]) for item in claims],
            [
                ("preference.beverage", ["a"]),
                ("preference.tea", ["b"]),
            ],
        )
        self.assertEqual(
            manager.last_call_metadata["controller_delta_merge"][
                "prior_claim_count"
            ],
            1,
        )

    def test_existing_capsule_additive_delta_uses_flash(self):
        capsule = [{
            "capsule_id": "cap_x",
            "revision": 1,
            "status": "active",
            "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
            "claims": [{"claim_id": "c1", "canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["a"], "counterevidence": []}],
        }]
        flash = RecordingClient({"operations": [{"action": "revise", "capsule_id": "cap_x", "base_revision": 1, "claims": [{"canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["b"], "counterevidence": []}]}]})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=RecordingClient({"operations": []}))
        patch = manager.propose(region(leaf("a"), leaf("b")), capsule)
        self.assertEqual(len(flash.calls), 1)
        self.assertEqual(manager.last_call_metadata["route"], "flash")
        self.assertEqual(
            patch["operations"][0]["claims"][0]["support"], ["a", "b"]
        )
        self.assertEqual(
            [item["memory_id"] for item in flash.calls[0][0]["evidence"]], ["b"]
        )

    def test_capsule_without_current_support_is_retired_without_api(self):
        capsule = [{
            "capsule_id": "cap_x",
            "revision": 1,
            "status": "active",
            "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
            "claims": [{"claim_id": "c1", "canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["a"], "counterevidence": []}],
        }]
        flash = RecordingClient({"operations": []})
        pro = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        patch = manager.propose(region(leaf("a", state="quarantined")), capsule)
        self.assertEqual(patch["operations"][0]["action"], "retire")
        self.assertEqual(len(flash.calls), 0)
        self.assertEqual(len(pro.calls), 0)
        self.assertEqual(
            manager.last_call_metadata["route"],
            "deterministic_support_cleanup",
        )
        self.assertEqual(
            manager.last_call_metadata["support_cleanup"][
                "removed_support_ids"
            ],
            ["a"],
        )

    def test_active_capsule_with_only_challenged_support_is_retired_without_api(self):
        capsule = [
            {
                "capsule_id": "cap_x",
                "revision": 1,
                "status": "active",
                "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
                "claims": [{
                    "claim_id": "c1",
                    "canonical_slot": "opinion.literature.impact",
                    "text": "User doubts literature affects behavior.",
                    "support": ["a"],
                    "counterevidence": [],
                }],
            },
            {
                "capsule_id": "cap_y",
                "revision": 1,
                "status": "active",
                "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
                "claims": [{
                    "claim_id": "c2",
                    "canonical_slot": "opinion.literature.impact",
                    "text": "User believes literature affects behavior.",
                    "support": ["b"],
                    "counterevidence": [],
                }],
            },
        ]
        flash = RecordingClient({"operations": []})
        pro = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        patch = manager.propose(
            region(
                leaf(
                    "a",
                    value="User doubts literature affects behavior.",
                    slot="opinion.literature.impact",
                    state="challenged",
                ),
                leaf(
                    "b",
                    value="User believes literature affects behavior.",
                    slot="opinion.literature.impact",
                ),
            ),
            capsule,
        )
        self.assertEqual(patch["operations"][0]["action"], "retire")
        self.assertEqual(manager.last_call_metadata["route"], "deterministic_support_cleanup")
        self.assertEqual(len(flash.calls), 0)
        self.assertEqual(len(pro.calls), 0)

    def test_adjudicated_challenged_capsule_keeps_historical_support(self):
        capsule = [{
            "capsule_id": "cap_x",
            "revision": 1,
            "status": "challenged",
            "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
            "claims": [{
                "claim_id": "c1",
                "canonical_slot": "opinion.literature.impact",
                "text": "User doubts literature affects behavior.",
                "support": ["a"],
                "counterevidence": ["b"],
            }],
        }]
        sanitized, audit = slow_graph._sanitize_capsules_for_current_support(
            capsule,
            {"b"},
            {"a"},
            {"a", "b"},
        )
        self.assertFalse(audit["changed"])
        self.assertEqual(
            sanitized[0]["claims"][0]["support"],
            ["a"],
        )

    def test_mixed_capsule_drops_only_claim_without_current_support(self):
        capsule = [{
            "capsule_id": "cap_x",
            "revision": 2,
            "status": "active",
            "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
            "claims": [
                {"claim_id": "c1", "canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["a"], "counterevidence": []},
                {"claim_id": "c2", "canonical_slot": "preference.snack", "text": "prefers toast", "support": ["b"], "counterevidence": []},
            ],
        }]
        manager = slow_graph.TieredGraphPatchManager(
            flash=RecordingClient({"operations": []}),
            pro=RecordingClient({"operations": []}),
        )
        patch = manager.propose(
            region(
                leaf("a", state="quarantined"),
                leaf("b", value="prefers toast", slot="preference.snack"),
            ),
            capsule,
        )
        self.assertEqual(patch["operations"][0]["action"], "revise")
        self.assertEqual(
            [claim["support"] for claim in patch["operations"][0]["claims"]],
            [["b"]],
        )

    def test_inactive_prior_claim_is_not_merged_with_new_flash_delta(self):
        capsule = [{
            "capsule_id": "cap_x",
            "revision": 1,
            "status": "active",
            "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
            "claims": [{"claim_id": "c1", "canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["a"], "counterevidence": []}],
        }]
        flash = RecordingClient({"operations": [{
            "action": "revise",
            "capsule_id": "cap_x",
            "base_revision": 1,
            "claims": [{"canonical_slot": "preference.snack", "text": "prefers toast", "support": ["b"], "counterevidence": []}],
        }]})
        manager = slow_graph.TieredGraphPatchManager(
            flash=flash, pro=RecordingClient({"operations": []})
        )
        patch = manager.propose(
            region(
                leaf("a", state="quarantined"),
                leaf("b", value="prefers toast", slot="preference.snack"),
            ),
            capsule,
        )
        self.assertEqual(
            [claim["support"] for claim in patch["operations"][0]["claims"]],
            [["b"]],
        )
        self.assertTrue(manager.last_call_metadata["support_cleanup"]["changed"])

    def test_flash_delta_cannot_cite_existing_capsule_evidence(self):
        capsule = [{
            "capsule_id": "cap_x",
            "revision": 1,
            "status": "active",
            "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
            "claims": [{"claim_id": "c1", "canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["a"], "counterevidence": []}],
        }]
        flash = RecordingClient({"operations": [{"action": "revise", "capsule_id": "cap_x", "base_revision": 1, "claims": [{"canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["a", "b"], "counterevidence": []}]}]})
        manager = slow_graph.TieredGraphPatchManager(
            flash=flash, pro=RecordingClient({"operations": []})
        )
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError,
            "cited existing or non-delta evidence",
        ):
            manager.propose(region(leaf("a"), leaf("b")), capsule)

    def test_flash_delta_preserves_distinct_existing_claim_for_worker019_shape(self):
        capsule = [{
            "capsule_id": "cap_business",
            "revision": 1,
            "status": "active",
            "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
            "claims": [{
                "claim_id": "c1",
                "canonical_slot": "business.fact.migration.difficulty",
                "text": "User feels there are many things to give up to move.",
                "support": ["a"],
                "counterevidence": [],
            }],
        }]
        flash = RecordingClient({"operations": [{
            "action": "revise",
            "capsule_id": "cap_business",
            "base_revision": 1,
            "claims": [{
                "canonical_slot": "business.possession.current.platforms",
                "text": "User currently sells on three platforms.",
                "support": ["b"],
                "counterevidence": [],
            }],
        }]})
        manager = slow_graph.TieredGraphPatchManager(
            flash=flash, pro=RecordingClient({"operations": []})
        )
        patch = manager.propose(
            region(
                leaf("a", value="User feels there are many things to give up to move.", slot="business.fact.migration.difficulty"),
                leaf("b", value="User currently sells on three platforms.", slot="business.possession.current.platforms"),
            ),
            capsule,
        )
        self.assertEqual(
            [claim["canonical_slot"] for claim in patch["operations"][0]["claims"]],
            [
                "business.fact.migration.difficulty",
                "business.possession.current.platforms",
            ],
        )

    def test_replace_on_absent_slot_is_flash_compatible(self):
        capsule = [{
            "capsule_id": "cap_x",
            "revision": 1,
            "status": "active",
            "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
            "claims": [{"claim_id": "c1", "canonical_slot": "preference.beverage.morning", "text": "prefers coffee", "support": ["a"], "counterevidence": []}],
        }]
        flash = RecordingClient({"operations": [{"action": "revise", "capsule_id": "cap_x", "base_revision": 1, "claims": [{"canonical_slot": "preference.beverage.evening", "text": "prefers tea", "support": ["b"], "counterevidence": []}]}]})
        pro = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        patch = manager.propose(region(leaf("b", value="prefers tea", slot="preference.beverage.evening", operation="replace")), capsule)
        self.assertEqual(len(flash.calls), 1)
        self.assertEqual(len(pro.calls), 0)
        self.assertEqual(manager.last_call_metadata["route"], "flash")
        self.assertEqual(len(patch["operations"][0]["claims"]), 2)

    def test_replace_same_normalized_value_is_flash_compatible(self):
        capsule = [{
            "capsule_id": "cap_x",
            "revision": 1,
            "status": "active",
            "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
            "claims": [{"claim_id": "c1", "canonical_slot": "preference.beverage", "text": "Prefers   coffee", "support": ["a"], "counterevidence": []}],
        }]
        flash = RecordingClient({"operations": [{"action": "revise", "capsule_id": "cap_x", "base_revision": 1, "claims": [{"canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["b"], "counterevidence": []}]}]})
        pro = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        patch = manager.propose(region(leaf("a"), leaf("b", value="  PREFERS coffee ", operation="replace")), capsule)
        self.assertEqual(len(flash.calls), 1)
        self.assertEqual(len(pro.calls), 0)
        self.assertEqual(
            patch["operations"][0]["claims"][0]["support"], ["a", "b"]
        )

    def test_exact_flash_cross_slot_escalation_calls_pro_once(self):
        capsule = [
            {
                "capsule_id": "cap_x",
                "revision": 1,
                "status": "active",
                "partition_contract_version": slow_graph.SLOW_PARTITION_CONTRACT_VERSION,
                "claims": [
                    {
                        "claim_id": "c1",
                        "canonical_slot": "location.fact.current.city",
                        "text": "lives in Kansas City",
                        "support": ["a"],
                        "counterevidence": [],
                    }
                ],
            }
        ]
        flash = RecordingClient(
            {
                "operations": [
                    {
                        "action": "escalate",
                        "reason": slow_graph.FLASH_ESCALATION_REASON,
                    }
                ]
            }
        )
        pro = RecordingClient(
            {
                "operations": [
                    {
                        "action": "revise",
                        "capsule_id": "cap_x",
                        "base_revision": 1,
                        "claims": [
                            {
                                "canonical_slot": "location.fact.current.city",
                                "text": "lived in Kansas City",
                                "support": ["a"],
                                "counterevidence": [],
                            },
                            {
                                "canonical_slot": "location.identity.residence",
                                "text": "now lives in the San Francisco Bay Area",
                                "support": ["b"],
                                "counterevidence": [],
                            },
                        ],
                    }
                ]
            }
        )
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        patch = manager.propose(
            region(
                leaf(
                    "a",
                    value="lives in Kansas City",
                    slot="location.fact.current.city",
                ),
                leaf(
                    "b",
                    value="lives in the San Francisco Bay Area",
                    slot="location.identity.residence",
                ),
            ),
            capsule,
        )
        self.assertEqual(patch["operations"][0]["action"], "revise")
        self.assertEqual(len(flash.calls), 1)
        self.assertEqual(len(pro.calls), 1)
        self.assertEqual(
            [item["memory_id"] for item in flash.calls[0][0]["evidence"]],
            ["b"],
        )
        self.assertEqual(
            [item["memory_id"] for item in pro.calls[0][0]["evidence"]],
            ["a", "b"],
        )
        metadata = manager.last_call_metadata
        self.assertEqual(metadata["route"], "flash_to_pro")
        self.assertEqual(
            metadata["route_reason"],
            "flash_escalation:cross_slot_conflict",
        )
        self.assertEqual(metadata["physical_api_calls"], 2)
        self.assertEqual(metadata["attempt_count"], 2)
        self.assertEqual(metadata["usage"]["prompt_tokens"], 20)
        self.assertEqual(metadata["usage"]["completion_tokens"], 8)
        self.assertEqual(metadata["cost_audit"]["estimated_cost"], .5)
        self.assertEqual(len(metadata["tier_calls"]), 2)

    def test_malformed_flash_escalation_never_calls_pro(self):
        flash = RecordingClient(
            {
                "operations": [
                    {"action": "escalate", "reason": "unspecified"}
                ]
            }
        )
        pro = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        with self.assertRaises(slow_graph.PatchValidationError):
            manager.propose(region(leaf("a"), leaf("b")), [])
        self.assertEqual(len(flash.calls), 1)
        self.assertEqual(len(pro.calls), 0)
        self.assertEqual(manager.last_call_metadata["route"], "flash")

    def test_pro_create_cannot_commit_counterevidence_as_active(self):
        flash = RecordingClient(
            {
                "operations": [
                    {
                        "action": "escalate",
                        "reason": slow_graph.FLASH_ESCALATION_REASON,
                    }
                ]
            }
        )
        pro = RecordingClient({"operations": [{
            "action": "create",
            "claims": [{
                "canonical_slot": "location.current.city",
                "text": "User is in Kansas City.",
                "support": ["a"],
                "counterevidence": ["b"],
            }],
        }]})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError,
            "create cannot commit unresolved counterevidence",
        ):
            manager.propose(
                region(
                    leaf("a", value="User is in Kansas City.", slot="location.current.city"),
                    leaf("b", value="User is in the Bay Area.", slot="location.residence"),
                ),
                [],
            )

    def test_pro_reciprocal_counterevidence_cycle_is_rejected(self):
        capsule = [{
            "capsule_id": "cap_x",
            "revision": 1,
            "status": "active",
            "claims": [{"claim_id": "c1", "canonical_slot": "location.current.city", "text": "User is in Kansas City.", "support": ["a"], "counterevidence": []}],
        }]
        pro = RecordingClient({"operations": [{
            "action": "challenge",
            "capsule_id": "cap_x",
            "base_revision": 1,
            "claims": [
                {"canonical_slot": "location.current.city", "text": "User is in Kansas City.", "support": ["a"], "counterevidence": ["b"]},
                {"canonical_slot": "location.residence", "text": "User is in the Bay Area.", "support": ["b"], "counterevidence": ["a"]},
            ],
        }]})
        manager = slow_graph.TieredGraphPatchManager(
            flash=RecordingClient({"operations": []}), pro=pro
        )
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError,
            "support-only|reciprocal counterevidence",
        ):
            manager.propose(
                region(
                    leaf("a", value="User is in Kansas City.", slot="location.current.city"),
                    leaf("b", value="User is in the Bay Area.", slot="location.residence", counterevidence=True),
                ),
                capsule,
            )

    def test_compound_support_can_bind_distinct_slow_claims(self):
        compound = leaf(
            "a",
            value="User has a dog named Max and a cat named Luna.",
            slot="memory.user.pets.identity.pet.names",
        )
        current_region = region(compound)
        patch_value = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "pets.max",
                    "claims": [
                        claim(
                            "a",
                            slot="memory.user.pets.identity.pet.names",
                            text="User has a dog named Max.",
                        )
                    ],
                },
                {
                    "action": "create",
                    "capsule_key": "pets.luna",
                    "claims": [
                        claim(
                            "a",
                            slot="memory.user.pets.identity.pet.names",
                            text="User has a cat named Luna.",
                        )
                    ],
                },
            ]
        }
        slow_graph._validate_claim_evidence_contract(
            current_region, [], patch_value, route="pro"
        )
        slow_graph._validate_promotion_patch(current_region, [], patch_value)

    def test_compound_support_cannot_duplicate_the_same_semantic_claim(self):
        compound = leaf(
            "a",
            value="User has a dog named Max and a cat named Luna.",
            slot="memory.user.pets.identity.pet.names",
        )
        patch_value = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "pets.max.primary",
                    "claims": [
                        claim(
                            "a",
                            slot="memory.user.pets.identity.pet.names",
                            text="User has a dog named Max.",
                        )
                    ],
                },
                {
                    "action": "create",
                    "capsule_key": "pets.max.duplicate",
                    "claims": [
                        claim(
                            "a",
                            slot="memory.user.pets.identity.pet.names",
                            text="User has a dog named Max.",
                        )
                    ],
                },
            ]
        }
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError,
            "cannot duplicate a Fast evidence binding",
        ):
            slow_graph._validate_claim_evidence_contract(
                region(compound), [], patch_value, route="pro"
            )

    def test_parent_slot_can_synthesize_compatible_durable_subslot_support(self):
        base = leaf(
            "volunteering",
            value="User volunteers at the box office on Fridays.",
            slot="memory.user.schedule.routine.volunteering",
        )
        detail = leaf(
            "volunteering-time",
            value="Volunteering at the box office on Fridays is from 5 pm to 8 pm.",
            slot="memory.user.schedule.routine.volunteering.time",
        )
        for item in (base, detail):
            item["relation"] = "has_schedule"
            item["metadata"].update(
                {
                    "subject_signature": "schedule",
                    "graph_entity_key": "schedule",
                    "memory_family": "routine",
                    "semantic_slot": "has_schedule",
                }
            )
        patch_value = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "theater.volunteering",
                    "claims": [
                        {
                            "canonical_slot": "memory.user.schedule.routine.volunteering",
                            "text": "User volunteers at the box office on Fridays from 5 pm to 8 pm.",
                            "support": ["volunteering", "volunteering-time"],
                            "counterevidence": [],
                        }
                    ],
                }
            ]
        }
        current_region = region(base, detail)
        slow_graph._validate_claim_evidence_contract(
            current_region, [], patch_value, route="pro"
        )
        slow_graph._validate_promotion_patch(current_region, [], patch_value)

    def test_subslot_synthesis_rejects_reversed_or_incompatible_identity(self):
        base = leaf(
            "volunteering",
            value="User volunteers at the box office on Fridays.",
            slot="memory.user.schedule.routine.volunteering",
        )
        detail = leaf(
            "volunteering-time",
            value="Volunteering is from 5 pm to 8 pm.",
            slot="memory.user.schedule.routine.volunteering.time",
        )
        for item in (base, detail):
            item["relation"] = "has_schedule"
            item["metadata"].update(
                {
                    "subject_signature": "schedule",
                    "graph_entity_key": "schedule",
                    "memory_family": "routine",
                    "semantic_slot": "has_schedule",
                }
            )
        reversed_patch = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "theater.volunteering",
                    "claims": [
                        {
                            "canonical_slot": "memory.user.schedule.routine.volunteering.time",
                            "text": "User volunteers at the box office on Fridays from 5 pm to 8 pm.",
                            "support": ["volunteering", "volunteering-time"],
                            "counterevidence": [],
                        }
                    ],
                }
            ]
        }
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError, "canonical slot mismatch"
        ):
            slow_graph._validate_claim_evidence_contract(
                region(base, detail), [], reversed_patch, route="pro"
            )

        detail["metadata"]["graph_entity_key"] = "unrelated-schedule"
        parent_patch = json.loads(json.dumps(reversed_patch))
        parent_patch["operations"][0]["claims"][0]["canonical_slot"] = (
            "memory.user.schedule.routine.volunteering"
        )
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError, "canonical slot mismatch"
        ):
            slow_graph._validate_claim_evidence_contract(
                region(base, detail), [], parent_patch, route="pro"
            )

    def test_semantic_policy_classifier_accepts_reviewed_compound_binding_error(self):
        error = (
            slow_graph.LEGACY_SINGLE_BINDING_ERROR_PREFIX
            + '["memory.user.pets.identity.pet.names:82:0"]'
        )
        self.assertEqual(
            slow_graph._semantic_policy_failure_class(error),
            "compound_support_binding_policy",
        )
        self.assertEqual(
            slow_graph._semantic_policy_failure_class(
                slow_graph.LEGACY_SINGLE_BINDING_ERROR_PREFIX + "not-json"
            ),
            "",
        )
        cross_slot_error = (
            "claim support canonical slot mismatch: "
            "claim=memory.user.schedule.routine.volunteering "
            "evidence=memory.user.schedule.routine.volunteering.time "
            "id=memory.user.schedule.routine.volunteering.time:452:0"
        )
        self.assertEqual(
            slow_graph._semantic_policy_failure_class(cross_slot_error),
            "complementary_subslot_support_policy",
        )
        self.assertEqual(
            slow_graph._semantic_policy_failure_class(
                "claim support canonical slot mismatch: "
                "claim=memory.user.preference.food "
                "evidence=memory.user.business.ownership id=b"
            ),
            "",
        )

    def test_challenged_fast_leaf_cannot_create_new_active_support(self):
        pro = RecordingClient(
            {
                "operations": [
                    {
                        "action": "create",
                        "capsule_key": "literature.impact.skepticism",
                        "claims": [
                            claim(
                                "a",
                                slot="opinion.literature.impact",
                                text="User doubts literature affects behavior.",
                            )
                        ],
                    }
                ]
            }
        )
        manager = slow_graph.TieredGraphPatchManager(pro=pro)
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError,
            "non-current Fast evidence cannot become a new claim support",
        ):
            manager.propose(
                region(
                    leaf(
                        "a",
                        value="User doubts literature affects behavior.",
                        slot="opinion.literature.impact",
                        state="challenged",
                    ),
                    leaf(
                        "b",
                        value="User believes people are responsible for their actions.",
                        slot="opinion.personal.responsibility",
                    ),
                ),
                [],
            )

    def test_historical_challenged_support_requires_replacement_counterevidence(self):
        capsule = [
            {
                "capsule_id": "cap_x",
                "revision": 1,
                "status": "active",
                "claims": [
                    {
                        "claim_id": "c1",
                        "canonical_slot": "opinion.literature.impact",
                        "text": "User doubts literature affects behavior.",
                        "support": ["a"],
                        "counterevidence": [],
                    }
                ],
            }
        ]
        pro = RecordingClient(
            {
                "operations": [
                    {
                        "action": "revise",
                        "capsule_id": "cap_x",
                        "base_revision": 1,
                        "claims": [
                            {
                                "canonical_slot": "opinion.literature.impact",
                                "text": "User doubts literature affects behavior.",
                                "support": ["a"],
                                "counterevidence": [],
                            }
                        ],
                    }
                ]
            }
        )
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError,
            "historical non-current support requires current replacement counterevidence",
        ):
            slow_graph._validate_claim_evidence_contract(
                region(
                    leaf(
                        "a",
                        value="User doubts literature affects behavior.",
                        slot="opinion.literature.impact",
                        state="challenged",
                    ),
                    leaf(
                        "b",
                        value="User believes literature affects behavior.",
                        slot="opinion.literature.impact",
                    ),
                ),
                capsule,
                pro.patch,
                route="pro",
            )

    def test_same_slot_correction_uses_pro_and_never_flash(self):
        capsule = [{
            "capsule_id": "cap_x",
            "revision": 1,
            "status": "active",
            "claims": [{"claim_id": "c1", "canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["a"], "counterevidence": []}],
        }]
        pro = RecordingClient({"operations": [{"action": "revise", "capsule_id": "cap_x", "base_revision": 1, "claims": [{"canonical_slot": "preference.beverage", "text": "prefers tea", "support": ["b"], "counterevidence": ["a"]}]}]})
        flash = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        manager.propose(region(leaf("a"), leaf("b", value="prefers tea", operation="replace")), capsule)
        self.assertEqual(len(pro.calls), 1)
        self.assertEqual(len(flash.calls), 0)
        self.assertIn("same_slot_correction", manager.last_call_metadata["route_reason"])

    def test_counterevidence_and_unresolved_challenge_use_pro(self):
        capsule = [{
            "capsule_id": "cap_x",
            "revision": 2,
            "status": "challenged",
            "claims": [{"claim_id": "c1", "canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["a"], "counterevidence": []}],
        }]
        pro = RecordingClient({"operations": [{"action": "resolve_challenge", "capsule_id": "cap_x", "base_revision": 2, "claims": [{"canonical_slot": "preference.beverage", "text": "prefers tea", "support": ["b"], "counterevidence": ["a", "c"]}]}]})
        manager = slow_graph.TieredGraphPatchManager(flash=RecordingClient({"operations": []}), pro=pro)
        manager.propose(region(leaf("a"), leaf("b", value="prefers tea"), leaf("c", polarity="negative", counterevidence=True)), capsule)
        self.assertEqual(len(pro.calls), 1)
        self.assertIn("counterevidence", manager.last_call_metadata["route_reason"])
        self.assertIn("unresolved_challenge", manager.last_call_metadata["route_reason"])

    def test_challenged_fast_leaf_routes_existing_capsule_to_pro(self):
        capsule = [{
            "capsule_id": "cap_x",
            "revision": 1,
            "status": "active",
            "claims": [{"claim_id": "c1", "canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["a"], "counterevidence": []}],
        }]
        pro = RecordingClient({"operations": [{"action": "challenge", "capsule_id": "cap_x", "base_revision": 1, "claims": [{"canonical_slot": "preference.beverage", "text": "preference is unresolved", "support": ["a"], "counterevidence": ["b"]}]}]})
        flash = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        manager.propose(
            region(
                leaf("a"),
                leaf("b", value="prefers tea", state="challenged", operation="replace"),
            ),
            capsule,
        )
        self.assertEqual(len(pro.calls), 1)
        self.assertEqual(len(flash.calls), 0)
        self.assertIn("unresolved_fast_challenge", manager.last_call_metadata["route_reason"])
        self.assertEqual(manager.last_call_metadata["challenged_evidence_ids"], ["b"])

    def test_challenged_leaf_cannot_create_slow_memory_and_superseded_is_ignored(self):
        flash = RecordingClient({"operations": []})
        pro = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        patch = manager.propose(
            region(
                leaf("challenged", state="challenged"),
                leaf("old", state="superseded"),
            ),
            [],
        )
        self.assertEqual(patch, {"operations": [{"action": "noop"}]})
        self.assertEqual(len(flash.calls), 0)
        self.assertEqual(len(pro.calls), 0)
        self.assertEqual(manager.last_call_metadata["challenged_evidence_ids"], ["challenged"])
        self.assertEqual(manager.last_call_metadata["inactive_evidence_ids"], ["old"])

    def test_invalid_flash_does_not_fallback_to_pro(self):
        flash = RecordingClient({"operations": [{"action": "create", "claims": []}]})
        pro = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        with self.assertRaises(slow_graph.PatchValidationError):
            manager.propose(region(leaf("a"), leaf("b")), [])
        self.assertEqual(len(flash.calls), 1)
        self.assertEqual(len(pro.calls), 0)

    def test_route_specific_actions_fail_without_fallback(self):
        flash = RecordingClient({"operations": [{"action": "revise", "capsule_id": "cap_x", "base_revision": 1, "claims": [{"canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["a"], "counterevidence": []}]}]})
        pro = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=pro)
        with self.assertRaises(slow_graph.PatchValidationError):
            manager.propose(region(leaf("a"), leaf("b")), [])
        self.assertEqual(len(pro.calls), 0)

        flash = RecordingClient({"operations": [{"action": "challenge", "capsule_id": "cap_x", "base_revision": 1, "claims": [{"canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["a"], "counterevidence": []}]}]})
        manager = slow_graph.TieredGraphPatchManager(flash=flash, pro=RecordingClient({"operations": []}))
        capsule = [{"capsule_id": "cap_x", "revision": 1, "status": "active", "claims": [{"claim_id": "c1", "canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["a"], "counterevidence": []}]}]
        with self.assertRaises(slow_graph.PatchValidationError):
            manager.propose(region(leaf("a"), leaf("b")), capsule)

        pro = RecordingClient({"operations": [{"action": "revise", "capsule_id": "cap_x", "base_revision": 1, "claims": [{"canonical_slot": "preference.beverage", "text": "prefers tea", "support": ["b"], "counterevidence": []}]}]})
        manager = slow_graph.TieredGraphPatchManager(pro=pro)
        with self.assertRaises(slow_graph.PatchValidationError):
            manager.propose(region(leaf("b", value="prefers tea", polarity="negative", counterevidence=True)), [])

    def test_cache_cost_uses_miss_hit_and_output_rates_separately(self):
        config = slow_graph.DeepSeekFlashConfig("http://example.invalid", ("key",), 20, prompt_cost_per_million=2.0, completion_cost_per_million=5.0, cache_cost_per_million=.5)
        client = slow_graph.DeepSeekFlashGraphPatchManager(config)

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def getcode(self):
                return 200

            def read(self):
                return json.dumps({"id": "r1", "choices": [{"finish_reason": "stop", "message": {"content": '{"operations":[{"action":"noop"}]}'}}], "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cached_tokens": 30, "total_tokens": 110}}).encode()

        with patch("urllib.request.urlopen", return_value=Response()):
            client.propose({"region_key": "x", "evidence": []}, [])
        usage = client.last_call_metadata["usage"]
        self.assertEqual(usage["cache_hit_tokens"], 30)
        self.assertEqual(usage["cache_miss_tokens"], 70)
        self.assertAlmostEqual(client.last_call_metadata["cost_audit"]["estimated_cost"], (70 * 2 + 30 * .5 + 10 * 5) / 1_000_000)

    def test_api_client_accepts_only_exact_flash_escalation_marker(self):
        config = slow_graph.DeepSeekFlashConfig(
            "http://example.invalid", ("key",), 20
        )
        client = slow_graph.DeepSeekFlashGraphPatchManager(config)
        marker = {
            "operations": [
                {
                    "action": "escalate",
                    "reason": slow_graph.FLASH_ESCALATION_REASON,
                }
            ]
        }

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def getcode(self):
                return 200

            def read(self):
                return json.dumps(
                    {
                        "id": "r-escalate",
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": json.dumps(marker)},
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 20,
                            "completion_tokens": 4,
                            "total_tokens": 24,
                        },
                    }
                ).encode()

        public_region = {
            "region_key": "location",
            "evidence": [
                slow_graph._public_leaf(
                    leaf("a", slot="location.fact.current.city")
                ),
                slow_graph._public_leaf(
                    leaf("b", slot="location.identity.residence")
                ),
            ],
            "required_evidence_ids": ["a", "b"],
        }
        with patch("urllib.request.urlopen", return_value=Response()):
            result = client.propose(public_region, [])
        self.assertEqual(result, marker)
        self.assertEqual(client.last_call_metadata["status"], "completed")
        self.assertTrue(client.last_call_metadata["escalation_requested"])
        self.assertEqual(
            client.last_call_metadata["escalation_reason"],
            slow_graph.FLASH_ESCALATION_REASON,
        )

    def test_api_client_normalizes_null_noop_identity_without_fallback(self):
        config = slow_graph.DeepSeekFlashConfig(
            "http://example.invalid", ("key",), 20
        )
        client = slow_graph.DeepSeekFlashGraphPatchManager(config)
        content = '{"operations":[{"action":"noop","capsule_id":null}]}'

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def getcode(self):
                return 200

            def read(self):
                return json.dumps(
                    {
                        "id": "r-null-noop",
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": content},
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 20,
                            "completion_tokens": 5,
                            "total_tokens": 25,
                        },
                    }
                ).encode()

        with patch("urllib.request.urlopen", return_value=Response()):
            result = client.propose({"region_key": "x", "evidence": []}, [])
        self.assertEqual(result, {"operations": [{"action": "noop"}]})
        self.assertEqual(
            client.last_call_metadata["transport_normalizations"][0]["code"],
            "noop_null_capsule_id_ignored",
        )
        self.assertNotEqual(
            client.last_call_metadata["raw_patch_sha256"],
            client.last_call_metadata["normalized_patch_sha256"],
        )
        self.assertEqual(client.last_call_metadata["status"], "completed")
        self.assertEqual(
            client.last_call_metadata["request_sha256"],
            slow_graph._digest(client.last_call_metadata["request"]),
        )
        self.assertEqual(
            client.last_call_metadata["request"]["headers"]["authorization"],
            "redacted",
        )

    def test_api_client_ignores_invented_noop_identity_only_without_capsules(self):
        config = slow_graph.DeepSeekFlashConfig(
            "http://example.invalid", ("key",), 20
        )
        content = '{"operations":[{"action":"noop","capsule_id":"invented"}]}'

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def getcode(self):
                return 200

            def read(self):
                return json.dumps(
                    {
                        "id": "r-invented-noop",
                        "choices": [
                            {"finish_reason": "stop", "message": {"content": content}}
                        ],
                        "usage": {
                            "prompt_tokens": 20,
                            "completion_tokens": 5,
                            "total_tokens": 25,
                        },
                    }
                ).encode()

        client = slow_graph.DeepSeekFlashGraphPatchManager(config)
        with patch("urllib.request.urlopen", return_value=Response()):
            result = client.propose({"region_key": "x", "evidence": []}, [])
        self.assertEqual(result, {"operations": [{"action": "noop"}]})
        self.assertEqual(
            client.last_call_metadata["transport_normalizations"][0]["code"],
            "noop_identity_ignored_without_supplied_capsule",
        )

        client = slow_graph.DeepSeekFlashGraphPatchManager(config)
        supplied = [{"capsule_id": "real", "revision": 1, "status": "active"}]
        with patch("urllib.request.urlopen", return_value=Response()):
            result = client.propose(
                {"region_key": "x", "evidence": []}, supplied
            )
        self.assertEqual(result["operations"][0]["capsule_id"], "invented")
        self.assertNotIn("transport_normalizations", client.last_call_metadata)

    def test_multiple_evidence_keyed_noops_collapse_only_when_all_are_pure(self):
        evidence = [
            {"memory_id": "a"},
            {"memory_id": "b"},
        ]
        patch_value = {
            "operations": [
                {"action": "noop", "capsule_id": "a"},
                {"action": "noop", "capsule_id": "b"},
            ]
        }
        normalized, normalizations = slow_graph._normalize_transport_patch(
            patch_value,
            [],
            {"region_key": "x", "evidence": evidence},
        )
        self.assertEqual(normalized, {"operations": [{"action": "noop"}]})
        self.assertEqual(
            normalizations[0]["code"],
            "multiple_evidence_keyed_noops_collapsed",
        )

        invalid = {
            "operations": [
                {"action": "noop", "capsule_id": "a"},
                {"action": "noop", "capsule_id": "not-supplied"},
            ]
        }
        self.assertEqual(
            slow_graph._normalize_transport_patch(
                invalid,
                [],
                {"region_key": "x", "evidence": evidence},
            ),
            (invalid, []),
        )

    def test_create_capsule_key_normalizes_only_ascii_transport_spelling(self):
        patch_value = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "User.Routine.Wake_Up",
                    "claims": [claim("a", slot="routine.wake")],
                },
                {
                    "action": "create",
                    "capsule_key": "user routine-morning__routine",
                    "claims": [claim("b", slot="routine.morning")],
                },
            ]
        }
        normalized, normalizations = slow_graph._normalize_transport_patch(
            patch_value, [], region(leaf("a"), leaf("b"))
        )
        self.assertEqual(
            [item["capsule_key"] for item in normalized["operations"]],
            ["user.routine.wake.up", "user.routine.morning.routine"],
        )
        self.assertEqual(
            [item["code"] for item in normalizations],
            [
                "create_capsule_key_ascii_separators_normalized",
                "create_capsule_key_ascii_separators_normalized",
            ],
        )
        slow_graph.validate_patch(normalized)

    def test_create_capsule_key_transport_normalization_does_not_hide_collision(self):
        patch_value = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "topic_one",
                    "claims": [claim("a", slot="topic.one")],
                },
                {
                    "action": "create",
                    "capsule_key": "topic.one",
                    "claims": [claim("b", slot="topic.two")],
                },
            ]
        }
        normalized, normalizations = slow_graph._normalize_transport_patch(
            patch_value, [], region(leaf("a"), leaf("b"))
        )
        self.assertEqual(len(normalizations), 1)
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError, "duplicate create capsule_key"
        ):
            slow_graph.validate_patch(normalized)

    def test_create_capsule_key_transport_normalization_rejects_unsafe_spelling(self):
        patch_value = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "topic/one",
                    "claims": [claim("a", slot="topic.one")],
                }
            ]
        }
        self.assertEqual(
            slow_graph._normalize_transport_patch(
                patch_value, [], region(leaf("a"))
            ),
            (patch_value, []),
        )
        with self.assertRaisesRegex(
            slow_graph.PatchValidationError, "capsule_key"
        ):
            slow_graph.validate_patch(patch_value)

    def test_api_client_binds_empty_support_only_to_unique_exact_evidence(self):
        config = slow_graph.DeepSeekFlashConfig(
            "http://example.invalid", ("key",), 100
        )
        client = slow_graph.DeepSeekFlashGraphPatchManager(config)
        evidence = {
            "memory_id": "memory.flavor:1",
            "canonical_slot": "memory.user.flavor.preference.gochujang",
            "value": "I love the sweet and spicy flavor of gochujang.",
        }
        content = json.dumps(
            {
                "operations": [
                    {
                        "action": "create",
                        "summary": "I love the sweet and spicy flavor of gochujang.",
                        "claims": [
                            {
                                "canonical_slot": evidence["canonical_slot"],
                                "text": "  I LOVE the sweet and spicy flavor of gochujang. ",
                                "support": [],
                                "counterevidence": [],
                            }
                        ],
                    }
                ]
            }
        )

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def getcode(self):
                return 200

            def read(self):
                return json.dumps(
                    {
                        "id": "r-empty-support",
                        "choices": [
                            {"finish_reason": "stop", "message": {"content": content}}
                        ],
                        "usage": {
                            "prompt_tokens": 20,
                            "completion_tokens": 5,
                            "total_tokens": 25,
                        },
                    }
                ).encode()

        with patch("urllib.request.urlopen", return_value=Response()):
            result = client.propose(
                {"region_key": "flavor", "evidence": [evidence]}, []
            )
        self.assertEqual(
            result["operations"][0]["claims"][0]["support"],
            ["memory.flavor:1"],
        )
        self.assertEqual(
            client.last_call_metadata["transport_normalizations"][0]["code"],
            "empty_support_bound_to_unique_exact_evidence",
        )

    def test_api_client_wraps_one_operation_object_as_a_singleton_list(self):
        config = slow_graph.DeepSeekProConfig(
            "http://example.invalid", ("key",), 100
        )
        client = slow_graph.DeepSeekProGraphPatchManager(config)
        content = '{"operations":{"action":"noop"}}'

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def getcode(self):
                return 200

            def read(self):
                return json.dumps(
                    {
                        "id": "r-single-operation",
                        "choices": [
                            {"finish_reason": "stop", "message": {"content": content}}
                        ],
                        "usage": {
                            "prompt_tokens": 20,
                            "completion_tokens": 5,
                            "total_tokens": 25,
                        },
                    }
                ).encode()

        with patch("urllib.request.urlopen", return_value=Response()):
            result = client.propose({"region_key": "food", "evidence": []}, [])
        self.assertEqual(result, {"operations": [{"action": "noop"}]})
        self.assertEqual(
            client.last_call_metadata["transport_normalizations"][0]["code"],
            "single_operation_object_wrapped_as_list",
        )

    def test_api_client_rejects_empty_support_for_synthesized_claim(self):
        config = slow_graph.DeepSeekFlashConfig(
            "http://example.invalid", ("key",), 100
        )
        client = slow_graph.DeepSeekFlashGraphPatchManager(config)
        content = json.dumps(
            {
                "operations": [
                    {
                        "action": "create",
                        "claims": [
                            {
                                "canonical_slot": "memory.user.flavor.preference",
                                "text": "User likes several bold flavors.",
                                "support": [],
                                "counterevidence": [],
                            }
                        ],
                    }
                ]
            }
        )

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def getcode(self):
                return 200

            def read(self):
                return json.dumps(
                    {
                        "id": "r-unbound-support",
                        "choices": [
                            {"finish_reason": "stop", "message": {"content": content}}
                        ],
                        "usage": {
                            "prompt_tokens": 20,
                            "completion_tokens": 5,
                            "total_tokens": 25,
                        },
                    }
                ).encode()

        with patch("urllib.request.urlopen", return_value=Response()):
            with self.assertRaises(slow_graph.TieredAPIError):
                client.propose(
                    {
                        "region_key": "flavor",
                        "evidence": [
                            {
                                "memory_id": "memory.flavor:1",
                                "canonical_slot": "memory.user.flavor.preference.gochujang",
                                "value": "I love gochujang.",
                            }
                        ],
                    },
                    [],
                )
        self.assertEqual(client.last_call_metadata["status"], "response_received")
        normalization_codes = {
            item["code"]
            for item in client.last_call_metadata.get("transport_normalizations", [])
        }
        self.assertIn(
            "create_capsule_key_bound_to_unique_claim_slot", normalization_codes
        )
        self.assertNotIn(
            "empty_support_bound_to_unique_exact_evidence", normalization_codes
        )

    def test_api_prompt_separates_output_contract_from_evidence_payload(self):
        config = slow_graph.DeepSeekFlashConfig(
            "http://example.invalid", ("key",), 20
        )
        client = slow_graph.DeepSeekFlashGraphPatchManager(config)
        messages = client._messages(region(leaf("a"), leaf("b")), [])
        payload = json.loads(messages[1]["content"])
        self.assertEqual(set(payload), {"region", "capsules"})
        self.assertNotIn("schema", payload)
        self.assertNotIn("controller_route", payload)
        self.assertIn(
            "top-level object must contain exactly one key named operations",
            messages[0]["content"],
        )
        self.assertIn("never echo the user envelope", messages[0]["content"])

    def test_pro_correction_prompt_keeps_one_formal_evidence_envelope(self):
        config = slow_graph.DeepSeekProConfig(
            "http://example.invalid", ("key",), 20
        )
        client = slow_graph.DeepSeekProGraphPatchManager(config)
        rejected = {
            "operations": [
                {
                    "action": "create",
                    "capsule_key": "preferences",
                    "claims": [claim("a")],
                }
            ]
        }
        messages = client._messages(
            region(leaf("a")),
            [],
            correction={
                "rejected_patch": rejected,
                "validation_error": "capsule_key is too generic",
            },
        )
        user_messages = [item for item in messages if item["role"] == "user"]
        self.assertEqual(len(user_messages), 1)
        self.assertEqual(
            set(json.loads(user_messages[0]["content"])),
            {"region", "capsules"},
        )
        self.assertIn("single allowed correction pass", messages[0]["content"])
        self.assertIn("capsule_key is too generic", messages[0]["content"])
        self.assertIn(json.dumps(rejected, sort_keys=True, separators=(",", ":")), messages[0]["content"])

    def test_pro_config_matches_tier_client_fields(self):
        config = slow_graph.DeepSeekProConfig("http://example.invalid", ("key",), 20)
        client = slow_graph.DeepSeekProGraphPatchManager(config)
        self.assertEqual(client.config.model, "deepseek-v4-pro")

    def test_api_client_accepts_operator_selected_model(self):
        client = slow_graph.DeepSeekFlashGraphPatchManager(
            slow_graph.DeepSeekTierConfig(
                "http://example.invalid", ("key",), 20, model="operator-graph-v1"
            )
        )
        self.assertEqual(client.config.model, "operator-graph-v1")

    def test_missing_flash_is_explicit_failure_without_pro_fallback(self):
        pro = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(pro=pro)
        with self.assertRaises(slow_graph.TieredAPIError):
            manager.propose(region(leaf("a"), leaf("b")), [])
        self.assertEqual(len(pro.calls), 0)
        self.assertEqual(manager.last_call_metadata["status"], "unavailable")

    def test_benchmark_fields_are_rejected_before_api_route(self):
        flash = RecordingClient({"operations": []})
        manager = slow_graph.TieredGraphPatchManager(flash=flash)
        with self.assertRaises(slow_graph.EvidencePolicyError):
            manager.propose({"region_key": "x", "evidence": [leaf("a")], "benchmark_question": "gold"}, [])
        self.assertEqual(len(flash.calls), 0)

    def test_store_audit_reports_route_counts_and_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = slow_graph.SlowGraphStore(path, schema=(Record, Edge))
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE records(scope_id TEXT,memory_id TEXT,category TEXT,slot_key TEXT,value TEXT,relation TEXT,anchor_concepts_json TEXT,evidence_anchors_json TEXT,salience REAL,confidence REAL,source_kind TEXT,turn_index INTEGER,state TEXT,supersedes_json TEXT,metadata_json TEXT,PRIMARY KEY(scope_id,memory_id))")
            for index, memory_id in enumerate(("a", "b"), 1):
                metadata = {
                    "content_variant": "product_semantic_memory",
                    "memory_layer": "fast",
                    "node_kind": "atomic_user_assertion",
                    "atomic_evidence_leaf": True,
                    "authority": "user_assertion",
                    "graph_entity_key": "coffee.preference",
                    "canonical_slot_key": "preference.beverage",
                    "durability": "durable",
                    "temporal_status": "current",
                    "polarity": "positive",
                    "session_index": 1,
                    "message_index": index,
                    "source_record_id": "source." + memory_id,
                    "event_id": "event." + memory_id,
                    "evidence_char_start": 0,
                    "evidence_char_end": 5,
                }
                con.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("scope", memory_id, "fact", memory_id, "prefers coffee", "states", "[]", "[]", .7, .8, "writer", index, "active", "[]", json.dumps(metadata)))
            con.commit()
            con.close()
            flash = RecordingClient({"operations": [{"action": "create", "claims": [{"canonical_slot": "preference.beverage", "text": "prefers coffee", "support": ["a", "b"], "counterevidence": []}]}]})
            manager = slow_graph.TieredGraphPatchManager(flash=flash)
            job = store.enqueue("scope", "coffee.preference", ["a", "b"], manager=manager)
            store.run_job(job, manager)
            audit = store.audit("scope")
            self.assertEqual(audit["route_counts"], {"flash": 1})
            self.assertEqual(audit["usage"]["physical_api_calls"], 1)
            self.assertEqual(audit["usage"]["prompt_tokens"], 10)
            self.assertEqual(audit["usage"]["estimated_cost"], .25)
            self.assertTrue(audit["promotion_coverage"]["complete"])
            self.assertEqual(
                audit["promotion_coverage"]["eligible_current_durable_count"], 2
            )
            self.assertEqual(
                audit["promotion_coverage"]["cited_current_durable_count"], 2
            )

    def test_store_audit_accepts_controlled_compound_support_fanout(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            with sqlite3.connect(path) as con:
                con.execute(
                    "UPDATE records SET value=? WHERE memory_id='b'",
                    ("prefers tea",),
                )
            con.close()
            patch_value = {
                "operations": [
                    {
                        "action": "create",
                        "capsule_key": "coffee.morning",
                        "claims": [
                            claim(
                                "a",
                                text="The user prefers coffee in the morning.",
                            )
                        ],
                    },
                    {
                        "action": "create",
                        "capsule_key": "coffee.social",
                        "claims": [
                            claim(
                                "a",
                                text="The user chooses coffee for social drinks.",
                            )
                        ],
                    },
                    {
                        "action": "create",
                        "capsule_key": "tea.preference",
                        "claims": [claim("b", text="prefers tea")],
                    },
                ]
            }
            manager = slow_graph.TieredGraphPatchManager(
                pro=RecordingClient(patch_value)
            )
            job = store.enqueue(
                "scope", "coffee.preference", ["a", "b"], manager=manager
            )
            store.run_job(job, manager)

            audit = store.audit("scope", require_promotion_coverage=True)

            self.assertTrue(audit["promotion_coverage"]["complete"])
            self.assertNotIn(
                "fast_evidence_assigned_multiple_times",
                {
                    issue["code"]
                    for issue in audit["promotion_coverage"]
                    ["semantic_integrity_issues"]
                },
            )

    def test_strict_audit_rejects_uncited_current_durable_fast_leaf(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = slow_graph.SlowGraphStore(path, schema=(Record, Edge))
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE records(scope_id TEXT,memory_id TEXT,category TEXT,slot_key TEXT,value TEXT,relation TEXT,anchor_concepts_json TEXT,evidence_anchors_json TEXT,salience REAL,confidence REAL,source_kind TEXT,turn_index INTEGER,state TEXT,supersedes_json TEXT,metadata_json TEXT,PRIMARY KEY(scope_id,memory_id))")
            metadata = {
                "content_variant": "product_semantic_memory",
                "memory_layer": "fast",
                "node_kind": "atomic_user_assertion",
                "atomic_evidence_leaf": True,
                "authority": "user_assertion",
                "graph_entity_key": "coffee.preference",
                "canonical_slot_key": "preference.beverage",
                "durability": "durable",
                "temporal_status": "current",
                "polarity": "positive",
                "session_index": 1,
                "message_index": 1,
                "source_record_id": "source.a",
                "event_id": "event.a",
                "evidence_char_start": 0,
                "evidence_char_end": 5,
            }
            con.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "scope",
                    "a",
                    "fact",
                    "a",
                    "prefers coffee",
                    "states",
                    "[]",
                    "[]",
                    .7,
                    .8,
                    "writer",
                    1,
                    "active",
                    "[]",
                    json.dumps(metadata),
                ),
            )
            con.commit()
            con.close()

            report = store.audit("scope")
            self.assertFalse(report["promotion_coverage"]["complete"])
            self.assertEqual(
                report["promotion_coverage"]["uncited_current_durable_ids"], ["a"]
            )
            with self.assertRaisesRegex(
                slow_graph.AuditError, "missing from active Slow claims"
            ):
                store.audit("scope", require_promotion_coverage=True)

    def test_promotion_coverage_rejects_persisted_semantic_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            with sqlite3.connect(path) as con:
                b_metadata = json.loads(
                    con.execute(
                        "SELECT metadata_json FROM records WHERE memory_id='b'"
                    ).fetchone()[0]
                )
                b_metadata["canonical_slot_key"] = "business.ownership"
                con.execute(
                    "UPDATE records SET metadata_json=? WHERE memory_id='b'",
                    (json.dumps(b_metadata),),
                )
                self.insert_capsule(con, "slow.corrupt", support=("a", "b"))
                capsule_metadata = json.loads(
                    con.execute(
                        "SELECT metadata_json FROM records WHERE memory_id='slow.corrupt'"
                    ).fetchone()[0]
                )
                capsule_metadata["patch_id"] = "patch.corrupt"
                capsule_metadata["claims"][0]["counterevidence"] = ["a"]
                con.execute(
                    "UPDATE records SET metadata_json=? WHERE memory_id='slow.corrupt'",
                    (json.dumps(capsule_metadata),),
                )
                con.execute(
                    "INSERT INTO slow_graph_patches VALUES(?,?,?,?,?,?,?,?)",
                    (
                        "patch.corrupt",
                        "job.corrupt",
                        "scope",
                        "coffee.preference",
                        "deepseek-v4-flash",
                        "{}",
                        json.dumps({"route": "flash"}),
                        1,
                    ),
                )
            con.close()

            coverage = store.promotion_coverage("scope")
            self.assertFalse(coverage["complete"])
            self.assertEqual(coverage["uncited_current_durable_ids"], [])
            codes = {
                issue["code"]
                for issue in coverage["semantic_integrity_issues"]
            }
            self.assertTrue(
                {
                    "support_canonical_slot_mismatch",
                    "counterevidence_identical_to_claim",
                    "flash_invented_counterevidence",
                    "same_evidence_support_and_counterevidence",
                    "fast_evidence_assigned_multiple_times",
                    "missing_lossless_summary_contract",
                }.issubset(codes),
            )

    def test_promotion_coverage_rejects_active_slow_supporting_quarantined_fast(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            with sqlite3.connect(path) as con:
                self.insert_capsule(con, "slow.corrupt", support=("a", "b"))
                con.execute("UPDATE records SET state='quarantined' WHERE memory_id='a'")
            con.close()
            coverage = store.promotion_coverage("scope")
            self.assertFalse(coverage["complete"])
            self.assertIn(
                "support_noncurrent_fast_leaf",
                {
                    issue["code"]
                    for issue in coverage["semantic_integrity_issues"]
                },
            )

    def test_promotion_coverage_rejects_one_claim_merging_distinct_fast_values(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            with sqlite3.connect(path) as con:
                con.execute(
                    "UPDATE records SET value=? WHERE memory_id='b'",
                    ("prefers tea",),
                )
                self.insert_capsule(con, "slow.corrupt", support=("a", "b"))
            con.close()

            coverage = store.promotion_coverage("scope")

            self.assertFalse(coverage["complete"])
            issues = [
                issue
                for issue in coverage["semantic_integrity_issues"]
                if issue["code"] == "support_distinct_fast_values_merged"
            ]
            self.assertEqual(len(issues), 1)
            self.assertEqual(
                [group["evidence_ids"] for group in issues[0]["evidence_groups"]],
                [["a"], ["b"]],
            )

    def test_promotion_coverage_uses_only_unique_latest_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            with sqlite3.connect(path) as con:
                self.insert_capsule(con, "cap.r1", revision=1, support=("a",))
                self.insert_capsule(con, "cap.r2", revision=2, support=("b",))
            con.close()

            coverage = store.promotion_coverage("scope")
            self.assertFalse(coverage["complete"])
            self.assertEqual(coverage["active_or_challenged_claim_count"], 1)
            self.assertEqual(coverage["cited_current_durable_count"], 1)
            self.assertEqual(coverage["uncited_current_durable_ids"], ["a"])

    def test_promotion_coverage_fails_closed_for_duplicate_or_incomplete_identity(self):
        cases = ("duplicate", "incomplete")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "memory.sqlite3"
                store = self.make_seeded_store(path)
                with sqlite3.connect(path) as con:
                    if case == "duplicate":
                        self.insert_capsule(con, "cap.r1", revision=1)
                        self.insert_capsule(con, "cap.r2a", revision=2)
                        self.insert_capsule(con, "cap.r2b", revision=2)
                    else:
                        self.insert_capsule(con, "cap.missing", capsule_id="", revision=1)
                con.close()

                coverage = store.promotion_coverage("scope")
                self.assertFalse(coverage["complete"])
                self.assertEqual(coverage["active_or_challenged_claim_count"], 0)

    def test_promotion_coverage_does_not_use_active_old_revision_when_latest_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            with sqlite3.connect(path) as con:
                self.insert_capsule(con, "cap.r1", revision=1, support=("a",))
                self.insert_capsule(
                    con,
                    "cap.r2",
                    revision=2,
                    state="superseded",
                    status="retired",
                    support=("a", "b"),
                )
            con.close()

            coverage = store.promotion_coverage("scope")
            self.assertFalse(coverage["complete"])
            self.assertEqual(coverage["active_or_challenged_claim_count"], 0)
            self.assertEqual(coverage["cited_current_durable_count"], 0)
            self.assertEqual(coverage["uncited_current_durable_ids"], ["a", "b"])

    def test_failed_complete_noop_cannot_be_revalidated_over_durable_delta(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = slow_graph.SlowGraphStore(path, schema=(Record, Edge))
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE records(scope_id TEXT,memory_id TEXT,category TEXT,slot_key TEXT,value TEXT,relation TEXT,anchor_concepts_json TEXT,evidence_anchors_json TEXT,salience REAL,confidence REAL,source_kind TEXT,turn_index INTEGER,state TEXT,supersedes_json TEXT,metadata_json TEXT,PRIMARY KEY(scope_id,memory_id))")
            metadata = {
                "content_variant": "product_semantic_memory",
                "memory_layer": "fast",
                "node_kind": "atomic_user_assertion",
                "atomic_evidence_leaf": True,
                "authority": "user_assertion",
                "graph_entity_key": "coffee.preference",
                "canonical_slot_key": "preference.beverage",
                "durability": "durable",
                "temporal_status": "current",
                "polarity": "positive",
                "session_index": 1,
                "message_index": 1,
                "source_record_id": "source.a",
                "event_id": "event.a",
                "evidence_char_start": 0,
                "evidence_char_end": 5,
            }
            con.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "scope",
                    "a",
                    "fact",
                    "a",
                    "prefers coffee",
                    "states",
                    "[]",
                    "[]",
                    .7,
                    .8,
                    "writer",
                    1,
                    "active",
                    "[]",
                    json.dumps(metadata),
                ),
            )
            con.commit()
            con.close()

            class FailedRawManager:
                model_config = {"model": "deepseek-v4-flash"}
                prompt_hash = "failed-raw-manager"
                last_call_metadata = {}

                def propose(self, current_region, current_capsules):
                    content = '{"operations":[{"action":"noop","capsule_id":"invented"}]}'
                    raw_response = json.dumps(
                        {
                            "id": "saved-call",
                            "choices": [
                                {
                                    "finish_reason": "stop",
                                    "message": {"content": content},
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 4,
                                "total_tokens": 14,
                            },
                        }
                    )
                    self.last_call_metadata = {
                        "route": "flash",
                        "route_reason": "compatible_consolidation",
                        "prompt_version": slow_graph.SLOW_PROMPT_VERSION,
                        "physical_api_call": True,
                        "physical_api_calls": 1,
                        "physical_call_id": "saved-physical-call",
                        "api_provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "attempt_count": 1,
                        "status": "completed",
                        "http_status": 200,
                        "finish_reason": "stop",
                        "content": content,
                        "raw_response": raw_response,
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 4,
                            "total_tokens": 14,
                        },
                        "cost_audit": {"estimated_cost": .25},
                    }
                    raise slow_graph.EvidencePolicyError(
                        "GraphPatch capsule_id does not belong to the current region"
                    )

            manager = FailedRawManager()
            job_id = store.enqueue(
                "scope", "coffee.preference", ["a"], manager=manager
            )
            with self.assertRaises(slow_graph.EvidencePolicyError):
                store.run_job(job_id, manager)

            with self.assertRaisesRegex(
                slow_graph.PatchValidationError,
                "does not allow action 'noop'|noop cannot consume uncited",
            ):
                slow_graph.revalidate_failed_raw_response(store, job_id)
            con = sqlite3.connect(path)
            self.assertEqual(
                con.execute(
                    "SELECT status FROM slow_graph_jobs WHERE job_id=?", (job_id,)
                ).fetchone()[0],
                "failed",
            )
            self.assertEqual(
                dict(
                    con.execute(
                        "SELECT status,count(*) FROM slow_graph_attempts GROUP BY status"
                    ).fetchall()
                ),
                {"failed": 1},
            )
            self.assertEqual(
                con.execute(
                    "SELECT count(*) FROM slow_graph_patches WHERE job_id=?", (job_id,)
                ).fetchone()[0],
                0,
            )
            failed_metadata = json.loads(
                con.execute(
                    "SELECT call_metadata_json FROM slow_graph_attempts "
                    "WHERE job_id=? AND status='failed'",
                    (job_id,),
                ).fetchone()[0]
            )
            con.close()
            self.assertEqual(failed_metadata["physical_api_calls"], 1)
            self.assertEqual(failed_metadata["cost_audit"]["estimated_cost"], .25)

    def test_failed_pro_validation_has_one_explicit_bounded_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            with sqlite3.connect(path) as con:
                for memory_id, slot, value in (
                    (
                        "a",
                        "memory.user.preferences.preference.cocktail.base",
                        "User prefers gin-based cocktails.",
                    ),
                    (
                        "b",
                        "memory.user.preferences.preference.vehicle.type",
                        "User prefers compact cars.",
                    ),
                ):
                    metadata = json.loads(
                        con.execute(
                            "SELECT metadata_json FROM records WHERE memory_id=?",
                            (memory_id,),
                        ).fetchone()[0]
                    )
                    metadata["graph_entity_key"] = "preferences"
                    metadata["canonical_slot_key"] = slot
                    con.execute(
                        "UPDATE records SET value=?,metadata_json=? WHERE memory_id=?",
                        (value, json.dumps(metadata), memory_id),
                    )
            con.close()

            invalid = {
                "operations": [
                    {
                        "action": "create",
                        "capsule_key": "preferences",
                        "claims": [
                            claim(
                                "a",
                                slot="memory.user.preferences.preference.cocktail.base",
                                text="User prefers gin-based cocktails.",
                            ),
                            claim(
                                "b",
                                slot="memory.user.preferences.preference.vehicle.type",
                                text="User prefers compact cars.",
                            ),
                        ],
                    }
                ]
            }

            class FailedProValidationManager:
                model_config = {"model": "deepseek-v4-tiered-slow-graph"}
                prompt_hash = "failed-pro-validation"
                last_call_metadata = {}

                def propose(self, current_region, current_capsules):
                    content = json.dumps(invalid)
                    self.last_call_metadata = {
                        "route": "pro",
                        "route_reason": "generic_region_semantic_management",
                        "prompt_version": slow_graph.SLOW_PROMPT_VERSION,
                        "physical_api_call": True,
                        "physical_api_calls": 2,
                        "physical_call_id": "failed-pro-physical-call",
                        "api_provider": "deepseek",
                        "model": "deepseek-v4-pro",
                        "attempt_count": 2,
                        "status": "semantic_correction_rejected",
                        "http_status": 200,
                        "finish_reason": "stop",
                        "content": content,
                        "raw_response": json.dumps(
                            {
                                "choices": [
                                    {
                                        "finish_reason": "stop",
                                        "message": {"content": content},
                                    }
                                ],
                                "usage": {
                                    "prompt_tokens": 10,
                                    "completion_tokens": 4,
                                    "total_tokens": 14,
                                },
                            }
                        ),
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 4,
                            "total_tokens": 14,
                        },
                        "cost_audit": {"estimated_cost": .25},
                    }
                    raise slow_graph.PatchValidationError(
                        "operations[0].capsule_key must name a concrete semantic topic"
                    )

            failed_manager = FailedProValidationManager()
            job_id = store.enqueue(
                "scope", "preferences", ["a", "b"], manager=failed_manager
            )
            with self.assertRaises(slow_graph.PatchValidationError):
                store.run_job(job_id, failed_manager)

            corrected = {
                "operations": [
                    {
                        "action": "create",
                        "capsule_key": "user.cocktail.preferences",
                        "claims": [
                            claim(
                                "a",
                                slot="memory.user.preferences.preference.cocktail.base",
                                text="User prefers gin-based cocktails.",
                            )
                        ],
                    },
                    {
                        "action": "create",
                        "capsule_key": "user.vehicle.preferences",
                        "claims": [
                            claim(
                                "b",
                                slot="memory.user.preferences.preference.vehicle.type",
                                text="User prefers compact cars.",
                            )
                        ],
                    },
                ]
            }
            pro = CorrectingRecordingClient(invalid, corrected)
            recovery_manager = slow_graph.TieredGraphPatchManager(pro=pro)
            result = slow_graph.resume_failed_model_validation(
                store, job_id, recovery_manager
            )
            self.assertTrue(result)
            with sqlite3.connect(path) as con:
                self.assertEqual(
                    con.execute(
                        "SELECT status FROM slow_graph_jobs WHERE job_id=?", (job_id,)
                    ).fetchone()[0],
                    "completed",
                )
                self.assertEqual(
                    dict(
                        con.execute(
                            "SELECT status,count(*) FROM slow_graph_attempts "
                            "WHERE job_id=? GROUP BY status",
                            (job_id,),
                        ).fetchall()
                    ),
                    {"completed": 1, "failed": 1},
                )
                completed_metadata = json.loads(
                    con.execute(
                        "SELECT call_metadata_json FROM slow_graph_attempts "
                        "WHERE job_id=? AND status='completed'",
                        (job_id,),
                    ).fetchone()[0]
                )
            con.close()
            self.assertEqual(completed_metadata["physical_api_calls"], 2)
            self.assertTrue(completed_metadata["semantic_correction_applied"])
            self.assertTrue(store.audit("scope")["promotion_coverage"]["complete"])
            with self.assertRaisesRegex(
                slow_graph.SlowGraphError, "one unclaimed failed attempt"
            ):
                slow_graph.resume_failed_model_validation(
                    store, job_id, recovery_manager
                )

    def test_semantic_policy_revalidation_replays_changed_final_two_call_pro_patch(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)
            slots = {
                "a": "memory.user.communication.preference.answer.format",
                "b": "memory.user.communication.preference.detail.level",
            }
            values = {
                "a": "User wants brief step-by-step answers.",
                "b": "User will ask when more detail is needed.",
            }
            with sqlite3.connect(path) as con:
                for memory_id in ("a", "b"):
                    metadata = json.loads(
                        con.execute(
                            "SELECT metadata_json FROM records WHERE memory_id=?",
                            (memory_id,),
                        ).fetchone()[0]
                    )
                    metadata["graph_entity_key"] = "communication"
                    metadata["canonical_slot_key"] = slots[memory_id]
                    con.execute(
                        "UPDATE records SET value=?,metadata_json=? WHERE memory_id=?",
                        (values[memory_id], json.dumps(metadata), memory_id),
                    )
            con.close()

            patch_value = {
                "operations": [
                    {
                        "action": "create",
                        "capsule_key": "communication.preferences",
                        "claims": [
                            claim("a", slot=slots["a"], text=values["a"]),
                            claim("b", slot=slots["b"], text=values["b"]),
                        ],
                    }
                ]
            }
            initial_patch_value = json.loads(json.dumps(patch_value))
            initial_patch_value["operations"][0]["claims"][0]["text"] = (
                "User requests concise step-by-step answers."
            )

            class FailedPolicyManager:
                model_config = {"model": "deepseek-v4-tiered-slow-graph"}
                prompt_hash = "failed-semantic-policy"
                last_call_metadata = {}

                @staticmethod
                def _call(call_id, request, content):
                    raw_response = json.dumps(
                        {
                            "id": call_id,
                            "choices": [
                                {
                                    "finish_reason": "stop",
                                    "message": {"content": content},
                                }
                            ],
                        }
                    )
                    return {
                        "route": "pro",
                        "prompt_version": slow_graph.SLOW_PROMPT_VERSION,
                        "physical_api_call": True,
                        "physical_api_calls": 1,
                        "physical_call_id": call_id,
                        "api_provider": "deepseek",
                        "model": "deepseek-v4-pro",
                        "attempt_count": 1,
                        "status": "completed",
                        "http_status": 200,
                        "finish_reason": "stop",
                        "content": content,
                        "raw_response": raw_response,
                        "request": request,
                        "request_sha256": slow_graph._digest(request),
                    }

                def propose(self, current_region, current_capsules):
                    initial_content = json.dumps(initial_patch_value)
                    corrected_content = json.dumps(patch_value)
                    public_region = {
                        "region_key": "communication",
                        "evidence": [
                            slow_graph._public_leaf(item)
                            for item in current_region["evidence"]
                        ],
                        "required_evidence_ids": ["a", "b"],
                        "semantic_partition_required": True,
                        "semantic_partition_mode": "manage",
                    }
                    initial_request = {
                        "messages": [
                            {"role": "system", "content": "initial"},
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {
                                        "region": public_region,
                                        "capsules": current_capsules,
                                    }
                                ),
                            },
                        ]
                    }
                    correction_request = {
                        "messages": [
                            {"role": "system", "content": "correction"},
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {
                                        "region": public_region,
                                        "capsules": current_capsules,
                                    }
                                ),
                            },
                        ]
                    }
                    initial = self._call(
                        "initial-call", initial_request, initial_content
                    )
                    initial["tier_stage"] = "initial_pro"
                    corrected = self._call(
                        "correction-call", correction_request, corrected_content
                    )
                    corrected["tier_stage"] = "semantic_correction"
                    error = (
                        "operations[0]."
                        + slow_graph.GENERIC_MULTI_SLOT_CAPSULE_KEY_ERROR
                    )
                    initial_hash = slow_graph._digest(initial_patch_value)
                    corrected_hash = slow_graph._digest(patch_value)
                    self.last_call_metadata = {
                        **corrected,
                        "route": "pro",
                        "route_reason": "generic_region_semantic_management",
                        "physical_api_calls": 2,
                        "attempt_count": 2,
                        "status": "semantic_correction_rejected",
                        "semantic_correction_attempted": True,
                        "semantic_correction_applied": False,
                        "semantic_correction_validation_error": error,
                        "semantic_correction_rejection_error": error,
                        "initial_rejected_patch_sha256": initial_hash,
                        "corrected_patch_sha256": corrected_hash,
                        "eligible_evidence_ids": ["a", "b"],
                        "challenged_evidence_ids": [],
                        "uncertain_evidence_ids": [],
                        "episodic_evidence_ids": [],
                        "inactive_evidence_ids": [],
                        "ignored_evidence_ids": [],
                        "required_operation_evidence_ids": ["a", "b"],
                        "tier_calls": [initial, corrected],
                    }
                    raise slow_graph.PatchValidationError(error)

            manager = FailedPolicyManager()
            job_id = store.enqueue(
                "scope", "communication", ["a", "b"], manager=manager
            )
            with self.assertRaises(slow_graph.PatchValidationError):
                store.run_job(job_id, manager)

            patch_id = slow_graph.revalidate_failed_semantic_policy_response(
                store, job_id
            )
            self.assertTrue(patch_id.startswith("sgp_"))
            with sqlite3.connect(path) as con:
                con.row_factory = sqlite3.Row
                statuses = dict(
                    con.execute(
                        "SELECT status,count(*) FROM slow_graph_attempts "
                        "WHERE job_id=? GROUP BY status",
                        (job_id,),
                    ).fetchall()
                )
                completed_metadata = json.loads(
                    con.execute(
                        "SELECT call_metadata_json FROM slow_graph_attempts "
                        "WHERE job_id=? AND status='completed'",
                        (job_id,),
                    ).fetchone()[0]
                )
            con.close()
            self.assertEqual(statuses, {"completed": 1, "failed": 1})
            self.assertEqual(
                completed_metadata["route"], "semantic_policy_revalidation"
            )
            self.assertEqual(completed_metadata["physical_api_calls"], 0)
            self.assertEqual(completed_metadata["original_physical_api_calls"], 2)
            self.assertTrue(
                completed_metadata["revalidation_details"]
                ["semantic_correction_changed_patch"]
            )
            self.assertTrue(store.audit("scope")["promotion_coverage"]["complete"])

    def test_failed_create_revalidation_reuses_saved_response_and_materializes_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = self.make_seeded_store(path)

            class FailedRawManager:
                model_config = {"model": "deepseek-v4-flash"}
                prompt_hash = "failed-create-manager"
                last_call_metadata = {}

                def propose(self, current_region, current_capsules):
                    operation = {
                        "action": "create",
                        "capsule_key": "Coffee_Preference",
                        "claims": [
                            {
                                "canonical_slot": "preference.beverage",
                                "text": "prefers coffee",
                                "support": ["a", "b"],
                                "counterevidence": [],
                            }
                        ],
                    }
                    content = json.dumps({"operations": [operation]})
                    public_region = {
                        "region_key": current_region["region_key"],
                        "evidence": [
                            slow_graph._public_leaf(item)
                            for item in current_region["evidence"]
                        ],
                        "required_evidence_ids": ["a", "b"],
                    }
                    raw_response = json.dumps(
                        {
                            "id": "saved-create-call",
                            "choices": [
                                {
                                    "finish_reason": "stop",
                                    "message": {"content": content},
                                }
                            ],
                        }
                    )
                    self.last_call_metadata = {
                        "route": "flash",
                        "route_reason": "compatible_consolidation",
                        "prompt_version": slow_graph.SLOW_PROMPT_VERSION,
                        "physical_api_call": True,
                        "physical_api_calls": 1,
                        "physical_call_id": "saved-create-physical-call",
                        "api_provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "attempt_count": 1,
                        "status": "response_received",
                        "http_status": 200,
                        "finish_reason": "stop",
                        "content": content,
                        "raw_response": raw_response,
                        "request": {
                            "messages": [
                                {
                                    "role": "user",
                                    "content": json.dumps(
                                        {
                                            "region": public_region,
                                            "capsules": current_capsules,
                                        }
                                    ),
                                }
                            ]
                        },
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 4,
                            "total_tokens": 14,
                        },
                        "cost_audit": {"estimated_cost": .25},
                    }
                    raise slow_graph.PatchValidationError(
                        "capsule_key transport spelling is invalid"
                    )

            manager = FailedRawManager()
            job_id = store.enqueue(
                "scope", "coffee.preference", ["a", "b"], manager=manager
            )
            with self.assertRaises(slow_graph.PatchValidationError):
                store.run_job(job_id, manager)

            with sqlite3.connect(path) as con:
                con.execute(
                    "INSERT INTO slow_graph_attempts("
                    "attempt_id,job_id,scope_id,status,call_metadata_json,error,"
                    "created_at,completed_at,claim_token,claim_owner) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        "later-zero-call-failure",
                        job_id,
                        "scope",
                        "failed",
                        json.dumps(
                            {
                                "status": "completed",
                                "physical_api_call": False,
                                "physical_api_calls": 0,
                            }
                        ),
                        "summary materialization failed before commit",
                        9999999999,
                        9999999999,
                        None,
                        None,
                    ),
                )
            con.close()

            patch_id = slow_graph.revalidate_failed_raw_response(store, job_id)
            self.assertTrue(patch_id.startswith("sgp_"))
            with sqlite3.connect(path) as con:
                con.row_factory = sqlite3.Row
                patch_value = json.loads(
                    con.execute(
                        "SELECT patch_json FROM slow_graph_patches WHERE job_id=?",
                        (job_id,),
                    ).fetchone()[0]
                )
                completed_metadata = json.loads(
                    con.execute(
                        "SELECT call_metadata_json FROM slow_graph_attempts "
                        "WHERE job_id=? AND status='completed'",
                        (job_id,),
                    ).fetchone()[0]
                )
                status = con.execute(
                    "SELECT status FROM slow_graph_jobs WHERE job_id=?", (job_id,)
                ).fetchone()[0]
            con.close()
            operation = patch_value["operations"][0]
            self.assertEqual(status, "completed")
            self.assertEqual(operation["capsule_key"], "coffee.preference")
            self.assertEqual(operation["summary"], "prefers coffee")
            self.assertEqual(completed_metadata["physical_api_calls"], 0)
            self.assertEqual(
                completed_metadata["original_physical_call_id"],
                "saved-create-physical-call",
            )
            self.assertIn(
                "controller_summary_materialization", completed_metadata
            )

    def test_fast_region_snapshot_carries_actual_record_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            store = slow_graph.SlowGraphStore(path, schema=(Record, Edge))
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE records(scope_id TEXT,memory_id TEXT,category TEXT,slot_key TEXT,value TEXT,relation TEXT,anchor_concepts_json TEXT,evidence_anchors_json TEXT,salience REAL,confidence REAL,source_kind TEXT,turn_index INTEGER,state TEXT,supersedes_json TEXT,metadata_json TEXT,PRIMARY KEY(scope_id,memory_id))")
            metadata = {
                "content_variant": "product_semantic_memory",
                "memory_layer": "fast",
                "node_kind": "atomic_user_assertion",
                "atomic_evidence_leaf": True,
                "authority": "user_assertion",
                "graph_entity_key": "coffee.preference",
                "canonical_slot_key": "preference.beverage",
            }
            con.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("scope", "old", "fact", "old", "prefers coffee", "states", "[]", "[]", .7, .8, "writer", 1, "superseded", "[]", json.dumps(metadata)))
            con.commit()
            con.close()
            fast = store.fast_regions("scope")["coffee.preference"][0]
            self.assertEqual(fast["record_state"], "superseded")
            self.assertEqual(fast["metadata"]["record_state"], "superseded")


if __name__ == "__main__":
    unittest.main()
