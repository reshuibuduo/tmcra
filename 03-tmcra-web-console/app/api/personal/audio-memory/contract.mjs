const EVENT_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$/u;
const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/u;
const SCOPE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/u;
const SPEAKER_ID_PATTERN = /^spk_[A-Za-z0-9_-]{8,96}$/u;
const LANGUAGE_PATTERN = /^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,3}$/u;
const CLIENT_VERSION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.+-]{0,39}$/u;
const SHA256_PATTERN = /^[0-9a-f]{64}$/u;
const REASON_PATTERN = /^[a-z][a-z0-9_]{1,79}$/u;
const MAX_TRANSCRIPT_CHARACTERS = 50_000;

export function audioTranscriptionReviewRequest(formData) {
  const eventId = optionalPatternText(
    formData.get("eventId"),
    EVENT_ID_PATTERN,
    "invalid_audio_event_id",
  );
  const localTranscript = optionalText(
    formData.get("localTranscript"),
    MAX_TRANSCRIPT_CHARACTERS,
    "invalid_audio_transcript",
  );
  const localModel = optionalText(formData.get("localModel"), 200, "invalid_audio_asr");
  const confidenceText = formData.get("localConfidence");
  const localConfidence = confidenceText === undefined || confidenceText === null || confidenceText === ""
    ? null
    : numberValue(Number(confidenceText), 0, 1, "invalid_audio_asr");
  if (localTranscript && !eventId) {
    throw contractError(422, "invalid_audio_event_id", "Event ID is required for remote transcript review.");
  }
  if (!localTranscript && (localModel || localConfidence !== null)) {
    throw contractError(422, "invalid_audio_asr", "Local ASR metadata requires a local transcript.");
  }
  let protectedTerms = [];
  const termsText = formData.get("protectedTerms");
  if (termsText !== undefined && termsText !== null && termsText !== "") {
    const raw = String(termsText);
    if (raw.length > 5_000) {
      throw contractError(422, "invalid_audio_protected_terms", "Protected terms are invalid.");
    }
    try {
      protectedTerms = JSON.parse(raw);
    } catch {
      throw contractError(422, "invalid_audio_protected_terms", "Protected terms are invalid.");
    }
    if (!Array.isArray(protectedTerms) || protectedTerms.length > 50) {
      throw contractError(422, "invalid_audio_protected_terms", "Protected terms are invalid.");
    }
    protectedTerms = protectedTerms.map((value) => boundedText(
      value,
      80,
      "invalid_audio_protected_terms",
      "Protected terms are invalid.",
    ));
  }
  return { eventId, localTranscript, localModel, localConfidence, protectedTerms };
}

export class AudioMemoryContractError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = "AudioMemoryContractError";
    this.status = status;
    this.code = code;
  }
}

export async function audioMemoryEventRequest(value, namespace) {
  const source = objectValue(value);
  rejectUnknown(source, [
    "eventId",
    "sessionId",
    "scopeName",
    "capturedAt",
    "transcript",
    "durationMs",
    "language",
    "speaker",
    "asr",
    "hints",
    "client",
  ]);
  const cleanNamespace = patternText(
    namespace,
    SCOPE_PATTERN,
    "invalid_audio_namespace",
    "Audio-memory account namespace is invalid.",
  );
  const eventId = patternText(
    source.eventId,
    EVENT_ID_PATTERN,
    "invalid_audio_event_id",
    "Audio-memory Event ID is invalid.",
  );
  const sessionId = patternText(
    source.sessionId,
    SESSION_ID_PATTERN,
    "invalid_audio_session_id",
    "Audio-memory Session ID is invalid.",
  );
  const defaultScope = `${cleanNamespace}-project-life-audio`;
  const scopeName = source.scopeName === undefined || source.scopeName === null || source.scopeName === ""
    ? defaultScope
    : patternText(
      source.scopeName,
      SCOPE_PATTERN,
      "invalid_audio_scope",
      "Audio-memory Project Scope is invalid.",
    );
  if (!scopeName.startsWith(`${cleanNamespace}-project-`)) {
    throw contractError(403, "audio_scope_forbidden", "Audio-memory Project Scope is outside this account.");
  }

  const capturedAt = isoTimestamp(source.capturedAt);
  const transcript = boundedText(
    source.transcript,
    MAX_TRANSCRIPT_CHARACTERS,
    "invalid_audio_transcript",
    "Audio-memory transcript is invalid.",
  );
  const durationMs = integerValue(source.durationMs, 100, 300_000, "invalid_audio_duration");
  const language = optionalPatternText(source.language, LANGUAGE_PATTERN, "invalid_audio_language");
  const speaker = speakerValue(source.speaker);
  const asr = asrValue(source.asr);
  const hints = hintValue(source.hints);
  const client = clientValue(source.client);
  const digest = await sha256Hex([
    "tmcra-audio-memory-event-v1",
    cleanNamespace,
    scopeName,
    sessionId,
    eventId,
    capturedAt,
    speaker.localId,
    transcript,
  ].join("\u0000"));
  const role = speaker.relation === "self" ? "user" : "tool";
  const memoryContent = role === "user"
    ? transcript
    : `[Observed speaker: local_id=${speaker.localId}; relation=${speaker.relation}${speaker.label ? `; label=${speaker.label}` : ""}] ${transcript}`;
  const metadata = {
    actor_role: role,
    observed_speaker_id: speaker.localId,
    observed_speaker_relation: speaker.relation,
    ...(speaker.label ? { observed_speaker_label: speaker.label } : {}),
    speaker_confidence: speaker.confidence,
    duration_ms: durationMs,
    asr_mode: asr.mode,
    asr_confidence: asr.confidence,
    ...(asr.model ? { asr_model: asr.model } : {}),
    ...(asr.local ? {
      local_asr_sha256: asr.local.sha256,
      ...(asr.local.model ? { local_asr_model: asr.local.model } : {}),
      ...(asr.local.confidence !== null ? { local_asr_confidence: asr.local.confidence } : {}),
    } : {}),
    ...(asr.remote ? {
      remote_asr_sha256: asr.remote.sha256,
      ...(asr.remote.model ? { remote_asr_model: asr.remote.model } : {}),
      ...(asr.remote.provider ? { remote_asr_provider: asr.remote.provider } : {}),
    } : {}),
    ...(asr.resolution ? {
      asr_resolution_source: asr.resolution.selectedSource,
      asr_resolution_confidence: asr.resolution.confidenceBand,
      ...(asr.resolution.similarity !== null ? { asr_resolution_similarity: asr.resolution.similarity } : {}),
      asr_resolution_reasons: asr.resolution.reasons,
    } : {}),
    ...(language ? { language } : {}),
    client_platform: client.platform,
    client_version: client.version,
    ...(role === "tool"
      ? {
        agent_id: "tmcra-mobile-audio-sensor",
        agent_name: "TMCRA Mobile Audio Sensor",
        agent_role: "ambient_audio_observer",
      }
      : {}),
  };
  return {
    eventId,
    sessionId,
    scopeName,
    globalScope: `${cleanNamespace}-global`,
    capturedAt,
    transcript,
    memoryContent,
    durationMs,
    language,
    speaker,
    asr,
    hints,
    client,
    role,
    messageId: `audio-${digest.slice(0, 48)}`,
    idempotencyKey: `tmcra-audio-${digest.slice(0, 48)}`,
    metadata,
  };
}

export async function speakerIdentityMappingRequest(value, namespace) {
  const source = objectValue(value);
  rejectUnknown(source, ["localId", "label", "relation", "revision", "client"]);
  const cleanNamespace = patternText(
    namespace,
    SCOPE_PATTERN,
    "invalid_audio_namespace",
    "Audio-memory account namespace is invalid.",
  );
  const localId = patternText(
    source.localId,
    SPEAKER_ID_PATTERN,
    "invalid_audio_speaker",
    "Audio-memory local Speaker ID is invalid.",
  );
  const relation = String(source.relation ?? "").trim();
  if (!new Set(["self", "known"]).has(relation)) {
    throw contractError(422, "invalid_audio_speaker", "Only confirmed speaker identities can be synchronized.");
  }
  const label = boundedText(
    source.label,
    80,
    "invalid_audio_speaker",
    "Audio-memory speaker label is invalid.",
  );
  const revision = integerValue(source.revision, 1, 1_000_000, "invalid_audio_speaker_revision");
  const client = clientValue(source.client);
  const digest = await sha256Hex([
    "tmcra-audio-speaker-mapping-v1",
    cleanNamespace,
    localId,
    relation,
    label,
    String(revision),
  ].join("\u0000"));
  const content = relation === "self"
    ? `The user confirmed that local speaker ID ${localId} is their own voice (label: ${label}).`
    : `The user labeled local speaker ID ${localId} as ${label}.`;
  return {
    localId,
    label,
    relation,
    revision,
    client,
    content,
    globalScope: `${cleanNamespace}-global`,
    messageId: `speaker-map-${digest.slice(0, 44)}`,
    idempotencyKey: `tmcra-speaker-map-${digest.slice(0, 44)}`,
    metadata: {
      actor_role: "user",
      memory_type: "speaker_identity_mapping",
      local_speaker_id: localId,
      speaker_relation: relation,
      speaker_label: label,
      mapping_revision: revision,
      client_platform: client.platform,
      client_version: client.version,
      privacy_boundary: "identity_label_only_no_audio_no_biometrics",
    },
  };
}

export function compactPromptEvidence(value, maximumCharacters = 12_000) {
  const prompt = value && typeof value === "object" && !Array.isArray(value)
    ? value.prompt_evidence
    : null;
  const content = typeof prompt === "string"
    ? prompt
    : prompt && typeof prompt === "object" && !Array.isArray(prompt) && typeof prompt.content === "string"
      ? prompt.content
      : "";
  return content.trim().slice(0, maximumCharacters);
}

export async function sha256Hex(value) {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function speakerValue(value) {
  const source = objectValue(value, "invalid_audio_speaker");
  rejectUnknown(source, ["localId", "label", "relation", "confidence"]);
  const relation = String(source.relation ?? "").trim();
  if (!new Set(["self", "known", "unknown"]).has(relation)) {
    throw contractError(422, "invalid_audio_speaker", "Audio-memory speaker relation is invalid.");
  }
  return {
    localId: patternText(
      source.localId,
      SPEAKER_ID_PATTERN,
      "invalid_audio_speaker",
      "Audio-memory local Speaker ID is invalid.",
    ),
    label: optionalText(source.label, 80, "invalid_audio_speaker"),
    relation,
    confidence: numberValue(source.confidence, 0, 1, "invalid_audio_speaker"),
  };
}

function asrValue(value) {
  const source = objectValue(value, "invalid_audio_asr");
  rejectUnknown(source, ["mode", "confidence", "model", "local", "remote", "resolution"]);
  const mode = String(source.mode ?? "").trim();
  if (!new Set(["on_device", "remote_fallback", "remote_review", "dual_review", "manual"]).has(mode)) {
    throw contractError(422, "invalid_audio_asr", "Audio-memory ASR mode is invalid.");
  }
  const local = asrCandidateValue(source.local, "local");
  const remote = asrCandidateValue(source.remote, "remote");
  const resolution = asrResolutionValue(source.resolution);
  if (mode === "dual_review" && (!local || !remote || !resolution)) {
    throw contractError(422, "invalid_audio_asr", "Dual ASR review requires both candidates and a resolution.");
  }
  if (resolution && resolution.status !== "resolved") {
    throw contractError(409, "audio_transcript_review_required", "Unresolved transcript conflicts cannot be written to memory.");
  }
  return {
    mode,
    model: optionalText(source.model, 160, "invalid_audio_asr"),
    confidence: source.confidence === null || source.confidence === undefined
      ? null
      : numberValue(source.confidence, 0, 1, "invalid_audio_asr"),
    local,
    remote,
    resolution,
  };
}

function asrCandidateValue(value, sourceName) {
  if (value === undefined || value === null) return null;
  const source = objectValue(value, "invalid_audio_asr");
  rejectUnknown(source, ["sha256", "model", "provider", "confidence"]);
  const provider = optionalText(source.provider, 80, "invalid_audio_asr");
  if (sourceName === "local" && provider) {
    throw contractError(422, "invalid_audio_asr", "Local ASR candidate cannot declare a remote provider.");
  }
  return {
    sha256: patternText(
      source.sha256,
      SHA256_PATTERN,
      "invalid_audio_asr",
      "Audio-memory ASR digest is invalid.",
    ),
    model: optionalText(source.model, 200, "invalid_audio_asr"),
    provider,
    confidence: source.confidence === null || source.confidence === undefined
      ? null
      : numberValue(source.confidence, 0, 1, "invalid_audio_asr"),
  };
}

function asrResolutionValue(value) {
  if (value === undefined || value === null) return null;
  const source = objectValue(value, "invalid_audio_asr");
  rejectUnknown(source, ["status", "selectedSource", "confidenceBand", "similarity", "reasons"]);
  const status = String(source.status ?? "").trim();
  if (!new Set(["resolved", "review_required"]).has(status)) {
    throw contractError(422, "invalid_audio_asr", "Audio-memory ASR resolution status is invalid.");
  }
  const selectedSource = String(source.selectedSource ?? "").trim();
  if (!new Set(["agreement", "local", "remote", "manual", "none"]).has(selectedSource)) {
    throw contractError(422, "invalid_audio_asr", "Audio-memory ASR selected source is invalid.");
  }
  if ((status === "resolved") === (selectedSource === "none")) {
    throw contractError(422, "invalid_audio_asr", "Audio-memory ASR resolution source is inconsistent.");
  }
  const confidenceBand = String(source.confidenceBand ?? "").trim();
  if (!new Set(["high", "medium", "low"]).has(confidenceBand)) {
    throw contractError(422, "invalid_audio_asr", "Audio-memory ASR confidence band is invalid.");
  }
  if (!Array.isArray(source.reasons) || source.reasons.length < 1 || source.reasons.length > 20) {
    throw contractError(422, "invalid_audio_asr", "Audio-memory ASR resolution reasons are invalid.");
  }
  const reasons = source.reasons.map((reason) => patternText(
    reason,
    REASON_PATTERN,
    "invalid_audio_asr",
    "Audio-memory ASR resolution reason is invalid.",
  ));
  return {
    status,
    selectedSource,
    confidenceBand,
    similarity: source.similarity === null || source.similarity === undefined
      ? null
      : numberValue(source.similarity, 0, 1, "invalid_audio_asr"),
    reasons,
  };
}

function hintValue(value) {
  const source = value === undefined || value === null ? {} : objectValue(value, "invalid_audio_hints");
  rejectUnknown(source, ["commitment", "temporal", "person"]);
  for (const key of Object.keys(source)) {
    if (typeof source[key] !== "boolean") {
      throw contractError(422, "invalid_audio_hints", "Audio-memory trigger hints must be booleans.");
    }
  }
  return {
    commitment: source.commitment === true,
    temporal: source.temporal === true,
    person: source.person === true,
  };
}

function clientValue(value) {
  const source = objectValue(value, "invalid_audio_client");
  rejectUnknown(source, ["platform", "version"]);
  if (source.platform !== "android") {
    throw contractError(422, "invalid_audio_client", "Audio-memory client platform is invalid.");
  }
  return {
    platform: "android",
    version: patternText(
      source.version,
      CLIENT_VERSION_PATTERN,
      "invalid_audio_client",
      "Audio-memory client version is invalid.",
    ),
  };
}

function isoTimestamp(value) {
  const clean = boundedText(value, 64, "invalid_audio_timestamp", "Audio-memory timestamp is invalid.");
  const timestamp = Date.parse(clean);
  if (!Number.isFinite(timestamp)) {
    throw contractError(422, "invalid_audio_timestamp", "Audio-memory timestamp is invalid.");
  }
  const now = Date.now();
  if (timestamp > now + 5 * 60_000 || timestamp < now - 366 * 24 * 60 * 60_000) {
    throw contractError(422, "invalid_audio_timestamp", "Audio-memory timestamp is outside the accepted range.");
  }
  return new Date(timestamp).toISOString();
}

function objectValue(value, code = "invalid_json") {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw contractError(400, code, "Audio-memory request must be a JSON object.");
  }
  return value;
}

function rejectUnknown(value, allowed) {
  const names = new Set(allowed);
  if (Object.keys(value).some((key) => !names.has(key))) {
    throw contractError(422, "unexpected_audio_field", "Audio-memory request contains an unsupported field.");
  }
}

function boundedText(value, maximum, code, message) {
  const clean = String(value ?? "").trim();
  if (!clean || clean.length > maximum || /\u0000/u.test(clean)) throw contractError(422, code, message);
  return clean;
}

function optionalText(value, maximum, code) {
  if (value === undefined || value === null || value === "") return null;
  return boundedText(value, maximum, code, "Audio-memory field is invalid.");
}

function patternText(value, pattern, code, message) {
  const clean = String(value ?? "").trim();
  if (!pattern.test(clean)) throw contractError(422, code, message);
  return clean;
}

function optionalPatternText(value, pattern, code) {
  if (value === undefined || value === null || value === "") return null;
  return patternText(value, pattern, code, "Audio-memory field is invalid.");
}

function integerValue(value, minimum, maximum, code) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw contractError(422, code, "Audio-memory integer field is invalid.");
  }
  return value;
}

function numberValue(value, minimum, maximum, code) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum) {
    throw contractError(422, code, "Audio-memory confidence is invalid.");
  }
  return value;
}

function contractError(status, code, message) {
  return new AudioMemoryContractError(status, code, message);
}
