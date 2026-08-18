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

const SCOPE_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
const SESSION_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/;
const ON_BEHALF_SUBJECT_HEADER = "X-TMCRA-On-Behalf-Of-Subject";
const MAX_UPSTREAM_BYTES = 20_000_000;

export async function GET(request: Request) {
  return proxySessionGraph(request, false);
}

export async function POST(request: Request) {
  return proxySessionGraph(request, true);
}

async function proxySessionGraph(request: Request, refresh: boolean) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    const identity = await requireApiIdentity();
    if (refresh) requireSameOrigin(request);
    const access = await resolvePersonalMemoryAccess(identity);
    const url = new URL(request.url);
    const scopeName = resolveScope(url.searchParams.get("scope"), access.space.scopeName);
    const action = url.searchParams.get("action") ?? "atlas";
    const sessionId = optionalSessionId(url.searchParams.get("sessionId"));
    if (action === "session" && !sessionId) {
      throw new ConsoleError(422, "session_id_required", "Select a Session first.");
    }
    if (!refresh && !["atlas", "session", "visual-atlas"].includes(action)) {
      throw new ConsoleError(422, "invalid_session_graph_action", "Invalid Session Graph action.");
    }
    if (refresh && !["refresh-atlas", "refresh-session", "refresh-visual-atlas"].includes(action)) {
      throw new ConsoleError(422, "invalid_session_graph_action", "Invalid Session Graph refresh action.");
    }
    if (action === "refresh-session" && !sessionId) {
      throw new ConsoleError(422, "session_id_required", "Select a Session first.");
    }

    const binding = memoryBinding();
    const upstream = new URL(binding.baseUrl);
    upstream.pathname = action.includes("visual-atlas")
      ? `/v1/scopes/${encodeURIComponent(scopeName)}/memory-graph/visual-atlas`
      : `/v1/scopes/${encodeURIComponent(scopeName)}/memory-graph/sessions`;
    if (sessionId && ["session", "refresh-session"].includes(action)) {
      upstream.pathname += `/${encodeURIComponent(sessionId)}`;
    }
    if (refresh) upstream.pathname += "/refresh";
    upstream.search = "";
    upstream.hash = "";

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), refresh ? 20_000 : 25_000);
    let response: Response;
    try {
      response = await fetchMemoryApi(env, upstream, {
        method: refresh ? "POST" : "GET",
        headers: {
          Authorization: `Bearer ${binding.apiKey}`,
          Accept: "application/json",
          ...(refresh ? { [ON_BEHALF_SUBJECT_HEADER]: access.space.id } : {}),
          "X-Request-ID": requestId,
        },
        redirect: "error",
        signal: controller.signal,
      });
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") {
        throw new ConsoleError(504, "session_graph_timeout", "The Session Graph service timed out.");
      }
      throw new ConsoleError(502, "session_graph_unavailable", "The Session Graph service is unavailable.");
    } finally {
      clearTimeout(timeout);
    }
    const text = await readBoundedText(response, MAX_UPSTREAM_BYTES);
    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch {
      throw new ConsoleError(502, "invalid_session_graph_response", "The Session Graph service returned invalid JSON.");
    }
    if (!response.ok) {
      const error = upstreamError(body);
      throw new ConsoleError(normalizeStatus(response.status), error.code, error.message);
    }
    return Response.json(
      { ok: true, graph: body },
      { headers: noStoreHeaders(requestId) },
    );
  } catch (error) {
    return errorResponse(error, requestId);
  }
}

function memoryBinding() {
  const apiKey = String(env.TMCRA_MEMORY_API_CONTROL_KEY ?? "").trim();
  if (!apiKey.startsWith("tmcra_") || apiKey.length > 512) {
    throw new ConsoleError(503, "session_graph_configuration_invalid", "Session Graph configuration is invalid.");
  }
  try {
    return {
      apiKey,
      baseUrl: normalizeMemoryApiBaseUrl(
        String(env.TMCRA_MEMORY_API_CONTROL_BASE_URL ?? env.TMCRA_MEMORY_API_BASE_URL ?? "").trim(),
        { allowHttpLoopback: env.TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK === "1" },
      ),
    };
  } catch {
    throw new ConsoleError(503, "session_graph_configuration_invalid", "Session Graph configuration is invalid.");
  }
}

function resolveScope(value: string | null, namespace: string) {
  const scopeName = value?.trim() || `${namespace}-global`;
  if (!SCOPE_RE.test(namespace) || !SCOPE_RE.test(scopeName) || !scopeName.startsWith(`${namespace}-`)) {
    throw new ConsoleError(403, "session_graph_scope_forbidden", "The requested Scope is not part of this account.");
  }
  return scopeName;
}

function optionalSessionId(value: string | null) {
  if (value === null || value === "") return null;
  const clean = value.trim();
  if (!SESSION_RE.test(clean)) {
    throw new ConsoleError(422, "invalid_session_id", "The Session ID is invalid.");
  }
  return clean;
}

async function requireApiIdentity(): Promise<ConsoleIdentity> {
  const user = await getChatGPTUser();
  if (!user) throw new ConsoleError(401, "authentication_required", "Sign in with ChatGPT to continue.");
  return { email: user.email, displayName: user.displayName, fullName: user.fullName };
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

async function readBoundedText(response: Response, maximum: number) {
  const announced = Number(response.headers.get("content-length") ?? "0");
  if (Number.isFinite(announced) && announced > maximum) {
    throw new ConsoleError(502, "session_graph_response_too_large", "The Session Graph response is too large.");
  }
  const text = await response.text();
  if (new TextEncoder().encode(text).byteLength > maximum) {
    throw new ConsoleError(502, "session_graph_response_too_large", "The Session Graph response is too large.");
  }
  return text;
}

function upstreamError(value: unknown) {
  const fallback = { code: "session_graph_request_failed", message: "The Session Graph request could not be completed." };
  if (!value || typeof value !== "object" || Array.isArray(value)) return fallback;
  const detail = (value as Record<string, unknown>).error;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return fallback;
  const source = detail as Record<string, unknown>;
  return {
    code: typeof source.code === "string" && /^[a-z0-9_]{1,80}$/.test(source.code) ? source.code : fallback.code,
    message: typeof source.message === "string" && source.message.length <= 300 ? source.message : fallback.message,
  };
}

function normalizeStatus(status: number) {
  return [400, 401, 403, 404, 409, 413, 422, 429, 503, 504].includes(status) ? status : 502;
}

function errorResponse(error: unknown, requestId: string) {
  if (error instanceof ConsoleError) {
    return Response.json(
      { ok: false, error: { code: error.code, message: error.message, requestId } },
      { status: error.status, headers: noStoreHeaders(requestId) },
    );
  }
  console.error("TMCRA Session Graph proxy failed", {
    requestId,
    error: error instanceof Error ? error.name : "UnknownError",
  });
  return Response.json(
    { ok: false, error: { code: "internal_error", message: "The Session Graph request could not be completed.", requestId } },
    { status: 500, headers: noStoreHeaders(requestId) },
  );
}

function noStoreHeaders(requestId: string) {
  return {
    "Cache-Control": "private, no-store, max-age=0",
    "X-Content-Type-Options": "nosniff",
    "X-Request-ID": requestId,
  };
}
