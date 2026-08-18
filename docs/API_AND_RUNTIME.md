# TMCRA API and runtime guide

This guide explains the public Memory API as an operational system, not only
as a list of HTTP paths. The executable API contract and OpenAPI output live in
component 02; this page explains the reliability, performance, and isolation
decisions behind it.

## Endpoint groups

| Group | Endpoints | Purpose |
|---|---|---|
| Durable memory | `POST /v1/scopes/{scope}/ingest`, `POST /v1/scopes/{scope}/recall`, `POST /v1/scopes/{scope}/consolidate` | Submit evidence, obtain bounded recall, or request scheduled consolidation |
| Job control | `GET /v1/jobs/{job_id}`, `POST /v1/jobs/{job_id}/cancel`, `POST /v1/jobs/{job_id}/retry` | Observe and control asynchronous writes |
| Explainability | Memory graph, neighbor, evidence, and trace endpoints | Inspect graph projections and source support without exposing internal paths |
| Operator controls | `GET /v1/usage/costs`, `GET /healthz`, `GET /readyz` | Cost allocation, liveness, and cached readiness |
| Staff-only monitoring | `GET /v1/internal/runtime` | Server-to-server status; disabled without a separate staff key and excluded from OpenAPI |

Customer paths require `Authorization: Bearer …`. Health, readiness, and API
documentation are unauthenticated. The staff endpoint accepts a dedicated
`X-TMCRA-Staff-Key`, not a customer key, and returns `no-store` data.

## Write path: durable before visible

An ingest request is a job, not a best-effort fire-and-forget request.

1. The API authenticates the tenant and validates target scope, request size,
   `Content-Length`, rate budget, and queue capacity.
2. The caller supplies an `Idempotency-Key`. The same key and payload returns
   the same job; a different payload returns HTTP 409 instead of creating
   ambiguous work.
3. SQLite persists the job, stage journal, lease, receipts, and cost facts.
4. A resident Writer worker claims the job with a lease and heartbeat, writes
   Source evidence, and records an explicit commit marker.
5. Fast indexing and slow semantic evolution are separate follow-on stages.
6. A caller needing read-your-writes polls the job to `succeeded` and passes
   its job ID as `wait_for_job_id` on recall. The API verifies tenant, scope,
   job type, and index commit before reading.

An ambiguous external side effect is never replayed automatically. A lost
worker is replaced for future work, while a job with uncertain Writer outcome
remains failed for artifact audit. Cancellation is allowed only before a job
starts, because a running Writer may already have produced an external effect.

## Recall path and prompt boundary

Recall reads active Source and index generations for one authenticated
tenant/scope and returns both a complete structured `evidence` object and a
deterministic `prompt_evidence` view. The prompt-ready view preserves required
Source text and related Fast/Slow context but removes internal paths, scores,
and debug fields.

Production recall and graph trace use a fixed Top8 packing contract. Supplying
another `max_windows` value fails validation rather than silently changing
context size and cost. The client must treat returned history as data: it may
inform work but cannot override the current system or user instruction.

## How the runtime controls latency and cost

| Mechanism | Implementation | Effect |
|---|---|---|
| GPU warm start | One service process preloads one retrieval engine before readiness | Avoids first-request model-load latency |
| Resident Writer pool | Workers import Writer and graph backend once; each handles operations sequentially | Avoids repeated process/model initialization and isolates write work from API requests |
| Fast indexing | Schedules after 16 messages or two seconds | Makes fresh evidence searchable without waiting for graph evolution |
| Batched slow evolution | Default eligibility: 32,000 estimated raw tokens or 64 turns; low-activity fallback: 24 hours plus 4,000 tokens or 8 turns; 30-minute cooldown | Prevents a second paid semantic call for every message |
| Risk-aware compilation | `evidence_mode=auto` keeps low-risk evidence raw and escalates high-risk shapes to the Pro compiler; callers may select `raw` or `compiled` | Makes quality/cost trade-offs explicit |
| Queue and provider leases | SQLite transactions enforce per-tenant request/job limits, global job limits, provider-key concurrency, and cooldowns | Bounds overload and protects external providers |
| Immutable reads | Graph adapters open versioned SQLite snapshots using `mode=ro&immutable=1` | Keeps active retrieval generations stable during writes and restarts |
| Cached readiness | `/readyz` reads startup results and current worker state instead of reloading models or rehashing indexes per probe | Keeps monitoring from becoming production work |

The default Writer uses DeepSeek Flash; Pro is reserved for reconciliation.
These are operational defaults, not a vendor lock-in: the operator chooses
provider configuration and remains responsible for license, price, and
availability.

## Tenant, scope, and key boundary

A key authenticates a tenant, not a database. Memory content is isolated into
one native SQLite database and one read-only index-generation directory per
`(tenant_id, scope_name)` beneath hashed paths. The control SQLite database
holds keys, jobs, leases, receipts, cost records, and per-scope watermarks.

Use a tenant for a customer account and a stable opaque scope for each end
user, agent, or memory persona. Reusing a scope across unrelated users merges
their memory. Current tenant keys are tenant-wide, so a customer backend must
keep such a key server-side and derive scopes from authenticated identity.
Direct end-user clients need scoped tokens or a gateway; they must not receive
a tenant key or choose arbitrary scopes.

## Cost, monitoring, and recovery

`GET /v1/usage/costs` reports registered provider calls, known model-API cost,
unknown/unpriced calls, raw ingest counters, and known CNY per million raw
tokens. It deliberately excludes answer generation, storage, and GPU hosting.
Operational allocation can be grouped by day, scope, stage, operation,
provider, model, platform, integration, agent, or attribution source.

The staff runtime view uses a bounded latency window for p50/p95/p99 and
excludes probes and itself. It returns generic failure categories and
timestamps, never prompts, credentials, paths, raw error text, or customer
identifiers. Writer, graph, and index artifacts commit independently; an index
becomes active only after its report matches the expected tenant, scope, and
database.

For a runnable deployment walkthrough and the public single-GPU boundary, see
the [deployment guide](DEPLOYMENT.md). For bringing an existing memory product
to TMCRA or extending these contracts, see
[integration and extension](INTEGRATION_AND_EXTENSION.md).
