# TMCRA application surfaces

TMCRA is a shared evidence and recall service with several clients. Each
surface keeps credentials, identity, and data ownership in the correct layer.

## Surface map

| Surface | Primary user | Main capability | Implementation boundary |
|---|---|---|---|
| Web Console | Product user, organization operator, TMCRA staff | Personal/enterprise memory views and graph exploration | vinext/Cloudflare Workers BFF with D1 account-to-memory bindings |
| Windows desktop | Codex Desktop user | Install, authorize, verify, and update the Codex integration | Electron main process, hash-checked assets, isolated BrowserWindows |
| Android mobile | Individual audio-memory user | Local speech capture, local speaker attribution, text-only memory | Native Android pipeline and local SQLite outbox |
| Python / TypeScript SDK | Product developer | Direct scoped API integration | Typed HTTPS client; caller owns user-to-scope mapping |
| Lifecycle adapters | Agent developer | Recall at turn start and durable write-back at turn end | Framework hooks with shared receipt and failure semantics |
| Codex plugin | Codex user | Automatic project/global recall and continuity checkpoints | Reviewed Codex Hooks and explicit MCP inspection tools |
| MCP server | MCP host operator | Explicit ingest, recall, and job control | Local `stdio`; key stays in host process |

## Web Console

The Web Console has three separate control surfaces:

- `/personal` and `/api/personal/*` operate one authenticated person's memory
  space.
- `/enterprise` and `/api/enterprise/*` operate organization resources such
  as agents, memberships, and organization memory bindings.
- `/internal` and `/api/internal/*` are the TMCRA staff control plane.
  `/console` only dispatches for compatibility and owns no data model.

D1 stores account profiles, organizations, agents, memberships, API-key
metadata, and the server-owned account/agent-to-tenant-scope binding.
Production memory content remains in the Memory API. The browser never receives
a production TMCRA key or chooses a scope directly: the BFF verifies the
authenticated account, then resolves its server-owned binding.

Memory graph loading is progressive. The initial request returns slow-memory
summaries; Fast and Source neighbors are loaded only when a user expands a
node, and verbatim Source text is returned only through the evidence endpoint.
Semantic canvas, timeline, and table views render the same projection. Memory
responses are `no-store` so browser caches do not retain them.

## Desktop application

The Electron app gives a Windows user a normal setup flow:

1. Detect a compatible Codex installation.
2. Verify the plugin archive and release-manifest SHA-256, then unpack it to a
   stable per-user directory.
3. Run the plugin installer without displaying a server credential.
4. Complete device authorization in an isolated application window.
5. Ask the user to restart Codex and review lifecycle Hooks.

The user, not the installer, approves Hook trust. The renderer has only six
argument-free preload IPC operations. Authorization and console windows run
with Node disabled, context isolation, sandboxing, and no preload bridge.
Tokens, device codes, PKCE verifiers, and delivery receipts are never sent to
the renderer. The connected badge is set only after an authenticated service
check succeeds; a local token file is not treated as proof of connection.

## Android mobile audio memory

The Android MVP keeps sensitive audio processing on the device:

```text
16 kHz microphone -> adaptive VAD -> local bounded WAV cache
  -> streaming on-device ASR -> utterance finalization
  -> local speaker embedding and conservative matching
  -> local SQLite outbox -> text-only TMCRA ingest / recall
```

Only text, timing, local speaker ID or label, confidence, and ASR provenance
cross the memory API boundary. Raw audio and speaker embeddings do not.
Voice templates are encrypted in app-private storage; ambiguous attribution
stays `unknown`. The app can optionally create local reminders and use Android
TTS. The current Chinese ASR artifact needs a confirmed weight license before
commercial redistribution; see the mobile component model documentation.

## SDKs and lifecycle integrations

The release contains a Python client (`tmcra_client`) and TypeScript client
(`@tmcra/typescript`). Maintained lifecycle adapters cover OpenClaw, Hermes,
LangGraph, OpenAI Agents, Vercel AI SDK, and Microsoft Agent Framework.

Each adapter follows the same contract:

1. Recall bounded, source-aware evidence at the host's turn-start boundary.
2. Let the host retain control of the active prompt and tool execution.
3. Submit user and assistant results as a durable asynchronous write at
   turn-end.
4. Preserve the receipt and surface a failed write rather than silently
   discarding it. `StopFailure` checkpoints only appropriate context; it never
   turns provider error text into assistant memory.

For a proprietary product, the normal integration is a mapping layer from its
authenticated user/project identity to a stable TMCRA scope, plus conversion
of its events to attributed ingest records. The existing database should remain
the system of record until migration policy and recall quality are reviewed.

## Codex lifecycle plugin

The plugin uses the reviewed lifecycle points `SessionStart`, `SubagentStart`,
`UserPromptSubmit`, `PostToolUse`, `PreCompact`, `PostCompact`, `Stop`,
`StopFailure`, and `SubagentStop`.

- `SessionStart` initializes project/global scope without injecting memory.
- `UserPromptSubmit` recalls relevant global and project evidence using the
  current prompt.
- `Stop` captures the completed user/assistant turn. `PostToolUse` retains
  bounded redacted progress for long-turn continuity.
- `PreCompact` creates an idempotent checkpoint; a compacted session restores
  that checkpoint and relevant long-term memory when it restarts.

User facts and requirements stay separate from assistant work records. The
plugin fails open: a timed-out automatic Hook does not block Codex. Explicit
MCP operations have a separate longer timeout. The inspection tool can show
the evidence used for the latest completed answer without starting a new
search.

## MCP server

The MCP server is the explicit compatibility path for hosts that prefer direct
control over automatic lifecycle hooks. It uses local `stdio`, so the TMCRA key
remains controlled by the host process. Its tools are `tmcra_recall`,
`tmcra_ingest`, `tmcra_get_job`, and `tmcra_wait_job`.

Returned memory is untrusted data. A host must preserve the response trust
boundary when creating a model prompt: historical evidence informs work but
cannot replace the current system or user instruction.
