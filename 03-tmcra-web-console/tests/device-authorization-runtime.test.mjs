import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { once } from "node:events";
import { DatabaseSync } from "node:sqlite";
import { after, before, test } from "node:test";

import {
  createMemoryControlClient,
  normalizeMemoryApiBaseUrl,
} from "../app/api/device/v1/upstream-client.mjs";

const root = new URL("../", import.meta.url);
const migrations = [
  "drizzle/0000_abandoned_justice.sql",
  "drizzle/0004_living_marvex.sql",
  "drizzle/0005_clean_loa.sql",
  "drizzle/0006_volatile_xavin.sql",
  "drizzle/0007_device_token_delivery.sql",
  "drizzle/0009_parallel_owl.sql",
  "drizzle/0010_deepseek_harness.sql",
];

let server;
let client;
let issueCalls = 0;
let revokeCalls = 0;
const requests = [];
const issuedExpiresAt = Math.floor(Date.now() / 1000) + 3_600;
const confirmedExpiresAt = Math.floor(Date.now() / 1000) + 365 * 24 * 60 * 60;

test("HTTP control endpoints require explicit loopback mode and never admit non-loopback hosts", () => {
  assert.equal(normalizeMemoryApiBaseUrl("https://api.tmcra.com/"), "https://api.tmcra.com");
  assert.throws(() => normalizeMemoryApiBaseUrl("http://127.0.0.1:2009"));
  assert.equal(
    normalizeMemoryApiBaseUrl("http://127.0.0.1:2009", { allowHttpLoopback: true }),
    "http://127.0.0.1:2009",
  );
  assert.throws(() =>
    normalizeMemoryApiBaseUrl("http://localhost:2009", { allowHttpLoopback: true }),
  );
  assert.throws(() =>
    normalizeMemoryApiBaseUrl("http://[::1]:2009", { allowHttpLoopback: true }),
  );
  assert.throws(() =>
    normalizeMemoryApiBaseUrl("http://" + "user:secret@" + "127.0.0.1:2009", {
      allowHttpLoopback: true,
    }),
  );
  assert.throws(() =>
    normalizeMemoryApiBaseUrl("http://127.0.0.1.evil.example:2009", {
      allowHttpLoopback: true,
    }),
  );
  assert.throws(() =>
    normalizeMemoryApiBaseUrl("http://203.0.113.8:2009", { allowHttpLoopback: true }),
  );
});

test("control requests use Workerd-compatible manual redirects and reject redirect responses", async () => {
  let redirectMode;
  let calls = 0;
  const redirectClient = createMemoryControlClient({
    baseUrl: "https://api.tmcra.test",
    controlKey: "runtime-control-key",
    fetchImpl: async (_input, init) => {
      calls += 1;
      redirectMode = init.redirect;
      return new Response(null, {
        status: 302,
        headers: { Location: "https://untrusted.example/collect" },
      });
    },
  });

  await assert.rejects(
    redirectClient.issue(
      {
        label: "Runtime redirect test",
        permissions: ["memory:read"],
        scope_names: ["runtime"],
        expires_in_seconds: 3_600,
      },
      "runtime-redirect-request",
    ),
    (error) => {
      assert.equal(error.status, 502);
      assert.equal(error.code, "memory_control_request_failed");
      return true;
    },
  );
  assert.equal(redirectMode, "manual");
  assert.equal(calls, 1);
});

before(async () => {
  server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks).toString("utf8");
    requests.push({
      method: request.method,
      url: request.url,
      authorization: request.headers.authorization,
      requestId: request.headers["x-request-id"],
      idempotencyKey: request.headers["idempotency-key"],
      body,
    });
    response.setHeader("content-type", "application/json");
    if (request.method === "POST" && request.url === "/v1/access-tokens") {
      issueCalls += 1;
      response.statusCode = 201;
      response.end(JSON.stringify({
        token_id: "runtime_token_1",
        access_token: `tmcra_st_runtime_token_1.${"s".repeat(32)}`,
        expires_at: issuedExpiresAt,
      }));
      return;
    }
    if (request.method === "POST" && request.url === "/v1/access-tokens/runtime_token_1/confirm") {
      response.statusCode = 200;
      response.end(JSON.stringify({
        token_id: "runtime_token_1",
        expires_at: confirmedExpiresAt,
      }));
      return;
    }
    if (request.method === "DELETE" && request.url === "/v1/access-tokens/runtime_token_1") {
      revokeCalls += 1;
      if (revokeCalls === 1) {
        response.statusCode = 503;
        response.end(JSON.stringify({ detail: { message: "temporary failure" } }));
      } else {
        response.statusCode = 200;
        response.end(JSON.stringify({ token_id: "runtime_token_1", revoked: true }));
      }
      return;
    }
    response.statusCode = 404;
    response.end(JSON.stringify({ detail: "not found" }));
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  client = createMemoryControlClient({
    baseUrl: `http://127.0.0.1:${address.port}`,
    controlKey: "runtime-control-key",
    allowHttpLoopback: true,
  });
});

after(async () => {
  server.close();
  await once(server, "close");
});

function applyMigrations(database) {
  database.exec("PRAGMA foreign_keys = ON");
  for (const migrationPath of migrations) {
    const migration = readFileSync(new URL(migrationPath, root), "utf8");
    for (const statement of migration.split("--> statement-breakpoint")) {
      if (statement.trim()) database.exec(statement);
    }
  }
}

function accountDatabase() {
  const database = new DatabaseSync(":memory:");
  applyMigrations(database);
  const now = Date.now();
  database.prepare(
    `INSERT INTO users (id, email_normalized, email_display, display_name)
     VALUES ('usr_runtime', 'runtime@example.com', 'runtime@example.com', 'Runtime User')`,
  ).run();
  database.prepare(
    `INSERT INTO account_profiles (user_id, account_type, status, selected_at)
     VALUES ('usr_runtime', 'personal', 'active', ?)`,
  ).run(now);
  database.prepare(
    `INSERT INTO personal_memory_spaces
       (id, user_id, scope_name, display_name, status, created_at, updated_at)
     VALUES ('psp_runtime', 'usr_runtime', 'personal-runtime', 'Runtime Memory',
             'active', ?, ?)`,
  ).run(now, now);
  return database;
}

function insertAuthorization(database, id, status, expiresAt, sourceHash = "source-a") {
  const now = Date.now();
  database.prepare(
    `INSERT INTO device_authorizations (
       id, device_code_hash, user_code_hash, code_challenge, client_name,
       source_hash, status, interval_seconds, created_at, updated_at, expires_at,
       approved_by_user_id, personal_space_id, approved_at
     ) VALUES (?, ?, ?, ?, 'Codex runtime', ?, ?, 5, ?, ?, ?,
               'usr_runtime', 'psp_runtime', ?)`,
  ).run(id, `dh-${id}`, `uh-${id}`, `challenge-${id}`, sourceHash, status, now, now, expiresAt, now);
}

function rateUpsert(database, key, bucket, limit, admission, now) {
  return database.prepare(
    `INSERT INTO device_flow_rate_limits
       (limit_key, bucket_start, request_count, last_admission_id, updated_at)
     VALUES (?, ?, 1, ?, ?)
     ON CONFLICT(limit_key, bucket_start) DO UPDATE SET
       request_count = request_count + 1,
       last_admission_id = excluded.last_admission_id,
       updated_at = excluded.updated_at
     WHERE request_count < ?`,
  ).run(key, bucket, admission, now, limit);
}

function admitStart(database, { id, source, globalLimit, sourceLimit, rateLimit }) {
  const now = 1_800_000_000_000;
  const bucket = Math.floor(now / 600_000) * 600_000;
  const admission = `adm-${id}`;
  database.exec("BEGIN IMMEDIATE");
  try {
    rateUpsert(database, `start:source:${source}`, bucket, rateLimit, admission, now);
    rateUpsert(database, "start:global", bucket, 100, admission, now);
    const inserted = database.prepare(
      `INSERT INTO device_authorizations (
         id, device_code_hash, user_code_hash, code_challenge, client_name,
         source_hash, status, interval_seconds, created_at, updated_at, expires_at
       ) SELECT ?, ?, ?, ?, 'Codex', ?, 'pending', 5, ?, ?, ?
       WHERE EXISTS (
         SELECT 1 FROM device_flow_rate_limits
         WHERE limit_key = ? AND bucket_start = ? AND last_admission_id = ?
       ) AND EXISTS (
         SELECT 1 FROM device_flow_rate_limits
         WHERE limit_key = 'start:global' AND bucket_start = ? AND last_admission_id = ?
       ) AND (SELECT COUNT(*) FROM device_authorizations
              WHERE expires_at > ? AND status IN ('pending','approved','authorizing')) < ?
         AND (SELECT COUNT(*) FROM device_authorizations
              WHERE source_hash = ? AND expires_at > ?
                AND status IN ('pending','approved','authorizing')) < ?`,
    ).run(
      id,
      `dh-${id}`,
      `uh-${id}`,
      `challenge-${id}`,
      source,
      now,
      now,
      now + 600_000,
      `start:source:${source}`,
      bucket,
      admission,
      bucket,
      admission,
      now,
      globalLimit,
      source,
      now,
      sourceLimit,
    );
    database.exec("COMMIT");
    return inserted.changes;
  } catch (error) {
    database.exec("ROLLBACK");
    throw error;
  }
}

test("D1 admission transaction enforces source/global live caps and cannot reuse a failed rate admission", () => {
  const database = accountDatabase();
  assert.equal(admitStart(database, { id: "dva_a1", source: "A", globalLimit: 3, sourceLimit: 2, rateLimit: 2 }), 1);
  assert.equal(admitStart(database, { id: "dva_a2", source: "A", globalLimit: 3, sourceLimit: 2, rateLimit: 2 }), 1);
  assert.equal(admitStart(database, { id: "dva_a3", source: "A", globalLimit: 3, sourceLimit: 2, rateLimit: 2 }), 0);
  assert.equal(admitStart(database, { id: "dva_b1", source: "B", globalLimit: 3, sourceLimit: 2, rateLimit: 2 }), 1);
  assert.equal(admitStart(database, { id: "dva_c1", source: "C", globalLimit: 3, sourceLimit: 2, rateLimit: 2 }), 0);
  assert.equal(database.prepare("SELECT COUNT(*) AS count FROM device_authorizations").get().count, 3);
  assert.equal(
    database.prepare("SELECT request_count FROM device_flow_rate_limits WHERE limit_key = 'start:source:A'").get().request_count,
    2,
  );
  database.close();
});

test("invalid user-code attempts are limited independently by account and source", () => {
  const database = accountDatabase();
  const now = 1_800_000_000_000;
  const bucket = Math.floor(now / 600_000) * 600_000;
  const attempt = (id, account, source) => {
    database.exec("BEGIN IMMEDIATE");
    try {
      const accountResult = rateUpsert(
        database,
        `invalid:account:${account}`,
        bucket,
        2,
        id,
        now,
      );
      const sourceResult = rateUpsert(
        database,
        `invalid:source:${source}`,
        bucket,
        3,
        id,
        now,
      );
      database.exec("COMMIT");
      return accountResult.changes === 1 && sourceResult.changes === 1;
    } catch (error) {
      database.exec("ROLLBACK");
      throw error;
    }
  };
  assert.equal(attempt("invalid-1", "account-a", "source-a"), true);
  assert.equal(attempt("invalid-2", "account-a", "source-a"), true);
  assert.equal(attempt("invalid-3", "account-a", "source-a"), false);
  assert.equal(attempt("invalid-4", "account-b", "source-a"), false);
  assert.equal(attempt("invalid-5", "account-b", "source-b"), true);
  database.close();
});

test("response loss replays one idempotent provisional Token and confirmation is repeatable", async () => {
  const database = accountDatabase();
  const beforeIssue = issueCalls;
  insertAuthorization(database, "dva_runtime", "pending", Date.now() + 600_000);
  const approved = database.prepare(
    `UPDATE device_authorizations SET status = 'approved', updated_at = ?
     WHERE id = 'dva_runtime' AND status = 'pending'`,
  ).run(Date.now());
  assert.equal(approved.changes, 1);
  assert.equal(issueCalls, beforeIssue, "browser approval must not mint a production Token");

  const issuanceRequestId = "device-issue-dva_runtime";
  const locked = database.prepare(
    `UPDATE device_authorizations
     SET status = 'authorizing', issuance_request_id = ?, updated_at = ?
     WHERE id = 'dva_runtime' AND status = 'approved' AND expires_at > ?`,
  ).run(issuanceRequestId, Date.now(), Date.now());
  assert.equal(locked.changes, 1);
  const issued = await client.issue({
    label: "Codex / runtime",
    subject: "psp_runtime",
    permissions: ["memory:read", "memory:write", "memory:consolidate", "memory:feedback"],
    scope_names: [],
    scope_prefixes: ["personal-runtime-"],
    expires_in_seconds: 3600,
  }, issuanceRequestId);
  const replayed = await client.issue({
    label: "Codex / runtime",
    subject: "psp_runtime",
    permissions: ["memory:read", "memory:write", "memory:consolidate", "memory:feedback"],
    scope_names: [],
    scope_prefixes: ["personal-runtime-"],
    expires_in_seconds: 3600,
  }, issuanceRequestId);
  assert.deepEqual(replayed, issued);
  const confirmed = await client.confirm(issued.token_id, "device-confirm-dva_runtime");
  const confirmedAgain = await client.confirm(issued.token_id, "device-confirm-dva_runtime");
  assert.deepEqual(confirmedAgain, confirmed);
  const completedAt = Date.now();
  database.exec("BEGIN IMMEDIATE");
  try {
    const claimed = database.prepare(
      `UPDATE device_authorizations
       SET status = 'claimed', claimed_at = ?, updated_at = ?
       WHERE id = 'dva_runtime' AND status = 'authorizing'
         AND issuance_request_id = ?`,
    ).run(completedAt, completedAt, issuanceRequestId);
    assert.equal(claimed.changes, 1);
    database.prepare(
      `INSERT INTO device_connections (
         id, authorization_id, user_id, personal_space_id, display_name,
         token_id, token_prefix, scope_prefix, permissions_json, status,
         token_expires_at, created_at, updated_at, last_connected_at
       ) VALUES ('dvc_runtime', 'dva_runtime', 'usr_runtime', 'psp_runtime',
                 'Codex runtime', ?, ?, 'personal-runtime-',
                 '["memory:read","memory:write","memory:consolidate","memory:feedback"]', 'active',
                 ?, ?, ?, ?)`,
    ).run(
      issued.token_id,
      issued.access_token.split(".", 1)[0],
      issued.expires_at * 1000,
      completedAt,
      completedAt,
      completedAt,
    );
    database.exec("COMMIT");
  } catch (error) {
    database.exec("ROLLBACK");
    throw error;
  }

  const secondLock = database.prepare(
    `UPDATE device_authorizations SET status = 'authorizing'
     WHERE id = 'dva_runtime' AND status = 'approved'`,
  ).run();
  assert.equal(secondLock.changes, 0);
  assert.equal(issueCalls, beforeIssue + 2);
  const issuanceRequests = requests.filter(
    (request) => request.method === "POST" && request.url === "/v1/access-tokens",
  ).slice(-2);
  assert.equal(issuanceRequests.length, 2);
  assert.ok(issuanceRequests.every((request) => request.idempotencyKey === issuanceRequestId));
  const confirmationRequests = requests.filter(
    (request) => request.method === "POST" && request.url.endsWith("/confirm"),
  ).slice(-2);
  assert.ok(confirmationRequests.every(
    (request) => request.idempotencyKey === "device-confirm-dva_runtime",
  ));
  const persisted = JSON.stringify({
    authorization: database.prepare("SELECT * FROM device_authorizations WHERE id = 'dva_runtime'").get(),
    connection: database.prepare("SELECT * FROM device_connections WHERE id = 'dvc_runtime'").get(),
  });
  assert.doesNotMatch(persisted, new RegExp(issued.access_token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.equal(database.prepare("SELECT last_connected_at FROM device_connections WHERE id = 'dvc_runtime'").get().last_connected_at, completedAt);
  database.close();
});

test("revocation outbox persists token evidence across upstream failure and completes on retry", async () => {
  const database = accountDatabase();
  insertAuthorization(database, "dva_revoke", "claimed", Date.now() + 600_000);
  const now = Date.now();
  database.prepare(
    `INSERT INTO device_connections (
       id, authorization_id, user_id, personal_space_id, display_name,
       token_id, token_prefix, scope_prefix, permissions_json, status,
       token_expires_at, created_at, updated_at, last_connected_at
     ) VALUES ('dvc_revoke', 'dva_revoke', 'usr_runtime', 'psp_runtime', 'Codex',
               'runtime_token_1', 'tmcra_st_runtime_token_1', 'personal-runtime-',
               '[]', 'active', ?, ?, ?, ?)`,
  ).run(now + 3_600_000, now, now, now);
  database.exec("BEGIN IMMEDIATE");
  database.prepare(
    `UPDATE device_connections SET status = 'revoked', revoked_at = ?, updated_at = ?
     WHERE id = 'dvc_revoke'`,
  ).run(now, now);
  database.prepare(
    `INSERT INTO device_revocation_outbox (
       id, token_id, connection_id, reason, status, attempt_count,
       next_attempt_at, created_at, updated_at
     ) VALUES ('rvo_runtime', 'runtime_token_1', 'dvc_revoke', 'user_revoked',
               'pending', 0, ?, ?, ?)`,
  ).run(now, now, now);
  database.exec("COMMIT");

  await assert.rejects(client.revoke("runtime_token_1", "revoke-attempt-1"), (error) => {
    assert.equal(error.code, "memory_control_request_failed");
    return true;
  });
  database.prepare(
    `UPDATE device_revocation_outbox
     SET status = 'pending', attempt_count = 1, next_attempt_at = ?,
         last_error_code = 'memory_control_request_failed', updated_at = ?
     WHERE id = 'rvo_runtime'`,
  ).run(Date.now(), Date.now());
  const failedRow = database.prepare("SELECT * FROM device_revocation_outbox WHERE id = 'rvo_runtime'").get();
  assert.equal(failedRow.token_id, "runtime_token_1");
  assert.equal(failedRow.status, "pending");
  assert.equal(failedRow.attempt_count, 1);

  await client.revoke("runtime_token_1", "revoke-attempt-2");
  database.prepare(
    `UPDATE device_revocation_outbox
     SET status = 'completed', attempt_count = 2, completed_at = ?,
         last_error_code = NULL, updated_at = ?
     WHERE id = 'rvo_runtime'`,
  ).run(Date.now(), Date.now());
  const completed = database.prepare("SELECT * FROM device_revocation_outbox WHERE id = 'rvo_runtime'").get();
  assert.equal(completed.status, "completed");
  assert.equal(completed.token_id, "runtime_token_1");
  assert.equal(revokeCalls, 2);
  assert.ok(requests.every((request) => request.authorization === "Bearer runtime-control-key"));
  database.close();
});

test("stale issuance leases recover while unclaimed delivery expires and queues revocation", () => {
  const database = accountDatabase();
  const now = Date.now();
  insertAuthorization(database, "dva_stale_lease", "authorizing", now + 600_000);
  database.prepare(
    `UPDATE device_authorizations
     SET issuance_request_id = 'lease-old', updated_at = ?
     WHERE id = 'dva_stale_lease'`,
  ).run(now - 180_000);
  const recovered = database.prepare(
    `UPDATE device_authorizations
     SET status = CASE WHEN expires_at > ? THEN 'approved' ELSE 'expired' END,
         issuance_request_id = NULL, updated_at = ?
     WHERE status = 'authorizing' AND updated_at < ?`,
  ).run(now, now, now - 120_000);
  assert.equal(recovered.changes, 1);
  assert.equal(
    database.prepare("SELECT status FROM device_authorizations WHERE id = 'dva_stale_lease'").get().status,
    "approved",
  );

  insertAuthorization(database, "dva_unclaimed", "approved", now + 600_000);
  database.prepare(
    `UPDATE device_authorizations
     SET token_ciphertext = 'encrypted-only', token_iv = 'unique-iv',
         delivery_receipt_hash = 'receipt-hash', updated_at = ?
     WHERE id = 'dva_unclaimed'`,
  ).run(now - 360_000);
  database.prepare(
    `INSERT INTO device_connections (
       id, authorization_id, user_id, personal_space_id, display_name,
       token_id, token_prefix, scope_prefix, permissions_json, status,
       token_expires_at, created_at, updated_at
     ) VALUES ('dvc_unclaimed', 'dva_unclaimed', 'usr_runtime', 'psp_runtime',
               'Codex', 'token_unclaimed', 'tmcra_st_token_unclaimed',
               'personal-runtime-', '[]', 'active', ?, ?, ?)`,
  ).run(now + 240_000, now - 360_000, now - 360_000);
  database.exec("BEGIN IMMEDIATE");
  try {
    database.prepare(
      `INSERT INTO device_revocation_outbox (
         id, token_id, connection_id, reason, status, attempt_count,
         next_attempt_at, created_at, updated_at
       ) VALUES ('rvo_unclaimed', 'token_unclaimed', 'dvc_unclaimed',
                 'delivery_unclaimed', 'pending', 0, ?, ?, ?)`,
    ).run(now, now, now);
    database.prepare(
      `UPDATE device_connections SET status = 'expired', revoked_at = ?, updated_at = ?
       WHERE id = 'dvc_unclaimed'`,
    ).run(now, now);
    database.prepare(
      `UPDATE device_authorizations
       SET status = 'expired', token_ciphertext = NULL, token_iv = NULL, updated_at = ?
       WHERE id = 'dva_unclaimed'`,
    ).run(now);
    database.exec("COMMIT");
  } catch (error) {
    database.exec("ROLLBACK");
    throw error;
  }
  assert.deepEqual(
    { ...database.prepare(
      "SELECT status, token_ciphertext AS ciphertext FROM device_authorizations WHERE id = 'dva_unclaimed'",
    ).get() },
    { status: "expired", ciphertext: null },
  );
  assert.deepEqual(
    { ...database.prepare(
      "SELECT reason, status FROM device_revocation_outbox WHERE token_id = 'token_unclaimed'",
    ).get() },
    { reason: "delivery_unclaimed", status: "pending" },
  );
  database.close();
});

test("natural expiry creates revocation evidence and terminal cleanup stays bounded", () => {
  const database = accountDatabase();
  const now = Date.now();
  insertAuthorization(database, "dva_expired_connection", "claimed", now + 600_000);
  database.prepare(
    `INSERT INTO device_connections (
       id, authorization_id, user_id, personal_space_id, display_name,
       token_id, token_prefix, scope_prefix, permissions_json, status,
       token_expires_at, created_at, updated_at
     ) VALUES ('dvc_expired_connection', 'dva_expired_connection', 'usr_runtime',
               'psp_runtime', 'Codex', 'naturally_expired_token',
               'tmcra_st_naturally_expired_token', 'personal-runtime-', '[]',
               'active', ?, ?, ?)`,
  ).run(now - 1, now - 1000, now - 1000);
  database.exec("BEGIN IMMEDIATE");
  database.prepare(
    `INSERT INTO device_revocation_outbox (
       id, token_id, connection_id, reason, status, attempt_count,
       next_attempt_at, created_at, updated_at
     ) SELECT 'rvo_natural', token_id, id, 'token_expired', 'pending', 0, ?, ?, ?
       FROM device_connections WHERE status = 'active' AND token_expires_at <= ?`,
  ).run(now, now, now, now);
  database.prepare(
    `UPDATE device_connections SET status = 'expired', revoked_at = ?, updated_at = ?
     WHERE status = 'active' AND token_expires_at <= ?`,
  ).run(now, now, now);
  database.exec("COMMIT");
  assert.equal(database.prepare("SELECT status FROM device_connections WHERE id = 'dvc_expired_connection'").get().status, "expired");
  assert.deepEqual(
    { ...database.prepare("SELECT token_id, reason, status FROM device_revocation_outbox WHERE id = 'rvo_natural'").get() },
    { token_id: "naturally_expired_token", reason: "token_expired", status: "pending" },
  );

  const terminalAt = now - 2 * 24 * 60 * 60_000;
  for (let index = 0; index < 150; index += 1) {
    database.prepare(
      `INSERT INTO device_authorizations (
         id, device_code_hash, user_code_hash, code_challenge, source_hash,
         status, expires_at, created_at, updated_at
       ) VALUES (?, ?, ?, ?, 'cleanup-source', 'denied', ?, ?, ?)`,
    ).run(
      `dva_cleanup_${index}`,
      `dh-cleanup-${index}`,
      `uh-cleanup-${index}`,
      `challenge-cleanup-${index}`,
      terminalAt,
      terminalAt,
      terminalAt,
    );
  }
  const cleanup = database.prepare(
    `DELETE FROM device_authorizations
     WHERE id IN (
       SELECT id FROM device_authorizations
       WHERE status IN ('denied','expired') AND updated_at < ?
         AND NOT EXISTS (
           SELECT 1 FROM device_connections
           WHERE authorization_id = device_authorizations.id
         )
       ORDER BY updated_at ASC LIMIT 100
     )`,
  ).run(now - 24 * 60 * 60_000);
  assert.equal(cleanup.changes, 100);
  assert.equal(database.prepare("SELECT COUNT(*) AS count FROM device_authorizations WHERE id LIKE 'dva_cleanup_%'").get().count, 50);
  database.close();
});

test("stale expiry cannot revoke a connection after a concurrent ACK claim", () => {
  const database = accountDatabase();
  const staleAt = Date.now() - 1_000;
  const claimedAt = staleAt + 500;
  const expireAttemptAt = claimedAt + 500;
  insertAuthorization(database, "dva_ack_won", "approved", Date.now() + 600_000);
  database.prepare(
    `UPDATE device_authorizations
     SET token_ciphertext = 'encrypted', token_iv = 'iv', updated_at = ?
     WHERE id = 'dva_ack_won'`,
  ).run(staleAt);
  database.prepare(
    `INSERT INTO device_connections (
       id, authorization_id, user_id, personal_space_id, display_name,
       token_id, token_prefix, scope_prefix, permissions_json, status,
       token_expires_at, created_at, updated_at
     ) VALUES ('dvc_ack_won', 'dva_ack_won', 'usr_runtime', 'psp_runtime',
               'Codex', 'token_ack_won', 'tmcra_st_token_ack_won',
               'personal-runtime-', '[]', 'active', ?, ?, ?)`,
  ).run(Date.now() + 600_000, staleAt, staleAt);

  database.prepare(
    `UPDATE device_authorizations
     SET status = 'claimed', token_ciphertext = NULL, token_iv = NULL,
         claimed_at = ?, updated_at = ?
     WHERE id = 'dva_ack_won' AND status = 'approved'`,
  ).run(claimedAt, claimedAt);

  database.exec("BEGIN IMMEDIATE");
  try {
    const authorizationWrite = database.prepare(
      `UPDATE device_authorizations
       SET status = 'expired', token_ciphertext = NULL, token_iv = NULL,
           issuance_request_id = NULL, updated_at = ?
       WHERE id = ? AND status = ? AND updated_at = ?
         AND issuance_request_id IS ?`,
    ).run(expireAttemptAt, "dva_ack_won", "approved", staleAt, null);
    const connectionWrite = database.prepare(
      `UPDATE device_connections
       SET status = 'expired', revoked_at = COALESCE(revoked_at, ?), updated_at = ?
       WHERE id = 'dvc_ack_won' AND status = 'active'
         AND EXISTS (
           SELECT 1 FROM device_authorizations a
           WHERE a.id = 'dva_ack_won' AND a.status = 'expired' AND a.updated_at = ?
         )`,
    ).run(expireAttemptAt, expireAttemptAt, expireAttemptAt);
    const outboxWrite = database.prepare(
      `INSERT INTO device_revocation_outbox (
         id, token_id, connection_id, reason, status, attempt_count,
         next_attempt_at, created_at, updated_at
       ) SELECT 'rvo_ack_won', 'token_ack_won', 'dvc_ack_won',
                'authorization_expired', 'pending', 0, ?, ?, ?
         WHERE EXISTS (
           SELECT 1 FROM device_authorizations a
           WHERE a.id = 'dva_ack_won' AND a.status = 'expired' AND a.updated_at = ?
         )`,
    ).run(expireAttemptAt, expireAttemptAt, expireAttemptAt, expireAttemptAt);
    database.exec("COMMIT");
    assert.equal(authorizationWrite.changes, 0);
    assert.equal(connectionWrite.changes, 0);
    assert.equal(outboxWrite.changes, 0);
  } catch (error) {
    database.exec("ROLLBACK");
    throw error;
  }

  assert.equal(
    database.prepare("SELECT status FROM device_authorizations WHERE id = 'dva_ack_won'").get().status,
    "claimed",
  );
  assert.equal(
    database.prepare("SELECT status FROM device_connections WHERE id = 'dvc_ack_won'").get().status,
    "active",
  );
  assert.equal(
    database.prepare("SELECT COUNT(*) AS count FROM device_revocation_outbox WHERE token_id = 'token_ack_won'").get().count,
    0,
  );
  database.close();
});

test("revoking an unclaimed delivery makes authorization terminal before ACK", () => {
  const database = accountDatabase();
  const now = Date.now();
  insertAuthorization(database, "dva_revoke_before_ack", "approved", now + 600_000);
  database.prepare(
    `UPDATE device_authorizations
     SET token_ciphertext = 'encrypted', token_iv = 'iv',
         delivery_receipt_hash = 'receipt-hash', updated_at = ?
     WHERE id = 'dva_revoke_before_ack'`,
  ).run(now);
  database.prepare(
    `INSERT INTO device_connections (
       id, authorization_id, user_id, personal_space_id, display_name,
       token_id, token_prefix, scope_prefix, permissions_json, status,
       token_expires_at, created_at, updated_at
     ) VALUES ('dvc_revoke_before_ack', 'dva_revoke_before_ack', 'usr_runtime',
               'psp_runtime', 'Codex', 'token_revoke_before_ack',
               'tmcra_st_token_revoke_before_ack', 'personal-runtime-', '[]',
               'active', ?, ?, ?)`,
  ).run(now + 600_000, now, now);

  database.exec("BEGIN IMMEDIATE");
  database.prepare(
    `UPDATE device_connections
     SET status = 'revoked', revoked_at = ?, updated_at = ?
     WHERE id = 'dvc_revoke_before_ack' AND status <> 'revoked'`,
  ).run(now + 1, now + 1);
  database.prepare(
    `UPDATE device_authorizations
     SET status = CASE
           WHEN status IN ('approved', 'authorizing') THEN 'expired'
           ELSE status
         END,
         token_ciphertext = NULL, token_iv = NULL,
         issuance_request_id = CASE
           WHEN status IN ('approved', 'authorizing') THEN NULL
           ELSE issuance_request_id
         END,
         updated_at = ?
     WHERE id = 'dva_revoke_before_ack'`,
  ).run(now + 1);
  database.exec("COMMIT");

  const ackWrite = database.prepare(
    `UPDATE device_authorizations
     SET status = 'claimed', claimed_at = ?, updated_at = ?
     WHERE id = 'dva_revoke_before_ack' AND status = 'approved'
       AND token_ciphertext IS NOT NULL
       AND EXISTS (
         SELECT 1 FROM device_connections c
         WHERE c.authorization_id = 'dva_revoke_before_ack'
           AND c.token_id = 'token_revoke_before_ack' AND c.status = 'active'
       )`,
  ).run(now + 2, now + 2);
  assert.equal(ackWrite.changes, 0);
  assert.deepEqual(
    { ...database.prepare(
      `SELECT status, token_ciphertext AS ciphertext, token_iv AS iv
       FROM device_authorizations WHERE id = 'dva_revoke_before_ack'`,
    ).get() },
    { status: "expired", ciphertext: null, iv: null },
  );
  assert.equal(
    database.prepare("SELECT status FROM device_connections WHERE id = 'dvc_revoke_before_ack'").get().status,
    "revoked",
  );
  database.close();
});
