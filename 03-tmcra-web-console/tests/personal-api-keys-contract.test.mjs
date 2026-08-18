import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { DatabaseSync } from "node:sqlite";

const root = new URL("../", import.meta.url);

function source(path) {
  return readFile(new URL(path, root), "utf8");
}

test("personal API key schema persists metadata only", async () => {
  const consoleSource = await source("db/console.ts");
  const match = consoleSource.match(
    /`CREATE TABLE IF NOT EXISTS personal_api_keys \(([\s\S]*?)\)`,/u,
  );
  assert.ok(match, "personal_api_keys schema statement must exist");
  const tableBody = match[1];
  assert.doesNotMatch(tableBody, /access_token|\bsecret\b|cipher|hash/iu);

  const database = new DatabaseSync(":memory:");
  database.exec("PRAGMA foreign_keys = ON");
  database.exec("CREATE TABLE personal_memory_spaces (id TEXT PRIMARY KEY)");
  database.exec(`CREATE TABLE personal_api_keys (${tableBody})`);
  const columns = database
    .prepare("PRAGMA table_info(personal_api_keys)")
    .all()
    .map((column) => column.name);
  assert.deepEqual(columns, [
    "personal_space_id",
    "token_id",
    "token_prefix",
    "permissions_json",
    "name",
    "status",
    "expires_at",
    "created_at",
    "revoked_at",
  ]);

  database.prepare("INSERT INTO personal_memory_spaces (id) VALUES ('psp_test')").run();
  database.prepare(
    `INSERT INTO personal_api_keys (
       personal_space_id, token_id, token_prefix, permissions_json,
       name, status, expires_at
     ) VALUES (?, ?, ?, ?, ?, 'active', ?)`,
  ).run(
    "psp_test",
    "token_test",
    "tmcra_st_token_test",
    '["memory:read","memory:write","memory:feedback"]',
    "Local development",
    Date.now() + 86_400_000,
  );
  const persisted = JSON.stringify(database.prepare("SELECT * FROM personal_api_keys").get());
  assert.doesNotMatch(persisted, /tmcra_st_token_test\./u);
  assert.throws(
    () => database.prepare(
      `INSERT INTO personal_api_keys (
         personal_space_id, token_id, token_prefix, permissions_json,
         name, status, expires_at
       ) VALUES ('psp_test', 'bad_json', 'prefix', 'not-json', 'Bad', 'active', 1)`,
    ).run(),
    /CHECK constraint failed/u,
  );
  database.close();
});

test("personal API key issuance is least-privilege, direct, and one-time-secret", async () => {
  const [service, consoleSource] = await Promise.all([
    source("app/api/personal/api-keys.ts"),
    source("db/console.ts"),
  ]);
  assert.match(service, /createMemoryControlClient/u);
  assert.match(service, /subject:\s*access\.space\.id/u);
  assert.match(service, /scope_names:\s*\[\]/u);
  assert.match(service, /scope_prefixes:\s*\[`\$\{access\.space\.scopeName\}-`\]/u);
  for (const permission of ["memory:read", "memory:write", "memory:feedback"]) {
    assert.match(service, new RegExp(`"${permission}"`, "u"));
  }
  assert.doesNotMatch(service, /provisional_delivery_seconds/u);
  assert.match(service, /secret:\s*issued\.accessToken/u);
  assert.match(service, /await storePersonalApiKey\(\{/u);
  assert.match(consoleSource, /permissions_json AS permissionsJson/u);
  assert.doesNotMatch(consoleSource.slice(
    consoleSource.indexOf("export async function listPersonalApiKeys"),
    consoleSource.indexOf("export async function getConsoleSnapshot"),
  ), /accessToken|access_token|\bsecret\b/u);
});

test("personal API key revoke is upstream-first, retry-safe, and idempotent", async () => {
  const [service, upstream] = await Promise.all([
    source("app/api/personal/api-keys.ts"),
    source("app/api/device/v1/upstream-client.mjs"),
  ]);
  const revokeCall = service.indexOf("await memoryControlClient().revoke(tokenId, requestId)");
  const localUpdate = service.indexOf("await markPersonalApiKeyRevoked(access.space.id, tokenId, revokedAt)");
  assert.ok(revokeCall >= 0, "revoke must call Memory Control");
  assert.ok(localUpdate > revokeCall, "local status must change only after upstream revoke succeeds");
  assert.match(upstream, /if \(error\?\.status === 404\) return \{ token_id: tokenId, revoked: true \}/u);
  assert.match(service, /personal_api_key_not_found/u);
  assert.match(service, /personal_api_key_rollback_failed/u);
  assert.match(service, /await control\.revoke\(issued\.tokenId, `\$\{requestId\}-rollback`\)/u);
});

test("personal route returns real subject costs and degrades without fabricating billing", async () => {
  const route = await source("app/api/personal/route.ts");
  const getSnapshot = route.slice(route.indexOf("export async function GET"), route.indexOf("export async function POST"));
  assert.match(getSnapshot, /space:\s*\{[\s\S]{0,160}scopeName:\s*access\.space\.scopeName/u);
  assert.doesNotMatch(getSnapshot, /\bsecret\b|accessToken|access_token/u);
  assert.match(route, /action === "api_key\.create"/u);
  assert.match(route, /action === "api_key\.revoke"/u);
  assert.match(route, /personalApiKeyRequestId\(request, access\.space\.id, requestId\)/u);
  assert.match(route, /new TextEncoder\(\)\.encode\(`\$\{personalSpaceId\}\\u0000\$\{key\}`\)/u);
  assert.match(route, /apiKeysPromise = listPersonalApiKeys\(access\.space\.id\)/u);
  assert.match(route, /`\/v1\/usage\/costs\?scope_prefix=\$\{encodeURIComponent\(`\$\{access\.space\.scopeName\}-`\)\}`/u);
  assert.doesNotMatch(route, /X-TMCRA-On-Behalf-Of-Subject/u);
  assert.match(route, /billingResult\.ok[\s\S]{0,120}normalizeBilling/u);
  assert.match(route, /\.\.\.\(billing \? \[\] : \["billing"\]\)/u);
  assert.match(route, /serviceStatus: serviceErrors\.length \? "partial" : "ready"/u);
  assert.match(route, /function responseHeaders\(requestId: string\)[\s\S]*?"X-Request-ID": requestId/u);
  assert.match(route, /TMCRA personal memory upstream fetch failed[\s\S]*?requestId/u);
  assert.match(route, /status: error\.status, headers: responseHeaders\(requestId\)/u);
  assert.match(route, /usageCosts: costs/u);
  assert.match(route, /payment: \{ status: "unavailable" \}/u);
  assert.match(route, /invoices: \{ status: "unavailable" \}/u);
  assert.match(route, /:\s*"pilot"/u);
  assert.doesNotMatch(route, /estimateBilling|estimatedCost|fallbackCost/iu);
});

test("personal API key validation exposes explicit error contracts", async () => {
  const service = await source("app/api/personal/api-keys.ts");
  for (const code of [
    "invalid_api_key_permissions",
    "invalid_api_key_expiry",
    "invalid_token_id",
    "personal_api_key_invalid_response",
    "memory_control_unavailable",
  ]) {
    assert.match(service, new RegExp(`"${code}"`, "u"));
  }
  assert.match(service, /permissions must be a non-empty array/u);
  assert.match(service, /expiresInSeconds must be between 3600 and 31536000/u);
});
