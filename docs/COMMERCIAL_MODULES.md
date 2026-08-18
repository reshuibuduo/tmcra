# TMCRA commercial modules

TMCRA is self-hosted software, not a hosted billing service. Its commercial
modules provide the technical control plane a product needs to package,
authorize, meter, and operate memory for customers. The deploying operator
remains responsible for identity, payment collection, tax, invoices, support,
legal terms, and compliance policy.

## Commercial capability map

| Capability | What is in the release | Operational use |
|---|---|---|
| Multi-tenant isolation | Tenant authentication; isolated Source database and index generations per tenant/scope | Separate one customer account from another and avoid cross-customer memory access |
| Account and organization console | Personal, enterprise, and staff control surfaces; D1 account, organization, agent, membership, and binding records | Give users/teams a console without exposing Memory API keys to browsers |
| Credential lifecycle | Tenant API keys shown once and stored as PBKDF2 hashes; revocation; short-lived scoped tokens with permission/scope restrictions | Keep tenant keys server-side and issue narrower credentials to approved clients/devices |
| Device authorization | Desktop/device routes and confirmation flow; protected local credential delivery | Connect a Codex installation without asking a normal user to paste a server key |
| Plans and billing groups | Versioned plans, periods, group membership/roles, statuses, and per-subject entitlements | Model a subscription/product tier and allocate it to customer users or teams |
| Quota enforcement | Ingest raw-token and recall-request entitlements; tenant/subject quota identity | Apply an included allowance or product limit before generating unbounded API work |
| Cost and attribution | Provider-call ledger; known/unknown cost, raw-ingest counters, and grouping by scope/stage/provider/model/platform/integration/agent | Attribute operating cost to a product feature, customer, integration, or agent |
| Webhooks | Signed lifecycle notifications for job, ingest, consolidation, index, export, and scope-deletion events | Synchronize an operator's CRM, usage service, or customer workflow with memory state changes |
| Data controls | Retention-policy endpoints, scope/message deletion contracts, export/status surfaces, and feedback | Build user-facing privacy controls and support workflows on top of auditable operations |
| Operator monitoring | Separate staff key, release metadata, readiness, bounded latency/cost/queue facts, and generic failure categories | Run a private operations plane without leaking evidence or credentials to customers |

## Account, organization, and credential model

The Web Console separates personal, enterprise, and staff surfaces. Its BFF
uses the authenticated account to resolve a server-owned D1 binding to the
correct TMCRA tenant/scope. This is important commercially: a browser session
is an account identity, not an API credential, and the browser never gets a
tenant key or arbitrary-scope selector.

Tenant API keys are displayed once, then only PBKDF2 hashes are stored. Keys
can be independently issued and revoked. Scoped tokens add expiry, permissions,
and explicit scope-name/prefix restrictions. This supports a common commercial
pattern:

```text
customer signs in to product
  -> product backend verifies membership/plan
  -> backend derives approved tenant and scope
  -> backend calls TMCRA, or issues a limited scoped token
  -> product stores the resulting job/usage receipt
```

Do not distribute a tenant-wide key in a browser, mobile bundle, plugin
repository, or customer-controlled configuration file.

## Plans, groups, and entitlements

The internal billing control plane supports versioned plan definitions with a
billing interval, price/currency metadata, included raw ingest tokens, recall
requests, maximum members, and additional entitlements. It also supports
billing groups within a tenant, member roles, lifecycle periods, and group
status changes.

The public usage plane can report a subject's quota/billing profile. A managing
key with `tokens:manage` can set per-subject ingest-token and recall-request
entitlements. These controls meter memory service capacity; they do not charge
a card, issue an invoice, calculate tax, or act as a payment processor. Connect
them to the operator's chosen commerce system through a trusted backend.

## Usage and cost attribution

`GET /v1/usage/costs` records registered provider calls, priced and unpriced
calls, raw-ingest counters, and known CNY-per-million-raw-token values. It can
group operational allocation by day, scope, stage, operation, provider, model,
platform, integration, agent, or attribution source.

The ledger intentionally does **not** claim to be a complete customer invoice:
answer generation, storage, GPU hosting, taxes, and other operator costs are
outside this endpoint. Treat tenant/subject quota and registered provider-call
totals as the authoritative memory-service facts; use your commerce system for
the final customer bill.

## Webhooks and product automation

The webhook subsystem can deliver signed notifications for:

- `job.succeeded`, `job.failed`, and `job.cancelled`;
- `ingest.completed`, `consolidation.completed`, and `index.completed`;
- `export.ready` and `scope.deleted`.

Webhook endpoints must use HTTPS. The service rejects local/private targets and
performs DNS resolution checks so a customer-configured callback cannot target
loopback or private network addresses. Operators should still verify signatures,
deduplicate deliveries, apply their own retry/alert policy, and keep the
destination under their control.

## Data and support controls

The release includes retention-policy, content-deletion, export/status, and
feedback contracts. Build customer-facing privacy controls through the product
BFF, not by exposing unrestricted operator endpoints. Retention, erasure,
export formats, consent, regional storage, and legal response policy are
operator decisions and must be reviewed for the deployment's jurisdiction.

The internal runtime endpoint uses a separate staff credential and avoids raw
customer content. It exposes startup/readiness state, bounded request latency,
job stages, queue/cost facts, and generic error categories. Release metadata
can identify the deployed artifact, channel, canary percentage, and declared
rollback target for operational rollouts.

## What a commercial deployer still owns

TMCRA provides a strong technical base, but the following are not a hosted
product delivered by this repository:

- customer identity provider, SSO, and product-specific authorization rules;
- payment processor, invoices, tax, refunds, and revenue recognition;
- legal terms, data-processing agreements, consent, retention policy, and
  jurisdiction-specific compliance decisions;
- customer support staffing, incident communication, and SLA commitments;
- multi-GPU capacity planning and the selected cloud/hosting provider.

Use the [integration and extension guide](INTEGRATION_AND_EXTENSION.md) to
connect these operator-owned systems through a server-side mapping and
webhooks, while preserving TMCRA tenant/scope and receipt boundaries.
