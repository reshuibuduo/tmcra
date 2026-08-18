import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  configuredBootstrapOwnerEmail,
  isConfiguredBootstrapOwner,
} from "../db/internal-bootstrap-policy.ts";

const readSource = (path) => readFile(new URL(path, import.meta.url), "utf8");

test("internal owner bootstrap accepts only one exact valid configured email", () => {
  assert.equal(
    configuredBootstrapOwnerEmail("Owner@Example.com"),
    "owner@example.com",
  );
  assert.equal(
    isConfiguredBootstrapOwner("Owner@Example.com", "owner@example.com"),
    true,
  );
  assert.equal(
    isConfiguredBootstrapOwner("owner@example.com", "stranger@example.com"),
    false,
  );

  for (const invalid of [
    undefined,
    null,
    "",
    "   ",
    " owner@example.com",
    "owner@example.com ",
    "owner@example.com,stranger@example.com",
    "*@example.com",
    "owner@example",
    "owner@@example.com",
    42,
  ]) {
    assert.equal(configuredBootstrapOwnerEmail(invalid), null);
    assert.equal(isConfiguredBootstrapOwner(invalid, "owner@example.com"), false);
  }
});

test("internal API explicitly passes the server-only bootstrap setting", async () => {
  const [route, envTypes] = await Promise.all([
    readSource("../app/api/internal/route.ts"),
    readSource("../cloudflare-env.d.ts"),
  ]);

  assert.match(route, /env\.TMCRA_INTERNAL_BOOTSTRAP_OWNER_EMAIL/);
  assert.match(route, /getInternalSnapshot\([\s\S]*internalBootstrapConfig\(\)/);
  assert.match(route, /executeInternalAction\([\s\S]*internalBootstrapConfig\(\)/);
  assert.match(envTypes, /TMCRA_INTERNAL_BOOTSTRAP_OWNER_EMAIL\?: string/);
});

test("a denied request cannot initialize internal control-plane records", async () => {
  const internal = await readSource("../db/internal.ts");
  const initializer = internal.slice(
    internal.indexOf("async function initializeInternalSchema"),
    internal.indexOf("export async function getInternalSnapshot"),
  );
  const actorGuard = internal.slice(
    internal.indexOf("async function requireInternalActor"),
    internal.indexOf("async function attemptOwnerBootstrap"),
  );

  assert.doesNotMatch(initializer, /INSERT\s+INTO\s+internal_(?:meta|staff|audit_logs)/i);
  assert.match(
    actorGuard,
    /!actor\s*&&[\s\S]*!bootstrapLock\s*&&[\s\S]*isConfiguredBootstrapOwner\(bootstrapConfig\.ownerEmail, email\)/,
  );
  assert.match(
    actorGuard,
    /internal_access_denied[\s\S]*Internal access is not available for this account/,
  );
});

test("internal UI no longer promises first-visitor owner initialization", async () => {
  const client = await readSource("../app/internal/InternalClient.tsx");
  assert.doesNotMatch(client, /first internal Owner bootstrap is handled by the API/i);
  assert.doesNotMatch(client, /first Owner bootstrap is completed by the internal API/i);
  assert.match(client, /server-configured internal email/i);
  assert.match(client, /server-allowlisted internal email/i);
});
