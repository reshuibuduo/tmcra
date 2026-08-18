import { getChatGPTUser } from "@/app/chatgpt-auth";
import {
  ConsoleError,
  resolvePersonalMemoryAccess,
  type ConsoleIdentity,
} from "@/db/console";
import {
  approveDeviceAuthorization,
  denyDeviceAuthorization,
  getDeviceApproval,
  listDeviceConnections,
  revokeDeviceConnection,
} from "@/app/api/device/v1/device-service";
import { deviceRequestSource } from "@/app/api/device/v1/http";

export const dynamic = "force-dynamic";

const RESPONSE_HEADERS = {
  "Cache-Control": "private, no-store, max-age=0",
  "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "X-Robots-Tag": "noindex, nofollow",
};

export async function GET(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    const access = await requirePersonalAccess();
    const userCode = new URL(request.url).searchParams.get("user_code");
    const [authorization, connections] = await Promise.all([
      userCode
        ? getDeviceApproval(access, userCode, deviceRequestSource(request))
        : Promise.resolve(null),
      listDeviceConnections(access),
    ]);
    return Response.json(
      { ok: true, authorization, connections },
      { headers: { ...RESPONSE_HEADERS, "X-Request-ID": requestId } },
    );
  } catch (error) {
    return errorResponse(error, requestId);
  }
}

export async function POST(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    requireSameOrigin(request);
    const access = await requirePersonalAccess();
    const body = await readJsonObject(request, 4_096);
    const action = typeof body.action === "string" ? body.action : "";
    let result: unknown;
    if (action === "approve") {
      result = await approveDeviceAuthorization(
        access,
        body.userCode,
        deviceRequestSource(request),
      );
    } else if (action === "deny") {
      result = await denyDeviceAuthorization(
        access,
        body.userCode,
        deviceRequestSource(request),
      );
    } else if (action === "revoke") {
      result = await revokeDeviceConnection(access, body.connectionId, requestId);
    } else {
      throw new ConsoleError(422, "invalid_action", "Unsupported authorization action.");
    }
    return Response.json(
      { ok: true, result },
      { headers: { ...RESPONSE_HEADERS, "X-Request-ID": requestId } },
    );
  } catch (error) {
    return errorResponse(error, requestId);
  }
}

async function requirePersonalAccess() {
  const user = await getChatGPTUser();
  if (!user) {
    throw new ConsoleError(
      401,
      "authentication_required",
      "Sign in with ChatGPT to continue.",
    );
  }
  const identity: ConsoleIdentity = {
    email: user.email,
    displayName: user.displayName,
    fullName: user.fullName,
  };
  return resolvePersonalMemoryAccess(identity);
}

function requireSameOrigin(request: Request) {
  const expected = new URL(request.url).origin;
  const origin = request.headers.get("origin");
  if (!origin || origin !== expected) {
    throw new ConsoleError(403, "origin_mismatch", "Request origin is not allowed.");
  }
  const site = request.headers.get("sec-fetch-site");
  if (site && site !== "same-origin") {
    throw new ConsoleError(
      403,
      "cross_site_request",
      "Cross-site requests are not allowed.",
    );
  }
}

async function readJsonObject(request: Request, maximumBytes: number) {
  const contentType = request.headers.get("content-type")?.toLowerCase() ?? "";
  if (!contentType.startsWith("application/json")) {
    throw new ConsoleError(
      415,
      "unsupported_media_type",
      "Content-Type must be application/json.",
    );
  }
  const announced = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(announced) && announced > maximumBytes) {
    throw new ConsoleError(413, "payload_too_large", "Payload is too large.");
  }
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > maximumBytes) {
    throw new ConsoleError(413, "payload_too_large", "Payload is too large.");
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new ConsoleError(400, "invalid_json", "Request body must be valid JSON.");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ConsoleError(400, "invalid_json", "Request body must be a JSON object.");
  }
  return value as Record<string, unknown>;
}

function errorResponse(error: unknown, requestId: string) {
  if (error instanceof ConsoleError) {
    return Response.json(
      { ok: false, error: { code: error.code, message: error.message, requestId } },
      {
        status: error.status,
        headers: {
          ...RESPONSE_HEADERS,
          "X-Request-ID": requestId,
          ...(error.status === 429 ? { "Retry-After": "60" } : {}),
        },
      },
    );
  }
  console.error("TMCRA device authorization console request failed", {
    requestId,
    error: error instanceof Error ? error.name : "UnknownError",
  });
  return Response.json(
    {
      ok: false,
      error: {
        code: "internal_error",
        message: "Device authorization request failed.",
        requestId,
      },
    },
    {
      status: 500,
      headers: { ...RESPONSE_HEADERS, "X-Request-ID": requestId },
    },
  );
}
