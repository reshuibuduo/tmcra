import { ConsoleError } from "@/db/console";

import {
  PERSONAL_NO_STORE_HEADERS,
  personalErrorResponse,
  personalMemoryBinding,
  personalMemoryRequest,
  requirePersonalAccess,
} from "../../export/server";

export const dynamic = "force-dynamic";

const EVENT_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$/u;
const MESSAGE_ID = /^audio-[0-9a-f]{48}$/u;
const SCOPE_NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/u;

export async function POST(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    requireSameOrigin(request);
    if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) {
      throw new ConsoleError(415, "unsupported_media_type", "Content-Type must be application/json.");
    }
    const announced = Number(request.headers.get("content-length") ?? "0");
    if (Number.isFinite(announced) && announced > 16_384) {
      throw new ConsoleError(413, "payload_too_large", "Audio-memory deletion request is too large.");
    }
    const access = await requirePersonalAccess();
    const text = await request.text();
    if (new TextEncoder().encode(text).byteLength > 16_384) {
      throw new ConsoleError(413, "payload_too_large", "Audio-memory deletion request is too large.");
    }
    let raw: unknown;
    try {
      raw = JSON.parse(text);
    } catch {
      throw new ConsoleError(400, "invalid_json", "Request body must be valid JSON.");
    }
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      throw new ConsoleError(422, "invalid_audio_deletion", "Audio-memory deletion request is invalid.");
    }
    const source = raw as Record<string, unknown>;
    if (Object.keys(source).some((key) => !["eventId", "scopeName", "messageId"].includes(key))) {
      throw new ConsoleError(422, "invalid_audio_deletion", "Audio-memory deletion request is invalid.");
    }
    const eventId = String(source.eventId ?? "").trim();
    const scopeName = String(source.scopeName ?? "").trim();
    const messageId = String(source.messageId ?? "").trim();
    if (!EVENT_ID.test(eventId) || !MESSAGE_ID.test(messageId) || !SCOPE_NAME.test(scopeName)) {
      throw new ConsoleError(422, "invalid_audio_deletion", "Audio-memory deletion target is invalid.");
    }
    const expectedScope = `${access.space.scopeName}-project-life-audio`;
    if (scopeName !== expectedScope) {
      throw new ConsoleError(403, "audio_scope_forbidden", "Audio-memory deletion target is outside this account.");
    }
    const result = await personalMemoryRequest(
      personalMemoryBinding(),
      `/v1/scopes/${encodeURIComponent(scopeName)}/messages`,
      {
        method: "DELETE",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": `tmcra-audio-delete-${eventId}`,
          "X-TMCRA-Confirm-Message-Count": "1",
          "X-TMCRA-On-Behalf-Of-Subject": access.space.id,
          "X-TMCRA-Client-Platform": "tmcra_mobile_audio",
          "X-TMCRA-Agent-ID": "tmcra-mobile-audio-sensor",
        },
        body: JSON.stringify({ message_ids: [messageId] }),
      },
      requestId,
    );
    return Response.json(
      { ok: true, eventId, deletion: result, requestId },
      { status: 202, headers: PERSONAL_NO_STORE_HEADERS },
    );
  } catch (error) {
    return personalErrorResponse(error, requestId);
  }
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
