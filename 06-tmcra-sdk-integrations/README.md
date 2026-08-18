# TMCRA SDKs and integrations

## SDKs

- `sdk/python` — Python client (`tmcra_client`).
- `sdk/typescript` — TypeScript client (`@tmcra/typescript`).

## Integrations

Agent-framework integrations with automatic lifecycle (recall on turn start,
write-back on turn end):

- `integrations/openclaw` — OpenClaw plugin.
- `integrations/hermes` — Hermes plugin.
- `integrations/langgraph` — LangGraph.
- `integrations/openai-agents` — OpenAI Agents.
- `integrations/vercel-ai-sdk` — Vercel AI SDK.
- `integrations/microsoft-agent-framework` — Microsoft Agent Framework.

Each integration documents its own install path (`INSTALL.md` or README).
All integrations share the TMCRA receipts contract: durable queues, explicit
commit semantics, and `StopFailure` handling so a failed write is never
silently dropped.
