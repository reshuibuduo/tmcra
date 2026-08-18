import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const config = JSON.parse(
  await readFile(new URL("../capacitor.config.json", import.meta.url), "utf8"),
);

test("mobile client uses the production HTTPS personal console", () => {
  assert.equal(config.appId, "com.tmcra.memory.mobile");
  assert.equal(config.server.url, "https://tmcra.com/personal");
  assert.equal(config.server.cleartext, false);
  assert.deepEqual(config.server.allowNavigation, ["tmcra.com"]);
  assert.equal(config.android.allowMixedContent, false);
});
