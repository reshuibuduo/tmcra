from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tmcra_service.planner import AuditedRecallPlanner
from tmcra_v3_recall_planner import RecallPlannerResponseError


def valid_plan() -> dict[str, object]:
    return {
        "schema_version": "tmcra.recall-role-plan.v1",
        "resolved_query": "What is my deployment codename?",
        "query_kind": "fact",
        "temporal_focus": "current",
        "conflict_policy": "prefer_recent",
        "layers": {
            "source": {"role": "primary", "weight": 1.0},
            "fast": {"role": "support", "weight": 0.5},
        },
    }


class FailingPlanner:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def plan(self, **kwargs: object):
        content = json.dumps(self.payload)
        raise RecallPlannerResponseError(
            "strict validation failed",
            response_content=content,
            request_metadata={"physical_api_calls": 1},
        )


class AuditedPlannerTests(unittest.TestCase):
    def test_missing_unavailable_layer_is_neutrally_repaired_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "repairs.jsonl"
            planner = AuditedRecallPlanner(FailingPlanner(valid_plan()), audit)
            plan, metadata = planner.plan(
                available_layers={
                    "source": {"available": True},
                    "fast": {"available": True},
                    "slow": {"available": False},
                }
            )
            self.assertEqual(plan["layers"]["slow"], {"role": "context", "weight": 0.0})
            self.assertEqual(metadata["repaired_missing_layers"], ["slow"])
            row = json.loads(audit.read_text(encoding="utf-8"))
            self.assertEqual(row["missing_layers"], ["slow"])
            self.assertEqual(
                row["available_layers"],
                {"source": True, "fast": True, "slow": False},
            )

    def test_missing_available_layer_remains_a_hard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            planner = AuditedRecallPlanner(
                FailingPlanner(valid_plan()), Path(directory) / "repairs.jsonl"
            )
            with self.assertRaises(RecallPlannerResponseError):
                planner.plan(
                    available_layers={
                        "source": {"available": True},
                        "fast": {"available": True},
                        "slow": {"available": True},
                    }
                )


if __name__ == "__main__":
    unittest.main()
