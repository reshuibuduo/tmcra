import { ConsoleError } from "@/db/console";

import {
  PERSONAL_NO_STORE_HEADERS,
  personalErrorResponse,
  personalMemoryBinding,
  personalMemoryJson,
  requirePersonalAccess,
} from "../export/server";

export const dynamic = "force-dynamic";

const SCOPE_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;

export async function GET(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    const access = await requirePersonalAccess();
    const scopeName = new URL(request.url).searchParams.get("scope")?.trim() ?? "";
    if (!SCOPE_RE.test(scopeName) || !scopeName.startsWith(`${access.space.scopeName}-`)) {
      throw new ConsoleError(403, "session_scope_forbidden", "The requested Scope is not part of this account.");
    }
    const summary = await personalMemoryJson(
      personalMemoryBinding(),
      `/v1/scopes/${encodeURIComponent(scopeName)}/summary`,
      requestId,
    );
    return Response.json(
      { ok: true, scopeName, sessions: scopeSessions(summary, scopeName) },
      { headers: PERSONAL_NO_STORE_HEADERS },
    );
  } catch (error) {
    return personalErrorResponse(error, requestId);
  }
}

function scopeSessions(value: unknown, scopeName: string) {
  if (!isRecord(value) || !Array.isArray(value.sessions)) return [];
  return value.sessions.flatMap((item) => {
    if (!isRecord(item) || typeof item.session_id !== "string") return [];
    return [{
      id: item.session_id,
      sessionId: item.session_id,
      displayName: `Session ${item.session_id.slice(0, 8)}`,
      scopeName,
      status: "active",
      createdAt: typeof item.created_at === "number" ? item.created_at : null,
      updatedAt: typeof item.last_ingest_at === "number" ? item.last_ingest_at : null,
    }];
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
