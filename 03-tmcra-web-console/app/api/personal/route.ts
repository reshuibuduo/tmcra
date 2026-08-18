import { env } from "cloudflare:workers";

import { getChatGPTUser } from "@/app/chatgpt-auth";
import { normalizeMemoryApiBaseUrl } from "@/app/api/device/v1/upstream-client.mjs";
import { fetchMemoryApi } from "@/app/lib/memory-api-fetch";
import {
  listDeviceConnections,
  revokeDeviceConnection,
} from "@/app/api/device/v1/device-service";
import {
  ConsoleError,
  listPersonalApiKeys,
  resolvePersonalMemoryAccess,
  type ConsoleIdentity,
} from "@/db/console";
import {
  createPersonalApiKey,
  revokePersonalApiKey,
} from "./api-keys";

export const dynamic = "force-dynamic";

const NO_STORE_HEADERS = {
  "Cache-Control": "private, no-store, max-age=0",
  "X-Content-Type-Options": "nosniff",
};
const RATINGS = new Set(["helpful", "incorrect", "stale", "unsafe", "missing"]);
const MAX_RESPONSE_BYTES = 2_500_000;

type MemoryBinding = {
  apiKey: string;
  baseUrl: string;
};

type ScopeCatalogRow = {
  scope_name: string;
  created_at: number;
  last_seen_at: number;
  last_ingest_at: number | null;
  last_recall_at: number | null;
  session_count: number;
  ingest_request_count: number;
  recall_request_count: number;
  message_count: number;
};

export async function GET(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    const access = await resolvePersonalMemoryAccess(await requireApiIdentity());
    const binding = memoryBinding();
    const globalScope = `${access.space.scopeName}-global`;
    const projectPrefix = `${access.space.scopeName}-project-`;
    const connectionsPromise = listDeviceConnections(access);
    const apiKeysPromise = listPersonalApiKeys(access.space.id);
    const retentionPromise = memoryRequest(
      binding,
      `/v1/scopes/${encodeURIComponent(globalScope)}/retention`,
      { method: "GET" },
      requestId,
    );
    const quotaPromise = memoryRequest(
      binding,
      `/v1/usage/quota?subject=${encodeURIComponent(access.space.id)}`,
      { method: "GET" },
      requestId,
    );
    const scopesPromise = memoryRequest(
      binding,
      `/v1/scopes?prefix=${encodeURIComponent(`${access.space.scopeName}-`)}&limit=1000`,
      { method: "GET" },
      requestId,
    );
    const billingPromise = memoryRequest(
      binding,
      `/v1/usage/costs?scope_prefix=${encodeURIComponent(`${access.space.scopeName}-`)}`,
      { method: "GET" },
      requestId,
    );

    const [connections, apiKeys, retentionResult, quotaResult, scopesResult, billingResult] = await Promise.all([
      connectionsPromise,
      apiKeysPromise,
      settle("retention", retentionPromise, requestId),
      settle("quota", quotaPromise, requestId),
      settle("catalog", scopesPromise, requestId),
      settle("billing", billingPromise, requestId),
    ]);
    const catalog = scopeCatalog(scopesResult.value, access.space.scopeName);
    const billing = billingResult.ok
      ? normalizeBilling(billingResult.value, quotaResult.value)
      : null;
    const serviceErrors = [
      ...(retentionResult.ok ? [] : ["retention"]),
      ...(quotaResult.ok ? [] : ["quota"]),
      ...(scopesResult.ok ? [] : ["catalog"]),
      ...(billing ? [] : ["billing"]),
    ];

    return Response.json(
      {
        ok: true,
        actor: access.actor,
        space: {
          id: access.space.id,
          scopeName: access.space.scopeName,
          displayName: access.space.displayName,
          status: access.space.status,
        },
        retention: isRecord(retentionResult.value) ? retentionResult.value : null,
        quota: normalizeQuota(quotaResult.value),
        scopes: catalog.map((scope) => ({
          id: scope.scope_name,
          scopeName: scope.scope_name,
          displayName: scope.scope_name === globalScope ? "Global memory" : projectLabel(scope.scope_name, projectPrefix),
          status: "active",
          createdAt: scope.created_at,
          updatedAt: scope.last_seen_at,
          sessionCount: scope.session_count,
          messageCount: scope.message_count,
        })),
        projects: catalog
          .filter((scope) => scope.scope_name.startsWith(projectPrefix))
          .map((scope) => ({
            id: scope.scope_name,
            scopeName: scope.scope_name,
            displayName: projectLabel(scope.scope_name, projectPrefix),
            status: "active",
            createdAt: scope.created_at,
            updatedAt: scope.last_seen_at,
          })),
        sessionTotal: catalog.reduce((total, scope) => total + Math.max(0, Number(scope.session_count) || 0), 0),
        connections,
        apiKeys,
        billing,
        serviceStatus: serviceErrors.length ? "partial" : "ready",
        serviceErrors: [...new Set(serviceErrors)],
        requestId,
      },
      { headers: NO_STORE_HEADERS },
    );
  } catch (error) {
    return errorResponse(error, requestId);
  }
}

export async function POST(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    requireSameOrigin(request);
    const body = await readJsonObject(request, 32_768);
    const action = requiredText(body.action, "action", 80);
    const payload = isRecord(body.payload) ? body.payload : {};
    const access = await resolvePersonalMemoryAccess(await requireApiIdentity());

    if (action === "connection.revoke") {
      const result = await revokeDeviceConnection(access, payload.connectionId, requestId);
      return Response.json({ ok: true, result }, { headers: NO_STORE_HEADERS });
    }
    if (action === "api_key.create") {
      const result = await createPersonalApiKey(
        access,
        payload,
        await personalApiKeyRequestId(request, access.space.id, requestId),
      );
      return Response.json({ ok: true, result }, { status: 201, headers: NO_STORE_HEADERS });
    }
    if (action === "api_key.revoke") {
      const result = await revokePersonalApiKey(access, payload, requestId);
      return Response.json({ ok: true, result }, { headers: NO_STORE_HEADERS });
    }

    const binding = memoryBinding();
    const globalScope = `${access.space.scopeName}-global`;
    let result: unknown;
    if (action === "retention.set") {
      if (typeof payload.enabled !== "boolean") {
        throw new ConsoleError(422, "invalid_enabled", "enabled must be a boolean.");
      }
      const inactiveDays = Number(payload.inactiveDays);
      if (!Number.isInteger(inactiveDays) || inactiveDays < 1 || inactiveDays > 3650) {
        throw new ConsoleError(422, "invalid_inactive_days", "inactiveDays must be between 1 and 3650.");
      }
      result = await scopeRequest(
        binding,
        globalScope,
        "/retention",
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: payload.enabled, inactive_days: inactiveDays }),
        },
        requestId,
      );
    } else if (action === "feedback.submit") {
      const rating = requiredText(payload.rating, "rating", 20);
      if (!RATINGS.has(rating)) {
        throw new ConsoleError(422, "invalid_rating", "rating is invalid.");
      }
      const comment = optionalText(payload.comment, "comment", 4000);
      const queryId = optionalText(payload.queryId, "queryId", 200);
      const memoryIds = stringArray(payload.memoryIds, "memoryIds", 100, 512);
      result = await scopeRequest(
        binding,
        globalScope,
        "/feedback",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query_id: queryId,
            rating,
            memory_ids: memoryIds,
            comment,
            metadata: { surface: "personal_memory_console" },
          }),
        },
        requestId,
      );
    } else if (action === "export.start") {
      const idempotencyKey = validatedIdempotencyKey(
        request.headers.get("idempotency-key") ?? `personal-export-${crypto.randomUUID()}`,
      );
      result = await scopeRequest(
        binding,
        globalScope,
        "/exports",
        { method: "POST", headers: { "Idempotency-Key": idempotencyKey } },
        requestId,
      );
    } else {
      throw new ConsoleError(422, "invalid_action", "Unsupported personal console action.");
    }

    return Response.json({ ok: true, result }, { headers: responseHeaders(requestId) });
  } catch (error) {
    return errorResponse(error, requestId);
  }
}

function memoryBinding(): MemoryBinding {
  const apiKey = String(env.TMCRA_MEMORY_API_CONTROL_KEY ?? "").trim();
  const baseUrl = String(
    env.TMCRA_MEMORY_API_CONTROL_BASE_URL ?? env.TMCRA_MEMORY_API_BASE_URL ?? "",
  ).trim();
  if (!apiKey || !apiKey.startsWith("tmcra_") || apiKey.length > 1024) {
    throw new ConsoleError(503, "personal_memory_not_configured", "Personal memory service is not configured.");
  }
  try {
    return {
      apiKey,
      baseUrl: normalizeMemoryApiBaseUrl(baseUrl, {
        allowHttpLoopback:
          env.TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK === "1",
      }),
    };
  } catch {
    throw new ConsoleError(503, "personal_memory_configuration_invalid", "Personal memory endpoint is invalid.");
  }
}

async function scopeRequest(
  binding: MemoryBinding,
  scopeName: string,
  suffix: "/retention" | "/feedback" | "/exports",
  init: RequestInit,
  requestId: string,
) {
  return memoryRequest(
    binding,
    `/v1/scopes/${encodeURIComponent(scopeName)}${suffix}`,
    init,
    requestId,
  );
}

async function memoryRequest(
  binding: MemoryBinding,
  pathAndQuery: string,
  init: RequestInit,
  requestId: string,
) {
  const url = new URL(pathAndQuery, `${binding.baseUrl}/`);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30_000);
  let response: Response;
  try {
    response = await fetchMemoryApi(env, url, {
      ...init,
      headers: {
        Authorization: `Bearer ${binding.apiKey}`,
        Accept: "application/json",
        "X-Request-ID": requestId,
        ...init.headers,
      },
      redirect: "error",
      signal: controller.signal,
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new ConsoleError(504, "personal_memory_timeout", "Personal memory service timed out.");
    }
    console.warn("TMCRA personal memory upstream fetch failed", {
      requestId,
      error: error instanceof Error ? error.name : "UnknownError",
    });
    throw new ConsoleError(502, "personal_memory_unavailable", "Personal memory service is unavailable.");
  } finally {
    clearTimeout(timeout);
  }

  const text = await readBoundedText(response, MAX_RESPONSE_BYTES);
  let value: unknown;
  try {
    value = text ? JSON.parse(text) : null;
  } catch {
    throw new ConsoleError(502, "personal_memory_invalid_response", "Personal memory service returned invalid JSON.");
  }
  if (!response.ok) {
    throw new ConsoleError(
      [400, 401, 403, 404, 409, 410, 413, 422, 429, 503].includes(response.status) ? response.status : 502,
      "personal_memory_request_failed",
      upstreamMessage(value),
    );
  }
  return value;
}

async function settle(component: string, promise: Promise<unknown>, requestId: string) {
  try {
    return { ok: true as const, value: await promise };
  } catch (error) {
    console.warn("TMCRA personal console component request failed", {
      component,
      requestId,
      error: error instanceof Error ? error.name : "UnknownError",
      message: error instanceof Error ? error.message.slice(0, 240) : "Unknown failure",
    });
    return { ok: false as const, value: null };
  }
}

function scopeCatalog(value: unknown, namespace: string): ScopeCatalogRow[] {
  if (!Array.isArray(value)) return [];
  const prefix = `${namespace}-`;
  return value.filter((item): item is ScopeCatalogRow =>
    isRecord(item) &&
    typeof item.scope_name === "string" &&
    item.scope_name.startsWith(prefix) &&
    typeof item.created_at === "number" &&
    typeof item.last_seen_at === "number");
}

function normalizeQuota(value: unknown) {
  if (!isRecord(value)) return null;
  const metric = (name: string) => {
    const row = isRecord(value[name]) ? value[name] : {};
    const limit = typeof row.limit === "number" ? row.limit : null;
    return {
      used: typeof row.used === "number" ? row.used : 0,
      limit,
      remaining: typeof row.remaining === "number" ? row.remaining : null,
      unlimited: limit === null,
    };
  };
  const ingest = metric("ingest_raw_tokens");
  const recall = metric("recall_requests");
  return {
    plan: typeof value.plan === "string" ? value.plan : "pilot",
    status: ingest.unlimited && recall.unlimited ? "unlimited" : "active",
    metrics: {
      ingest_raw_tokens: ingest,
      recall_requests: recall,
    },
  };
}

function normalizeBilling(costs: unknown, quota: unknown) {
  if (
    !isRecord(costs) ||
    typeof costs.currency !== "string" ||
    typeof costs.known_cost_cny !== "number" ||
    !Number.isFinite(costs.known_cost_cny) ||
    !isRecord(costs.source) ||
    !isRecord(costs.calls) ||
    !isRecord(costs.by_stage)
  ) {
    return null;
  }
  const plan = isRecord(quota) && typeof quota.plan === "string" && quota.plan.trim()
    ? quota.plan.trim()
    : "pilot";
  return {
    plan,
    usageCosts: costs,
    payment: { status: "unavailable" },
    invoices: { status: "unavailable" },
  };
}

async function personalApiKeyRequestId(
  request: Request,
  personalSpaceId: string,
  fallback: string,
) {
  const supplied = request.headers.get("idempotency-key");
  if (!supplied) return fallback;
  const key = validatedIdempotencyKey(supplied);
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(`${personalSpaceId}\u0000${key}`),
  );
  const hex = [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  return `personal-api-key-${hex}`;
}

function projectLabel(scopeName: string, projectPrefix: string) {
  if (!scopeName.startsWith(projectPrefix)) return scopeName;
  const relative = scopeName.slice(projectPrefix.length).replace(/-[a-f0-9]{16}$/u, "");
  return relative || "Project";
}

async function requireApiIdentity(): Promise<ConsoleIdentity> {
  const user = await getChatGPTUser();
  if (!user) throw new ConsoleError(401, "authentication_required", "Sign in with ChatGPT to continue.");
  return { email: user.email, displayName: user.displayName, fullName: user.fullName };
}

function requireSameOrigin(request: Request) {
  const site = request.headers.get("sec-fetch-site");
  if (site === "cross-site") throw new ConsoleError(403, "cross_site_request", "Cross-site requests are not allowed.");
  const origin = request.headers.get("origin");
  if (origin && origin !== new URL(request.url).origin) {
    throw new ConsoleError(403, "origin_mismatch", "Request origin is not allowed.");
  }
}

async function readJsonObject(request: Request, maximumBytes: number) {
  const contentType = request.headers.get("content-type")?.toLowerCase() ?? "";
  if (!contentType.startsWith("application/json")) {
    throw new ConsoleError(415, "unsupported_media_type", "Content-Type must be application/json.");
  }
  const text = await readBoundedText(request, maximumBytes);
  let value: unknown;
  try { value = JSON.parse(text); } catch {
    throw new ConsoleError(400, "invalid_json", "Request body must be valid JSON.");
  }
  if (!isRecord(value)) throw new ConsoleError(400, "invalid_json", "Request body must be a JSON object.");
  return value;
}

async function readBoundedText(source: Request | Response, maximumBytes: number) {
  const announced = Number(source.headers.get("content-length") ?? "0");
  if (Number.isFinite(announced) && announced > maximumBytes) {
    throw new ConsoleError(413, "payload_too_large", "Payload is too large.");
  }
  const text = await source.text();
  if (new TextEncoder().encode(text).byteLength > maximumBytes) {
    throw new ConsoleError(413, "payload_too_large", "Payload is too large.");
  }
  return text;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function requiredText(value: unknown, field: string, maximum: number) {
  if (typeof value !== "string" || !value.trim() || value.trim().length > maximum) {
    throw new ConsoleError(422, "invalid_field", `${field} is invalid.`);
  }
  return value.trim();
}

function optionalText(value: unknown, field: string, maximum: number) {
  if (value === undefined || value === null || value === "") return null;
  return requiredText(value, field, maximum);
}

function stringArray(value: unknown, field: string, maximumItems: number, maximumLength: number) {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > maximumItems) {
    throw new ConsoleError(422, "invalid_field", `${field} is invalid.`);
  }
  return value.map((item) => requiredText(item, field, maximumLength));
}

function validatedIdempotencyKey(value: string) {
  const clean = value.trim();
  if (clean.length < 8 || clean.length > 200 || /[\u0000-\u001f]/.test(clean)) {
    throw new ConsoleError(422, "invalid_idempotency_key", "Idempotency-Key is invalid.");
  }
  return clean;
}

function upstreamMessage(value: unknown) {
  if (!isRecord(value)) return "Personal memory request failed.";
  const error = isRecord(value.error) ? value.error : {};
  const detail = isRecord(value.detail) ? value.detail : {};
  const message = typeof error.message === "string"
    ? error.message
    : typeof detail.message === "string"
      ? detail.message
      : typeof value.detail === "string"
        ? value.detail
        : null;
  return message && message.length <= 300 ? message : "Personal memory request failed.";
}

function errorResponse(error: unknown, requestId: string) {
  if (error instanceof ConsoleError) {
    return Response.json(
      { ok: false, error: { code: error.code, message: error.message, requestId } },
      { status: error.status, headers: responseHeaders(requestId) },
    );
  }
  console.error("TMCRA personal console request failed", {
    requestId,
    error: error instanceof Error ? error.name : "UnknownError",
  });
  return Response.json(
    { ok: false, error: { code: "internal_error", message: "Personal console request failed.", requestId } },
    { status: 500, headers: responseHeaders(requestId) },
  );
}

function responseHeaders(requestId: string) {
  return { ...NO_STORE_HEADERS, "X-Request-ID": requestId };
}
