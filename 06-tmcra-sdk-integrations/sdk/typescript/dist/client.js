import { TMCRAAbortError, TMCRAHttpError, TMCRAJobFailedError, TMCRAJobPollingTimeoutError, TMCRANetworkError, TMCRAResponseParseError, TMCRATimeoutError, } from "./errors.js";
import { isTerminalJobStatus, } from "./models.js";
const DEFAULT_RETRY_STATUS_CODES = [408, 425, 429, 500, 502, 503, 504];
const DEFAULT_RETRY = {
    maxAttempts: 3,
    initialDelayMs: 250,
    maxDelayMs: 30_000,
    jitter: 0.2,
    retryStatusCodes: DEFAULT_RETRY_STATUS_CODES,
};
const DEFAULT_TIMEOUT_MS = 30_000;
const DEFAULT_POLL_TIMEOUT_MS = 300_000;
let idempotencyCounter = 0;
function assertFiniteNonNegative(value, name) {
    if (!Number.isFinite(value) || value < 0) {
        throw new RangeError(`${name} must be a finite non-negative number`);
    }
}
function toWireValue(value) {
    if (value instanceof Date) {
        if (Number.isNaN(value.getTime()))
            throw new TypeError("Invalid Date");
        return value.toISOString();
    }
    return value;
}
function randomIdempotencyKey() {
    const webCrypto = globalThis.crypto;
    if (webCrypto?.randomUUID)
        return webCrypto.randomUUID();
    if (webCrypto?.getRandomValues) {
        const bytes = new Uint8Array(16);
        webCrypto.getRandomValues(bytes);
        bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
        bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
        const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
        return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex
            .slice(6, 8)
            .join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
    }
    idempotencyCounter += 1;
    return `tmcra-${Date.now().toString(36)}-${idempotencyCounter.toString(36)}-${Math.random()
        .toString(36)
        .slice(2)}`;
}
function mergeHeaders(...sources) {
    const result = new Headers();
    for (const source of sources) {
        if (!source)
            continue;
        new Headers(source).forEach((value, key) => result.set(key, value));
    }
    return result;
}
function parseRetryAfter(value) {
    if (!value)
        return undefined;
    const seconds = Number(value.trim());
    if (Number.isFinite(seconds) && seconds >= 0)
        return seconds;
    const date = Date.parse(value);
    if (Number.isNaN(date))
        return undefined;
    return Math.max(0, (date - Date.now()) / 1000);
}
function isAbortLike(error) {
    return (typeof error === "object" &&
        error !== null &&
        "name" in error &&
        error.name === "AbortError");
}
function sleep(delayMs, signal) {
    if (delayMs <= 0)
        return Promise.resolve();
    return new Promise((resolve, reject) => {
        if (signal?.aborted) {
            reject(new TMCRAAbortError({ cause: signal.reason }));
            return;
        }
        const timer = setTimeout(resolve, delayMs);
        const onAbort = () => {
            clearTimeout(timer);
            signal?.removeEventListener("abort", onAbort);
            reject(new TMCRAAbortError({ cause: signal?.reason }));
        };
        signal?.addEventListener("abort", onAbort, { once: true });
    });
}
function composeSignal(signal, timeoutMs) {
    if (timeoutMs !== undefined)
        assertFiniteNonNegative(timeoutMs, "timeoutMs");
    if (!signal && timeoutMs === undefined)
        return { signal: undefined, timedOut: () => false, cleanup: () => { } };
    const controller = new AbortController();
    let timedOut = false;
    let timer;
    const onAbort = () => controller.abort(signal?.reason);
    if (signal) {
        if (signal.aborted)
            controller.abort(signal.reason);
        else
            signal.addEventListener("abort", onAbort, { once: true });
    }
    if (timeoutMs !== undefined) {
        timer = setTimeout(() => {
            timedOut = true;
            controller.abort(new Error("TMCRA timeout"));
        }, timeoutMs);
    }
    return {
        signal: controller.signal,
        timedOut: () => timedOut,
        cleanup: () => {
            if (timer)
                clearTimeout(timer);
            signal?.removeEventListener("abort", onAbort);
        },
    };
}
async function readJson(response) {
    const text = await response.text();
    if (!text.trim())
        return undefined;
    try {
        return JSON.parse(text);
    }
    catch (error) {
        throw new TMCRAResponseParseError(response.status, { cause: error, details: text.slice(0, 4096) });
    }
}
async function readErrorPayload(response) {
    const text = await response.text();
    if (!text.trim())
        return undefined;
    try {
        return JSON.parse(text);
    }
    catch {
        return text.slice(0, 4096);
    }
}
function messageFromPayload(payload, status) {
    if (typeof payload === "string" && payload)
        return payload;
    if (typeof payload === "object" && payload !== null) {
        const record = payload;
        const error = record.error;
        if (typeof error === "object" && error !== null) {
            const message = error.message;
            if (typeof message === "string" && message)
                return message;
            const code = error.code;
            if (typeof code === "string" && code)
                return code;
        }
        const detail = record.detail;
        if (typeof detail === "string" && detail)
            return detail;
        if (detail !== undefined)
            return JSON.stringify(detail);
    }
    return `TMCRA request failed with HTTP ${status}`;
}
function calculateRetryDelay(error, attempt, retry) {
    const retryAfter = error.retryAfterSeconds === undefined ? undefined : error.retryAfterSeconds * 1000;
    const exponential = Math.min(retry.maxDelayMs, retry.initialDelayMs * 2 ** (attempt - 1));
    const base = retryAfter === undefined ? exponential : Math.min(retry.maxDelayMs, retryAfter);
    if (retryAfter !== undefined)
        return base;
    const jitter = base * Math.min(1, Math.max(0, retry.jitter));
    return Math.max(0, base - jitter + Math.random() * jitter * 2);
}
export class TMCRAClient {
    baseUrl;
    apiKey;
    fetchImpl;
    defaultTimeoutMs;
    retryPolicy;
    defaultHeaders;
    constructor(options) {
        const resolvedBaseUrl = options.baseUrl ?? "https://api.tmcra.com";
        const base = new URL(resolvedBaseUrl);
        if (base.protocol !== "http:" && base.protocol !== "https:") {
            throw new TypeError("baseUrl must use http or https");
        }
        this.baseUrl = resolvedBaseUrl.replace(/\/+$/, "");
        this.apiKey = options.apiKey;
        const fetchImpl = options.fetch ?? globalThis.fetch?.bind(globalThis);
        if (!fetchImpl)
            throw new TypeError("This runtime does not provide fetch; pass options.fetch");
        this.fetchImpl = fetchImpl;
        if (options.defaultTimeoutMs !== undefined)
            assertFiniteNonNegative(options.defaultTimeoutMs, "defaultTimeoutMs");
        this.defaultTimeoutMs = options.defaultTimeoutMs ?? DEFAULT_TIMEOUT_MS;
        const retry = { ...DEFAULT_RETRY, ...(options.retry ?? {}) };
        if (!Number.isInteger(retry.maxAttempts) || retry.maxAttempts < 1)
            throw new RangeError("maxAttempts must be a positive integer");
        assertFiniteNonNegative(retry.initialDelayMs, "initialDelayMs");
        assertFiniteNonNegative(retry.maxDelayMs, "maxDelayMs");
        if (retry.maxDelayMs < retry.initialDelayMs)
            throw new RangeError("maxDelayMs must be >= initialDelayMs");
        if (!Array.isArray(retry.retryStatusCodes) || retry.retryStatusCodes.some((status) => !Number.isInteger(status))) {
            throw new RangeError("retryStatusCodes must contain integers");
        }
        this.retryPolicy = retry;
        this.defaultHeaders = mergeHeaders({
            "X-TMCRA-Client-Platform": options.clientPlatform ?? "typescript",
            ...(options.integrationId ? { "X-TMCRA-Integration-ID": options.integrationId } : {}),
            ...(options.agentId ? { "X-TMCRA-Agent-ID": options.agentId } : {}),
        }, options.headers);
    }
    async healthz(options = {}) {
        return this.requestJson("healthz", { method: "GET" }, { ...options, retryMode: "safe" });
    }
    async readyz(options = {}) {
        return this.requestJson("readyz", { method: "GET" }, { ...options, retryMode: "safe" });
    }
    async authenticatedSession(options = {}) {
        return this.requestJson("v1/session", { method: "GET" }, { ...options, retryMode: "safe" });
    }
    async listScopes(options = {}) {
        const { prefix, limit = 100, ...requestOptions } = options;
        if (!Number.isInteger(limit) || limit < 1 || limit > 1000)
            throw new RangeError("limit must be between 1 and 1000");
        if (prefix !== undefined && (prefix.length < 1 || prefix.length > 128))
            throw new RangeError("prefix must be 1-128 characters");
        const params = new URLSearchParams({ limit: String(limit) });
        if (prefix !== undefined)
            params.set("prefix", prefix);
        return this.requestJson(`v1/scopes?${params}`, { method: "GET" }, { ...requestOptions, retryMode: "safe" });
    }
    async scopeSummary(scopeName, options = {}) {
        return this.requestJson(`v1/scopes/${encodeURIComponent(scopeName)}/summary`, { method: "GET" }, { ...options, retryMode: "safe" });
    }
    async quota(subject, options = {}) {
        const query = subject === undefined ? "" : `?subject=${encodeURIComponent(subject)}`;
        return this.requestJson(`v1/usage/quota${query}`, { method: "GET" }, { ...options, retryMode: "safe" });
    }
    async billingProfile(options = {}) {
        return this.requestJson("v1/billing/profile", { method: "GET" }, { ...options, retryMode: "safe" });
    }
    async setEntitlement(subject, body, options = {}) {
        return this.requestJson(`v1/usage/entitlements/${encodeURIComponent(subject)}`, {
            method: "PUT",
            body: JSON.stringify(body),
        }, { ...options, retryMode: "safe" });
    }
    async setQuotaEntitlement(subject, body, options = {}) {
        return this.requestJson(`v1/usage/quota?subject=${encodeURIComponent(subject)}`, {
            method: "PUT",
            body: JSON.stringify(body),
        }, { ...options, retryMode: "safe" });
    }
    async ingest(scopeName, body, options = {}) {
        const idempotencyKey = this.requireIdempotencyKey(options.idempotencyKey);
        const payload = {
            ...body,
            messages: body.messages.map((message) => ({ ...message, timestamp: toWireValue(message.timestamp) })),
        };
        return this.requestJson(`v1/scopes/${encodeURIComponent(scopeName)}/ingest`, {
            method: "POST",
            body: JSON.stringify(payload),
        }, { ...options, headers: mergeHeaders(options.headers, { "Idempotency-Key": idempotencyKey }), retryMode: "safe" });
    }
    async bulkIngest(scopeName, body, options = {}) {
        const firstKey = body.items[0]?.idempotency_key;
        if (!firstKey)
            throw new RangeError("bulk ingest requires at least one item");
        const retryKey = this.requireIdempotencyKey(firstKey);
        const payload = {
            items: body.items.map((item) => ({
                ...item,
                messages: item.messages.map((message) => ({ ...message, timestamp: toWireValue(message.timestamp) })),
            })),
        };
        return this.requestJson(`v1/scopes/${encodeURIComponent(scopeName)}/ingest/batch`, {
            method: "POST",
            body: JSON.stringify(payload),
        }, { ...options, headers: mergeHeaders(options.headers, { "Idempotency-Key": retryKey }), retryMode: "safe" });
    }
    async consolidate(scopeName, options = {}) {
        const idempotencyKey = this.requireIdempotencyKey(options.idempotencyKey);
        return this.requestJson(`v1/scopes/${encodeURIComponent(scopeName)}/consolidate`, {
            method: "POST",
            body: "{}",
        }, { ...options, headers: mergeHeaders(options.headers, { "Idempotency-Key": idempotencyKey }), retryMode: "safe" });
    }
    async recall(scopeName, body, options = {}) {
        const payload = { ...body, ...(body.query_time ? { query_time: toWireValue(body.query_time) } : {}) };
        return this.requestJson(`v1/scopes/${encodeURIComponent(scopeName)}/recall`, {
            method: "POST",
            body: JSON.stringify(payload),
        }, { ...options, retryMode: "never" });
    }
    async memoryGraph(scopeName, options = {}) {
        const { layers = ["slow"], limit = 180, cursor, query, ...requestOptions } = options;
        const params = new URLSearchParams({ layers: layers.join(","), limit: String(limit) });
        if (cursor)
            params.set("cursor", cursor);
        if (query)
            params.set("query", query);
        return this.requestJson(`v1/scopes/${encodeURIComponent(scopeName)}/memory-graph?${params}`, { method: "GET" }, { ...requestOptions, retryMode: "safe" });
    }
    async memoryGraphNeighbors(scopeName, memoryId, options = {}) {
        const { depth = 1, layers = ["slow", "fast", "source"], limit = 80, cursor, ...requestOptions } = options;
        const params = new URLSearchParams({
            depth: String(depth),
            layers: layers.join(","),
            limit: String(limit),
        });
        if (cursor)
            params.set("cursor", cursor);
        return this.requestJson(`v1/scopes/${encodeURIComponent(scopeName)}/memory-graph/nodes/${encodeURIComponent(memoryId)}/neighbors?${params}`, { method: "GET" }, { ...requestOptions, retryMode: "safe" });
    }
    async memoryGraphEvidence(scopeName, memoryId, options = {}) {
        const { limit = 10, cursor, ...requestOptions } = options;
        const params = new URLSearchParams({ limit: String(limit) });
        if (cursor)
            params.set("cursor", cursor);
        return this.requestJson(`v1/scopes/${encodeURIComponent(scopeName)}/memory-graph/nodes/${encodeURIComponent(memoryId)}/evidence?${params}`, { method: "GET" }, { ...requestOptions, retryMode: "safe" });
    }
    async traceMemoryRecall(scopeName, body, options = {}) {
        const payload = {
            ...body,
            ...(body.query_time ? { query_time: toWireValue(body.query_time) } : {}),
        };
        return this.requestJson(`v1/scopes/${encodeURIComponent(scopeName)}/memory-graph/trace`, { method: "POST", body: JSON.stringify(payload) }, { ...options, retryMode: "never" });
    }
    async getJob(jobId, options = {}) {
        return this.requestJson(`v1/jobs/${encodeURIComponent(jobId)}`, { method: "GET" }, { ...options, retryMode: "safe" });
    }
    async cancelJob(jobId, options = {}) {
        return this.requestJson(`v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
            method: "POST",
            body: "{}",
        }, { ...options, retryMode: "never" });
    }
    async retryJob(jobId, options = {}) {
        const idempotencyKey = this.requireIdempotencyKey(options.idempotencyKey);
        return this.requestJson(`v1/jobs/${encodeURIComponent(jobId)}/retry`, {
            method: "POST",
            body: "{}",
        }, { ...options, headers: mergeHeaders(options.headers, { "Idempotency-Key": idempotencyKey }), retryMode: "safe" });
    }
    async usageCosts(scopeName, options = {}) {
        const { scopePrefix, fromTimestamp, toTimestamp, groupBy, ...requestOptions } = options;
        const parameters = new URLSearchParams();
        if (scopeName !== undefined)
            parameters.set("scope_name", scopeName);
        if (scopePrefix !== undefined)
            parameters.set("scope_prefix", scopePrefix);
        if (fromTimestamp !== undefined)
            parameters.set("from_timestamp", String(fromTimestamp));
        if (toTimestamp !== undefined)
            parameters.set("to_timestamp", String(toTimestamp));
        if (groupBy !== undefined)
            parameters.set("group_by", groupBy);
        const query = parameters.size ? `?${parameters}` : "";
        return this.requestJson(`v1/usage/costs${query}`, { method: "GET" }, { ...requestOptions, retryMode: "safe" });
    }
    async issueAccessToken(body, options = {}) {
        const idempotencyKey = this.requireIdempotencyKey(options.idempotencyKey);
        return this.requestJson("v1/access-tokens", {
            method: "POST",
            body: JSON.stringify(body),
        }, { ...options, headers: mergeHeaders(options.headers, { "Idempotency-Key": idempotencyKey }), retryMode: "safe" });
    }
    async confirmAccessToken(tokenId, options = {}) {
        return this.requestJson(`v1/access-tokens/${encodeURIComponent(tokenId)}/confirm`, { method: "POST" }, { ...options, retryMode: "never" });
    }
    async listAccessTokens(options = {}) {
        return this.requestJson("v1/access-tokens", { method: "GET" }, { ...options, retryMode: "safe" });
    }
    async revokeAccessToken(tokenId, options = {}) {
        return this.requestJson(`v1/access-tokens/${encodeURIComponent(tokenId)}`, { method: "DELETE" }, { ...options, retryMode: "never" });
    }
    async createWebhook(body, options = {}) {
        return this.requestJson("v1/webhooks", {
            method: "POST",
            body: JSON.stringify(body),
        }, { ...options, retryMode: "never" });
    }
    async listWebhooks(options = {}) {
        return this.requestJson("v1/webhooks", { method: "GET" }, { ...options, retryMode: "safe" });
    }
    async disableWebhook(endpointId, options = {}) {
        return this.requestJson(`v1/webhooks/${encodeURIComponent(endpointId)}`, { method: "DELETE" }, { ...options, retryMode: "never" });
    }
    async exportScope(scopeName, options = {}) {
        const idempotencyKey = this.requireIdempotencyKey(options.idempotencyKey);
        return this.requestJson(`v1/scopes/${encodeURIComponent(scopeName)}/exports`, {
            method: "POST",
            body: "{}",
        }, { ...options, headers: mergeHeaders(options.headers, { "Idempotency-Key": idempotencyKey }), retryMode: "safe" });
    }
    async downloadScopeExport(scopeName, exportId, options = {}) {
        const response = await this.request(`v1/scopes/${encodeURIComponent(scopeName)}/exports/${encodeURIComponent(exportId)}`, { method: "GET" }, { ...options, headers: mergeHeaders(options.headers, { Accept: "application/zip" }), retryMode: "safe" });
        return new Uint8Array(await response.arrayBuffer());
    }
    async deleteScope(scopeName, options = {}) {
        const idempotencyKey = this.requireIdempotencyKey(options.idempotencyKey);
        return this.requestJson(`v1/scopes/${encodeURIComponent(scopeName)}`, {
            method: "DELETE",
            body: "{}",
        }, {
            ...options,
            headers: mergeHeaders(options.headers, {
                "Idempotency-Key": idempotencyKey,
                "X-TMCRA-Confirm-Scope": scopeName,
            }),
            retryMode: "safe",
        });
    }
    async reopenScope(scopeName, options = {}) {
        return this.requestJson(`v1/scopes/${encodeURIComponent(scopeName)}/reopen`, {
            method: "POST",
            body: "{}",
        }, { ...options, retryMode: "never" });
    }
    async setRetentionPolicy(scopeName, body, options = {}) {
        return this.requestJson(`v1/scopes/${encodeURIComponent(scopeName)}/retention`, {
            method: "PUT",
            body: JSON.stringify(body),
        }, { ...options, retryMode: "safe" });
    }
    async getRetentionPolicy(scopeName, options = {}) {
        return this.requestJson(`v1/scopes/${encodeURIComponent(scopeName)}/retention`, {
            method: "GET",
        }, { ...options, retryMode: "safe" });
    }
    async submitFeedback(scopeName, body, options = {}) {
        return this.requestJson(`v1/scopes/${encodeURIComponent(scopeName)}/feedback`, {
            method: "POST",
            body: JSON.stringify(body),
        }, { ...options, retryMode: "never" });
    }
    async waitForJob(jobId, options = {}) {
        const { pollIntervalMs = 500, maxPollIntervalMs = 5_000, pollBackoffFactor = 1.5, throwOnFailure = false, timeoutMs = DEFAULT_POLL_TIMEOUT_MS, ...requestOptions } = options;
        assertFiniteNonNegative(timeoutMs, "timeoutMs");
        assertFiniteNonNegative(pollIntervalMs, "pollIntervalMs");
        assertFiniteNonNegative(maxPollIntervalMs, "maxPollIntervalMs");
        if (pollBackoffFactor < 1 || !Number.isFinite(pollBackoffFactor))
            throw new RangeError("pollBackoffFactor must be >= 1");
        const deadline = Date.now() + timeoutMs;
        let delay = Math.min(pollIntervalMs, maxPollIntervalMs);
        let lastJob;
        while (true) {
            const remaining = deadline - Date.now();
            if (remaining < 0)
                throw new TMCRAJobPollingTimeoutError(jobId, timeoutMs, lastJob);
            lastJob = await this.getJob(jobId, {
                ...requestOptions,
                timeoutMs: remaining,
            });
            if (isTerminalJobStatus(lastJob.status)) {
                if (throwOnFailure && lastJob.status !== "succeeded")
                    throw new TMCRAJobFailedError(jobId, lastJob);
                return lastJob;
            }
            const waitMs = Math.min(delay, Math.max(0, deadline - Date.now()));
            await sleep(waitMs, requestOptions.signal);
            if (Date.now() >= deadline)
                throw new TMCRAJobPollingTimeoutError(jobId, timeoutMs, lastJob);
            delay = Math.min(maxPollIntervalMs, Math.max(delay, delay * pollBackoffFactor));
        }
    }
    requireIdempotencyKey(value) {
        const key = value ?? randomIdempotencyKey();
        if (key.length < 8 || key.length > 200)
            throw new RangeError("idempotencyKey must be 8-200 characters");
        return key;
    }
    async requestJson(path, init, options) {
        const response = await this.request(path, init, options);
        if (response.status === 204)
            return undefined;
        const payload = await readJson(response);
        return payload;
    }
    async request(path, init, options) {
        const method = (init.method ?? "GET").toUpperCase();
        const retryEnabled = options.retry !== false && options.retryMode === "safe";
        const maxAttempts = retryEnabled ? this.retryPolicy.maxAttempts : 1;
        const timeoutMs = options.timeoutMs ?? this.defaultTimeoutMs;
        const headers = mergeHeaders(this.defaultHeaders, options.headers, init.headers, {
            Accept: "application/json",
            ...(init.body !== undefined ? { "Content-Type": "application/json" } : {}),
            ...(this.apiKey ? { Authorization: `Bearer ${this.apiKey}` } : {}),
        });
        const url = new URL(path.replace(/^\/+/, ""), `${this.baseUrl}/`).toString();
        for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
            const composed = composeSignal(options.signal, timeoutMs);
            try {
                const response = await this.fetchImpl(url, { ...init, method, headers, signal: composed.signal });
                if (response.ok)
                    return response;
                const payload = await readErrorPayload(response);
                const error = new TMCRAHttpError(messageFromPayload(payload, response.status), {
                    status: response.status,
                    method,
                    path,
                    requestId: response.headers.get("x-request-id") ?? undefined,
                    details: payload,
                    retryAfterSeconds: parseRetryAfter(response.headers.get("retry-after")),
                });
                if (attempt < maxAttempts && this.retryPolicy.retryStatusCodes.includes(response.status)) {
                    await sleep(calculateRetryDelay(error, attempt, this.retryPolicy), options.signal);
                    continue;
                }
                throw error;
            }
            catch (error) {
                if (error instanceof TMCRAHttpError)
                    throw error;
                let normalized;
                if (composed.timedOut())
                    normalized = new TMCRATimeoutError(timeoutMs ?? 0, { cause: error });
                else if (options.signal?.aborted || isAbortLike(error))
                    normalized = new TMCRAAbortError({ cause: error });
                else
                    normalized = new TMCRANetworkError(`TMCRA network request failed: ${error instanceof Error ? error.message : String(error)}`, { cause: error });
                if (attempt < maxAttempts &&
                    retryEnabled &&
                    (normalized instanceof TMCRANetworkError || normalized instanceof TMCRATimeoutError)) {
                    await sleep(calculateRetryDelay(new TMCRAHttpError(normalized.message, { status: 503, method, path }), attempt, this.retryPolicy), options.signal);
                    continue;
                }
                throw normalized;
            }
            finally {
                composed.cleanup();
            }
        }
        throw new Error("unreachable");
    }
}
//# sourceMappingURL=client.js.map