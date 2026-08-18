import json
import unittest
from unittest import mock

from test_tmcra_v4_semantic_evidence import contract, row
from tmcra_v4_evidence_operations import build_evidence_catalog
from tmcra_v4_semantic_evidence import resolution_payload, task_contract_payload
from tmcra_v4_semantic_planner import SemanticJsonPlanner, SemanticPlannerError


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getcode(self):
        return 200

    def read(self):
        return json.dumps(
            {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(self.value)}}
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 30,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 100,
                    "total_tokens": 130,
                },
            }
        ).encode()


class RawResponse(Response):
    def read(self):
        return json.dumps(
            {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "```json\n{broken\n```"}}
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 100,
                    "total_tokens": 110,
                },
            }
        ).encode()


class SemanticPlannerTests(unittest.TestCase):
    def test_task_payload_is_evidence_independent(self):
        payload = task_contract_payload(row())
        self.assertEqual(set(payload), {"question", "question_date"})
        self.assertNotIn("evidence_windows", payload)
        self.assertNotIn("question_type", payload)

    def test_resolution_payload_uses_contract_without_benchmark_route(self):
        payload = resolution_payload(row(), contract(), build_evidence_catalog(row()))
        self.assertIn("task_contract", payload)
        self.assertIn("evidence", payload)
        self.assertNotIn("question_type", payload)
        self.assertEqual(payload["task_contract"]["output"]["origin"], "memory_conditioned")

    def test_task_planner_validates_one_json_response(self):
        planner = SemanticJsonPlanner(base_url="https://planner.test/v1", api_keys=["k"])
        with mock.patch(
            "tmcra_v4_semantic_planner.urllib.request.urlopen",
            return_value=Response(contract()),
        ) as call:
            value, metadata = planner.plan_task_contract(row())
        self.assertEqual(value, contract())
        self.assertEqual(metadata["stage"], "task_contract_planner")
        self.assertEqual(metadata["physical_api_calls"], 1)
        self.assertEqual(call.call_count, 1)

    def test_non_json_model_content_is_an_explicit_repairable_validation_failure(self):
        planner = SemanticJsonPlanner(base_url="https://planner.test/v1", api_keys=["k"])
        with mock.patch(
            "tmcra_v4_semantic_planner.urllib.request.urlopen",
            return_value=RawResponse(None),
        ):
            with self.assertRaises(SemanticPlannerError) as raised:
                planner.plan_task_contract(row())
        self.assertEqual(raised.exception.metadata["status"], "completed")
        self.assertIn("strict JSON decode failed", raised.exception.metadata["validation_error"])
        self.assertIn("{broken", raised.exception.metadata["raw_response"])


if __name__ == "__main__":
    unittest.main()
