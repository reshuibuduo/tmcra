import assert from "node:assert/strict";
import test from "node:test";


const BASE_URL = process.env.TEST_BASE_URL ?? "http://localhost:3001";
const API_URL = new URL("/api/internal", withTrailingSlash(BASE_URL));
if (!["localhost", "127.0.0.1", "::1"].includes(API_URL.hostname)) {
  throw new Error(`Refusing active security probes for ${API_URL.hostname}.`);
}
const EXPECTED_ORIGIN = API_URL.origin;
const OWNER_EMAIL = process.env.TMCRA_TEST_OWNER_EMAIL ?? "seedy@sites.test";


test("Sites local-auth internal security regression", { timeout: 120_000 }, async (t) => {
  let cookie = "";
  let ownerSnapshot = null;

  await t.test("rejects unauthenticated and caller-supplied identity headers", async () => {
    const unauthenticated = await apiRequest();
    assertError(unauthenticated, 401, "authentication_required");
    assertSecurityHeaders(unauthenticated.response);

    const spoofed = await apiRequest({
      headers: {
        "oai-authenticated-user-email": "attacker@example.invalid",
        "x-forwarded-email": "attacker@example.invalid",
        "cf-access-authenticated-user-email": "attacker@example.invalid",
      },
    });
    assertError(spoofed, 401, "authentication_required");
  });

  await t.test("signs in through the official Sites flow and locks bootstrap", async () => {
    const signIn = await fetch(
      new URL("/signin-with-chatgpt?return_to=/", API_URL),
      { redirect: "manual" },
    );
    assert.equal(signIn.status, 302);
    assert.equal(signIn.headers.get("location"), "/");
    const setCookie = signIn.headers.get("set-cookie") ?? "";
    cookie = setCookie.split(";", 1)[0];
    assert.equal(cookie, "__sites_local_auth=1");

    const owner = await apiRequest({ cookie });
    assertOk(owner);
    assert.equal(owner.json.actor.email.toLowerCase(), OWNER_EMAIL.toLowerCase());
    assert.equal(owner.json.actor.role, "platform_owner");
    assert.equal(owner.json.actor.status, "active");
    assert.equal(owner.json.system.bootstrapLocked, true);
    ownerSnapshot = owner.json;
  });

  await t.test("authenticated callers cannot override the Sites identity", async () => {
    const result = await apiRequest({
      cookie,
      headers: {
        "oai-authenticated-user-email": "attacker@example.invalid",
        "oai-authenticated-user-id": "attacker-id",
      },
    });
    assertOk(result);
    assert.equal(result.json.actor.email.toLowerCase(), OWNER_EMAIL.toLowerCase());
    assert.notEqual(result.json.actor.id, "attacker-id");
  });

  await t.test("enforces mutation origin, media type, and body limits", async () => {
    const missingOrigin = await apiRequest({
      method: "POST",
      cookie,
      origin: null,
    });
    assertError(missingOrigin, 403, "origin_mismatch");

    const wrongMediaType = await apiRequest({
      method: "POST",
      cookie,
      contentType: "text/plain",
    });
    assertError(wrongMediaType, 415, "unsupported_media_type");

    const oversized = await apiRequest({
      method: "POST",
      cookie,
      rawBody: JSON.stringify({
        action: "security.probe",
        payload: { padding: "x".repeat(65_536) },
      }),
    });
    assertError(oversized, 413, "payload_too_large");

    const authenticatedSameOrigin = await apiRequest({ method: "POST", cookie });
    assertError(authenticatedSameOrigin, 400, "unknown_action");
  });

  await t.test("owner snapshot exposes no credential material", () => {
    assert.ok(ownerSnapshot);
    assert.deepEqual(findForbiddenSecretKeys(ownerSnapshot), []);
    assert.doesNotMatch(
      JSON.stringify(ownerSnapshot),
      /tmcra_sk_live_[A-Za-z0-9_:-]+\.[A-Za-z0-9_-]{20,}/,
    );
  });
});


async function apiRequest({
  method = "GET",
  cookie,
  origin = method === "POST" ? EXPECTED_ORIGIN : undefined,
  fetchSite = method === "POST" ? "same-origin" : undefined,
  contentType = method === "POST" ? "application/json" : undefined,
  rawBody,
  headers: extraHeaders = {},
} = {}) {
  const headers = new Headers(extraHeaders);
  if (cookie) headers.set("cookie", cookie);
  if (origin !== undefined && origin !== null) headers.set("origin", origin);
  if (fetchSite !== undefined && fetchSite !== null) {
    headers.set("sec-fetch-site", fetchSite);
  }
  if (contentType !== undefined && contentType !== null) {
    headers.set("content-type", contentType);
  }
  const body = method === "POST"
    ? rawBody ?? JSON.stringify({ action: "security.probe", payload: {} })
    : undefined;
  const response = await fetch(API_URL, {
    method,
    headers,
    body,
    redirect: "manual",
    cache: "no-store",
  });
  const text = await response.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    // Assertion failures include the raw response.
  }
  return { response, text, json };
}


function assertOk(result) {
  assert.equal(result.response.status, 200, result.text);
  assert.equal(result.json?.ok, true, result.text);
  assertSecurityHeaders(result.response);
}


function assertError(result, status, code) {
  assert.equal(result.response.status, status, result.text);
  assert.equal(result.json?.ok, false, result.text);
  assert.equal(result.json?.error?.code, code, result.text);
  assert.deepEqual(
    Object.keys(result.json?.error ?? {}).sort(),
    ["code", "message", "requestId"],
    result.text,
  );
  assertSecurityHeaders(result.response);
}


function assertSecurityHeaders(response) {
  assert.match(response.headers.get("cache-control") ?? "", /\bno-store\b/i);
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
  assert.equal(response.headers.get("x-frame-options"), "DENY");
  assert.match(response.headers.get("content-security-policy") ?? "", /frame-ancestors 'none'/);
  assert.match(response.headers.get("permissions-policy") ?? "", /camera=\(\)/);
  assert.match(response.headers.get("x-robots-tag") ?? "", /noindex/i);
  assert.ok(response.headers.get("x-request-id"));
}


function findForbiddenSecretKeys(value, path = "$", found = []) {
  if (!value || typeof value !== "object") return found;
  if (Array.isArray(value)) {
    value.forEach((entry, index) => findForbiddenSecretKeys(entry, `${path}[${index}]`, found));
    return found;
  }
  const forbidden = new Set([
    "password", "passwordhash", "password_hash", "secret", "secrethash",
    "secret_hash", "token", "tokenhash", "token_hash", "apikeyhash",
    "api_key_hash", "credential", "credentials",
  ]);
  for (const [key, entry] of Object.entries(value)) {
    if (forbidden.has(key.toLowerCase())) found.push(`${path}.${key}`);
    findForbiddenSecretKeys(entry, `${path}.${key}`, found);
  }
  return found;
}


function withTrailingSlash(value) {
  return value.endsWith("/") ? value : `${value}/`;
}
