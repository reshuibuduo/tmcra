import { env } from "cloudflare:workers";
import { TMCRAClient } from "@tmcra/typescript";

import { fetchMemoryApi } from "@/app/lib/memory-api-fetch";
import { ConsoleError } from "@/db/console";

import {
  PERSONAL_NO_STORE_HEADERS,
  personalErrorResponse,
  personalMemoryBinding,
  requirePersonalAccess,
} from "../../export/server";
import {
  AudioMemoryContractError,
  speakerIdentityMappingRequest,
} from "../contract.mjs";

export const dynamic = "force-dynamic";

const MAX_REQUEST_BYTES = 16 * 1024;
const ON_BEHALF_SUBJECT_HEADER = "X-TMCRA-On-Behalf-Of-Subject";

export async function POST(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    requireSameOrigin(request);
    const access = await requirePersonalAccess();
    const input = await speakerIdentityMappingRequest(
      await readJsonObject(request),
      access.space.scopeName,
    );
    const binding = personalMemoryBinding();
    const client = new TMCRAClient({
      baseUrl: binding.baseUrl,
      apiKey: binding.apiKey,
      fetch: (inputValue, init) => fetchMemoryApi(env, inputValue, init),
      headers: { [ON_BEHALF_SUBJECT_HEADER]: access.space.id },
      clientPlatform: "tmcra_mobile_audio",
      agentId: "tmcra-mobile-speaker-registry",
      defaultTimeoutMs: 30_000,
    });

    const job = await client.ingest(input.globalScope, {
      session_id: "mobile-speaker-registry",
      messages: [{
        message_id: input.messageId,
        role: "user",
        content: input.content,
        timestamp: new Date().toISOString(),
        metadata: input.metadata,
      }],
      consistency: "eventual",
      slow_policy: "auto",
      metadata: {
        integration: "tmcra-mobile-audio",
        source_kind: "speaker_identity_mapping",
        privacy_boundary: "identity_label_only_no_audio_no_biometrics",
      },
    }, { idempotencyKey: input.idempotencyKey });

    return Response.json({
      ok: true,
      localId: input.localId,
      revision: input.revision,
      write: {
        status: job.status,
        jobId: job.job_id,
        statusUrl: job.status_url,
      },
      requestId,
    }, { status: 202, headers: PERSONAL_NO_STORE_HEADERS });
  } catch (error) {
    return personalErrorResponse(mapAudioMemoryError(error), requestId);
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

async function readJsonObject(request: Request) {
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) {
    throw new ConsoleError(415, "unsupported_media_type", "Content-Type must be application/json.");
  }
  const announced = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(announced) && announced > MAX_REQUEST_BYTES) {
    throw new ConsoleError(413, "payload_too_large", "Speaker mapping request is too large.");
  }
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > MAX_REQUEST_BYTES) {
    throw new ConsoleError(413, "payload_too_large", "Speaker mapping request is too large.");
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ConsoleError(400, "invalid_json", "Request body must be valid JSON.");
  }
}

function mapAudioMemoryError(error: unknown) {
  if (error instanceof AudioMemoryContractError) {
    return new ConsoleError(error.status, error.code, error.message);
  }
  return error;
}
