import test from "node:test";
import assert from "node:assert/strict";

import { NdjsonLineBuffer, parseInstallerEvent } from "../src/lib/progress-events.mjs";

const authorizationBaseUrl = "https://account.tmcra.test";

test("progress events are normalized to the public step contract", () => {
  assert.deepEqual(
    parseInstallerEvent(
      JSON.stringify({
        event: "tmcra.install.progress",
        step: "authorize",
        status: "running",
        message: "Waiting for approval",
        accessToken: "<access-token>",
      }),
      { authorizationBaseUrl },
    ),
    {
      type: "progress",
      step: "authorization",
      status: "running",
      message: "Waiting for approval",
    },
  );
});

test("authorization event exposes only a safe code, URL and expiry", () => {
  const event = parseInstallerEvent(
    JSON.stringify({
      event: "tmcra.authorization.required",
      userCode: "ABCD-EFGH",
      verificationUrl: "https://account.tmcra.test/console/connect/codex?user_code=ABCD-EFGH",
      expiresAt: "2026-07-17T12:00:00.000Z",
      deviceCode: "secret-device-code",
      codeVerifier: "secret-verifier",
      deliveryReceipt: "secret-receipt",
    }),
    { authorizationBaseUrl },
  );

  assert.deepEqual(event, {
    type: "authorization_required",
    userCode: "ABCD-EFGH",
    verificationUrl: "https://account.tmcra.test/console/connect/codex?user_code=ABCD-EFGH",
    expiresAt: "2026-07-17T12:00:00.000Z",
  });
  assert.equal("deviceCode" in event, false);
  assert.equal("codeVerifier" in event, false);
});

test("unknown, plaintext and cross-origin events are ignored", () => {
  assert.equal(parseInstallerEvent("Waiting for approval", { authorizationBaseUrl }), null);
  assert.equal(
    parseInstallerEvent(JSON.stringify({ event: "debug", accessToken: "secret" }), {
      authorizationBaseUrl,
    }),
    null,
  );
  assert.equal(
    parseInstallerEvent(
      JSON.stringify({
        event: "tmcra.authorization.required",
        userCode: "ABCD-EFGH",
        verificationUrl: "https://evil.test/console/connect/codex",
      }),
      { authorizationBaseUrl },
    ),
    null,
  );
});

test("NDJSON buffer handles split chunks and CRLF without returning partial lines", () => {
  const buffer = new NdjsonLineBuffer();
  assert.deepEqual(buffer.push('{"event":"complete"'), []);
  assert.deepEqual(buffer.push("}\r\nplain\npartial"), [
    '{"event":"complete"}',
    "plain",
  ]);
  assert.deepEqual(buffer.flush(), ["partial"]);
});
