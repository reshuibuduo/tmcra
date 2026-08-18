import { pollDeviceAuthorization } from "../device-service";
import {
  DEVICE_RESPONSE_HEADERS,
  deviceErrorResponse,
  readDeviceJson,
} from "../http";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    const body = await readDeviceJson(request);
    const result = await pollDeviceAuthorization({
      deviceCode: body.deviceCode,
      codeVerifier: body.codeVerifier,
      deliveryReceipt: body.deliveryReceipt,
    });
    return Response.json(
      { ok: true, ...result },
      {
        status: 200,
        headers: { ...DEVICE_RESPONSE_HEADERS, "X-Request-ID": requestId },
      },
    );
  } catch (error) {
    return deviceErrorResponse(error, requestId);
  }
}
