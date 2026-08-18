import { env } from "cloudflare:workers";

import { getChatGPTUser } from "@/app/chatgpt-auth";
import { normalizeMemoryApiBaseUrl } from "@/app/api/device/v1/upstream-client.mjs";
import { fetchMemoryApi } from "@/app/lib/memory-api-fetch";
import {
  ConsoleError,
  resolvePersonalMemoryAccess,
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
const ON_BEHALF_SUBJECT_HEADER = "X-TMCRA-On-Behalf-Of-Subject";

type MemoryBinding = {
  apiKey: string;
  baseUrl: string;
};

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
    const access = await resolvePersonalMemoryAccess(identity);
    const binding = memoryBinding();
    const scopeName = resolveScope(url.searchParams.get("scope"), access.space.scopeName);
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
          ...(mutation ? { [ON_BEHALF_SUBJECT_HEADER]: access.space.id } : {}),
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
      console.warn("TMCRA memory graph upstream fetch failed", {
        requestId,
        error: error instanceof Error ? error.name : "UnknownError",
        message: error instanceof Error ? error.message.slice(0, 240) : "Unknown failure",
      });
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
      { status: 200, headers: responseHeaders(requestId) },
    );
  } catch (error) {
    return errorResponse(error, requestId);
  }
}

function memoryBinding(): MemoryBinding {
  const apiKey = String(env.TMCRA_MEMORY_API_CONTROL_KEY ?? "").trim();
  if (!apiKey.startsWith("tmcra_") || apiKey.length > 512) {
    throw new ConsoleError(503, "memory_graph_configuration_invalid", "Memory graph configuration is invalid.");
  }
  const baseUrl = String(
    env.TMCRA_MEMORY_API_CONTROL_BASE_URL ?? env.TMCRA_MEMORY_API_BASE_URL ?? "",
  ).trim();
  try {
    return {
      apiKey,
      baseUrl: normalizeMemoryApiBaseUrl(baseUrl, {
        allowHttpLoopback:
          env.TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK === "1",
      }),
    };
  } catch {
    throw new ConsoleError(503, "memory_graph_configuration_invalid", "Memory graph configuration is invalid.");
  }
}

function resolveScope(requestedScope: string | null, namespace: string): string {
  const scopeName = requestedScope?.trim() || `${namespace}-global`;
  if (!SCOPE_RE.test(namespace) || !SCOPE_RE.test(scopeName) || !scopeName.startsWith(`${namespace}-`)) {
    throw new ConsoleError(403, "memory_graph_scope_forbidden", "The requested memory Scope is not part of this account.");
  }
  return scopeName;
}

function graphUpstreamUrl(
  binding: MemoryBinding,
  action: string,
  scopeName: string,
  search: URLSearchParams,
  mutation: boolean,
): URL {
  const base = binding.baseUrl;
  let origin: URL;
  try {
    origin = new URL(base);
  } catch {
    throw new ConsoleError(503, "memory_graph_configuration_invalid", "Memory graph configuration is invalid.");
  }
  // memoryBinding() already enforces HTTPS or explicitly enabled loopback HTTP.
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
      { status: error.status, headers: responseHeaders(requestId) },
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
    { status: 500, headers: responseHeaders(requestId) },
  );
}

function responseHeaders(requestId: string) {
  return { ...NO_STORE_HEADERS, "X-Request-ID": requestId };
}
