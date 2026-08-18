# TMCRA for OpenAI Agents SDK

This adapter preserves the Agents SDK `Session` as short-term conversation
storage. It uses the official `session_input_callback` to add transient TMCRA
evidence to prepared model input, and a `RunHooks.on_agent_end` hook to write the
completed user/assistant turn.

```python
memory = TMCRAAgentsMemory(
    tmcra_async_client,
    scope_name="person_123",
    session_id="conversation_456",
)

result = await Runner.run(
    agent,
    "What did I decide?",
    session=session,
    hooks=memory.hooks,
    run_config=RunConfig(session_input_callback=memory.session_input_callback),
)
```

Create one adapter instance per concurrent conversation. Use a scoped TMCRA
token with only `memory:read`, `memory:write`, and the assigned persona scope.
`failure_mode="raise"` is the default; choose `continue` only when answering
without memory is explicitly acceptable.
