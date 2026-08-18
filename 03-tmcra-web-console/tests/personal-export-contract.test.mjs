import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  inspectOwnedExportJob,
  requireExportId,
  requireJobId,
} from "../app/api/personal/export/export-contract.mjs";

const root = new URL("../", import.meta.url);
const source = (path) => readFile(new URL(path, root), "utf8");

const JOB_ID = "a".repeat(32);
const EXPORT_ID = `exp_${"b".repeat(32)}`;
const GLOBAL_SCOPE = "personal-account-global";

function exportJob(overrides = {}) {
  return {
    job_id: JOB_ID,
    scope_name: GLOBAL_SCOPE,
    job_type: "export_scope",
    status: "succeeded",
    updated_at: 90,
    result: {
      export_id: EXPORT_ID,
      expires_at: 200,
      size_bytes: 4096,
    },
    ...overrides,
  };
}

test("export contract accepts only strict identifiers and the signed-in account global Scope", () => {
  assert.equal(requireJobId(JOB_ID), JOB_ID);
  assert.equal(requireExportId(EXPORT_ID), EXPORT_ID);
  assert.throws(() => requireJobId(`job_${JOB_ID}`), (error) => error.code === "invalid_job_id");
  assert.throws(() => requireExportId("exp_../other"), (error) => error.code === "invalid_export_id");

  assert.deepEqual(inspectOwnedExportJob(exportJob(), GLOBAL_SCOPE, EXPORT_ID, 100), {
    jobId: JOB_ID,
    exportId: EXPORT_ID,
    status: "ready",
    expiresAt: 200,
    sizeBytes: 4096,
    updatedAt: 90,
  });
  assert.throws(
    () => inspectOwnedExportJob(exportJob({ scope_name: "personal-other-global" }), GLOBAL_SCOPE, undefined, 100),
    (error) => error.code === "export_not_found",
  );
  assert.throws(
    () => inspectOwnedExportJob(exportJob(), GLOBAL_SCOPE, `exp_${"c".repeat(32)}`, 100),
    (error) => error.code === "export_not_found",
  );
});

test("export contract reports pending, failed, and expired states without inventing a download", () => {
  assert.deepEqual(
    inspectOwnedExportJob(exportJob({ status: "pending", result: null }), GLOBAL_SCOPE, undefined, 100),
    { jobId: JOB_ID, status: "pending", updatedAt: 90 },
  );
  assert.deepEqual(
    inspectOwnedExportJob(exportJob({ status: "failed", result: null }), GLOBAL_SCOPE, undefined, 100),
    { jobId: JOB_ID, status: "failed", updatedAt: 90 },
  );
  assert.deepEqual(
    inspectOwnedExportJob(exportJob({ result: { export_id: EXPORT_ID, expires_at: 99 } }), GLOBAL_SCOPE, undefined, 100),
    { jobId: JOB_ID, exportId: EXPORT_ID, status: "expired", expiresAt: 99 },
  );
});

test("export status and download BFF re-authenticate, bind the job to the personal Scope, and keep the control key server-side", async () => {
  const [statusRoute, downloadRoute, server, client] = await Promise.all([
    source("app/api/personal/export/status/route.ts"),
    source("app/api/personal/export/download/route.ts"),
    source("app/api/personal/export/server.ts"),
    source("app/personal/PersonalConsoleClient.tsx"),
  ]);

  for (const route of [statusRoute, downloadRoute]) {
    assert.match(route, /requirePersonalAccess\(\)/);
    assert.match(route, /`\$\{access\.space\.scopeName\}-global`/);
    assert.match(route, /inspectOwnedExportJob/);
    assert.doesNotMatch(route, /TMCRA_MEMORY_API_CONTROL_KEY/);
  }
  assert.match(downloadRoute, /inspectOwnedExportJob\(job, globalScope, exportId\)/);
  assert.match(downloadRoute, /Content-Type", "application\/zip"/);
  assert.match(downloadRoute, /new Response\(upstream\.body/);
  assert.match(server, /TMCRA_MEMORY_API_CONTROL_KEY/);
  assert.match(server, /Authorization: `Bearer \$\{binding\.apiKey\}`/);
  assert.doesNotMatch(client, /TMCRA_MEMORY_API_CONTROL_KEY|Authorization:\s*`Bearer/);
  assert.match(client, /\/api\/personal\/export\/status\?job_id=/);
  assert.match(client, /Download ZIP/);
  assert.doesNotMatch(client, /Export queued \/ \$\{jobId\}/);
});

test("personal overview has a fixed upstream request count and Sessions load one selected Scope at a time", async () => {
  const [personalRoute, sessionsRoute, client] = await Promise.all([
    source("app/api/personal/route.ts"),
    source("app/api/personal/sessions/route.ts"),
    source("app/personal/PersonalConsoleClient.tsx"),
  ]);

  assert.doesNotMatch(personalRoute, /MAX_SUMMARY_REQUESTS|catalog\.slice\([^)]*\)\.map/);
  assert.doesNotMatch(personalRoute, /\/summary/);
  assert.match(personalRoute, /sessionTotal:\s*catalog\.reduce/);
  assert.match(sessionsRoute, /scopeName\.startsWith\(`\$\{access\.space\.scopeName\}-`\)/);
  assert.equal((sessionsRoute.match(/\/summary/g) ?? []).length, 1);
  assert.match(client, /\/api\/personal\/sessions\?scope=/);
  assert.match(client, /Loading Sessions for this Scope/);
});
