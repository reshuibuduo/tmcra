import test from "node:test";
import assert from "node:assert/strict";

import {
  stateEventBeforeInstallerExit,
  stateEventForInstallerExit,
} from "../src/lib/installer-policy.mjs";

test("authorization completion starts remote verification but never marks connected", () => {
  assert.deepEqual(
    stateEventBeforeInstallerExit({
      type: "progress",
      step: "authorization",
      status: "completed",
    }),
    { type: "remote_verification" },
  );
  assert.equal(stateEventBeforeInstallerExit({ type: "complete" }), null);
});

test("only a successful final installer exit produces the connected event", () => {
  assert.deepEqual(stateEventForInstallerExit(0), { type: "complete" });
  assert.equal(stateEventForInstallerExit(1).type, "error");
  assert.equal(stateEventForInstallerExit(null).type, "error");
});
