import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

const root = resolve(import.meta.dirname, "..");
const pngSignature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

test("site branding uses the official TMCRA artwork", async () => {
  const component = await readFile(resolve(root, "app", "BrandMark.tsx"), "utf8");
  const layout = await readFile(resolve(root, "app", "layout.tsx"), "utf8");

  assert.match(component, /src="\/brand\/tmcra-mark\.png"/);
  assert.doesNotMatch(component, /tmcra-mark-(?:frame|axis|core|node)/);
  assert.match(layout, /\/brand\/tmcra-app-icon\.png/);

  for (const asset of ["tmcra-logo.png", "tmcra-mark.png", "tmcra-app-icon.png"]) {
    const bytes = await readFile(resolve(root, "public", "brand", asset));
    assert.ok(bytes.length > 1024, `${asset} should contain the production artwork`);
    assert.deepEqual(bytes.subarray(0, pngSignature.length), pngSignature, `${asset} should be a PNG`);
  }
});

test("homepage continuity narrative keeps traceable records in semantic DOM", async () => {
  const page = await readFile(resolve(root, "app", "page.tsx"), "utf8");
  const scene = await readFile(resolve(root, "app", "components", "brand", "ContinuityCutScene.tsx"), "utf8");
  const recall = await readFile(resolve(root, "app", "components", "brand", "ContinuityCutRecall.tsx"), "utf8");

  assert.match(page, /Continue work/);
  assert.match(page, /跨对话/);
  assert.match(page, /继续工作。/);
  assert.match(page, /ContinuityCutScene/);
  assert.match(page, /ContinuityCutRecall/);
  assert.match(scene, /USER GLOBAL/);
  assert.match(scene, /PROJECT \/ memory-sdk/);
  assert.match(recall, /Session remains a grouping inside Project/);
  assert.match(recall, /actor: "USER" \| "AGENT"/);
  assert.match(recall, /layer: "SOURCE" \| "FAST" \| "SLOW"/);
  assert.match(scene, /<canvas[^>]+aria-hidden="true"/);
  assert.doesNotMatch(page, /TopologyCanvas/);
});

test("public benchmark copy keeps LoCoMo protocols and denominators separate", async () => {
  const homepage = await readFile(resolve(root, "app", "page.tsx"), "utf8");
  const benchmarkPage = await readFile(resolve(root, "app", "benchmarks", "page.tsx"), "utf8");

  for (const source of [homepage, benchmarkPage]) {
    assert.match(source, /80\.92/);
    assert.match(source, /MEM0-STYLE LLM JUDGE/);
    assert.match(source, /1,540/);
    assert.match(source, /1,986/);
    assert.match(source, /55\.20/);
    assert.match(source, /82\.00/);
  }

  assert.match(benchmarkPage, /auxiliary five-run mean/);
  assert.match(benchmarkPage, /does not include Category 5/);
});

test("product page retains the former site comparison and deployment contexts", async () => {
  const productPage = await readFile(resolve(root, "app", "product", "page.tsx"), "utf8");

  assert.match(productPage, /Context Window/);
  assert.match(productPage, /Vector RAG/);
  assert.match(productPage, /PERSONAL AI/);
  assert.match(productPage, /AUTONOMOUS AGENTS/);
  assert.match(productPage, /ENTERPRISE ASSISTANTS/);
  assert.match(productPage, /EMBODIED AI/);
});
