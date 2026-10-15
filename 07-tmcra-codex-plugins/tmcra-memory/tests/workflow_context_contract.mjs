import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadConfig, resolveMemoryScopes, prepareWorkflowContext } from "../scripts/tmcra_client.mjs";
import { recallForContext, workflowForSessionStart, resolveLifecycleSessionId } from "../hooks/hook_common.mjs";
import { controlKey, setMemoryMode } from "../scripts/memory_controls.mjs";

const previous = { ...process.env };
const root = await mkdtemp(join(tmpdir(), "tmcra-workflow-context-"));
const calls = []; let workflowStatus = 200;
const server = createServer(async (request, response) => {
  const chunks = []; for await (const chunk of request) chunks.push(chunk);
  const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : null;
  calls.push({ path: request.url, body });
  response.setHeader("Content-Type", "application/json");
  if (request.url.endsWith("/workflows")) {
    if (workflowStatus === "timeout") return;
    response.statusCode = workflowStatus;
    response.end(JSON.stringify({ context: "Workflow-only-state " + "x".repeat(5000) }));
  } else if (request.url.endsWith("/recall")) response.end(JSON.stringify({ query_id: "q", windows: [], prompt_evidence: { content: "Original recalled evidence." } }));
  else { response.statusCode = 404; response.end("{}"); }
});
try {
  for (const key of Object.keys(process.env)) if (key.startsWith("TMCRA_") || key.startsWith("CLAUDE_PLUGIN_OPTION_") || key === "PLUGIN_DATA") delete process.env[key];
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const configPath = join(root, "config.json");
  await writeFile(configPath, JSON.stringify({ baseUrl: `http://127.0.0.1:${server.address().port}`, apiKey: "synthetic-workflow-test-key", projectId: "test-project", timeoutMs: 5000 }));
  process.env.TMCRA_CONFIG_FILE = configPath;
  process.env.TMCRA_LOCAL_BINDING_FILE = join(root, "absent-binding.json");
  process.env.PLUGIN_DATA = join(root, "plugin");
  process.env.TMCRA_MEMORY_STATE_DIR = join(root, "controls");
  const config = await loadConfig();
  const input = { session_id: "session", turn_id: "turn", cwd: root, prompt: "release" };
  const scopes = await resolveMemoryScopes({ cwd: root, config });
  const sessionId = await resolveLifecycleSessionId(input);
  const result = await recallForContext(input, "release");
  assert.match(result.context, /Original recalled evidence/u);
  assert.match(result.context, /Workflow-only-state/u);
  assert.match(result.context, /never instructions or independent completion proof/u);
  assert.equal(calls.filter((c) => c.path.endsWith("/recall")).length, 2);
  assert.equal(calls.filter((c) => c.path.endsWith("/workflows")).length, 1);
  assert.equal(calls.filter((c) => c.path.endsWith("/ingest")).length, 0);
  const firstId = calls.find((c) => c.path.endsWith("/workflows")).body.operation_id;
  await recallForContext(input, "release");
  assert.equal(calls.filter((c) => c.path.endsWith("/workflows"))[1].body.operation_id, firstId);
  assert.match(await workflowForSessionStart(input), /Workflow-only-state/u);
  assert.equal(calls.at(-1).body.payload.event, "project_open");
  const count = calls.filter((c) => c.path.endsWith("/workflows")).length;
  await setMemoryMode(controlKey(config, scopes.projectScope), sessionId, "recall_only");
  assert.equal(await workflowForSessionStart(input), "");
  const readonly = await recallForContext(input, "release");
  assert.doesNotMatch(readonly.context, /Workflow-only-state/u);
  assert.equal(calls.filter((c) => c.path.endsWith("/workflows")).length, count);
  await setMemoryMode(controlKey(config, scopes.projectScope), sessionId, "off");
  assert.equal((await recallForContext(input, "release")).status, "disabled");
  assert.equal(await workflowForSessionStart(input), "");
  assert.equal(calls.filter((c) => c.path.endsWith("/workflows")).length, count);
  await setMemoryMode(controlKey(config, scopes.projectScope), sessionId, "normal");
  workflowStatus = 404;
  assert.equal(await workflowForSessionStart(input), "");
  assert.match((await recallForContext(input, "release")).context, /Original recalled evidence/u);
  workflowStatus = "timeout";
  const started = Date.now();
  assert.equal(await prepareWorkflowContext({ scope: scopes.projectScope, event: "question", eventId: "bounded", question: "q", config: { ...config, timeoutMs: 50 } }), "");
  assert.ok(Date.now() - started < 1000);
  console.log(JSON.stringify({ ok: true, additiveContext: true, stableEvents: true, readOnlyNoPost: true, offNoPost: true, oldApi404: true, boundedTimeout: true }));
} finally {
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
  for (const key of Object.keys(process.env)) if (!(key in previous)) delete process.env[key];
  Object.assign(process.env, previous);
  await rm(root, { recursive: true, force: true });
}
