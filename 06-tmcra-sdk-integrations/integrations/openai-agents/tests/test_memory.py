import asyncio
import json
from pathlib import Path

from tmcra_openai_agents import TMCRAAgentsMemory


class FakeClient:
    def __init__(self):
        self.ingests = []

    async def recall(self, scope_name, request):
        return {"prompt_evidence": {"content": "The user prefers concise reports."}}

    async def ingest(self, scope_name, request, *, idempotency_key):
        self.ingests.append((scope_name, request, idempotency_key))
        return {"job_id": "job-1"}


def test_session_callback_injects_transient_context_and_hook_commits_once():
    async def run():
        client = FakeClient()
        memory = TMCRAAgentsMemory(client, scope_name="person-1", session_id="session-1")
        combined = await memory.session_input_callback(
            [{"role": "assistant", "content": "Earlier answer"}],
            [{"role": "user", "content": "How should this report look?"}],
        )
        assert combined[0]["role"] == "system"
        await memory.hooks.on_agent_end(None, None, "Keep it concise.")
        await memory.hooks.on_agent_end(None, None, "Keep it concise.")
        assert len(client.ingests) == 1
        assert client.ingests[0][1]["metadata"]["adapter"] == "openai-agents"

    asyncio.run(run())


def test_continue_mode_can_fail_open_on_recall():
    class FailingClient(FakeClient):
        async def recall(self, scope_name, request):
            raise RuntimeError("offline")

    async def run():
        memory = TMCRAAgentsMemory(
            FailingClient(),
            scope_name="person-1",
            session_id="session-1",
            failure_mode="continue",
        )
        items = await memory.session_input_callback([], [{"role": "user", "content": "Hi"}])
        assert items == [{"role": "user", "content": "Hi"}]

    asyncio.run(run())


def test_response_loss_keeps_outbox_and_reconcile_reuses_same_key(tmp_path: Path):
    async def run():
        class LossyClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.fail = True

            async def ingest(self, scope_name, request, *, idempotency_key):
                self.ingests.append((scope_name, request, idempotency_key))
                if self.fail:
                    self.fail = False
                    raise TimeoutError("response lost")
                return {"job_id": "job-2", "status_url": "/jobs/job-2"}

        client = LossyClient()
        memory = TMCRAAgentsMemory(client, scope_name="p", session_id="s", failure_mode="continue", outbox_path=tmp_path / "outbox.json")
        await memory.session_input_callback([], [{"role": "user", "content": "remember this"}])
        assert await memory.commit_final_output("answer") is None
        receipts = await memory.reconcile_pending()
        assert receipts[0].status == "submitted"
        assert client.ingests[0][2] == client.ingests[1][2]
        memory.acknowledge(receipts[0].idempotency_key)
        assert json.loads((tmp_path / "outbox.json").read_text(encoding="utf-8")) == []

    asyncio.run(run())
