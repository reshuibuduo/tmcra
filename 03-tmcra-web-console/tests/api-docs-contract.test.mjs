import assert from "node:assert/strict";
import fs from "node:fs";
import crypto from "node:crypto";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), "utf8");

test("bilingual API docs cover every published OpenAPI operation", () => {
  const spec = JSON.parse(read("public/openapi.json"));
  const content = read("app/docs/docs-content.ts");
  const page = read("app/docs/page.tsx");
  const methods = new Set(["get", "post", "put", "delete", "patch"]);
  const operations = [];

  for (const [apiPath, pathItem] of Object.entries(spec.paths)) {
    for (const method of Object.keys(pathItem)) {
      if (methods.has(method)) operations.push([method.toUpperCase(), apiPath]);
    }
  }

  assert.equal(spec.info.title, "TMCRA Memory API");
  assert.equal(spec.info.version, "0.2.0");
  assert.equal(spec.servers[0].url, "https://api.tmcra.com");
  assert.equal(spec.paths["/v1/session"].get.operationId, "getAuthenticatedSession");
  assert.equal(operations.length, 32);
  for (const [method, apiPath] of operations) {
    assert.match(content, new RegExp(`method: \\"${method}\\"[\\s\\S]{0,120}path: \\"${apiPath.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\"`));
  }

  assert.match(page, /API 参考/);
  assert.match(content, /记忆图谱/);
  assert.match(page, /Search endpoints, paths, concepts/);
  assert.match(page, /搜索端点、路径或概念/);
  assert.match(page, /href="\/openapi\.json"/);
});

test("public integration copy distinguishes stable, preview, and pilot availability", () => {
  const developers = read("app/developers/page.tsx");
  const access = read("app/access/page.tsx");
  const accessRoute = read("app/api/access/route.ts");
  const codex = read("app/developers/codex/page.tsx");
  const consoleClient = read("app/console/ConsoleClient.tsx");
  const docs = read("app/docs/page.tsx");

  assert.match(developers, /REST \/ OpenAPI[\s\S]{0,180}STABLE/);
  assert.match(developers, /Python SDK[\s\S]{0,180}PREVIEW/);
  assert.match(developers, /Codex[\s\S]{0,180}PREVIEW/);
  assert.match(developers, /OpenClaw[\s\S]{0,180}PILOT/);
  assert.match(developers, /Hermes Agent[\s\S]{0,180}PILOT/);
  assert.doesNotMatch(developers, /SUPPORTED TODAY/);

  for (const platform of ["REST / OpenAPI", "Python SDK", "TypeScript SDK", "MCP Server", "Codex", "OpenClaw", "Hermes Agent"]) {
    assert.match(access, new RegExp(platform.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    assert.match(accessRoute, new RegExp(platform.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }

  assert.match(codex, /\/downloads\/tmcra-codex-latest\.zip/);
  assert.doesNotMatch(codex, /tmcra-codex-0\.1\.3/);
  assert.match(consoleClient, /className="topbar-link" href="\/docs">Docs<\/Link>/);
  assert.match(docs, /promptEvidence !== null/);
  assert.match(docs, /typeof promptEvidence\.content === "string"/);
  assert.match(docs, /PILOT SOURCE/);
});

test("automatic integration guide documents the real lifecycle and multi-Agent boundaries", () => {
  const developers = read("app/developers/page.tsx");
  const automatic = read("app/developers/automatic-memory/page.tsx");

  for (const target of [
    "/developers/automatic-memory#python",
    "/developers/automatic-memory#javascript-typescript",
    "/developers/automatic-memory#mcp",
    "/developers/automatic-memory#openclaw",
    "/developers/automatic-memory#hermes",
  ]) {
    assert.match(developers, new RegExp(target.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }

  const question = automatic.indexOf("The user submits the new prompt");
  const recall = automatic.indexOf("Evidence is recalled, fenced, and injected");
  const write = automatic.indexOf("The completed turn is persisted");
  assert.ok(question >= 0 && recall > question && write > recall);
  assert.match(automatic, /用户还没有提出问题时，系统并不知道这一轮该召回什么/);
  assert.match(automatic, /用户消息与助手最终回复作为两个不同主体分别写入/);

  assert.match(automatic, /User global/);
  assert.match(automatic, /Shared project/);
  assert.match(automatic, /Current-Agent private/);
  assert.match(automatic, /Off by default and recall-only/);
  assert.match(automatic, /Automatic writes still go to the shared project scope/);
  assert.match(automatic, /target_agent_id/);
  assert.match(automatic, /agent_id/);
  assert.match(automatic, /SCOPE IS NOT SESSION/);

  assert.match(automatic, /before_prompt_build/);
  assert.match(automatic, /agent_end/);
  assert.match(automatic, /sharedProjectId/);
  assert.match(automatic, /prefetch \/ queue_prefetch/);
  assert.match(automatic, /sync_turn/);
  assert.match(automatic, /on_delegation/);
  assert.match(automatic, /SyncMemoryLifecycle/);
  assert.match(automatic, /AsyncMemoryLifecycle/);
  assert.match(automatic, /TMCRAMemoryLifecycle/);
  assert.match(automatic, /agentPrivateScope/);

  assert.match(automatic, /Generic MCP is explicit/);
  assert.match(automatic, /Connecting stdio alone cannot observe a host’s turns/);
  assert.match(automatic, /tmcra-mcp-setup install --mode explicit/);
  assert.match(automatic, /tmcra-mcp-setup install --mode codex-hooks/);
  assert.match(automatic, /python -m pip install https:\/\/tmcra\.com\/downloads\/integrations\/tmcra_client-0\.5\.0-py3-none-any\.whl/);
  assert.match(automatic, /npm install https:\/\/tmcra\.com\/downloads\/integrations\/tmcra-typescript-0\.5\.0\.tgz/);
  assert.match(automatic, /python -m pip install https:\/\/tmcra\.com\/downloads\/integrations\/tmcra_mcp_server-0\.4\.0-py3-none-any\.whl/);
  assert.doesNotMatch(automatic, /<code>python -m pip install tmcra-client<\/code>/);
  assert.doesNotMatch(automatic, /<code>npm install @tmcra\/typescript<\/code>/);
  assert.doesNotMatch(automatic, /python -m pip install tmcra-mcp-server/);
  assert.doesNotMatch(automatic, /Generic MCP is automatic/);
});

test("documentation language follows the system until a manual preference exists", () => {
  const i18n = read("app/i18n.tsx");
  const layout = read("app/layout.tsx");
  const shell = read("app/MarketingShell.tsx");

  assert.match(i18n, /navigator\.language/);
  assert.match(i18n, /addEventListener\("languagechange"/);
  assert.match(i18n, /manualPreference !== "zh" && manualPreference !== "en"/);
  assert.match(i18n, /LANGUAGE_PREFERENCE_KEY = "tmcra-language-preference"/);
  assert.match(i18n, /localStorage\.setItem\(LANGUAGE_PREFERENCE_KEY, nextLanguage\)/);
  assert.match(layout, /get\("accept-language"\)/);
  assert.match(layout, /<LanguageProvider initialLanguage=\{initialLanguage\}>/);
  assert.match(shell, /\{ en: "API Docs", zh: "API 文档" \}, "\/docs"/);
});

test("published documentation contains no embedded production credential", () => {
  const files = [
    read("app/docs/page.tsx"),
    read("app/docs/docs-content.ts"),
    read("app/developers/automatic-memory/page.tsx"),
    read("public/openapi.json"),
  ].join("\n");

  assert.doesNotMatch(files, /tmcra_[A-Za-z0-9_-]{20,}/);
  assert.doesNotMatch(files, /sk-[A-Za-z0-9_-]{20,}/);
  assert.doesNotMatch(files, /Authorization:\s*Bearer\s+(?!\$|<)[A-Za-z0-9_-]{12,}/);
});

test("published integration downloads match their machine-readable checksums", () => {
  const automatic = read("app/developers/automatic-memory/page.tsx");
  const directory = path.join(root, "public", "downloads", "integrations");
  const manifest = JSON.parse(fs.readFileSync(path.join(directory, "manifest.json"), "utf8"));
  assert.equal(manifest.schema_version, "tmcra.integration-artifacts.1");
  assert.equal(manifest.artifacts.length, 8);
  for (const artifact of manifest.artifacts) {
    const artifactPath = path.join(directory, artifact.path);
    const payload = fs.readFileSync(artifactPath);
    assert.equal(payload.byteLength, artifact.bytes, artifact.path);
    assert.equal(crypto.createHash("sha256").update(payload).digest("hex"), artifact.sha256, artifact.path);
  }
  for (const file of [
    "tmcra-openclaw-memory-0.4.0.tgz",
    "tmcra_hermes_plugin-0.4.1-py3-none-any.whl",
    "tmcra_client-0.5.0-py3-none-any.whl",
    "tmcra-typescript-0.5.0.tgz",
    "tmcra_mcp_server-0.4.0-py3-none-any.whl",
    "SHA256SUMS.txt",
  ]) {
    assert.match(automatic, new RegExp(`/downloads/integrations/${file.replace(/[.*+?^${}()|[\\]\\]/g, "\\$&")}`));
  }
});
