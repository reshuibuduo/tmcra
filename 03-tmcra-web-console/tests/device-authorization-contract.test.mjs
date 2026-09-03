import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { DatabaseSync } from "node:sqlite";

const root = new URL("../", import.meta.url);
const migrationUrls = [
  new URL("drizzle/0000_abandoned_justice.sql", root),
  new URL("drizzle/0004_living_marvex.sql", root),
  new URL("drizzle/0005_clean_loa.sql", root),
  new URL("drizzle/0006_volatile_xavin.sql", root),
  new URL("drizzle/0007_device_token_delivery.sql", root),
  new URL("drizzle/0009_parallel_owl.sql", root),
  new URL("drizzle/0010_deepseek_harness.sql", root),
];

function source(path) {
  return readFile(new URL(path, root), "utf8");
}

function applyMigration(database, url) {
  const migration = readFileSync(url, "utf8");
  for (const statement of migration.split("--> statement-breakpoint")) {
    if (statement.trim()) database.exec(statement);
  }
}

test("device authorization migration stores hashes, metadata, and no plaintext credentials", () => {
  const database = new DatabaseSync(":memory:");
  database.exec("PRAGMA foreign_keys = ON");
  for (const migration of migrationUrls) applyMigration(database, migration);

  const authorizationColumns = database
    .prepare("PRAGMA table_info(device_authorizations)")
    .all()
    .map((column) => column.name);
  assert.ok(authorizationColumns.includes("device_code_hash"));
  assert.ok(authorizationColumns.includes("user_code_hash"));
  assert.ok(authorizationColumns.includes("token_ciphertext"));
  assert.ok(authorizationColumns.includes("token_iv"));
  assert.ok(authorizationColumns.includes("source_hash"));
  assert.ok(authorizationColumns.includes("issuance_request_id"));
  assert.ok(authorizationColumns.includes("delivery_receipt_hash"));
  assert.ok(authorizationColumns.includes("provider"));
  assert.equal(authorizationColumns.includes("device_code"), false);
  assert.equal(authorizationColumns.includes("user_code"), false);
  assert.equal(authorizationColumns.includes("access_token"), false);

  const connectionColumns = database
    .prepare("PRAGMA table_info(device_connections)")
    .all()
    .map((column) => column.name);
  assert.ok(connectionColumns.includes("token_id"));
  assert.ok(connectionColumns.includes("token_prefix"));
  assert.ok(connectionColumns.includes("provider"));
  assert.equal(connectionColumns.includes("access_token"), false);
  assert.equal(connectionColumns.includes("secret"), false);
  assert.ok(database.prepare("PRAGMA table_info(device_flow_rate_limits)").all().length > 0);
  assert.ok(database.prepare("PRAGMA table_info(device_revocation_outbox)").all().length > 0);

  const now = Date.now();
  database.prepare(
    `INSERT INTO users (id, email_normalized, email_display, display_name)
     VALUES ('usr_test', 'test@example.com', 'test@example.com', 'Test User')`,
  ).run();
  database.prepare(
    `INSERT INTO account_profiles (user_id, account_type, status, selected_at)
     VALUES ('usr_test', 'personal', 'active', ?)`,
  ).run(now);
  database.prepare(
    `INSERT INTO personal_memory_spaces
      (id, user_id, scope_name, display_name, status)
     VALUES ('psp_test', 'usr_test', 'personal-deadbeef', 'Personal Memory', 'active')`,
  ).run();

  const rawDeviceCode = "d".repeat(43);
  const rawUserCode = "ABCD2345";
  database.prepare(
    `INSERT INTO device_authorizations (
       id, device_code_hash, user_code_hash, code_challenge, client_name,
       source_hash, status, interval_seconds, token_ciphertext, token_iv,
       created_at, updated_at, expires_at, approved_by_user_id, personal_space_id
     ) VALUES (
       'dva_test', 'device_hash_value', 'user_hash_value', 'challenge_hash_value',
       'Codex', 'source_hash_value', 'approved', 5, NULL, NULL, ?, ?, ?,
       'usr_test', 'psp_test'
     )`,
  ).run(now, now, now + 600_000);
  database.prepare(
    `INSERT INTO device_connections (
       id, authorization_id, user_id, personal_space_id, provider, display_name,
       token_id, token_prefix, scope_prefix, permissions_json, status,
       token_expires_at, created_at, updated_at
     ) VALUES (
       'dvc_test', 'dva_test', 'usr_test', 'psp_test', 'codex', 'Codex',
       'token123', 'tmcra_st_token123', 'personal-deadbeef-',
       '["memory:read","memory:write","memory:feedback"]', 'active', ?, ?, ?
     )`,
  ).run(now + 31_536_000_000, now, now);

  const persisted = JSON.stringify({
    authorization: database.prepare("SELECT * FROM device_authorizations").get(),
    connection: database.prepare("SELECT * FROM device_connections").get(),
  });
  assert.doesNotMatch(persisted, new RegExp(rawDeviceCode));
  assert.doesNotMatch(persisted, new RegExp(rawUserCode));
  assert.doesNotMatch(persisted, /tmcra_st_token123\./);

  const firstClaim = database.prepare(
    `UPDATE device_authorizations
     SET status = 'authorizing', issuance_request_id = 'issue-dva_test', updated_at = ?
     WHERE id = 'dva_test' AND status = 'approved'`,
  ).run(now + 1);
  const secondClaim = database.prepare(
    `UPDATE device_authorizations
     SET status = 'authorizing', issuance_request_id = 'issue-dva_test-2'
     WHERE id = 'dva_test' AND status = 'approved'`,
  ).run();
  assert.equal(firstClaim.changes, 1);
  assert.equal(secondClaim.changes, 0);
  assert.deepEqual(
    { ...database.prepare(
      "SELECT status, token_ciphertext, token_iv, issuance_request_id FROM device_authorizations WHERE id = 'dva_test'",
    ).get() },
    { status: "authorizing", token_ciphertext: null, token_iv: null, issuance_request_id: "issue-dva_test" },
  );

  database.prepare(
    `INSERT INTO device_authorizations (
       id, device_code_hash, user_code_hash, code_challenge, provider,
       client_name, source_hash, status, interval_seconds,
       created_at, updated_at, expires_at, approved_by_user_id, personal_space_id
     ) VALUES (
       'dva_harness', 'device_hash_harness', 'user_hash_harness',
       'challenge_hash_harness', 'deepseek_harness', 'DeepSeek Harness',
       'source_hash_harness', 'approved', 5, ?, ?, ?, 'usr_test', 'psp_test'
     )`,
  ).run(now, now, now + 600_000);
  database.prepare(
    `INSERT INTO device_connections (
       id, authorization_id, user_id, personal_space_id, provider, display_name,
       token_id, token_prefix, scope_prefix, permissions_json, status,
       token_expires_at, created_at, updated_at
     ) VALUES (
       'dvc_harness', 'dva_harness', 'usr_test', 'psp_test',
       'deepseek_harness', 'DeepSeek Harness', 'token_harness',
       'tmcra_st_token_harness', 'personal-deadbeef-',
       '["memory:read","memory:write"]', 'active', ?, ?, ?
     )`,
  ).run(now + 31_536_000_000, now, now);
  assert.equal(
    database.prepare("SELECT provider FROM device_connections WHERE id = 'dvc_harness'").get().provider,
    "deepseek_harness",
  );
  assert.throws(
    () => database.prepare(
      `INSERT INTO device_authorizations (
         id, device_code_hash, user_code_hash, code_challenge, provider,
         client_name, source_hash, status, interval_seconds,
         created_at, updated_at, expires_at
       ) VALUES ('dva_unknown', 'dh_unknown', 'uh_unknown', 'challenge_unknown',
                 'unknown_client', 'Unknown', 'source_unknown', 'pending', 5, ?, ?, ?)`,
    ).run(now, now, now + 600_000),
    /CHECK constraint failed/u,
  );

  database.close();
});

test("account setup is SIWC-backed, atomic, idempotent, and PII-free", async () => {
  const [route, consoleSource, setupPage, setupClient] = await Promise.all([
    source("app/api/account/route.ts"),
    source("db/console.ts"),
    source("app/account-setup/page.tsx"),
    source("app/account-setup/AccountSetupClient.tsx"),
  ]);
  assert.match(route, /getChatGPTUser/);
  assert.match(route, /requireSameOrigin\(request\)/);
  assert.match(route, /provisionPersonalAccount/);
  assert.match(consoleSource, /await database\.batch\(\[/);
  assert.match(consoleSource, /ON CONFLICT\(user_id\) DO NOTHING/);
  assert.match(consoleSource, /personal-\$\{await shortHash\(`tmcra:personal:\$\{actor\.id\}`\)\}/);
  assert.doesNotMatch(consoleSource, /scopeName\s*=.*email/i);
  assert.match(setupPage, /requireChatGPTUser\(setupPath\)/);
  assert.match(setupClient, /action:\s*"create_personal"/);
  assert.match(setupClient, /Scope 名称.*不会包含邮箱/);
});

test("anonymous start and PKCE poll contract matches the Codex installer", async () => {
  const [startRoute, tokenRoute, service] = await Promise.all([
    source("app/api/device/v1/authorizations/route.ts"),
    source("app/api/device/v1/token/route.ts"),
    source("app/api/device/v1/device-service.ts"),
  ]);
  assert.doesNotMatch(startRoute, /getChatGPTUser|requireChatGPTUser|resolvePersonalMemoryAccess/);
  assert.match(startRoute, /body\.codeChallenge/);
  assert.match(startRoute, /body\.codeChallengeMethod/);
  assert.match(service, /const DEVICE_CLIENTS =/);
  assert.match(service, /"tmcra-codex"/);
  assert.match(service, /"tmcra-deepseek-harness"/);
  assert.match(service, /verificationPath:\s*"\/console\/connect\/deepseek-harness"/);
  assert.match(service, /Device client is not supported/);
  assert.match(service, /input\.codeChallengeMethod !== "S256"/);
  assert.match(startRoute, /status:\s*201/);
  assert.match(service, /randomBase64Url\(32\)/);
  assert.match(service, /randomUserCode\(\)/);
  assert.match(service, /new Uint8Array\(8\)/);
  assert.match(service, /sha256Base64Url\(deviceCode\)/);
  assert.match(service, /sha256Base64Url\(userCode\)/);
  assert.match(service, /sourceFingerprint\(input\.requestSource\)/);
  assert.match(service, /device_flow_rate_limits/);
  assert.match(service, /START_SOURCE_LIVE_LIMIT/);
  assert.doesNotMatch(service, /INSERT INTO device_authorizations[\s\S]{0,450}\bdevice_code\b(?!_hash)/);
  assert.match(tokenRoute, /body\.deviceCode/);
  assert.match(tokenRoute, /body\.codeVerifier/);
  assert.match(tokenRoute, /body\.deliveryReceipt/);
  assert.match(service, /sha256Base64Url\(codeVerifier\)/);
  assert.match(service, /constantTimeEqual\(presentedChallenge, row\.codeChallenge\)/);
  for (const state of [
    "authorization_pending",
    "slow_down",
    "access_denied",
    "expired_token",
    "invalid_grant",
  ]) {
    assert.match(service, new RegExp(`"${state}"`));
  }
  for (const field of ["accessToken", "deliveryReceipt", "tokenType", "expiresIn", "baseUrl", "scopeNamespace"]) {
    assert.match(service, new RegExp(`\\b${field}\\b`));
  }
});

test("approval requires SIWC plus personal space and never returns the control credential", async () => {
  const [approvalRoute, service, connectPage, harnessConnectPage, connectClient, startRoute] = await Promise.all([
    source("app/api/console/v1/device-authorizations/route.ts"),
    source("app/api/device/v1/device-service.ts"),
    source("app/console/connect/codex/page.tsx"),
    source("app/console/connect/deepseek-harness/page.tsx"),
    source("app/console/connect/codex/CodexDeviceAuthorizationClient.tsx"),
    source("app/api/device/v1/authorizations/route.ts"),
  ]);
  assert.match(approvalRoute, /getChatGPTUser/);
  assert.match(approvalRoute, /resolvePersonalMemoryAccess/);
  assert.match(approvalRoute, /requireSameOrigin\(request\)/);
  assert.match(approvalRoute, /action === "approve"/);
  assert.match(connectPage, /requireChatGPTUser\(returnTo\)/);
  assert.match(connectPage, /user_code/);
  assert.match(connectClient, /Authorize \$\{providerLabel\}/);
  assert.match(connectClient, /撤销连接/);
  assert.match(harnessConnectPage, /requireChatGPTUser\(returnTo\)/);
  assert.match(harnessConnectPage, /provider="deepseek_harness"/);
  assert.match(harnessConnectPage, /providerLabel="DeepSeek Harness"/);
  assert.match(connectClient, /nextAuthorization\.provider !== provider/);

  assert.match(service, /createMemoryControlClient/);
  assert.match(service, /TMCRA_MEMORY_API_CONTROL_BASE_URL/);
  assert.match(service, /TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK === "1"/);
  assert.match(service, /baseUrl:\s*publicMemoryApiBaseUrl\(\)/);
  assert.match(service, /scope_names:\s*\[\]/);
  assert.match(service, /scope_prefixes:\s*\[`\$\{access\.space\.scopeName\}-`\]/);
  assert.match(service, /subject:\s*access\.space\.id/);
  assert.match(service, /"memory:read",\s*"memory:write",\s*"memory:consolidate",\s*"memory:feedback"/);
  assert.match(service, /expires_in_seconds:\s*TOKEN_LIFETIME_SECONDS/);
  assert.match(service, /provisional_delivery_seconds:\s*PROVISIONAL_TOKEN_SECONDS/);
  assert.match(service, /issueTokenOnFirstPoll/);
  assert.match(service, /acknowledgeTokenDelivery/);
  assert.match(service, /encryptTokenDelivery/);
  assert.match(service, /decryptTokenDelivery/);
  assert.match(service, /delivery_receipt_hash/);
  assert.match(service, /status = 'claimed'/);
  assert.match(service, /token_ciphertext = NULL, token_iv = NULL/);
  assert.match(service, /device_revocation_outbox/);
  assert.match(service, /drainRevocationOutbox/);
  const upstream = await source("app/api/device/v1/upstream-client.mjs");
  assert.match(upstream, /method:\s*"DELETE"/);
  assert.match(upstream, /"Idempotency-Key": requestId/);
  assert.match(upstream, /\/confirm`/);
  assert.match(upstream, /redirect:\s*"manual"/);
  assert.doesNotMatch(upstream, /redirect:\s*"error"/);

  assert.doesNotMatch(connectClient, /TMCRA_MEMORY_API_CONTROL_KEY|controlKey|Authorization:\s*`Bearer/);
  assert.doesNotMatch(startRoute, /TMCRA_MEMORY_API_CONTROL_KEY|TMCRA_DEVICE_TOKEN_ENCRYPTION_KEY/);
  assert.doesNotMatch(
    service,
    /console\.(?:log|warn|error)\([^)]*(?:controlKey|accessToken|tokenCiphertext)/,
  );
});

test("device delivery has autonomous revocation maintenance and race-safe terminal states", async () => {
  const [service, maintenanceRoute, maintenanceWorker, supervisor, environmentExample, viteConfig, deployScript] =
    await Promise.all([
      source("app/api/device/v1/device-service.ts"),
      source("app/api/device/v1/maintenance/route.ts"),
      source("deploy/gpuhome/maintenance.py"),
      source("deploy/gpuhome/supervisor.py"),
      source("deploy/gpuhome/deployment.env.example"),
      source("vite.config.ts"),
      source("deploy/gpuhome/deploy_release.sh"),
    ]);

  assert.match(service, /export async function runDeviceMaintenance/);
  assert.match(service, /drainRevocationOutbox\(database, requestId, 10\)/);
  assert.match(service, /connection\.status !== "active"/);
  assert.match(service, /status IN \('approved', 'authorizing'\) THEN 'expired'/);
  assert.match(service, /status = \?3 AND updated_at = \?4/);
  assert.match(service, /a\.status = 'expired' AND a\.updated_at = \?1/);

  assert.match(maintenanceRoute, /TMCRA_DEVICE_MAINTENANCE_SECRET/);
  assert.match(maintenanceRoute, /constantTimeSecretEqual/);
  assert.match(maintenanceRoute, /runDeviceMaintenance\(requestId\)/);
  assert.doesNotMatch(maintenanceRoute, /TMCRA_MEMORY_API_CONTROL_KEY/);
  assert.match(maintenanceWorker, /127\.0\.0\.1/);
  assert.match(maintenanceWorker, /Authorization.*Bearer/);
  assert.match(maintenanceWorker, /device maintenance failed/);
  assert.match(supervisor, /"maintenance": \[sys\.executable, "-u", str\(maintenance\)\]/);
  assert.match(environmentExample, /TMCRA_DEVICE_MAINTENANCE_SECRET=/);
  assert.match(environmentExample, /TMCRA_DEVICE_MAINTENANCE_INTERVAL_SECONDS=30/);
  for (const name of [
    "TMCRA_MEMORY_API_BASE_URL",
    "TMCRA_MEMORY_API_CONTROL_BASE_URL",
    "TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK",
    "TMCRA_MEMORY_API_CONTROL_KEY",
    "TMCRA_MEMORY_API_STAFF_MONITORING_KEY",
    "TMCRA_DEVICE_TOKEN_ENCRYPTION_KEY",
    "TMCRA_DEVICE_FLOW_HASH_KEY",
    "TMCRA_DEVICE_MAINTENANCE_SECRET",
  ]) {
    assert.match(viteConfig, new RegExp(`"${name}"`));
  }
  assert.match(
    viteConfig,
    /secrets:\s*\{ required:\s*\[\.\.\.WORKER_RUNTIME_VAR_NAMES\] \}/,
  );
  assert.doesNotMatch(viteConfig, /vars:\s*runtimeWorkerVars\(\)/);
  assert.match(deployScript, /done <"\$shared\/deployment\.env"/);
  assert.match(deployScript, /export "\$name=\$value"/);
  assert.match(deployScript, /export TMCRA_RELEASE_ID="\$TMCRA_RELEASE"/);
  assert.match(deployScript, /persist_release_id/);
  assert.match(deployScript, /os\.replace\(temporary, path\)/);
  assert.match(deployScript, /deployment_env_activated/);
  assert.match(deployScript, /payload\.get\("release"\) == sys\.argv\[1\]/);
  assert.match(deployScript, /kill -KILL -- "-\$child_group"/);
  assert.match(deployScript, /chmod 700 "\$temp_release"/);
});
