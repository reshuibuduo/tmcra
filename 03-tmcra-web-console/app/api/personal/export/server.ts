import { env } from "cloudflare:workers";

import { getChatGPTUser } from "@/app/chatgpt-auth";
import { normalizeMemoryApiBaseUrl } from "@/app/api/device/v1/upstream-client.mjs";
import { fetchMemoryApi } from "@/app/lib/memory-api-fetch";
import {
  ConsoleError,
  resolvePersonalMemoryAccess,
  type ConsoleIdentity,
} from "@/db/console";

export const PERSONAL_NO_STORE_HEADERS = {
  "Cache-Control": "private, no-store, max-age=0",
  "X-Content-Type-Options": "nosniff",
};

const MAX_JSON_BYTES = 1_000_000;

type MemoryBinding = {
  apiKey: string;
  baseUrl: string;
};

export async function requirePersonalAccess() {
  const user = await getChatGPTUser();
  if (!user) throw new ConsoleError(401, "authentication_required", "Sign in with ChatGPT to continue.");
  const identity: ConsoleIdentity = {
    email: user.email,
    displayName: user.displayName,
    fullName: user.fullName,
  };
  return resolvePersonalMemoryAccess(identity);
}

export function personalMemoryBinding(): MemoryBinding {
  const apiKey = String(env.TMCRA_MEMORY_API_CONTROL_KEY ?? "").trim();
  const baseUrl = String(
    env.TMCRA_MEMORY_API_CONTROL_BASE_URL ?? env.TMCRA_MEMORY_API_BASE_URL ?? "",
  ).trim();
  if (!apiKey || !apiKey.startsWith("tmcra_") || apiKey.length > 1024) {
    throw new ConsoleError(503, "personal_memory_not_configured", "Personal memory service is not configured.");
  }
  try {
    return {
      apiKey,
      baseUrl: normalizeMemoryApiBaseUrl(baseUrl, {
        allowHttpLoopback:
          env.TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK === "1",
      }),
    };
  } catch {
    throw new ConsoleError(503, "personal_memory_configuration_invalid", "Personal memory endpoint is invalid.");
  }
}

export async function personalMemoryJson(
  binding: MemoryBinding,
  pathAndQuery: string,
  requestId: string,
) {
  return personalMemoryRequest(binding, pathAndQuery, { method: "GET" }, requestId);
}

export async function personalMemoryRequest(
  binding: MemoryBinding,
  pathAndQuery: string,
  init: RequestInit,
  requestId: string,
) {
  const response = await personalMemoryFetch(binding, pathAndQuery, requestId, init);
  const text = await readBoundedText(response, MAX_JSON_BYTES);
  let value: unknown;
  try {
    value = text ? JSON.parse(text) : null;
  } catch {
    throw new ConsoleError(502, "personal_memory_invalid_response", "Personal memory service returned invalid JSON.");
  }
  if (!response.ok) {
    const publicError = upstreamError(value);
    throw new ConsoleError(
      [400, 401, 403, 404, 409, 410, 413, 415, 422, 429, 503, 504].includes(response.status) ? response.status : 502,
      publicError.code,
      publicError.message,
    );
  }
  return value;
}

export async function personalMemoryFetch(
  binding: MemoryBinding,
  pathAndQuery: string,
  requestId: string,
  init: RequestInit = { method: "GET" },
  timeoutMs = 30_000,
) {
  const url = new URL(pathAndQuery, `${binding.baseUrl}/`);
  if (url.origin !== new URL(binding.baseUrl).origin) {
    throw new ConsoleError(500, "personal_memory_path_invalid", "Personal memory request path is invalid.");
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetchMemoryApi(env, url, {
      ...init,
      headers: {
        Authorization: `Bearer ${binding.apiKey}`,
        Accept: "application/json, application/zip",
        "X-Request-ID": requestId,
        ...init.headers,
      },
      redirect: "error",
      signal: controller.signal,
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new ConsoleError(504, "personal_memory_timeout", "Personal memory service timed out.");
    }
    throw new ConsoleError(502, "personal_memory_unavailable", "Personal memory service is unavailable.");
  } finally {
    clearTimeout(timeout);
  }
}

function upstreamError(value: unknown) {
  const fallback = {
    code: "personal_memory_request_failed",
    message: "Personal memory request failed.",
  };
  if (!value || typeof value !== "object" || Array.isArray(value)) return fallback;
  const top = value as Record<string, unknown>;
  const source = top.error && typeof top.error === "object" && !Array.isArray(top.error)
    ? top.error as Record<string, unknown>
    : top.detail && typeof top.detail === "object" && !Array.isArray(top.detail)
      ? top.detail as Record<string, unknown>
      : top;
  return {
    code: typeof source.code === "string" && /^[a-z0-9_]{1,100}$/u.test(source.code)
      ? source.code
      : fallback.code,
    message: typeof source.message === "string" && source.message.length <= 300
      ? source.message
      : typeof top.detail === "string" && top.detail.length <= 300
        ? top.detail
        : fallback.message,
  };
}

export function personalErrorResponse(error: unknown, requestId: string) {
  if (error instanceof ConsoleError) {
    return Response.json(
      { ok: false, error: { code: error.code, message: error.message, requestId } },
      { status: error.status, headers: PERSONAL_NO_STORE_HEADERS },
    );
  }
  console.error("TMCRA personal memory BFF request failed", {
    requestId,
    error: error instanceof Error ? error.name : "UnknownError",
  });
  return Response.json(
    { ok: false, error: { code: "internal_error", message: "Personal memory request failed.", requestId } },
    { status: 500, headers: PERSONAL_NO_STORE_HEADERS },
  );
}

async function readBoundedText(response: Response, maximumBytes: number) {
  const announced = Number(response.headers.get("content-length") ?? "0");
  if (Number.isFinite(announced) && announced > maximumBytes) {
    throw new ConsoleError(502, "personal_memory_response_too_large", "Personal memory response is too large.");
  }
  const text = await response.text();
  if (new TextEncoder().encode(text).byteLength > maximumBytes) {
    throw new ConsoleError(502, "personal_memory_response_too_large", "Personal memory response is too large.");
  }
  return text;
}
