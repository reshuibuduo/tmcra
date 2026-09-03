import { env } from "cloudflare:workers";

import { getD1 } from "@/db";
import { ConsoleError, ensureConsoleSchema } from "@/db/console";
import { fetchMemoryApi } from "@/app/lib/memory-api-fetch";
import {
  createMemoryControlClient,
  normalizeMemoryApiBaseUrl,
} from "./upstream-client.mjs";

const DEVICE_LIFETIME_MS = 10 * 60_000;
const INITIAL_POLL_INTERVAL_SECONDS = 5;
const TOKEN_LIFETIME_SECONDS = 365 * 24 * 60 * 60;
const RATE_WINDOW_MS = 10 * 60_000;
const START_SOURCE_RATE_LIMIT = 20;
const START_GLOBAL_RATE_LIMIT = 2_000;
const START_SOURCE_LIVE_LIMIT = 5;
const START_GLOBAL_LIVE_LIMIT = 5_000;
const INVALID_CODE_ACCOUNT_LIMIT = 10;
const INVALID_CODE_SOURCE_LIMIT = 30;
const TERMINAL_RETENTION_MS = 24 * 60 * 60_000;
const OUTBOX_RETENTION_MS = 30 * 24 * 60 * 60_000;
const CLEANUP_LIMIT = 100;
const OUTBOX_LEASE_MS = 2 * 60_000;
const TOKEN_DELIVERY_WINDOW_MS = 5 * 60_000;
const PROVISIONAL_TOKEN_SECONDS = 10 * 60;
const USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const PKCE_CHALLENGE_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const PKCE_VERIFIER_PATTERN = /^[A-Za-z0-9._~-]{43,128}$/;
const USER_CODE_PATTERN = /^[A-HJ-NP-Z2-9]{8}$/;
const DEVICE_CODE_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const TOKEN_PERMISSIONS = [
  "memory:read",
  "memory:write",
  "memory:consolidate",
  "memory:feedback",
] as const;

type DeviceProvider = "codex" | "deepseek_harness";

const DEVICE_CLIENTS = {
  "tmcra-codex": {
    provider: "codex",
    defaultName: "Codex",
    tokenLabel: "Codex",
    verificationPath: "/console/connect/codex",
  },
  "tmcra-deepseek-harness": {
    provider: "deepseek_harness",
    defaultName: "DeepSeek Harness",
    tokenLabel: "DeepSeek Harness",
    verificationPath: "/console/connect/deepseek-harness",
  },
} as const satisfies Record<string, {
  provider: DeviceProvider;
  defaultName: string;
  tokenLabel: string;
  verificationPath: string;
}>;

type PersonalAccess = {
  actor: { id: string; displayName: string; email: string };
  space: {
    id: string;
    scopeName: string;
    displayName: string;
    status: string;
  };
};

type DeviceAuthorizationRow = {
  id: string;
  deviceCodeHash: string;
  userCodeHash: string;
  codeChallenge: string;
  provider: DeviceProvider;
  clientName: string;
  sourceHash: string;
  status:
    | "pending"
    | "authorizing"
    | "approved"
    | "denied"
    | "claimed"
    | "expired";
  intervalSeconds: number;
  pollCount: number;
  lastPolledAt: number | null;
  approvedByUserId: string | null;
  personalSpaceId: string | null;
  tokenCiphertext: string | null;
  tokenIv: string | null;
  createdAt: number;
  updatedAt: number;
  expiresAt: number;
  approvedAt: number | null;
  claimedAt: number | null;
  issuanceRequestId: string | null;
  deliveryReceiptHash: string | null;
};

type DeviceConnectionRow = {
  id: string;
  authorizationId: string;
  userId: string;
  personalSpaceId: string;
  provider: DeviceProvider;
  displayName: string;
  tokenId: string;
  tokenPrefix: string;
  scopePrefix: string;
  permissionsJson: string;
  status: "active" | "revoked" | "expired";
  tokenExpiresAt: number;
  createdAt: number;
  updatedAt: number;
  lastConnectedAt: number | null;
  revokedAt: number | null;
  revocationStatus?: "pending" | "processing" | "completed" | null;
};

type IssuedToken = {
  accessToken: string;
  tokenId: string;
  tokenPrefix: string;
  expiresAt: number;
};

type RevocationOutboxRow = {
  id: string;
  tokenId: string;
  connectionId: string | null;
  attemptCount: number;
};

type RevocationOutboxHealthRow = {
  pending: number | string;
  processing: number | string;
  due: number | string;
  oldestPendingAt: number | string | null;
};

export class DeviceFlowError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly interval?: number,
  ) {
    super(message);
    this.name = "DeviceFlowError";
  }
}

export async function startDeviceAuthorization(input: {
  clientId: unknown;
  codeChallenge: unknown;
  codeChallengeMethod: unknown;
  clientName?: unknown;
  requestOrigin: string;
  requestSource: string;
}) {
  const client = typeof input.clientId === "string"
    ? DEVICE_CLIENTS[input.clientId as keyof typeof DEVICE_CLIENTS]
    : undefined;
  if (!client) {
    throw new DeviceFlowError(400, "invalid_client", "Device client is not supported.");
  }
  if (input.codeChallengeMethod !== "S256") {
    throw new DeviceFlowError(
      400,
      "invalid_code_challenge_method",
      "Only PKCE S256 is supported.",
    );
  }
  const codeChallenge = requiredPkceChallenge(input.codeChallenge);
  const clientName = cleanClientName(input.clientName, client.defaultName);
  const database = getD1();
  await ensureConsoleSchema(database);
  const sourceHash = await sourceFingerprint(input.requestSource);
  await cleanupDeviceState(database);

  for (let attempt = 0; attempt < 4; attempt += 1) {
    const id = newId("dva");
    const deviceCode = randomBase64Url(32);
    const userCode = randomUserCode();
    const now = Date.now();
    const bucketStart = Math.floor(now / RATE_WINDOW_MS) * RATE_WINDOW_MS;
    const admissionId = newId("adm");
    const sourceRateKey = `start:source:${sourceHash}`;
    const globalRateKey = "start:global";
    try {
      const [sourceAdmission, globalAdmission, inserted] = await database.batch([
        rateAdmissionStatement(
          database,
          sourceRateKey,
          bucketStart,
          START_SOURCE_RATE_LIMIT,
          admissionId,
          now,
        ),
        rateAdmissionStatement(
          database,
          globalRateKey,
          bucketStart,
          START_GLOBAL_RATE_LIMIT,
          admissionId,
          now,
        ),
        database.prepare(
           `INSERT INTO device_authorizations (
             id, device_code_hash, user_code_hash, code_challenge,
             code_challenge_method, provider, client_name, source_hash, status,
             interval_seconds, poll_count, created_at, updated_at, expires_at
           ) SELECT ?1, ?2, ?3, ?4, 'S256', ?5, ?6, ?7, 'pending', ?8, 0, ?9, ?9, ?10
           WHERE EXISTS (
             SELECT 1 FROM device_flow_rate_limits
             WHERE limit_key = ?11 AND bucket_start = ?12 AND last_admission_id = ?13
           )
             AND EXISTS (
               SELECT 1 FROM device_flow_rate_limits
               WHERE limit_key = ?14 AND bucket_start = ?12 AND last_admission_id = ?13
             )
             AND (SELECT COUNT(*) FROM device_authorizations
                  WHERE expires_at > ?9 AND status IN ('pending', 'approved', 'authorizing'))
                 < ?15
             AND (SELECT COUNT(*) FROM device_authorizations
                  WHERE source_hash = ?7 AND expires_at > ?9
                    AND status IN ('pending', 'approved', 'authorizing'))
                 < ?16`,
         )
         .bind(
           id,
           await sha256Base64Url(deviceCode),
           await sha256Base64Url(userCode),
           codeChallenge,
           client.provider,
           clientName,
           sourceHash,
           INITIAL_POLL_INTERVAL_SECONDS,
          now,
          now + DEVICE_LIFETIME_MS,
          sourceRateKey,
          bucketStart,
          admissionId,
          globalRateKey,
          START_GLOBAL_LIVE_LIMIT,
          START_SOURCE_LIVE_LIMIT,
        ),
      ]);
      if (changes(inserted) !== 1) {
        const admitted =
          changes(sourceAdmission) === 1 && changes(globalAdmission) === 1;
        throw new DeviceFlowError(
          429,
          admitted ? "authorization_capacity_reached" : "rate_limited",
          admitted
            ? "Too many unexpired device authorizations are already open."
            : "Too many device authorization requests.",
          60,
        );
      }
      const verificationUri = `${input.requestOrigin}${client.verificationPath}`;
      return {
        provider: client.provider,
        deviceCode,
        userCode,
        verificationUri,
        verificationUriComplete: `${verificationUri}?user_code=${encodeURIComponent(userCode)}`,
        expiresIn: DEVICE_LIFETIME_MS / 1000,
        interval: INITIAL_POLL_INTERVAL_SECONDS,
      };
    } catch (error) {
      if (error instanceof DeviceFlowError) throw error;
      if (!databaseMessage(error).includes("UNIQUE constraint failed") || attempt === 3) {
        throw error;
      }
    }
  }
  throw new DeviceFlowError(
    503,
    "authorization_unavailable",
    "A device authorization could not be created.",
  );
}

export async function pollDeviceAuthorization(input: {
  deviceCode: unknown;
  codeVerifier: unknown;
  deliveryReceipt?: unknown;
}) {
  const deviceCode = requiredPattern(
    input.deviceCode,
    DEVICE_CODE_PATTERN,
    "device_code",
    "invalid_device_code",
  );
  const codeVerifier = requiredPattern(
    input.codeVerifier,
    PKCE_VERIFIER_PATTERN,
    "code_verifier",
    "invalid_grant",
  );
  const database = getD1();
  await ensureConsoleSchema(database);
  await cleanupDeviceState(database);
  const row = await findAuthorizationByDeviceHash(
    database,
    await sha256Base64Url(deviceCode),
  );
  if (!row) {
    throw new DeviceFlowError(400, "invalid_device_code", "Device code is invalid.");
  }

  const presentedChallenge = await sha256Base64Url(codeVerifier);
  if (!constantTimeEqual(presentedChallenge, row.codeChallenge)) {
    throw new DeviceFlowError(400, "invalid_grant", "PKCE verification failed.");
  }

  if (input.deliveryReceipt !== undefined) {
    return acknowledgeTokenDelivery(database, row, input.deliveryReceipt);
  }

  if (row.status === "claimed") {
    throw new DeviceFlowError(400, "invalid_grant", "Device authorization was already used.");
  }
  if (row.tokenCiphertext && row.tokenIv && row.deliveryReceiptHash) {
    return retrieveDeliverableToken(database, row);
  }

  const now = Date.now();
  if (row.expiresAt <= now || row.status === "expired") {
    await expireAuthorization(database, row);
    throw new DeviceFlowError(400, "expired_token", "Device authorization expired.");
  }

  const cadence = await database
    .prepare(
      `UPDATE device_authorizations
       SET last_polled_at = ?1, poll_count = poll_count + 1, updated_at = ?1
       WHERE id = ?2
         AND (last_polled_at IS NULL OR last_polled_at <= ?1 - (interval_seconds * 1000))`,
    )
    .bind(now, row.id)
    .run();
  if (changes(cadence) !== 1) {
    await database
      .prepare(
        `UPDATE device_authorizations
         SET interval_seconds = MIN(interval_seconds + 5, 60),
             last_polled_at = ?1, poll_count = poll_count + 1, updated_at = ?1
         WHERE id = ?2`,
      )
      .bind(now, row.id)
      .run();
    throw new DeviceFlowError(
      400,
      "slow_down",
      "Polling too quickly.",
      Math.min(Number(row.intervalSeconds) + 5, 60),
    );
  }

  if (row.status === "pending" || row.status === "authorizing") {
    throw new DeviceFlowError(
      400,
      "authorization_pending",
      "Authorization is still pending.",
      Number(row.intervalSeconds),
    );
  }
  if (row.status === "denied") {
    throw new DeviceFlowError(400, "access_denied", "Authorization was denied.");
  }
  if (row.status !== "approved" || !row.personalSpaceId || !row.approvedByUserId) {
    throw new DeviceFlowError(409, "authorization_state_invalid", "Authorization is unavailable.");
  }
  return issueTokenOnFirstPoll(database, row);
}

export async function getDeviceApproval(
  access: PersonalAccess,
  userCodeInput: unknown,
  requestSource: string,
) {
  const database = getD1();
  await ensureConsoleSchema(database);
  const { row, userCode } = await findAuthorizationForAccount(
    database,
    access,
    userCodeInput,
    requestSource,
  );
  if (row.expiresAt <= Date.now() || row.status === "expired") {
    await expireAuthorization(database, row);
    throw new ConsoleError(410, "authorization_expired", "Authorization code expired.");
  }
  assertAuthorizationOwner(row, access);
  return {
    userCode,
    provider: row.provider,
    clientName: row.clientName,
    status: row.status,
    expiresAt: row.expiresAt,
    connection: sanitizeConnection(
      await findConnectionByAuthorization(database, row.id),
    ),
  };
}

export async function approveDeviceAuthorization(
  access: PersonalAccess,
  userCodeInput: unknown,
  requestSource: string,
) {
  const database = getD1();
  await ensureConsoleSchema(database);
  let { row } = await findAuthorizationForAccount(
    database,
    access,
    userCodeInput,
    requestSource,
  );
  if (row.expiresAt <= Date.now() || row.status === "expired") {
    await expireAuthorization(database, row);
    throw new ConsoleError(410, "authorization_expired", "Authorization code expired.");
  }
  assertAuthorizationOwner(row, access);
  if (row.status === "approved" || row.status === "claimed") {
    return {
      status: row.status,
      connection: sanitizeConnection(
        await findConnectionByAuthorization(database, row.id),
      ),
    };
  }
  if (row.status === "denied") {
    throw new ConsoleError(409, "authorization_denied", "Authorization was denied.");
  }
  if (row.status === "authorizing") {
    throw new ConsoleError(409, "authorization_in_progress", "Authorization is in progress.");
  }

  const now = Date.now();
  const approved = await database
    .prepare(
      `UPDATE device_authorizations
       SET status = 'approved', approved_by_user_id = ?1,
           personal_space_id = ?2, approved_at = ?3, updated_at = ?3
       WHERE id = ?4 AND status = 'pending' AND expires_at > ?3`,
    )
    .bind(access.actor.id, access.space.id, now, row.id)
    .run();
  if (changes(approved) !== 1) {
    row = (await findAuthorizationByDeviceHash(
      database,
      row.deviceCodeHash,
    )) as DeviceAuthorizationRow;
    throw new ConsoleError(
      409,
      row?.status === "approved" ? "authorization_already_approved" : "authorization_state_changed",
      "Authorization state changed. Refresh and try again.",
    );
  }
  return { status: "approved" as const, connection: null };
}

export async function denyDeviceAuthorization(
  access: PersonalAccess,
  userCodeInput: unknown,
  requestSource: string,
) {
  const database = getD1();
  await ensureConsoleSchema(database);
  const { row } = await findAuthorizationForAccount(
    database,
    access,
    userCodeInput,
    requestSource,
  );
  if (row.expiresAt <= Date.now()) {
    await expireAuthorization(database, row);
    throw new ConsoleError(410, "authorization_expired", "Authorization code expired.");
  }
  assertAuthorizationOwner(row, access);
  if (row.status === "denied") return { status: "denied" as const };
  if (row.status !== "pending") {
    throw new ConsoleError(409, "authorization_state_changed", "Authorization can no longer be denied.");
  }
  const now = Date.now();
  const result = await database
    .prepare(
      `UPDATE device_authorizations
       SET status = 'denied', approved_by_user_id = ?1,
           personal_space_id = ?2, updated_at = ?3
       WHERE id = ?4 AND status = 'pending' AND expires_at > ?3`,
    )
    .bind(access.actor.id, access.space.id, now, row.id)
    .run();
  if (changes(result) !== 1) {
    throw new ConsoleError(409, "authorization_state_changed", "Authorization state changed.");
  }
  return { status: "denied" as const };
}

export async function listDeviceConnections(access: PersonalAccess) {
  const database = getD1();
  await ensureConsoleSchema(database);
  await expireNaturalConnections(database, access);
  await drainRevocationOutbox(database, crypto.randomUUID(), 1);
  const result = await database
    .prepare(
      `SELECT
         id,
         authorization_id AS authorizationId,
         user_id AS userId,
         personal_space_id AS personalSpaceId,
         provider,
         display_name AS displayName,
         token_id AS tokenId,
         token_prefix AS tokenPrefix,
         scope_prefix AS scopePrefix,
         permissions_json AS permissionsJson,
         status,
         token_expires_at AS tokenExpiresAt,
         created_at AS createdAt,
         updated_at AS updatedAt,
         last_connected_at AS lastConnectedAt,
         revoked_at AS revokedAt,
         (SELECT status FROM device_revocation_outbox
          WHERE token_id = device_connections.token_id LIMIT 1) AS revocationStatus
       FROM device_connections
       WHERE user_id = ?1 AND personal_space_id = ?2
       ORDER BY created_at DESC
       LIMIT 100`,
    )
    .bind(access.actor.id, access.space.id)
    .all<DeviceConnectionRow>();
  return result.results.map((row) => sanitizeConnection(row));
}

export async function revokeDeviceConnection(
  access: PersonalAccess,
  connectionIdInput: unknown,
  requestId: string,
) {
  const connectionId = requiredPattern(
    connectionIdInput,
    /^dvc_[a-f0-9]{32}$/,
    "connection_id",
    "invalid_connection_id",
  );
  const database = getD1();
  await ensureConsoleSchema(database);
  const connection = await database
    .prepare(
      `SELECT
         id,
         authorization_id AS authorizationId,
         user_id AS userId,
         personal_space_id AS personalSpaceId,
         provider,
         display_name AS displayName,
         token_id AS tokenId,
         token_prefix AS tokenPrefix,
         scope_prefix AS scopePrefix,
         permissions_json AS permissionsJson,
         status,
         token_expires_at AS tokenExpiresAt,
         created_at AS createdAt,
         updated_at AS updatedAt,
         last_connected_at AS lastConnectedAt,
         revoked_at AS revokedAt
       FROM device_connections
       WHERE id = ?1 AND user_id = ?2 AND personal_space_id = ?3
       LIMIT 1`,
    )
    .bind(connectionId, access.actor.id, access.space.id)
    .first<DeviceConnectionRow>();
  if (!connection) {
    throw new ConsoleError(404, "connection_not_found", "Connection was not found.");
  }
  if (connection.status === "revoked") {
    await drainRevocationOutbox(database, requestId, 1, connection.tokenId);
    return sanitizeConnection(await findConnectionByAuthorization(database, connection.authorizationId));
  }
  const now = Date.now();
  await database.batch([
    database
      .prepare(
        `UPDATE device_connections
         SET status = 'revoked', revoked_at = ?1, updated_at = ?1
         WHERE id = ?2 AND status <> 'revoked'`,
      )
      .bind(now, connection.id),
    database
      .prepare(
        `UPDATE device_authorizations
         SET status = CASE
               WHEN status IN ('approved', 'authorizing') THEN 'expired'
               ELSE status
             END,
             token_ciphertext = NULL, token_iv = NULL,
             issuance_request_id = CASE
               WHEN status IN ('approved', 'authorizing') THEN NULL
               ELSE issuance_request_id
             END,
             updated_at = ?1
         WHERE id = ?2`,
      )
      .bind(now, connection.authorizationId),
    revocationOutboxStatement(
      database,
      connection.tokenId,
      connection.id,
      "user_revoked",
      now,
    ),
  ]);
  await drainRevocationOutbox(database, requestId, 1, connection.tokenId);
  return sanitizeConnection(await findConnectionByAuthorization(database, connection.authorizationId));
}

export async function runDeviceMaintenance(requestId: string) {
  const database = getD1();
  await ensureConsoleSchema(database);
  const cleanupAttempts = await cleanupDeviceState(database);
  const drainAttempts = await drainRevocationOutbox(database, requestId, 10);
  const now = Date.now();
  const health = await database
    .prepare(
      `SELECT
         SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
         SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END) AS processing,
         SUM(CASE WHEN status = 'pending' AND next_attempt_at <= ?1 THEN 1 ELSE 0 END) AS due,
         MIN(CASE WHEN status = 'pending' THEN created_at ELSE NULL END) AS oldestPendingAt
       FROM device_revocation_outbox
       WHERE status IN ('pending', 'processing')`,
    )
    .bind(now)
    .first<RevocationOutboxHealthRow>();
  return {
    attempted: cleanupAttempts + drainAttempts,
    pending: Number(health?.pending ?? 0),
    processing: Number(health?.processing ?? 0),
    due: Number(health?.due ?? 0),
    oldestPendingAt: health?.oldestPendingAt == null
      ? null
      : Number(health.oldestPendingAt),
  };
}

async function issueTokenOnFirstPoll(
  database: D1Database,
  row: DeviceAuthorizationRow,
) {
  const now = Date.now();
  const idempotencyKey = `device-issue-${row.id}`;
  const issuanceLeaseId = newId("isl");
  const locked = await database
    .prepare(
      `UPDATE device_authorizations
       SET status = 'authorizing', issuance_request_id = ?1, updated_at = ?2
       WHERE id = ?3 AND status = 'approved' AND expires_at > ?2
         AND token_ciphertext IS NULL`,
    )
    .bind(issuanceLeaseId, now, row.id)
    .run();
  if (changes(locked) !== 1) {
    const current = await findAuthorizationByDeviceHash(database, row.deviceCodeHash);
    if (current?.tokenCiphertext && current.tokenIv && current.deliveryReceiptHash) {
      return retrieveDeliverableToken(database, current);
    }
    throw new DeviceFlowError(
      400,
      "authorization_pending",
      "Token issuance is already in progress.",
      Number(row.intervalSeconds),
    );
  }

  const access = await authorizationPersonalAccess(database, row.id);
  if (!access) {
    await resetIssuance(database, row.id, issuanceLeaseId);
    throw new DeviceFlowError(409, "authorization_state_invalid", "Authorization owner is unavailable.");
  }

  let issued: IssuedToken | null = null;
  try {
    issued = await issueProductionToken(access, row.provider, row.clientName, idempotencyKey);
    const completedAt = Date.now();
    const authorizedAt = Number(row.approvedAt) || completedAt;
    const connectionId = newId("dvc");
    const scopePrefix = `${access.space.scopeName}-`;
    const deliveryReceipt = randomBase64Url(32);
    const encrypted = await encryptTokenDelivery(
      row.id,
      JSON.stringify({ accessToken: issued.accessToken, deliveryReceipt }),
    );
    const deliveryReceiptHash = await sha256Base64Url(deliveryReceipt);
    const [authorizationWrite, connectionWrite] = await database.batch([
      database
        .prepare(
          `UPDATE device_authorizations
           SET status = 'approved', token_ciphertext = ?1, token_iv = ?2,
               delivery_receipt_hash = ?3, updated_at = ?4
           WHERE id = ?5 AND status = 'authorizing'
             AND issuance_request_id = ?6 AND expires_at > ?4`,
        )
        .bind(
          encrypted.ciphertext,
          encrypted.iv,
          deliveryReceiptHash,
          completedAt,
          row.id,
          issuanceLeaseId,
        ),
      database
        .prepare(
          `INSERT INTO device_connections (
             id, authorization_id, user_id, personal_space_id, provider,
             display_name, token_id, token_prefix, scope_prefix,
             permissions_json, status, token_expires_at, created_at, updated_at,
             last_connected_at
           ) SELECT
             ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10,
             'active', ?11, ?12, ?13, ?13
           WHERE EXISTS (
             SELECT 1 FROM device_authorizations
             WHERE id = ?2 AND status = 'approved'
               AND issuance_request_id = ?14 AND updated_at = ?13
               AND token_ciphertext IS NOT NULL
           )`,
        )
        .bind(
          connectionId,
          row.id,
           access.actor.id,
           access.space.id,
           row.provider,
           row.clientName,
          issued.tokenId,
          issued.tokenPrefix,
          scopePrefix,
          JSON.stringify(TOKEN_PERMISSIONS),
          issued.expiresAt,
          authorizedAt,
          completedAt,
          issuanceLeaseId,
        ),
    ]);
    if (changes(authorizationWrite) !== 1 || changes(connectionWrite) !== 1) {
      throw new DeviceFlowError(
        409,
        "token_delivery_failed",
        "Authorization expired before the Token could be delivered.",
      );
    }
    return deliveryResponse(
      issued.accessToken,
      deliveryReceipt,
      issued.expiresAt,
      access.space.scopeName,
    );
  } catch (error) {
    const current = await findAuthorizationByDeviceHash(database, row.deviceCodeHash);
    if (current?.tokenCiphertext && current.tokenIv && current.deliveryReceiptHash) {
      return retrieveDeliverableToken(database, current);
    }
    await resetIssuance(database, row.id, issuanceLeaseId);
    throw error;
  }
}

async function retrieveDeliverableToken(
  database: D1Database,
  row: DeviceAuthorizationRow,
) {
  if (
    row.status !== "approved" ||
    !row.tokenCiphertext ||
    !row.tokenIv ||
    !row.deliveryReceiptHash ||
    row.updatedAt + TOKEN_DELIVERY_WINDOW_MS <= Date.now()
  ) {
    await expireAuthorization(database, row);
    throw new DeviceFlowError(400, "expired_token", "Token delivery window expired.");
  }
  const connection = await findConnectionByAuthorization(database, row.id);
  if (!connection || connection.status !== "active") {
    await expireAuthorization(database, row);
    throw new DeviceFlowError(409, "authorization_state_invalid", "Token delivery is unavailable.");
  }
  let delivery: { accessToken?: unknown; deliveryReceipt?: unknown };
  try {
    delivery = JSON.parse(
      await decryptTokenDelivery(row.id, row.tokenCiphertext, row.tokenIv),
    );
  } catch {
    await expireAuthorization(database, row);
    throw new DeviceFlowError(409, "token_delivery_failed", "Token delivery could not be recovered.");
  }
  const accessToken = requiredUpstreamString(delivery.accessToken, "access_token", 1_024);
  const deliveryReceipt = requiredUpstreamString(
    delivery.deliveryReceipt,
    "delivery_receipt",
    200,
  );
  if (
    !/^[A-Za-z0-9_-]{43}$/.test(deliveryReceipt) ||
    !constantTimeEqual(await sha256Base64Url(deliveryReceipt), row.deliveryReceiptHash)
  ) {
    await expireAuthorization(database, row);
    throw new DeviceFlowError(409, "token_delivery_failed", "Token delivery could not be verified.");
  }
  return deliveryResponse(
    accessToken,
    deliveryReceipt,
    connection.tokenExpiresAt,
    connection.scopePrefix.replace(/-$/, ""),
  );
}

async function acknowledgeTokenDelivery(
  database: D1Database,
  row: DeviceAuthorizationRow,
  receiptInput: unknown,
) {
  const deliveryReceipt = requiredPattern(
    receiptInput,
    /^[A-Za-z0-9_-]{43}$/,
    "delivery_receipt",
    "invalid_grant",
  );
  if (
    !row.deliveryReceiptHash ||
    !constantTimeEqual(await sha256Base64Url(deliveryReceipt), row.deliveryReceiptHash)
  ) {
    throw new DeviceFlowError(400, "invalid_grant", "Token delivery receipt is invalid.");
  }
  const connection = await findConnectionByAuthorization(database, row.id);
  if (!connection) {
    throw new DeviceFlowError(409, "authorization_state_invalid", "Token delivery is unavailable.");
  }
  if (connection.status !== "active" || connection.tokenExpiresAt <= Date.now()) {
    throw new DeviceFlowError(400, "invalid_grant", "The device connection is no longer active.");
  }
  if (row.status === "claimed") {
    return deliveryClaimResponse(connection.tokenExpiresAt);
  }
  if (
    row.status !== "approved" ||
    !row.tokenCiphertext ||
    !row.tokenIv ||
    row.updatedAt + TOKEN_DELIVERY_WINDOW_MS <= Date.now()
  ) {
    await expireAuthorization(database, row);
    throw new DeviceFlowError(400, "expired_token", "Token delivery window expired.");
  }

  const confirmed = await confirmProductionToken(
    connection.tokenId,
    `device-confirm-${row.id}`,
  );
  const completedAt = Date.now();
  const [authorizationWrite, connectionWrite] = await database.batch([
    database
      .prepare(
        `UPDATE device_authorizations
         SET status = 'claimed', token_ciphertext = NULL, token_iv = NULL,
             claimed_at = ?1, updated_at = ?1
         WHERE id = ?2 AND status = 'approved'
           AND delivery_receipt_hash = ?3 AND token_ciphertext IS NOT NULL
           AND EXISTS (
             SELECT 1 FROM device_connections c
             WHERE c.authorization_id = ?2 AND c.token_id = ?4
               AND c.status = 'active'
           )`,
      )
      .bind(completedAt, row.id, row.deliveryReceiptHash, connection.tokenId),
    database
      .prepare(
        `UPDATE device_connections
         SET token_expires_at = ?1, last_connected_at = ?2, updated_at = ?2
         WHERE authorization_id = ?3 AND token_id = ?4 AND status = 'active'`,
      )
      .bind(confirmed.expiresAt, completedAt, row.id, connection.tokenId),
  ]);
  if (changes(authorizationWrite) !== 1 || changes(connectionWrite) !== 1) {
      const current = await findAuthorizationByDeviceHash(database, row.deviceCodeHash);
      if (current?.status === "claimed" && current.deliveryReceiptHash === row.deliveryReceiptHash) {
        const currentConnection = await findConnectionByAuthorization(database, row.id);
        if (
          currentConnection?.status === "active" &&
          currentConnection.tokenExpiresAt > Date.now()
        ) {
          return deliveryClaimResponse(currentConnection.tokenExpiresAt);
        }
      }
    throw new DeviceFlowError(409, "token_delivery_failed", "Token delivery could not be confirmed.");
  }
  return deliveryClaimResponse(confirmed.expiresAt);
}

function deliveryResponse(
  accessToken: string,
  deliveryReceipt: string,
  expiresAt: number,
  scopeNamespace: string,
) {
  return {
    accessToken,
    deliveryReceipt,
    tokenType: "Bearer",
    expiresIn: Math.max(0, Math.floor((expiresAt - Date.now()) / 1000)),
    // Codex must receive the public HTTPS endpoint. The server may use a
    // separate loopback control endpoint on single-host GPUHome deployments.
    baseUrl: publicMemoryApiBaseUrl(),
    scopeNamespace,
    deliveryAcknowledgementRequired: true,
  };
}

function deliveryClaimResponse(expiresAt: number) {
  return {
    claimed: true,
    expiresIn: Math.max(0, Math.floor((expiresAt - Date.now()) / 1000)),
  };
}

async function authorizationPersonalAccess(
  database: D1Database,
  authorizationId: string,
) {
  return database
    .prepare(
      `SELECT
         u.id AS actorId, u.display_name AS actorDisplayName,
         u.email_display AS actorEmail, p.id AS spaceId,
         p.scope_name AS scopeName, p.display_name AS spaceDisplayName,
         p.status AS spaceStatus
       FROM device_authorizations a
       JOIN users u ON u.id = a.approved_by_user_id
       JOIN personal_memory_spaces p ON p.id = a.personal_space_id
       WHERE a.id = ?1 AND a.status = 'authorizing'
         AND p.user_id = u.id AND p.status = 'active'
       LIMIT 1`,
    )
    .bind(authorizationId)
    .first<{
      actorId: string;
      actorDisplayName: string;
      actorEmail: string;
      spaceId: string;
      scopeName: string;
      spaceDisplayName: string;
      spaceStatus: string;
    }>()
    .then((value) => value ? ({
      actor: {
        id: value.actorId,
        displayName: value.actorDisplayName,
        email: value.actorEmail,
      },
      space: {
        id: value.spaceId,
        scopeName: value.scopeName,
        displayName: value.spaceDisplayName,
        status: value.spaceStatus,
      },
    } satisfies PersonalAccess) : null);
}

async function resetIssuance(
  database: D1Database,
  authorizationId: string,
  issuanceLeaseId: string,
) {
  const now = Date.now();
  await database
    .prepare(
      `UPDATE device_authorizations
       SET status = CASE WHEN expires_at > ?1 THEN 'approved' ELSE 'expired' END,
           issuance_request_id = NULL, updated_at = ?1
       WHERE id = ?2 AND status = 'authorizing' AND issuance_request_id = ?3`,
    )
    .bind(now, authorizationId, issuanceLeaseId)
    .run();
}

async function issueProductionToken(
  access: PersonalAccess,
  provider: DeviceProvider,
  clientName: string,
  requestId: string,
): Promise<IssuedToken> {
  let response: unknown;
  try {
    response = await memoryControlClient().issue(
      {
        label: `${DEVICE_CLIENTS[provider === "codex" ? "tmcra-codex" : "tmcra-deepseek-harness"].tokenLabel} / ${clientName}`.slice(0, 120),
        subject: access.space.id,
        permissions: [...TOKEN_PERMISSIONS],
        scope_names: [],
        scope_prefixes: [`${access.space.scopeName}-`],
        expires_in_seconds: TOKEN_LIFETIME_SECONDS,
        provisional_delivery_seconds: PROVISIONAL_TOKEN_SECONDS,
      },
      requestId,
    );
  } catch (error) {
    throw asConsoleControlError(error);
  }
  if (!isRecord(response)) {
    throw new ConsoleError(502, "token_issue_invalid_response", "Memory API returned an invalid response.");
  }
  const accessToken = requiredUpstreamString(response.access_token, "access_token", 1_024);
  const tokenId = requiredUpstreamString(response.token_id, "token_id", 200);
  if (
    !/^[A-Za-z0-9_-]{1,160}$/.test(tokenId) ||
    !new RegExp(`^tmcra_st_${tokenId}\\.[A-Za-z0-9_-]{20,700}$`).test(accessToken)
  ) {
    throw new ConsoleError(502, "token_issue_invalid_response", "Memory API returned an invalid credential.");
  }
  const expiresAtSeconds = Number(response.expires_at);
  const now = Date.now();
  const expiresAt = Math.trunc(expiresAtSeconds * 1000);
  if (
    !Number.isFinite(expiresAtSeconds) ||
    expiresAt <= now ||
    expiresAt > now + (TOKEN_LIFETIME_SECONDS + 300) * 1000
  ) {
    throw new ConsoleError(502, "token_issue_invalid_response", "Memory API returned an invalid expiry.");
  }
  return {
    accessToken,
    tokenId,
    tokenPrefix: accessToken.split(".", 1)[0],
    expiresAt,
  };
}

async function confirmProductionToken(tokenId: string, requestId: string) {
  let response: unknown;
  try {
    response = await memoryControlClient().confirm(tokenId, requestId);
  } catch (error) {
    throw asConsoleControlError(error);
  }
  if (!isRecord(response) || response.token_id !== tokenId) {
    throw new ConsoleError(502, "token_confirm_invalid_response", "Memory API returned an invalid confirmation.");
  }
  const expiresAtSeconds = Number(response.expires_at);
  const now = Date.now();
  const expiresAt = Math.trunc(expiresAtSeconds * 1000);
  if (
    !Number.isFinite(expiresAtSeconds) ||
    expiresAt <= now + (TOKEN_LIFETIME_SECONDS - 600) * 1000 ||
    expiresAt > now + (TOKEN_LIFETIME_SECONDS + 300) * 1000
  ) {
    throw new ConsoleError(502, "token_confirm_invalid_response", "Memory API returned an invalid confirmation expiry.");
  }
  return { expiresAt };
}

async function revokeProductionToken(tokenId: string, requestId: string) {
  try {
    await memoryControlClient().revoke(tokenId, requestId);
  } catch (error) {
    throw asConsoleControlError(error);
  }
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
    throw asConsoleControlError(error);
  }
}

function publicMemoryApiBaseUrl() {
  try {
    return normalizeMemoryApiBaseUrl(env.TMCRA_MEMORY_API_BASE_URL);
  } catch (error) {
    throw asConsoleControlError(error);
  }
}

function asConsoleControlError(error: unknown) {
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

async function expireAuthorization(
  database: D1Database,
  row: DeviceAuthorizationRow,
) {
  if (row.status === "claimed") return;
  const connection = await findConnectionByAuthorization(database, row.id);
  const now = Date.now();
  const statements = [
    database
      .prepare(
        `UPDATE device_authorizations
         SET status = 'expired', token_ciphertext = NULL, token_iv = NULL,
             issuance_request_id = NULL, updated_at = ?1
         WHERE id = ?2 AND status = ?3 AND updated_at = ?4
           AND issuance_request_id IS ?5`,
      )
      .bind(now, row.id, row.status, row.updatedAt, row.issuanceRequestId),
  ];
  if (connection) {
    statements.push(
      database
        .prepare(
          `UPDATE device_connections
           SET status = 'expired', revoked_at = COALESCE(revoked_at, ?1),
               updated_at = ?1
           WHERE id = ?2 AND status = 'active'
             AND EXISTS (
               SELECT 1 FROM device_authorizations a
               WHERE a.id = ?3 AND a.status = 'expired' AND a.updated_at = ?1
             )`,
        )
        .bind(now, connection.id, row.id),
    );
    statements.push(
      database
        .prepare(
          `INSERT INTO device_revocation_outbox (
             id, token_id, connection_id, reason, status, attempt_count,
             next_attempt_at, created_at, updated_at
           ) SELECT ?1, ?2, ?3, 'authorization_expired', 'pending', 0, ?4, ?4, ?4
             WHERE EXISTS (
               SELECT 1 FROM device_authorizations a
               WHERE a.id = ?5 AND a.status = 'expired' AND a.updated_at = ?4
             )
           ON CONFLICT(token_id) DO UPDATE SET
             connection_id = COALESCE(device_revocation_outbox.connection_id, excluded.connection_id),
             reason = excluded.reason,
             status = CASE
               WHEN device_revocation_outbox.status = 'completed' THEN 'completed'
               ELSE 'pending'
             END,
             next_attempt_at = CASE
               WHEN device_revocation_outbox.status = 'completed'
                 THEN device_revocation_outbox.next_attempt_at
               ELSE MIN(device_revocation_outbox.next_attempt_at, excluded.next_attempt_at)
             END,
             updated_at = excluded.updated_at`,
        )
        .bind(newId("rvo"), connection.tokenId, connection.id, now, row.id),
    );
  }
  const results = await database.batch(statements);
  if (changes(results[0]) !== 1) return;
  if (connection) {
    await drainRevocationOutbox(database, crypto.randomUUID(), 1, connection.tokenId);
  }
}

async function cleanupDeviceState(database: D1Database) {
  const now = Date.now();
  await database.batch([
    database
      .prepare(
        `INSERT INTO device_revocation_outbox (
           id, token_id, connection_id, reason, status, attempt_count,
           next_attempt_at, created_at, updated_at
         ) SELECT 'rvo_' || lower(hex(randomblob(16))), c.token_id, c.id,
                  'delivery_unclaimed', 'pending', 0, ?1, ?1, ?1
           FROM device_connections c
           JOIN device_authorizations a ON a.id = c.authorization_id
          WHERE a.status = 'approved' AND a.token_ciphertext IS NOT NULL
            AND a.updated_at < ?2 AND c.status = 'active'
          ORDER BY a.updated_at ASC LIMIT ?3
         ON CONFLICT(token_id) DO UPDATE SET
           connection_id = COALESCE(device_revocation_outbox.connection_id, excluded.connection_id),
           reason = 'delivery_unclaimed',
           status = CASE WHEN device_revocation_outbox.status = 'completed'
                         THEN 'completed' ELSE 'pending' END,
           next_attempt_at = MIN(device_revocation_outbox.next_attempt_at, excluded.next_attempt_at),
           updated_at = excluded.updated_at`,
      )
      .bind(now, now - TOKEN_DELIVERY_WINDOW_MS, CLEANUP_LIMIT),
    database
      .prepare(
        `UPDATE device_connections
         SET status = 'expired', revoked_at = COALESCE(revoked_at, ?1), updated_at = ?1
         WHERE authorization_id IN (
           SELECT id FROM device_authorizations
           WHERE status = 'approved' AND token_ciphertext IS NOT NULL
             AND updated_at < ?2
           ORDER BY updated_at ASC LIMIT ?3
         ) AND status = 'active'`,
      )
      .bind(now, now - TOKEN_DELIVERY_WINDOW_MS, CLEANUP_LIMIT),
    database
      .prepare(
        `UPDATE device_authorizations
         SET status = 'expired', token_ciphertext = NULL, token_iv = NULL,
             updated_at = ?1
         WHERE id IN (
           SELECT id FROM device_authorizations
           WHERE status = 'approved' AND token_ciphertext IS NOT NULL
             AND updated_at < ?2
           ORDER BY updated_at ASC LIMIT ?3
         )`,
      )
      .bind(now, now - TOKEN_DELIVERY_WINDOW_MS, CLEANUP_LIMIT),
    database
      .prepare(
        `UPDATE device_authorizations
         SET status = 'expired', token_ciphertext = NULL, token_iv = NULL,
             updated_at = ?1
         WHERE id IN (
           SELECT id FROM device_authorizations
           WHERE expires_at <= ?1
             AND (status = 'pending' OR (status = 'approved' AND token_ciphertext IS NULL))
           ORDER BY expires_at ASC LIMIT ?2
         )`,
      )
      .bind(now, CLEANUP_LIMIT),
    database
      .prepare(
        `UPDATE device_authorizations
         SET status = CASE WHEN expires_at > ?1 THEN 'approved' ELSE 'expired' END,
             issuance_request_id = NULL, updated_at = ?1
         WHERE id IN (
           SELECT id FROM device_authorizations
           WHERE status = 'authorizing' AND updated_at < ?3
           ORDER BY updated_at ASC LIMIT ?2
         )`,
      )
      .bind(now, CLEANUP_LIMIT, now - OUTBOX_LEASE_MS),
    database
      .prepare(
        `DELETE FROM device_authorizations
         WHERE id IN (
           SELECT a.id FROM device_authorizations a
           WHERE a.status IN ('denied', 'expired') AND a.updated_at < ?1
             AND NOT EXISTS (
               SELECT 1 FROM device_connections c WHERE c.authorization_id = a.id
             )
           ORDER BY a.updated_at ASC LIMIT ?2
         )`,
      )
      .bind(now - TERMINAL_RETENTION_MS, CLEANUP_LIMIT),
    database
      .prepare(
        `DELETE FROM device_flow_rate_limits
         WHERE rowid IN (
           SELECT rowid FROM device_flow_rate_limits
           WHERE bucket_start < ?1 ORDER BY bucket_start ASC LIMIT ?2
         )`,
      )
      .bind(now - RATE_WINDOW_MS * 2, CLEANUP_LIMIT),
    database
      .prepare(
        `DELETE FROM device_revocation_outbox
         WHERE id IN (
           SELECT id FROM device_revocation_outbox
           WHERE status = 'completed' AND completed_at < ?1
           ORDER BY completed_at ASC LIMIT ?2
         )`,
      )
      .bind(now - OUTBOX_RETENTION_MS, CLEANUP_LIMIT),
    database
      .prepare(
        `UPDATE device_revocation_outbox
         SET status = 'pending', next_attempt_at = ?1, updated_at = ?1,
             last_error_code = 'lease_recovered'
         WHERE id IN (
           SELECT id FROM device_revocation_outbox
           WHERE status = 'processing' AND updated_at < ?2
           ORDER BY updated_at ASC LIMIT ?3
         )`,
      )
      .bind(now, now - OUTBOX_LEASE_MS, CLEANUP_LIMIT),
  ]);
  return drainRevocationOutbox(database, crypto.randomUUID(), 1);
}

function rateAdmissionStatement(
  database: D1Database,
  limitKey: string,
  bucketStart: number,
  limit: number,
  admissionId: string,
  now: number,
) {
  return database
    .prepare(
      `INSERT INTO device_flow_rate_limits (
         limit_key, bucket_start, request_count, last_admission_id, updated_at
       ) VALUES (?1, ?2, 1, ?3, ?4)
       ON CONFLICT(limit_key, bucket_start) DO UPDATE SET
         request_count = request_count + 1,
         last_admission_id = excluded.last_admission_id,
         updated_at = excluded.updated_at
       WHERE request_count < ?5`,
    )
    .bind(limitKey, bucketStart, admissionId, now, limit);
}

async function sourceFingerprint(source: string) {
  const normalized = String(source || "unknown").trim().slice(0, 200) || "unknown";
  return keyedFingerprint(`tmcra:device-source:${normalized}`);
}

async function findAuthorizationForAccount(
  database: D1Database,
  access: PersonalAccess,
  userCodeInput: unknown,
  requestSource: string,
) {
  let userCode: string;
  try {
    userCode = normalizedUserCode(userCodeInput);
  } catch (error) {
    await consumeInvalidUserCodeAttempt(database, access, requestSource);
    throw error;
  }
  const row = await findAuthorizationByUserHash(
    database,
    await sha256Base64Url(userCode),
  );
  if (
    !row ||
    (row.approvedByUserId && row.approvedByUserId !== access.actor.id) ||
    (row.personalSpaceId && row.personalSpaceId !== access.space.id)
  ) {
    await consumeInvalidUserCodeAttempt(database, access, requestSource);
    throw new ConsoleError(404, "authorization_not_found", "Authorization code was not found.");
  }
  return { row, userCode };
}

async function consumeInvalidUserCodeAttempt(
  database: D1Database,
  access: PersonalAccess,
  requestSource: string,
) {
  const now = Date.now();
  const bucketStart = Math.floor(now / RATE_WINDOW_MS) * RATE_WINDOW_MS;
  const admissionId = newId("adm");
  const sourceHash = await sourceFingerprint(requestSource);
  const accountKey = `invalid:account:${await keyedFingerprint(access.actor.id)}`;
  const sourceKey = `invalid:source:${sourceHash}`;
  const results = await database.batch([
    rateAdmissionStatement(
      database,
      accountKey,
      bucketStart,
      INVALID_CODE_ACCOUNT_LIMIT,
      admissionId,
      now,
    ),
    rateAdmissionStatement(
      database,
      sourceKey,
      bucketStart,
      INVALID_CODE_SOURCE_LIMIT,
      admissionId,
      now,
    ),
  ]);
  if (results.some((result) => changes(result) !== 1)) {
    throw new ConsoleError(
      429,
      "invalid_code_rate_limited",
      "Too many invalid authorization code attempts. Try again later.",
    );
  }
}

function revocationOutboxStatement(
  database: D1Database,
  tokenId: string,
  connectionId: string | null,
  reason: string,
  now: number,
) {
  return database
    .prepare(
      `INSERT INTO device_revocation_outbox (
         id, token_id, connection_id, reason, status, attempt_count,
         next_attempt_at, created_at, updated_at
       ) VALUES (?1, ?2, ?3, ?4, 'pending', 0, ?5, ?5, ?5)
       ON CONFLICT(token_id) DO UPDATE SET
         connection_id = COALESCE(device_revocation_outbox.connection_id, excluded.connection_id),
         reason = excluded.reason,
         status = CASE
           WHEN device_revocation_outbox.status = 'completed' THEN 'completed'
           ELSE 'pending'
         END,
         next_attempt_at = CASE
           WHEN device_revocation_outbox.status = 'completed'
             THEN device_revocation_outbox.next_attempt_at
           ELSE MIN(device_revocation_outbox.next_attempt_at, excluded.next_attempt_at)
         END,
         updated_at = excluded.updated_at`,
    )
    .bind(newId("rvo"), tokenId, connectionId, reason, now);
}

async function drainRevocationOutbox(
  database: D1Database,
  requestId: string,
  limit: number,
  onlyTokenId?: string,
) {
  let attempted = 0;
  const now = Date.now();
  const result = await database
    .prepare(
      `SELECT id, token_id AS tokenId, connection_id AS connectionId,
              attempt_count AS attemptCount
       FROM device_revocation_outbox
       WHERE status = 'pending' AND next_attempt_at <= ?1
         AND (?2 IS NULL OR token_id = ?2)
       ORDER BY next_attempt_at ASC, created_at ASC
       LIMIT ?3`,
    )
    .bind(now, onlyTokenId ?? null, Math.max(1, Math.min(limit, 10)))
    .all<RevocationOutboxRow>();
  for (const row of result.results) {
    const claimedAt = Date.now();
    const claimed = await database
      .prepare(
        `UPDATE device_revocation_outbox
         SET status = 'processing', last_attempt_at = ?1,
             attempt_count = attempt_count + 1, updated_at = ?1
         WHERE id = ?2 AND status = 'pending' AND next_attempt_at <= ?1`,
      )
      .bind(claimedAt, row.id)
      .run();
    if (changes(claimed) !== 1) continue;
    attempted += 1;
    try {
      await revokeProductionToken(row.tokenId, requestId);
      const completedAt = Date.now();
      await database
        .prepare(
          `UPDATE device_revocation_outbox
           SET status = 'completed', completed_at = ?1, updated_at = ?1,
               last_error_code = NULL
           WHERE id = ?2 AND status = 'processing'`,
        )
        .bind(completedAt, row.id)
        .run();
    } catch (error) {
      const attempt = Number(row.attemptCount) + 1;
      const retryAt = Date.now() + Math.min(24 * 60 * 60_000, 30_000 * 2 ** Math.min(attempt - 1, 11));
      await database
        .prepare(
          `UPDATE device_revocation_outbox
           SET status = 'pending', next_attempt_at = ?1, updated_at = ?2,
               last_error_code = ?3
           WHERE id = ?4 AND status = 'processing'`,
        )
        .bind(retryAt, Date.now(), revocationErrorCode(error), row.id)
        .run();
    }
  }
  return attempted;
}

function revocationErrorCode(error: unknown) {
  if (error instanceof ConsoleError) return error.code.slice(0, 80);
  return error instanceof Error ? error.name.slice(0, 80) : "unknown_error";
}

async function expireNaturalConnections(
  database: D1Database,
  access: PersonalAccess,
) {
  const now = Date.now();
  await database.batch([
    database
      .prepare(
        `INSERT INTO device_revocation_outbox (
           id, token_id, connection_id, reason, status, attempt_count,
           next_attempt_at, created_at, updated_at
         ) SELECT 'rvo_' || lower(hex(randomblob(16))), token_id, id,
                  'token_expired', 'pending', 0, ?1, ?1, ?1
           FROM device_connections
          WHERE user_id = ?2 AND personal_space_id = ?3
            AND status = 'active' AND token_expires_at <= ?1
         ON CONFLICT(token_id) DO UPDATE SET
           connection_id = COALESCE(device_revocation_outbox.connection_id, excluded.connection_id),
           reason = 'token_expired',
           status = CASE WHEN device_revocation_outbox.status = 'completed'
                         THEN 'completed' ELSE 'pending' END,
           updated_at = excluded.updated_at`,
      )
      .bind(now, access.actor.id, access.space.id),
    database
      .prepare(
        `UPDATE device_connections
         SET status = 'expired', revoked_at = COALESCE(revoked_at, ?1),
             updated_at = ?1
         WHERE user_id = ?2 AND personal_space_id = ?3
           AND status = 'active' AND token_expires_at <= ?1`,
      )
      .bind(now, access.actor.id, access.space.id),
  ]);
}

async function findAuthorizationByDeviceHash(
  database: D1Database,
  hash: string,
) {
  return database
    .prepare(`${authorizationSelect()} WHERE device_code_hash = ?1 LIMIT 1`)
    .bind(hash)
    .first<DeviceAuthorizationRow>();
}

async function findAuthorizationByUserHash(
  database: D1Database,
  hash: string,
) {
  return database
    .prepare(`${authorizationSelect()} WHERE user_code_hash = ?1 LIMIT 1`)
    .bind(hash)
    .first<DeviceAuthorizationRow>();
}

function authorizationSelect() {
  return `SELECT
    id,
    device_code_hash AS deviceCodeHash,
    user_code_hash AS userCodeHash,
    code_challenge AS codeChallenge,
    provider,
    client_name AS clientName,
    source_hash AS sourceHash,
    status,
    interval_seconds AS intervalSeconds,
    poll_count AS pollCount,
    last_polled_at AS lastPolledAt,
    approved_by_user_id AS approvedByUserId,
    personal_space_id AS personalSpaceId,
    token_ciphertext AS tokenCiphertext,
    token_iv AS tokenIv,
    created_at AS createdAt,
    updated_at AS updatedAt,
    expires_at AS expiresAt,
    approved_at AS approvedAt,
    claimed_at AS claimedAt,
    issuance_request_id AS issuanceRequestId,
    delivery_receipt_hash AS deliveryReceiptHash
  FROM device_authorizations`;
}

async function findConnectionByAuthorization(
  database: D1Database,
  authorizationId: string,
) {
  return database
    .prepare(
      `SELECT
         id,
         authorization_id AS authorizationId,
         user_id AS userId,
         personal_space_id AS personalSpaceId,
         provider,
         display_name AS displayName,
         token_id AS tokenId,
         token_prefix AS tokenPrefix,
         scope_prefix AS scopePrefix,
         permissions_json AS permissionsJson,
         status,
         token_expires_at AS tokenExpiresAt,
         created_at AS createdAt,
         updated_at AS updatedAt,
         last_connected_at AS lastConnectedAt,
         revoked_at AS revokedAt,
         (SELECT status FROM device_revocation_outbox
          WHERE token_id = device_connections.token_id LIMIT 1) AS revocationStatus
       FROM device_connections
       WHERE authorization_id = ?1
       LIMIT 1`,
    )
    .bind(authorizationId)
    .first<DeviceConnectionRow>();
}

function assertAuthorizationOwner(
  row: DeviceAuthorizationRow,
  access: PersonalAccess,
) {
  if (
    (row.approvedByUserId && row.approvedByUserId !== access.actor.id) ||
    (row.personalSpaceId && row.personalSpaceId !== access.space.id)
  ) {
    throw new ConsoleError(404, "authorization_not_found", "Authorization code was not found.");
  }
}

function sanitizeConnection(row: DeviceConnectionRow | null) {
  if (!row) return null;
  const naturallyExpired = row.tokenExpiresAt <= Date.now();
  const status = row.status === "active" && naturallyExpired ? "expired" : row.status;
  return {
    id: row.id,
    provider: row.provider,
    displayName: row.displayName,
    tokenId: row.tokenId,
    tokenPrefix: row.tokenPrefix,
    scopePrefix: row.scopePrefix,
    permissions: parseStringArray(row.permissionsJson),
    status,
    revocationPending:
      row.revocationStatus === "pending" || row.revocationStatus === "processing",
    expiresAt: row.tokenExpiresAt,
    createdAt: row.createdAt,
    firstConnectedAt: row.lastConnectedAt,
    lastConnectedAt: row.lastConnectedAt,
    revokedAt: row.revokedAt,
  };
}

function requiredPkceChallenge(value: unknown) {
  return requiredPattern(
    value,
    PKCE_CHALLENGE_PATTERN,
    "code_challenge",
    "invalid_code_challenge",
  );
}

function cleanClientName(value: unknown, fallback: string) {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value !== "string") {
    throw new DeviceFlowError(422, "invalid_client_name", "clientName is invalid.");
  }
  const clean = value.trim().replace(/\s+/g, " ");
  if (!clean || clean.length > 80 || /[\u0000-\u001f\u007f]/.test(clean)) {
    throw new DeviceFlowError(422, "invalid_client_name", "clientName is invalid.");
  }
  return clean;
}

function normalizedUserCode(value: unknown) {
  if (typeof value !== "string") {
    throw new ConsoleError(422, "invalid_user_code", "Enter the eight-character authorization code.");
  }
  const code = value.toUpperCase().replace(/[\s-]+/g, "");
  if (!USER_CODE_PATTERN.test(code)) {
    throw new ConsoleError(422, "invalid_user_code", "Enter the eight-character authorization code.");
  }
  return code;
}

function requiredPattern(
  value: unknown,
  pattern: RegExp,
  field: string,
  code: string,
) {
  if (typeof value !== "string" || !pattern.test(value)) {
    throw new DeviceFlowError(400, code, `${field} is invalid.`);
  }
  return value;
}

function randomUserCode() {
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  let value = "";
  for (const byte of bytes) value += USER_CODE_ALPHABET[byte % USER_CODE_ALPHABET.length];
  return value;
}

function newId(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replace(/-/g, "")}`;
}

function randomBase64Url(byteLength: number) {
  return bytesToBase64Url(crypto.getRandomValues(new Uint8Array(byteLength)));
}

async function sha256Base64Url(value: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return bytesToBase64Url(new Uint8Array(digest));
}

async function keyedFingerprint(value: string) {
  const encoded = String(
    env.TMCRA_DEVICE_FLOW_HASH_KEY ?? env.TMCRA_DEVICE_TOKEN_ENCRYPTION_KEY ?? "",
  ).trim();
  let bytes: Uint8Array;
  try {
    bytes = base64UrlToBytes(encoded);
  } catch {
    throw new ConsoleError(
      503,
      "device_hash_key_not_configured",
      "Device authorization security is not configured.",
    );
  }
  if (bytes.byteLength !== 32) {
    throw new ConsoleError(
      503,
      "device_hash_key_not_configured",
      "Device authorization security is not configured.",
    );
  }
  const key = await crypto.subtle.importKey(
    "raw",
    Uint8Array.from(bytes).buffer,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(value),
  );
  return bytesToBase64Url(new Uint8Array(signature));
}

async function tokenEncryptionKey() {
  const encoded = String(env.TMCRA_DEVICE_TOKEN_ENCRYPTION_KEY ?? "").trim();
  let bytes: Uint8Array;
  try {
    bytes = base64UrlToBytes(encoded);
  } catch {
    throw new ConsoleError(
      503,
      "device_encryption_key_not_configured",
      "Device authorization security is not configured.",
    );
  }
  if (bytes.byteLength !== 32) {
    throw new ConsoleError(
      503,
      "device_encryption_key_not_configured",
      "Device authorization security is not configured.",
    );
  }
  return crypto.subtle.importKey(
    "raw",
    Uint8Array.from(bytes).buffer,
    { name: "AES-GCM" },
    false,
    ["encrypt", "decrypt"],
  );
}

async function encryptTokenDelivery(authorizationId: string, plaintext: string) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt(
    {
      name: "AES-GCM",
      iv,
      additionalData: new TextEncoder().encode(`tmcra-device-delivery-v1:${authorizationId}`),
    },
    await tokenEncryptionKey(),
    new TextEncoder().encode(plaintext),
  );
  return {
    ciphertext: bytesToBase64Url(new Uint8Array(ciphertext)),
    iv: bytesToBase64Url(iv),
  };
}

async function decryptTokenDelivery(
  authorizationId: string,
  ciphertext: string,
  iv: string,
) {
  const plaintext = await crypto.subtle.decrypt(
    {
      name: "AES-GCM",
      iv: base64UrlToBytes(iv),
      additionalData: new TextEncoder().encode(`tmcra-device-delivery-v1:${authorizationId}`),
    },
    await tokenEncryptionKey(),
    base64UrlToBytes(ciphertext),
  );
  return new TextDecoder().decode(plaintext);
}

function constantTimeEqual(left: string, right: string) {
  const maxLength = Math.max(left.length, right.length);
  let difference = left.length ^ right.length;
  for (let index = 0; index < maxLength; index += 1) {
    difference |= (left.charCodeAt(index) || 0) ^ (right.charCodeAt(index) || 0);
  }
  return difference === 0;
}

function bytesToBase64Url(bytes: Uint8Array) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function base64UrlToBytes(value: string) {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) throw new Error("invalid base64url");
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(
    Math.ceil(value.length / 4) * 4,
    "=",
  );
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}


function changes(result: D1Result) {
  return Number(result.meta.changes ?? 0);
}

function databaseMessage(error: unknown) {
  return error instanceof Error ? `${error.message}\n${String(error.cause ?? "")}` : String(error);
}

function requiredUpstreamString(value: unknown, field: string, maximum: number) {
  if (typeof value !== "string" || !value || value.length > maximum) {
    throw new ConsoleError(502, "token_issue_invalid_response", `Memory API omitted ${field}.`);
  }
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function parseStringArray(value: unknown) {
  if (typeof value !== "string") return [];
  try {
    const parsed: unknown = JSON.parse(value);
    return Array.isArray(parsed) && parsed.every((entry) => typeof entry === "string")
      ? parsed
      : [];
  } catch {
    return [];
  }
}
