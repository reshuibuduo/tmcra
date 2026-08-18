import { env } from "cloudflare:workers";

import { runDeviceMaintenance } from "../device-service";
import { DEVICE_RESPONSE_HEADERS } from "../http";

export const dynamic = "force-dynamic";

const MINIMUM_SECRET_LENGTH = 43;
const MAXIMUM_SECRET_LENGTH = 256;

export async function POST(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  const configuredSecret = env.TMCRA_DEVICE_MAINTENANCE_SECRET?.trim() ?? "";
  if (configuredSecret.length < MINIMUM_SECRET_LENGTH) {
    console.error("TMCRA device maintenance is not configured", { requestId });
    return maintenanceResponse(
      { ok: false, error: { code: "maintenance_unavailable", requestId } },
      503,
      requestId,
    );
  }

  const authorization = request.headers.get("authorization") ?? "";
  const presentedSecret = authorization.startsWith("Bearer ")
    ? authorization.slice("Bearer ".length).trim()
    : "";
  if (
    presentedSecret.length < MINIMUM_SECRET_LENGTH ||
    presentedSecret.length > MAXIMUM_SECRET_LENGTH ||
    !(await constantTimeSecretEqual(presentedSecret, configuredSecret))
  ) {
    return maintenanceResponse(
      { ok: false, error: { code: "unauthorized", requestId } },
      401,
      requestId,
      { "WWW-Authenticate": 'Bearer realm="tmcra-device-maintenance"' },
    );
  }

  try {
    const result = await runDeviceMaintenance(requestId);
    return maintenanceResponse({ ok: true, ...result }, 200, requestId);
  } catch (error) {
    console.error("TMCRA device maintenance failed", {
      requestId,
      error: error instanceof Error ? error.name : "UnknownError",
    });
    return maintenanceResponse(
      { ok: false, error: { code: "maintenance_failed", requestId } },
      500,
      requestId,
    );
  }
}

async function constantTimeSecretEqual(left: string, right: string) {
  const encoder = new TextEncoder();
  const [leftHash, rightHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(left)),
    crypto.subtle.digest("SHA-256", encoder.encode(right)),
  ]);
  const leftBytes = new Uint8Array(leftHash);
  const rightBytes = new Uint8Array(rightHash);
  let difference = 0;
  for (let index = 0; index < leftBytes.length; index += 1) {
    difference |= leftBytes[index] ^ rightBytes[index];
  }
  return difference === 0;
}

function maintenanceResponse(
  body: unknown,
  status: number,
  requestId: string,
  extraHeaders: Record<string, string> = {},
) {
  return Response.json(body, {
    status,
    headers: {
      ...DEVICE_RESPONSE_HEADERS,
      ...extraHeaders,
      "X-Request-ID": requestId,
    },
  });
}
