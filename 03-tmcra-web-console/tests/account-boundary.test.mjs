import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { DatabaseSync } from "node:sqlite";

const baseMigration = new URL("../drizzle/0000_abandoned_justice.sql", import.meta.url);
const boundaryMigration = new URL("../drizzle/0004_living_marvex.sql", import.meta.url);

function applyMigration(database, url) {
  const migration = readFileSync(url, "utf8");
  for (const statement of migration.split("--> statement-breakpoint")) {
    if (statement.trim()) database.exec(statement);
  }
}

function insertUser(database, id, email) {
  database.prepare(
    `INSERT INTO users (id, email_normalized, email_display, display_name)
     VALUES (?, ?, ?, ?)`,
  ).run(id, email, email, email);
}

test("account migration preserves enterprise users without manufacturing personal spaces", () => {
  const database = new DatabaseSync(":memory:");
  applyMigration(database, baseMigration);
  insertUser(database, "user-enterprise", "enterprise@example.com");
  database.prepare(
    `INSERT INTO organizations (id, name, slug, created_by_user_id)
     VALUES ('org-1', 'Example Org', 'example-org', 'user-enterprise')`,
  ).run();
  database.prepare(
    `INSERT INTO organization_members (organization_id, user_id, role, status)
     VALUES ('org-1', 'user-enterprise', 'owner', 'active')`,
  ).run();

  applyMigration(database, boundaryMigration);
  assert.equal(
    database.prepare("SELECT account_type FROM account_profiles WHERE user_id = ?").get("user-enterprise").account_type,
    "enterprise",
  );
  assert.equal(database.prepare("SELECT COUNT(*) AS count FROM personal_memory_spaces").get().count, 0);
});

test("database triggers keep personal and enterprise resources mutually exclusive", () => {
  const database = new DatabaseSync(":memory:");
  applyMigration(database, baseMigration);
  applyMigration(database, boundaryMigration);
  insertUser(database, "personal-user", "personal@example.com");
  insertUser(database, "enterprise-user", "enterprise@example.com");
  database.prepare("INSERT INTO account_profiles (user_id, account_type) VALUES (?, 'personal')").run("personal-user");
  database.prepare("INSERT INTO account_profiles (user_id, account_type) VALUES (?, 'enterprise')").run("enterprise-user");
  database.prepare(
    `INSERT INTO personal_memory_spaces (id, user_id, scope_name, display_name)
     VALUES ('space-1', 'personal-user', 'personal.user-1', 'Personal Memory')`,
  ).run();
  database.prepare(
    `INSERT INTO organizations (id, name, slug, created_by_user_id)
     VALUES ('org-1', 'Example Org', 'example-org', 'enterprise-user')`,
  ).run();

  assert.throws(
    () => database.prepare(
      `INSERT INTO organization_members (organization_id, user_id, role, status)
       VALUES ('org-1', 'personal-user', 'viewer', 'active')`,
    ).run(),
    /personal_account_enterprise_activation/,
  );
  assert.throws(
    () => database.prepare(
      `INSERT INTO personal_memory_spaces (id, user_id, scope_name, display_name)
       VALUES ('space-2', 'enterprise-user', 'personal.user-2', 'Invalid')`,
    ).run(),
    /personal_space_requires_personal_account/,
  );
});

test("customer and internal control planes have independent routes and bindings", async () => {
  const [consoleDb, router, consoleHome, enterpriseApi, personalApi, internalApi] = await Promise.all([
    readFile(new URL("../db/console.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/console/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/console/ConsoleHomeClient.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/enterprise/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/personal/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/internal/route.ts", import.meta.url), "utf8"),
  ]);
  const bootstrap = consoleDb.slice(consoleDb.indexOf("async function bootstrapActor"), consoleDb.indexOf("async function listActorMemberships"));
  assert.doesNotMatch(bootstrap, /INSERT OR IGNORE INTO organizations|Workspace`/);
  assert.match(bootstrap, /INSERT INTO account_profiles/);
  assert.match(router, /ConsoleHomeClient/);
  assert.doesNotMatch(router, /redirect\(routing\.destination\)/);
  assert.match(consoleHome, /Personal memory/);
  assert.match(consoleHome, /Enterprise workspace/);
  assert.match(consoleHome, /Developer tools/);
  assert.match(enterpriseApi, /getConsoleSnapshot/);
  assert.match(personalApi, /resolvePersonalMemoryAccess/);
  assert.match(personalApi, /TMCRA_MEMORY_API_CONTROL_KEY/);
  assert.match(personalApi, /subject=\$\{encodeURIComponent\(access\.space\.id\)\}/);
  assert.match(personalApi, /startsWith\(projectPrefix\)/);
  assert.doesNotMatch(personalApi, /TMCRA_MEMORY_API_PERSONAL_BINDINGS|organizationId|TMCRA_MEMORY_API_TENANT_BINDINGS/);
  assert.match(internalApi, /getInternalSnapshot/);
  assert.doesNotMatch(internalApi, /resolvePersonalMemoryAccess|getConsoleSnapshot/);
});
