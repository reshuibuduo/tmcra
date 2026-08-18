# TMCRA for LangGraph

This package adds TMCRA as cross-thread long-term memory. It does not replace a
LangGraph checkpointer: checkpoints retain graph execution state, while TMCRA
recalls and evolves user memory across different threads.

Add `memory.recall_node` before the model node and `memory.ingest_node` after the
final model node. Feed `TMCRALangGraphMemory.model_messages(state)` to the model
so recalled evidence remains transient and is not copied into every checkpoint.

Each invocation must provide these stable values in state, runtime context, or
`configurable`: `tmcra_scope_name`, `tmcra_session_id`, `tmcra_turn_id`, and an
ISO-8601 `tmcra_turn_timestamp`. This makes super-step retries idempotent.

```python
memory = TMCRALangGraphMemory(tmcra_async_client)

builder.add_node("recall_memory", memory.recall_node)
builder.add_node("call_model", call_model)
builder.add_node("commit_memory", memory.ingest_node)
```

Use an application-scoped TMCRA token limited to `memory:read`, `memory:write`
and the one persona scope assigned to this graph.
