# TMCRA OpenClaw Plugin

This directory is a native, hook-only OpenClaw plugin. It uses the current
typed plugin lifecycle API:

- `before_prompt_build` recalls TMCRA memory and returns `prependSystemContext`.
  The injected block explicitly labels memory as untrusted evidence, not a
  user message or instruction.
- `agent_end` sends exactly the completed user prompt and final assistant text
  to TMCRA.
- `gateway_start` and `gateway_stop` drain a local durable pending-ingest queue.

The plugin does not register a tool or command, so the TMCRA credential is not
available to the model or end users. `TMCRA_API_KEY` and
`TMCRA_IDENTITY_SECRET` are process environment variables only. The latter is
used as the HMAC secret for stable opaque scope, session, message, and
idempotency identifiers; raw OpenClaw session, sender, chat, and run values are
never sent as identifiers.

## Configuration

Set plugin config in the OpenClaw operator configuration. Do not put secrets in
`plugins.entries.tmcra-openclaw.config`.

```json5
{
  plugins: {
    entries: {
      "tmcra-openclaw": {
        enabled: true,
        hooks: { allowConversationAccess: true },
        config: {
          baseUrl: "https://memory.example.invalid",
          tenantId: "customer-a",
          queuePath: "/var/lib/openclaw/tmcra/pending-ingest.json"
        }
      }
    }
  }
}
```

Required server-side environment:

```bash
export TMCRA_API_KEY=YOUR_ISSUED_API_KEY
export TMCRA_IDENTITY_SECRET=YOUR_IDENTITY_SECRET
```

`TMCRA_BASE_URL` and `TMCRA_TENANT_ID` may replace the corresponding config
fields. `TMCRA_QUEUE_PATH` may replace `queuePath`. The URL must be HTTPS, in
line with the TMCRA service contract.

## Behavior and failure handling

Recall failures do not block or alter the OpenClaw turn. Ingest failures are
written to the local queue and retried with the same `Idempotency-Key`, so a
process restart or a repeated `agent_end` event does not create duplicate
turns. Queue files are written atomically with owner-only permissions and do
not contain either credential.

The queue is retried at Gateway start, Gateway stop, and every 60 seconds by
default. A slow or unavailable TMCRA service therefore degrades to normal
OpenClaw operation while preserving pending writes locally.

## Install and verify

See [INSTALL.md](./INSTALL.md). The deterministic mock harness runs with:

```bash
npm test
```

It verifies hook registration, system-context injection, opaque identity
derivation, successful ingestion, idempotency-key reuse, queue persistence,
retry drain, validation, and credential non-disclosure.

An operator-only real-service smoke is also available as `npm run test:server`.
It requires the documented `TMCRA_*` environment variables, writes no secret
to its report, and verifies a native hook write followed by native hook recall.
