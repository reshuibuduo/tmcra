import { ConsoleError } from "@/db/console";

import {
  PERSONAL_NO_STORE_HEADERS,
  personalErrorResponse,
  personalMemoryBinding,
  personalMemoryRequest,
  requirePersonalAccess,
} from "../export/server";
import {
  PersonalMemoryControlContractError,
  deletionStatusRequest,
  idempotencyKey,
  memoryDeletionRequest,
  ownedScope,
  sessionDeletionRequest,
  usageQuery,
} from "./contract.mjs";

export const dynamic = "force-dynamic";

const MAX_REQUEST_BYTES = 64_000;
const ON_BEHALF_SUBJECT_HEADER = "X-TMCRA-On-Behalf-Of-Subject";

export async function GET(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    const access = await requirePersonalAccess();
    const url = new URL(request.url);
    const action = url.searchParams.get("action") ?? "usage";
    const binding = personalMemoryBinding();
    let upstreamPath: string;

    if (action === "usage") {
      const query = usageQuery(url.searchParams, access.space.scopeName);
      const parameters = new URLSearchParams();
      if (query.scopeName) parameters.set("scope_name", query.scopeName);
      else parameters.set("scope_prefix", `${access.space.scopeName}-`);
      if (query.fromTimestamp !== null) parameters.set("from_timestamp", String(query.fromTimestamp));
      if (query.toTimestamp !== null) parameters.set("to_timestamp", String(query.toTimestamp));
      if (query.groupBy) parameters.set("group_by", query.groupBy);
      upstreamPath = `/v1/usage/costs?${parameters}`;
    } else if (action === "deletion") {
      const scopeName = ownedScope(url.searchParams.get("scope"), access.space.scopeName);
      const deletionId = deletionStatusRequest(url.searchParams.get("deletionId"));
      upstreamPath = `/v1/scopes/${encodeURIComponent(scopeName)}/deletions/${encodeURIComponent(deletionId)}`;
    } else {
      throw new ConsoleError(422, "invalid_memory_control_action", "Memory control action is invalid.");
    }

    const result = await personalMemoryRequest(binding, upstreamPath, { method: "GET" }, requestId);
    return Response.json({ ok: true, result }, { headers: responseHeaders(requestId) });
  } catch (error) {
    return personalErrorResponse(mapContractError(error), requestId);
  }
}

export async function POST(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    requireSameOrigin(request);
    const access = await requirePersonalAccess();
    const body = await readJsonObject(request);
    const action = String(body.action ?? "").trim();
    const scopeName = ownedScope(body.scope, access.space.scopeName);
    const requestKey = idempotencyKey(request.headers.get("idempotency-key"));
    const binding = personalMemoryBinding();
    let upstreamPath: string;
    let upstreamBody: unknown;
    let confirmationHeader: Record<string, string>;

    if (action === "memory.delete") {
      const deletion = memoryDeletionRequest(body);
      upstreamPath = `/v1/scopes/${encodeURIComponent(scopeName)}/memories`;
      upstreamBody = { memory_ids: deletion.memoryIds };
      confirmationHeader = { "X-TMCRA-Confirm-Memory-Count": String(deletion.memoryIds.length) };
    } else if (action === "session.delete") {
      const deletion = sessionDeletionRequest(body);
      upstreamPath = `/v1/scopes/${encodeURIComponent(scopeName)}/sessions/${encodeURIComponent(deletion.sessionId)}`;
      upstreamBody = undefined;
      confirmationHeader = { "X-TMCRA-Confirm-Session": deletion.sessionId };
    } else {
      throw new ConsoleError(422, "invalid_memory_control_action", "Memory control action is invalid.");
    }

    const result = await personalMemoryRequest(
      binding,
      upstreamPath,
      {
        method: "DELETE",
        headers: {
          "Idempotency-Key": requestKey,
          [ON_BEHALF_SUBJECT_HEADER]: access.space.id,
          ...confirmationHeader,
          ...(upstreamBody === undefined ? {} : { "Content-Type": "application/json" }),
        },
        body: upstreamBody === undefined ? undefined : JSON.stringify(upstreamBody),
      },
      requestId,
    );
    return Response.json({ ok: true, result }, { status: 202, headers: responseHeaders(requestId) });
  } catch (error) {
    return personalErrorResponse(mapContractError(error), requestId);
  }
}

function requireSameOrigin(request: Request) {
  if (request.headers.get("sec-fetch-site") === "cross-site") {
    throw new ConsoleError(403, "cross_site_request", "Cross-site requests are not allowed.");
  }
  const origin = request.headers.get("origin");
  if (origin && origin !== new URL(request.url).origin) {
    throw new ConsoleError(403, "origin_mismatch", "Request origin is not allowed.");
  }
}

async function readJsonObject(request: Request) {
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) {
    throw new ConsoleError(415, "unsupported_media_type", "Content-Type must be application/json.");
  }
  const announced = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(announced) && announced > MAX_REQUEST_BYTES) {
    throw new ConsoleError(413, "payload_too_large", "Payload is too large.");
  }
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > MAX_REQUEST_BYTES) {
    throw new ConsoleError(413, "payload_too_large", "Payload is too large.");
  }
  let value: unknown;
  try {
    value = JSON.parse(text) as unknown;
  } catch {
    throw new ConsoleError(400, "invalid_json", "Request body must be valid JSON.");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ConsoleError(400, "invalid_json_object", "Request body must be a JSON object.");
  }
  return value as Record<string, unknown>;
}

function mapContractError(error: unknown) {
  if (!(error instanceof PersonalMemoryControlContractError)) return error;
  return new ConsoleError(error.status, error.code, error.message);
}

function responseHeaders(requestId: string) {
  return { ...PERSONAL_NO_STORE_HEADERS, "X-Request-ID": requestId };
}
