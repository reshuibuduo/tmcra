from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tmcra_service.control_db import ControlDB
from tmcra_service.jobs import (
    PENDING,
    RUNNING,
    STAGE_READY,
    STAGE_SUCCEEDED,
    JobStateError,
    JobStore,
)


class ProductionLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "control.sqlite3"
        self.db = ControlDB(self.path)
        self.jobs = JobStore(self.db, lease_seconds=10)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_legacy_jobs_are_migrated_and_scoped(self) -> None:
        legacy_path = Path(self.tempdir.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE jobs(
                    job_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL, state TEXT NOT NULL,
                    result_json TEXT, error TEXT, worker_id TEXT, created_at REAL NOT NULL,
                    updated_at REAL NOT NULL, started_at REAL, finished_at REAL,
                    version INTEGER NOT NULL DEFAULT 0, UNIQUE(tenant_id, idempotency_key)
                );
                INSERT INTO jobs(job_id, tenant_id, idempotency_key, payload_json, payload_hash, state, created_at, updated_at)
                VALUES ('old-1', 'tenant-a', 'old', '{"scope_name":"alpha"}', 'hash', 'succeeded', 1, 1);
                CREATE TABLE scope_evolution_state(
                    tenant_id TEXT NOT NULL, scope_name TEXT NOT NULL,
                    source_event_seq INTEGER NOT NULL DEFAULT 0,
                    promoted_event_seq INTEGER NOT NULL DEFAULT 0,
                    conflict_generation INTEGER NOT NULL DEFAULT 0,
                    promoted_conflict_generation INTEGER NOT NULL DEFAULT 0,
                    last_ingest_at REAL, last_slow_success_at REAL,
                    active_evolution_job_id TEXT,
                    reserved_cost_micro_cny INTEGER NOT NULL DEFAULT 0,
                    spent_cost_micro_cny INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(tenant_id, scope_name)
                );
                """
            )
        migrated = ControlDB(legacy_path)
        with closing(migrated.connect()) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            row = connection.execute("SELECT scope_name, scope_seq FROM jobs WHERE job_id='old-1'").fetchone()
        self.assertTrue({"scope_name", "scope_seq", "heartbeat_at", "lease_expires_at"} <= columns)
        self.assertTrue({
            "scope_heads", "scope_evolution_state", "scope_ingest_watermark_commits",
            "operation_stages", "provider_calls", "provider_prices",
        } <= tables)
        self.assertEqual(tuple(row), ("alpha", 1))
        self.assertEqual(migrated.allocate_scope_seq("tenant-a", "alpha"), 2)
        with closing(migrated.connect()) as connection:
            evolution_columns = {row[1] for row in connection.execute("PRAGMA table_info(scope_evolution_state)")}
        self.assertTrue({
            "indexed_event_seq", "last_index_success_at", "active_index_job_id",
            "source_raw_token_estimate", "promoted_raw_token_estimate",
            "source_user_turns", "promoted_user_turns", "dirty_since_at",
            "index_dirty_since_at",
        } <= evolution_columns)

    def test_scope_sequence_and_ready_claim_are_per_scope(self) -> None:
        first = self.jobs.submit("tenant-a", "one", {}, scope_name="alpha")
        second = self.jobs.submit("tenant-a", "two", {}, scope_name="alpha")
        other = self.jobs.submit("tenant-a", "other", {}, scope_name="beta")
        derived = self.jobs.submit(
            "tenant-b", "derived", {"scope_name": "payload-scope"}
        )
        self.assertEqual((first.scope_name, first.scope_seq), ("alpha", 1))
        self.assertEqual(second.scope_seq, 2)
        self.assertEqual(other.scope_seq, 1)
        self.assertEqual((derived.scope_name, derived.scope_seq), ("payload-scope", 1))
        self.assertEqual(self.jobs.claim_next("worker-1").job_id, first.job_id)  # type: ignore[union-attr]
        self.assertEqual(self.jobs.claim_next("worker-2").job_id, other.job_id)  # type: ignore[union-attr]
        self.assertIsNone(self.jobs.claim_next("worker-3", scope_name="alpha"))
        self.jobs.succeed(first.job_id, {}, worker_id="worker-1")
        self.assertEqual(self.jobs.claim_next("worker-3", scope_name="alpha").job_id, second.job_id)  # type: ignore[union-attr]

    def test_stage_lifecycle_claims_ready_stage_in_order(self) -> None:
        first = self.jobs.create_stage("tenant-a", "alpha", "slow", stage_seq=0)
        second = self.jobs.create_stage("tenant-a", "alpha", "index", stage_seq=1)
        self.assertEqual(first.state, STAGE_READY)
        claimed = self.jobs.claim_ready_stage("stage-worker", scope_name="alpha")
        self.assertEqual(claimed.stage_id, first.stage_id)  # type: ignore[union-attr]
        self.assertIsNone(self.jobs.claim_ready_stage("other-worker", scope_name="alpha"))
        done = self.jobs.complete_stage(first.stage_id, {"committed": True}, worker_id="stage-worker")
        self.assertEqual(done.state, STAGE_SUCCEEDED)
        self.assertEqual(self.jobs.claim_next_ready_stage("stage-worker", scope_name="alpha").stage_id, second.stage_id)  # type: ignore[union-attr]

    def test_provider_ledger_and_price_are_durable(self) -> None:
        call = self.jobs.record_provider_call(
            "tenant-a", "provider-a", "model-a", scope_name="alpha", request={"x": 1},
            response={"ok": True}, cost_micro_cny=12,
        )
        self.assertEqual(self.jobs.get_provider_call(call.call_id).response, {"ok": True})  # type: ignore[union-attr]
        price = self.jobs.upsert_provider_price(
            "provider-a", "model-a", input_micro_cny_per_million=100,
            output_micro_cny_per_million=200, effective_at=10,
        )
        self.assertEqual(self.jobs.get_provider_price("provider-a", "model-a", at=10).output_micro_cny_per_million, 200)  # type: ignore[union-attr]
        self.assertEqual(price.currency, "CNY")

    def test_provider_call_lifecycle_is_idempotent_and_identity_is_immutable(self) -> None:
        started = self.jobs.record_provider_call(
            "tenant-a", "deepseek", "deepseek-v4-flash", scope_name="alpha",
            call_id="physical-1", job_id="job-1", stage_id="stage-1",
            key_id="key-1", request_sha256="request-1", status="started",
        )
        self.assertEqual(started.status, "started")
        completed = self.jobs.transition_provider_call(
            "physical-1", "completed", input_tokens=10, output_tokens=2,
            cache_hit_tokens=4, cache_miss_tokens=6, usage_state="complete",
            price_version="price-1", cost_micro_cny=123, response_sha256="response-1",
        )
        repeated = self.jobs.transition_provider_call(
            "physical-1", "completed", cost_micro_cny=999, response_sha256="changed",
        )
        self.assertEqual(completed.status, repeated.status)
        self.assertEqual(repeated.cost_micro_cny, 123)
        with self.assertRaises(JobStateError):
            self.jobs.record_provider_call(
                "tenant-a", "deepseek", "deepseek-v4-flash", scope_name="alpha",
                call_id="physical-1", job_id="job-2", stage_id="stage-1",
                key_id="key-1", request_sha256="request-1", status="completed",
            )

    def test_evolution_watermarks_due_claim_and_success_gate(self) -> None:
        self.db.record_committed_source_events(
            "tenant-a", "alpha", 5, conflict_generation=2, ingested_at=100,
            raw_token_estimate=5_000, user_turns=8,
        )
        due = self.db.list_due_scopes(
            dirty_threshold=3, max_age_seconds=20, now=130,
            include_conflicts=True, min_success_interval_seconds=0,
        )
        self.assertEqual(due[0]["due_reasons"], ("dirty_threshold", "max_age", "conflict"))
        self.assertTrue(self.jobs.claim_scope_evolution_job("tenant-a", "alpha", "evo-1"))
        self.assertFalse(self.jobs.claim_scope_evolution_job("tenant-a", "alpha", "evo-2"))
        with self.assertRaises(ValueError):
            self.jobs.advance_evolution_watermarks(
                "tenant-a", "alpha", source_event_seq=5, conflict_generation=2,
                slow_succeeded=True, index_activated=False, evolution_job_id="evo-1",
            )
        state = self.jobs.advance_evolution_watermarks(
            "tenant-a", "alpha", source_event_seq=5, conflict_generation=2,
            slow_succeeded=True, index_activated=True, evolution_job_id="evo-1",
            succeeded_at=140,
        )
        self.assertEqual((state["promoted_event_seq"], state["promoted_conflict_generation"]), (5, 2))
        self.assertEqual(state["indexed_event_seq"], 5)
        self.assertIsNone(state["active_evolution_job_id"])
        self.assertEqual(self.db.list_due_scopes(), [])

    def test_coalesced_index_watermark_has_exclusive_claim_and_atomic_advance(self) -> None:
        self.db.record_committed_source_events("tenant-a", "alpha", 10, ingested_at=100)
        self.assertEqual(self.db.list_due_index_scopes(dirty_threshold=3, now=101)[0]["dirty_events"], 10)
        self.assertTrue(self.jobs.claim_scope_index_job("tenant-a", "alpha", "index-1"))
        self.assertFalse(self.jobs.claim_scope_index_job("tenant-a", "alpha", "index-2"))
        with self.assertRaises(ValueError):
            self.jobs.advance_index_watermark(
                "tenant-a", "alpha", indexed_event_seq=10,
                index_succeeded=False, index_job_id="index-1",
            )
        partial = self.jobs.advance_index_watermark(
            "tenant-a", "alpha", indexed_event_seq=7,
            index_job_id="index-1", succeeded_at=110,
        )
        self.assertEqual(partial["indexed_event_seq"], 7)
        self.assertIsNone(partial["active_index_job_id"])
        self.assertEqual(self.db.list_due_index_scopes(dirty_threshold=4, now=111), [])
        self.assertEqual(self.db.list_due_index_scopes(dirty_threshold=3, now=111)[0]["dirty_events"], 3)
        self.jobs.advance_index_watermark("tenant-a", "alpha", indexed_event_seq=10, succeeded_at=120)
        self.assertEqual(self.db.list_due_index_scopes(), [])

    def test_ingest_commit_metrics_are_idempotent_across_crash_replay(self) -> None:
        first = self.db.record_committed_source_events(
            "tenant-a", "alpha", 2,
            operation_id="ingest-1", new_message_count=2,
            raw_token_estimate=32_000, user_turns=2, ingested_at=100,
        )
        replayed = self.db.record_committed_source_events(
            "tenant-a", "alpha", 4,
            operation_id="ingest-1", new_message_count=2,
            raw_token_estimate=32_000, user_turns=2, ingested_at=200,
        )
        self.assertEqual(first["source_event_seq"], 2)
        self.assertEqual(replayed["source_event_seq"], 2)
        self.assertEqual(replayed["source_raw_token_estimate"], 32_000)
        self.assertEqual(replayed["source_user_turns"], 2)
        with self.db.transaction(immediate=False) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM scope_ingest_watermark_commits"
            ).fetchone()[0]
        self.assertEqual(count, 1)
        with self.assertRaises(ValueError):
            self.db.record_committed_source_events(
                "tenant-a", "alpha", 4,
                operation_id="ingest-1", new_message_count=2,
                raw_token_estimate=31_999, user_turns=2,
            )

    def test_slow_policy_batches_tokens_or_turns_with_age_floor_and_cooldown(self) -> None:
        self.db.record_committed_source_events(
            "tenant-a", "conflict-only", 1, conflict_generation=1,
            raw_token_estimate=100, user_turns=1, ingested_at=0,
        )
        self.db.record_committed_source_events(
            "tenant-a", "token-batch", 1,
            raw_token_estimate=32_000, user_turns=1, ingested_at=99_999,
        )
        self.db.record_committed_source_events(
            "tenant-a", "turn-batch", 1,
            raw_token_estimate=100, user_turns=64, ingested_at=99_999,
        )
        self.db.record_committed_source_events(
            "tenant-a", "aged-minimum", 1,
            raw_token_estimate=4_000, user_turns=1, ingested_at=0,
        )
        due = self.db.list_due_scopes(max_age_seconds=86_400, now=100_000)
        self.assertEqual(
            {row["scope_name"] for row in due},
            {"token-batch", "turn-batch", "aged-minimum"},
        )
        self.assertNotIn("conflict-only", {row["scope_name"] for row in due})

        self.db.record_committed_source_events(
            "tenant-a", "cooldown", 1,
            raw_token_estimate=100, user_turns=1, ingested_at=0,
        )
        self.db.advance_promoted_watermarks(
            "tenant-a", "cooldown", source_event_seq=1, conflict_generation=0,
            slow_succeeded=True, index_activated=True, succeeded_at=100,
        )
        self.db.record_committed_source_events(
            "tenant-a", "cooldown", 2,
            raw_token_estimate=32_000, user_turns=1, ingested_at=110,
        )
        self.assertNotIn(
            "cooldown",
            {row["scope_name"] for row in self.db.list_due_scopes(now=200)},
        )
        self.assertIn(
            "cooldown",
            {row["scope_name"] for row in self.db.list_due_scopes(now=1_901)},
        )

    def test_index_age_is_measured_from_current_dirty_batch(self) -> None:
        self.db.record_committed_source_events(
            "tenant-a", "alpha", 1, ingested_at=100,
        )
        self.assertEqual(
            self.db.list_due_index_scopes(dirty_threshold=16, max_age_seconds=2, now=101),
            [],
        )
        self.assertEqual(
            self.db.list_due_index_scopes(dirty_threshold=16, max_age_seconds=2, now=102)[0]["due_reasons"],
            ("max_age",),
        )
        self.db.advance_index_watermark(
            "tenant-a", "alpha", indexed_event_seq=1, succeeded_at=103,
        )
        state = self.db.get_scope_evolution_state("tenant-a", "alpha")
        self.assertIsNone(state["index_dirty_since_at"])  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
