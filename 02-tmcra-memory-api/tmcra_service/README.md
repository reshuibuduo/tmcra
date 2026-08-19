# TMCRA Memory API

This package is the production HTTP control plane. It is separate from the
`run_tmcra_v4_*.py` benchmark orchestration and stores no benchmark labels or
frozen answers.

`shared_core_manifest.json` pins the exact shared V4 algorithm files consumed
by this service. Startup fails closed when any pinned file is missing or has a
different SHA256. Release verification also runs the service test suite with
`TMCRA_VERIFY_SHARED_CORE=1` on the deployment checkout.

## Runtime contract

- Public base URL must be HTTPS.
- Public deployments terminate TLS at a trusted reverse proxy and use
  `TMCRA_SERVICE_TLS_PROXY_MODE=trusted_proxy` for the internal hop.
- One service process owns one startup-preloaded GPU retrieval engine.
- Writer operations are asynchronous jobs with durable SQLite state, worker
  leases, heartbeats, and explicit commit markers.
- Production ingestion uses an isolated resident Writer process pool. Each
  worker imports the unchanged benchmark V4 Writer and graph backend once, then
  handles operations sequentially. A lost worker is replaced only for future
  work; an in-flight operation with an uncertain outcome is never replayed.
  A one-second local monitor replaces idle failed workers before they can be
  assigned to a customer operation.
- Default ingestion uses DeepSeek Flash. Pro is invoked only when the Writer
  creates a reconciliation job.
- Default recall uses `evidence_mode=auto`: low-risk evidence remains raw and
  high-risk shapes invoke the Pro compiler. Callers may still explicitly select
  `raw` or `compiled` as a cost/quality override.
- Slow-graph consolidation defaults to `slow_policy=auto`, but it is a batched
  evolution layer rather than a second per-message write. A scope becomes due
  after 32,000 new estimated raw tokens or 64 new user turns. The low-activity
  fallback requires 24 hours of dirty age plus at least 4,000 tokens or 8 user
  turns. Automatic runs have a 30-minute cooldown; conflicts only raise
  priority and never trigger a model call by themselves.
- Fast indexing is scheduled independently after 16 new messages or two seconds,
  so fresh evidence can become searchable without waiting for Slow evolution.
- The production process preloads the online retrieval engine before becoming
  ready, so the first customer request does not pay model-loading latency.

## Startup gate

Production defaults to `TMCRA_SERVICE_STARTUP_PREFLIGHT_MODE=full`. Before the
HTTP lifespan becomes ready it verifies the deployment paths and shared-core
hashes, state-directory atomic writes, SQLite integrity and write locking,
free disk, provider configuration, every active generation checksum, CUDA
allocation, BGE-M3 and cross-encoder inference, node/path checkpoints, one
available graph adapter, and every resident Writer handshake. These probes are
local and make no paid provider call.

The complete report is atomically persisted as
`$TMCRA_SERVICE_STATE_DIR/startup_preflight.json`. `/healthz` remains a process
liveness probe. `/readyz` reads the cached startup result and adds current
worker, Writer-pool, and shared-core status; it does not repeat model loading or
index hashing on every request. The deployment control script declares startup
successful only after `/readyz` returns HTTP 200.

Operators can run the same no-HTTP, no-paid-call gate before a deployment:

```bash
python ops/run_tmcra_service_preflight.py \
  --env-file /opt/tmcra/deploy/tmcra-service.env
```

The relevant production settings are:

```text
TMCRA_SERVICE_WRITER_EXECUTION_MODE=resident
TMCRA_SERVICE_WRITER_POOL_SIZE=4
TMCRA_SERVICE_WRITER_POOL_STARTUP_TIMEOUT_SECONDS=120
TMCRA_SERVICE_WRITER_POOL_REQUEST_TIMEOUT_SECONDS=900
TMCRA_SERVICE_STARTUP_PREFLIGHT_MODE=full
TMCRA_SERVICE_STARTUP_TIMEOUT_SECONDS=180
```

## Staff-only runtime monitoring

`GET /v1/internal/runtime` is a server-to-server operations endpoint. It is
excluded from OpenAPI and is disabled unless
`TMCRA_SERVICE_STAFF_MONITORING_KEY` is configured. The key must contain 32 to
512 characters, is compared with `hmac.compare_digest`, and must be sent only
in `X-TMCRA-Staff-Key`. A missing service key returns HTTP 404; a missing or
incorrect request key returns HTTP 401. The response is marked `no-store`.
This key belongs in the internal control-plane server environment, never in a
browser bundle, desktop application, customer SDK, log, or URL.

Generate and install a distinct staff-monitoring credential during deployment:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'

export TMCRA_SERVICE_STAFF_MONITORING_KEY='<generated server-side value>'
curl -fsS https://api.tmcra.com/v1/internal/runtime \
  -H "X-TMCRA-Staff-Key: $TMCRA_SERVICE_STAFF_MONITORING_KEY"
```

The endpoint aggregates existing service facts rather than maintaining a
second operations database:

- startup status comes from the persisted `startup_preflight.json`, but only
  known check names, booleans, timing, and a generic failure category are
  returned;
- current job and operation-stage counts come from the control DB; recent
  failures expose only a fixed category and timestamp, never raw error text,
  identifiers, paths, prompts, request/response bodies, or Tokens;
- request p50/p95/p99 uses a bounded in-memory window populated by the HTTP
  middleware. It stores only timestamp, duration, and status code. Health,
  readiness, documentation, OpenAPI, and the staff endpoint itself are
  excluded so monitoring probes do not distort customer-request latency;
- provider-call cost comes from the registered provider-call ledger. Slow
  evolution reserved/spent cost is reported separately and must not be added
  to provider-call cost without checking whether the caller's accounting
  model treats it as the same physical call;
- current readiness is the existing `ContinuousReadinessMonitor` snapshot.

No sample means `unavailable`, not zero latency. Missing release metadata also
returns explicit `unavailable` field envelopes. Queue and cost values are
reported only when their control-DB queries succeed; failures return a generic
unavailable reason without embedding the database exception.

Production release automation should set the following validated metadata.
These values are declarations from the deployment system; the service does not
infer canary allocation or invent a rollback target:

```text
TMCRA_SERVICE_RELEASE_ID=release-20260718-1
TMCRA_SERVICE_RELEASE_SHA256=<64 lowercase or uppercase hex characters>
TMCRA_SERVICE_RELEASE_CHANNEL=stable
TMCRA_SERVICE_CANARY_PERCENT=0
TMCRA_SERVICE_ROLLBACK_RELEASE_ID=release-20260717-4
TMCRA_SERVICE_STAFF_LATENCY_WINDOW_SECONDS=300
TMCRA_SERVICE_STAFF_LATENCY_MAX_SAMPLES=4096
TMCRA_SERVICE_STAFF_RECENT_ERROR_WINDOW_SECONDS=86400
TMCRA_SERVICE_STAFF_RECENT_ERROR_LIMIT=20
```

`TMCRA_SERVICE_RELEASE_ID`, channel, canary percentage, and rollback target are
independently optional. Omitted values remain unavailable in the staff
contract. `ops/run_tmcra_service_preflight.py` prints these non-secret release
fields plus a boolean `staff_monitoring_enabled`; it never prints the key.

## Authentication and tenants

Raw API keys are shown once and only PBKDF2 hashes are stored.

```bash
python -m tmcra_service.cli tenant-create --tenant-id customer-a
python -m tmcra_service.cli key-issue --tenant-id customer-a
python -m tmcra_service.cli key-revoke --key-id KEY_ID
python -m tmcra_service.cli status
```

Each tenant can own multiple independently revocable keys. A key's permissions
must be granted by both the key and the tenant policy.

## Endpoints

- `POST /v1/scopes/{scope}/ingest`
- `POST /v1/scopes/{scope}/recall`
- `POST /v1/scopes/{scope}/consolidate`
- `GET /v1/jobs/{job_id}`
- `POST /v1/jobs/{job_id}/cancel`
- `POST /v1/jobs/{job_id}/retry`
- `GET /v1/usage/costs`
- `GET /v1/scopes/{scope}/memory-graph`
- `GET /v1/scopes/{scope}/memory-graph/nodes/{memory_id}/neighbors`
- `GET /v1/scopes/{scope}/memory-graph/nodes/{memory_id}/evidence`
- `POST /v1/scopes/{scope}/memory-graph/trace`
- `GET /healthz`
- `GET /readyz`
- `GET /v1/internal/runtime` (staff-only, hidden from OpenAPI)
- `GET /docs`

`/readyz` intentionally returns only boolean component status and does not
expose server paths, provider endpoints, or database error text. Operators use
the CLI status command and service logs for detailed diagnosis.

Customer endpoints require `Authorization: Bearer ...`; health probes and API
documentation are unauthenticated. The hidden staff runtime endpoint uses only
the dedicated `X-TMCRA-Staff-Key` contract described above and does not accept
customer API keys.
Job-creating and retry requests require an `Idempotency-Key`: the same key and
payload return the same job, while a different payload returns HTTP 409.
Scoped Token issuance also requires `Idempotency-Key`; an identical replay
returns the same credential. Device brokers may request a short provisional
delivery lifetime and confirm it only after the client has durably saved the
credential, preventing abandoned year-long Tokens after a lost response.
Cancellation is allowed only while a job is still pending; running jobs cannot
be cancelled because an external Writer may already have side effects.

`GET /v1/usage/costs` reports registered provider calls, known model-API cost,
unknown or unpriced calls, raw ingest counters, and known CNY per million raw
tokens. It intentionally excludes answer generation, storage, and GPU hosting.
The endpoint accepts an exact `scope_name` or, for a managing API key, a
`scope_prefix`; optional `from_timestamp` and `to_timestamp` form a half-open
time window. `group_by` supports `day`, `scope`, `stage`, `operation`,
`provider`, `model`, `platform`, `integration`, `agent`, and
`attribution_source`.

Usage attribution is stored on quota events and provider calls as
`client_platform`, `integration_id`, `agent_id`, and `attribution_source`.
Direct SDK and scoped-token labels are `client_reported`. A personal BFF call
is `trusted_proxy` only inside the managing-key/on-behalf trust boundary and
after the BFF has checked the integration registry. Automatic consolidation
costs use the `tmcra_internal` / `system_derived` cost center. Missing legacy
identity remains `unattributed`; it is never guessed from ingest metadata.
Tenant/subject quota and registered provider-call totals are the authoritative
billing facts. Platform, integration, and Agent buckets are operational
allocation views unless the request came through the trusted proxy boundary.

Recall returns both the complete structured `evidence` object and a deterministic
`prompt_evidence` view. The latter preserves Source text and required Fast/Slow
and neighboring context while omitting internal paths, scores, and debug fields.
Production recall and graph trace enforce the fixed Top8 packing contract;
requests that supply any other `max_windows` value fail validation with HTTP 422.

## Storage isolation

API keys authenticate a tenant; they are not database identities. The control
plane uses one shared SQLite database for API keys, jobs, stage journals,
provider-call costs, per-scope watermarks, and mutable graph-runtime audits.
Memory content is isolated into one native SQLite database and one read-only
index-generation directory per
`(tenant_id, scope_name)`, under hashed tenant and scope paths.

Graph adapters open generation SQLite snapshots with
`mode=ro&immutable=1`. Retrieval and answer-support audits are committed to the
control database and rehydrated on adapter load, so a recall or process restart
cannot invalidate an active generation hash.

For a multi-user product, use one tenant for the customer account and a stable,
opaque scope for each end user or memory persona. Reusing one scope for unrelated
end users deliberately merges their memory and is therefore a caller error.

Keys are currently tenant-wide: `scope_name` is a storage namespace, not a
per-key ACL. A customer backend must keep the key server-side and derive the
scope from its authenticated user identity. Directly distributing a tenant key
to end-user clients is unsupported; that deployment needs scoped tokens or a
gateway that enforces an allowed-scope claim.

For read-your-writes behavior, submit an ingest request with
`consistency=read_your_writes`, poll its job to `succeeded`, then pass the
returned job ID as `wait_for_job_id` on recall. The service verifies tenant,
scope, job type, and index commit before reading.

## Pressure controls

The service enforces all of the following in SQLite transactions:

- concurrent HTTP request leases per tenant;
- fixed per-minute request budgets per tenant;
- pending/running job limits per tenant;
- a global pending/running job limit;
- per-provider-key concurrent leases and cooldowns;
- request-size and required `Content-Length` contracts.

Idempotent replays bypass queue admission because they do not create work.

## Failure recovery

Writer, slow graph, and index artifacts are committed separately. A failed
ingest can resume automatically only when the Writer commit and database are
both durable. Index attempts use distinct files and activate only after the
index report matches the expected tenant scope and database. Operations with
ambiguous external side effects remain failed and require artifact audit.

## Deployment

The deployable files are in `deploy/`. A third-party operator can deploy this
release without a private TMCRA checkout:

1. Create `/opt/tmcra` from this component, then install
   `requirements-tmcra-service.txt` in a dedicated Python environment.
2. Download the publicly referenced BGE embedding and cross-encoder models to
   the paths in `deploy/tmcra-service.env.example`; copy the bundled
   `models/tmcra_v3_reranker.pt` to that same models directory.
3. Copy `deploy/tmcra-service.env.example` to `/etc/tmcra/service.env` and
   `deploy/writer.env.example` to `/etc/tmcra/writer.env`; replace every path,
   URL, and credential placeholder. Never publish either live environment file.
4. Set `TMCRA_SERVICE_PUBLIC_BASE_URL` to the operator's HTTPS origin and use
   `trusted_proxy` only when the bind address is protected by that proxy.
5. Run `python ops/run_tmcra_service_preflight.py --env-file
   /etc/tmcra/service.env` before enabling the system service. A successful
   preflight is the readiness gate; it performs no paid provider call.

`TMCRA_LEARNED_GRAPH_ENABLED=0` is the supported public default. Enabling it
requires separately licensed node/path checkpoints and is deliberately not a
silent fallback.

`TMCRA_SERVICE_STARTUP_PREFLIGHT_MODE=off` exists only for unit tests and
local contract development. Production deployment must use `full` and treats a
failed preflight as a hard startup failure.

The default production route is explicit: local `Qwen3.6-35B-A3B` for Writer,
Reviewer, recall planning, and slow-graph generation; local `BAAI/bge-m3`
embedding; local `BAAI/bge-reranker-v2-m3` cross encoding; and the bundled TMCRA
runtime reranker. The Qwen model has 35B total parameters and about 3B active
parameters per token. DeepSeek V4 Flash/Pro is an optional provider route. The
Memory API returns a bounded evidence pack, so the calling product may use its
own outer Agent model. Exact model roles, retrieval sizes, environment
variables, and replacement contracts are in
[`docs/PRODUCTION_MODEL_STACK.md`](../../docs/PRODUCTION_MODEL_STACK.md).

On a normal systemd host, install
`tmcra-memory-api.service`. On hosts without a working systemd control plane,
use `deploy/tmcra-memory-api-control.sh start`. The supervisor parses
`--env-file`, validates the deployment and shared-core manifest before entering
its restart loop, and writes durable supervisor/service PID files. The
bootstrap key file and service state directory must remain mode-restricted.
