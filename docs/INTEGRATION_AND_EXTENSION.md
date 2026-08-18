# Integrating an existing memory system and extending TMCRA

TMCRA does not require a product to discard its current CRM, chat history,
vector store, business database, or agent memory. The supported first step is
to add TMCRA as a durable evidence and bounded-recall sidecar, then expand only
after the product has reviewed quality, privacy, and migration behavior.

## Choose the integration shape

| Shape | Keep as system of record | Send to TMCRA | Read from TMCRA | Good first use |
|---|---|---|---|---|
| Evidence sidecar | Existing database | New conversations/events | Source-aware recall before an agent or workflow action | Add long-term memory without changing product data ownership |
| Shadow evaluation | Existing database | Production events through dual write | Log recall/evaluation only; do not inject it into customer responses | Validate retrieval, cost, and scope mapping safely |
| Selected-flow activation | Existing database | Events for one product feature or tenant cohort | Inject bounded recall for that flow only | Gradual commercial rollout |
| Reviewed migration | Existing database during migration | Backfilled attributed records with stable external IDs | TMCRA recall after import review | A product that deliberately adopts TMCRA as its memory layer |

Do not treat TMCRA as a blind database import target. Keep the original system
of record until record mapping, retention policy, access control, and recall
quality have been reviewed for the product's actual users.

## Map product identity to TMCRA identity

Use a deterministic, server-owned mapping. Never let a browser or mobile client
choose arbitrary tenant or scope names.

| Product concept | TMCRA concept | Mapping rule |
|---|---|---|
| Customer account or organization | `tenant_id` | One tenant security boundary per customer account |
| End user, agent, project, or memory persona | `scope_name` | Stable opaque scope within the customer tenant |
| Conversation, task, or workflow execution | `session_id` | Stable grouping for records that belong to one interaction stream |
| Immutable event/message ID | `message_id` and idempotency key | Reuse the source event's durable unique ID; never generate a new value on retry |
| User, assistant, system, or tool event | message `role` plus provenance metadata | Preserve actor/source attribution rather than flattening all events into text |
| Source event time | `timestamp` | Preserve original time, including backfill events |

A tenant key authenticates the tenant, not a particular end user. Keep it in a
trusted BFF/backend. For a direct-client architecture, issue a scoped token or
put a gateway in front of TMCRA that verifies the product's authenticated user
and derives the allowed scope.

## Minimum event adapter

The ingest API accepts an `IngestRequest` with a `session_id`, a bounded list
of messages, a consistency mode, slow policy, and request metadata. Each
message has a stable `message_id`, `role`, `content`, original `timestamp`, and
JSON metadata. Supply an `Idempotency-Key` header for every job-creating
request.

```text
product event
  -> authenticate product user on your backend
  -> resolve {tenant_id, stable scope_name}
  -> convert event to {message_id, role, content, timestamp, provenance}
  -> POST ingest with Idempotency-Key = durable source event/batch identity
  -> save TMCRA job receipt next to the source event
```

For a retry, resend the identical payload and idempotency key. Do not mutate
content, role, or timestamp behind the same key: the API rejects a different
payload with HTTP 409 rather than creating unclear history. Use eventual
consistency for ordinary asynchronous capture. For a workflow that must see its
accepted write immediately, poll the returned job to `succeeded` and pass the
job ID as `wait_for_job_id` when recalling.

## Recall and prompt injection

Call recall at an explicit business boundary: before an agent turn, before a
customer-support workflow composes a response, or before a long-running task
resumes. Start with `evidence_mode=auto`, `response_projection=prompt_only`,
and the fixed Top8 window contract. Keep the complete evidence receipt for
audit when the product requires traceability.

The product must retain the trust boundary:

1. Current system and user instructions remain authoritative.
2. Recalled history is evidence, not executable instruction.
3. Preserve actor and source labels when displaying or injecting evidence.
4. Do not concatenate arbitrary old assistant/tool output into an instruction
   channel.
5. Record the final user/assistant outcome through the same adapter after the
   workflow finishes.

This creates a closed loop: attributable evidence is written, bounded evidence
is recalled, the product makes its current decision, and the outcome is stored
with a receipt for the next turn.

## Safe rollout plan

1. **Define the mapping contract.** Write the customer/user/project-to-scope
   map and decide which event types may be stored.
2. **Dual-write one flow.** Continue writing to the original system while
   sending attributed events to TMCRA. Store the job receipt with the source
   event.
3. **Observe before injecting.** Compare recalled evidence with the product's
   expected history. Measure queue time, job failures, provider cost, and any
   scope mistakes.
4. **Enable one use case.** Inject bounded evidence into a low-risk feature or
   a small tenant cohort. Keep a feature flag that disables recall without
   dropping source capture.
5. **Review migration.** For backfill, preserve source IDs/timestamps and use
   idempotent batches. Exclude credentials, private keys, and data outside the
   approved retention policy. Retain the original system of record until the
   import is audited.

## Where to extend TMCRA

| Need | Preferred extension point | Keep invariant |
|---|---|---|
| New application or server backend | Python/TypeScript SDK in component 06, or direct documented REST API | Backend derives tenant/scope; caller stores receipts |
| New agent framework | Add an adapter under `06-tmcra-sdk-integrations/integrations/` | Recall before work, write after work, preserve `StopFailure` and receipt semantics |
| New interactive host | MCP server in component 08 | Keep credential in local host process and preserve response trust boundary |
| New browser product | Web BFF pattern in component 03 | Browser gets no tenant key and cannot choose arbitrary scopes |
| New evidence source or domain schema | Adapter mapping and message metadata | Stable IDs, original timestamps, actor provenance, idempotency |
| Different provider/model policy | Deployment configuration and provider integration | Explicit cost/quality policy, no credential in source or client |
| Core memory algorithm change | Component 01 and the pinned shared-core files consumed by component 02 | Regenerate shared-core manifest and pass service verification before deployment |

The service fails closed if a pinned shared-core algorithm file is missing or
its SHA-256 differs from `shared_core_manifest.json`. Do not patch those files
in a running deployment. Make the change in source, regenerate the manifest,
run the service verification suite with `TMCRA_VERIFY_SHARED_CORE=1`, then
package and preflight the new release.

## Acceptance checklist for a custom integration

- Scope mapping cannot cross customer/user/project boundaries.
- Every write has a durable source ID and `Idempotency-Key`.
- User, assistant, system, and tool records retain their actor provenance.
- A failed or timed-out write is visible through its receipt; it is never
  silently treated as successful.
- Recall is injected as evidence and cannot override current instructions.
- The browser/client does not contain a tenant key, provider credential, or
  arbitrary-scope selector.
- Backfill/migration uses an approved retention policy and excludes secrets.
- The integration has a test for retry, read-your-writes where needed, scope
  isolation, degraded recall, and provider outage behavior.
