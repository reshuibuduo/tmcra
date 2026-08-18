import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("published package exposes compiled JavaScript and declarations", async () => {
  const packageJson = JSON.parse(
    await readFile(new URL("../package.json", import.meta.url), "utf8"),
  ) as Record<string, unknown>;
  assert.equal(packageJson.main, "./dist/index.js");
  assert.equal(packageJson.types, "./dist/index.d.ts");
  assert.deepEqual(packageJson.publishConfig, { access: "public" });

  const module = await import("../dist/index.js");
  assert.equal(typeof module.TMCRAClient, "function");
  assert.equal(typeof module.TMCRAMemoryLifecycle, "function");
  assert.equal(typeof module.PreparedTurn, "function");
});
