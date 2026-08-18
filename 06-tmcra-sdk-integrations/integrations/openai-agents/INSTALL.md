# Install TMCRA for OpenAI Agents SDK

```bash
python -m pip install tmcra-openai-agents
```

Create a server-side `AsyncMemoryClient`, a scope-bound token, and one
`TMCRAAgentsMemory` instance per concurrent conversation. Register its input
callback and `RunHooks` with the Agents SDK. Keep the outbox on durable local
storage when process restart or response loss is possible.

Run `python -m pytest -q` for the local contract suite. This package does not
configure a host automatically and does not prove a real Agents Runner E2E.
