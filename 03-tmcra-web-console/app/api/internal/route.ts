import { getChatGPTUser } from "@/app/chatgpt-auth";
import { env } from "cloudflare:workers";
import {
  executeInternalAction,
  getInternalSnapshot,
  InternalError,
  type InternalIdentity,
} from "@/db/internal";
import { collectInternalOperations } from "./operations";
import { fetchMemoryApi } from "@/app/lib/memory-api-fetch";

export const dynamic = "force-dynamic";

const MAXIMUM_BODY_BYTES = 65_536;

function responseHeaders(requestId: string): Record<string, string> {
  return {
    "Cache-Control": "private, no-store, max-age=0",
    "Content-Security-Policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Request-ID": requestId,
    "X-Robots-Tag": "noindex, nofollow, noarchive",
  };
}

export async function GET(request: Request) {
  const requestId = getRequestId(request);
  try {
    const identity = await requireIdentity();
    const url = new URL(request.url);
    const organizationId = url.searchParams.get("organizationId") ?? undefined;
    const snapshot = await getInternalSnapshot(
      identity,
      { organizationId },
      internalBootstrapConfig(),
    );
    // Run external probes only after the database-backed internal RBAC guard
    // succeeds. Unauthorized callers must not be able to use this route as a
    // health or deployment oracle.
    const operations = await collectInternalOperations({
      memoryApiBaseUrl: env.TMCRA_MEMORY_API_BASE_URL,
      memoryApiControlBaseUrl: env.TMCRA_MEMORY_API_CONTROL_BASE_URL,
      allowHttpLoopback:
        env.TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK === "1",
      staffMonitoringKey: env.TMCRA_MEMORY_API_STAFF_MONITORING_KEY,
      requestUrl: request.url,
      fetchImpl: (input, init) => fetchMemoryApi(env, input, init),
    });
    return Response.json(
      { ok: true, ...snapshot, operations },
      { status: 200, headers: responseHeaders(requestId) },
    );
  } catch (error) {
    return errorResponse(error, requestId);
  }
}

export async function POST(request: Request) {
  const requestId = getRequestId(request);
  try {
    requireStrictSameOrigin(request);
    const identity = await requireIdentity();
    const contentType = request.headers.get("content-type")?.toLowerCase() ?? "";
    if (!contentType.startsWith("application/json")) {
      throw new InternalError(
        415,
        "unsupported_media_type",
        "Content-Type must be application/json.",
      );
    }
    const body = await readJsonObject(request, MAXIMUM_BODY_BYTES);
    if (typeof body.action !== "string" || !body.action.trim()) {
      throw new InternalError(400, "invalid_action", "action is required.");
    }
    const result = await executeInternalAction(
      identity,
      requestId,
      body.action.trim(),
      body.payload ?? {},
      internalBootstrapConfig(),
    );
    return Response.json(
      { ok: true, ...result },
      { status: 200, headers: responseHeaders(requestId) },
    );
  } catch (error) {
    return errorResponse(error, requestId);
  }
}

function internalBootstrapConfig() {
  return { ownerEmail: env.TMCRA_INTERNAL_BOOTSTRAP_OWNER_EMAIL };
}

async function requireIdentity(): Promise<InternalIdentity> {
  const user = await getChatGPTUser();
  if (!user) {
    throw new InternalError(
      401,
      "authentication_required",
      "Sign in with ChatGPT to continue.",
    );
  }
  return {
    email: user.email,
    displayName: user.displayName,
    fullName: user.fullName,
  };
}

function requireStrictSameOrigin(request: Request) {
  const requestOrigin = new URL(request.url).origin;
  const origin = request.headers.get("origin");
  if (!origin || origin !== requestOrigin) {
    throw new InternalError(403, "origin_mismatch", "Request origin is not allowed.");
  }
  const fetchSite = request.headers.get("sec-fetch-site");
  if (fetchSite && fetchSite !== "same-origin") {
    throw new InternalError(403, "cross_site_request", "Cross-site requests are not allowed.");
  }
  if (request.headers.get("sec-fetch-mode") === "navigate") {
    throw new InternalError(403, "navigation_not_allowed", "Navigation requests are not allowed.");
  }
}

async function readJsonObject(
  request: Request,
  maximumBytes: number,
): Promise<Record<string, unknown>> {
  const announcedLength = request.headers.get("content-length");
  if (announcedLength !== null) {
    const size = Number(announcedLength);
    if (!Number.isFinite(size) || size < 0 || size > maximumBytes) {
      throw new InternalError(413, "payload_too_large", "Request body is too large.");
    }
  }
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > maximumBytes) {
    throw new InternalError(413, "payload_too_large", "Request body is too large.");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new InternalError(400, "invalid_json", "Request body must be valid JSON.");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new InternalError(400, "invalid_json", "Request body must be a JSON object.");
  }
  return parsed as Record<string, unknown>;
}

function getRequestId(request: Request): string {
  void request;
  return crypto.randomUUID();
}

function errorResponse(error: unknown, requestId: string) {
  if (error instanceof InternalError) {
    const headers = responseHeaders(requestId);
    if (error.status === 429) headers["Retry-After"] = "60";
    return Response.json(
      { ok: false, error: { code: error.code, message: error.message, requestId } },
      { status: error.status, headers },
    );
  }
  const message = error instanceof Error ? error.message : "Unexpected error";
  const databaseUnavailable = message.includes("D1 binding `DB` is unavailable");
  console.error("TMCRA internal request failed", {
    requestId,
    error: error instanceof Error ? error.name : "UnknownError",
  });
  return Response.json(
    {
      ok: false,
      error: {
        code: databaseUnavailable ? "database_unavailable" : "internal_error",
        message: databaseUnavailable
          ? "The internal database is not configured."
          : "The internal request could not be completed.",
        requestId,
      },
    },
    {
      status: databaseUnavailable ? 503 : 500,
      headers: responseHeaders(requestId),
    },
  );
}
