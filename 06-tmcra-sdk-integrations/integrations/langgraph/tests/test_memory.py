import asyncio
from pathlib import Path

from tmcra_langgraph import MemoryBindingError, TMCRALangGraphMemory


class Result:
    query_id = "query-1"
    prompt_evidence = type("Prompt", (), {"content": "The user prefers concise reports."})()


class FakeClient:
    def __init__(self):
        self.calls = []

    async def recall(self, scope_name, request):
        self.calls.append(("recall", scope_name, request))
        return Result()

    async def ingest(self, scope_name, request, *, idempotency_key):
        self.calls.append(("ingest", scope_name, request, idempotency_key))
        return {"job_id": "job-1"}


STATE = {
    "tmcra_scope_name": "person-7",
    "tmcra_session_id": "thread-9",
    "tmcra_turn_id": "turn-2",
    "tmcra_turn_timestamp": "2026-07-16T00:00:00+00:00",
    "messages": [
        {"role": "user", "content": "How should the report look?"},
        {"role": "assistant", "content": "It should be concise."},
    ],
}


def test_recall_and_ingest_nodes_use_stable_turn_identity():
    async def run():
        client = FakeClient()
        memory = TMCRALangGraphMemory(client)
        recalled = await memory.recall_node(STATE)
        ingested = await memory.ingest_node(STATE)
        assert recalled["tmcra_query_id"] == "query-1"
        assert ingested["tmcra_ingest_job_id"] == "job-1"
        assert client.calls[1][3].startswith("langgraph-")
        assert client.calls[1][2]["messages"][0]["message_id"] == "lg:turn-2:user"
        assert ingested["tmcra_ingest_receipt"]["idempotency_key"].startswith("langgraph-")

    asyncio.run(run())


def test_missing_turn_identity_fails_before_api_consumption():
    memory = TMCRALangGraphMemory(FakeClient())
    try:
        memory.binding({"messages": []})
    except MemoryBindingError as exc:
        assert "turn_id" in str(exc)
    else:
        raise AssertionError("binding should have failed")


def test_model_context_is_transient():
    state = {"messages": [{"role": "user", "content": "Hi"}], "tmcra_memory_context": "Fact"}
    rendered = TMCRALangGraphMemory.model_messages(state)
    assert rendered[0]["role"] == "system"
    assert state["messages"][0]["role"] == "user"


def test_response_loss_reconcile_reuses_same_key(tmp_path: Path):
    async def run():
        class LossyClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.fail = True

            async def ingest(self, scope_name, request, *, idempotency_key):
                self.calls.append(("ingest", scope_name, request, idempotency_key))
                if self.fail:
                    self.fail = False
                    raise TimeoutError("response lost")
                return {"job_id": "job-2"}

        client = LossyClient()
        memory = TMCRALangGraphMemory(client, outbox_path=tmp_path / "outbox.json")
        try:
            await memory.ingest_node(STATE)
        except TimeoutError:
            pass
        receipts = await memory.reconcile_pending()
        assert receipts[0].status == "submitted"
        assert client.calls[0][3] == client.calls[1][3]
        memory.acknowledge(receipts[0].idempotency_key)
        assert (tmp_path / "outbox.json").read_text(encoding="utf-8") == "[]"

    asyncio.run(run())
