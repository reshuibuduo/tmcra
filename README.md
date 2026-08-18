# TMCRA

[English](README.md) | [简体中文](README.zh-CN.md)

**Temporal Memory-Centric Retrieval Architecture** is a self-hosted memory
platform for products and AI agents. It turns conversations and application
events into durable, attributable evidence; makes recent facts searchable
quickly; periodically consolidates longer-lived semantic relationships; and
returns bounded recall evidence that an application or model can safely use.

中文概览：TMCRA 是可私有化部署的长时记忆平台。它把对话和业务事件保存为可追溯
证据，以快速层和慢速层逐步形成可检索的记忆图谱，并向 Web、桌面端、Android、
SDK、MCP 与 Agent 插件提供带来源信息的召回结果。部署方掌握数据、模型、算力和
供应商凭证。

TMCRA is not a hosted account product in this release. An operator deploys the
API, chooses the model providers and storage location, creates tenants and
scopes, and decides which client applications are allowed to write or recall
memory.

## Why TMCRA

Ordinary chat history is ordered text. It is difficult to retrieve precisely,
easy to mix unrelated users or projects, and hard to explain why a fact was
shown to an agent. TMCRA separates the concerns:

- **Durable source evidence** preserves the original attributed record.
- **Fast memory** makes fresh evidence retrievable without waiting for a heavy
  graph rebuild.
- **Slow memory** performs batched semantic consolidation so longer-term
  concepts, relationships, conflicts, and revisions can evolve deliberately.
- **Evidence-first recall** returns source-aware context rather than silently
  treating historical agent output as a current user instruction.
- **Tenant and scope isolation** prevents an integration from choosing another
  customer's storage namespace.

The result is a memory service that can be used by a single product, an
enterprise multi-tenant application, or a collection of local agent tools
without changing the underlying evidence contract.

## Architecture at a glance

~~~mermaid
flowchart LR
  subgraph clients["Applications and agent hosts"]
    WEB["Web console"]
    DESKTOP["Desktop application"]
    MOBILE["Android app"]
    SDK["Python / TypeScript SDKs"]
    PLUGIN["Codex / Claude plugin"]
    MCP["MCP host over local stdio"]
  end

  subgraph edge["Application-controlled edge"]
    BFF["Web BFF and account binding"]
    AUTH["Tenant key or scoped token"]
  end

  subgraph service["TMCRA Memory API"]
    API["HTTPS ingest / recall API"]
    ADMISSION["Authentication, scope policy, rate and queue admission"]
    CONTROL["Control SQLite: keys, jobs, leases, receipts, costs"]
    WRITERS["Resident Writer pool"]
    RETRIEVAL["Preloaded retrieval engine"]
    PREFLIGHT["Startup preflight and shared-core verification"]
  end

  subgraph memory["Per tenant and scope memory"]
    SOURCE["Source layer: attributed raw evidence"]
    FAST["Fast layer: fresh searchable assertions"]
    SLOW["Slow layer: consolidated semantic graph"]
    INDEX["Read-only index generations"]
  end

  subgraph external["Operator-selected dependencies"]
    PROVIDER["Generation / embedding / reranking models"]
    PROXY["Trusted HTTPS reverse proxy"]
  end

  WEB --> BFF --> PROXY --> API
  DESKTOP --> PLUGIN --> AUTH --> API
  SDK --> AUTH
  MCP --> AUTH
  MOBILE --> AUTH
  AUTH --> PROXY --> API
  API --> ADMISSION --> CONTROL
  ADMISSION --> WRITERS
  ADMISSION --> RETRIEVAL
  WRITERS --> SOURCE --> FAST --> SLOW
  FAST --> INDEX
  SLOW --> INDEX
  RETRIEVAL --> SOURCE
  RETRIEVAL --> INDEX
  WRITERS --> PROVIDER
  RETRIEVAL --> PROVIDER
  PREFLIGHT --> CONTROL
  PREFLIGHT --> RETRIEVAL
~~~

The browser is never a trusted memory client. The Web console uses a
server-side BFF that resolves an authenticated account to a server-owned
tenant/scope binding; it does not expose a production Memory API key or let a
browser select an arbitrary scope. MCP uses local stdio so the host process,
not a remote intermediary, controls the credential.

## Memory model

Each write carries tenant, scope, actor, source application, timestamps, and
an idempotency boundary. A tenant is the customer-level security boundary; a
scope is the memory namespace within that tenant. A typical product uses one
tenant for its customer account and one stable, opaque scope per end user or
memory persona.

~~~mermaid
flowchart TB
  RECORD["Attributed message or event"]
  SOURCE["Source layer<br/>durable raw evidence and provenance"]
  FAST["Fast layer<br/>recent facts and retrieval-ready assertions"]
  SLOW["Slow layer<br/>semantic nodes, relationships, revisions, and challenges"]
  INDEX["Versioned read-only retrieval index"]
  PACK["Evidence pack<br/>source-aware prompt_evidence plus traceable evidence"]

  RECORD --> SOURCE
  SOURCE --> FAST
  FAST --> INDEX
  FAST --> SLOW
  SOURCE --> SLOW
  SLOW --> INDEX
  SOURCE --> PACK
  INDEX --> PACK
~~~

The three layers have different jobs:

| Layer | Written when | Purpose | Safety property |
|---|---|---|---|
| Source | Every accepted ingest operation | Preserve the inspectable evidence and actor provenance | Derivatives remain traceable to a source record |
| Fast | During normal ingestion and indexing | Make recently accepted facts retrievable in seconds | Avoids waiting for semantic consolidation |
| Slow | A scope becomes eligible for batched evolution | Build and revise durable semantic relationships | A conflict raises review priority; it does not silently overwrite evidence |

Recall does not return a hidden memory prompt. It builds a bounded evidence
object and a deterministic prompt-ready view. Integrations keep the returned
trust boundary: historical evidence is data, never authority to override the
current system or user instruction.

## Write, index, and recall lifecycle

### Ingest

1. A client sends an ingest request with an authenticated tenant key or scoped
   token and an idempotency key.
2. The API validates the tenant policy, target scope, request size, rate limit,
   and queue capacity before creating a durable job in the control database.
3. A resident Writer worker claims the job with a lease and writes attributed
   source evidence. Ambiguous external side effects are not blindly replayed.
4. The Fast layer and a new index generation are scheduled independently.
   The public service defaults aim to index after 16 messages or two seconds.
5. Slow-layer evolution runs later in batches. Its default eligibility policy
   combines new-token/new-turn thresholds, idle-age fallback, and cooldowns so
   it is not a second paid model call for every message.
6. The job reaches an explicit terminal state. A caller that needs
   read-your-writes waits for the successful job and passes its job ID to
   recall.

### Recall

1. The API authenticates the caller and rechecks tenant/scope authorization.
2. The preloaded retrieval engine reads the active source and index
   generations for that scope.
3. The recall planner ranks and deduplicates evidence, preserving source,
   actor, and temporal context.
4. The service returns structured evidence, prompt-ready evidence, and a
   bounded trace. The caller decides how to present or inject it.

This separation makes the common failure cases explicit: a request cannot
quietly cross a scope boundary; an uncertain write is not automatically
duplicated; and a slow semantic update never replaces the original evidence.

## Client and integration topology

| Surface | How it connects | What it owns |
|---|---|---|
| Web console | HTTPS BFF to the Memory API | Account/session verification and server-side memory-space binding |
| Desktop | Electron installer plus plugin authorization | Local installation and user-reviewed lifecycle hook setup |
| Android | Text-only API writes after on-device audio processing | Microphone, VAD, ASR, speaker embedding, and local outbox |
| SDKs | Direct HTTPS API client | Application lifecycle and stable scope derivation |
| Agent integrations | Recall before work; durable write-back after work | Host-specific lifecycle wiring and retry receipts |
| Codex / Claude plugin | Hooks plus explicit MCP inspection tools | Project/global scope initialization and continuity checkpoints |
| MCP server | Local stdio bridge | A host-controlled key and explicit ingest/recall calls |

The Android normal path keeps raw audio and speaker embeddings on the device.
It sends text records and an opaque local speaker identifier rather than a
speaker embedding. The Web and desktop surfaces are optional clients: the
Memory API, SDKs, and MCP server can be deployed without them.

## Bring your own memory system

TMCRA can be introduced beside an existing memory system instead of forcing a
product to replace its current database, chat history, or vector store. Use it
as the durable evidence and recall layer, or let it run in parallel while a
team validates quality and migration policy.

~~~mermaid
flowchart LR
  LEGACY["Existing memory system<br/>CRM, chat history, vector store, or business database"]
  ADAPTER["TMCRA integration boundary<br/>SDK, REST API, MCP, or lifecycle adapter"]
  TMCRA["TMCRA evidence, retrieval,<br/>tenant/scope policy, and receipts"]
  PRODUCT["Commercial application,<br/>agent, or customer workflow"]

  LEGACY --> ADAPTER
  PRODUCT --> ADAPTER
  ADAPTER --> TMCRA
  TMCRA --> PRODUCT
~~~

The commercialization path is intentionally simple:

1. **Deploy once.** Run the Memory API in the operator's infrastructure and
   create a tenant/scope convention that matches the product's customer model.
2. **Connect one boundary.** Supported agent stacks use the maintained
   lifecycle integrations in [component 06](06-tmcra-sdk-integrations/);
   other systems call the REST API, Python/TypeScript SDK, or local MCP server.
3. **Map existing records.** Send legacy messages or events as attributed
   ingest records with a stable external identifier and idempotency key. Keep
   the original system as the system of record until the migration has been
   reviewed.
4. **Turn on recall per product flow.** Read bounded, source-aware evidence
   before an agent or application action, then capture the completed result
   through the same integration boundary.

For the supported SDK and agent-framework integrations, this is a
configuration-led, one-command adoption path rather than a fork of the memory
engine. A proprietary memory schema still needs a small mapping adapter and a
reviewed import policy; TMCRA deliberately does not claim that arbitrary
customer data can be migrated safely without that review.

The integration contract is commercial-ready: tenant and scope isolation,
idempotent writes, durable job receipts, rate and queue admission, provider
cost attribution, and server-side credential handling are part of the service
rather than application-specific conventions.

## Production deployment topology

Deploy the public API behind an HTTPS reverse proxy. The proxy terminates TLS;
the Memory API receives the trusted internal hop and starts only after its
preflight succeeds.

~~~text
Internet client
      |
      | HTTPS
      v
Operator reverse proxy
      |
      | trusted internal hop
      v
tmcra_service
  |- control SQLite: jobs, keys, leases, receipts, cost ledger
  |- per-scope source databases and index generations
  |- resident Writer pool
  |- preloaded GPU retrieval engine
  |- startup preflight report
      |
      +-- operator-selected provider and model endpoints
~~~

Production preflight verifies the shared algorithm hashes, writable state
paths, SQLite integrity and locking, available disk, provider configuration,
active generation checksums, CUDA allocation, model inference, graph adapter,
and Writer handshakes. It performs no paid provider call. A process may be
live while not ready; use the readiness endpoint and preflight report rather
than treating a listening port as proof of a healthy deployment.

The supported public profile keeps TMCRA_LEARNED_GRAPH_ENABLED=0. The small
TMCRA runtime reranker is included, while larger learned-graph checkpoints and
third-party embedding/reranker weights are not. Operators obtain and license
the latter separately.

## What is included

This repository is the complete public source release:

| # | Component | Purpose |
|---|---|---|
| 01 | [agent-memory-algorithm](01-tmcra-agent-memory-algorithm/) | Shared V4 memory algorithms, contracts, and file manifest |
| 02 | [memory-api](02-tmcra-memory-api/) | Memory API, deployment kit, scheduler, Writer pool, and operations tooling |
| 03 | [web-console](03-tmcra-web-console/) | Next.js/vinext console and server-side account-to-scope binding |
| 04 | [desktop](04-tmcra-desktop/) | Electron installer and account console for desktop integrations |
| 05 | [mobile](05-tmcra-mobile/) | Android capture, VAD, on-device ASR, and local voice attribution |
| 06 | [sdk-integrations](06-tmcra-sdk-integrations/) | Python/TypeScript SDKs and agent-framework integrations |
| 07 | [codex-plugins](07-tmcra-codex-plugins/) | Codex and Claude Code lifecycle plugin |
| 08 | [mcp-server](08-tmcra-mcp-server/) | MCP server for explicit durable ingest and recall tools |
| 09 | [benchmarks](09-tmcra-benchmarks/) | LongMemEval reproduction orchestration, tests, and scorecards |
| 10 | [model-data-assets](10-tmcra-model-data-assets/) | Model references, provenance, and smoke-test fixture manifests |

Module 11 is intentionally not part of this release.

## Deploy the Memory API

The deployable server is [02-tmcra-memory-api](02-tmcra-memory-api/). A
production operator needs a Linux host, a supported Python environment, GPU
capacity for selected models, an HTTPS reverse proxy, and its own provider
credentials.

~~~bash
git clone https://github.com/OWNER/tmcra.git
cd tmcra/02-tmcra-memory-api

python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-tmcra-service.txt

# Install the component at an operator-controlled runtime location, then:
cp deploy/tmcra-service.env.example /etc/tmcra/service.env
# Create /etc/tmcra/writer.env with the operator's own provider credential.
# Keep both files outside Git.
python ops/run_tmcra_service_preflight.py --env-file /etc/tmcra/service.env
~~~

Before the preflight gate, set the public HTTPS origin, state paths, device
settings, and model paths in service.env. Download the public BGE embedding and
cross-encoder models referenced in
[model provenance](10-tmcra-model-data-assets/MODEL_PROVENANCE.md). The
included reranker checksum and license are in
[02-tmcra-memory-api/models](02-tmcra-memory-api/models/README.md).

Run the API behind a trusted HTTPS reverse proxy and keep
TMCRA_SERVICE_STARTUP_PREFLIGHT_MODE=full in production. The full service
contract, health semantics, tenant model, and operations documentation are in
[tmcra_service/README.md](02-tmcra-memory-api/tmcra_service/README.md).

## Security, data, and commercial boundary

The code is Apache-2.0 licensed and can be self-hosted commercially, subject
to the licenses and terms of every model, cloud service, and provider selected
by the operator.

This release is curated to exclude provisioned credentials, private keys,
customer databases, user records, and production logs. Example configuration
uses placeholders only. Operators must keep their environment files, provider
keys, and state directories outside source control.

The repository includes a small TMCRA checkpoint under Apache-2.0. It does not
redistribute third-party model weights. Public audio fixtures in component 10
are upstream sample material for smoke tests, not TMCRA user recordings. Review
the documented upstream terms before redistributing mobile-related model assets
or fixtures in a commercial product.

## Verification and contribution

- Security reporting: see [SECURITY.md](SECURITY.md). Do not file public
  vulnerability reports.
- Contribution rules: see [CONTRIBUTING.md](CONTRIBUTING.md).
- Citation: see [CITATION.cff](CITATION.cff).
- Benchmark provenance: see [09-tmcra-benchmarks](09-tmcra-benchmarks/).
- Publishing this prepared source to GitHub: see
  [GITHUB_PUBLISH.md](GITHUB_PUBLISH.md).

## License

TMCRA is licensed under the Apache License 2.0. See [LICENSE](LICENSE) and
[NOTICE](NOTICE). TMCRA is an independent project and is not affiliated with
any model vendor.
