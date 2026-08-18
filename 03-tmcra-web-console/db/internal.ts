import { ensureConsoleSchema } from "./console";
import { isConfiguredBootstrapOwner } from "./internal-bootstrap-policy";
import { getD1 } from "./index";

export type InternalRole =
  | "platform_owner"
  | "platform_admin"
  | "support"
  | "security"
  | "analyst";

export type InternalStaffStatus = "invited" | "active" | "suspended";

export type InternalIdentity = {
  email: string;
  displayName: string;
  fullName: string | null;
};

export type InternalBootstrapConfig = {
  /** A single trusted server-side email. Missing or invalid values fail closed. */
  ownerEmail?: unknown;
};

export class InternalError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "InternalError";
    this.status = status;
    this.code = code;
  }
}

type StaffRow = {
  id: string;
  emailNormalized: string;
  emailDisplay: string;
  displayName: string;
  role: InternalRole;
  status: InternalStaffStatus;
  invitedByStaffId: string | null;
  joinedAt: number | null;
  lastSeenAt: number | null;
  createdAt: number;
  updatedAt: number;
};

type OrganizationRow = {
  id: string;
  name: string;
  slug: string;
  status: "active" | "archived";
  sampleMode: number;
  version: number;
  createdAt: number;
  updatedAt: number;
  memberCount: number;
  activeMemberCount: number;
  agentCount: number;
  eventCount: number;
  edgeCount: number;
  apiKeyCount: number;
  activeApiKeyCount: number;
  lastActivity: number;
};

type AuditRow = {
  id: string;
  actorStaffId: string | null;
  actorEmail: string;
  actorRole: InternalRole;
  action: string;
  targetType: string;
  targetId: string;
  requestId: string;
  metadataJson: string;
  createdAt: number;
};

type AccessRequestRow = {
  id: string;
  emailDisplay: string;
  contactName: string;
  companyName: string;
  industry: string;
  companySize: string;
  primaryUseCase: string;
  platformsJson: string;
  timeline: string;
  source: string;
  status: "new" | "contacted" | "qualified" | "closed";
  reviewNote: string;
  lastReviewedBy: string | null;
  lastReviewedAt: number | null;
  version: number;
  createdAt: number;
  updatedAt: number;
};

export type InternalReleasePolicy = {
  availability: "available" | "unavailable";
  applicationMode: "configuration_only";
  applied: false;
  version: number;
  targetReleaseId: string | null;
  rollbackReleaseId: string | null;
  channel: "stable" | "canary" | null;
  canaryPercent: number | null;
  reason: string | null;
  updatedAt: number | null;
  updatedBy: string | null;
};

const BOOTSTRAP_META_KEY = "internal_bootstrap_owner_email";
const RELEASE_POLICY_META_KEY = "internal_release_policy_v1";
const INTERNAL_SCHEMA_VERSION = "1";
const MUTATIONS_PER_MINUTE = 30;
const DAY_MS = 86_400_000;

const ROLE_VALUES = [
  "platform_owner",
  "platform_admin",
  "support",
  "security",
  "analyst",
] as const;
const STATUS_VALUES = ["invited", "active", "suspended"] as const;
const ACCESS_REQUEST_STATUS_VALUES = ["new", "contacted", "qualified", "closed"] as const;

const INTERNAL_SCHEMA_STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS internal_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000)
  )`,
  `CREATE TABLE IF NOT EXISTS internal_staff (
    id TEXT PRIMARY KEY,
    email_normalized TEXT NOT NULL UNIQUE,
    email_display TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('platform_owner', 'platform_admin', 'support', 'security', 'analyst')),
    status TEXT NOT NULL DEFAULT 'invited' CHECK (status IN ('invited', 'active', 'suspended')),
    invited_by_staff_id TEXT,
    joined_at INTEGER,
    last_seen_at INTEGER,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000)
  )`,
  `CREATE INDEX IF NOT EXISTS internal_staff_status_role_idx
    ON internal_staff(status, role, created_at)`,
  `CREATE TABLE IF NOT EXISTS internal_audit_logs (
    id TEXT PRIMARY KEY,
    actor_staff_id TEXT,
    actor_email TEXT NOT NULL,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('platform_owner', 'platform_admin', 'support', 'security', 'analyst')),
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json)),
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000)
  )`,
  `CREATE INDEX IF NOT EXISTS internal_audit_logs_created_idx
    ON internal_audit_logs(created_at DESC, id DESC)`,
  `CREATE INDEX IF NOT EXISTS internal_audit_logs_actor_created_idx
    ON internal_audit_logs(actor_staff_id, created_at DESC)`,
  `CREATE INDEX IF NOT EXISTS internal_audit_logs_target_created_idx
    ON internal_audit_logs(target_type, target_id, created_at DESC)`,
  `CREATE TABLE IF NOT EXISTS internal_action_limits (
    actor_staff_id TEXT NOT NULL,
    bucket_start INTEGER NOT NULL,
    mutation_count INTEGER NOT NULL DEFAULT 1 CHECK (mutation_count > 0),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    PRIMARY KEY (actor_staff_id, bucket_start)
  )`,
  `CREATE INDEX IF NOT EXISTS internal_action_limits_bucket_idx
    ON internal_action_limits(bucket_start)`,
  `CREATE TRIGGER IF NOT EXISTS internal_staff_keep_last_owner_delete
    BEFORE DELETE ON internal_staff
    WHEN OLD.role = 'platform_owner' AND OLD.status = 'active'
      AND NOT EXISTS (
        SELECT 1 FROM internal_staff AS other
        WHERE other.id <> OLD.id
          AND other.role = 'platform_owner'
          AND other.status = 'active'
      )
    BEGIN
      SELECT RAISE(ABORT, 'internal_last_platform_owner');
    END`,
  `CREATE TRIGGER IF NOT EXISTS internal_staff_keep_last_owner_update
    BEFORE UPDATE OF role, status ON internal_staff
    WHEN OLD.role = 'platform_owner' AND OLD.status = 'active'
      AND (NEW.role <> 'platform_owner' OR NEW.status <> 'active')
      AND NOT EXISTS (
        SELECT 1 FROM internal_staff AS other
        WHERE other.id <> OLD.id
          AND other.role = 'platform_owner'
          AND other.status = 'active'
      )
    BEGIN
      SELECT RAISE(ABORT, 'internal_last_platform_owner');
    END`,
  `CREATE TRIGGER IF NOT EXISTS internal_audit_logs_immutable_update
    BEFORE UPDATE ON internal_audit_logs
    BEGIN
      SELECT RAISE(ABORT, 'internal_audit_immutable');
    END`,
  `CREATE TRIGGER IF NOT EXISTS internal_audit_logs_immutable_delete
    BEFORE DELETE ON internal_audit_logs
    BEGIN
      SELECT RAISE(ABORT, 'internal_audit_immutable');
    END`,
  `CREATE TRIGGER IF NOT EXISTS internal_bootstrap_meta_immutable_update
    BEFORE UPDATE ON internal_meta
    WHEN OLD.key = 'internal_bootstrap_owner_email'
    BEGIN
      SELECT RAISE(ABORT, 'internal_bootstrap_locked');
    END`,
  `CREATE TRIGGER IF NOT EXISTS internal_bootstrap_meta_immutable_delete
    BEFORE DELETE ON internal_meta
    WHEN OLD.key = 'internal_bootstrap_owner_email'
    BEGIN
      SELECT RAISE(ABORT, 'internal_bootstrap_locked');
    END`,
] as const;

let internalSchemaReady: Promise<void> | undefined;

export function ensureInternalSchema(
  database: D1Database = getD1(),
): Promise<void> {
  if (!internalSchemaReady) {
    internalSchemaReady = initializeInternalSchema(database).catch((error) => {
      internalSchemaReady = undefined;
      throw error;
    });
  }
  return internalSchemaReady;
}

async function initializeInternalSchema(database: D1Database): Promise<void> {
  await ensureConsoleSchema(database);
  await database.batch(
    INTERNAL_SCHEMA_STATEMENTS.map((statement) => database.prepare(statement)),
  );
}

export async function getInternalSnapshot(
  identity: InternalIdentity,
  options: { organizationId?: string } = {},
  bootstrapConfig: InternalBootstrapConfig = {},
) {
  const database = getD1();
  const actor = await requireInternalActor(
    database,
    identity,
    false,
    bootstrapConfig,
  );
  const now = Date.now();
  const canReadOrganizations = actor.role !== "analyst";
  const canReadStaffAndAudit = [
    "platform_owner",
    "platform_admin",
    "security",
  ].includes(actor.role);
  const canReadCustomerDetails = ["platform_owner", "platform_admin"].includes(
    actor.role,
  );
  const canReadAccessRequests = ["platform_owner", "platform_admin", "support"].includes(actor.role);
  const organizationId = options.organizationId
    ? parseToken(options.organizationId, "organizationId", 100)
    : undefined;

  const metricsPromise = database
    .prepare(
      `SELECT
         (SELECT COUNT(*) FROM organizations) AS organizations,
         (SELECT COUNT(*) FROM organizations WHERE status = 'active') AS activeOrganizations,
         (SELECT COUNT(*) FROM organizations WHERE status = 'archived') AS archivedOrganizations,
         (SELECT COUNT(*) FROM users) AS users,
         (SELECT COUNT(*) FROM internal_staff WHERE status = 'active') AS activeStaff,
         (SELECT COUNT(*) FROM agents) AS agents,
         (SELECT COUNT(*) FROM memory_events) AS memoryEvents,
         (SELECT COUNT(*) FROM memory_event_edges) AS memoryEdges,
         (SELECT COUNT(*) FROM api_keys) AS apiKeys,
         (SELECT COUNT(*) FROM early_access_requests) AS accessRequests,
         (SELECT COUNT(*) FROM early_access_requests WHERE status = 'new') AS newAccessRequests,
         (SELECT COUNT(*) FROM api_keys
            WHERE revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?1)) AS activeApiKeys,
         (SELECT COUNT(*) FROM memory_events WHERE created_at >= ?2) AS writes24h,
         (SELECT COUNT(*) FROM memory_events WHERE created_at >= ?3) AS writes7d`,
    )
    .bind(now, now - DAY_MS, startOfUtcDay(now - 6 * DAY_MS))
    .first<Record<string, number>>();

  const organizationsPromise: Promise<{ results: OrganizationRow[] }> = canReadOrganizations
    ? database
    .prepare(
      `SELECT
         o.id,
         o.name,
         o.slug,
         o.status,
         o.sample_mode AS sampleMode,
         o.version,
         o.created_at AS createdAt,
         o.updated_at AS updatedAt,
         (SELECT COUNT(*) FROM organization_members m
            WHERE m.organization_id = o.id) AS memberCount,
         (SELECT COUNT(*) FROM organization_members m
            WHERE m.organization_id = o.id AND m.status = 'active') AS activeMemberCount,
         (SELECT COUNT(*) FROM agents a
            WHERE a.organization_id = o.id) AS agentCount,
         (SELECT COUNT(*) FROM memory_events e
            WHERE e.organization_id = o.id) AS eventCount,
         (SELECT COUNT(*) FROM memory_event_edges edge
            WHERE edge.organization_id = o.id) AS edgeCount,
         (SELECT COUNT(*) FROM api_keys k
            WHERE k.organization_id = o.id) AS apiKeyCount,
         (SELECT COUNT(*) FROM api_keys k
            WHERE k.organization_id = o.id
              AND k.revoked_at IS NULL
              AND (k.expires_at IS NULL OR k.expires_at > ?1)) AS activeApiKeyCount,
         MAX(
           o.updated_at,
           COALESCE((SELECT MAX(a.updated_at) FROM agents a WHERE a.organization_id = o.id), 0),
           COALESCE((SELECT MAX(e.created_at) FROM memory_events e WHERE e.organization_id = o.id), 0),
           COALESCE((SELECT MAX(edge.created_at) FROM memory_event_edges edge WHERE edge.organization_id = o.id), 0),
           COALESCE((SELECT MAX(COALESCE(k.last_used_at, k.created_at)) FROM api_keys k WHERE k.organization_id = o.id), 0)
         ) AS lastActivity
       FROM organizations o
       ORDER BY lastActivity DESC, o.id
       LIMIT 250`,
    )
    .bind(now)
        .all<OrganizationRow>()
    : Promise.resolve({ results: [] });

  const staffPromise: Promise<{ results: StaffRow[] }> = canReadStaffAndAudit
    ? database
    .prepare(
      `SELECT
         id,
         email_normalized AS emailNormalized,
         email_display AS emailDisplay,
         display_name AS displayName,
         role,
         status,
         invited_by_staff_id AS invitedByStaffId,
         joined_at AS joinedAt,
         last_seen_at AS lastSeenAt,
         created_at AS createdAt,
         updated_at AS updatedAt
       FROM internal_staff
       ORDER BY
         CASE role
           WHEN 'platform_owner' THEN 0
           WHEN 'platform_admin' THEN 1
           WHEN 'security' THEN 2
           WHEN 'support' THEN 3
           ELSE 4
         END,
         display_name COLLATE NOCASE,
         id
       LIMIT 250`,
    )
        .all<StaffRow>()
    : Promise.resolve({ results: [] });

  const auditPromise: Promise<{ results: AuditRow[] }> = canReadStaffAndAudit
    ? database
    .prepare(
      `SELECT
         id,
         actor_staff_id AS actorStaffId,
         actor_email AS actorEmail,
         actor_role AS actorRole,
         action,
         target_type AS targetType,
         target_id AS targetId,
         request_id AS requestId,
         metadata_json AS metadataJson,
         created_at AS createdAt
       FROM internal_audit_logs
       ORDER BY created_at DESC, id DESC
       LIMIT 100`,
    )
        .all<AuditRow>()
    : Promise.resolve({ results: [] });

  const accessRequestsPromise: Promise<{ results: AccessRequestRow[] }> = canReadAccessRequests
    ? database
        .prepare(
          `SELECT
             id,
             email_display AS emailDisplay,
             contact_name AS contactName,
             company_name AS companyName,
             industry,
             company_size AS companySize,
             primary_use_case AS primaryUseCase,
             platforms_json AS platformsJson,
             timeline,
             source,
             status,
             review_note AS reviewNote,
             last_reviewed_by AS lastReviewedBy,
             last_reviewed_at AS lastReviewedAt,
             version,
             created_at AS createdAt,
             updated_at AS updatedAt
           FROM early_access_requests
           ORDER BY
             CASE status WHEN 'new' THEN 0 WHEN 'qualified' THEN 1 WHEN 'contacted' THEN 2 ELSE 3 END,
             updated_at DESC,
             id
           LIMIT 250`,
        )
        .all<AccessRequestRow>()
    : Promise.resolve({ results: [] });

  const usagePromise = database
    .prepare(
      `SELECT
         strftime('%Y-%m-%d', created_at / 1000, 'unixepoch') AS day,
         COUNT(*) AS writes
       FROM memory_events
       WHERE created_at >= ?1
       GROUP BY day
       ORDER BY day`,
    )
    .bind(startOfUtcDay(now - 6 * DAY_MS))
    .all<{ day: string; writes: number }>();

  const [metrics, organizationRows, staffRows, auditRows, accessRequestRows, usageRows] =
    await Promise.all([
      metricsPromise,
      organizationsPromise,
      staffPromise,
      auditPromise,
      accessRequestsPromise,
      usagePromise,
    ]);

  const organizations = organizationRows.results.map(normalizeOrganization);
  let selectedBase = organizationId
    ? organizations.find((organization) => organization.id === organizationId)
    : organizations[0];
  if (canReadOrganizations && organizationId && !selectedBase) {
    const direct = await getOrganizationSummary(database, organizationId, now);
    selectedBase = direct ? normalizeOrganization(direct) : undefined;
  }
  if (canReadOrganizations && organizationId && !selectedBase) {
    throw new InternalError(
      404,
      "organization_not_found",
      "Organization not found.",
    );
  }

  const selectedOrganization = selectedBase
    ? await getOrganizationDetail(database, selectedBase, canReadCustomerDetails)
    : null;
  const bootstrapLock = await database
    .prepare(`SELECT value FROM internal_meta WHERE key = ?1 LIMIT 1`)
    .bind(BOOTSTRAP_META_KEY)
    .first<{ value: string }>();
  const releasePolicyRow = await database
    .prepare(`SELECT value, updated_at AS updatedAt FROM internal_meta WHERE key = ?1 LIMIT 1`)
    .bind(RELEASE_POLICY_META_KEY)
    .first<{ value: string; updatedAt: number }>();

  return {
    actor: publicStaff(actor),
    system: {
      schemaVersion: Number(INTERNAL_SCHEMA_VERSION),
      generatedAt: now,
      bootstrapLocked: Boolean(bootstrapLock),
      mutationLimitPerMinute: MUTATIONS_PER_MINUTE,
      accessGate: "Private Sites + exact-email internal RBAC",
      authMode: "TMCRA Account / SIWC-compatible identity",
      database: "Cloudflare D1",
      databaseBinding: "DB",
      auditProtection: "Append-only database triggers",
      environment: "Cloudflare Workers",
      dataRegion: "Cloudflare-managed",
    },
    metrics: { ...normalizeNumbers(metrics ?? {}), updatedAt: now },
    organizations,
    selectedOrganization,
    staff: staffRows.results.map(publicStaff),
    auditLogs: auditRows.results.map((entry) => ({
      ...entry,
      metadata: parseJsonObject(entry.metadataJson),
      metadataJson: undefined,
    })),
    releasePolicy: parseReleasePolicy(releasePolicyRow),
    accessRequests: accessRequestRows.results.map((entry) => ({
      ...entry,
      version: Number(entry.version),
      platforms: parseStringArray(entry.platformsJson),
      platformsJson: undefined,
    })),
    usageDaily: fillUsageDays(usageRows.results),
  };
}

export async function executeInternalAction(
  identity: InternalIdentity,
  requestId: string,
  action: string,
  payloadValue: unknown,
  bootstrapConfig: InternalBootstrapConfig = {},
) {
  const database = getD1();
  const payload = asObject(payloadValue, "payload");
  const safeRequestId = cleanRequestId(requestId);

  try {
    if (action === "staff.accept_invite") {
      const actor = await requireInternalActor(
        database,
        identity,
        true,
        bootstrapConfig,
      );
      if (actor.status !== "invited") {
        throw new InternalError(409, "invitation_not_pending", "No pending invitation exists.");
      }
      await consumeMutation(database, actor.id);
      return acceptInvitation(database, actor, identity, safeRequestId);
    }

    const actor = await requireInternalActor(
      database,
      identity,
      false,
      bootstrapConfig,
    );
    await consumeMutation(database, actor.id);

    switch (action) {
      case "staff.add":
        return addStaff(database, actor, payload, safeRequestId);
      case "staff.update":
        return updateStaff(database, actor, payload, safeRequestId);
      case "staff.remove":
        return removeStaff(database, actor, payload, safeRequestId);
      case "organization.set_status":
        return setOrganizationStatus(database, actor, payload, safeRequestId);
      case "access_request.update":
        return updateAccessRequest(database, actor, payload, safeRequestId);
      case "release_policy.update":
        return updateReleasePolicy(database, actor, payload, safeRequestId);
      default:
        throw new InternalError(400, "unknown_action", "Unsupported internal action.");
    }
  } catch (error) {
    throw mapDatabaseError(error);
  }
}

async function updateReleasePolicy(
  database: D1Database,
  actor: StaffRow,
  payload: Record<string, unknown>,
  requestId: string,
) {
  requireRole(actor.role, ["platform_owner", "platform_admin"]);
  const targetReleaseId = parseToken(payload.targetReleaseId, "targetReleaseId", 128);
  const rollbackReleaseId = optionalToken(payload.rollbackReleaseId, "rollbackReleaseId", 128);
  const channel = parseEnum(payload.channel, "channel", ["stable", "canary"] as const);
  const canaryPercent = parsePercentage(payload.canaryPercent, "canaryPercent");
  const reason = requiredString(payload.reason, "reason", 500);
  const expectedVersion = parseNonNegativeInteger(payload.expectedVersion, "expectedVersion");
  if (reason.length < 10) {
    throw new InternalError(422, "invalid_field", "reason must be 10-500 characters.");
  }
  if (channel === "stable" && canaryPercent !== 100) {
    throw new InternalError(422, "invalid_field", "stable channel requires canaryPercent=100.");
  }
  if (channel === "canary" && (canaryPercent <= 0 || canaryPercent >= 100)) {
    throw new InternalError(422, "invalid_field", "canary channel requires canaryPercent between 0 and 100.");
  }
  if (rollbackReleaseId === targetReleaseId) {
    throw new InternalError(422, "invalid_field", "rollbackReleaseId must differ from targetReleaseId.");
  }

  const currentRow = await database
    .prepare(`SELECT value FROM internal_meta WHERE key = ?1 LIMIT 1`)
    .bind(RELEASE_POLICY_META_KEY)
    .first<{ value: string }>();
  const current = parseReleasePolicy(
    currentRow ? { value: currentRow.value, updatedAt: 0 } : null,
  );
  if (current.version !== expectedVersion) {
    throw new InternalError(409, "version_conflict", "Release policy changed; refresh and retry.");
  }
  if (
    current.targetReleaseId === targetReleaseId &&
    current.rollbackReleaseId === rollbackReleaseId &&
    current.channel === channel &&
    current.canaryPercent === canaryPercent
  ) {
    throw new InternalError(409, "no_changes", "The desired release policy already has those settings.");
  }

  const now = Date.now();
  const version = expectedVersion + 1;
  const next = {
    schemaVersion: 1,
    version,
    targetReleaseId,
    rollbackReleaseId,
    channel,
    canaryPercent,
    reason,
    updatedBy: actor.emailNormalized,
    updatedAt: now,
    applicationMode: "configuration_only",
  };
  const statement = expectedVersion === 0
    ? database
        .prepare(
          `INSERT INTO internal_meta (key, value, updated_at)
           VALUES (?1, ?2, ?3)
           ON CONFLICT(key) DO NOTHING`,
        )
        .bind(RELEASE_POLICY_META_KEY, JSON.stringify(next), now)
    : database
        .prepare(
          `UPDATE internal_meta
           SET value = ?1, updated_at = ?2
           WHERE key = ?3
             AND CAST(json_extract(value, '$.version') AS INTEGER) = ?4`,
        )
        .bind(JSON.stringify(next), now, RELEASE_POLICY_META_KEY, expectedVersion);
  const results = await database.batch([
    statement,
    auditInsertAfterChange(database, {
      id: newId("iaud"),
      actor,
      action: "release_policy.update",
      targetType: "release_policy",
      targetId: "memory-api",
      requestId,
      metadata: {
        before: {
          version: current.version,
          targetReleaseId: current.targetReleaseId,
          rollbackReleaseId: current.rollbackReleaseId,
          channel: current.channel,
          canaryPercent: current.canaryPercent,
        },
        after: {
          version,
          targetReleaseId,
          rollbackReleaseId,
          channel,
          canaryPercent,
        },
        reason,
        applied: false,
        applicationMode: "configuration_only",
      },
      now,
    }),
  ]);
  if (statementChanges(results[0]) !== 1) {
    throw new InternalError(409, "version_conflict", "Release policy changed; refresh and retry.");
  }
  return {
    action: "release_policy.update",
    releasePolicy: parseReleasePolicy({ value: JSON.stringify(next), updatedAt: now }),
  };
}

async function requireInternalActor(
  database: D1Database,
  identity: InternalIdentity,
  allowInvited: boolean,
  bootstrapConfig: InternalBootstrapConfig,
): Promise<StaffRow> {
  await ensureInternalSchema(database);
  const email = normalizeIdentityEmail(identity.email);
  const now = Date.now();

  await database
    .prepare(
      `INSERT OR IGNORE INTO internal_meta (key, value, updated_at)
       SELECT ?1, 'preconfigured', ?2
       WHERE EXISTS (SELECT 1 FROM internal_staff)
         AND NOT EXISTS (SELECT 1 FROM internal_meta WHERE key = ?1)`,
    )
    .bind(BOOTSTRAP_META_KEY, now)
    .run();

  let actor = await findStaffByEmail(database, email);
  const bootstrapLock = await database
    .prepare(`SELECT value FROM internal_meta WHERE key = ?1 LIMIT 1`)
    .bind(BOOTSTRAP_META_KEY)
    .first<{ value: string }>();

  if (
    !actor &&
    !bootstrapLock &&
    isConfiguredBootstrapOwner(bootstrapConfig.ownerEmail, email)
  ) {
    await attemptOwnerBootstrap(database, identity, email, now);
    actor = await findStaffByEmail(database, email);
  }

  if (!actor || actor.status === "suspended") {
    throw new InternalError(
      403,
      "internal_access_denied",
      "Internal access is not available for this account.",
    );
  }
  if (actor.status === "invited" && !allowInvited) {
    throw new InternalError(
      403,
      "internal_invitation_pending",
      "Accept the internal staff invitation before continuing.",
    );
  }
  if (actor.status === "active" && (actor.lastSeenAt ?? 0) < now - 15 * 60_000) {
    const emailDisplay = identity.email.trim().slice(0, 254);
    await database
      .prepare(
        `UPDATE internal_staff
         SET email_display = ?1, last_seen_at = ?2, updated_at = ?2
         WHERE id = ?3 AND status = 'active'`,
      )
      .bind(emailDisplay, now, actor.id)
      .run();
    actor.emailDisplay = emailDisplay;
    actor.lastSeenAt = now;
    actor.updatedAt = now;
  }
  return actor;
}

async function attemptOwnerBootstrap(
  database: D1Database,
  identity: InternalIdentity,
  email: string,
  now: number,
) {
  const staffId = newId("istf");
  const auditId = newId("iaud");
  const displayName = cleanDisplayName(
    identity.fullName ?? identity.displayName,
    email,
  );
  const requestId = `bootstrap:${auditId}`;
  await database.batch([
    database
      .prepare(
        `INSERT OR IGNORE INTO internal_meta (key, value, updated_at)
         SELECT ?1, ?2, ?3
         WHERE NOT EXISTS (SELECT 1 FROM internal_staff)
           AND NOT EXISTS (SELECT 1 FROM internal_meta WHERE key = ?1)`,
      )
      .bind(BOOTSTRAP_META_KEY, email, now),
    database
      .prepare(
        `INSERT OR IGNORE INTO internal_staff (
           id, email_normalized, email_display, display_name,
           role, status, joined_at, last_seen_at, created_at, updated_at
         )
         SELECT ?1, ?2, ?3, ?4,
                'platform_owner', 'active', ?5, ?5, ?5, ?5
         WHERE (SELECT value FROM internal_meta WHERE key = ?6) = ?2
           AND NOT EXISTS (SELECT 1 FROM internal_staff)`,
      )
      .bind(
        staffId,
        email,
        identity.email.trim().slice(0, 254),
        displayName,
        now,
        BOOTSTRAP_META_KEY,
      ),
    database
      .prepare(
        `INSERT INTO internal_audit_logs (
           id, actor_staff_id, actor_email, actor_role,
           action, target_type, target_id, request_id, metadata_json, created_at
         )
         SELECT ?1, id, email_normalized, role,
                'platform.bootstrap', 'internal_staff', id, ?2, ?3, ?4
         FROM internal_staff
         WHERE id = ?5
           AND NOT EXISTS (
             SELECT 1 FROM internal_audit_logs
             WHERE action = 'platform.bootstrap' AND target_id = ?5
           )`,
      )
      .bind(
        auditId,
        requestId,
        JSON.stringify({ email, role: "platform_owner" }),
        now,
        staffId,
      ),
  ]);
}

async function acceptInvitation(
  database: D1Database,
  actor: StaffRow,
  identity: InternalIdentity,
  requestId: string,
) {
  const now = Date.now();
  const auditId = newId("iaud");
  const displayName = cleanDisplayName(
    identity.fullName ?? identity.displayName,
    actor.displayName,
  );
  const results = await database.batch([
    database
      .prepare(
        `UPDATE internal_staff
         SET status = 'active', email_display = ?1, display_name = ?2,
             joined_at = ?3, last_seen_at = ?3, updated_at = ?3
         WHERE id = ?4 AND status = 'invited'`,
      )
      .bind(identity.email.trim().slice(0, 254), displayName, now, actor.id),
    auditInsertAfterChange(database, {
      id: auditId,
      actor,
      action: "staff.accept_invite",
      targetType: "internal_staff",
      targetId: actor.id,
      requestId,
      metadata: { role: actor.role },
      now,
    }),
  ]);
  if (statementChanges(results[0]) !== 1) {
    throw new InternalError(409, "invitation_not_pending", "No pending invitation exists.");
  }
  return { action: "staff.accept_invite", staffId: actor.id, status: "active" as const };
}

async function addStaff(
  database: D1Database,
  actor: StaffRow,
  payload: Record<string, unknown>,
  requestId: string,
) {
  requireRole(actor.role, ["platform_owner", "platform_admin"]);
  const email = normalizeEmail(requiredString(payload.email, "email", 254));
  const role = parseRole(payload.role);
  if (actor.role === "platform_admin" && !["support", "analyst"].includes(role)) {
    throw new InternalError(403, "forbidden", "Platform admins cannot assign that role.");
  }
  const displayName = payload.displayName === undefined
    ? email
    : cleanDisplayName(requiredString(payload.displayName, "displayName", 100), email);
  const now = Date.now();
  const staffId = newId("istf");
  await database.batch([
    database
      .prepare(
        `INSERT INTO internal_staff (
           id, email_normalized, email_display, display_name, role, status,
           invited_by_staff_id, created_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, 'invited', ?6, ?7, ?7)`,
      )
      .bind(staffId, email, email, displayName, role, actor.id, now),
    auditInsert(database, {
      id: newId("iaud"), actor, action: "staff.add", targetType: "internal_staff",
      targetId: staffId, requestId, metadata: { email, role, status: "invited" }, now,
    }),
  ]);
  return { action: "staff.add", staffId, status: "invited" as const };
}

async function updateStaff(
  database: D1Database,
  actor: StaffRow,
  payload: Record<string, unknown>,
  requestId: string,
) {
  requireRole(actor.role, ["platform_owner", "platform_admin"]);
  const staffId = parseToken(payload.staffId, "staffId", 100);
  const target = await findStaffById(database, staffId);
  if (!target) throw new InternalError(404, "staff_not_found", "Staff member not found.");
  const role = payload.role === undefined ? undefined : parseRole(payload.role);
  const status = payload.status === undefined ? undefined : parseStatus(payload.status);
  if (!role && !status) {
    throw new InternalError(422, "invalid_payload", "role or status is required.");
  }
  if (status === "invited") {
    throw new InternalError(
      422,
      "invalid_staff_status_transition",
      "staff.update cannot set invitation status.",
    );
  }
  if (target.status === "invited" && status === "active") {
    throw new InternalError(
      409,
      "invitation_acceptance_required",
      "The invited staff member must accept the invitation.",
    );
  }
  if (
    actor.role === "platform_admin" &&
    (!["support", "analyst"].includes(target.role) ||
      (role !== undefined && !["support", "analyst"].includes(role)))
  ) {
    throw new InternalError(403, "forbidden", "Platform admins cannot modify that staff member.");
  }
  const nextRole = role ?? target.role;
  const nextStatus = status ?? target.status;
  if (nextRole === target.role && nextStatus === target.status) {
    throw new InternalError(409, "no_changes", "The staff member already has those settings.");
  }
  await requireAnotherActiveOwner(database, target, nextRole, nextStatus);
  const now = Date.now();
  const results = await database.batch([
    database
      .prepare(
        `UPDATE internal_staff
         SET role = ?1, status = ?2,
             joined_at = CASE
               WHEN ?2 = 'active' THEN COALESCE(joined_at, ?3)
               WHEN ?2 = 'invited' THEN NULL
               ELSE joined_at
             END,
             updated_at = ?3
         WHERE id = ?4`,
      )
      .bind(nextRole, nextStatus, now, target.id),
    auditInsertAfterChange(database, {
      id: newId("iaud"), actor, action: "staff.update", targetType: "internal_staff",
      targetId: target.id, requestId,
      metadata: {
        before: { role: target.role, status: target.status },
        after: { role: nextRole, status: nextStatus },
      },
      now,
    }),
  ]);
  if (statementChanges(results[0]) !== 1) {
    throw new InternalError(409, "staff_changed", "Staff member changed; refresh and retry.");
  }
  return { action: "staff.update", staffId: target.id, role: nextRole, status: nextStatus };
}

async function removeStaff(
  database: D1Database,
  actor: StaffRow,
  payload: Record<string, unknown>,
  requestId: string,
) {
  requireRole(actor.role, ["platform_owner", "platform_admin"]);
  const staffId = parseToken(payload.staffId, "staffId", 100);
  const confirmEmail = normalizeEmail(requiredString(payload.confirmEmail, "confirmEmail", 254));
  const target = await findStaffById(database, staffId);
  if (!target) throw new InternalError(404, "staff_not_found", "Staff member not found.");
  if (confirmEmail !== target.emailNormalized) {
    throw new InternalError(422, "confirmation_mismatch", "Confirmation email does not match.");
  }
  if (
    actor.role === "platform_admin" &&
    !["support", "analyst"].includes(target.role)
  ) {
    throw new InternalError(403, "forbidden", "Platform admins cannot remove that staff member.");
  }
  await requireAnotherActiveOwner(database, target, null, null);
  const now = Date.now();
  const results = await database.batch([
    database.prepare(`DELETE FROM internal_staff WHERE id = ?1`).bind(target.id),
    auditInsertAfterChange(database, {
      id: newId("iaud"), actor, action: "staff.remove", targetType: "internal_staff",
      targetId: target.id, requestId,
      metadata: { email: target.emailNormalized, role: target.role, status: target.status }, now,
    }),
  ]);
  if (statementChanges(results[0]) !== 1) {
    throw new InternalError(409, "staff_changed", "Staff member changed; refresh and retry.");
  }
  return { action: "staff.remove", staffId: target.id, removed: true };
}

async function setOrganizationStatus(
  database: D1Database,
  actor: StaffRow,
  payload: Record<string, unknown>,
  requestId: string,
) {
  requireRole(actor.role, ["platform_owner"]);
  const organizationId = parseToken(payload.organizationId, "organizationId", 100);
  const status = parseEnum(payload.status, "status", ["active", "archived"] as const);
  const confirmSlug = requiredString(payload.confirmSlug, "confirmSlug", 100);
  const reason = requiredString(payload.reason, "reason", 500);
  if (reason.length < 10) {
    throw new InternalError(422, "invalid_field", "reason must be 10-500 characters.");
  }
  const expectedVersion = parsePositiveInteger(payload.expectedVersion, "expectedVersion");
  const target = await database
    .prepare(
      `SELECT id, slug, status, version FROM organizations WHERE id = ?1 LIMIT 1`,
    )
    .bind(organizationId)
    .first<{ id: string; slug: string; status: "active" | "archived"; version: number }>();
  if (!target) throw new InternalError(404, "organization_not_found", "Organization not found.");
  if (target.slug !== confirmSlug) {
    throw new InternalError(422, "confirmation_mismatch", "Confirmation slug does not match.");
  }
  if (target.version !== expectedVersion) {
    throw new InternalError(409, "version_conflict", "Organization changed; refresh and retry.");
  }
  if (target.status === status) {
    throw new InternalError(409, "no_changes", "Organization already has that status.");
  }
  const now = Date.now();
  const auditId = newId("iaud");
  const results = await database.batch([
    database
      .prepare(
        `UPDATE organizations
         SET status = ?1, version = version + 1, updated_at = ?2
         WHERE id = ?3 AND version = ?4`,
      )
      .bind(status, now, target.id, expectedVersion),
    auditInsertAfterChange(database, {
      id: auditId, actor, action: "organization.set_status", targetType: "organization",
      targetId: target.id, requestId,
      metadata: {
        slug: target.slug,
        before: target.status,
        after: status,
        reason,
        expectedVersion,
        resultingVersion: expectedVersion + 1,
        activeApiKeysRevoked: status === "archived",
      },
      now,
    }),
    database
      .prepare(
        `UPDATE api_keys
         SET revoked_at = ?1
         WHERE organization_id = ?2
           AND revoked_at IS NULL
           AND ?3 = 'archived'
           AND EXISTS (SELECT 1 FROM internal_audit_logs WHERE id = ?4)`,
      )
      .bind(now, target.id, status, auditId),
  ]);
  if (statementChanges(results[0]) !== 1) {
    throw new InternalError(409, "version_conflict", "Organization changed; refresh and retry.");
  }
  return {
    action: "organization.set_status",
    organizationId: target.id,
    status,
    version: expectedVersion + 1,
    revokedApiKeys: statementChanges(results[2]),
  };
}

async function updateAccessRequest(
  database: D1Database,
  actor: StaffRow,
  payload: Record<string, unknown>,
  requestId: string,
) {
  requireRole(actor.role, ["platform_owner", "platform_admin", "support"]);
  const accessRequestId = parseToken(payload.requestId, "requestId", 100);
  const status = parseEnum(payload.status, "status", ACCESS_REQUEST_STATUS_VALUES);
  const reviewNote = optionalString(payload.reviewNote, "reviewNote", 2000);
  const expectedVersion = parsePositiveInteger(payload.expectedVersion, "expectedVersion");
  const target = await database
    .prepare(
      `SELECT id, status, review_note AS reviewNote, version
       FROM early_access_requests
       WHERE id = ?1
       LIMIT 1`,
    )
    .bind(accessRequestId)
    .first<{ id: string; status: AccessRequestRow["status"]; reviewNote: string; version: number }>();
  if (!target) {
    throw new InternalError(404, "access_request_not_found", "Pilot application not found.");
  }
  if (Number(target.version) !== expectedVersion) {
    throw new InternalError(409, "version_conflict", "Pilot application changed; refresh and retry.");
  }
  if (target.status === status && target.reviewNote === reviewNote) {
    throw new InternalError(409, "no_changes", "The pilot application already has those review settings.");
  }

  const now = Date.now();
  const results = await database.batch([
    database
      .prepare(
        `UPDATE early_access_requests
         SET status = ?1,
             review_note = ?2,
             last_reviewed_by = ?3,
             last_reviewed_at = ?4,
             version = version + 1,
             updated_at = ?4
         WHERE id = ?5 AND version = ?6`,
      )
      .bind(status, reviewNote, actor.emailDisplay, now, target.id, expectedVersion),
    auditInsertAfterChange(database, {
      id: newId("iaud"),
      actor,
      action: "access_request.update",
      targetType: "early_access_request",
      targetId: target.id,
      requestId,
      metadata: {
        before: { status: target.status, hasReviewNote: Boolean(target.reviewNote) },
        after: { status, hasReviewNote: Boolean(reviewNote) },
        expectedVersion,
        resultingVersion: expectedVersion + 1,
      },
      now,
    }),
  ]);
  if (statementChanges(results[0]) !== 1) {
    throw new InternalError(409, "version_conflict", "Pilot application changed; refresh and retry.");
  }
  return { action: "access_request.update", requestId: target.id, status, version: expectedVersion + 1 };
}

async function consumeMutation(database: D1Database, staffId: string) {
  const now = Date.now();
  const bucketStart = Math.floor(now / 60_000) * 60_000;
  const row = await database
    .prepare(
      `INSERT INTO internal_action_limits (
         actor_staff_id, bucket_start, mutation_count, updated_at
       ) VALUES (?1, ?2, 1, ?3)
       ON CONFLICT(actor_staff_id, bucket_start) DO UPDATE SET
         mutation_count = internal_action_limits.mutation_count + 1,
         updated_at = excluded.updated_at
       RETURNING mutation_count AS mutationCount`,
    )
    .bind(staffId, bucketStart, now)
    .first<{ mutationCount: number }>();
  if (!row || Number(row.mutationCount) > MUTATIONS_PER_MINUTE) {
    throw new InternalError(429, "rate_limited", "Too many internal mutations; retry next minute.");
  }
}

async function getOrganizationDetail(
  database: D1Database,
  organization: ReturnType<typeof normalizeOrganization>,
  includeSensitiveDetails: boolean,
) {
  const membersPromise = includeSensitiveDetails
    ? database
        .prepare(
          `SELECT
             m.user_id AS id, u.email_display AS email, u.display_name AS displayName,
             m.role, m.status, m.joined_at AS joinedAt, m.created_at AS createdAt
           FROM organization_members m
           JOIN users u ON u.id = m.user_id
           WHERE m.organization_id = ?1
           ORDER BY m.created_at DESC
           LIMIT 250`,
        )
        .bind(organization.id)
        .all()
    : Promise.resolve({ results: [] as Record<string, unknown>[] });
  const agentsPromise = database
    .prepare(
      `SELECT
         a.id, a.organization_id AS organizationId, a.name, a.slug, a.status, a.version,
         a.created_at AS createdAt, a.updated_at AS updatedAt,
         (SELECT COUNT(*) FROM memory_events e WHERE e.agent_id = a.id) AS eventCount,
         (SELECT COUNT(*) FROM memory_event_edges edge WHERE edge.agent_id = a.id) AS edgeCount
       FROM agents a
       WHERE a.organization_id = ?1
       ORDER BY a.updated_at DESC, a.id
       LIMIT 250`,
    )
    .bind(organization.id)
    .all();
  const apiKeysPromise = includeSensitiveDetails
    ? database
        .prepare(
          `SELECT
             id, name, token_prefix AS tokenPrefix, scopes_json AS scopesJson,
             created_at AS createdAt, expires_at AS expiresAt,
             last_used_at AS lastUsedAt, revoked_at AS revokedAt
           FROM api_keys
           WHERE organization_id = ?1
           ORDER BY created_at DESC, id DESC
           LIMIT 100`,
        )
        .bind(organization.id)
        .all()
    : Promise.resolve({ results: [] as Record<string, unknown>[] });
  const recentEventsPromise = includeSensitiveDetails
    ? database
        .prepare(
          `SELECT
             id, agent_id AS agentId, event_type AS eventType, source,
             occurred_at AS occurredAt, created_at AS createdAt,
             CASE WHEN redacted_at IS NULL THEN 0 ELSE 1 END AS redacted
           FROM memory_events
           WHERE organization_id = ?1
           ORDER BY created_at DESC, id DESC
           LIMIT 50`,
        )
        .bind(organization.id)
        .all()
    : Promise.resolve({ results: [] as Record<string, unknown>[] });
  const [members, agents, apiKeys, recentEvents] = await Promise.all([
    membersPromise,
    agentsPromise,
    apiKeysPromise,
    recentEventsPromise,
  ]);
  const baseDetail = {
    ...organization,
    agents: agents.results.map((row) => ({
      ...normalizeRowNumbers(row),
      organizationName: organization.name,
    })),
  };
  if (!includeSensitiveDetails) return baseDetail;
  return {
    ...baseDetail,
    members: members.results,
    apiKeys: apiKeys.results.map((row) => {
      const record = row as Record<string, unknown>;
      return {
        ...record,
        scopes: parseStringArray(record.scopesJson),
        scopesJson: undefined,
      };
    }),
    recentEvents: recentEvents.results,
  };
}

async function getOrganizationSummary(
  database: D1Database,
  organizationId: string,
  now: number,
) {
  return database
    .prepare(
      `SELECT
         o.id, o.name, o.slug, o.status, o.sample_mode AS sampleMode,
         o.version, o.created_at AS createdAt, o.updated_at AS updatedAt,
         (SELECT COUNT(*) FROM organization_members m WHERE m.organization_id = o.id) AS memberCount,
         (SELECT COUNT(*) FROM organization_members m WHERE m.organization_id = o.id AND m.status = 'active') AS activeMemberCount,
         (SELECT COUNT(*) FROM agents a WHERE a.organization_id = o.id) AS agentCount,
         (SELECT COUNT(*) FROM memory_events e WHERE e.organization_id = o.id) AS eventCount,
         (SELECT COUNT(*) FROM memory_event_edges edge WHERE edge.organization_id = o.id) AS edgeCount,
         (SELECT COUNT(*) FROM api_keys k WHERE k.organization_id = o.id) AS apiKeyCount,
         (SELECT COUNT(*) FROM api_keys k WHERE k.organization_id = o.id AND k.revoked_at IS NULL
            AND (k.expires_at IS NULL OR k.expires_at > ?2)) AS activeApiKeyCount,
         MAX(
           o.updated_at,
           COALESCE((SELECT MAX(a.updated_at) FROM agents a WHERE a.organization_id = o.id), 0),
           COALESCE((SELECT MAX(e.created_at) FROM memory_events e WHERE e.organization_id = o.id), 0),
           COALESCE((SELECT MAX(edge.created_at) FROM memory_event_edges edge WHERE edge.organization_id = o.id), 0),
           COALESCE((SELECT MAX(COALESCE(k.last_used_at, k.created_at)) FROM api_keys k WHERE k.organization_id = o.id), 0)
         ) AS lastActivity
       FROM organizations o
       WHERE o.id = ?1
       LIMIT 1`,
    )
    .bind(organizationId, now)
    .first<OrganizationRow>();
}

async function findStaffByEmail(database: D1Database, email: string) {
  return database
    .prepare(
      `SELECT
         id, email_normalized AS emailNormalized, email_display AS emailDisplay,
         display_name AS displayName, role, status,
         invited_by_staff_id AS invitedByStaffId, joined_at AS joinedAt,
         last_seen_at AS lastSeenAt, created_at AS createdAt, updated_at AS updatedAt
       FROM internal_staff WHERE email_normalized = ?1 LIMIT 1`,
    )
    .bind(email)
    .first<StaffRow>();
}

async function findStaffById(database: D1Database, staffId: string) {
  return database
    .prepare(
      `SELECT
         id, email_normalized AS emailNormalized, email_display AS emailDisplay,
         display_name AS displayName, role, status,
         invited_by_staff_id AS invitedByStaffId, joined_at AS joinedAt,
         last_seen_at AS lastSeenAt, created_at AS createdAt, updated_at AS updatedAt
       FROM internal_staff WHERE id = ?1 LIMIT 1`,
    )
    .bind(staffId)
    .first<StaffRow>();
}

type AuditInput = {
  id: string;
  actor: StaffRow;
  action: string;
  targetType: string;
  targetId: string;
  requestId: string;
  metadata: Record<string, unknown>;
  now: number;
};

function auditInsert(database: D1Database, input: AuditInput) {
  return database
    .prepare(
      `INSERT INTO internal_audit_logs (
         id, actor_staff_id, actor_email, actor_role, action,
         target_type, target_id, request_id, metadata_json, created_at
       ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)`,
    )
    .bind(
      input.id, input.actor.id, input.actor.emailNormalized, input.actor.role,
      input.action, input.targetType, input.targetId, input.requestId,
      JSON.stringify(input.metadata), input.now,
    );
}

function auditInsertAfterChange(database: D1Database, input: AuditInput) {
  return database
    .prepare(
      `INSERT INTO internal_audit_logs (
         id, actor_staff_id, actor_email, actor_role, action,
         target_type, target_id, request_id, metadata_json, created_at
       )
       SELECT ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10
       WHERE changes() = 1`,
    )
    .bind(
      input.id, input.actor.id, input.actor.emailNormalized, input.actor.role,
      input.action, input.targetType, input.targetId, input.requestId,
      JSON.stringify(input.metadata), input.now,
    );
}

function requireRole(current: InternalRole, allowed: InternalRole[]) {
  if (!allowed.includes(current)) {
    throw new InternalError(403, "forbidden", "You do not have permission for this action.");
  }
}

function parseRole(value: unknown): InternalRole {
  return parseEnum(value, "role", ROLE_VALUES);
}

function parseStatus(value: unknown): InternalStaffStatus {
  return parseEnum(value, "status", STATUS_VALUES);
}

function asObject(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new InternalError(400, "invalid_payload", `${field} must be an object.`);
  }
  return value as Record<string, unknown>;
}

function requiredString(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== "string") {
    throw new InternalError(422, "invalid_field", `${field} must be a string.`);
  }
  const clean = value.trim();
  if (!clean || clean.length > maxLength) {
    throw new InternalError(422, "invalid_field", `${field} is invalid.`);
  }
  return clean;
}

function optionalString(value: unknown, field: string, maxLength: number): string {
  if (value === undefined || value === null) return "";
  if (typeof value !== "string") {
    throw new InternalError(422, "invalid_field", `${field} must be a string.`);
  }
  const clean = value.trim();
  if (clean.length > maxLength) {
    throw new InternalError(422, "invalid_field", `${field} is too long.`);
  }
  return clean;
}

function optionalToken(value: unknown, field: string, maxLength: number): string | null {
  if (value === undefined || value === null || value === "") return null;
  return parseToken(value, field, maxLength);
}

function parseEnum<const T extends readonly string[]>(
  value: unknown,
  field: string,
  allowed: T,
): T[number] {
  if (typeof value !== "string" || !allowed.includes(value)) {
    throw new InternalError(422, "invalid_field", `${field} is invalid.`);
  }
  return value as T[number];
}

function parseToken(value: unknown, field: string, maxLength: number): string {
  const token = requiredString(value, field, maxLength);
  if (!/^[A-Za-z0-9._:-]+$/.test(token)) {
    throw new InternalError(422, "invalid_field", `${field} contains unsupported characters.`);
  }
  return token;
}

function parsePositiveInteger(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
    throw new InternalError(422, "invalid_field", `${field} must be a positive integer.`);
  }
  return value;
}

function parseNonNegativeInteger(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new InternalError(422, "invalid_field", `${field} must be a non-negative integer.`);
  }
  return value;
}

function parsePercentage(value: unknown, field: string): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < 0 ||
    value > 100 ||
    Math.round(value * 100) !== value * 100
  ) {
    throw new InternalError(422, "invalid_field", `${field} must be between 0 and 100 with at most two decimal places.`);
  }
  return value;
}

function normalizeIdentityEmail(value: string): string {
  try {
    return normalizeEmail(value);
  } catch {
    throw new InternalError(
      403,
      "internal_access_denied",
      "Internal access is not available for this account.",
    );
  }
}

function normalizeEmail(value: string): string {
  const email = value.trim().toLowerCase();
  if (email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    throw new InternalError(422, "invalid_email", "A valid email address is required.");
  }
  return email;
}

function cleanDisplayName(value: string, fallback: string): string {
  const clean = value.trim().replace(/\s+/g, " ").slice(0, 100);
  return clean || fallback;
}

function cleanRequestId(value: string): string {
  const clean = value.trim().slice(0, 128);
  return clean || crypto.randomUUID();
}

function publicStaff(staff: StaffRow) {
  return {
    id: staff.id,
    email: staff.emailDisplay,
    displayName: staff.displayName,
    role: staff.role,
    status: staff.status,
    invitedByStaffId: staff.invitedByStaffId,
    joinedAt: staff.joinedAt,
    lastSeenAt: staff.lastSeenAt,
    createdAt: staff.createdAt,
    updatedAt: staff.updatedAt,
  };
}

function normalizeOrganization(row: OrganizationRow) {
  return {
    ...row,
    sampleMode: Number(row.sampleMode) === 1,
    memberCount: Number(row.memberCount),
    activeMemberCount: Number(row.activeMemberCount),
    agentCount: Number(row.agentCount),
    eventCount: Number(row.eventCount),
    edgeCount: Number(row.edgeCount),
    memoryEventCount: Number(row.eventCount),
    memoryEdgeCount: Number(row.edgeCount),
    apiKeyCount: Number(row.apiKeyCount),
    activeApiKeyCount: Number(row.activeApiKeyCount),
    lastActivity: Number(row.lastActivity),
    version: Number(row.version),
  };
}

function normalizeNumbers(value: Record<string, number>) {
  return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, Number(entry)]));
}

function normalizeRowNumbers(row: Record<string, unknown>) {
  return {
    ...row,
    eventCount: Number(row.eventCount ?? 0),
    edgeCount: Number(row.edgeCount ?? 0),
    version: Number(row.version ?? 0),
  };
}

function parseJsonObject(value: unknown): Record<string, unknown> {
  if (typeof value !== "string") return {};
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

function parseReleasePolicy(
  row: { value: string; updatedAt: number } | null | undefined,
): InternalReleasePolicy {
  const unavailable: InternalReleasePolicy = {
    availability: "unavailable",
    applicationMode: "configuration_only",
    applied: false,
    version: 0,
    targetReleaseId: null,
    rollbackReleaseId: null,
    channel: null,
    canaryPercent: null,
    reason: null,
    updatedAt: null,
    updatedBy: null,
  };
  if (!row) return unavailable;
  const value = parseJsonObject(row.value);
  const version = Number(value.version);
  const canaryPercent = Number(value.canaryPercent);
  const channel = value.channel;
  if (
    value.schemaVersion !== 1 ||
    !Number.isSafeInteger(version) ||
    version < 1 ||
    (channel !== "stable" && channel !== "canary") ||
    !Number.isFinite(canaryPercent) ||
    canaryPercent < 0 ||
    canaryPercent > 100 ||
    typeof value.targetReleaseId !== "string" ||
    !/^[A-Za-z0-9._:-]{1,128}$/.test(value.targetReleaseId)
  ) {
    return unavailable;
  }
  const rollbackReleaseId =
    typeof value.rollbackReleaseId === "string" &&
    /^[A-Za-z0-9._:-]{1,128}$/.test(value.rollbackReleaseId)
      ? value.rollbackReleaseId
      : null;
  return {
    availability: "available",
    applicationMode: "configuration_only",
    applied: false,
    version,
    targetReleaseId: value.targetReleaseId,
    rollbackReleaseId,
    channel,
    canaryPercent,
    reason: typeof value.reason === "string" ? value.reason.slice(0, 500) : null,
    updatedAt:
      typeof value.updatedAt === "number" && Number.isFinite(value.updatedAt)
        ? value.updatedAt
        : Number(row.updatedAt) || null,
    updatedBy:
      typeof value.updatedBy === "string" ? value.updatedBy.slice(0, 254) : null,
  };
}

function parseStringArray(value: unknown): string[] {
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

function fillUsageDays(rows: { day: string; writes: number }[]) {
  const counts = new Map(rows.map((row) => [row.day, Number(row.writes)]));
  const start = startOfUtcDay(Date.now() - 6 * DAY_MS);
  return Array.from({ length: 7 }, (_, index) => {
    const date = new Date(start + index * DAY_MS).toISOString().slice(0, 10);
    return { date, writes: counts.get(date) ?? 0 };
  });
}

function startOfUtcDay(value: number): number {
  const date = new Date(value);
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
}

function statementChanges(result: D1Result<unknown>): number {
  return Number(result.meta.changes ?? 0);
}

async function requireAnotherActiveOwner(
  database: D1Database,
  target: StaffRow,
  nextRole: InternalRole | null,
  nextStatus: InternalStaffStatus | null,
) {
  const removesActiveOwner =
    target.role === "platform_owner" &&
    target.status === "active" &&
    (nextRole !== "platform_owner" || nextStatus !== "active");
  if (!removesActiveOwner) return;
  const row = await database
    .prepare(
      `SELECT COUNT(*) AS ownerCount
       FROM internal_staff
       WHERE role = 'platform_owner' AND status = 'active'`,
    )
    .first<{ ownerCount: number }>();
  if (Number(row?.ownerCount ?? 0) <= 1) {
    throw new InternalError(
      409,
      "last_platform_owner",
      "At least one active platform owner is required.",
    );
  }
}

function newId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID().replace(/-/g, "")}`;
}

function mapDatabaseError(error: unknown): Error {
  if (error instanceof InternalError) return error;
  const message = databaseErrorText(error);
  if (message.includes("internal_last_platform_owner")) {
    return new InternalError(409, "last_platform_owner", "At least one active platform owner is required.");
  }
  if (message.includes("internal_bootstrap_locked")) {
    return new InternalError(409, "bootstrap_locked", "Platform bootstrap is permanently locked.");
  }
  if (message.includes("internal_staff.email_normalized") || message.includes("UNIQUE constraint failed")) {
    return new InternalError(409, "conflict", "A matching internal record already exists.");
  }
  if (message.includes("FOREIGN KEY constraint failed")) {
    return new InternalError(409, "related_record_conflict", "A related record changed; refresh and retry.");
  }
  return error instanceof Error ? error : new Error("Unexpected internal database error");
}

function databaseErrorText(value: unknown, depth = 0): string {
  if (depth > 4 || value === null || value === undefined) return "";
  if (value instanceof Error) {
    return [
      value.name,
      value.message,
      value.stack ?? "",
      databaseErrorText(value.cause, depth + 1),
    ].join("\n");
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    return ["message", "cause", "stack", "code"]
      .map((key) => databaseErrorText(record[key], depth + 1))
      .join("\n");
  }
  return String(value);
}
