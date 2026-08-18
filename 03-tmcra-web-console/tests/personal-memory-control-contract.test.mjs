import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  deletionStatusRequest,
  idempotencyKey,
  memoryDeletionRequest,
  ownedScope,
  sessionDeletionRequest,
  usageQuery,
} from "../app/api/personal/memory-control/contract.mjs";

const root = new URL("../", import.meta.url);
const source = (path) => readFile(new URL(path, root), "utf8");

test("personal memory deletion selectors stay inside the signed-in namespace", () => {
  assert.equal(ownedScope("personal-owner-project-a", "personal-owner"), "personal-owner-project-a");
  assert.throws(
    () => ownedScope("personal-other-project-a", "personal-owner"),
    (error) => error.code === "memory_scope_forbidden",
  );
  assert.deepEqual(memoryDeletionRequest({ memoryIds: ["source-a", "fast-a"] }), {
    memoryIds: ["source-a", "fast-a"],
  });
  assert.throws(
    () => memoryDeletionRequest({ memoryIds: ["source-a", "source-a"] }),
    (error) => error.code === "invalid_memory_ids",
  );
  assert.deepEqual(sessionDeletionRequest({ sessionId: "codex-history-abc" }), {
    sessionId: "codex-history-abc",
  });
  assert.equal(deletionStatusRequest(`del_${"a".repeat(32)}`), `del_${"a".repeat(32)}`);
  assert.equal(idempotencyKey("delete-memory-001"), "delete-memory-001");
});

test("personal usage query exposes supported ledger dimensions", () => {
  const parameters = new URLSearchParams({ scope: "personal-owner-global", from: "100", to: "200", groupBy: "provider" });
  assert.deepEqual(usageQuery(parameters, "personal-owner"), {
    scopeName: "personal-owner-global",
    fromTimestamp: 100,
    toTimestamp: 200,
    groupBy: "provider",
  });
  for (const groupBy of ["agent", "integration", "platform", "attribution_source"]) {
    assert.equal(usageQuery(new URLSearchParams({ groupBy }), "personal-owner").groupBy, groupBy);
  }
  assert.throws(
    () => usageQuery(new URLSearchParams({ groupBy: "invented" }), "personal-owner"),
    (error) => error.code === "invalid_usage_group",
  );
  assert.throws(
    () => usageQuery(new URLSearchParams({ from: "200", to: "100" }), "personal-owner"),
    (error) => error.code === "invalid_time_window",
  );
});

test("personal web console reads server ledger totals without estimating missing usage", async () => {
  const [consoleSource, ledgerSource, route] = await Promise.all([
    source("app/personal/PersonalConsoleClient.tsx"),
    source("app/personal/PersonalUsageLedger.tsx"),
    source("app/api/personal/memory-control/route.ts"),
  ]);
  assert.match(consoleSource, /<PersonalUsageLedger language=\{language\} t=\{t\} \/>/u);
  assert.match(ledgerSource, /\/api\/personal\/memory-control\?\$\{parameters\}/u);
  for (const dimension of ["platform", "integration", "agent", "attribution_source"]) {
    assert.match(ledgerSource, new RegExp(`id: "${dimension}"`, "u"));
  }
  assert.match(ledgerSource, /complete_for_registered_calls/u);
  assert.match(ledgerSource, /uncertain_cost_call_count/u);
  assert.match(ledgerSource, /known_cost_cny/u);
  assert.match(ledgerSource, /typeof value === "number" && Number\.isFinite\(value\) && value >= 0/u);
  assert.doesNotMatch(ledgerSource, /Number\(value\)|parseFloat\(value\)/u);
  assert.match(route, /parameters\.set\("group_by", query\.groupBy\)/u);
});

test("personal web console exposes confirmed asynchronous Session deletion", async () => {
  const consoleSource = await source("app/personal/PersonalConsoleClient.tsx");
  assert.match(consoleSource, /action: "session\.delete"/u);
  assert.match(consoleSource, /personal-session-delete-\$\{crypto\.randomUUID\(\)\}/u);
  assert.match(consoleSource, /action=deletion/u);
  assert.match(consoleSource, /state === "completed"/u);
  assert.match(consoleSource, /Delete Session/u);
});

test("personal memory control BFF keeps the control key server-side and forwards confirmations", async () => {
  const [route, server, consoleSource] = await Promise.all([
    source("app/api/personal/memory-control/route.ts"),
    source("app/api/personal/export/server.ts"),
    source("app/personal/PersonalConsoleClient.tsx"),
  ]);
  assert.match(route, /requirePersonalAccess\(\)/u);
  assert.match(route, /ownedScope\(body\.scope, access\.space\.scopeName\)/u);
  assert.match(route, /"X-TMCRA-Confirm-Memory-Count"/u);
  assert.match(route, /"X-TMCRA-Confirm-Session"/u);
  assert.match(route, /\[ON_BEHALF_SUBJECT_HEADER\]: access\.space\.id/u);
  assert.match(route, /personalMemoryRequest/u);
  assert.doesNotMatch(route, /TMCRA_MEMORY_API_CONTROL_KEY/u);
  assert.match(server, /TMCRA_MEMORY_API_CONTROL_KEY/u);
  assert.doesNotMatch(consoleSource, /TMCRA_MEMORY_API_CONTROL_KEY|Authorization:\s*`Bearer/u);
});
