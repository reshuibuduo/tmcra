import { env } from "cloudflare:workers";

import {
  PERSONAL_NO_STORE_HEADERS,
  personalErrorResponse,
  personalMemoryBinding,
  personalMemoryFetch,
  requirePersonalAccess,
} from "../../export/server";
import { ConsoleError } from "@/db/console";
import {
  VOICE_MAX_AUDIO_BYTES,
  sha256Hex,
  transcriptionRequest,
} from "../../chat/voice-contract.mjs";
import {
  mapVoiceError,
  readVoiceProviderJson,
  recordVoiceProviderReceipt,
  requireSameOrigin,
  safeProviderErrorCode,
  safeVoiceLog,
} from "../../chat/voice/server";
import {
  AudioMemoryContractError,
  audioTranscriptionReviewRequest,
} from "../contract.mjs";
import { resolveTranscriptCandidates } from "../transcript-resolution.mjs";

export const dynamic = "force-dynamic";

/**
 * Paid/fallback transcription for the mobile audio-memory client.
 *
 * Normal personal accounts may use this endpoint. The general chat and
 * realtime voice surfaces keep their separate internal-preview boundary.
 */
export async function POST(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    requireSameOrigin(request);
    const contentType = request.headers.get("content-type")?.toLowerCase() ?? "";
    if (!contentType.startsWith("multipart/form-data")) {
      throw new ConsoleError(415, "unsupported_media_type", "Content-Type must be multipart/form-data.");
    }
    const announced = Number(request.headers.get("content-length") ?? "0");
    if (Number.isFinite(announced) && announced > VOICE_MAX_AUDIO_BYTES + 512_000) {
      return Response.json(
        { ok: false, error: { code: "payload_too_large", message: "Audio-memory request is too large.", requestId } },
        { status: 413, headers: PERSONAL_NO_STORE_HEADERS },
      );
    }
    const access = await requirePersonalAccess();
    let formData: FormData;
    try {
      formData = await request.formData();
    } catch {
      return Response.json(
        { ok: false, error: { code: "invalid_multipart", message: "Audio upload is invalid.", requestId } },
        { status: 400, headers: PERSONAL_NO_STORE_HEADERS },
      );
    }
    const input = transcriptionRequest(formData, access.space.scopeName, [
      "eventId",
      "localTranscript",
      "localConfidence",
      "localModel",
      "protectedTerms",
    ]);
    const review = audioTranscriptionReviewRequest(formData);
    const config = audioAsrConfig();
    const audioBytes = await input.audio.arrayBuffer();
    const audioHash = await sha256Hex(audioBytes);
    const requestHash = await sha256Hex([
      "tmcra-mobile-audio-transcription-v1",
      audioHash,
      config.model,
      input.language ?? "",
      input.prompt ?? "",
      review.eventId ?? "",
      review.localTranscript ?? "",
      review.localModel ?? "",
    ].join("\u0000"));
    const callId = `mobile-stt-${crypto.randomUUID()}`;
    const startedAt = Date.now() / 1000;

    try {
      const providerForm = new FormData();
      providerForm.set(
        "file",
        new File([audioBytes], input.filename, { type: input.mediaType }),
      );
      providerForm.set("model", config.model);
      providerForm.set("response_format", "json");
      if (input.language) providerForm.set("language", input.language);
      if (input.prompt) providerForm.set("prompt", input.prompt);
      const providerResponse = await personalMemoryFetch(
        personalMemoryBinding(),
        "/v1/audio/transcriptions",
        requestId,
        {
          method: "POST",
          body: providerForm,
          headers: {
            "X-TMCRA-On-Behalf-Of-Subject": access.space.id,
            "X-TMCRA-Client-Platform": "tmcra_mobile_audio",
            "X-TMCRA-Agent-ID": "tmcra-mobile-audio-sensor",
          },
        },
        90_000,
      );
      const providerBody = await readVoiceProviderJson(providerResponse);
      const remoteTranscript = typeof providerBody.text === "string" ? providerBody.text.trim() : "";
      if (!remoteTranscript || remoteTranscript.length > 200_000) {
        throw new Error("voice_provider_invalid_transcript");
      }
      const resolution = await resolveTranscriptCandidates({
        local: review.localTranscript
          ? {
            text: review.localTranscript,
            model: review.localModel,
            confidence: review.localConfidence,
          }
          : null,
        remote: {
          text: remoteTranscript,
          model: config.model,
          provider: config.provider,
          confidence: null,
        },
        protectedTerms: review.protectedTerms,
      });
      const usage = transcriptionUsage(providerBody.usage);
      const accounting = await recordVoiceProviderReceipt(
        access,
        input.scopeName,
        {
          call_id: callId,
          provider: config.provider,
          model: config.model,
          operation: "voice_transcription",
          status: "completed",
          ...(usage.inputTokens !== undefined ? { input_tokens: usage.inputTokens } : {}),
          ...(usage.outputTokens !== undefined ? { output_tokens: usage.outputTokens } : {}),
          ...(usage.totalTokens !== undefined ? { total_tokens: usage.totalTokens } : {}),
          request_sha256: requestHash,
          response_sha256: await sha256Hex(remoteTranscript),
          started_at: startedAt,
          finished_at: Date.now() / 1000,
        },
        requestId,
      );
      return Response.json(
        {
          ok: true,
          transcript: resolution.finalTranscript ?? remoteTranscript,
          provider: config.provider,
          model: config.model,
          ...(typeof providerBody.language === "string" ? { language: providerBody.language } : {}),
          localCandidate: resolution.localCandidate,
          remoteCandidate: resolution.remoteCandidate,
          resolution: {
            status: resolution.status,
            selectedSource: resolution.selectedSource,
            confidenceBand: resolution.confidenceBand,
            similarity: resolution.similarity,
            reasons: resolution.reasons,
            criticalConflicts: resolution.criticalConflicts,
            finalTranscript: resolution.finalTranscript,
          },
          usage,
          accounting: accounting.recorded ? "recorded" : "pending",
          requestId,
        },
        { headers: PERSONAL_NO_STORE_HEADERS },
      );
    } catch (error) {
      safeVoiceLog("TMCRA mobile audio transcription failed", requestId, error);
      void recordVoiceProviderReceipt(
        access,
        input.scopeName,
        {
          call_id: callId,
          provider: config.provider,
          model: config.model,
          operation: "voice_transcription",
          status: "failed",
          error_code: safeProviderErrorCode(error),
          request_sha256: requestHash,
          started_at: startedAt,
          finished_at: Date.now() / 1000,
        },
        requestId,
      );
      throw error;
    }
  } catch (error) {
    if (error instanceof AudioMemoryContractError) {
      return personalErrorResponse(
        new ConsoleError(error.status, error.code, error.message),
        requestId,
      );
    }
    return personalErrorResponse(mapVoiceError(error), requestId);
  }
}

function audioAsrConfig() {
  const runtime = env as unknown as Record<string, unknown>;
  const provider = String(runtime.TMCRA_AUDIO_ASR_PROVIDER ?? "tmcra-qwen3-asr").trim();
  const model = String(runtime.TMCRA_AUDIO_ASR_MODEL ?? "Qwen3-ASR-0.6B-bf16").trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/u.test(provider)) {
    throw new ConsoleError(503, "audio_asr_configuration_invalid", "Audio ASR provider is invalid.");
  }
  if (!model || model.length > 200 || /[\u0000-\u001f]/u.test(model)) {
    throw new ConsoleError(503, "audio_asr_configuration_invalid", "Audio ASR model is invalid.");
  }
  return { provider, model };
}

function transcriptionUsage(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const source = value as Record<string, unknown>;
  const inputTokens = safeUsageNumber(source.input_tokens);
  const outputTokens = safeUsageNumber(source.output_tokens);
  const totalTokens = safeUsageNumber(source.total_tokens);
  const seconds = typeof source.seconds === "number" && Number.isFinite(source.seconds) && source.seconds >= 0
    ? source.seconds
    : undefined;
  return {
    ...(inputTokens !== undefined ? { inputTokens } : {}),
    ...(outputTokens !== undefined ? { outputTokens } : {}),
    ...(totalTokens !== undefined ? { totalTokens } : {}),
    ...(seconds !== undefined ? { seconds } : {}),
  };
}

function safeUsageNumber(value: unknown) {
  return Number.isSafeInteger(value) && Number(value) >= 0 && Number(value) <= 100_000_000
    ? Number(value)
    : undefined;
}
