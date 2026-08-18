import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

test("package and manifest point at the compiled hook entry", async () => {
  const packageJson = JSON.parse(await readFile(new URL("../package.json", import.meta.url)));
  const manifest = JSON.parse(await readFile(new URL("../openclaw.plugin.json", import.meta.url)));
  assert.deepEqual(packageJson.openclaw.extensions, ["./dist/index.js"]);
  assert.equal(packageJson.openclaw.runtimeExtensions, undefined);
  assert.equal(packageJson.openclaw.install.minHostVersion, ">=2026.7.1-2");
  assert.equal(manifest.id, "tmcra-openclaw");
  assert.equal(manifest.version, packageJson.version);
  assert.equal(manifest.activation.onStartup, true);
  assert.ok(manifest.activation.onCapabilities.includes("hook"));
  assert.equal(manifest.configSchema.properties.baseUrl.default, "https://api.tmcra.com");

  const entry = await import("../dist/index.js");
  assert.equal(entry.default.id, manifest.id);
  assert.equal(typeof entry.default.register, "function");
});
