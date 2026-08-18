import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

test("minimum window width does not force the two-column layout to overflow", async () => {
  const css = await readFile(resolve(root, "src", "renderer", "styles.css"), "utf8");

  assert.match(
    css,
    /\.layout\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1\.12fr\)\s+minmax\(320px,\s*0\.88fr\)/s,
  );
  assert.doesNotMatch(css, /grid-template-columns:\s*minmax\(510px,[^;]+minmax\(390px,/);
});

test("installer status labels remain readable", async () => {
  const css = await readFile(resolve(root, "src", "renderer", "styles.css"), "utf8");

  for (const selector of [
    ".connection-pill",
    ".version",
    ".panel-heading > div > span",
    ".step-count",
    ".step-index",
    ".step-status",
  ]) {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    assert.match(css, new RegExp(`${escaped}[^}]*font(?:-size)?:[^;]*12px`, "s"));
  }
});

test("desktop header uses the official TMCRA mark and keeps its text label", async () => {
  const html = await readFile(resolve(root, "src", "renderer", "index.html"), "utf8");
  const css = await readFile(resolve(root, "src", "renderer", "styles.css"), "utf8");

  assert.match(html, /aria-label="TMCRA Memory"/);
  assert.match(html, /<img src="\.\/assets\/tmcra-mark\.png" alt="" \/>/);
  assert.match(html, /<b>TMCRA<\/b>/);
  assert.doesNotMatch(html, /brand-mark[^>]*><i>/);
  assert.match(css, /\.brand-mark img\s*\{/);
});
