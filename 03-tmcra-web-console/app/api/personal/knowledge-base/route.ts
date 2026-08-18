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
const ON_BEHALF_SUBJECT_HEADER = "X-TMCRA-On-Behalf-Of-Subject";
const MAX_UPSTREAM_BYTES = 20_000_000;

export async function GET(request: Request) {
  return proxyKnowledgeBase(request, false);
}

export async function POST(request: Request) {
  return proxyKnowledgeBase(request, true);
}

async function proxyKnowledgeBase(request: Request, refresh: boolean) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    const identity = await requireApiIdentity();
    if (refresh) requireSameOrigin(request);
    const access = await resolvePersonalMemoryAccess(identity);
    const url = new URL(request.url);
    const scopeName = resolveScope(url.searchParams.get("scope"), access.space.scopeName);
    const binding = memoryBinding();
    const upstream = new URL(binding.baseUrl);
    upstream.pathname = `/v1/scopes/${encodeURIComponent(scopeName)}/knowledge-base${refresh ? "/refresh" : ""}`;
    upstream.search = "";
    upstream.hash = "";

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), refresh ? 20_000 : 30_000);
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
        throw new ConsoleError(504, "knowledge_base_timeout", "The Personal Knowledge service timed out.");
      }
      throw new ConsoleError(502, "knowledge_base_unavailable", "The Personal Knowledge service is unavailable.");
    } finally {
      clearTimeout(timeout);
    }

    const text = await readBoundedText(response, MAX_UPSTREAM_BYTES);
    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch {
      throw new ConsoleError(502, "invalid_knowledge_base_response", "The Personal Knowledge service returned invalid JSON.");
    }
    if (!response.ok) {
      const error = upstreamError(body);
      throw new ConsoleError(normalizeStatus(response.status), error.code, error.message);
    }
    return Response.json(
      refresh ? { ok: true, refresh: body } : { ok: true, knowledgeBase: body },
      { headers: noStoreHeaders(requestId) },
    );
  } catch (error) {
    return errorResponse(error, requestId);
  }
}

function memoryBinding() {
  const apiKey = String(env.TMCRA_MEMORY_API_CONTROL_KEY ?? "").trim();
  if (!apiKey.startsWith("tmcra_") || apiKey.length > 512) {
    throw new ConsoleError(503, "knowledge_base_configuration_invalid", "Personal Knowledge configuration is invalid.");
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
    throw new ConsoleError(503, "knowledge_base_configuration_invalid", "Personal Knowledge configuration is invalid.");
  }
}

function resolveScope(value: string | null, namespace: string) {
  const scopeName = value?.trim() || `${namespace}-global`;
  if (!SCOPE_RE.test(namespace) || !SCOPE_RE.test(scopeName) || !scopeName.startsWith(`${namespace}-`)) {
    throw new ConsoleError(403, "knowledge_base_scope_forbidden", "The requested Scope is not part of this account.");
  }
  return scopeName;
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
    throw new ConsoleError(502, "knowledge_base_response_too_large", "The Personal Knowledge response is too large.");
  }
  const text = await response.text();
  if (new TextEncoder().encode(text).byteLength > maximum) {
    throw new ConsoleError(502, "knowledge_base_response_too_large", "The Personal Knowledge response is too large.");
  }
  return text;
}

function upstreamError(value: unknown) {
  const fallback = { code: "knowledge_base_request_failed", message: "The Personal Knowledge request could not be completed." };
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
  console.error("TMCRA Personal Knowledge proxy failed", {
    requestId,
    error: error instanceof Error ? error.name : "UnknownError",
  });
  return Response.json(
    { ok: false, error: { code: "internal_error", message: "The Personal Knowledge request could not be completed.", requestId } },
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
