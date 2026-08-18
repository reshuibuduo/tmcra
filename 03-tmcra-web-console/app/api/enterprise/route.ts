import { getChatGPTUser } from "@/app/chatgpt-auth";
import {
  ConsoleError,
  executeConsoleAction,
  getConsoleSnapshot,
  type ConsoleIdentity,
} from "@/db/console";

export const dynamic = "force-dynamic";

const NO_STORE_HEADERS = {
  "Cache-Control": "private, no-store, max-age=0",
  "X-Content-Type-Options": "nosniff",
};

export async function GET(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    const identity = await requireApiIdentity();
    const url = new URL(request.url);
    const organizationId =
      url.searchParams.get("organizationId") ?? url.searchParams.get("orgId") ?? undefined;
    const agentId = url.searchParams.get("agentId") ?? undefined;
    const rawLimit = url.searchParams.get("eventLimit");
    const eventLimit = rawLimit === null ? undefined : Number(rawLimit);
    const snapshot = await getConsoleSnapshot(identity, {
      organizationId,
      agentId,
      eventLimit,
    });
    return Response.json(
      { ok: true, ...snapshot },
      { status: 200, headers: NO_STORE_HEADERS },
    );
  } catch (error) {
    return errorResponse(error, requestId);
  }
}

export async function POST(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    const identity = await requireApiIdentity();
    requireSameOrigin(request);
    const contentType = request.headers.get("content-type")?.toLowerCase() ?? "";
    if (!contentType.startsWith("application/json")) {
      throw new ConsoleError(415, "unsupported_media_type", "Content-Type must be application/json.");
    }
    const body = await readJsonObject(request, 131_072);
    if (typeof body.action !== "string" || !body.action.trim()) {
      throw new ConsoleError(400, "invalid_action", "action is required.");
    }
    const result = await executeConsoleAction(
      identity,
      requestId,
      body.action.trim(),
      body.payload ?? {},
    );
    return Response.json(
      { ok: true, ...result },
      { status: 200, headers: NO_STORE_HEADERS },
    );
  } catch (error) {
    return errorResponse(error, requestId);
  }
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

async function readJsonObject(
  request: Request,
  maximumBytes: number,
): Promise<Record<string, unknown>> {
  const announcedLength = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(announcedLength) && announcedLength > maximumBytes) {
    throw new ConsoleError(413, "payload_too_large", "Request body is too large.");
  }
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > maximumBytes) {
    throw new ConsoleError(413, "payload_too_large", "Request body is too large.");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new ConsoleError(400, "invalid_json", "Request body must be valid JSON.");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new ConsoleError(400, "invalid_json", "Request body must be a JSON object.");
  }
  return parsed as Record<string, unknown>;
}

function errorResponse(error: unknown, requestId: string) {
  if (error instanceof ConsoleError) {
    return Response.json(
      { ok: false, error: { code: error.code, message: error.message, requestId } },
      { status: error.status, headers: NO_STORE_HEADERS },
    );
  }
  const message = error instanceof Error ? error.message : "Unexpected error";
  const databaseUnavailable = message.includes("D1 binding `DB` is unavailable");
  console.error("TMCRA console request failed", {
    requestId,
    error: error instanceof Error ? error.name : "UnknownError",
    message,
  });
  return Response.json(
    {
      ok: false,
      error: {
        code: databaseUnavailable ? "database_unavailable" : "internal_error",
        message: databaseUnavailable
          ? "The console database is not configured."
          : "The console request could not be completed.",
        requestId,
      },
    },
    { status: databaseUnavailable ? 503 : 500, headers: NO_STORE_HEADERS },
  );
}
