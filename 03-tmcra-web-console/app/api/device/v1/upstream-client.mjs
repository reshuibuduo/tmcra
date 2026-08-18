const MAXIMUM_RESPONSE_BYTES = 65_536;

export function createMemoryControlClient({
  baseUrl,
  controlKey,
  fetchImpl = fetch,
  allowHttpLoopback = false,
}) {
  const normalizedBaseUrl = normalizeMemoryApiBaseUrl(baseUrl, { allowHttpLoopback });
  const credential = String(controlKey ?? "").trim();
  if (!credential) throw clientError(503, "memory_control_not_configured", "Memory control service is not configured.");

  async function request(pathname, init, requestId, timeoutMs = 30_000) {
    const url = new URL(pathname, `${normalizedBaseUrl}/`);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    let response;
    try {
      response = await fetchImpl(url, {
        ...init,
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${credential}`,
          "X-Request-ID": requestId,
          ...init.headers,
        },
        // Workerd does not implement Fetch's error-on-redirect mode. Manual mode keeps
        // control-plane credentials on the configured origin and lets the
        // non-2xx handling below reject every redirect response.
        redirect: "manual",
        signal: controller.signal,
      });
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") {
        throw clientError(504, "memory_control_timeout", "Memory control service timed out.");
      }
      throw clientError(502, "memory_control_unavailable", "Memory control service is unavailable.");
    } finally {
      clearTimeout(timeout);
    }
    const text = await boundedResponseText(response, MAXIMUM_RESPONSE_BYTES);
    let value = null;
    if (text) {
      try {
        value = JSON.parse(text);
      } catch {
        throw clientError(502, "memory_control_invalid_response", "Memory control service returned invalid JSON.");
      }
    }
    if (!response.ok) {
      const status = [400, 401, 403, 404, 409, 422, 429, 503].includes(response.status)
        ? response.status
        : 502;
      throw clientError(status, "memory_control_request_failed", upstreamMessage(value));
    }
    return value;
  }

  return {
    baseUrl: normalizedBaseUrl,
    issue(body, requestId) {
      return request(
        "/v1/access-tokens",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": requestId,
          },
          body: JSON.stringify(body),
        },
        requestId,
      );
    },
    confirm(tokenId, requestId) {
      return request(
        `/v1/access-tokens/${encodeURIComponent(tokenId)}/confirm`,
        { method: "POST", headers: { "Idempotency-Key": requestId } },
        requestId,
      );
    },
    async revoke(tokenId, requestId) {
      try {
        return await request(
          `/v1/access-tokens/${encodeURIComponent(tokenId)}`,
          { method: "DELETE" },
          requestId,
          5_000,
        );
      } catch (error) {
        if (error?.status === 404) return { token_id: tokenId, revoked: true };
        throw error;
      }
    },
  };
}

export function normalizeMemoryApiBaseUrl(raw, { allowHttpLoopback = false } = {}) {
  let url;
  try {
    url = new URL(String(raw ?? "").trim());
  } catch {
    throw clientError(503, "memory_control_not_configured", "Memory control endpoint is not configured.");
  }
  const loopback = url.hostname === "127.0.0.1";
  if (
    (url.protocol !== "https:" && !(allowHttpLoopback && loopback && url.protocol === "http:")) ||
    url.username ||
    url.password
  ) {
    throw clientError(503, "memory_control_not_configured", "Memory control endpoint is invalid.");
  }
  url.pathname = url.pathname.replace(/\/+$/, "");
  url.search = "";
  url.hash = "";
  return url.toString().replace(/\/$/, "");
}

async function boundedResponseText(response, maximumBytes) {
  const announced = Number(response.headers.get("content-length") ?? "0");
  if (Number.isFinite(announced) && announced > maximumBytes) {
    throw clientError(502, "memory_control_response_too_large", "Memory control response is too large.");
  }
  const text = await response.text();
  if (new TextEncoder().encode(text).byteLength > maximumBytes) {
    throw clientError(502, "memory_control_response_too_large", "Memory control response is too large.");
  }
  return text;
}

function upstreamMessage(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "Memory control request failed.";
  const detail = value.detail && typeof value.detail === "object" && !Array.isArray(value.detail)
    ? value.detail
    : value;
  const message = typeof detail.message === "string"
    ? detail.message
    : typeof value.detail === "string"
      ? value.detail
      : null;
  return message && message.length <= 300 ? message : "Memory control request failed.";
}

function clientError(status, code, message) {
  const error = new Error(message);
  error.name = "MemoryControlClientError";
  error.status = status;
  error.code = code;
  return error;
}
