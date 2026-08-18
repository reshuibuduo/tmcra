import test from "node:test";
import assert from "node:assert/strict";

import {
  allowedRemoteOrigins,
  buildConsoleUrl,
  normalizeAuthorizationBaseUrl,
  validateRemoteNavigation,
  validateVerificationUrl,
} from "../src/lib/security.mjs";

const AUTHORIZATION_BASE = "https://account.tmcra.test:8443";

test("verification URL requires HTTPS, the exact origin and the Codex path", () => {
  assert.equal(
    validateVerificationUrl(
      "https://account.tmcra.test:8443/console/connect/codex?user_code=ABCD-EFGH",
      AUTHORIZATION_BASE,
    ),
    "https://account.tmcra.test:8443/console/connect/codex?user_code=ABCD-EFGH",
  );

  assert.throws(
    () => validateVerificationUrl("http://account.tmcra.test:8443/console/connect/codex", AUTHORIZATION_BASE),
    /HTTPS/u,
  );
  assert.throws(
    () => validateVerificationUrl("https://evil.test/console/connect/codex", AUTHORIZATION_BASE),
    /unexpected origin/u,
  );
  assert.throws(
    () => validateVerificationUrl("https://account.tmcra.test:8443/personal", AUTHORIZATION_BASE),
    /unexpected path/u,
  );
  assert.throws(
    () => validateVerificationUrl("https://" + "user:pass@" + "account.tmcra.test:8443/console/connect/codex", AUTHORIZATION_BASE),
    /embedded credentials/u,
  );
});

test("authorization and console URLs stay inside the configured origin", () => {
  assert.equal(
    normalizeAuthorizationBaseUrl(`${AUTHORIZATION_BASE}/`),
    AUTHORIZATION_BASE,
  );
  assert.equal(buildConsoleUrl(AUTHORIZATION_BASE, "/personal"), `${AUTHORIZATION_BASE}/personal`);
  assert.throws(() => buildConsoleUrl(AUTHORIZATION_BASE, "//evil.test/personal"), /root-relative/u);
});

test("remote navigation accepts only explicitly allowed HTTPS origins", () => {
  const origins = allowedRemoteOrigins("https://account.tmcra.test", ["https://auth.openai.com"]);
  assert.equal(
    validateRemoteNavigation("https://account.tmcra.test/personal", origins),
    "https://account.tmcra.test/personal",
  );
  assert.equal(
    validateRemoteNavigation("https://auth.openai.com/authorize", origins),
    "https://auth.openai.com/authorize",
  );
  assert.throws(
    () => validateRemoteNavigation("https://account.tmcra.test.evil.example/personal", origins),
    /not allowed/u,
  );
  assert.throws(
    () => validateRemoteNavigation("file:///C:/Windows/System32/drivers/etc/hosts", origins),
    /HTTPS/u,
  );
});
