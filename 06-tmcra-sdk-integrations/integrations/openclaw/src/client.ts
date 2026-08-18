export interface TmcraClientConfig {
  baseUrl: string;
  apiKey: string;
  requestTimeoutMs?: number;
  fetchImpl?: typeof fetch;
  integrationId?: string;
}

export interface RecallInput {
  scopeName: string;
  query: string;
  evidenceMode: "raw" | "auto" | "compiled";
  maxWindows: number;
  waitForJobId?: string;
  agentId?: string;
}

export interface RecallResult {
  prompt_evidence?: unknown;
  [key: string]: unknown;
}

export type JobStatus = "queued" | "pending" | "running" | "succeeded" | "failed" | "cancelled";

export interface IngestSubmission {
  status: JobStatus;
  jobId?: string;
  requestId?: string;
  [key: string]: unknown;
}

export interface JobPollResult {
  status: JobStatus;
  requestId?: string;
  error?: string;
  [key: string]: unknown;
}

export interface IngestInput {
  scopeName: string;
  payload: Record<string, unknown>;
  idempotencyKey: string;
  agentId?: string;
}

function joinUrl(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/$/, "")}${path}`;
}

function stringField(value: Record<string, unknown>, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
  }
  return undefined;
}

function jobStatus(value: Record<string, unknown>): JobStatus {
  const status = stringField(value, "status", "state") as JobStatus | undefined;
  return ["queued", "pending", "running", "succeeded", "failed", "cancelled"].includes(status || "")
    ? status as JobStatus
    : "pending";
}

export class TmcraHttpError extends Error {
  readonly status: number;
  readonly operation: string;

  constructor(status: number, operation: string) {
    super(`TMCRA ${operation} failed with HTTP ${status}`);
    this.name = "TmcraHttpError";
    this.status = status;
    this.operation = operation;
  }
}

export class TmcraClient {
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly requestTimeoutMs: number;
  private readonly fetchImpl: typeof fetch;
  private readonly integrationId?: string;

  constructor({ baseUrl, apiKey, requestTimeoutMs = 15_000, fetchImpl = fetch, integrationId }: TmcraClientConfig) {
    this.baseUrl = baseUrl;
    this.apiKey = apiKey;
    this.requestTimeoutMs = requestTimeoutMs;
    this.fetchImpl = fetchImpl;
    this.integrationId = integrationId;
  }

  private async request(
    path: string,
    options: {
      method?: string;
      body?: unknown;
      idempotencyKey?: string;
      operation: string;
      agentId?: string;
    },
  ): Promise<Record<string, unknown>> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.requestTimeoutMs);
    timeout.unref?.();
    try {
      const headers: Record<string, string> = {
        Authorization: `Bearer ${this.apiKey}`,
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-TMCRA-Client-Platform": "openclaw",
        ...(this.integrationId ? { "X-TMCRA-Integration-ID": this.integrationId } : {}),
        ...(options.agentId ? { "X-TMCRA-Agent-ID": options.agentId } : {}),
      };
      if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
      const response = await this.fetchImpl(joinUrl(this.baseUrl, path), {
        method: options.method ?? "POST",
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: controller.signal,
      });
      const raw = await response.text();
      let parsed: unknown = {};
      if (raw) {
        try {
          parsed = JSON.parse(raw);
        } catch {
          parsed = {};
        }
      }
      if (!response.ok) throw new TmcraHttpError(response.status, options.operation);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? parsed as Record<string, unknown>
        : {};
    } finally {
      clearTimeout(timeout);
    }
  }

  recall({ scopeName, query, evidenceMode, maxWindows, waitForJobId, agentId }: RecallInput): Promise<RecallResult> {
    return this.request(`/v1/scopes/${encodeURIComponent(scopeName)}/recall`, {
      body: {
        query,
        evidence_mode: evidenceMode,
        max_windows: maxWindows,
        ...(waitForJobId ? { wait_for_job_id: waitForJobId } : {}),
      },
      operation: "recall",
      agentId,
    });
  }

  async ingest({ scopeName, payload, idempotencyKey, agentId }: IngestInput): Promise<IngestSubmission> {
    const response = await this.request(`/v1/scopes/${encodeURIComponent(scopeName)}/ingest`, {
      body: payload,
      idempotencyKey,
      operation: "ingest",
      agentId,
    });
    const submission = {
      ...response,
      status: jobStatus(response),
      jobId: stringField(response, "job_id", "jobId", "id"),
      requestId: stringField(response, "request_id", "requestId"),
    } satisfies IngestSubmission;
    if (submission.status !== "succeeded" && !submission.jobId) {
      throw new Error("TMCRA ingest accepted without a job_id");
    }
    return submission;
  }

  async pollJob(jobId: string, agentId?: string): Promise<JobPollResult> {
    const response = await this.request(`/v1/jobs/${encodeURIComponent(jobId)}`, {
      method: "GET",
      operation: "job_poll",
      agentId,
    });
    return {
      ...response,
      status: jobStatus(response),
      requestId: stringField(response, "request_id", "requestId"),
      error: stringField(response, "error", "error_message", "message"),
    } satisfies JobPollResult;
  }
}
