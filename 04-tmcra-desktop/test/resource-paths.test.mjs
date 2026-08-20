import test from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";

import {
  resolveProductResourcePath,
  resolveProductScriptPath,
} from "../src/lib/resource-paths.mjs";

test("packaged PowerShell scripts resolve to physical extraResources paths", () => {
  const context = {
    isPackaged: true,
    resourcesPath: "C:\\Program Files\\TMCRA Memory\\resources",
    sourceRoot: "C:\\source",
  };
  assert.equal(
    resolveProductScriptPath(context, "expand-plugin.ps1"),
    join(context.resourcesPath, "desktop-scripts", "expand-plugin.ps1"),
  );
  assert.equal(
    resolveProductResourcePath(context, "tmcra-codex-latest.zip"),
    join(context.resourcesPath, "tmcra-codex-latest.zip"),
  );
});

test("development resources stay under their dedicated source directories", () => {
  const context = {
    isPackaged: false,
    resourcesPath: "C:\\packaged",
    sourceRoot: "C:\\source",
  };
  assert.equal(
    resolveProductScriptPath(context, "find-codex.ps1"),
    join(context.sourceRoot, "scripts", "find-codex.ps1"),
  );
  assert.throws(
    () => resolveProductScriptPath(context, "..\\escape.ps1"),
    /plain filename/u,
  );
  assert.throws(
    () => resolveProductScriptPath(context, "../escape.ps1"),
    /plain filename/u,
  );
});
