import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const source = (relative) => readFile(path.join(ROOT, relative), "utf8");
const json = async (relative) => JSON.parse(await source(relative));

test("graph renderer dependency licenses remain explicit and commercially usable", async () => {
  const packageJson = await json("package.json");
  assert.equal(packageJson.dependencies.sigma, "^3.0.3");
  assert.equal(packageJson.dependencies.graphology, "^0.26.0");
  assert.equal(packageJson.dependencies.cytoscape, "^3.34.0");
  assert.equal(packageJson.dependencies["cytoscape-elk"], "^2.3.0");
  assert.equal(packageJson.dependencies["cytoscape-fcose"], undefined);
  assert.equal(packageJson.dependencies["graphology-layout-forceatlas2"], undefined);

  for (const dependency of ["sigma", "graphology", "cytoscape", "cytoscape-elk"]) {
    const dependencyPackage = await json(`node_modules/${dependency}/package.json`);
    const licenseFile = dependency === "sigma" || dependency === "graphology" ? "LICENSE.txt" : "LICENSE";
    const license = await source(`node_modules/${dependency}/${licenseFile}`);
    assert.equal(dependencyPackage.license, "MIT");
    assert.match(license, /Permission is hereby granted, free of charge/);
  }

  const elkPackage = await json("node_modules/elkjs/package.json");
  const elkLicense = await source("node_modules/elkjs/LICENSE.md");
  assert.equal(elkPackage.license, "EPL-2.0");
  assert.match(elkLicense, /Eclipse Public License - v 2\.0/);

  const notices = await source("THIRD_PARTY_NOTICES.md");
  assert.match(notices, /Sigma\.js 3\.0\.3/);
  assert.match(notices, /Graphology 0\.26\.0/);
  assert.match(notices, /Cytoscape\.js 3\.34\.0/);
  assert.match(notices, /cytoscape-elk 2\.3\.0/);
  assert.match(notices, /elkjs 0\.9\.3/);
  assert.match(notices, /Eclipse Public License 2\.0/);
  assert.match(notices, /The above copyright notice and this permission notice/);
});

test("graph BFF keeps production keys server-side and tenant-bound", async () => {
  const route = await source("app/api/enterprise/graph/route.ts");
  const personalRoute = await source("app/api/personal/graph/route.ts");
  const client = await source("app/console/MemoryExplorer.tsx");

  assert.match(route, /resolveConsoleGraphAccess\(identity/);
  assert.match(await source("db/console.ts"), /requireAgent\([\s\S]*membership\.organizationId/);
  assert.match(route, /TMCRA_MEMORY_API_TENANT_BINDINGS/);
  assert.match(personalRoute, /TMCRA_MEMORY_API_CONTROL_KEY/);
  assert.match(personalRoute, /resolvePersonalMemoryAccess\(identity\)/);
  assert.match(personalRoute, /scopeName\.startsWith\(`\$\{namespace\}-`\)/);
  assert.match(personalRoute, /X-TMCRA-On-Behalf-Of-Subject/);
  assert.match(personalRoute, /\[ON_BEHALF_SUBJECT_HEADER\]: access\.space\.id/);
  assert.match(personalRoute, /mutation \? \{ \[ON_BEHALF_SUBJECT_HEADER\]/);
  assert.match(personalRoute, /action === "narrative"/);
  assert.match(personalRoute, /origin\.pathname \+= "\/narrative"/);
  assert.match(personalRoute, /NARRATIVE_FOCUS_RE/);
  assert.match(route, /action === "narrative"/);
  assert.match(personalRoute, /function responseHeaders\(requestId: string\)[\s\S]*?"X-Request-ID": requestId/);
  assert.match(personalRoute, /status: error\.status, headers: responseHeaders\(requestId\)/);
  assert.doesNotMatch(personalRoute, /TMCRA_MEMORY_API_PERSONAL_BINDINGS|searchParams\.get\("organizationId"\)|searchParams\.get\("scopeName"\)/);
  assert.match(route, /Authorization: `Bearer \$\{binding\.apiKey\}`/);
  assert.match(route, /requireSameOrigin\(request\)/);
  assert.match(route, /normalizeMemoryApiBaseUrl/);
  assert.match(route, /!tenantBaseUrl/);
  assert.match(route, /TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK === "1"/);
  assert.match(personalRoute, /normalizeMemoryApiBaseUrl/);
  assert.match(route, /scopeByAgent/);
  assert.match(route, /max_windows must be 8/);
  assert.doesNotMatch(client, /Authorization|localStorage|sessionStorage|TMCRA_MEMORY_API_(?:TENANT|PERSONAL)_BINDINGS/);
});

test("memory explorer exposes layered lazy expansion and equivalent views", async () => {
  const graph = await source("app/console/MemoryGraph.tsx");
  const explorer = await source("app/console/MemoryExplorer.tsx");

  assert.match(graph, /type GraphView = "semantic" \| "timeline" \| "table"/);
  assert.match(graph, /const LAYERS: MemoryLayer\[\] = \["slow", "fast", "source"\]/);
  assert.match(graph, /onExpand/);
  assert.match(graph, /onLoadEvidence/);
  assert.match(graph, /VERBATIM SOURCE/);
  assert.match(explorer, /action: "overview"/);
  assert.match(explorer, /action: "neighbors"/);
  assert.match(explorer, /action: "evidence"/);
  assert.match(explorer, /action: "trace"/);
  assert.match(explorer, /Control-plane event projection \/ production graph not connected/);
  assert.match(explorer, /graphEndpoint = "\/api\/enterprise\/graph"/);
});

test("personal memory exposes a full evidence-bound semantic zoom atlas", async () => {
  const route = await source("app/api/personal/session-graph/route.ts");
  const explorer = await source("app/personal/VisualMemoryAtlasExplorer.tsx");
  const atlas = await source("app/personal/VisualMemoryAtlas.tsx");
  const consoleClient = await source("app/personal/PersonalConsoleClient.tsx");

  assert.match(route, /resolvePersonalMemoryAccess\(identity\)/);
  assert.match(route, /scopeName\.startsWith\(`\$\{namespace\}-`\)/);
  assert.match(route, /memory-graph\/visual-atlas/);
  assert.match(route, /refresh-visual-atlas/);
  assert.match(route, /requireSameOrigin\(request\)/);
  assert.doesNotMatch(explorer, /Authorization|localStorage|sessionStorage|TMCRA_MEMORY_API_CONTROL_KEY/);
  assert.match(explorer, /action: "visual-atlas"/);
  assert.match(explorer, /action: "refresh-visual-atlas"/);
  assert.match(explorer, /action: "evidence"/);
  assert.match(explorer, /full_projection !== true/);
  assert.match(explorer, /truncated !== false/);
  assert.match(atlas, /AtlasViewTab mode="global"/);
  assert.match(atlas, /AtlasViewTab mode="threads"/);
  assert.match(atlas, /AtlasViewTab mode="evolution"/);
  assert.match(atlas, /Source evidence/);
  assert.match(atlas, /Remember what you worked on/);
  assert.match(atlas, /Trace every memory to its evidence/);
  assert.match(atlas, /Technical sessions stay out of the way/);
  assert.match(atlas, /function isNarrativeViewMode/);
  assert.match(atlas, /mode === "global" \|\| mode === "evolution" \|\| mode === "relations"/);
  const graph = await source("app/personal/VisualAtlasGraph.tsx");
  assert.match(graph, /buildGlobalMemoryView/);
  assert.match(graph, /Show more/);
  assert.match(graph, /edge\.type\.toLowerCase\(\) !== "continues"/);
  assert.match(graph, /Source 原文/);
  assert.match(graph, /visualKindLabel\(node, language, mode\)/);
  assert.match(graph, /时间先后请查看演化流/);
  assert.match(graph, /domainSessionKeys/);
  assert.match(graph, /compareAcrossSessions/);
  assert.match(atlas, /loadedScopeRef/);
  assert.doesNotMatch(atlas, /data\.(?:galaxies|sessions|chapters|memories)\.slice/);
  assert.match(consoleClient, /<VisualMemoryAtlasExplorer scopeName=\{effectiveScope\}/);
  assert.match(consoleClient, /graphMode === "sessions"/);
});

test("memory graph keeps user facts and Agent progress role-partitioned while showing both by default", async () => {
  const graph = await source("app/console/MemoryGraph.tsx");
  const explorer = await source("app/console/MemoryExplorer.tsx");
  const styles = await source("app/console/console.css");

  for (const field of ["actor_role", "actor_roles", "authority", "provenance_source"]) {
    assert.match(explorer, new RegExp(`${field}:`));
  }
  assert.match(explorer, /provenance: UnknownRecord/);
  assert.match(explorer, /actorRole: nullableText\(item\.actor_role\) \?\? nullableText\(item\.role\)/);
  assert.match(graph, /type ActorBucket = "user" \| "assistant" \| "mixed" \| "unknown"/);
  assert.match(graph, /if \(roles\.size > 1\) return "mixed"/);
  assert.match(graph, /user: true/);
  assert.match(graph, /assistant: true/);
  assert.match(graph, /mixed: true/);
  assert.match(graph, /unknown: false/);
  assert.match(graph, /用户要求\/事实与 Agent 进度\/结果/);
  assert.match(graph, /Agent 进度 \/ 结果/);
  assert.match(graph, /角色冲突 \/ 混合来源/);
  assert.match(graph, /actorLaneY\[actorBucket\(event\)\]/);
  assert.match(graph, /graph-actor-legend/);
  assert.match(graph, /data-actor-role=\{evidenceActorBucket\(item\)\}/);
  assert.match(graph, /edgeProvenanceLabel\(edge, language\)/);
  assert.match(styles, /\.inspector-layer \{[^}]*flex-wrap: wrap/);
  assert.match(styles, /\.evidence-list header \{[^}]*flex-wrap: wrap/);
  assert.match(styles, /\.relationship-list button \{[^}]*minmax\(108px/);
  assert.match(styles, /\.graph-data-table \{ min-width: 980px; \}/);
  assert.match(explorer, /Recall result replay/);
  assert.match(explorer, /previous graph remains visible/);
  assert.match(explorer, /does not claim live planner or reranker stages/);
});
