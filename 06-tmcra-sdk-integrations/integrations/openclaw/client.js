function joinUrl(baseUrl, path) {
  return `${baseUrl.replace(/\/$/, "")}${path}`;
}

function safeResponseSummary(status) {
  return `TMCRA request failed with HTTP ${status}`;
}

export class TmcraHttpError extends Error {
  constructor(status, operation) {
    super(safeResponseSummary(status));
    this.name = "TmcraHttpError";
    this.status = status;
    this.operation = operation;
  }
}

export class TmcraClient {
  constructor({ baseUrl, apiKey, requestTimeoutMs = 15000, fetchImpl = fetch }) {
    this.baseUrl = baseUrl;
    this.apiKey = apiKey;
    this.requestTimeoutMs = requestTimeoutMs;
    this.fetchImpl = fetchImpl;
  }

  async request(path, { method = "POST", body, idempotencyKey, operation }) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.requestTimeoutMs);
    if (typeof timeout.unref === "function") timeout.unref();
    try {
      const headers = {
        Authorization: `Bearer ${this.apiKey}`,
        "Content-Type": "application/json",
        Accept: "application/json",
      };
      if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
      const response = await this.fetchImpl(joinUrl(this.baseUrl, path), {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      const raw = await response.text();
      let parsed = null;
      if (raw) {
        try {
          parsed = JSON.parse(raw);
        } catch {
          parsed = null;
        }
      }
      if (!response.ok) throw new TmcraHttpError(response.status, operation);
      return parsed ?? {};
    } finally {
      clearTimeout(timeout);
    }
  }

  recall({ scopeName, query, evidenceMode, maxWindows, waitForJobId }) {
    const queryParams = {
      query,
      evidence_mode: evidenceMode,
      max_windows: maxWindows,
    };
    if (waitForJobId) queryParams.wait_for_job_id = waitForJobId;
    return this.request(`/v1/scopes/${encodeURIComponent(scopeName)}/recall`, {
      body: queryParams,
      operation: "recall",
    });
  }

  ingest({ scopeName, payload, idempotencyKey }) {
    return this.request(`/v1/scopes/${encodeURIComponent(scopeName)}/ingest`, {
      body: payload,
      idempotencyKey,
      operation: "ingest",
    });
  }
}
