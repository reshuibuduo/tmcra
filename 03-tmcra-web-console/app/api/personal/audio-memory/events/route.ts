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
  audioMemoryEventRequest,
  compactPromptEvidence,
} from "../contract.mjs";

export const dynamic = "force-dynamic";

const MAX_REQUEST_BYTES = 128 * 1024;
const ON_BEHALF_SUBJECT_HEADER = "X-TMCRA-On-Behalf-Of-Subject";

export async function POST(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    requireSameOrigin(request);
    const access = await requirePersonalAccess();
    const input = await audioMemoryEventRequest(
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
      agentId: "tmcra-mobile-audio-sensor",
      defaultTimeoutMs: 30_000,
    });

    const recallTargets = [input.globalScope, input.scopeName];
    const recallOutcomes = await Promise.all(recallTargets.map(async (scopeName) => {
      try {
        const response = await client.recall(scopeName, {
          query: input.transcript,
          query_time: input.capturedAt,
          evidence_mode: "auto",
          recall_profile: "interactive",
          response_projection: "prompt_only",
          max_windows: 8,
        });
        return {
          scopeName,
          status: "completed" as const,
          queryId: response.query_id,
          evidence: compactPromptEvidence(response),
        };
      } catch (error) {
        console.warn("TMCRA mobile audio recall failed open", {
          requestId,
          scopeName,
          error: error instanceof Error ? error.name : "UnknownError",
        });
        return {
          scopeName,
          status: "failed" as const,
          queryId: null,
          evidence: "",
        };
      }
    }));

    const job = await client.ingest(input.scopeName, {
      session_id: input.sessionId,
      messages: [{
        message_id: input.messageId,
        role: input.role === "user" ? "user" : "tool",
        content: input.memoryContent,
        timestamp: input.capturedAt,
        metadata: input.metadata,
      }],
      consistency: "eventual",
      slow_policy: "auto",
      metadata: {
        integration: "tmcra-mobile-audio",
        source_kind: "ambient_audio_transcript",
        event_id: input.eventId,
        trigger_hints: input.hints,
        privacy_boundary: "transcript_only_no_audio_no_biometrics",
      },
    }, { idempotencyKey: input.idempotencyKey });

    return Response.json({
      ok: true,
      eventId: input.eventId,
      scopeName: input.scopeName,
      recalls: recallOutcomes.map(({ evidence: _evidence, ...receipt }) => ({
        ...receipt,
        evidenceAvailable: Boolean(_evidence),
      })),
      context: recallOutcomes
        .filter((outcome) => outcome.evidence)
        .map((outcome) => ({ scopeName: outcome.scopeName, content: outcome.evidence })),
      write: {
        status: job.status,
        jobId: job.job_id,
        messageId: input.messageId,
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
    throw new ConsoleError(413, "payload_too_large", "Audio-memory request is too large.");
  }
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > MAX_REQUEST_BYTES) {
    throw new ConsoleError(413, "payload_too_large", "Audio-memory request is too large.");
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
