import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createHash } from "node:crypto";
import { workflowPath, workflowRequest, requireWorkflowOrigin, readWorkflowBody } from "../app/api/personal/workflows/contract.mjs";
import { workflowHandler, workflowProjects } from "../app/api/personal/workflows/handler.mjs";

const valid = { action: "task.upsert", payload: { id: "task-1", title: "Work" }, operation_id: "operation-001" };
test("workflow path binds a server namespace and rejects path substitution", () => {
  assert.equal(workflowPath("personal-owner"), "/v1/scopes/personal-owner/workflows");
  for (const value of ["../other", "personal/a", "a?token=x", ""]) assert.throws(() => workflowPath(value));
});
test("workflow request rejects all browser-supplied account selectors and unknown actions", () => {
  assert.deepEqual(workflowRequest(valid), valid);
  for (const key of ["scope", "namespace", "tenant", "tenant_id", "project_id"]) {
    assert.throws(() => workflowRequest({ ...valid, [key]: "other" }));
    assert.throws(() => workflowRequest({ ...valid, payload: { [key]: "other" } }));
  }
  assert.throws(() => workflowRequest({ ...valid, action: "shell.run" }));
  assert.throws(() => workflowRequest({ ...valid, operation_id: "\n" }));
  for (const action of ["decision.revise", "reminder.evaluate", "bridge.export", "bridge.preview", "bridge.commit", "bridge.plan", "bridge.retry_failed"]) assert.equal(workflowRequest({ ...valid, action }).action, action);
});
test("mutations require an explicit matching Origin and reject cross-site metadata", () => {
  requireWorkflowOrigin(new Request("https://team.example/api/personal/workflows", { headers: { origin: "https://team.example" } }));
  for (const headers of [{}, { origin: "https://evil.example" }, { origin: "https://team.example", "sec-fetch-site": "cross-site" }]) {
    assert.throws(() => requireWorkflowOrigin(new Request("https://team.example/api/personal/workflows", { headers })));
  }
});
test("body parser bounds streamed bytes and rejects invalid JSON/content type", async () => {
  const make = (text, type = "application/json") => new Request("https://team.example/api/personal/workflows", { method: "POST", headers: { "content-type": type }, body: text });
  assert.deepEqual(await readWorkflowBody(make(JSON.stringify(valid))), valid);
  await assert.rejects(readWorkflowBody(make(JSON.stringify(valid)), 8), (e) => e.status === 413);
  await assert.rejects(readWorkflowBody(make("{")), (e) => e.status === 400);
  await assert.rejects(readWorkflowBody(make("{}", "text/plain")), (e) => e.status === 415);
});
test("route reuses authenticated BFF and adds no browser-controlled tenant or upstream URL", async () => {
  const route = await readFile(new URL("../app/api/personal/workflows/route.ts", import.meta.url), "utf8");
  const handler = await readFile(new URL("../app/api/personal/workflows/handler.mjs", import.meta.url), "utf8");
  assert.match(route, /requirePersonalAccess/u);
  assert.match(handler, /workflowPath\(selected\.scope\)/u);
  assert.match(handler, /workflowProjects\(access\.space\.scopeName, catalog\)/u);
  assert.match(handler, /"X-TMCRA-On-Behalf-Of-Subject": access\.space\.id/u);
  assert.match(handler, /requireWorkflowOrigin\(request\)/u);
  assert.match(route, /personalMemoryRequest/u);
  assert.doesNotMatch(route, /body\.scope|searchParams\.get\("scope"\)|fetch\(/u);
});

test("executable BFF binds authenticated identity, rejects selectors/CSRF, and preserves upstream errors", async () => {
  const calls = [];
  let signedIn = true, unavailable = false;
  const catalog = [{ scope_name: "personal-owner-project-app-a1b2c3d4e5f60718" }, { scope_name: "personal-other-project-private-a1b2c3d4e5f60718" }, { scope_name: "personal-owner-global" }];
  const projects = await workflowProjects("personal-owner", catalog);
  const project = projects.find((item) => item.id !== "global");
  const handler = workflowHandler({
    requirePersonalAccess: async () => { if (!signedIn) throw { status: 401, code: "authentication_required" }; return { space: { scopeName: "personal-owner", id: "space-owner" } }; },
    personalMemoryBinding: () => ({ apiKey: "server-only-key", baseUrl: "https://internal.example" }),
    personalMemoryRequest: async (...args) => { calls.push(args); if (unavailable) throw { status: 503, code: "upstream_busy" }; return args[1].startsWith("/v1/scopes?") ? catalog : { revision: 3, tasks: [], available_records: [{ id: args[1].includes("-project-") ? "plugin-source" : "global-source" }] }; },
    noStoreHeaders: { "Cache-Control": "private, no-store, max-age=0" },
    contractError: (status, code, message) => ({ status, code, message }),
    personalErrorResponse: (error) => Response.json({ error: error.code }, { status: error.status || 500 }),
  });
  const get = () => new Request("https://team.example/api/personal/workflows");
  const post = (body, origin = "https://team.example") => new Request(get().url, { method: "POST", headers: { origin, "content-type": "application/json" }, body: JSON.stringify(body) });
  let response = await handler(get(), false);
  assert.equal(response.status, 200); const initial = await response.json();
  assert.equal(initial.workflow_project.id, "global");
  assert.deepEqual(initial.workflow_projects, projects.map(({ id, label }) => ({ id, label })));
  assert.equal(initial.available_records[0].id, "global-source");
  assert.match(response.headers.get("cache-control"), /no-store/u);
  assert.equal(calls[0][1], "/v1/scopes?prefix=personal-owner-&limit=1000");
  assert.equal(calls[1][1], "/v1/scopes/personal-owner-global/workflows");
  assert.equal(calls[0][2].headers["X-TMCRA-On-Behalf-Of-Subject"], "space-owner");
  response = await handler(post(valid), true); assert.equal(response.status, 200);
  assert.deepEqual(JSON.parse(calls[3][2].body), valid);
  assert.equal((await handler(post(valid, "https://evil.example"), true)).status, 403);
  assert.equal((await handler(post({ ...valid, scope: "personal-other" }), true)).status, 422);
  assert.equal((await handler(new Request(`${get().url}?scope=personal-other`), false)).status, 422);
  assert.equal(calls.length, 4);
  response = await handler(new Request(`${get().url}?project=${project.id}`), false);
  assert.equal(response.status, 200); assert.equal((await response.json()).available_records[0].id, "plugin-source");
  assert.equal(calls.at(-1)[1], `/v1/scopes/${project.scope}/workflows`);
  const selectedPost = new Request(`${get().url}?project=${project.id}`, { method: "POST", headers: { origin: "https://team.example", "content-type": "application/json" }, body: JSON.stringify(valid) });
  assert.equal((await handler(selectedPost, true)).status, 200);
  assert.equal(calls.at(-1)[1], `/v1/scopes/${project.scope}/workflows`);
  const foreign = (await workflowProjects("personal-other", catalog)).find((item) => item.id !== "global");
  assert.equal((await handler(new Request(`${get().url}?project=${foreign.id}`), false)).status, 403);
  for (const query of ["project=personal-owner-global", "project=global&project=global", "tenant=another"]) {
    assert.equal((await handler(new Request(`${get().url}?${query}`), false)).status, 422);
  }
  const count = calls.length;
  signedIn = false; assert.equal((await handler(get(), false)).status, 401); assert.equal(calls.length, count);
  signedIn = true; unavailable = true; response = await handler(get(), false); assert.equal(response.status, 503); assert.deepEqual(await response.json(), { error: "upstream_busy" });
});

test("shared workflow UI assets are complete and hash-matched, without persisted tokens or HTML interpolation", async () => {
  const root = new URL("../public/workflows/", import.meta.url);
  const manifest = JSON.parse(await readFile(new URL("assets-manifest.json", root), "utf8"));
  assert.deepEqual(Object.keys(manifest.files).sort(), ["workflow-center.css", "workflow-center.html", "workflow-center.js"]);
  for (const [name, digest] of Object.entries(manifest.files)) {
    assert.equal(createHash("sha256").update(await readFile(new URL(name, root))).digest("hex"), digest, name);
  }
  const script = await readFile(new URL("workflow-center.js", root), "utf8");
  assert.doesNotMatch(script, /innerHTML|outerHTML|insertAdjacentHTML|localStorage|sessionStorage|document\.write|eval\(/u);
  assert.match(script, /node\.textContent/u);
  assert.match(script, /preview_token: preview\.preview_token/u);
  assert.match(script, /credentials: personal \? "same-origin" : "omit"/u);
  const html = await readFile(new URL("workflow-center.html", root), "utf8");
  assert.match(html, /src="\.\/workflow-center\.js"/u);
  assert.match(html, /href="\.\/workflow-center\.css"/u);
  assert.doesNotMatch(html, /onclick=|onerror=|src="https?:/u);
});


test("catalog uses actual plugin project scopes and rejects invalid/unowned entries", async () => {
  const prefix = "personal-d05268a82b6c9eac";
  const realScope = `${prefix}-project-my-engine-a1b2c3d4e5f60718`;
  const items = await workflowProjects(prefix, [
    { scope_name: realScope }, { scope_name: realScope },
    { scope_name: `${prefix}-global` }, { scope_name: `${prefix}evil-project-hidden` },
    { scope_name: `${prefix}-project-bad/path` }, { scope_name: "personal-other-project-secret" },
  ]);
  assert.deepEqual(items.map((item) => item.scope), [`${prefix}-global`, realScope]);
  assert.equal(items[1].label, "my-engine");
  assert.match(items[1].id, /^p-[a-f0-9]{32}$/u);
  assert.ok(!items[1].id.includes(prefix));
  await assert.rejects(workflowProjects(prefix, null), (e) => e.status === 502);
});
