import assert from "node:assert/strict";
import { test } from "node:test";
import {
  TMCRAClient,
  TMCRAHttpError,
  TMCRAJobFailedError,
  TMCRAJobPollingTimeoutError,
  TMCRATimeoutError,
} from "../src/index.ts";

function response(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function job(status: string, id = "job-1") {
  return {
    job_id: id,
    tenant_id: "tenant-a",
    scope_name: "default",
    job_type: "ingest",
    status,
    attempts: 1,
    created_at: 1,
    updated_at: 2,
    started_at: null,
    finished_at: null,
    heartbeat_at: null,
    lease_expires_at: null,
    result: null,
    error: null,
    status_url: `https://api.tmcra.com/v1/jobs/${id}`,
  };
}

test("ingest serializes dates, sends bearer auth, and generates an idempotency key", async () => {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const client = new TMCRAClient({
    baseUrl: "https://api.tmcra.com/",
    apiKey: "secret",
    fetch: async (input, init) => {
      calls.push({ url: String(input), init: init ?? {} });
      return response(202, job("pending"));
    },
  });

  await client.ingest("scope/a", {
    session_id: "session-1",
    messages: [{ message_id: "m1", role: "user", content: "hi", timestamp: new Date("2026-07-15T00:00:00Z") }],
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0]?.url, "https://api.tmcra.com/v1/scopes/scope%2Fa/ingest");
  const headers = new Headers(calls[0]?.init.headers);
  assert.equal(headers.get("authorization"), "Bearer secret");
  assert.match(headers.get("idempotency-key") ?? "", /^.{8,200}$/);
  assert.deepEqual(JSON.parse(String(calls[0]?.init.body)), {
    session_id: "session-1",
    messages: [{ message_id: "m1", role: "user", content: "hi", timestamp: "2026-07-15T00:00:00.000Z" }],
  });
});

test("client sends ledger attribution and queries platform buckets", async () => {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const client = new TMCRAClient({
    baseUrl: "https://api.tmcra.com",
    clientPlatform: "typescript",
    integrationId: "int_local_typescript",
    agentId: "planner",
    fetch: async (input, init) => {
      calls.push({ url: String(input), init: init ?? {} });
      return response(200, {
        tenant_id: "tenant-a",
        scope_name: null,
        scope_prefix: "scope-",
        source_ledger_coverage: "scope_evolution_totals",
        currency: "CNY",
        ledger_coverage: "registered_calls_only",
        complete_for_registered_calls: true,
        source: {}, calls: {}, known_cost_cny: 0,
        known_model_api_cny_per_million_ingested_raw_tokens: null,
        uncertain_cost_call_count: 0, by_stage: {}, group_by: "platform", buckets: [],
        attribution_coverage: {
          system_derived: {
            provider_call_count: 0, usage_event_count: 0, ingest_raw_tokens: 0,
            recall_requests: 0, known_cost_micro_cny: 0,
          },
        },
      });
    },
  });
  const result = await client.usageCosts(undefined, {
    scopePrefix: "scope-",
    fromTimestamp: 100,
    toTimestamp: 200,
    groupBy: "platform",
  });
  const headers = new Headers(calls[0]?.init.headers);
  assert.equal(headers.get("x-tmcra-client-platform"), "typescript");
  assert.equal(headers.get("x-tmcra-integration-id"), "int_local_typescript");
  assert.equal(headers.get("x-tmcra-agent-id"), "planner");
  const usageUrl = new URL(calls[0]?.url ?? "");
  assert.equal(usageUrl.searchParams.get("scope_prefix"), "scope-");
  assert.equal(usageUrl.searchParams.get("from_timestamp"), "100");
  assert.equal(usageUrl.searchParams.get("to_timestamp"), "200");
  assert.equal(usageUrl.searchParams.get("group_by"), "platform");
  assert.equal(result.group_by, "platform");
  assert.equal(result.scope_name, null);
  assert.equal(result.scope_prefix, "scope-");
  assert.ok(result.attribution_coverage?.system_derived);
});

test("safe idempotent writes retry bounded transient failures with the same key", async () => {
  let calls = 0;
  const keys: string[] = [];
  const client = new TMCRAClient({
    baseUrl: "https://api.tmcra.com",
    retry: { initialDelayMs: 0, maxDelayMs: 0, jitter: 0 },
    fetch: async (_input, init) => {
      calls += 1;
      keys.push(new Headers(init?.headers).get("idempotency-key") ?? "");
      return calls === 1 ? response(503, { error: { code: "busy", message: "try again" } }) : response(202, job("pending"));
    },
  });
  await client.consolidate("default", { idempotencyKey: "stable-key-1" });
  assert.equal(calls, 2);
  assert.deepEqual(keys, ["stable-key-1", "stable-key-1"]);
});

test("non-idempotent recall does not retry and preserves API error details", async () => {
  let calls = 0;
  const client = new TMCRAClient({
    baseUrl: "https://api.tmcra.com",
    fetch: async () => {
      calls += 1;
      return response(409, { detail: { code: "write_not_committed" } }, { "x-request-id": "request-7" });
    },
  });
  await assert.rejects(
    client.recall("default", { query: "hello" }),
    (error: unknown) => {
      assert.ok(error instanceof TMCRAHttpError);
      assert.equal(error.status, 409);
      assert.equal(error.requestId, "request-7");
      assert.deepEqual(error.details, { detail: { code: "write_not_committed" } });
      return true;
    },
  );
  assert.equal(calls, 1);
});

test("memory graph methods encode paths and keep verbatim evidence behind an explicit call", async () => {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const client = new TMCRAClient({
    baseUrl: "https://api.tmcra.com",
    fetch: async (input, init) => {
      calls.push({ url: String(input), init: init ?? {} });
      const url = String(input);
      if (url.includes("/evidence?")) {
        return response(200, {
          schema_version: "tmcra.memory-graph.1",
          scope_name: "scope/a",
          snapshot_id: "snap-1",
          memory_id: "node/a",
          items: [],
          page: { limit: 10, offset: 0, truncated: false, next_cursor: null, returned_neighbors: null },
        });
      }
      return response(200, {
        schema_version: "tmcra.memory-graph.1",
        scope_name: "scope/a",
        snapshot_id: "snap-1",
        view: "overview",
        requested_layers: ["slow"],
        resolved_layers: ["slow"],
        fallback_layer: null,
        nodes: [],
        edges: [],
        counts: { nodes: 0, edges: 0, slow: 0, fast: 0, source: 0 },
        page: { limit: 12, offset: 0, truncated: false, next_cursor: null, returned_neighbors: null },
        root_id: null,
        depth: null,
        selected_memory_ids: [],
        missing_memory_ids: [],
      });
    },
  });

  await client.memoryGraph("scope/a", { layers: ["slow", "fast"], limit: 12, query: "launch plan" });
  await client.memoryGraphNeighbors("scope/a", "node/a", { depth: 2, limit: 20 });
  await client.memoryGraphEvidence("scope/a", "node/a");
  await client.traceMemoryRecall("scope/a", { query: "what changed?", query_time: new Date("2026-07-15T00:00:00Z") });

  assert.equal(calls[0]?.url, "https://api.tmcra.com/v1/scopes/scope%2Fa/memory-graph?layers=slow%2Cfast&limit=12&query=launch+plan");
  assert.match(calls[1]?.url ?? "", /nodes\/node%2Fa\/neighbors\?depth=2&layers=slow%2Cfast%2Csource&limit=20$/);
  assert.match(calls[2]?.url ?? "", /nodes\/node%2Fa\/evidence\?limit=10$/);
  assert.equal(calls[3]?.init.method, "POST");
  assert.deepEqual(JSON.parse(String(calls[3]?.init.body)), {
    query: "what changed?",
    query_time: "2026-07-15T00:00:00.000Z",
  });
});

test("waitForJob polls until terminal and can raise for failed jobs", async () => {
  let calls = 0;
  const client = new TMCRAClient({
    baseUrl: "https://api.tmcra.com",
    fetch: async () => {
      calls += 1;
      return response(200, job(calls === 1 ? "running" : "failed"));
    },
  });
  await assert.rejects(
    client.waitForJob("job-1", { pollIntervalMs: 0, throwOnFailure: true }),
    (error: unknown) => error instanceof TMCRAJobFailedError,
  );
  assert.equal(calls, 2);
});

test("waitForJob enforces an overall deadline", async () => {
  const client = new TMCRAClient({
    baseUrl: "https://api.tmcra.com",
    fetch: async () => response(200, job("running")),
  });
  await assert.rejects(
    client.waitForJob("job-1", { timeoutMs: 0, pollIntervalMs: 0 }),
    (error: unknown) => error instanceof TMCRAJobPollingTimeoutError,
  );
});

test("request timeout is surfaced as a distinct error", async () => {
  const client = new TMCRAClient({
    baseUrl: "https://api.tmcra.com",
    defaultTimeoutMs: 1,
    fetch: (_input, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
    }),
  });
  await assert.rejects(client.healthz(), (error: unknown) => error instanceof TMCRATimeoutError);
});

test("commercial contract methods keep batch idempotency and destructive confirmation", async () => {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const client = new TMCRAClient({
    baseUrl: "https://api.tmcra.com",
    fetch: async (input, init) => {
      calls.push({ url: String(input), init: init ?? {} });
      const url = String(input);
      if (url.endsWith("/ingest/batch")) {
        return response(202, { scope_name: "scope-1", jobs: [job("pending")] });
      }
      if (url.endsWith("/exports/export-1")) {
        return new Response(new Uint8Array([0x50, 0x4b, 3, 4]), { status: 200 });
      }
      if (url.endsWith("/scope-1") && init?.method === "DELETE") {
        return response(202, job("pending"));
      }
      if (url.endsWith("/v1/access-tokens")) {
        return response(201, {
          token_id: "token-1", tenant_id: "tenant-1", access_token: "secret",
          permissions: ["memory:read"], scope_names: [], scope_prefixes: ["scope-"],
          label: "Codex", subject: null, created_by_key_id: "key-1",
          created_at: 1, expires_at: 2, revoked_at: null, last_used_at: null,
        });
      }
      throw new Error(`unexpected request: ${url}`);
    },
  });

  await client.bulkIngest("scope-1", {
    items: [{
      idempotency_key: "batch-item-1",
      session_id: "session-1",
      messages: [{ message_id: "m1", role: "user", content: "hi", timestamp: new Date("2026-07-16T00:00:00Z") }],
    }],
  });
  const archive = await client.downloadScopeExport("scope-1", "export-1");
  await client.deleteScope("scope-1", { idempotencyKey: "delete-scope-1" });
  await client.issueAccessToken({
    label: "Codex",
    permissions: ["memory:read"],
    scope_prefixes: ["scope-"],
    expires_in_seconds: 3600,
  }, { idempotencyKey: "issue-token-1" });

  assert.equal(new Headers(calls[0]?.init.headers).get("idempotency-key"), "batch-item-1");
  assert.equal(archive[0], 0x50);
  assert.equal(new Headers(calls[2]?.init.headers).get("x-tmcra-confirm-scope"), "scope-1");
  const tokenCall = calls.find((call) => call.url.endsWith("/v1/access-tokens"));
  assert.equal(new Headers(tokenCall?.init.headers).get("idempotency-key"), "issue-token-1");
});

test("discovery session and quota cover the public control plane", async () => {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const client = new TMCRAClient({
    apiKey: "secret",
    fetch: async (input, init) => {
      const url = String(input);
      calls.push({ url, init: init ?? {} });
      if (url.endsWith("/v1/session")) return response(200, {
        ok: true,
        authenticated: true,
        service: { name: "tmcra-memory", version: "1", capabilities: ["recall"] },
        credential: {
          type: "scope_token", tenant_id: "tenant-1", principal: "token:1",
          permissions: ["memory:read"], subject: "user-1", expires_at: 2,
          scope_restrictions: { unrestricted: false, names: ["scope-1"], prefixes: [] },
        },
      });
      if (url.includes("/v1/scopes?") && !url.includes("/summary")) return response(200, [{
        scope_name: "scope-1", created_at: 1, last_seen_at: 2,
        session_count: 1, ingest_request_count: 2, recall_request_count: 3,
        message_count: 4, last_ingest_at: 2, last_recall_at: 2,
      }]);
      if (url.endsWith("/summary")) return response(200, {
        scope: {
          scope_name: "scope-1", created_at: 1, last_seen_at: 2,
          session_count: 1, ingest_request_count: 2, recall_request_count: 3,
          message_count: 4, last_ingest_at: null, last_recall_at: null,
        },
        sessions: [{ session_id: "session-1", created_at: 1, last_ingest_at: 2, ingest_request_count: 2, message_count: 4 }],
      });
      if (url.endsWith("/v1/billing/profile")) return response(200, {
        tenant_id: "tenant-1",
        subject: "user-1",
        consumer_principal: "subject:user-1",
        quota_principal: "billing:team-1:period-1",
        membership: { group_id: "team-1", role: "member", status: "active" },
        quota: {
          tenant_id: "tenant-1", principal: "billing:team-1:period-1", plan: "team",
          plan_version: "2026-08",
          billing_group: {
            group_id: "team-1", display_name: "Team 1", status: "active",
            period_id: "period-1", period_status: "active", billing_interval: "monthly",
            starts_at: 1, ends_at: 2, max_members: 5, currency: "CNY", price_minor_units: 9900,
          },
          ingest_raw_tokens: { used: 10, limit: 100, remaining: 90 },
          recall_requests: { used: 2, limit: 20, remaining: 18 },
          member_usage: { "subject:user-1": { ingest_raw_tokens: 10, recall_requests: 2 } },
        },
      });
      if (url.includes("/v1/usage/")) return response(200, {
        tenant_id: "tenant-1", principal: "user-1", plan: "pilot",
        plan_version: null, billing_group: null, member_usage: {},
        ingest_raw_tokens: { used: 10, limit: 100, remaining: 90 },
        recall_requests: { used: 2, limit: 20, remaining: 18 },
      });
      throw new Error(`unexpected request: ${url}`);
    },
  });

  assert.equal((await client.authenticatedSession()).credential.subject, "user-1");
  assert.equal((await client.listScopes({ prefix: "scope-", limit: 10 }))[0]?.message_count, 4);
  assert.equal((await client.scopeSummary("scope-1")).sessions[0]?.session_id, "session-1");
  assert.equal((await client.quota()).recall_requests.remaining, 18);
  assert.equal((await client.billingProfile()).quota.billing_group?.group_id, "team-1");
  await client.setEntitlement("user-1", { ingest_raw_tokens: 100, recall_requests: 20 });
  await client.setQuotaEntitlement("user-1", { ingest_raw_tokens: null, recall_requests: 20 });

  assert.ok(calls.every((call) => call.url.startsWith("https://api.tmcra.com/")));
  assert.match(calls[1]?.url ?? "", /limit=10/);
  assert.match(calls[1]?.url ?? "", /prefix=scope-/);
  assert.deepEqual(JSON.parse(String(calls[5]?.init.body)), { ingest_raw_tokens: 100, recall_requests: 20 });
  assert.match(calls[6]?.url ?? "", /subject=user-1/);
});
