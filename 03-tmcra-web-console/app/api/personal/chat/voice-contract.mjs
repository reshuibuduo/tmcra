const SCOPE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/u;
const SESSION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{7,119}$/u;
const ITEM_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{2,199}$/u;
const CALL_PATTERN = /^rtc_[A-Za-z0-9_-]{4,180}$/u;
const PROVIDER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/u;
const MODEL_PATTERN = /^[^\u0000-\u001f]{1,200}$/u;
const VOICE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/u;
const LANGUAGE_PATTERN = /^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$/u;

export const VOICE_MAX_AUDIO_BYTES = 20_000_000;
export const VOICE_MAX_SDP_BYTES = 128_000;
export const VOICE_MAX_JSON_BYTES = 512_000;

export const VOICE_AUDIO_ACCEPT = [
  "audio/flac",
  "audio/m4a",
  "audio/mp4",
  "audio/mpeg",
  "audio/ogg",
  "audio/wav",
  "audio/webm",
].join(",");

const ALLOWED_AUDIO_TYPES = new Set(VOICE_AUDIO_ACCEPT.split(","));
const AUDIO_TYPE_ALIASES = new Map([
  ["audio/mp3", "audio/mpeg"],
  ["audio/x-m4a", "audio/m4a"],
  ["audio/x-wav", "audio/wav"],
  ["audio/wave", "audio/wav"],
  ["video/mp4", "audio/mp4"],
]);

export class PersonalVoiceContractError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = "PersonalVoiceContractError";
    this.status = status;
    this.code = code;
  }
}

export function resolveVoiceProviderConfig(runtimeEnv) {
  const baseUrlText = requiredEnv(
    runtimeEnv.TMCRA_VOICE_PROVIDER_BASE_URL ?? runtimeEnv.TMCRA_CHAT_PROVIDER_BASE_URL,
    "voice_provider_not_configured",
  );
  const apiKey = requiredEnv(
    runtimeEnv.TMCRA_VOICE_PROVIDER_API_KEY ?? runtimeEnv.TMCRA_CHAT_PROVIDER_API_KEY,
    "voice_provider_not_configured",
  );
  if (apiKey.length > 4096 || /[\u0000-\u001f]/u.test(apiKey)) {
    throw contractError(503, "voice_provider_configuration_invalid", "Voice provider credential is invalid.");
  }
  let baseUrl;
  try {
    baseUrl = new URL(baseUrlText);
  } catch {
    throw contractError(503, "voice_provider_configuration_invalid", "Voice provider endpoint is invalid.");
  }
  const allowLoopback = runtimeEnv.TMCRA_VOICE_PROVIDER_ALLOW_HTTP_LOOPBACK === "1";
  const loopback = ["127.0.0.1", "localhost", "::1"].includes(baseUrl.hostname);
  if (
    baseUrl.username ||
    baseUrl.password ||
    (baseUrl.protocol !== "https:" && !(allowLoopback && loopback && baseUrl.protocol === "http:"))
  ) {
    throw contractError(503, "voice_provider_configuration_invalid", "Voice provider endpoint is invalid.");
  }
  const provider = String(runtimeEnv.TMCRA_VOICE_PROVIDER_NAME ?? "openai").trim();
  if (!PROVIDER_PATTERN.test(provider)) {
    throw contractError(503, "voice_provider_configuration_invalid", "Voice provider name is invalid.");
  }
  const transcriptionModel = modelValue(
    runtimeEnv.TMCRA_VOICE_TRANSCRIPTION_MODEL ?? "gpt-4o-mini-transcribe",
    "Voice transcription model is invalid.",
  );
  const realtimeModel = optionalModel(runtimeEnv.TMCRA_VOICE_REALTIME_MODEL);
  const realtimeTranscriptionModel = optionalModel(
    runtimeEnv.TMCRA_VOICE_REALTIME_TRANSCRIPTION_MODEL ?? transcriptionModel,
  );
  const realtimeVoice = String(runtimeEnv.TMCRA_VOICE_REALTIME_VOICE ?? "marin").trim();
  if (!VOICE_PATTERN.test(realtimeVoice)) {
    throw contractError(503, "voice_provider_configuration_invalid", "Realtime voice is invalid.");
  }
  return {
    provider,
    baseUrl: baseUrl.toString().replace(/\/$/u, ""),
    apiKey,
    transcriptionModel,
    realtimeModel,
    realtimeTranscriptionModel,
    realtimeVoice,
  };
}

export function transcriptionRequest(formData, namespace, extraAllowedFields = []) {
  const allowedFields = new Set([
    "audio",
    "scopeName",
    "sessionId",
    "language",
    "prompt",
    ...extraAllowedFields,
  ]);
  for (const key of formData.keys()) {
    if (!allowedFields.has(key)) {
      throw contractError(422, "unexpected_voice_field", "Voice request contains an unsupported field.");
    }
  }
  const audio = formData.get("audio");
  if (!(audio instanceof File)) {
    throw contractError(422, "voice_audio_required", "An audio file is required.");
  }
  const mediaType = normalizeAudioType(audio.type);
  if (!ALLOWED_AUDIO_TYPES.has(mediaType)) {
    throw contractError(415, "unsupported_voice_audio", "Audio type is not supported.");
  }
  if (audio.size < 1 || audio.size > VOICE_MAX_AUDIO_BYTES) {
    throw contractError(413, "voice_audio_too_large", "Audio must be between 1 byte and 20 MB.");
  }
  const filename = safeFilename(audio.name || `recording.${audioExtension(mediaType)}`);
  const scopeName = ownedProjectScope(formData.get("scopeName"), namespace);
  const sessionId = patternText(
    formData.get("sessionId"),
    SESSION_PATTERN,
    "invalid_voice_session",
    "A stable voice Session ID is required.",
  );
  const language = optionalPatternText(formData.get("language"), LANGUAGE_PATTERN, "invalid_voice_language");
  const prompt = optionalText(formData.get("prompt"), 1_000, "invalid_voice_prompt");
  return {
    audio,
    filename,
    mediaType,
    scopeName,
    sessionId,
    language,
    prompt,
  };
}

export function realtimeSessionRequest(requestUrl, sdp, namespace) {
  const url = new URL(requestUrl);
  for (const key of url.searchParams.keys()) {
    if (!new Set(["scopeName", "sessionId"]).has(key)) {
      throw contractError(422, "unexpected_voice_field", "Realtime request contains an unsupported field.");
    }
  }
  const body = String(sdp ?? "");
  const bytes = new TextEncoder().encode(body).byteLength;
  if (bytes < 16 || bytes > VOICE_MAX_SDP_BYTES || !/^v=0\r?\n/u.test(body)) {
    throw contractError(422, "invalid_realtime_sdp", "Realtime SDP offer is invalid.");
  }
  return {
    sdp: body,
    scopeName: ownedProjectScope(url.searchParams.get("scopeName"), namespace),
    sessionId: patternText(
      url.searchParams.get("sessionId"),
      SESSION_PATTERN,
      "invalid_voice_session",
      "A stable voice Session ID is required.",
    ),
  };
}

export async function realtimeTurnRequest(value, namespace) {
  const source = objectValue(value);
  rejectUnknown(source, ["callId", "scopeName", "sessionId", "itemId", "transcript"]);
  const callId = patternText(source.callId, CALL_PATTERN, "invalid_realtime_call", "Realtime Call ID is invalid.");
  const scopeName = ownedProjectScope(source.scopeName, namespace);
  const sessionId = patternText(source.sessionId, SESSION_PATTERN, "invalid_voice_session", "Voice Session ID is invalid.");
  const itemId = patternText(source.itemId, ITEM_PATTERN, "invalid_realtime_item", "Realtime Item ID is invalid.");
  const transcript = boundedText(source.transcript, 50_000, "invalid_realtime_transcript", "Realtime transcript is invalid.");
  const turnId = `voice-${(await sha256Hex([callId, itemId, transcript].join("\u0000"))).slice(0, 48)}`;
  return { callId, scopeName, sessionId, itemId, transcript, turnId };
}

export async function realtimeCommitRequest(value, namespace) {
  const source = objectValue(value);
  rejectUnknown(source, [
    "callId",
    "scopeName",
    "sessionId",
    "itemId",
    "transcript",
    "assistantText",
    "usage",
    "responseId",
  ]);
  const turn = await realtimeTurnRequest({
    callId: source.callId,
    scopeName: source.scopeName,
    sessionId: source.sessionId,
    itemId: source.itemId,
    transcript: source.transcript,
  }, namespace);
  const assistantText = boundedText(
    source.assistantText,
    100_000,
    "invalid_realtime_answer",
    "Realtime assistant transcript is invalid.",
  );
  const responseId = patternText(
    source.responseId,
    ITEM_PATTERN,
    "invalid_realtime_response",
    "Realtime Response ID is invalid.",
  );
  const usage = realtimeUsage(source.usage);
  return { ...turn, assistantText, usage, responseId };
}

export function realtimeSessionConfig(config, instructions) {
  if (!config.realtimeModel || !config.realtimeTranscriptionModel) {
    throw contractError(
      503,
      "realtime_voice_not_configured",
      "Realtime voice model and transcription model are not configured.",
    );
  }
  return {
    type: "realtime",
    model: config.realtimeModel,
    instructions: boundedText(
      instructions,
      12_000,
      "voice_provider_configuration_invalid",
      "Realtime instructions are invalid.",
    ),
    audio: {
      input: {
        transcription: { model: config.realtimeTranscriptionModel },
        turn_detection: {
          type: "server_vad",
          threshold: 0.5,
          prefix_padding_ms: 300,
          silence_duration_ms: 500,
          create_response: false,
          interrupt_response: true,
        },
      },
      output: { voice: config.realtimeVoice },
    },
  };
}

export async function sha256Hex(value) {
  const input = typeof value === "string" ? new TextEncoder().encode(value) : value;
  const digest = await crypto.subtle.digest("SHA-256", input);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function realtimeUsage(value) {
  if (value === undefined || value === null) return null;
  const source = objectValue(value);
  rejectUnknown(source, [
    "inputTokens",
    "outputTokens",
    "totalTokens",
    "cachedInputTokens",
    "inputAudioTokens",
    "outputAudioTokens",
    "inputTextTokens",
    "outputTextTokens",
  ]);
  const result = {};
  for (const key of Object.keys(source)) {
    const number = source[key];
    if (!Number.isSafeInteger(number) || number < 0 || number > 100_000_000) {
      throw contractError(422, "invalid_realtime_usage", "Realtime usage is invalid.");
    }
    result[key] = number;
  }
  if (
    result.totalTokens !== undefined &&
    result.inputTokens !== undefined &&
    result.outputTokens !== undefined &&
    result.totalTokens < result.inputTokens + result.outputTokens
  ) {
    throw contractError(422, "invalid_realtime_usage", "Realtime total usage is invalid.");
  }
  return result;
}

function ownedProjectScope(value, namespace) {
  const cleanNamespace = patternText(
    namespace,
    SCOPE_PATTERN,
    "invalid_voice_namespace",
    "Voice account namespace is invalid.",
  );
  const defaultScope = `${cleanNamespace}-project-tmcra-chat`;
  const scope = value === null || value === undefined || value === "" ? defaultScope : String(value).trim();
  if (!SCOPE_PATTERN.test(scope) || !scope.startsWith(`${cleanNamespace}-project-`)) {
    throw contractError(403, "voice_scope_forbidden", "The requested Project Scope is not part of this account.");
  }
  return scope;
}

function normalizeAudioType(value) {
  const clean = String(value ?? "").split(";", 1)[0].trim().toLowerCase();
  return AUDIO_TYPE_ALIASES.get(clean) ?? clean;
}

function audioExtension(mediaType) {
  return {
    "audio/flac": "flac",
    "audio/m4a": "m4a",
    "audio/mp4": "mp4",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/webm": "webm",
  }[mediaType] ?? "audio";
}

function safeFilename(value) {
  const clean = String(value ?? "recording").trim();
  if (!clean || clean.length > 160 || /[\u0000-\u001f\u007f]/u.test(clean)) {
    throw contractError(422, "invalid_voice_filename", "Audio filename is invalid.");
  }
  return clean.replace(/[\\/]/gu, "_");
}

function modelValue(value, message) {
  const clean = String(value ?? "").trim();
  if (!MODEL_PATTERN.test(clean)) {
    throw contractError(503, "voice_provider_configuration_invalid", message);
  }
  return clean;
}

function optionalModel(value) {
  const clean = String(value ?? "").trim();
  if (!clean) return "";
  return modelValue(clean, "Realtime model is invalid.");
}

function requiredEnv(value, code) {
  const clean = String(value ?? "").trim();
  if (!clean) throw contractError(503, code, "Voice provider is not configured.");
  return clean;
}

function objectValue(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw contractError(400, "invalid_json", "Request body must be a JSON object.");
  }
  return value;
}

function rejectUnknown(value, allowed) {
  const set = new Set(allowed);
  if (Object.keys(value).some((key) => !set.has(key))) {
    throw contractError(422, "unexpected_voice_field", "Voice request contains an unsupported field.");
  }
}

function boundedText(value, maximum, code, message) {
  const clean = String(value ?? "").trim();
  if (!clean || clean.length > maximum || /\u0000/u.test(clean)) throw contractError(422, code, message);
  return clean;
}

function optionalText(value, maximum, code) {
  if (value === null || value === undefined || value === "") return null;
  return boundedText(value, maximum, code, "Voice field is invalid.");
}

function patternText(value, pattern, code, message) {
  const clean = String(value ?? "").trim();
  if (!pattern.test(clean)) throw contractError(422, code, message);
  return clean;
}

function optionalPatternText(value, pattern, code) {
  if (value === null || value === undefined || value === "") return null;
  return patternText(value, pattern, code, "Voice language is invalid.");
}

function contractError(status, code, message) {
  return new PersonalVoiceContractError(status, code, message);
}
