export class WorkflowContractError extends Error {
  constructor(status, code, message) {
    super(message); this.status = status; this.code = code;
  }
}

const ACTIONS = new Set([
  "task.upsert", "task.receipt", "attempt.record", "decision.create", "decision.revise",
  "reminder.upsert", "reminder.evaluate", "reminder.act", "record.delete",
  "bridge.export", "bridge.export_revocations", "bridge.preview", "bridge.commit", "bridge.plan", "bridge.retry_failed", "context.prepare",
]);

export function requireWorkflowOrigin(request) {
  const origin = request.headers.get("origin");
  if (request.headers.get("sec-fetch-site") === "cross-site" || origin !== new URL(request.url).origin) {
    throw new WorkflowContractError(403, "origin_mismatch", "A same-origin request is required.");
  }
}

export function workflowPath(namespace) {
  if (typeof namespace !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/u.test(namespace)) {
    throw new WorkflowContractError(500, "invalid_workflow_namespace", "Workflow account binding is invalid.");
  }
  return `/v1/scopes/${encodeURIComponent(namespace)}/workflows`;
}

export function workflowRequest(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).some((key) => !["action", "payload", "operation_id"].includes(key))) {
    throw new WorkflowContractError(422, "invalid_workflow_request", "Use action, payload and operation_id only.");
  }
  if (!ACTIONS.has(value.action)) throw new WorkflowContractError(422, "invalid_workflow_action", "Unsupported workflow action.");
  if (!value.payload || typeof value.payload !== "object" || Array.isArray(value.payload)) {
    throw new WorkflowContractError(422, "invalid_workflow_payload", "payload must be an object.");
  }
  if (["scope", "namespace", "tenant", "tenant_id", "project_id"].some((key) => key in value.payload)) {
    throw new WorkflowContractError(403, "workflow_scope_forbidden", "The server binds the workflow account.");
  }
  if (typeof value.operation_id !== "string" || !/^[A-Za-z0-9_.:-]{8,128}$/u.test(value.operation_id)) {
    throw new WorkflowContractError(422, "invalid_operation_id", "A bounded operation_id is required.");
  }
  return { action: value.action, payload: value.payload, operation_id: value.operation_id };
}

export async function readWorkflowBody(request, maximum = 512_000) {
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) {
    throw new WorkflowContractError(415, "unsupported_media_type", "Use application/json.");
  }
  const reader = request.body?.getReader();
  if (!reader) throw new WorkflowContractError(400, "invalid_json", "A JSON body is required.");
  const chunks = []; let bytes = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read(); if (done) break;
      bytes += value.byteLength;
      if (bytes > maximum) {
        await reader.cancel();
        throw new WorkflowContractError(413, "payload_too_large", "Workflow request exceeds 512 KB.");
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const joined = new Uint8Array(bytes); let offset = 0;
  for (const chunk of chunks) { joined.set(chunk, offset); offset += chunk.length; }
  let value;
  try { value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(joined)); }
  catch { throw new WorkflowContractError(400, "invalid_json", "A valid UTF-8 JSON body is required."); }
  return workflowRequest(value);
}
