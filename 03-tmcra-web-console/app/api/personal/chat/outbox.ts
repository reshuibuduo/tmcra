import { ConsoleError, ensureConsoleSchema } from "@/db/console";
import { getD1 } from "@/db";
import { personalMemoryRequest } from "../export/server";

const ON_BEHALF_SUBJECT_HEADER = "X-TMCRA-On-Behalf-Of-Subject";
const CLIENT_PLATFORM_HEADER = "X-TMCRA-Client-Platform";
const AGENT_ID_HEADER = "X-TMCRA-Agent-ID";

export type ChatProviderReceipt = {
  call_id: string;
  provider: string;
  model: string;
  operation: "chat_answer" | "voice_transcription" | "voice_realtime";
  status: "completed" | "failed" | "unknown";
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  cache_hit_tokens?: number;
  error_code?: string;
  request_sha256: string;
  response_sha256?: string;
  started_at?: number;
  finished_at?: number;
};

type PersonalAccess = {
  space: { id: string };
};

type MemoryBinding = {
  apiKey: string;
  baseUrl: string;
};

type OutboxRow = {
  id: string;
  scopeName: string;
  payloadJson: string;
  attemptCount: number;
};

export async function enqueueProviderReceipt(
  access: PersonalAccess,
  scopeName: string,
  receipt: ChatProviderReceipt,
) {
  await ensureConsoleSchema();
  const now = Date.now();
  await getD1()
    .prepare(
      `INSERT OR IGNORE INTO chat_provider_receipt_outbox(
         id, personal_space_id, scope_name, payload_json, status,
         attempt_count, next_attempt_at, created_at, updated_at
       ) VALUES (?1, ?2, ?3, ?4, 'pending', 0, ?5, ?5, ?5)`,
    )
    .bind(
      receipt.call_id,
      access.space.id,
      scopeName,
      JSON.stringify(receipt),
      now,
    )
    .run();
}

export async function flushProviderReceiptOutbox(
  access: PersonalAccess,
  binding: MemoryBinding,
  requestId: string,
  limit = 5,
) {
  await ensureConsoleSchema();
  const database = getD1();
  const now = Date.now();
  await database
    .prepare(
      `UPDATE chat_provider_receipt_outbox
       SET status = 'pending', next_attempt_at = ?1, updated_at = ?1,
           last_error_code = 'processing_lease_expired'
       WHERE personal_space_id = ?2
         AND status = 'processing'
         AND last_attempt_at IS NOT NULL
         AND last_attempt_at < ?3`,
    )
    .bind(now, access.space.id, now - 5 * 60 * 1000)
    .run();
  const result = await database
    .prepare(
      `SELECT
         id,
         scope_name AS scopeName,
         payload_json AS payloadJson,
         attempt_count AS attemptCount
       FROM chat_provider_receipt_outbox
       WHERE personal_space_id = ?1
         AND status = 'pending'
         AND next_attempt_at <= ?2
       ORDER BY created_at ASC
       LIMIT ?3`,
    )
    .bind(access.space.id, now, Math.max(1, Math.min(20, limit)))
    .all<OutboxRow>();

  let completed = 0;
  let pending = 0;
  let blocked = 0;
  const completedIds: string[] = [];
  const blockedIds: string[] = [];
  for (const row of result.results) {
    const claimed = await database
      .prepare(
        `UPDATE chat_provider_receipt_outbox
         SET status = 'processing', last_attempt_at = ?2, updated_at = ?2
         WHERE id = ?1 AND personal_space_id = ?3 AND status = 'pending'`,
      )
      .bind(row.id, now, access.space.id)
      .run();
    if ((claimed.meta.changes ?? 0) !== 1) continue;

    try {
      const payload = parseReceipt(row.payloadJson, row.id);
      await personalMemoryRequest(
        binding,
        `/v1/scopes/${encodeURIComponent(row.scopeName)}/provider-calls`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            [ON_BEHALF_SUBJECT_HEADER]: access.space.id,
            [CLIENT_PLATFORM_HEADER]: "vercel_ai_sdk",
            [AGENT_ID_HEADER]: "tmcra-chat",
          },
          body: JSON.stringify(payload),
        },
        `${requestId}-usage-${row.id.slice(-12)}`,
      );
      await database
        .prepare(
          `UPDATE chat_provider_receipt_outbox
           SET status = 'completed', attempt_count = attempt_count + 1,
               last_error_code = NULL, completed_at = ?2, updated_at = ?2
           WHERE id = ?1 AND personal_space_id = ?3`,
        )
        .bind(row.id, Date.now(), access.space.id)
        .run();
      completed += 1;
      completedIds.push(row.id);
    } catch (error) {
      const isConflict = error instanceof ConsoleError && error.status === 409;
      const nextStatus = isConflict ? "blocked" : "pending";
      const nextAttemptAt = Date.now() + retryDelayMs(row.attemptCount + 1);
      await database
        .prepare(
          `UPDATE chat_provider_receipt_outbox
           SET status = ?2, attempt_count = attempt_count + 1,
               next_attempt_at = ?3, last_error_code = ?4, updated_at = ?5
           WHERE id = ?1 AND personal_space_id = ?6`,
        )
        .bind(
          row.id,
          nextStatus,
          nextAttemptAt,
          safeErrorCode(error),
          Date.now(),
          access.space.id,
        )
        .run();
      if (isConflict) {
        blocked += 1;
        blockedIds.push(row.id);
      }
      else pending += 1;
    }
  }
  return { completed, pending, blocked, completedIds, blockedIds } as const;
}

function parseReceipt(value: string, expectedCallId: string): ChatProviderReceipt {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new ConsoleError(500, "chat_receipt_corrupt", "Stored usage receipt is invalid.");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new ConsoleError(500, "chat_receipt_corrupt", "Stored usage receipt is invalid.");
  }
  const record = parsed as Record<string, unknown>;
  if (record.call_id !== expectedCallId) {
    throw new ConsoleError(500, "chat_receipt_corrupt", "Stored usage receipt identity is invalid.");
  }
  return parsed as ChatProviderReceipt;
}

function retryDelayMs(attempt: number) {
  return Math.min(60 * 60 * 1000, 5_000 * 2 ** Math.min(8, Math.max(0, attempt - 1)));
}

function safeErrorCode(error: unknown) {
  if (error instanceof ConsoleError && /^[a-z0-9_]{1,100}$/u.test(error.code)) {
    return error.code;
  }
  if (error instanceof Error && /^[A-Za-z][A-Za-z0-9]{0,79}$/u.test(error.name)) {
    return error.name.slice(0, 80);
  }
  return "unknown_error";
}
