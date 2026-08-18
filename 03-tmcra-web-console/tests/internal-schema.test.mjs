import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { DatabaseSync } from "node:sqlite";

const migrationUrl = new URL("../drizzle/0001_hot_dexter_bennett.sql", import.meta.url);
const accessMigrationUrl = new URL("../drizzle/0002_noisy_sunfire.sql", import.meta.url);
const applicationMigrationUrl = new URL("../drizzle/0003_tiny_giant_girl.sql", import.meta.url);

function applyMigration(database, url) {
  const migration = readFileSync(url, "utf8");
  for (const statement of migration.split("--> statement-breakpoint")) {
    if (statement.trim()) database.exec(statement);
  }
}

function migratedDatabase() {
  const database = new DatabaseSync(":memory:");
  applyMigration(database, migrationUrl);
  return database;
}

test("pilot application migration preserves legacy requests and adds review fields", () => {
  const database = new DatabaseSync(":memory:");
  applyMigration(database, accessMigrationUrl);
  database.prepare(
    `INSERT INTO early_access_requests
      (id, email_normalized, email_display, source, status, created_at, updated_at)
     VALUES (?, ?, ?, 'website', 'new', ?, ?)`,
  ).run("request-1", "pilot@example.com", "pilot@example.com", 100, 100);

  applyMigration(database, applicationMigrationUrl);
  const saved = database.prepare(
    `SELECT id, contact_name AS contactName, platforms_json AS platformsJson,
            review_note AS reviewNote, version
     FROM early_access_requests WHERE id = ?`,
  ).get("request-1");

  assert.deepEqual({ ...saved }, {
    id: "request-1",
    contactName: "",
    platformsJson: "[]",
    reviewNote: "",
    version: 1,
  });
  assert.throws(
    () => database.prepare("UPDATE early_access_requests SET platforms_json = 'invalid' WHERE id = ?").run("request-1"),
    /early_access_requests_platforms_json_check/,
  );
});

function insertStaff(database, { id, email, role = "platform_owner", status = "active" }) {
  database
    .prepare(
      `INSERT INTO internal_staff (
         id, email_normalized, email_display, display_name, role, status
       ) VALUES (?, ?, ?, ?, ?, ?)`,
    )
    .run(id, email, email, email, role, status);
}

test("the permanent internal bootstrap lock cannot be changed or removed", () => {
  const database = migratedDatabase();
  database
    .prepare("INSERT INTO internal_meta (key, value) VALUES (?, ?)")
    .run("internal_bootstrap_owner_email", "owner@example.com");

  assert.throws(
    () => database.prepare("UPDATE internal_meta SET value = ? WHERE key = ?").run(
      "attacker@example.com",
      "internal_bootstrap_owner_email",
    ),
    /internal_bootstrap_locked/,
  );
  assert.throws(
    () => database.prepare("DELETE FROM internal_meta WHERE key = ?").run(
      "internal_bootstrap_owner_email",
    ),
    /internal_bootstrap_locked/,
  );
});

test("the database preserves at least one active platform owner", () => {
  const database = migratedDatabase();
  insertStaff(database, { id: "owner-1", email: "owner-1@example.com" });

  assert.throws(
    () => database.prepare("UPDATE internal_staff SET status = 'suspended' WHERE id = ?").run("owner-1"),
    /internal_last_platform_owner/,
  );
  assert.throws(
    () => database.prepare("UPDATE internal_staff SET role = 'support' WHERE id = ?").run("owner-1"),
    /internal_last_platform_owner/,
  );
  assert.throws(
    () => database.prepare("DELETE FROM internal_staff WHERE id = ?").run("owner-1"),
    /internal_last_platform_owner/,
  );

  insertStaff(database, { id: "owner-2", email: "owner-2@example.com" });
  database.prepare("UPDATE internal_staff SET status = 'suspended' WHERE id = ?").run("owner-1");
  assert.equal(
    database.prepare(
      "SELECT COUNT(*) AS count FROM internal_staff WHERE role = 'platform_owner' AND status = 'active'",
    ).get().count,
    1,
  );
});

test("internal audit entries are append-only", () => {
  const database = migratedDatabase();
  database
    .prepare(
      `INSERT INTO internal_audit_logs (
         id, actor_email, actor_role, action, target_type, target_id, request_id
       ) VALUES (?, ?, ?, ?, ?, ?, ?)`,
    )
    .run(
      "audit-1",
      "owner@example.com",
      "platform_owner",
      "security.test",
      "system",
      "internal",
      "request-1",
    );

  assert.throws(
    () => database.prepare("UPDATE internal_audit_logs SET action = 'tampered' WHERE id = ?").run("audit-1"),
    /internal_audit_immutable/,
  );
  assert.throws(
    () => database.prepare("DELETE FROM internal_audit_logs WHERE id = ?").run("audit-1"),
    /internal_audit_immutable/,
  );
});
