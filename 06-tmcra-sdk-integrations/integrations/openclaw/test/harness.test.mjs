import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

import plugin, {
  renderPromptContext,
  validateConfig,
} from "../index.js";
import { deriveIdentity } from "../ids.js";

const API_KEY = "<test-api-key>";
const IDENTITY_SECRET = "stable-test-identity-secret";

function response(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function setup(fetchImpl, queuePath) {
  const hooks = new Map();
  const logs = [];
  globalThis.fetch = fetchImpl;
  plugin.register({
    pluginConfig: {
      baseUrl: "https://tmcra.example.test",
      tenantId: "tenant-a",
      queuePath,
      drainIntervalMs: 10000,
    },
    logger: {
      warn(message) { logs.push(String(message)); },
      info() {},
      debug() {},
      error() {},
    },
    on(name, handler) { hooks.set(name, handler); },
  });
  return { hooks, logs };
}

test.beforeEach(() => {
  process.env.TMCRA_API_KEY = API_KEY;
  process.env.TMCRA_IDENTITY_SECRET = IDENTITY_SECRET;
});

test("validates server-only credentials and HTTPS configuration", () => {
  assert.throws(
    () => validateConfig({ baseUrl: "http://localhost", tenantId: "tenant-a" }, process.env),
    /HTTPS/,
  );
  assert.throws(
    () => validateConfig({ baseUrl: "https://example.test", tenantId: "tenant-a", apiKey: "bad" }, process.env),
    /credentials must be supplied/,
  );
  const config = validateConfig({ baseUrl: "https://example.test", tenantId: "tenant-a" }, process.env);
  assert.equal(config.apiKey, API_KEY);
});

test("derives stable opaque identities without raw OpenClaw identifiers", () => {
  const config = { identitySecret: IDENTITY_SECRET, scopeNamespace: "openclaw" };
  const input = { config, context: {
    sessionKey: "telegram:chat:42",
    senderId: "user-42",
    channel: "telegram",
    agentId: "main",
  }, runId: "run-1" };
  const first = deriveIdentity(input);
  const second = deriveIdentity(input);
  assert.deepEqual(first, second);
  assert.match(first.scopeName, /^ocw_scope_[0-9a-f]{40}$/);
  assert.doesNotMatch(JSON.stringify(first), /telegram|chat:42|user-42|run-1/);
});

test("recalls into system context and ingests the completed turn", async () => {
  const root = await mkdtemp(join(tmpdir(), "tmcra-openclaw-"));
  const queuePath = join(root, "pending.json");
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init, body: JSON.parse(init.body) });
    if (url.includes("/recall")) return response(200, { prompt_evidence: { content: "User likes concise answers." } });
    return response(202, { job_id: "job-1", status: "pending" });
  };
  const { hooks } = await setup(fetchImpl, queuePath);
  const context = { sessionKey: "session-42", senderId: "user-42", channel: "test", agentId: "main" };
  const injected = await hooks.get("before_prompt_build")({ prompt: "How should I answer?", runId: "run-1" }, context);
  assert.ok(injected.prependSystemContext.includes("<tmcra-memory-context>"));
  assert.ok(injected.prependSystemContext.includes("User likes concise answers."));
  assert.match(injected.prependSystemContext, /not a user message/);
  assert.ok(!injected.prependSystemContext.includes("TMCRA_API_KEY"));

  await hooks.get("agent_end")({
    runId: "run-1",
    messages: [{ role: "assistant", content: "Answer briefly." }],
  }, context);
  const ingest = calls.find((call) => call.url.includes("/ingest"));
  assert.ok(ingest);
  assert.equal(ingest.init.headers.Authorization, `Bearer ${API_KEY}`);
  assert.equal(ingest.body.messages.length, 2);
  assert.deepEqual(ingest.body.messages.map((message) => message.role), ["user", "assistant"]);
  assert.match(ingest.init.headers["Idempotency-Key"], /^ocw_ingest_/);
  assert.ok(!JSON.stringify(ingest.body).includes("session-42"));
});

test("persists failed ingest and drains it with the same idempotency key", async () => {
  const root = await mkdtemp(join(tmpdir(), "tmcra-openclaw-"));
  const queuePath = join(root, "pending.json");
  let fail = true;
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init, body: init.body && JSON.parse(init.body) });
    if (url.includes("/recall")) return response(200, { prompt_evidence: null });
    if (fail) throw new Error("offline");
    return response(202, { job_id: "job-retry", status: "pending" });
  };
  const first = await setup(fetchImpl, queuePath);
  const context = { sessionKey: "session-retry", senderId: "user-retry", channel: "test" };
  await first.hooks.get("before_prompt_build")({ prompt: "Remember this", runId: "run-retry" }, context);
  await first.hooks.get("agent_end")({ runId: "run-retry", messages: [{ role: "assistant", content: "Stored." }] }, context);
  const queued = JSON.parse(await readFile(queuePath, "utf8"));
  assert.equal(queued.items.length, 1);
  const originalKey = queued.items[0].idempotencyKey;
  assert.ok(!JSON.stringify(queued).includes(API_KEY));

  fail = false;
  await first.hooks.get("gateway_start")();
  const after = JSON.parse(await readFile(queuePath, "utf8"));
  assert.equal(after.items.length, 0);
  const retry = calls.at(-1);
  assert.equal(retry.init.headers["Idempotency-Key"], originalKey);
});

test("bounds and labels prompt evidence", () => {
  const rendered = renderPromptContext({ content: "x".repeat(5000) }, 1000);
  assert.ok(rendered.length < 1200);
  assert.match(rendered, /untrusted data/);
  assert.match(rendered, /truncated/);
});
