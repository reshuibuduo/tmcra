const PROBE_TIMEOUT_MS = 3_000;
const MAXIMUM_PROBE_BYTES = 65_536;

type FetchImplementation = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

type Availability = "available" | "partial" | "unavailable";

type JsonProbe = {
  available: boolean;
  reason: string | null;
  statusCode: number | null;
  latencyMs: number | null;
  serviceLatencyMs: number | null;
  payload: Record<string, unknown> | null;
};

type ProbeSource = {
  availability: Availability;
  reason: string | null;
  source: string;
  checkedAt: string;
  httpStatus: number | null;
  probeLatencyMs: number | null;
  serviceLatencyMs: number | null;
};

export type InternalOperationsSnapshot = {
  collectedAt: string;
  health: ProbeSource & {
    status: "healthy" | "unhealthy" | "unavailable";
    service: string | null;
    version: string | null;
  };
  readiness: ProbeSource & {
    status: "ready" | "not_ready" | "unavailable";
    service: string | null;
    version: string | null;
    checks: Record<string, boolean> | null;
    snapshotStale: boolean | null;
    snapshotAgeSeconds: number | null;
    monitorGeneration: number | null;
  };
  deployment: ProbeSource & {
    status: "healthy" | "degraded" | "unavailable";
    service: string | null;
    release: string | null;
    upstreamStatus: number | null;
  };
  startupPreflight: {
    availability: Availability;
    reason: string;
    source: string;
    status: string | null;
    mode: string | null;
    releaseId: string | null;
    completedAt: string | null;
    failedChecks: string[];
  };
  queue: {
    availability: Availability;
    reason: string;
    source: string;
    pending: number | null;
    running: number | null;
    failed: number | null;
    active: number | null;
    activeLimit: number | null;
    recentErrorTotal: number | null;
    recentErrors: string[];
  };
  latency: {
    availability: Availability;
    reason: string;
    healthProbeMs: number | null;
    healthServiceMs: number | null;
    readinessProbeMs: number | null;
    readinessServiceMs: number | null;
    deploymentProbeMs: number | null;
    sampleWindowSeconds: number | null;
    sampleCount: number | null;
    p50Ms: number | null;
    p95Ms: number | null;
    p99Ms: number | null;
    recallP50Ms: number | null;
    recallP95Ms: number | null;
    recallP99Ms: number | null;
    writeP50Ms: number | null;
    writeP95Ms: number | null;
    writeP99Ms: number | null;
  };
  costs: {
    availability: Availability;
    reason: string;
    source: string;
    currency: string | null;
    periodStart: string | null;
    periodEnd: string | null;
    knownCostMicroCny: number | null;
    unknownCallCount: number | null;
    registeredCallCount: number | null;
    completedCallCount: number | null;
    failedCallCount: number | null;
    inputTokens: number | null;
    outputTokens: number | null;
  };
  release: {
    availability: Availability;
    reason: string;
    websiteRelease: string | null;
    apiVersion: string | null;
    apiReleaseId: string | null;
    releaseSha256: string | null;
    channel: string | null;
    canaryPercent: number | null;
    previousRelease: string | null;
  };
};

export async function collectInternalOperations({
  memoryApiBaseUrl,
  memoryApiControlBaseUrl,
  allowHttpLoopback = false,
  staffMonitoringKey,
  requestUrl,
  fetchImpl = fetch,
  now = () => new Date(),
  monotonicNow = () => performance.now(),
}: {
  memoryApiBaseUrl: unknown;
  memoryApiControlBaseUrl?: unknown;
  allowHttpLoopback?: boolean;
  staffMonitoringKey?: unknown;
  requestUrl: string;
  fetchImpl?: FetchImplementation;
  now?: () => Date;
  monotonicNow?: () => number;
}): Promise<InternalOperationsSnapshot> {
  const collectedAt = now().toISOString();
  const apiBase = normalizeApiBase(memoryApiBaseUrl);
  const staffBase = normalizeStaffApiBase({
    publicBase: memoryApiBaseUrl,
    controlBase: memoryApiControlBaseUrl,
    allowHttpLoopback,
  });
  const staffKey = normalizeStaffKey(staffMonitoringKey);
  const siteOrigin = normalizeSiteOrigin(requestUrl);

  const [healthProbe, readinessProbe, deploymentProbe, staffProbe] = await Promise.all([
    apiBase.url
      ? fetchJsonProbe(new URL("healthz", apiBase.url), fetchImpl, monotonicNow)
      : unavailableProbe(apiBase.reason),
    apiBase.url
      ? fetchJsonProbe(new URL("readyz", apiBase.url), fetchImpl, monotonicNow)
      : unavailableProbe(apiBase.reason),
    siteOrigin.url
      ? fetchJsonProbe(
          new URL("/__deployment/health", siteOrigin.url),
          fetchImpl,
          monotonicNow,
        )
      : unavailableProbe(siteOrigin.reason),
    staffBase.url && staffKey.value
      ? fetchJsonProbe(
          new URL("v1/internal/runtime", staffBase.url),
          fetchImpl,
          monotonicNow,
          {
            "X-TMCRA-Staff-Key": staffKey.value,
            "Cache-Control": "no-store",
          },
        )
      : unavailableProbe(staffBase.url ? staffKey.reason : staffBase.reason),
  ]);

  const health = normalizeHealth(healthProbe, collectedAt);
  const readiness = normalizeReadiness(readinessProbe, collectedAt);
  const deployment = normalizeDeployment(deploymentProbe, collectedAt);
  const measuredProbeCount = [healthProbe, readinessProbe, deploymentProbe].filter(
    (probe) => probe.latencyMs !== null,
  ).length;
  const validProbeCount = [healthProbe, readinessProbe, deploymentProbe].filter(
    (probe) => probe.available,
  ).length;
  const hasReleaseIdentifier = Boolean(deployment.release || health.version);
  const runtime = normalizeStaffRuntime(staffProbe);

  return {
    collectedAt,
    health,
    readiness,
    deployment,
    startupPreflight: runtime.startupPreflight ?? {
      availability: "unavailable",
      reason: runtime.reason,
      status: null,
      mode: null,
      source: "GET /v1/internal/runtime",
      releaseId: null,
      completedAt: null,
      failedChecks: [],
    },
    queue: runtime.queue ?? {
      availability: "unavailable",
      reason: runtime.reason,
      pending: null,
      running: null,
      failed: null,
      active: null,
      activeLimit: null,
      recentErrorTotal: null,
      source: "GET /v1/internal/runtime",
      recentErrors: [],
    },
    latency: {
      availability: runtime.latency ? runtime.latency.availability : measuredProbeCount > 0 ? "partial" : "unavailable",
      reason:
        runtime.latency?.reason ?? (validProbeCount > 0
          ? "Only current server-to-server probe timings are available; production request percentiles and sample windows are not exposed."
          : measuredProbeCount > 0
            ? "Only failed or timed-out probe durations are available; no valid operational payload or production request percentiles were returned."
          : "No operational probe completed, and production request percentiles are not exposed."),
      healthProbeMs: healthProbe.latencyMs,
      healthServiceMs: healthProbe.serviceLatencyMs,
      readinessProbeMs: readinessProbe.latencyMs,
      readinessServiceMs: readinessProbe.serviceLatencyMs,
      deploymentProbeMs: deploymentProbe.latencyMs,
      sampleWindowSeconds: runtime.latency?.sampleWindowSeconds ?? null,
      sampleCount: runtime.latency?.sampleCount ?? null,
      p50Ms: runtime.latency?.p50Ms ?? null,
      p95Ms: runtime.latency?.p95Ms ?? null,
      p99Ms: runtime.latency?.p99Ms ?? null,
      recallP50Ms: runtime.latency?.recallP50Ms ?? null,
      recallP95Ms: runtime.latency?.recallP95Ms ?? null,
      recallP99Ms: runtime.latency?.recallP99Ms ?? null,
      writeP50Ms: runtime.latency?.writeP50Ms ?? null,
      writeP95Ms: runtime.latency?.writeP95Ms ?? null,
      writeP99Ms: runtime.latency?.writeP99Ms ?? null,
    },
    costs: runtime.costs ?? {
      availability: "unavailable",
      reason: runtime.reason,
      currency: null,
      periodStart: null,
      periodEnd: null,
      knownCostMicroCny: null,
      unknownCallCount: null,
      registeredCallCount: null,
      completedCallCount: null,
      failedCallCount: null,
      inputTokens: null,
      outputTokens: null,
      source: "GET /v1/internal/runtime",
    },
    release: runtime.release ? {
      ...runtime.release,
      websiteRelease: deployment.release,
      apiVersion: runtime.release.apiVersion ?? health.version,
    } : {
      availability: hasReleaseIdentifier ? "partial" : "unavailable",
      reason: hasReleaseIdentifier
        ? "Current website and API identifiers are available, but release channel, rollback target, and canary allocation are not exposed."
        : "No current release identifier or release-control telemetry is available.",
      websiteRelease: deployment.release,
      apiVersion: health.version,
      apiReleaseId: null,
      releaseSha256: null,
      channel: null,
      canaryPercent: null,
      previousRelease: null,
    },
  };
}

function normalizeStaffRuntime(probe: JsonProbe) {
  const unavailableReason = staffProbeReason(probe);
  const absent = {
    reason: unavailableReason,
    startupPreflight: null,
    queue: null,
    latency: null,
    costs: null,
    release: null,
  };
  if (probe.statusCode !== 200 || !probe.available || !probe.payload) return absent;
  if (probe.payload.schema_version !== "tmcra.service.staff-runtime.1") {
    return {
      ...absent,
      reason: "The staff runtime payload did not match the supported schema.",
    };
  }

  const startupRaw = objectOrNull(probe.payload.startup_preflight);
  const queueRaw = objectOrNull(probe.payload.queue);
  const latencyRaw = objectOrNull(probe.payload.latency);
  const costsRaw = objectOrNull(probe.payload.costs);
  const releaseRaw = objectOrNull(probe.payload.release);
  const releaseId = wrappedString(releaseRaw, "release_id");

  const startupAvailability = runtimeAvailability(startupRaw);
  const startupStatus = shortString(startupRaw?.status);
  const startupMode = shortString(startupRaw?.mode);
  const startupPreflight = startupRaw && startupAvailability !== "unavailable" && startupStatus && startupMode
    ? {
        availability: startupAvailability,
        reason: runtimeReason(startupRaw),
        source: shortString(startupRaw.source) ?? "GET /v1/internal/runtime",
        status: startupStatus,
        mode: startupMode,
        releaseId,
        completedAt: epochSecondsToIso(startupRaw.completed_at),
        failedChecks: boundedStringArray(startupRaw.failed_checks, 64),
      }
    : null;

  const jobs = objectOrNull(queueRaw?.jobs);
  const stages = objectOrNull(queueRaw?.operation_stages);
  const queueAvailability = runtimeAvailability(queueRaw);
  const queue = queueRaw && queueAvailability !== "unavailable" && jobs && stages
    ? {
        availability: queueAvailability,
        reason: runtimeReason(queueRaw),
        source: shortString(queueRaw.source) ?? "GET /v1/internal/runtime",
        pending: sumNumbers(jobs.pending, stages.ready),
        running: sumNumbers(jobs.running, stages.running),
        failed: sumNumbers(jobs.failed, stages.failed),
        active: finiteNumber(queueRaw.active_job_count),
        activeLimit: finiteNumber(queueRaw.global_active_job_limit),
        recentErrorTotal: finiteNumber(queueRaw.recent_error_total),
        recentErrors: normalizeRecentErrors(queueRaw.recent_errors),
      }
    : null;

  const latencyAvailability = runtimeAvailability(latencyRaw);
  const recallRaw = objectOrNull(latencyRaw?.recall);
  const writeRaw = objectOrNull(latencyRaw?.write);
  const latency = latencyRaw
    ? {
        availability: latencyAvailability,
        reason: latencyAvailability === "available"
          ? recallRaw || writeRaw
            ? "Production request percentiles are collected in a bounded in-memory window."
            : "Aggregate production request percentiles are available; recall/write-specific percentiles are not exposed by the API yet."
          : runtimeReason(latencyRaw),
        sampleWindowSeconds: finiteNumber(latencyRaw.configured_window_seconds),
        sampleCount: finiteNumber(latencyRaw.sample_count),
        p50Ms: wrappedNumber(latencyRaw, "p50_ms"),
        p95Ms: wrappedNumber(latencyRaw, "p95_ms"),
        p99Ms: wrappedNumber(latencyRaw, "p99_ms"),
        recallP50Ms: wrappedNumber(recallRaw, "p50_ms"),
        recallP95Ms: wrappedNumber(recallRaw, "p95_ms"),
        recallP99Ms: wrappedNumber(recallRaw, "p99_ms"),
        writeP50Ms: wrappedNumber(writeRaw, "p50_ms"),
        writeP95Ms: wrappedNumber(writeRaw, "p95_ms"),
        writeP99Ms: wrappedNumber(writeRaw, "p99_ms"),
      }
    : null;

  const costsAvailability = runtimeAvailability(costsRaw);
  const costs = costsRaw && costsAvailability !== "unavailable"
    ? {
        availability: costsAvailability,
        reason: runtimeReason(costsRaw),
        source: shortString(costsRaw.source) ?? "GET /v1/internal/runtime",
        currency: shortString(costsRaw.currency, 16),
        periodStart: epochSecondsToIso(wrappedValue(costsRaw, "period_start")),
        periodEnd: epochSecondsToIso(wrappedValue(costsRaw, "period_end")),
        knownCostMicroCny: finiteNumber(costsRaw.known_cost_micro_cny),
        unknownCallCount: finiteNumber(costsRaw.uncertain_cost_call_count),
        registeredCallCount: finiteNumber(costsRaw.registered_call_count),
        completedCallCount: finiteNumber(costsRaw.completed_call_count),
        failedCallCount: finiteNumber(costsRaw.failed_call_count),
        inputTokens: finiteNumber(costsRaw.input_tokens),
        outputTokens: finiteNumber(costsRaw.output_tokens),
      }
    : null;

  const releaseAvailability = runtimeAvailability(releaseRaw);
  const release = releaseRaw
    ? {
        availability: releaseAvailability,
        reason: releaseAvailability === "available"
          ? "Active API release metadata is reported by the running service."
          : "Only the release fields configured on the running API are reported; missing fields remain unavailable.",
        websiteRelease: null,
        apiVersion: wrappedString(releaseRaw, "service_version"),
        apiReleaseId: releaseId,
        releaseSha256: wrappedString(releaseRaw, "release_sha256", 64),
        channel: wrappedString(releaseRaw, "channel", 64),
        canaryPercent: wrappedNumber(releaseRaw, "canary_percent"),
        previousRelease: wrappedString(releaseRaw, "rollback_release_id"),
      }
    : null;

  return {
    reason: unavailableReason,
    startupPreflight,
    queue,
    latency,
    costs,
    release,
  };
}

function staffProbeReason(probe: JsonProbe): string {
  if (probe.reason) return probe.reason;
  if (probe.statusCode === 401 || probe.statusCode === 403) {
    return "The Memory API rejected the dedicated staff telemetry credential.";
  }
  if (probe.statusCode === 404) {
    return "The Memory API staff telemetry endpoint is disabled or unavailable.";
  }
  if (probe.statusCode !== 200) {
    return probe.statusCode === null
      ? "The Memory API staff telemetry endpoint was not probed."
      : `The Memory API staff telemetry endpoint returned HTTP ${probe.statusCode}.`;
  }
  return "The Memory API staff telemetry payload was unavailable.";
}

function runtimeAvailability(value: Record<string, unknown> | null): Availability {
  const availability = shortString(value?.availability);
  return availability === "available" || availability === "partial"
    ? availability
    : "unavailable";
}

function runtimeReason(value: Record<string, unknown> | null): string {
  return shortString(value?.reason, 240) ?? "The runtime source did not return verifiable data.";
}

function wrappedValue(source: Record<string, unknown> | null, key: string): unknown {
  const wrapper = objectOrNull(source?.[key]);
  return wrapper?.availability === "available" ? wrapper.value : null;
}

function wrappedString(
  source: Record<string, unknown> | null,
  key: string,
  maximumLength = 128,
): string | null {
  return shortString(wrappedValue(source, key), maximumLength);
}

function wrappedNumber(source: Record<string, unknown> | null, key: string): number | null {
  return finiteNumber(wrappedValue(source, key));
}

function objectOrNull(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function boundedStringArray(value: unknown, maximum: number): string[] {
  return Array.isArray(value)
    ? value.slice(0, maximum).map((entry) => shortString(entry, 128)).filter((entry): entry is string => Boolean(entry))
    : [];
}

function sumNumbers(...values: unknown[]): number | null {
  const normalized = values.map(finiteNumber);
  return normalized.some((value) => value === null)
    ? null
    : normalized.reduce<number>((sum, value) => sum + (value ?? 0), 0);
}

function normalizeRecentErrors(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 20).flatMap((entry) => {
    const record = objectOrNull(entry);
    if (!record) return [];
    const source = shortString(record.source, 48);
    const category = shortString(record.category, 64);
    const occurredAt = epochSecondsToIso(record.occurred_at);
    if (!source || !category) return [];
    return [`${source} · ${category}${occurredAt ? ` · ${occurredAt}` : ""}`];
  });
}

function epochSecondsToIso(value: unknown): string | null {
  const seconds = finiteNumber(value);
  if (seconds === null || seconds > 253_402_300_799) return null;
  try {
    return new Date(seconds * 1000).toISOString();
  } catch {
    return null;
  }
}

function normalizeHealth(probe: JsonProbe, checkedAt: string) {
  const source = sourceFields(probe, "GET /healthz", checkedAt);
  if (!probe.available || !probe.payload) {
    return {
      ...source,
      status: "unavailable" as const,
      service: null,
      version: null,
    };
  }
  const status = shortString(probe.payload.status);
  const service = shortString(probe.payload.service);
  const version = shortString(probe.payload.version);
  if (!status || !service || !version) {
    return {
      ...source,
      availability: "unavailable" as const,
      reason: "The liveness probe payload did not match the expected contract.",
      status: "unavailable" as const,
      service: null,
      version: null,
    };
  }
  const healthy = probe.statusCode === 200 && status === "ok";
  return {
    ...source,
    status: healthy ? ("healthy" as const) : ("unhealthy" as const),
    service,
    version,
    reason: healthy ? null : `The liveness probe reported ${status}.`,
  };
}

function normalizeReadiness(probe: JsonProbe, checkedAt: string) {
  const source = sourceFields(probe, "GET /readyz", checkedAt);
  if (!probe.available || !probe.payload) {
    return {
      ...source,
      status: "unavailable" as const,
      service: null,
      version: null,
      checks: null,
      snapshotStale: null,
      snapshotAgeSeconds: null,
      monitorGeneration: null,
    };
  }
  const status = shortString(probe.payload.status);
  const checks = booleanRecord(probe.payload.checks);
  if ((status !== "ready" && status !== "not_ready") || checks === null) {
    return {
      ...source,
      availability: "unavailable" as const,
      reason: "The readiness probe payload did not match the expected contract.",
      status: "unavailable" as const,
      service: null,
      version: null,
      checks: null,
      snapshotStale: null,
      snapshotAgeSeconds: null,
      monitorGeneration: null,
    };
  }
  const readinessStatus = status === "ready" ? ("ready" as const) : ("not_ready" as const);
  return {
    ...source,
    status: readinessStatus,
    service: shortString(probe.payload.service),
    version: shortString(probe.payload.version),
    checks,
    snapshotStale: booleanOrNull(probe.payload.snapshot_stale),
    snapshotAgeSeconds: finiteNumber(probe.payload.snapshot_age_seconds),
    monitorGeneration: finiteNumber(probe.payload.monitor_generation),
    reason: readinessStatus === "ready" ? null : "One or more continuous readiness checks failed.",
  };
}

function normalizeDeployment(probe: JsonProbe, checkedAt: string) {
  const source = sourceFields(probe, "GET /__deployment/health", checkedAt);
  if (!probe.available || !probe.payload) {
    return {
      ...source,
      status: "unavailable" as const,
      service: null,
      release: null,
      upstreamStatus: null,
    };
  }
  const ok = booleanOrNull(probe.payload.ok);
  const release = shortString(probe.payload.release, 160);
  if (ok === null || !release) {
    return {
      ...source,
      availability: "unavailable" as const,
      reason: "The deployment probe payload did not match the expected contract.",
      status: "unavailable" as const,
      service: null,
      release: null,
      upstreamStatus: null,
    };
  }
  const upstreamStatus = finiteNumber(probe.payload.upstreamStatus);
  const healthy = probe.statusCode === 200 && ok && upstreamStatus === 200;
  return {
    ...source,
    status: healthy ? ("healthy" as const) : ("degraded" as const),
    service: shortString(probe.payload.service),
    release,
    upstreamStatus,
    reason: healthy ? null : "The website deployment probe reported a degraded dependency.",
  };
}

function sourceFields(
  probe: JsonProbe,
  source: string,
  checkedAt: string,
): ProbeSource {
  return {
    availability: probe.available ? "available" : "unavailable",
    reason: probe.reason,
    source,
    checkedAt,
    httpStatus: probe.statusCode,
    probeLatencyMs: probe.latencyMs,
    serviceLatencyMs: probe.serviceLatencyMs,
  };
}

async function fetchJsonProbe(
  url: URL,
  fetchImpl: FetchImplementation,
  monotonicNow: () => number,
  extraHeaders: Record<string, string> = {},
): Promise<JsonProbe> {
  const started = monotonicNow();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const response = await fetchImpl(url, {
      method: "GET",
      headers: { Accept: "application/json", ...extraHeaders },
      cache: "no-store",
      redirect: "manual",
      signal: controller.signal,
    });
    const latencyMs = roundedMilliseconds(monotonicNow() - started);
    const announcedLength = finiteNumber(response.headers.get("content-length"));
    if (announcedLength !== null && announcedLength > MAXIMUM_PROBE_BYTES) {
      return failedProbe(
        "The probe response exceeded the maximum accepted size.",
        response.status,
        latencyMs,
        response,
      );
    }
    const text = await response.text();
    if (new TextEncoder().encode(text).byteLength > MAXIMUM_PROBE_BYTES) {
      return failedProbe(
        "The probe response exceeded the maximum accepted size.",
        response.status,
        latencyMs,
        response,
      );
    }
    let payload: unknown;
    try {
      payload = JSON.parse(text);
    } catch {
      return failedProbe(
        `The probe returned HTTP ${response.status} without valid JSON.`,
        response.status,
        latencyMs,
        response,
      );
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return failedProbe(
        "The probe returned a non-object JSON payload.",
        response.status,
        latencyMs,
        response,
      );
    }
    return {
      available: true,
      reason: null,
      statusCode: response.status,
      latencyMs,
      serviceLatencyMs: responseLatency(response),
      payload: payload as Record<string, unknown>,
    };
  } catch (error) {
    const timedOut = controller.signal.aborted ||
      (error instanceof Error && error.name === "AbortError");
    return {
      available: false,
      reason: timedOut
        ? `The probe timed out after ${PROBE_TIMEOUT_MS} ms.`
        : "The probe request failed before a valid response was received.",
      statusCode: null,
      latencyMs: roundedMilliseconds(monotonicNow() - started),
      serviceLatencyMs: null,
      payload: null,
    };
  } finally {
    clearTimeout(timeout);
  }
}

function failedProbe(
  reason: string,
  statusCode: number,
  latencyMs: number,
  response: Response,
): JsonProbe {
  return {
    available: false,
    reason,
    statusCode,
    latencyMs,
    serviceLatencyMs: responseLatency(response),
    payload: null,
  };
}

function unavailableProbe(reason: string): Promise<JsonProbe> {
  return Promise.resolve({
    available: false,
    reason,
    statusCode: null,
    latencyMs: null,
    serviceLatencyMs: null,
    payload: null,
  });
}

function normalizeApiBase(value: unknown): { url: URL | null; reason: string } {
  if (typeof value !== "string" || !value.trim()) {
    return {
      url: null,
      reason: "TMCRA_MEMORY_API_BASE_URL is not configured for the internal runtime probe.",
    };
  }
  let url: URL;
  try {
    url = new URL(value.trim().replace(/\/+$/, "") + "/");
  } catch {
    return { url: null, reason: "TMCRA_MEMORY_API_BASE_URL is invalid." };
  }
  const loopback = ["127.0.0.1", "localhost", "::1"].includes(url.hostname);
  if (
    (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    return {
      url: null,
      reason: "TMCRA_MEMORY_API_BASE_URL is not an approved HTTPS or loopback origin.",
    };
  }
  return { url, reason: "" };
}

function normalizeStaffApiBase({
  publicBase,
  controlBase,
  allowHttpLoopback,
}: {
  publicBase: unknown;
  controlBase: unknown;
  allowHttpLoopback: boolean;
}): { url: URL | null; reason: string } {
  const selected = typeof controlBase === "string" && controlBase.trim()
    ? controlBase
    : publicBase;
  if (typeof selected !== "string" || !selected.trim()) {
    return {
      url: null,
      reason: "A Memory API base URL is not configured for staff telemetry.",
    };
  }
  let url: URL;
  try {
    url = new URL(selected.trim().replace(/\/+$/, "") + "/");
  } catch {
    return { url: null, reason: "The Memory API staff telemetry base URL is invalid." };
  }
  const approvedLoopback =
    allowHttpLoopback &&
    url.protocol === "http:" &&
    url.hostname === "127.0.0.1";
  if (
    (url.protocol !== "https:" && !approvedLoopback) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    return {
      url: null,
      reason: "The staff telemetry URL is not an approved HTTPS or explicit loopback origin.",
    };
  }
  return { url, reason: "" };
}

function normalizeStaffKey(value: unknown): { value: string | null; reason: string } {
  if (typeof value !== "string" || !value.trim()) {
    return {
      value: null,
      reason: "TMCRA_MEMORY_API_STAFF_MONITORING_KEY is not configured.",
    };
  }
  const key = value.trim();
  if (key.length < 32 || key.length > 512 || /\s/.test(key)) {
    return {
      value: null,
      reason: "TMCRA_MEMORY_API_STAFF_MONITORING_KEY is invalid.",
    };
  }
  return { value: key, reason: "" };
}

function normalizeSiteOrigin(requestUrl: string): { url: URL | null; reason: string } {
  let url: URL;
  try {
    url = new URL(requestUrl);
  } catch {
    return { url: null, reason: "The current site origin is invalid." };
  }
  const loopback = ["127.0.0.1", "localhost", "::1"].includes(url.hostname);
  const productionHost = url.hostname === "tmcra.com" || url.hostname === "www.tmcra.com";
  if (
    !((url.protocol === "https:" && productionHost) ||
      (url.protocol === "http:" && loopback))
  ) {
    return {
      url: null,
      reason: "The current site origin is not approved for the deployment probe.",
    };
  }
  return { url: new URL(url.origin), reason: "" };
}

function responseLatency(response: Response): number | null {
  return finiteNumber(response.headers.get("x-tmcra-latency-ms"));
}

function roundedMilliseconds(value: number): number {
  return Math.round(Math.max(0, value) * 100) / 100;
}

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function booleanOrNull(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function shortString(value: unknown, maximumLength = 128): string | null {
  return typeof value === "string" && value.length > 0 && value.length <= maximumLength
    ? value
    : null;
}

function booleanRecord(value: unknown): Record<string, boolean> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const entries = Object.entries(value);
  if (entries.length === 0 || entries.length > 64) return null;
  const output: Record<string, boolean> = {};
  for (const [key, entry] of entries) {
    if (!/^[a-z][a-z0-9_]{0,63}$/.test(key) || typeof entry !== "boolean") {
      return null;
    }
    output[key] = entry;
  }
  return output;
}
