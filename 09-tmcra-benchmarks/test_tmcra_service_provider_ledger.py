from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from tmcra_service.writer import LeasedDeepSeekClient, ProductionWriterError
from tmcra_service.provider_pool import ProviderKeyPool
from tmcra_service.control_db import ControlDB
from tmcra_service.jobs import JobStore


class ProviderLedgerTests(unittest.TestCase):
    def test_success_journals_metadata_without_bodies_and_prices(self) -> None:
        class FakeClient:
            def __init__(self, **_: object) -> None:
                pass

            def complete(self, _: object) -> tuple[str, dict[str, object]]:
                return "response content", {
                    "physical_call_id": "physical-success-1",
                    "request_sha256": "request-hash",
                    "response_sha256": "response-hash",
                    "stage": "batch_flash",
                    "started_at": 10.0,
                    "usage": {
                        "prompt_tokens": 1000,
                        "completion_tokens": 500,
                        "prompt_cache_hit_tokens": 400,
                        "prompt_cache_miss_tokens": 600,
                        "total_tokens": 1500,
                    },
                }

            reconcile = complete

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "control.sqlite3"
            pool = ProviderKeyPool(database, pool="deepseek-writer", keys=["secret-value"])
            client = LeasedDeepSeekClient(
                v4=SimpleNamespace(DeepSeekBatchClient=FakeClient),
                pool=pool,
                operation_id="op-1",
                base_url="https://provider.invalid",
                model="deepseek-v4-flash",
                timeout=1.0,
                max_tokens=32,
                ledger_database=database,
                tenant_id="tenant-a",
                scope_name="scope-a",
                job_id="job-a",
                stage_id="stage-a",
            )
            client.complete({"prompt": "secret prompt"})
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT status, request_json, response_json, input_tokens, output_tokens, "
                    "cache_hit_tokens, cache_miss_tokens, cost_micros, price_version, key_id, "
                    "request_sha256, response_sha256, operation FROM provider_calls "
                    "WHERE call_id='physical-success-1'"
                ).fetchone()
                prices = connection.execute(
                    "SELECT currency, cache_hit_input_micros_per_million, "
                    "cache_miss_input_micros_per_million, output_micros_per_million "
                    "FROM provider_prices WHERE model='deepseek-v4-flash'"
                ).fetchone()
            self.assertEqual(
                row,
                (
                    "completed", None, None, 1000, 500, 400, 600, 1608,
                    "deepseek-v4-official-cny-2026-07-15",
                    "31160254d1297393d2ad00e1", "request-hash", "response-hash", "batch_flash",
                ),
            )
            self.assertEqual(prices, ("CNY", 20_000, 1_000_000, 2_000_000))
            control = ControlDB(database)
            control.record_committed_source_events(
                "tenant-a", "scope-a", 1,
                raw_token_estimate=1_000, user_turns=1,
            )
            summary = JobStore(control).usage_cost_summary(
                "tenant-a", scope_name="scope-a"
            )
            self.assertTrue(summary["complete_for_registered_calls"])
            self.assertEqual(summary["known_cost_cny"], 0.001608)
            self.assertEqual(
                summary["known_model_api_cny_per_million_ingested_raw_tokens"],
                1.608,
            )
            self.assertEqual(
                summary["by_stage"]["batch_flash"]["known_cost_micro_cny"],
                1608,
            )

    def test_transport_error_is_unknown_and_not_zero_cost(self) -> None:
        class TransportError(RuntimeError):
            def __init__(self) -> None:
                super().__init__("transport failed with sensitive detail")
                self.metadata = {
                    "physical_call_id": "physical-unknown-1",
                    "request_sha256": "request-hash",
                    "status": "request_error",
                    "error": "full response must not be persisted",
                }

        class FakeClient:
            def __init__(self, **_: object) -> None:
                pass

            def complete(self, _: object) -> object:
                raise TransportError()

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "control.sqlite3"
            pool = ProviderKeyPool(database, pool="deepseek-writer", keys=["secret-value"])
            client = LeasedDeepSeekClient(
                v4=SimpleNamespace(DeepSeekBatchClient=FakeClient),
                pool=pool,
                operation_id="op-1",
                base_url="https://provider.invalid",
                model="deepseek-v4-pro",
                timeout=1.0,
                max_tokens=32,
                ledger_database=database,
                tenant_id="tenant-a",
                scope_name="scope-a",
                job_id="job-a",
                stage_id="stage-a",
            )
            with self.assertRaises(TransportError):
                client.complete({"prompt": "secret prompt"})
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT status, cost_micros, request_json, response_json, error "
                    "FROM provider_calls WHERE call_id='physical-unknown-1'"
                ).fetchone()
            self.assertEqual(row[0:4], ("unknown", None, None, None))
            self.assertNotIn("sensitive detail", row[4] or "")
            summary = JobStore(ControlDB(database)).usage_cost_summary("tenant-a")
            self.assertFalse(summary["complete_for_registered_calls"])
            self.assertEqual(summary["uncertain_cost_call_count"], 1)

    def test_ledger_identity_is_required_when_ledger_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "control.sqlite3"
            pool = ProviderKeyPool(database, pool="deepseek-writer", keys=["secret-value"])
            with self.assertRaises(ProductionWriterError):
                LeasedDeepSeekClient(
                    v4=SimpleNamespace(DeepSeekBatchClient=object),
                    pool=pool,
                    operation_id="op-1",
                    base_url="https://provider.invalid",
                    model="deepseek-v4-flash",
                    timeout=1.0,
                    max_tokens=32,
                    ledger_database=database,
                    tenant_id="tenant-a",
                    scope_name="scope-a",
                    job_id="job-a",
                )


if __name__ == "__main__":
    unittest.main()
