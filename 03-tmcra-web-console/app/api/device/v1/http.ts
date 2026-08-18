import { ConsoleError } from "@/db/console";
import { DeviceFlowError } from "./device-service";

export const DEVICE_RESPONSE_HEADERS = {
  "Cache-Control": "no-store, max-age=0",
  "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "X-Robots-Tag": "noindex, nofollow",
};

export function deviceRequestSource(request: Request) {
  return request.headers.get("cf-connecting-ip") ?? "unknown";
}

export async function readDeviceJson(request: Request, maximumBytes = 4_096) {
  const contentType = request.headers.get("content-type")?.toLowerCase() ?? "";
  if (!contentType.startsWith("application/json")) {
    throw new DeviceFlowError(
      415,
      "unsupported_media_type",
      "Content-Type must be application/json.",
    );
  }
  const announced = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(announced) && announced > maximumBytes) {
    throw new DeviceFlowError(413, "payload_too_large", "Payload is too large.");
  }
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > maximumBytes) {
    throw new DeviceFlowError(413, "payload_too_large", "Payload is too large.");
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new DeviceFlowError(400, "invalid_json", "Request body must be valid JSON.");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new DeviceFlowError(400, "invalid_json", "Request body must be a JSON object.");
  }
  return value as Record<string, unknown>;
}

export function deviceErrorResponse(error: unknown, requestId: string) {
  if (error instanceof DeviceFlowError || error instanceof ConsoleError) {
    const interval = error instanceof DeviceFlowError ? error.interval : undefined;
    return Response.json(
      {
        ok: false,
        error: {
          code: error.code,
          message: error.message,
          ...(interval === undefined ? {} : { interval }),
          requestId,
        },
      },
      {
        status: error.status,
        headers: {
          ...DEVICE_RESPONSE_HEADERS,
          "X-Request-ID": requestId,
          ...(interval === undefined ? {} : { "Retry-After": String(interval) }),
        },
      },
    );
  }
  console.error("TMCRA device authorization request failed", {
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
      headers: { ...DEVICE_RESPONSE_HEADERS, "X-Request-ID": requestId },
    },
  );
}
