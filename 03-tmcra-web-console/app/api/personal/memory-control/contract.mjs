const SCOPE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/u;
const SESSION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/u;
const MEMORY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,511}$/u;
const DELETION_PATTERN = /^del_[a-f0-9]{32}$/u;
const IDEMPOTENCY_PATTERN = /^[^\u0000-\u001f]{8,200}$/u;
const GROUPS = new Set([
  "day", "scope", "stage", "operation", "provider", "model",
  "platform", "integration", "agent", "attribution_source",
]);

export class PersonalMemoryControlContractError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = "PersonalMemoryControlContractError";
    this.status = status;
    this.code = code;
  }
}

export function ownedScope(value, namespace) {
  const scope = String(value ?? "").trim();
  const prefix = `${namespace}-`;
  if (!SCOPE_PATTERN.test(namespace) || !SCOPE_PATTERN.test(scope) || !scope.startsWith(prefix)) {
    throw contractError(403, "memory_scope_forbidden", "The requested Scope is not part of this account.");
  }
  return scope;
}

export function memoryDeletionRequest(value) {
  const source = objectValue(value);
  if (!Array.isArray(source.memoryIds) || source.memoryIds.length < 1 || source.memoryIds.length > 100) {
    throw contractError(422, "invalid_memory_ids", "memoryIds must contain 1-100 Memory IDs.");
  }
  const memoryIds = source.memoryIds.map((item) => {
    const memoryId = String(item ?? "").trim();
    if (!MEMORY_PATTERN.test(memoryId)) {
      throw contractError(422, "invalid_memory_ids", "One or more Memory IDs are invalid.");
    }
    return memoryId;
  });
  if (new Set(memoryIds).size !== memoryIds.length) {
    throw contractError(422, "invalid_memory_ids", "Memory IDs must be unique.");
  }
  return { memoryIds };
}

export function sessionDeletionRequest(value) {
  const source = objectValue(value);
  const sessionId = String(source.sessionId ?? "").trim();
  if (!SESSION_PATTERN.test(sessionId)) {
    throw contractError(422, "invalid_session_id", "Session ID is invalid.");
  }
  return { sessionId };
}

export function deletionStatusRequest(value) {
  const deletionId = String(value ?? "").trim();
  if (!DELETION_PATTERN.test(deletionId)) {
    throw contractError(422, "invalid_deletion_id", "Deletion ID is invalid.");
  }
  return deletionId;
}

export function idempotencyKey(value) {
  const clean = String(value ?? "").trim();
  if (!IDEMPOTENCY_PATTERN.test(clean)) {
    throw contractError(422, "invalid_idempotency_key", "Idempotency-Key is invalid.");
  }
  return clean;
}

export function usageQuery(searchParams, namespace) {
  const scope = searchParams.get("scope");
  const scopeName = scope ? ownedScope(scope, namespace) : null;
  const fromTimestamp = optionalTimestamp(searchParams.get("from"), "from");
  const toTimestamp = optionalTimestamp(searchParams.get("to"), "to");
  if (fromTimestamp !== null && toTimestamp !== null && fromTimestamp >= toTimestamp) {
    throw contractError(422, "invalid_time_window", "from must be earlier than to.");
  }
  const groupBy = searchParams.get("groupBy");
  if (groupBy !== null && !GROUPS.has(groupBy)) {
    throw contractError(422, "invalid_usage_group", "Usage group is invalid.");
  }
  return { scopeName, fromTimestamp, toTimestamp, groupBy };
}

function optionalTimestamp(value, field) {
  if (value === null || value === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) {
    throw contractError(422, "invalid_time_window", `${field} must be a non-negative Unix timestamp.`);
  }
  return number;
}

function objectValue(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw contractError(400, "invalid_json", "Request body must be a JSON object.");
  }
  return value;
}

function contractError(status, code, message) {
  return new PersonalMemoryControlContractError(status, code, message);
}
