from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import call, patch


# server_smoke runs inside Hermes in production, where this module is provided by
# the host. Supply the smallest compatible host surface for isolated unit tests.
memory_provider_module = types.ModuleType("agent.memory_provider")
memory_provider_module.MemoryProvider = type("MemoryProvider", (), {})
agent_module = types.ModuleType("agent")
agent_module.memory_provider = memory_provider_module
with patch.dict(
    sys.modules,
    {"agent": agent_module, "agent.memory_provider": memory_provider_module},
):
    import server_smoke


class ServerSmokeJobPollingTests(unittest.TestCase):
    def test_agent_b_cannot_reuse_agent_a_success_after_its_own_timeout(self) -> None:
        # Agent A succeeds immediately. Agent B gets a fresh deadline, is polled,
        # remains pending, and must time out instead of reusing A's success state.
        monotonic_values = [0.0, 0.0, 100.0, 100.0, 102.0]
        with (
            patch.object(
                server_smoke.time,
                "monotonic",
                side_effect=monotonic_values,
            ),
            patch.object(server_smoke.time, "sleep"),
            patch.object(
                server_smoke,
                "_get_job",
                side_effect=[
                    {"status": "succeeded", "job_id": "job-a"},
                    {"status": "running", "job_id": "job-b"},
                ],
            ) as get_job,
        ):
            state_a = server_smoke._wait_for_job(
                "https://api.tmcra.com",
                "test-key",
                "job-a",
                timeout_seconds=1.0,
                job_label="Agent A job",
            )
            self.assertEqual(state_a["status"], "succeeded")

            with self.assertRaisesRegex(RuntimeError, "Agent B job timed out"):
                server_smoke._wait_for_job(
                    "https://api.tmcra.com",
                    "test-key",
                    "job-b",
                    timeout_seconds=1.0,
                    job_label="Agent B job",
                )

        self.assertEqual(
            get_job.call_args_list,
            [
                call("https://api.tmcra.com", "test-key", "job-a"),
                call("https://api.tmcra.com", "test-key", "job-b"),
            ],
        )

    def test_each_agent_gets_a_deadline_from_its_own_start_time(self) -> None:
        monotonic_values = [10.0, 10.0, 90.0, 90.0]
        with (
            patch.object(
                server_smoke.time,
                "monotonic",
                side_effect=monotonic_values,
            ),
            patch.object(
                server_smoke,
                "_get_job",
                side_effect=[{"status": "succeeded"}, {"status": "succeeded"}],
            ),
        ):
            server_smoke._wait_for_job(
                "https://api.tmcra.com",
                "test-key",
                "job-a",
                timeout_seconds=5.0,
                job_label="Agent A job",
            )
            server_smoke._wait_for_job(
                "https://api.tmcra.com",
                "test-key",
                "job-b",
                timeout_seconds=5.0,
                job_label="Agent B job",
            )


if __name__ == "__main__":
    unittest.main()
