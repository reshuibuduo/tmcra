import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import { TMCRAClient } from "../dist/index.js";

const required = [
  "TMCRA_BASE_URL",
  "TMCRA_API_KEY",
  "TMCRA_DEFAULT_SCOPE",
  "TMCRA_SMOKE_EXPECTED_TEXT",
];
for (const name of required) {
  if (!process.env[name]?.trim()) throw new Error(`${name} is required`);
}

const client = new TMCRAClient({
  baseUrl: process.env.TMCRA_BASE_URL,
  apiKey: process.env.TMCRA_API_KEY,
  defaultTimeoutMs: 60_000,
});
const health = await client.healthz();
const readiness = await client.readyz();
const recalled = await client.recall(process.env.TMCRA_DEFAULT_SCOPE, {
  query: "What is my launch verification code?",
  evidence_mode: "auto",
  max_windows: 8,
});
assert.equal(health.status, "ok");
assert.equal(readiness.status, "ready");
assert.ok(
  recalled.prompt_evidence.content.includes(process.env.TMCRA_SMOKE_EXPECTED_TEXT),
);

const report = {
  schema_version: "tmcra.typescript-server-smoke.1",
  status: "passed",
  health: health.status,
  readiness: readiness.status,
  selected_evidence_mode: recalled.evidence_route.selected,
  recalled_expected_text: true,
};
if (process.env.TMCRA_SMOKE_REPORT) {
  await writeFile(
    process.env.TMCRA_SMOKE_REPORT,
    `${JSON.stringify(report, null, 2)}\n`,
    { mode: 0o600 },
  );
}
console.log(JSON.stringify(report));
