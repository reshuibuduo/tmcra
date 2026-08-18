import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { request as httpRequest } from "node:http";
import test from "node:test";

// The Worker must already be running with
// TMCRA_INTERNAL_BOOTSTRAP_OWNER_EMAIL equal to TMCRA_TEST_OWNER_EMAIL. This
// suite intentionally exercises only the local/private internal API and never
// targets an arbitrary public host.
const BASE_URL = process.env.TEST_BASE_URL ?? "http://localhost:3001";
const API_URL = new URL("/api/internal", withTrailingSlash(BASE_URL));
if (!["localhost", "127.0.0.1", "::1"].includes(API_URL.hostname)) {
  throw new Error(
    `Refusing to run active security probes against non-local host ${API_URL.hostname}.`,
  );
}
const EXPECTED_ORIGIN = API_URL.origin;
const OWNER_EMAIL =
  process.env.TMCRA_TEST_OWNER_EMAIL ?? "security-owner@example.com";
const RUN_ID = `${Date.now().toString(36)}-${randomUUID().slice(0, 8)}`;
const UNKNOWN_PROBE_COUNT = 24;

const uniqueEmail = (label) =>
  `tmcra-security-${label}-${RUN_ID}@example.com`.toLowerCase();

test(
  "TMCRA internal control-plane security regression",
  { timeout: 120_000 },
  async (t) => {
    let ownerSnapshot = null;
    let supportStaffId = null;
    let adminStaffId = null;
    const supportEmail = uniqueEmail("support");
    const adminEmail = uniqueEmail("admin");

    await t.test("rejects unauthenticated and untrusted identity headers", async () => {
      const unauthenticated = await apiRequest();
      assertError(unauthenticated, 401, "authentication_required");
      assertSecurityHeaders(unauthenticated.response);
      assertGenericError(unauthenticated);

      const spoofed = await apiRequest({
        headers: {
          "x-forwarded-email": uniqueEmail("forwarded"),
          "cf-access-authenticated-user-email": uniqueEmail("cf-access"),
          "x-user-email": uniqueEmail("generic"),
        },
      });
      assertError(spoofed, 401, "authentication_required");
      assertGenericError(spoofed);

      const unauthenticatedMutation = await apiRequest({
        method: "POST",
        action: "security.probe",
        email: null,
      });
      assertError(unauthenticatedMutation, 401, "authentication_required");
      assertGenericError(unauthenticatedMutation);
    });

    await t.test(
      "fails closed for a non-allowlisted first visitor without consuming bootstrap",
      async () => {
        const stranger = await apiRequest({ email: uniqueEmail("pre-bootstrap") });
        assertError(stranger, 403, "internal_access_denied");
        assertGenericError(stranger);
        assertSecurityHeaders(stranger.response);
      },
    );

    await t.test("bootstraps one owner once and permanently locks bootstrap", async () => {
      const result = await apiRequest({ email: OWNER_EMAIL });
      assert.equal(
        result.response.status,
        200,
        `Expected ${OWNER_EMAIL} to be the bootstrap/existing owner. ` +
          `Set TMCRA_TEST_OWNER_EMAIL to the already-bootstrapped owner when reusing a database. Body: ${result.text}`,
      );
      assert.equal(result.json?.ok, true);
      assert.equal(result.json?.actor?.email?.toLowerCase(), OWNER_EMAIL.toLowerCase());
      assert.equal(result.json?.actor?.role, "platform_owner");
      assert.equal(result.json?.actor?.status, "active");
      assert.equal(result.json?.system?.bootstrapLocked, true);
      if ((result.json?.auditLogs?.length ?? 0) < 100) {
        assert.ok(
          result.json.auditLogs.some((entry) => entry.action === "platform.bootstrap"),
          "The permanent owner bootstrap must be attributable in the audit stream.",
        );
      }
      assertSecurityHeaders(result.response);
      ownerSnapshot = result.json;
    });

    await t.test(
      "returns one generic 403 for burst identity enumeration without DB pollution",
      async (st) => {
        if (!ownerSnapshot) return st.skip("Owner snapshot is unavailable.");

        const before = securityCounts(ownerSnapshot);
        let expectedErrorShape = null;
        for (let index = 0; index < UNKNOWN_PROBE_COUNT; index += 1) {
          const probe = await apiRequest({
            email: uniqueEmail(`unknown-${index}`),
          });
          assertError(probe, 403, "internal_access_denied");
          assertGenericError(probe);
          const shape = {
            status: probe.response.status,
            code: probe.json.error.code,
            message: probe.json.error.message,
          };
          expectedErrorShape ??= shape;
          assert.deepEqual(
            shape,
            expectedErrorShape,
            "Unknown emails must not be distinguishable through status, code, or message.",
          );
        }

        const refreshed = await requireOwnerSnapshot();
        assert.deepEqual(
          securityCounts(refreshed),
          before,
          "Unknown authenticated identities must not create users, staff, tenants, or other counted records.",
        );
        ownerSnapshot = refreshed;
      },
    );

    await t.test("enforces Origin, Fetch-Site, JSON, and 64 KiB body limits", async () => {
      const missingOrigin = await apiRequest({
        method: "POST",
        email: OWNER_EMAIL,
        action: "security.probe",
        origin: null,
      });
      assertError(missingOrigin, 403, "origin_mismatch");
      assertGenericError(missingOrigin);

      const foreignOrigin = await apiRequest({
        method: "POST",
        email: OWNER_EMAIL,
        action: "security.probe",
        origin: "https://attacker.invalid",
      });
      // The local vinext edge may reject a foreign Origin before the route is
      // invoked. Both the edge-level plain 403 and the API JSON 403 are valid.
      assert.equal(foreignOrigin.response.status, 403, foreignOrigin.text);
      if (foreignOrigin.json) {
        assertError(foreignOrigin, 403, "origin_mismatch");
        assertSecurityHeaders(foreignOrigin.response);
        assertGenericError(foreignOrigin);
      } else {
        assert.match(foreignOrigin.text, /^Forbidden\s*$/);
      }

      const crossSite = await apiRequest({
        method: "POST",
        email: OWNER_EMAIL,
        action: "security.probe",
        fetchSite: "cross-site",
      });
      assertError(crossSite, 403, "cross_site_request");

      const navigationalSite = await apiRequest({
        method: "POST",
        email: OWNER_EMAIL,
        action: "security.probe",
        fetchSite: "none",
      });
      assertError(navigationalSite, 403, "cross_site_request");

      const navigationMode = await apiRequest({
        method: "POST",
        email: OWNER_EMAIL,
        action: "security.probe",
        fetchMode: "navigate",
      });
      assertError(navigationMode, 403, "navigation_not_allowed");

      const wrongMediaType = await apiRequest({
        method: "POST",
        email: OWNER_EMAIL,
        action: "security.probe",
        contentType: "text/plain",
      });
      assertError(wrongMediaType, 415, "unsupported_media_type");

      const oversized = await apiRequest({
        method: "POST",
        email: OWNER_EMAIL,
        rawBody: JSON.stringify({
          action: "security.probe",
          payload: { padding: "x".repeat(65_536) },
        }),
      });
      assertError(oversized, 413, "payload_too_large");

      for (const result of [
        missingOrigin,
        crossSite,
        navigationalSite,
        navigationMode,
        wrongMediaType,
        oversized,
      ]) {
        assertSecurityHeaders(result.response);
        assertGenericError(result);
      }
    });

    await t.test("never exposes credential material in the internal snapshot", async (st) => {
      if (!ownerSnapshot) return st.skip("Owner snapshot is unavailable.");
      const forbiddenKeys = findForbiddenSecretKeys(ownerSnapshot);
      assert.deepEqual(
        forbiddenKeys,
        [],
        `Snapshot exposed forbidden credential fields: ${forbiddenKeys.join(", ")}`,
      );
      assert.doesNotMatch(
        JSON.stringify(ownerSnapshot),
        /tmcra_sk_live_[A-Za-z0-9_:-]+\.[A-Za-z0-9_-]{20,}/,
        "A complete API credential must never appear in the internal snapshot.",
      );
    });

    await t.test(
      "rejects SQL-shaped IDs and every organization danger-confirmation bypass",
      async (st) => {
        if (!ownerSnapshot) return st.skip("Owner snapshot is unavailable.");
        const target = ownerSnapshot.organizations?.[0];
        const beforeOrganizations = organizationSecurityState(ownerSnapshot);

        const injection = await postAs(OWNER_EMAIL, "organization.set_status", {
          organizationId: "' OR 1=1 --",
          status: "archived",
          confirmSlug: "anything",
          reason: "Regression test must not mutate any tenant.",
          expectedVersion: 1,
        });
        assertError(injection, 422, "invalid_field");
        assertGenericError(injection);

        if (!target) {
          const afterInjection = await requireOwnerSnapshot();
          assert.deepEqual(organizationSecurityState(afterInjection), beforeOrganizations);
          ownerSnapshot = afterInjection;
          return st.skip("No organization exists for confirmation/version probes.");
        }

        const nextStatus = target.status === "archived" ? "active" : "archived";
        const basePayload = {
          organizationId: target.id,
          status: nextStatus,
          confirmSlug: target.slug,
          reason: "Security regression guard; no lifecycle change allowed.",
          expectedVersion: target.version,
        };

        const missingConfirmation = await postAs(
          OWNER_EMAIL,
          "organization.set_status",
          { ...basePayload, confirmSlug: undefined },
        );
        assertError(missingConfirmation, 422, "invalid_field");

        const wrongConfirmation = await postAs(
          OWNER_EMAIL,
          "organization.set_status",
          { ...basePayload, confirmSlug: `${target.slug}-wrong` },
        );
        assertError(wrongConfirmation, 422, "confirmation_mismatch");

        const shortReason = await postAs(OWNER_EMAIL, "organization.set_status", {
          ...basePayload,
          reason: "too short",
        });
        assertError(shortReason, 422, "invalid_field");

        const staleVersion = await postAs(OWNER_EMAIL, "organization.set_status", {
          ...basePayload,
          expectedVersion: target.version + 1,
        });
        assertError(staleVersion, 409, "version_conflict");

        for (const result of [
          missingConfirmation,
          wrongConfirmation,
          shortReason,
          staleVersion,
        ]) {
          assertGenericError(result);
        }

        const after = await requireOwnerSnapshot();
        assert.deepEqual(
          organizationSecurityState(after),
          beforeOrganizations,
          "Rejected organization operations must not change status or version.",
        );
        ownerSnapshot = after;
      },
    );

    await t.test(
      "requires exact-email invite acceptance and blocks support/admin escalation",
      async (st) => {
        if (!ownerSnapshot) return st.skip("Owner snapshot is unavailable.");

        const supportInvite = await postAs(OWNER_EMAIL, "staff.add", {
          email: supportEmail,
          displayName: "Security Test Support",
          role: "support",
        });
        assertOk(supportInvite);
        supportStaffId = supportInvite.json.staffId;

        const invitedRead = await apiRequest({ email: supportEmail });
        assertError(invitedRead, 403, "internal_invitation_pending");
        assertGenericError(invitedRead);

        const forcedActivation = await postAs(OWNER_EMAIL, "staff.update", {
          staffId: supportStaffId,
          status: "active",
        });
        assertError(forcedActivation, 409, "invitation_acceptance_required");

        const invitedEscalation = await postAs(supportEmail, "staff.add", {
          email: uniqueEmail("invited-escalation"),
          role: "platform_owner",
        });
        assertError(invitedEscalation, 403, "internal_invitation_pending");

        const acceptedSupport = await postAs(
          supportEmail,
          "staff.accept_invite",
          {},
        );
        assertOk(acceptedSupport);
        assert.equal(acceptedSupport.json.status, "active");

        const supportSnapshot = await apiRequest({ email: supportEmail });
        assertOk(supportSnapshot);
        assert.equal(supportSnapshot.json.actor.role, "support");
        assert.deepEqual(supportSnapshot.json.staff, []);
        assert.deepEqual(supportSnapshot.json.auditLogs, []);
        if (supportSnapshot.json.selectedOrganization) {
          assert.equal("members" in supportSnapshot.json.selectedOrganization, false);
          assert.equal("apiKeys" in supportSnapshot.json.selectedOrganization, false);
          assert.equal("recentEvents" in supportSnapshot.json.selectedOrganization, false);
        }

        const supportStaffMutation = await postAs(supportEmail, "staff.add", {
          email: uniqueEmail("support-escalation"),
          role: "platform_owner",
        });
        assertError(supportStaffMutation, 403, "forbidden");

        const supportOrganizationMutation = await postAs(
          supportEmail,
          "organization.set_status",
          { organizationId: "org_probe" },
        );
        assertError(supportOrganizationMutation, 403, "forbidden");

        const adminInvite = await postAs(OWNER_EMAIL, "staff.add", {
          email: adminEmail,
          displayName: "Security Test Admin",
          role: "platform_admin",
        });
        assertOk(adminInvite);
        adminStaffId = adminInvite.json.staffId;

        const acceptedAdmin = await postAs(adminEmail, "staff.accept_invite", {});
        assertOk(acceptedAdmin);

        const adminCreatesOwner = await postAs(adminEmail, "staff.add", {
          email: uniqueEmail("admin-created-owner"),
          role: "platform_owner",
        });
        assertError(adminCreatesOwner, 403, "forbidden");

        const adminDemotesOwner = await postAs(adminEmail, "staff.update", {
          staffId: ownerSnapshot.actor.id,
          role: "analyst",
        });
        assertError(adminDemotesOwner, 403, "forbidden");

        const adminRemovesOwner = await postAs(adminEmail, "staff.remove", {
          staffId: ownerSnapshot.actor.id,
          confirmEmail: OWNER_EMAIL,
        });
        assertError(adminRemovesOwner, 403, "forbidden");

        const adminArchivesTenant = await postAs(
          adminEmail,
          "organization.set_status",
          { organizationId: "org_probe" },
        );
        assertError(adminArchivesTenant, 403, "forbidden");

        for (const result of [
          forcedActivation,
          invitedEscalation,
          supportStaffMutation,
          supportOrganizationMutation,
          adminCreatesOwner,
          adminDemotesOwner,
          adminRemovesOwner,
          adminArchivesTenant,
        ]) {
          assertGenericError(result);
        }

        const after = await requireOwnerSnapshot();
        assert.equal(
          after.staff.some(
            (member) =>
              member.email === uniqueEmail("admin-created-owner") ||
              member.email === uniqueEmail("support-escalation"),
          ),
          false,
          "Rejected role escalation must not create a staff record.",
        );
        assert.equal(after.actor.role, "platform_owner");
        assert.equal(after.actor.status, "active");
        ownerSnapshot = after;
      },
    );

    await t.test("does not expose an audit update/delete mutation path", async (st) => {
      if (!ownerSnapshot) return st.skip("Owner snapshot is unavailable.");
      const protectedEntry = ownerSnapshot.auditLogs?.[0];
      if (!protectedEntry) return st.skip("No audit entry exists to compare.");

      const deleteAttempt = await postAs(OWNER_EMAIL, "audit.delete", {
        auditId: protectedEntry.id,
      });
      assertError(deleteAttempt, 400, "unknown_action");
      assertGenericError(deleteAttempt);

      const updateAttempt = await postAs(OWNER_EMAIL, "audit.update", {
        auditId: protectedEntry.id,
        action: "tampered",
      });
      assertError(updateAttempt, 400, "unknown_action");
      assertGenericError(updateAttempt);

      const after = await requireOwnerSnapshot();
      const sameEntry = after.auditLogs.find((entry) => entry.id === protectedEntry.id);
      assert.deepEqual(
        sameEntry,
        protectedEntry,
        "An existing audit record changed after an unsupported mutation attempt.",
      );
      ownerSnapshot = after;
    });

    await t.test("removes only records created by this run", async (st) => {
      if (!ownerSnapshot) return st.skip("Owner snapshot is unavailable.");

      for (const [staffId, email] of [
        [supportStaffId, supportEmail],
        [adminStaffId, adminEmail],
      ]) {
        if (!staffId) continue;
        const removal = await postAs(OWNER_EMAIL, "staff.remove", {
          staffId,
          confirmEmail: email,
        });
        assertOk(removal);
      }

      const after = await requireOwnerSnapshot();
      assert.equal(
        after.staff.some((member) =>
          [supportEmail, adminEmail].includes(member.email.toLowerCase()),
        ),
        false,
      );
      ownerSnapshot = after;
    });

    await t.test("database guard preserves the final active platform owner", async (st) => {
      if (!ownerSnapshot) return st.skip("Owner snapshot is unavailable.");
      const activeOwners = ownerSnapshot.staff.filter(
        (member) => member.role === "platform_owner" && member.status === "active",
      );
      if (
        activeOwners.length !== 1 ||
        activeOwners[0].id !== ownerSnapshot.actor.id
      ) {
        return st.skip(
          "The reused database has multiple active owners; a destructive final-owner probe is unsafe.",
        );
      }

      const demotion = await postAs(OWNER_EMAIL, "staff.update", {
        staffId: ownerSnapshot.actor.id,
        role: "analyst",
      });
      assertError(demotion, 409, "last_platform_owner");
      assertGenericError(demotion);

      const removal = await postAs(OWNER_EMAIL, "staff.remove", {
        staffId: ownerSnapshot.actor.id,
        confirmEmail: OWNER_EMAIL,
      });
      assertError(removal, 409, "last_platform_owner");
      assertGenericError(removal);

      const after = await requireOwnerSnapshot();
      assert.equal(after.actor.role, "platform_owner");
      assert.equal(after.actor.status, "active");
      assert.ok(
        after.staff.some(
          (member) =>
            member.id === after.actor.id &&
            member.role === "platform_owner" &&
            member.status === "active",
        ),
      );
      ownerSnapshot = after;
    });

    await t.test("D1 mutation limiter eventually returns 429", async (st) => {
      if (!ownerSnapshot) return st.skip("Owner snapshot is unavailable.");
      const configuredLimit = ownerSnapshot.system?.mutationLimitPerMinute;
      assert.equal(configuredLimit, 30);

      let limited = null;
      // Two buckets plus a small margin makes this deterministic even if the
      // test crosses a natural minute boundary midway through the loop.
      for (let index = 0; index < configuredLimit * 2 + 5; index += 1) {
        const result = await postAs(OWNER_EMAIL, "security.rate_probe", {
          attempt: index,
        });
        if (result.response.status === 429) {
          limited = result;
          break;
        }
        assertError(result, 400, "unknown_action");
      }

      assert.ok(limited, "The D1-backed mutation limiter never returned 429.");
      assertError(limited, 429, "rate_limited");
      assertGenericError(limited);
      assertSecurityHeaders(limited.response);
      assert.equal(limited.response.headers.get("retry-after"), "60");
    });
  },
);

async function requireOwnerSnapshot() {
  const result = await apiRequest({ email: OWNER_EMAIL });
  assertOk(result);
  return result.json;
}

function postAs(email, action, payload) {
  return apiRequest({ method: "POST", email, action, payload });
}

async function apiRequest({
  method = "GET",
  email,
  action,
  payload = {},
  query,
  origin = method === "POST" ? EXPECTED_ORIGIN : undefined,
  fetchSite = method === "POST" ? "same-origin" : undefined,
  fetchMode,
  contentType = method === "POST" ? "application/json" : undefined,
  rawBody,
  headers: extraHeaders = {},
} = {}) {
  const url = new URL(API_URL);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
  }

  const headers = new Headers(extraHeaders);
  if (email) {
    headers.set("oai-authenticated-user-email", email);
    headers.set(
      "oai-authenticated-user-full-name",
      encodeURIComponent(`TMCRA Security ${RUN_ID}`),
    );
    headers.set(
      "oai-authenticated-user-full-name-encoding",
      "percent-encoded-utf-8",
    );
  }
  if (origin !== undefined && origin !== null) headers.set("origin", origin);
  if (fetchSite !== undefined && fetchSite !== null) {
    headers.set("sec-fetch-site", fetchSite);
  }
  if (fetchMode !== undefined && fetchMode !== null) {
    headers.set("sec-fetch-mode", fetchMode);
  }
  if (contentType !== undefined && contentType !== null) {
    headers.set("content-type", contentType);
  }

  const body =
    method === "POST"
      ? rawBody ?? JSON.stringify({ action: action ?? "security.probe", payload })
      : undefined;
  // Fetch implementations overwrite Sec-Fetch-Mode, so use a raw local HTTP
  // request for the form-navigation probe to preserve the browser header.
  const response = fetchMode === "navigate"
    ? await rawLocalRequest(url, { method, headers, body })
    : await fetch(url, {
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
    // Assertions provide the raw response if a JSON contract regresses.
  }
  return { response, text, json };
}

function rawLocalRequest(url, { method, headers, body }) {
  return new Promise((resolve, reject) => {
    const request = httpRequest(
      url,
      { method, headers: Object.fromEntries(headers.entries()) },
      (incoming) => {
        const chunks = [];
        incoming.on("data", (chunk) => chunks.push(chunk));
        incoming.on("end", () => {
          const responseHeaders = new Headers();
          for (const [key, value] of Object.entries(incoming.headers)) {
            if (Array.isArray(value)) {
              value.forEach((entry) => responseHeaders.append(key, entry));
            } else if (value !== undefined) {
              responseHeaders.set(key, value);
            }
          }
          resolve(
            new Response(Buffer.concat(chunks), {
              status: incoming.statusCode ?? 500,
              statusText: incoming.statusMessage,
              headers: responseHeaders,
            }),
          );
        });
      },
    );
    request.on("error", reject);
    if (body !== undefined) request.write(body);
    request.end();
  });
}

function assertOk(result) {
  assert.equal(
    result.response.status,
    200,
    `Expected 200, received ${result.response.status}: ${result.text}`,
  );
  assert.equal(result.json?.ok, true, result.text);
  assertSecurityHeaders(result.response);
}

function assertError(result, status, code) {
  assert.equal(
    result.response.status,
    status,
    `Expected ${status}/${code}, received ${result.response.status}: ${result.text}`,
  );
  assert.equal(result.json?.ok, false, result.text);
  assert.equal(result.json?.error?.code, code, result.text);
  assert.equal(typeof result.json?.error?.message, "string", result.text);
  assert.ok(result.json.error.message.length > 0, result.text);
  assert.equal(typeof result.json?.error?.requestId, "string", result.text);
  assert.ok(result.json.error.requestId.length > 0, result.text);
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

function assertGenericError(result) {
  assert.deepEqual(
    Object.keys(result.json?.error ?? {}).sort(),
    ["code", "message", "requestId"],
    result.text,
  );
  assert.doesNotMatch(
    result.text,
    /D1_ERROR|SQLITE|\b(?:SELECT|INSERT|UPDATE|DELETE FROM|CREATE TABLE|DROP TABLE)\b|internal_(?:staff|meta|audit_logs?)\b|constraint failed|stack trace|\.(?:ts|js):\d+/i,
    "Error response leaked database, schema, query, or stack details.",
  );
}

function securityCounts(snapshot) {
  const stableMetrics = { ...snapshot.metrics };
  delete stableMetrics.updatedAt;
  return {
    metrics: stableMetrics,
    organizations: snapshot.organizations.map((organization) => organization.id).sort(),
    staff: snapshot.staff
      .map((member) => `${member.id}:${member.email.toLowerCase()}:${member.role}:${member.status}`)
      .sort(),
  };
}

function organizationSecurityState(snapshot) {
  return snapshot.organizations
    .map((organization) => ({
      id: organization.id,
      slug: organization.slug,
      status: organization.status,
      version: organization.version,
    }))
    .sort((left, right) => left.id.localeCompare(right.id));
}

function findForbiddenSecretKeys(value, path = "$", found = []) {
  if (!value || typeof value !== "object") return found;
  if (Array.isArray(value)) {
    value.forEach((entry, index) =>
      findForbiddenSecretKeys(entry, `${path}[${index}]`, found),
    );
    return found;
  }

  const forbidden = new Set([
    "password",
    "passwordhash",
    "password_hash",
    "secret",
    "secrethash",
    "secret_hash",
    "token",
    "tokenhash",
    "token_hash",
    "apikeyhash",
    "api_key_hash",
    "credential",
    "credentials",
  ]);
  for (const [key, entry] of Object.entries(value)) {
    const normalized = key.toLowerCase();
    if (forbidden.has(normalized)) found.push(`${path}.${key}`);
    findForbiddenSecretKeys(entry, `${path}.${key}`, found);
  }
  return found;
}

function withTrailingSlash(value) {
  return value.endsWith("/") ? value : `${value}/`;
}
