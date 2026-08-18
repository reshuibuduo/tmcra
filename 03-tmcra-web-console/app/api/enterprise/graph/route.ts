import { env } from "cloudflare:workers";

import { getChatGPTUser } from "@/app/chatgpt-auth";
import { normalizeMemoryApiBaseUrl } from "@/app/api/device/v1/upstream-client.mjs";
import { fetchMemoryApi } from "@/app/lib/memory-api-fetch";
import {
  ConsoleError,
  resolveConsoleGraphAccess,
  type ConsoleIdentity,
} from "@/db/console";

export const dynamic = "force-dynamic";

const NO_STORE_HEADERS = {
  "Cache-Control": "private, no-store, max-age=0",
  "X-Content-Type-Options": "nosniff",
};
const SCOPE_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
const LAYERS_RE = /^(?:slow|fast|source)(?:,(?:slow|fast|source))*$/;
const NARRATIVE_FOCUS_RE = /^(?:all|decision|milestone|goal|issue|preference|relationship|fact)$/;
const MAX_UPSTREAM_BYTES = 2_500_000;

type TenantBinding = {
  apiKey: string;
  baseUrl?: string;
  defaultScope?: string;
  scopeByAgent?: Record<string, string>;
};

type BindingMap = Record<string, TenantBinding>;

export async function GET(request: Request) {
  return proxyGraphRequest(request, false);
}

export async function POST(request: Request) {
  return proxyGraphRequest(request, true);
}

async function proxyGraphRequest(request: Request, mutation: boolean) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    const identity = await requireApiIdentity();
    if (mutation) requireSameOrigin(request);
    const url = new URL(request.url);
    const organizationId = requiredToken(url.searchParams.get("organizationId"), "organizationId", 100);
    const agentId = requiredToken(url.searchParams.get("agentId"), "agentId", 100);
    const access = await resolveConsoleGraphAccess(identity, {
      organizationId,
      agentId,
    });
    if (access.organization.sampleMode) {
      throw new ConsoleError(
        409,
        "sample_graph_unavailable",
        "The sample workspace is not connected to a production memory graph.",
      );
    }
    const binding = tenantBinding(access.organization.id);
    const scopeName = resolveScope(binding, access.agent.id, access.agent.slug);
    const action = url.searchParams.get("action") ?? "overview";
    const upstream = graphUpstreamUrl(
      binding,
      action,
      scopeName,
      url.searchParams,
      mutation,
    );
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), mutation ? 120_000 : 20_000);
    let upstreamResponse: Response;
    try {
      upstreamResponse = await fetchMemoryApi(env, upstream, {
        method: mutation ? "POST" : "GET",
        headers: {
          Authorization: `Bearer ${binding.apiKey}`,
          Accept: "application/json",
          ...(mutation ? { "Content-Type": "application/json" } : {}),
          "X-Request-ID": requestId,
        },
        body: mutation ? JSON.stringify(await readTraceBody(request)) : undefined,
        redirect: "error",
        signal: controller.signal,
      });
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") {
        throw new ConsoleError(504, "memory_graph_timeout", "The memory graph service timed out.");
      }
      throw new ConsoleError(502, "memory_graph_unavailable", "The memory graph service is unavailable.");
    } finally {
      clearTimeout(timeout);
    }

    const text = await readBoundedText(upstreamResponse, MAX_UPSTREAM_BYTES);
    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch {
      throw new ConsoleError(502, "invalid_graph_response", "The memory graph service returned invalid JSON.");
    }
    if (!upstreamResponse.ok) {
      const upstreamError = extractUpstreamError(body);
      throw new ConsoleError(
        normalizeUpstreamStatus(upstreamResponse.status),
        upstreamError.code,
        upstreamError.message,
      );
    }
    return Response.json(
      { ok: true, graph: body },
      { status: 200, headers: NO_STORE_HEADERS },
    );
  } catch (error) {
    return errorResponse(error, requestId);
  }
}

function tenantBinding(organizationId: string): TenantBinding {
  const raw = String(env.TMCRA_MEMORY_API_TENANT_BINDINGS ?? "").trim();
  if (!raw) {
    throw new ConsoleError(
      503,
      "memory_graph_not_configured",
      "This workspace is not connected to the production memory graph service.",
    );
  }
  let bindings: BindingMap;
  try {
    bindings = JSON.parse(raw) as BindingMap;
  } catch {
    throw new ConsoleError(503, "memory_graph_configuration_invalid", "Memory graph configuration is invalid.");
  }
  const binding = bindings[organizationId];
  if (!binding || typeof binding !== "object" || typeof binding.apiKey !== "string") {
    throw new ConsoleError(
      503,
      "memory_graph_not_configured",
      "This workspace is not connected to the production memory graph service.",
    );
  }
  const apiKey = binding.apiKey.trim();
  if (!apiKey.startsWith("tmcra_") || apiKey.length > 512) {
    throw new ConsoleError(503, "memory_graph_configuration_invalid", "Memory graph configuration is invalid.");
  }
  return { ...binding, apiKey };
}

function resolveScope(binding: TenantBinding, agentId: string, agentSlug: string): string {
  const scope = binding.scopeByAgent?.[agentId]
    ?? binding.scopeByAgent?.[agentSlug]
    ?? binding.defaultScope;
  if (!scope || !SCOPE_RE.test(scope)) {
    throw new ConsoleError(
      503,
      "memory_graph_scope_not_configured",
      "This agent is not mapped to a production memory scope.",
    );
  }
  return scope;
}

function graphUpstreamUrl(
  binding: TenantBinding,
  action: string,
  scopeName: string,
  search: URLSearchParams,
  mutation: boolean,
): URL {
  const tenantBaseUrl = String(binding.baseUrl ?? "").trim();
  const base = tenantBaseUrl || String(
    env.TMCRA_MEMORY_API_CONTROL_BASE_URL ?? env.TMCRA_MEMORY_API_BASE_URL ?? "",
  ).trim();
  let origin: URL;
  try {
    origin = new URL(normalizeMemoryApiBaseUrl(base, {
      // Tenant-supplied endpoints remain HTTPS-only. Loopback applies only to
      // the deployment-wide co-located control endpoint.
      allowHttpLoopback:
        !tenantBaseUrl &&
        env.TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK === "1",
    }));
  } catch {
    throw new ConsoleError(503, "memory_graph_configuration_invalid", "Memory graph configuration is invalid.");
  }
  origin.pathname = `/v1/scopes/${encodeURIComponent(scopeName)}/memory-graph`;
  origin.search = "";
  origin.hash = "";

  if (mutation) {
    if (action !== "trace") {
      throw new ConsoleError(422, "invalid_graph_action", "POST only supports the trace action.");
    }
    origin.pathname += "/trace";
    return origin;
  }

  if (action === "overview") {
    const layers = search.get("layers") ?? "slow";
    if (!LAYERS_RE.test(layers)) {
      throw new ConsoleError(422, "invalid_graph_layers", "Invalid memory graph layer selection.");
    }
    origin.searchParams.set("layers", layers);
    copyInteger(search, origin.searchParams, "limit", 1, 300, 180);
    copyOpaque(search, origin.searchParams, "cursor", 512);
    copyOpaque(search, origin.searchParams, "query", 200);
    return origin;
  }
  if (action === "narrative") {
    const focus = search.get("focus") ?? "all";
    if (!NARRATIVE_FOCUS_RE.test(focus)) {
      throw new ConsoleError(422, "invalid_narrative_focus", "Invalid narrative graph focus.");
    }
    origin.pathname += "/narrative";
    origin.searchParams.set("focus", focus);
    copyInteger(search, origin.searchParams, "limit", 1, 60, 36);
    copyOpaque(search, origin.searchParams, "query", 200);
    return origin;
  }
  const memoryId = requiredToken(search.get("memoryId"), "memoryId", 512);
  origin.pathname += `/nodes/${encodeURIComponent(memoryId)}`;
  if (action === "neighbors") {
    origin.pathname += "/neighbors";
    const layers = search.get("layers") ?? "slow,fast,source";
    if (!LAYERS_RE.test(layers)) {
      throw new ConsoleError(422, "invalid_graph_layers", "Invalid memory graph layer selection.");
    }
    origin.searchParams.set("layers", layers);
    copyInteger(search, origin.searchParams, "depth", 1, 2, 1);
    copyInteger(search, origin.searchParams, "limit", 1, 120, 80);
    copyOpaque(search, origin.searchParams, "cursor", 512);
    return origin;
  }
  if (action === "evidence") {
    origin.pathname += "/evidence";
    copyInteger(search, origin.searchParams, "limit", 1, 25, 10);
    copyOpaque(search, origin.searchParams, "cursor", 512);
    return origin;
  }
  throw new ConsoleError(422, "invalid_graph_action", "Invalid memory graph action.");
}

async function readTraceBody(request: Request) {
  const contentType = request.headers.get("content-type")?.toLowerCase() ?? "";
  if (!contentType.startsWith("application/json")) {
    throw new ConsoleError(415, "unsupported_media_type", "Content-Type must be application/json.");
  }
  const text = await readBoundedText(request, 120_000);
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    throw new ConsoleError(400, "invalid_json", "Request body must be valid JSON.");
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new ConsoleError(400, "invalid_json", "Request body must be a JSON object.");
  }
  const value = body as Record<string, unknown>;
  if (typeof value.query !== "string" || !value.query.trim() || value.query.length > 100_000) {
    throw new ConsoleError(422, "invalid_query", "query must be 1-100000 characters.");
  }
  if (value.max_windows !== undefined && value.max_windows !== 8) {
    throw new ConsoleError(422, "invalid_max_windows", "max_windows must be 8.");
  }
  return {
    query: value.query.trim(),
    query_time: typeof value.query_time === "string" ? value.query_time : null,
    max_windows: 8,
    debug: false,
  };
}

async function requireApiIdentity(): Promise<ConsoleIdentity> {
  const user = await getChatGPTUser();
  if (!user) {
    throw new ConsoleError(401, "authentication_required", "Sign in with ChatGPT to continue.");
  }
  return {
    email: user.email,
    displayName: user.displayName,
    fullName: user.fullName,
  };
}

function requireSameOrigin(request: Request) {
  const site = request.headers.get("sec-fetch-site");
  if (site === "cross-site") {
    throw new ConsoleError(403, "cross_site_request", "Cross-site requests are not allowed.");
  }
  const origin = request.headers.get("origin");
  if (origin && origin !== new URL(request.url).origin) {
    throw new ConsoleError(403, "origin_mismatch", "Request origin is not allowed.");
  }
}

function requiredToken(value: string | null, field: string, maximum: number) {
  const clean = value?.trim() ?? "";
  if (!clean || clean.length > maximum || !/^[A-Za-z0-9._:-]+$/.test(clean)) {
    throw new ConsoleError(422, "invalid_field", `${field} is invalid.`);
  }
  return clean;
}

function copyInteger(
  source: URLSearchParams,
  target: URLSearchParams,
  name: string,
  minimum: number,
  maximum: number,
  fallback: number,
) {
  const raw = source.get(name);
  const value = raw === null ? fallback : Number(raw);
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new ConsoleError(422, "invalid_field", `${name} is invalid.`);
  }
  target.set(name, String(value));
}

function copyOpaque(source: URLSearchParams, target: URLSearchParams, name: string, maximum: number) {
  const value = source.get(name);
  if (value === null || value === "") return;
  if (value.length > maximum || /[\u0000-\u001f]/.test(value)) {
    throw new ConsoleError(422, "invalid_field", `${name} is invalid.`);
  }
  target.set(name, value);
}

async function readBoundedText(response: Request | Response, maximum: number): Promise<string> {
  const announced = Number(response.headers.get("content-length") ?? "0");
  if (Number.isFinite(announced) && announced > maximum) {
    throw new ConsoleError(502, "graph_response_too_large", "The memory graph response is too large.");
  }
  const text = await response.text();
  if (new TextEncoder().encode(text).byteLength > maximum) {
    throw new ConsoleError(502, "graph_response_too_large", "The memory graph response is too large.");
  }
  return text;
}

function extractUpstreamError(value: unknown) {
  const fallback = {
    code: "memory_graph_request_failed",
    message: "The memory graph request could not be completed.",
  };
  if (!value || typeof value !== "object" || Array.isArray(value)) return fallback;
  const detail = (value as Record<string, unknown>).error;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return fallback;
  const source = detail as Record<string, unknown>;
  return {
    code: typeof source.code === "string" && /^[a-z0-9_]{1,80}$/.test(source.code)
      ? source.code
      : fallback.code,
    message: typeof source.message === "string" && source.message.length <= 300
      ? source.message
      : fallback.message,
  };
}

function normalizeUpstreamStatus(status: number) {
  return [400, 401, 403, 404, 409, 413, 422, 429, 503].includes(status) ? status : 502;
}

function errorResponse(error: unknown, requestId: string) {
  if (error instanceof ConsoleError) {
    return Response.json(
      { ok: false, error: { code: error.code, message: error.message, requestId } },
      { status: error.status, headers: NO_STORE_HEADERS },
    );
  }
  console.error("TMCRA memory graph proxy failed", {
    requestId,
    error: error instanceof Error ? error.name : "UnknownError",
  });
  return Response.json(
    {
      ok: false,
      error: {
        code: "internal_error",
        message: "The memory graph request could not be completed.",
        requestId,
      },
    },
    { status: 500, headers: NO_STORE_HEADERS },
  );
}
