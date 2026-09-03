import { env } from "cloudflare:workers";

import {
  createMemoryControlClient,
} from "@/app/api/device/v1/upstream-client.mjs";
import { fetchMemoryApi } from "@/app/lib/memory-api-fetch";
import {
  ConsoleError,
  getPersonalApiKey,
  markPersonalApiKeyRevoked,
  storePersonalApiKey,
} from "@/db/console";

const PERSONAL_API_KEY_PERMISSIONS = [
  "memory:read",
  "memory:write",
  "memory:consolidate",
  "memory:feedback",
] as const;
const PERSONAL_API_KEY_PERMISSION_SET = new Set<string>(PERSONAL_API_KEY_PERMISSIONS);
const DEFAULT_TOKEN_LIFETIME_SECONDS = 365 * 24 * 60 * 60;
const MINIMUM_TOKEN_LIFETIME_SECONDS = 60 * 60;

type PersonalAccess = {
  space: {
    id: string;
    scopeName: string;
  };
};

export async function createPersonalApiKey(
  access: PersonalAccess,
  payload: Record<string, unknown>,
  requestId: string,
) {
  const name = requiredText(payload.name, "name", 80);
  const permissions = requestedPermissions(payload.permissions);
  const expiresInSeconds = requestedLifetime(payload.expiresInSeconds);
  const control = memoryControlClient();
  let response: unknown;
  try {
    response = await control.issue(
      {
        label: `Personal API / ${name}`.slice(0, 120),
        subject: access.space.id,
        permissions,
        scope_names: [],
        scope_prefixes: [`${access.space.scopeName}-`],
        expires_in_seconds: expiresInSeconds,
      },
      requestId,
    );
  } catch (error) {
    throw asControlError(error);
  }

  const issued = validateIssuedToken(response, expiresInSeconds);
  try {
    await storePersonalApiKey({
      personalSpaceId: access.space.id,
      tokenId: issued.tokenId,
      tokenPrefix: issued.tokenPrefix,
      permissions,
      name,
      expiresAt: issued.expiresAt,
      createdAt: issued.createdAt,
    });
  } catch (storageError) {
    try {
      await control.revoke(issued.tokenId, `${requestId}-rollback`);
    } catch {
      throw new ConsoleError(
        502,
        "personal_api_key_rollback_failed",
        "The API key could not be stored or safely rolled back.",
      );
    }
    throw storageError;
  }

  return {
    apiKey: {
      tokenId: issued.tokenId,
      tokenPrefix: issued.tokenPrefix,
      permissions,
      name,
      status: "active" as const,
      expiresAt: issued.expiresAt,
      createdAt: issued.createdAt,
      revokedAt: null,
    },
    secret: issued.accessToken,
  };
}

export async function revokePersonalApiKey(
  access: PersonalAccess,
  payload: Record<string, unknown>,
  requestId: string,
) {
  const tokenId = requiredTokenId(payload.tokenId);
  const key = await getPersonalApiKey(access.space.id, tokenId);
  if (!key) {
    throw new ConsoleError(404, "personal_api_key_not_found", "Personal API key not found.");
  }

  // The control client treats an upstream 404 as a successful revoke. Calling it
  // on every attempt makes a retry safe when upstream succeeded but D1 did not.
  try {
    await memoryControlClient().revoke(tokenId, requestId);
  } catch (error) {
    throw asControlError(error);
  }
  const revokedAt = Date.now();
  await markPersonalApiKeyRevoked(access.space.id, tokenId, revokedAt);
  return {
    apiKey: {
      ...key,
      status: "revoked" as const,
      revokedAt,
    },
  };
}

function memoryControlClient() {
  try {
    return createMemoryControlClient({
      baseUrl:
        env.TMCRA_MEMORY_API_CONTROL_BASE_URL || env.TMCRA_MEMORY_API_BASE_URL,
      controlKey: env.TMCRA_MEMORY_API_CONTROL_KEY,
      allowHttpLoopback:
        env.TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK === "1",
      fetchImpl: (input: RequestInfo | URL, init?: RequestInit) =>
        fetchMemoryApi(env, input, init),
    });
  } catch (error) {
    throw asControlError(error);
  }
}

function requestedPermissions(value: unknown): string[] {
  if (value === undefined) return [...PERSONAL_API_KEY_PERMISSIONS];
  if (!Array.isArray(value) || value.length === 0) {
    throw new ConsoleError(422, "invalid_api_key_permissions", "permissions must be a non-empty array.");
  }
  const permissions = [...new Set(value.map((permission) => {
    if (typeof permission !== "string" || !PERSONAL_API_KEY_PERMISSION_SET.has(permission)) {
      throw new ConsoleError(
        422,
        "invalid_api_key_permissions",
        "Personal API keys only support memory:read, memory:write, memory:consolidate, and memory:feedback.",
      );
    }
    return permission;
  }))];
  return PERSONAL_API_KEY_PERMISSIONS.filter((permission) => permissions.includes(permission));
}

function requestedLifetime(value: unknown): number {
  if (value === undefined) return DEFAULT_TOKEN_LIFETIME_SECONDS;
  const seconds = Number(value);
  if (
    !Number.isInteger(seconds) ||
    seconds < MINIMUM_TOKEN_LIFETIME_SECONDS ||
    seconds > DEFAULT_TOKEN_LIFETIME_SECONDS
  ) {
    throw new ConsoleError(
      422,
      "invalid_api_key_expiry",
      "expiresInSeconds must be between 3600 and 31536000.",
    );
  }
  return seconds;
}

function validateIssuedToken(value: unknown, expiresInSeconds: number) {
  if (!isRecord(value)) {
    throw new ConsoleError(502, "personal_api_key_invalid_response", "Memory API returned an invalid response.");
  }
  const accessToken = requiredUpstreamText(value.access_token, "access_token", 1_024);
  const tokenId = requiredUpstreamText(value.token_id, "token_id", 200);
  if (
    !/^[A-Za-z0-9_-]{1,160}$/.test(tokenId) ||
    !new RegExp(`^tmcra_st_${tokenId}\\.[A-Za-z0-9_-]{20,700}$`).test(accessToken)
  ) {
    throw new ConsoleError(502, "personal_api_key_invalid_response", "Memory API returned an invalid credential.");
  }
  const expiresAtSeconds = Number(value.expires_at);
  const expiresAt = Math.trunc(expiresAtSeconds * 1000);
  const createdAtSeconds = Number(value.created_at);
  const createdAt = Math.trunc(createdAtSeconds * 1000);
  const now = Date.now();
  if (
    !Number.isFinite(createdAtSeconds) ||
    createdAt > now + 5 * 60 * 1000 ||
    createdAt < now - (expiresInSeconds + 5 * 60) * 1000 ||
    !Number.isFinite(expiresAtSeconds) ||
    expiresAt <= createdAt ||
    expiresAt <= now ||
    expiresAt > now + (expiresInSeconds + 300) * 1000
  ) {
    throw new ConsoleError(502, "personal_api_key_invalid_response", "Memory API returned an invalid expiry.");
  }
  return {
    accessToken,
    tokenId,
    tokenPrefix: accessToken.split(".", 1)[0],
    createdAt,
    expiresAt,
  };
}

function requiredTokenId(value: unknown) {
  const tokenId = requiredText(value, "tokenId", 160);
  if (!/^[A-Za-z0-9_-]+$/.test(tokenId)) {
    throw new ConsoleError(422, "invalid_token_id", "tokenId is invalid.");
  }
  return tokenId;
}

function requiredText(value: unknown, field: string, maximum: number) {
  if (typeof value !== "string" || !value.trim() || value.trim().length > maximum) {
    throw new ConsoleError(422, `invalid_${field}`, `${field} is invalid.`);
  }
  return value.trim();
}

function requiredUpstreamText(value: unknown, field: string, maximum: number) {
  if (typeof value !== "string" || !value || value.length > maximum) {
    throw new ConsoleError(
      502,
      "personal_api_key_invalid_response",
      `Memory API omitted ${field}.`,
    );
  }
  return value;
}

function asControlError(error: unknown) {
  if (error instanceof ConsoleError) return error;
  if (isRecord(error)) {
    const status = Number(error.status);
    const code = typeof error.code === "string" ? error.code : "memory_control_unavailable";
    const message = typeof error.message === "string"
      ? error.message
      : "Memory control service is unavailable.";
    return new ConsoleError(
      Number.isInteger(status) && status >= 400 && status <= 599 ? status : 502,
      code.slice(0, 100),
      message.slice(0, 300),
    );
  }
  return new ConsoleError(502, "memory_control_unavailable", "Memory control service is unavailable.");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
