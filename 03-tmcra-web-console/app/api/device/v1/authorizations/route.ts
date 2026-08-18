import { startDeviceAuthorization } from "../device-service";
import {
  DEVICE_RESPONSE_HEADERS,
  deviceRequestSource,
  deviceErrorResponse,
  readDeviceJson,
} from "../http";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    const body = await readDeviceJson(request);
    const result = await startDeviceAuthorization({
      clientId: body.clientId,
      codeChallenge: body.codeChallenge,
      codeChallengeMethod: body.codeChallengeMethod,
      clientName: body.clientName,
      requestOrigin: new URL(request.url).origin,
      requestSource: deviceRequestSource(request),
    });
    return Response.json(
      { ok: true, ...result },
      {
        status: 201,
        headers: { ...DEVICE_RESPONSE_HEADERS, "X-Request-ID": requestId },
      },
    );
  } catch (error) {
    return deviceErrorResponse(error, requestId);
  }
}
