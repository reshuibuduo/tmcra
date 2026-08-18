import test from "node:test";
import assert from "node:assert/strict";

import {
  createInstallState,
  normalizePublicState,
  reduceInstallState,
} from "../src/lib/install-state.mjs";

function step(state, id) {
  return state.steps.find((entry) => entry.id === id);
}

test("full setup finishes connected while leaving Hook trust to the user", () => {
  let state = reduceInstallState(createInstallState(), { type: "start" });
  assert.equal(state.phase, "checking");
  assert.equal(step(state, "environment").status, "running");

  state = reduceInstallState(state, {
    type: "progress",
    step: "plugin",
    status: "running",
  });
  assert.equal(step(state, "environment").status, "completed");

  state = reduceInstallState(state, {
    type: "authorization_required",
    userCode: "ABCD-EFGH",
    verificationUrl: "https://account.tmcra.test/console/connect/codex?user_code=ABCD-EFGH",
    expiresAt: "2026-07-17T12:00:00.000Z",
  });
  assert.equal(state.phase, "awaiting_authorization");
  assert.equal(step(state, "authorization").status, "action_required");

  state = reduceInstallState(state, { type: "remote_verification" });
  assert.equal(state.phase, "verifying_remote");
  assert.equal(state.connected, false);
  assert.equal(step(state, "authorization").status, "running");

  state = reduceInstallState(state, {
    type: "progress",
    step: "authorization",
    status: "completed",
  });
  assert.equal(state.connected, false, "authorization progress alone is not a remote service check");

  state = reduceInstallState(state, { type: "complete" });
  assert.equal(state.phase, "connected_pending_hooks");
  assert.equal(state.connected, true);
  assert.equal(step(state, "hooks").status, "action_required");

  state = reduceInstallState(state, { type: "acknowledge_hooks" });
  assert.equal(state.phase, "ready");
  assert.equal(step(state, "hooks").status, "completed");
});

test("public state normalization drops all unknown and credential fields", () => {
  const state = normalizePublicState({
    phase: "ready",
    connected: true,
    busy: false,
    accessToken: "secret-token",
    deviceCode: "secret-device-code",
    steps: [{ id: "hooks", status: "completed", token: "secret" }],
    authorization: {
      userCode: "ABCD-EFGH",
      verificationUrl: "https://account.tmcra.test/console/connect/codex",
      codeVerifier: "secret-verifier",
    },
  });

  assert.equal("accessToken" in state, false);
  assert.equal("deviceCode" in state, false);
  assert.equal("codeVerifier" in state.authorization, false);
  assert.deepEqual(step(state, "hooks"), { id: "hooks", status: "completed" });
});

test("an install error marks the active step and becomes retryable", () => {
  let state = reduceInstallState(createInstallState(), { type: "start" });
  state = reduceInstallState(state, {
    type: "error",
    code: "codex_not_found",
    message: "Codex was not found",
  });
  assert.equal(state.phase, "error");
  assert.equal(state.busy, false);
  assert.equal(step(state, "environment").status, "failed");
  assert.deepEqual(state.error, {
    code: "codex_not_found",
    message: "Codex was not found",
  });
});

test("startup restoration stays disconnected until the real remote probe exits successfully", () => {
  let state = reduceInstallState(createInstallState(), { type: "start" });
  state = reduceInstallState(state, {
    type: "progress",
    step: "plugin",
    status: "completed",
  });
  state = reduceInstallState(state, { type: "remote_verification" });
  assert.equal(state.phase, "verifying_remote");
  assert.equal(state.connected, false);

  const failed = reduceInstallState(state, {
    type: "error",
    code: "remote_probe_failed",
    message: "Remote probe failed",
  });
  assert.equal(failed.connected, false);
  assert.equal(failed.phase, "error");

  state = reduceInstallState(state, { type: "complete" });
  assert.equal(state.connected, true);
  assert.equal(state.phase, "connected_pending_hooks");
  state = reduceInstallState(state, { type: "acknowledge_hooks" });
  assert.equal(state.phase, "ready");
});
