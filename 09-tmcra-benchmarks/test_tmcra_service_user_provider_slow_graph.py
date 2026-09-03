from __future__ import annotations

import json
import unittest
from unittest import mock

from tmcra_service.user_provider_slow_graph import UserProviderTierClient


class FakeBroker:
    max_tokens = 256

    def __init__(self) -> None:
        self.calls = []

    def complete_messages(self, **kwargs):
        self.calls.append(kwargs)
        return (
            {"operations": [{"action": "noop"}]},
            {
                "physical_call_id": "upt_test",
                "physical_api_call": True,
                "physical_api_calls": 1,
                "provider": "openai-compatible",
                "model": "organizer-model",
                "provider_request_id": "provider-request-1",
                "request_sha256": "a" * 64,
                "response_sha256": "b" * 64,
                "started_at": 10.0,
                "completed_at": 10.25,
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 3,
                    "prompt_cache_hit_tokens": 5,
                    "prompt_cache_miss_tokens": 15,
                    "total_tokens": 23,
                },
            },
        )


class UserProviderSlowGraphTests(unittest.TestCase):
    def test_adapter_preserves_graph_patch_validation_and_synthetic_recovery_envelope(self):
        broker = FakeBroker()
        client = UserProviderTierClient(route="pro", broker=broker)
        with mock.patch.object(
            client,
            "_messages",
            return_value=[
                {"role": "system", "content": "Return JSON."},
                {"role": "user", "content": "{}"},
            ],
        ):
            patch = client.propose(
                {"required_evidence_ids": [], "evidence": []},
                [],
            )
        self.assertEqual(patch, {"operations": [{"action": "noop"}]})
        self.assertEqual(broker.calls[0]["operation"], "slow_graph_pro")
        metadata = dict(client.last_call_metadata)
        self.assertEqual(metadata["status"], "completed")
        self.assertEqual(metadata["provider"], "openai-compatible")
        self.assertEqual(metadata["execution_route"], "user-provider")
        self.assertEqual(metadata["latency_ms"], 250.0)
        envelope = json.loads(metadata["raw_response"])
        self.assertEqual(
            json.loads(envelope["choices"][0]["message"]["content"]),
            patch,
        )
        self.assertEqual(set(envelope), {"id", "choices", "usage"})


if __name__ == "__main__":
    unittest.main()
