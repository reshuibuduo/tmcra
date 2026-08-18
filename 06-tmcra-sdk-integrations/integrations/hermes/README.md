# TMCRA Hermes Agent Plugin

This directory is a standalone Hermes Agent `MemoryProvider` plugin. It uses
the official provider lifecycle: Hermes calls `prefetch()` before each model
turn and calls `sync_turn()` only after a completed user and assistant turn.
The provider does not register tools, alter the built-in memory provider, or
replace any Hermes agent behavior.

Recall is sent to `POST /v1/scopes/{opaque_scope}/recall`. The returned
`prompt_evidence.content` is bounded and wrapped in an explicit
`<tmcra-memory-context>` block that labels it as untrusted data, not a user
message or instruction. Successful turns are written to a local durable queue
before asynchronous ingest to `POST /v1/scopes/{opaque_scope}/ingest`.

All scope, session, message, and idempotency identifiers are stable HMAC-SHA256
derivations using `TMCRA_IDENTITY_SECRET`; raw Hermes session, user, and chat
identifiers are never sent as identifiers. The TMCRA API key is read only from
`TMCRA_API_KEY`, used for the server-side bearer header, and is never included
in prompt context, queue metadata, or logs.

## Configuration

Required host environment:

```text
TMCRA_BASE_URL=https://memory.example.invalid
TMCRA_TENANT_ID=tenant-a
TMCRA_API_KEY=<server-issued-key>
TMCRA_IDENTITY_SECRET=<stable-secret-at-least-16-characters>
```

Optional environment settings are `TMCRA_HERMES_QUEUE_PATH` (absolute path),
`TMCRA_HTTP_TIMEOUT_SECONDS`, `TMCRA_MAX_CONTEXT_CHARS`, `TMCRA_MAX_WINDOWS`,
`TMCRA_MAX_ATTEMPTS`, `TMCRA_RETRY_BASE_SECONDS`,
`TMCRA_RETRY_MAX_SECONDS`, and `TMCRA_DRAIN_INTERVAL_SECONDS`.

## Failure behavior

Recall is fail-open: an unavailable TMCRA service produces no injected context
and Hermes continues normally. `sync_turn()` is non-blocking. Ingest is first
persisted to an owner-readable, atomically replaced JSON queue and then retried
with the same idempotency key. Retries use bounded exponential backoff and move
to a capped local dead-letter list after `TMCRA_MAX_ATTEMPTS` failures.

The queue contains conversation content while writes are pending. Place it on
the Hermes host filesystem, protect the directory, and do not commit it.

## Development

Run deterministic mock tests from this directory:

```bash
python -m unittest discover -s tests -v
```

The suite uses an injected mock opener and does not contact TMCRA.

For an operator-only real-service check, run `tmcra-hermes-smoke` with the
documented `TMCRA_*` environment variables and the official Hermes package on
`PYTHONPATH`. It verifies the real `MemoryProvider` base class, native turn
sync, job completion, and native prefetch recall without exposing credentials.
