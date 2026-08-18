# @tmcra/typescript

Zero-runtime-dependency TypeScript client for the current TMCRA Memory API.
It uses the platform `fetch`, `AbortController`, `URL`, and `Headers` APIs.

## Install

```bash
npm install @tmcra/typescript
```

The published package contains compiled JavaScript and declarations. TypeScript
is a development-only dependency in this repository; no package is required at
runtime beyond a fetch-capable environment.

## Example

```ts
import { TMCRAClient } from "@tmcra/typescript";

const client = new TMCRAClient({
  baseUrl: process.env.TMCRA_BASE_URL!,
  apiKey: process.env.TMCRA_API_KEY!,
  defaultTimeoutMs: 30_000,
});

const job = await client.ingest("customer-user-42", {
  session_id: "session-2026-07-15",
  messages: [{
    message_id: "message-1",
    role: "user",
    content: "Remember that I prefer concise answers.",
    timestamp: new Date(),
  }],
  consistency: "read_your_writes",
});

const completed = await client.waitForJob(job.job_id);
const recall = await client.recall("customer-user-42", {
  query: "What answer style do I prefer?",
  wait_for_job_id: completed.job_id,
});
console.log(recall.prompt_evidence);
```

## Contract and behavior

- All API model properties use the service's wire-level `snake_case` names.
- `Date` values in `timestamp` and `query_time` are serialized as ISO-8601 strings.
- `ingest`, `consolidate`, and `retryJob` generate an `Idempotency-Key` when one
  is not supplied. Supply your own stable key when retrying an operation across
  process restarts.
- Safe retries are limited to GETs and idempotent job writes. The client never
  retries `recall` or `cancelJob` automatically. By default it retries HTTP
  408, 425, 429, 500, 502, 503, and 504, plus transport failures, with bounded
  exponential backoff and optional `Retry-After` support.
- A timeout applies to each HTTP attempt. Pass an `AbortSignal` to cancel both
  a request and any retry/poll delay. `waitForJob` also has a five-minute overall
  polling deadline by default.
- HTTP failures throw `TMCRAHttpError`; transport, timeout, abort, malformed
  response, and polling failures have distinct exported error classes. HTTP
  error details and the service `x-request-id` are preserved when available.

## Local verification

```bash
npm install
npm test
npm run typecheck
npm run build
```

Operators can run `npm run test:server` with `TMCRA_DEFAULT_SCOPE` and
`TMCRA_SMOKE_EXPECTED_TEXT` against a disposable scope. It verifies health,
readiness, and real recall without printing credentials or evidence content.
