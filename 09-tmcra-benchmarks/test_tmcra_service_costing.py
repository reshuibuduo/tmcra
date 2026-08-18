from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tmcra_service.control_db import ControlDB
from tmcra_service.costing import journal_deepseek_calls, physical_call_metadata
from tmcra_service.jobs import JobStore


class ProviderMetadataLedgerTests(unittest.TestCase):
    def test_aggregate_metadata_is_flattened_and_deduplicated(self) -> None:
        call = {
            "physical_call_id": "call-1",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "status": "completed",
            "stage": "recall_planner",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "prompt_cache_hit_tokens": 40,
                "prompt_cache_miss_tokens": 60,
                "total_tokens": 110,
            },
        }
        self.assertEqual(
            physical_call_metadata({"calls": [call, call], **call}),
            [call],
        )

    def test_call_is_bound_to_stage_without_persisting_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "control.sqlite3")
            jobs = JobStore(db)
            stage = jobs.create_stage(
                "tenant-a", "scope-a", "recall_planner", stage_id="query-1:planner"
            )
            count = journal_deepseek_calls(
                jobs,
                {
                    "physical_call_id": "call-1",
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash",
                    "status": "completed",
                    "stage": "recall_planner",
                    "request_sha256": "request-hash",
                    "response_sha256": "response-hash",
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 10,
                        "prompt_cache_hit_tokens": 40,
                        "prompt_cache_miss_tokens": 60,
                        "total_tokens": 110,
                    },
                },
                tenant_id="tenant-a",
                scope_name="scope-a",
                job_id=None,
                stage_id=stage.stage_id,
                operation="recall_planner",
                default_model="deepseek-v4-flash",
            )
            self.assertEqual(count, 1)
            stored = jobs.get_provider_call("call-1")
            self.assertEqual(stored.stage_id, "query-1:planner")  # type: ignore[union-attr]
            self.assertIsNone(stored.request)  # type: ignore[union-attr]
            self.assertIsNone(stored.response)  # type: ignore[union-attr]
            self.assertGreater(stored.cost_micro_cny, 0)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
