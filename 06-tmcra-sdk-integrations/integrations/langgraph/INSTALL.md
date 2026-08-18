# Install TMCRA for LangGraph

```bash
python -m pip install tmcra-langgraph
```

Add `recall_node` before the model node and `ingest_node` after the completed
model turn. Supply stable scope, session, turn, and timestamp fields. Enable
the durable outbox for process restart and call `reconcile_pending()` from a
worker tick. The package does not replace a LangGraph checkpointer.

Run `python -m pytest -q` for local checks; real checkpointer and service E2E
must be performed separately with a disposable scope.
