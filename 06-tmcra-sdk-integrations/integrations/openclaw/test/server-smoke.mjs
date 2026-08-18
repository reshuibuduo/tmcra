import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";
import plugin, { validateConfig } from "../index.js";
import { deriveIdentity } from "../ids.js";

const required = [
  "TMCRA_BASE_URL",
  "TMCRA_TENANT_ID",
  "TMCRA_API_KEY",
  "TMCRA_IDENTITY_SECRET",
  "TMCRA_QUEUE_PATH",
];
for (const name of required) {
  if (!process.env[name]?.trim()) throw new Error(`${name} is required`);
}

const originalFetch = globalThis.fetch;
const calls = [];
globalThis.fetch = async (input, init) => {
  const response = await originalFetch(input, init);
  let body = {};
  try {
    body = await response.clone().json();
  } catch {
    body = {};
  }
  calls.push({ url: String(input), status: response.status, body });
  return response;
};

const hooks = new Map();
plugin.register({
  pluginConfig: {},
  logger: { warn() {}, info() {}, debug() {} },
  on(name, handler) {
    hooks.set(name, handler);
  },
});

const runId = randomUUID();
const marker = `openclaw-native-${runId.slice(0, 12)}`;
const context = {
  sessionKey: `server-smoke-session-${runId}`,
  senderId: `server-smoke-user-${runId}`,
  channel: "server-smoke",
  agentId: "tmcra-launch",
};
const config = validateConfig({}, process.env);
const identity = deriveIdentity({ config, context, runId });

await hooks.get("before_prompt_build")(
  { prompt: `Remember my native verification code ${marker}.`, runId },
  context,
);
await hooks.get("agent_end")(
  {
    runId,
    success: true,
    messages: [{ role: "assistant", content: `Stored native verification code ${marker}.` }],
  },
  context,
);

const ingestCall = [...calls].reverse().find((call) => call.url.includes("/ingest"));
assert.equal(ingestCall?.status, 202);
assert.ok(ingestCall?.body?.job_id);
const jobId = ingestCall.body.job_id;
const deadline = Date.now() + Number(process.env.TMCRA_SMOKE_TIMEOUT_MS || 1_800_000);
let job;
while (Date.now() < deadline) {
  const response = await originalFetch(
    `${config.baseUrl}/v1/jobs/${encodeURIComponent(jobId)}`,
    { headers: { Authorization: `Bearer ${config.apiKey}`, Accept: "application/json" } },
  );
  job = await response.json();
  if (job.status === "succeeded") break;
  if (["failed", "cancelled"].includes(job.status)) {
    throw new Error(`OpenClaw ingest job ended as ${job.status}`);
  }
  await new Promise((resolve) => setTimeout(resolve, 1500));
}
assert.equal(job?.status, "succeeded");

const recalled = await hooks.get("before_prompt_build")(
  { prompt: "What is my native verification code?", runId: randomUUID() },
  context,
);
assert.ok(recalled?.prependSystemContext?.includes(marker));

const report = {
  schema_version: "tmcra.openclaw-server-smoke.1",
  status: "passed",
  scope: identity.scopeName,
  job_id: jobId,
  registered_hooks: [...hooks.keys()].sort(),
  recalled_marker: true,
};
if (process.env.TMCRA_SMOKE_REPORT) {
  await writeFile(
    process.env.TMCRA_SMOKE_REPORT,
    `${JSON.stringify(report, null, 2)}\n`,
    { mode: 0o600 },
  );
}
console.log(JSON.stringify(report));
