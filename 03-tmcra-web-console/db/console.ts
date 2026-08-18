import { getD1 } from "./index";

export type ConsoleRole = "owner" | "admin" | "developer" | "viewer";
export type AccountType = "personal" | "enterprise";

export type ConsoleIdentity = {
  email: string;
  displayName: string;
  fullName: string | null;
};

export class ConsoleError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ConsoleError";
    this.status = status;
    this.code = code;
  }
}

type ActorRow = {
  id: string;
  emailNormalized: string;
  emailDisplay: string;
  displayName: string;
  bootstrapCompletedAt: number | null;
};

type AccountProfileRow = {
  accountType: AccountType | null;
  status: "active" | "suspended";
  selectedAt: number | null;
};

type PersonalMemorySpaceRow = {
  id: string;
  userId: string;
  scopeName: string;
  displayName: string;
  status: "active" | "deleting" | "deleted";
};

type PersonalApiKeyRow = {
  personalSpaceId: string;
  tokenId: string;
  tokenPrefix: string;
  permissionsJson: string;
  name: string;
  status: "active" | "revoked" | "expired";
  expiresAt: number;
  createdAt: number;
  revokedAt: number | null;
};

type PersonalIntegrationRow = {
  id: string;
  personalSpaceId: string;
  platform: "codex" | "openclaw" | "hermes" | "claude_code" | "deepseek_harness";
  installationFingerprint: string;
  displayName: string;
  status: "detected" | "configured" | "connected" | "attention_required" | "disconnected";
  health: "unknown" | "healthy" | "degraded" | "failed";
  capabilitiesJson: string;
  clientVersion: string | null;
  integrationVersion: string | null;
  lastErrorCode: string | null;
  lastSeenAt: number;
  lastHealthyAt: number | null;
  createdAt: number;
  updatedAt: number;
  disconnectedAt: number | null;
  version: number;
};

type MembershipRow = {
  organizationId: string;
  organizationName: string;
  organizationSlug: string;
  organizationStatus: string;
  sampleMode: number;
  role: ConsoleRole;
  membershipStatus: string;
};

type AgentRow = {
  id: string;
  organizationId: string;
  name: string;
  slug: string;
  description: string;
  status: "active" | "paused" | "archived";
  version: number;
  createdAt: number;
  updatedAt: number;
  archivedAt: number | null;
  eventCount?: number;
  lastEventAt?: number | null;
};

type ApiKeyRow = {
  id: string;
  organizationId: string;
  organizationStatus: string;
  name: string;
  tokenPrefix: string;
  secretHash: string;
  hashVersion: number;
  scopesJson: string;
  createdAt: number;
  expiresAt: number | null;
  lastUsedAt: number | null;
  revokedAt: number | null;
};

type AuditInput = {
  organizationId: string;
  actorUserId: string;
  action: string;
  targetType: string;
  targetId: string;
  requestId: string;
  metadata?: Record<string, unknown>;
};

type AuditSnapshotRow = {
  id: string;
  action: string;
  targetType: string;
  targetId: string;
  metadataJson: string;
  requestId: string;
  createdAt: number;
  actorName: string;
  actorType: string;
};

const ROLE_ORDER: Record<ConsoleRole, number> = {
  viewer: 0,
  developer: 1,
  admin: 2,
  owner: 3,
};

const API_KEY_SCOPES = new Set([
  "agents:read",
  "agents:write",
  "memory:read",
  "memory:write",
]);

const SCHEMA_STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000)
  )`,
  `CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email_normalized TEXT NOT NULL UNIQUE,
    email_display TEXT NOT NULL,
    display_name TEXT NOT NULL,
    bootstrap_completed_at INTEGER,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    last_seen_at INTEGER
  )`,
  `CREATE TABLE IF NOT EXISTS account_profiles (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    account_type TEXT CHECK (account_type IS NULL OR account_type IN ('personal', 'enterprise')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    selected_at INTEGER,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000)
  )`,
  `CREATE INDEX IF NOT EXISTS account_profiles_type_status_idx
    ON account_profiles(account_type, status, updated_at DESC)`,
  `CREATE TABLE IF NOT EXISTS personal_memory_spaces (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    scope_name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deleting', 'deleted')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000)
  )`,
  `CREATE INDEX IF NOT EXISTS personal_memory_spaces_status_updated_idx
    ON personal_memory_spaces(status, updated_at DESC)`,
  `CREATE TABLE IF NOT EXISTS personal_integrations (
    id TEXT PRIMARY KEY,
    personal_space_id TEXT NOT NULL REFERENCES personal_memory_spaces(id) ON DELETE CASCADE,
    platform TEXT NOT NULL CHECK (platform IN ('codex', 'openclaw', 'hermes', 'claude_code', 'deepseek_harness')),
    installation_fingerprint TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'detected' CHECK (status IN ('detected', 'configured', 'connected', 'attention_required', 'disconnected')),
    health TEXT NOT NULL DEFAULT 'unknown' CHECK (health IN ('unknown', 'healthy', 'degraded', 'failed')),
    capabilities_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(capabilities_json)),
    client_version TEXT,
    integration_version TEXT,
    last_error_code TEXT,
    last_seen_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    last_healthy_at INTEGER,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    disconnected_at INTEGER,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    UNIQUE (personal_space_id, platform, installation_fingerprint)
  )`,
  `CREATE INDEX IF NOT EXISTS personal_integrations_space_status_idx
    ON personal_integrations(personal_space_id, status, updated_at DESC)`,
  `CREATE TABLE IF NOT EXISTS chat_provider_receipt_outbox (
    id TEXT PRIMARY KEY,
    personal_space_id TEXT NOT NULL REFERENCES personal_memory_spaces(id) ON DELETE CASCADE,
    scope_name TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'blocked')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    last_attempt_at INTEGER,
    last_error_code TEXT,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    completed_at INTEGER
  )`,
  `CREATE INDEX IF NOT EXISTS chat_provider_receipt_outbox_due_idx
    ON chat_provider_receipt_outbox(status, next_attempt_at)`,
  `CREATE INDEX IF NOT EXISTS chat_provider_receipt_outbox_space_idx
    ON chat_provider_receipt_outbox(personal_space_id, created_at DESC)`,
  `CREATE TABLE IF NOT EXISTS personal_api_keys (
    personal_space_id TEXT NOT NULL REFERENCES personal_memory_spaces(id) ON DELETE CASCADE,
    token_id TEXT PRIMARY KEY,
    token_prefix TEXT NOT NULL,
    permissions_json TEXT NOT NULL CHECK (json_valid(permissions_json)),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked', 'expired')),
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    revoked_at INTEGER
  )`,
  `CREATE INDEX IF NOT EXISTS personal_api_keys_space_status_idx
    ON personal_api_keys(personal_space_id, status, token_id)`,
  `CREATE TABLE IF NOT EXISTS device_authorizations (
    id TEXT PRIMARY KEY,
    device_code_hash TEXT NOT NULL UNIQUE,
    user_code_hash TEXT NOT NULL UNIQUE,
    code_challenge TEXT NOT NULL,
    code_challenge_method TEXT NOT NULL DEFAULT 'S256' CHECK (code_challenge_method = 'S256'),
    provider TEXT NOT NULL DEFAULT 'codex' CHECK (provider IN ('codex', 'deepseek_harness')),
    client_name TEXT NOT NULL DEFAULT 'Codex',
    source_hash TEXT NOT NULL DEFAULT 'legacy',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'authorizing', 'approved', 'denied', 'claimed', 'expired')),
    interval_seconds INTEGER NOT NULL DEFAULT 5 CHECK (interval_seconds BETWEEN 1 AND 60),
    poll_count INTEGER NOT NULL DEFAULT 0,
    last_polled_at INTEGER,
    approved_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    personal_space_id TEXT REFERENCES personal_memory_spaces(id) ON DELETE SET NULL,
    token_ciphertext TEXT,
    token_iv TEXT,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    expires_at INTEGER NOT NULL,
    approved_at INTEGER,
    claimed_at INTEGER,
    issuance_request_id TEXT,
    delivery_receipt_hash TEXT,
    CHECK ((token_ciphertext IS NULL) = (token_iv IS NULL))
  )`,
  `CREATE INDEX IF NOT EXISTS device_authorizations_status_expires_idx
    ON device_authorizations(status, expires_at)`,
  `CREATE INDEX IF NOT EXISTS device_authorizations_space_created_idx
    ON device_authorizations(personal_space_id, created_at DESC)`,
  `CREATE TABLE IF NOT EXISTS device_flow_rate_limits (
    limit_key TEXT NOT NULL,
    bucket_start INTEGER NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 1 CHECK (request_count > 0),
    last_admission_id TEXT NOT NULL,
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    PRIMARY KEY (limit_key, bucket_start)
  )`,
  `CREATE INDEX IF NOT EXISTS device_flow_rate_limits_bucket_idx
    ON device_flow_rate_limits(bucket_start)`,
  `CREATE TABLE IF NOT EXISTS device_connections (
    id TEXT PRIMARY KEY,
    authorization_id TEXT NOT NULL UNIQUE REFERENCES device_authorizations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    personal_space_id TEXT NOT NULL REFERENCES personal_memory_spaces(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'codex' CHECK (provider IN ('codex', 'deepseek_harness')),
    display_name TEXT NOT NULL DEFAULT 'Codex',
    token_id TEXT NOT NULL UNIQUE,
    token_prefix TEXT NOT NULL,
    scope_prefix TEXT NOT NULL,
    permissions_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(permissions_json)),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked', 'expired')),
    token_expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    last_connected_at INTEGER,
    revoked_at INTEGER
  )`,
  `CREATE INDEX IF NOT EXISTS device_connections_space_status_idx
    ON device_connections(personal_space_id, status, created_at DESC)`,
  `CREATE INDEX IF NOT EXISTS device_connections_user_status_idx
    ON device_connections(user_id, status, created_at DESC)`,
  `CREATE TABLE IF NOT EXISTS device_revocation_outbox (
    id TEXT PRIMARY KEY,
    token_id TEXT NOT NULL UNIQUE,
    connection_id TEXT REFERENCES device_connections(id) ON DELETE SET NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    last_attempt_at INTEGER,
    last_error_code TEXT,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    completed_at INTEGER
  )`,
  `CREATE INDEX IF NOT EXISTS device_revocation_outbox_due_idx
    ON device_revocation_outbox(status, next_attempt_at)`,
  `CREATE INDEX IF NOT EXISTS device_revocation_outbox_connection_idx
    ON device_revocation_outbox(connection_id)`,
  `CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    sample_mode INTEGER NOT NULL DEFAULT 0 CHECK (sample_mode IN (0, 1)),
    bootstrap_owner_user_id TEXT REFERENCES users(id) ON DELETE RESTRICT,
    created_by_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000)
  )`,
  `CREATE UNIQUE INDEX IF NOT EXISTS organizations_bootstrap_owner_uq
    ON organizations(bootstrap_owner_user_id)
    WHERE bootstrap_owner_user_id IS NOT NULL`,
  `CREATE TABLE IF NOT EXISTS organization_members (
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'developer', 'viewer')),
    status TEXT NOT NULL DEFAULT 'invited' CHECK (status IN ('invited', 'active', 'suspended')),
    invited_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    joined_at INTEGER,
    version INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    PRIMARY KEY (organization_id, user_id)
  )`,
  `CREATE INDEX IF NOT EXISTS organization_members_user_status_idx
    ON organization_members(user_id, status, organization_id)`,
  `CREATE INDEX IF NOT EXISTS organization_members_org_status_role_idx
    ON organization_members(organization_id, status, role)`,
  `CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'archived')),
    version INTEGER NOT NULL DEFAULT 1,
    created_by_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    archived_at INTEGER,
    UNIQUE (organization_id, slug),
    UNIQUE (organization_id, id)
  )`,
  `CREATE INDEX IF NOT EXISTS agents_org_status_updated_idx
    ON agents(organization_id, status, updated_at DESC, id)`,
  `CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    token_prefix TEXT NOT NULL,
    secret_hash TEXT NOT NULL,
    hash_version INTEGER NOT NULL DEFAULT 1,
    scopes_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(scopes_json)),
    created_by_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    expires_at INTEGER,
    last_used_at INTEGER,
    revoked_at INTEGER,
    revoked_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL
  )`,
  `CREATE INDEX IF NOT EXISTS api_keys_org_revoked_created_idx
    ON api_keys(organization_id, revoked_at, created_at DESC, id)`,
  `CREATE TABLE IF NOT EXISTS memory_events (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    content_text TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json)),
    source TEXT NOT NULL DEFAULT 'console',
    idempotency_key TEXT,
    occurred_at INTEGER NOT NULL,
    created_by_type TEXT NOT NULL DEFAULT 'user' CHECK (created_by_type IN ('user', 'api_key', 'system')),
    created_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_by_api_key_id TEXT REFERENCES api_keys(id) ON DELETE SET NULL,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    redacted_at INTEGER,
    UNIQUE (organization_id, agent_id, id),
    FOREIGN KEY (organization_id, agent_id)
      REFERENCES agents(organization_id, id) ON DELETE CASCADE
  )`,
  `CREATE UNIQUE INDEX IF NOT EXISTS memory_events_agent_idempotency_uq
    ON memory_events(agent_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL`,
  `CREATE INDEX IF NOT EXISTS memory_events_org_agent_occurred_idx
    ON memory_events(organization_id, agent_id, occurred_at DESC, id DESC)`,
  `CREATE INDEX IF NOT EXISTS memory_events_org_created_idx
    ON memory_events(organization_id, created_at DESC, id DESC)`,
  `CREATE TABLE IF NOT EXISTS memory_event_edges (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    target_event_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    UNIQUE (agent_id, source_event_id, target_event_id, relation),
    FOREIGN KEY (organization_id, agent_id)
      REFERENCES agents(organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, agent_id, source_event_id)
      REFERENCES memory_events(organization_id, agent_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, agent_id, target_event_id)
      REFERENCES memory_events(organization_id, agent_id, id) ON DELETE CASCADE
  )`,
  `CREATE INDEX IF NOT EXISTS memory_event_edges_org_agent_idx
    ON memory_event_edges(organization_id, agent_id, created_at DESC, id)`,
  `CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('user', 'api_key', 'system')),
    actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    actor_api_key_id TEXT REFERENCES api_keys(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json)),
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000)
  )`,
  `CREATE INDEX IF NOT EXISTS audit_logs_org_created_idx
    ON audit_logs(organization_id, created_at DESC, id DESC)`,
  `CREATE INDEX IF NOT EXISTS audit_logs_org_target_created_idx
    ON audit_logs(organization_id, target_type, target_id, created_at DESC)`,
  `CREATE INDEX IF NOT EXISTS audit_logs_org_actor_created_idx
    ON audit_logs(organization_id, actor_user_id, created_at DESC)`,
  `CREATE TABLE IF NOT EXISTS early_access_requests (
    id TEXT PRIMARY KEY,
    email_normalized TEXT NOT NULL,
    email_display TEXT NOT NULL,
    contact_name TEXT NOT NULL DEFAULT '',
    company_name TEXT NOT NULL DEFAULT '',
    industry TEXT NOT NULL DEFAULT '',
    company_size TEXT NOT NULL DEFAULT '',
    primary_use_case TEXT NOT NULL DEFAULT '',
    platforms_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(platforms_json)),
    timeline TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'website',
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'contacted', 'qualified', 'closed')),
    review_note TEXT NOT NULL DEFAULT '',
    last_reviewed_by TEXT,
    last_reviewed_at INTEGER,
    version INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000)
  )`,
  `CREATE UNIQUE INDEX IF NOT EXISTS early_access_requests_email_uq
    ON early_access_requests(email_normalized)`,
  `CREATE INDEX IF NOT EXISTS early_access_requests_status_created_idx
    ON early_access_requests(status, created_at)`,
  `CREATE TRIGGER IF NOT EXISTS organization_members_keep_last_owner_delete
    BEFORE DELETE ON organization_members
    WHEN OLD.role = 'owner' AND OLD.status = 'active'
      AND NOT EXISTS (
        SELECT 1 FROM organization_members AS other
        WHERE other.organization_id = OLD.organization_id
          AND other.user_id <> OLD.user_id
          AND other.role = 'owner'
          AND other.status = 'active'
      )
    BEGIN
      SELECT RAISE(ABORT, 'last_active_owner');
    END`,
  `CREATE TRIGGER IF NOT EXISTS organization_members_keep_last_owner_update
    BEFORE UPDATE OF role, status ON organization_members
    WHEN OLD.role = 'owner' AND OLD.status = 'active'
      AND (NEW.role <> 'owner' OR NEW.status <> 'active')
      AND NOT EXISTS (
        SELECT 1 FROM organization_members AS other
        WHERE other.organization_id = OLD.organization_id
          AND other.user_id <> OLD.user_id
          AND other.role = 'owner'
          AND other.status = 'active'
      )
    BEGIN
      SELECT RAISE(ABORT, 'last_active_owner');
    END`,
  `CREATE TRIGGER IF NOT EXISTS account_profiles_personal_no_active_enterprise
    BEFORE UPDATE OF account_type ON account_profiles
    WHEN NEW.account_type = 'personal'
      AND EXISTS (
        SELECT 1 FROM organization_members
        WHERE user_id = NEW.user_id AND status = 'active'
      )
    BEGIN
      SELECT RAISE(ABORT, 'personal_account_has_enterprise_membership');
    END`,
  `CREATE TRIGGER IF NOT EXISTS account_profiles_enterprise_no_personal_space
    BEFORE UPDATE OF account_type ON account_profiles
    WHEN NEW.account_type = 'enterprise'
      AND EXISTS (
        SELECT 1 FROM personal_memory_spaces
        WHERE user_id = NEW.user_id AND status <> 'deleted'
      )
    BEGIN
      SELECT RAISE(ABORT, 'enterprise_account_has_personal_space');
    END`,
  `CREATE TRIGGER IF NOT EXISTS organization_members_no_personal_activation
    BEFORE UPDATE OF status ON organization_members
    WHEN NEW.status = 'active'
      AND EXISTS (
        SELECT 1 FROM account_profiles
        WHERE user_id = NEW.user_id AND account_type = 'personal'
      )
    BEGIN
      SELECT RAISE(ABORT, 'personal_account_enterprise_activation');
    END`,
  `CREATE TRIGGER IF NOT EXISTS organization_members_no_personal_active_insert
    BEFORE INSERT ON organization_members
    WHEN NEW.status = 'active'
      AND EXISTS (
        SELECT 1 FROM account_profiles
        WHERE user_id = NEW.user_id AND account_type = 'personal'
      )
    BEGIN
      SELECT RAISE(ABORT, 'personal_account_enterprise_activation');
    END`,
  `CREATE TRIGGER IF NOT EXISTS personal_memory_spaces_require_personal_account
    BEFORE INSERT ON personal_memory_spaces
    WHEN NOT EXISTS (
      SELECT 1 FROM account_profiles
      WHERE user_id = NEW.user_id AND account_type = 'personal' AND status = 'active'
    )
    BEGIN
      SELECT RAISE(ABORT, 'personal_space_requires_personal_account');
    END`,
  `CREATE TRIGGER IF NOT EXISTS audit_logs_immutable_update
    BEFORE UPDATE ON audit_logs
    BEGIN
      SELECT RAISE(ABORT, 'audit_log_immutable');
    END`,
  `CREATE TRIGGER IF NOT EXISTS audit_logs_immutable_delete
    BEFORE DELETE ON audit_logs
    BEGIN
      SELECT RAISE(ABORT, 'audit_log_immutable');
    END`,
] as const;

let schemaReady: Promise<void> | undefined;

export function ensureConsoleSchema(database: D1Database = getD1()): Promise<void> {
  if (!schemaReady) {
    schemaReady = initializeSchema(database).catch((error) => {
      schemaReady = undefined;
      throw error;
    });
  }
  return schemaReady;
}

export async function resolveConsoleGraphAccess(
  identity: ConsoleIdentity,
  options: { organizationId: string; agentId: string },
) {
  const database = getD1();
  const actor = await bootstrapActor(database, identity);
  await requireAccountType(database, actor.id, "enterprise");
  const memberships = await listActorMemberships(database, actor.id);

  if (memberships.length === 0) {
    throw new ConsoleError(
      403,
      "no_active_organization",
      "Your account does not have an active organization.",
    );
  }

  const membership = memberships.find(
    (entry) => entry.organizationId === options.organizationId,
  );
  if (!membership) {
    throw new ConsoleError(404, "organization_not_found", "Organization not found.");
  }

  const agent = await requireAgent(
    database,
    membership.organizationId,
    options.agentId,
  );
  return {
    organization: {
      id: membership.organizationId,
      sampleMode: membership.sampleMode === 1,
    },
    agent: {
      id: agent.id,
      slug: agent.slug,
      status: agent.status,
    },
  };
}

async function initializeSchema(database: D1Database): Promise<void> {
  await database.batch(
    SCHEMA_STATEMENTS.map((statement) => database.prepare(statement)),
  );
  await database
    .prepare(
      `INSERT INTO schema_meta (key, value, updated_at)
       VALUES ('console_schema_version', '9', ?1)
       ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`,
    )
    .bind(Date.now())
    .run();
}

export async function resolveAccountRouting(identity: ConsoleIdentity) {
  const database = getD1();
  const actor = await bootstrapActor(database, identity);
  const profile = await getAccountProfile(database, actor.id);
  const enterpriseMembership = await database
    .prepare(
      `SELECT 1
       FROM organization_members AS m
       JOIN organizations AS o ON o.id = m.organization_id
       WHERE m.user_id = ?1 AND m.status = 'active' AND o.status = 'active'
       LIMIT 1`,
    )
    .bind(actor.id)
    .first();
  const personalSpace = await database
    .prepare(
      `SELECT 1 FROM personal_memory_spaces
       WHERE user_id = ?1 AND status <> 'deleted' LIMIT 1`,
    )
    .bind(actor.id)
    .first();

  return {
    accountType: profile.accountType,
    status: profile.status,
    destination: profile.status === "suspended"
      ? "/account-suspended"
      : profile.accountType === "personal"
        ? "/personal"
        : profile.accountType === "enterprise"
          ? "/enterprise"
          : "/account-setup",
    hasEnterpriseMembership: Boolean(enterpriseMembership),
    hasPersonalSpace: Boolean(personalSpace),
  } as const;
}

export async function provisionPersonalAccount(identity: ConsoleIdentity) {
  const database = getD1();
  const actor = await bootstrapActor(database, identity);
  const profile = await getAccountProfile(database, actor.id);
  if (profile.status !== "active") {
    throw new ConsoleError(403, "account_suspended", "This account is suspended.");
  }
  if (profile.accountType === "enterprise") {
    throw new ConsoleError(
      409,
      "account_type_conflict",
      "This ChatGPT identity is already assigned to an enterprise account.",
    );
  }

  const existing = await database
    .prepare(
      `SELECT
         id,
         user_id AS userId,
         scope_name AS scopeName,
         display_name AS displayName,
         status
       FROM personal_memory_spaces
       WHERE user_id = ?1
       LIMIT 1`,
    )
    .bind(actor.id)
    .first<PersonalMemorySpaceRow>();
  if (existing) {
    if (existing.status !== "active") {
      throw new ConsoleError(
        409,
        "personal_space_unavailable",
        "This personal memory space is not active.",
      );
    }
    return personalProvisioningResult(actor, existing, false);
  }

  const now = Date.now();
  const spaceId = newId("psp");
  const scopeName = `personal-${await shortHash(`tmcra:personal:${actor.id}`)}`;
  try {
    await database.batch([
      database
        .prepare(
          `UPDATE account_profiles
           SET account_type = 'personal', selected_at = COALESCE(selected_at, ?1),
               updated_at = ?1
           WHERE user_id = ?2 AND status = 'active'
             AND (account_type IS NULL OR account_type = 'personal')`,
        )
        .bind(now, actor.id),
      database
        .prepare(
          `INSERT INTO personal_memory_spaces (
             id, user_id, scope_name, display_name, status, version,
             created_at, updated_at
           ) VALUES (?1, ?2, ?3, 'Personal Memory', 'active', 1, ?4, ?4)
           ON CONFLICT(user_id) DO NOTHING`,
        )
        .bind(spaceId, actor.id, scopeName, now),
    ]);
  } catch (error) {
    throw mapDatabaseError(error);
  }

  const space = await database
    .prepare(
      `SELECT
         id,
         user_id AS userId,
         scope_name AS scopeName,
         display_name AS displayName,
         status
       FROM personal_memory_spaces
       WHERE user_id = ?1 AND status = 'active'
       LIMIT 1`,
    )
    .bind(actor.id)
    .first<PersonalMemorySpaceRow>();
  if (!space) {
    throw new ConsoleError(
      409,
      "personal_provisioning_conflict",
      "The account changed while the personal memory space was being created.",
    );
  }
  return personalProvisioningResult(actor, space, space.id === spaceId);
}

function personalProvisioningResult(
  actor: ActorRow,
  space: PersonalMemorySpaceRow,
  created: boolean,
) {
  return {
    created,
    destination: "/personal",
    actor: {
      id: actor.id,
      displayName: actor.displayName,
      email: actor.emailDisplay,
    },
    space: {
      id: space.id,
      scopeName: space.scopeName,
      displayName: space.displayName,
      status: space.status,
    },
  } as const;
}

export async function resolvePersonalMemoryAccess(identity: ConsoleIdentity) {
  const database = getD1();
  const actor = await bootstrapActor(database, identity);
  await requireAccountType(database, actor.id, "personal");
  const space = await database
    .prepare(
      `SELECT
         id,
         user_id AS userId,
         scope_name AS scopeName,
         display_name AS displayName,
         status
       FROM personal_memory_spaces
       WHERE user_id = ?1
       LIMIT 1`,
    )
    .bind(actor.id)
    .first<PersonalMemorySpaceRow>();
  if (!space) {
    throw new ConsoleError(
      409,
      "personal_space_not_provisioned",
      "Your personal memory space has not been provisioned.",
    );
  }
  if (space.status !== "active") {
    throw new ConsoleError(
      409,
      "personal_space_unavailable",
      "Your personal memory space is not active.",
    );
  }
  return {
    actor: {
      id: actor.id,
      displayName: actor.displayName,
      email: actor.emailDisplay,
    },
    space: {
      id: space.id,
      scopeName: space.scopeName,
      displayName: space.displayName,
      status: space.status,
    },
  };
}

export async function listPersonalIntegrations(personalSpaceId: string) {
  const database = getD1();
  await ensureConsoleSchema(database);
  const result = await database
    .prepare(
      `SELECT
         id,
         personal_space_id AS personalSpaceId,
         platform,
         installation_fingerprint AS installationFingerprint,
         display_name AS displayName,
         status,
         health,
         capabilities_json AS capabilitiesJson,
         client_version AS clientVersion,
         integration_version AS integrationVersion,
         last_error_code AS lastErrorCode,
         last_seen_at AS lastSeenAt,
         last_healthy_at AS lastHealthyAt,
         created_at AS createdAt,
         updated_at AS updatedAt,
         disconnected_at AS disconnectedAt,
         version
       FROM personal_integrations
       WHERE personal_space_id = ?1
       ORDER BY updated_at DESC, id ASC`,
    )
    .bind(personalSpaceId)
    .all<PersonalIntegrationRow>();
  return result.results.map(personalIntegrationView);
}

export async function getPersonalIntegration(
  personalSpaceId: string,
  integrationId: string,
) {
  const database = getD1();
  await ensureConsoleSchema(database);
  const row = await database
    .prepare(
      `SELECT
         id, personal_space_id AS personalSpaceId, platform,
         installation_fingerprint AS installationFingerprint,
         display_name AS displayName, status, health,
         capabilities_json AS capabilitiesJson,
         client_version AS clientVersion,
         integration_version AS integrationVersion,
         last_error_code AS lastErrorCode,
         last_seen_at AS lastSeenAt, last_healthy_at AS lastHealthyAt,
         created_at AS createdAt, updated_at AS updatedAt,
         disconnected_at AS disconnectedAt, version
       FROM personal_integrations
       WHERE id = ?1 AND personal_space_id = ?2
       LIMIT 1`,
    )
    .bind(integrationId, personalSpaceId)
    .first<PersonalIntegrationRow>();
  return row ? personalIntegrationView(row) : null;
}

export async function reportPersonalIntegration(input: {
  personalSpaceId: string;
  platform: PersonalIntegrationRow["platform"];
  installationFingerprint: string;
  displayName: string;
  status: PersonalIntegrationRow["status"];
  health: PersonalIntegrationRow["health"];
  capabilities: readonly string[];
  clientVersion: string | null;
  integrationVersion: string | null;
  lastErrorCode: string | null;
}) {
  const database = getD1();
  await ensureConsoleSchema(database);
  const now = Date.now();
  const id = `int_${crypto.randomUUID().replaceAll("-", "")}`;
  await database
    .prepare(
      `INSERT INTO personal_integrations (
         id, personal_space_id, platform, installation_fingerprint,
         display_name, status, health, capabilities_json,
         client_version, integration_version, last_error_code,
         last_seen_at, last_healthy_at, created_at, updated_at,
         disconnected_at, version
       ) VALUES (
         ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11,
         ?12, CASE WHEN ?7 = 'healthy' THEN ?12 ELSE NULL END,
         ?12, ?12, CASE WHEN ?6 = 'disconnected' THEN ?12 ELSE NULL END, 1
       )
       ON CONFLICT(personal_space_id, platform, installation_fingerprint)
       DO UPDATE SET
         display_name = excluded.display_name,
         status = excluded.status,
         health = excluded.health,
         capabilities_json = excluded.capabilities_json,
         client_version = excluded.client_version,
         integration_version = excluded.integration_version,
         last_error_code = excluded.last_error_code,
         last_seen_at = excluded.last_seen_at,
         last_healthy_at = CASE
           WHEN excluded.health = 'healthy' THEN excluded.last_seen_at
           ELSE personal_integrations.last_healthy_at
         END,
         updated_at = excluded.updated_at,
         disconnected_at = CASE
           WHEN excluded.status = 'disconnected' THEN excluded.updated_at
           ELSE NULL
         END,
         version = personal_integrations.version + 1`,
    )
    .bind(
      id,
      input.personalSpaceId,
      input.platform,
      input.installationFingerprint,
      input.displayName,
      input.status,
      input.health,
      JSON.stringify([...input.capabilities]),
      input.clientVersion,
      input.integrationVersion,
      input.lastErrorCode,
      now,
    )
    .run();
  const row = await database
    .prepare(
      `SELECT
         id, personal_space_id AS personalSpaceId, platform,
         installation_fingerprint AS installationFingerprint,
         display_name AS displayName, status, health,
         capabilities_json AS capabilitiesJson,
         client_version AS clientVersion,
         integration_version AS integrationVersion,
         last_error_code AS lastErrorCode,
         last_seen_at AS lastSeenAt, last_healthy_at AS lastHealthyAt,
         created_at AS createdAt, updated_at AS updatedAt,
         disconnected_at AS disconnectedAt, version
       FROM personal_integrations
       WHERE personal_space_id = ?1 AND platform = ?2
         AND installation_fingerprint = ?3
       LIMIT 1`,
    )
    .bind(input.personalSpaceId, input.platform, input.installationFingerprint)
    .first<PersonalIntegrationRow>();
  if (!row) throw new ConsoleError(500, "integration_report_failed", "Integration status could not be recorded.");
  return personalIntegrationView(row);
}

export async function disconnectPersonalIntegration(
  personalSpaceId: string,
  integrationId: string,
) {
  const database = getD1();
  await ensureConsoleSchema(database);
  const now = Date.now();
  const result = await database
    .prepare(
      `UPDATE personal_integrations
       SET status = 'disconnected', health = 'unknown',
           last_error_code = NULL, disconnected_at = ?1,
           updated_at = ?1, version = version + 1
       WHERE id = ?2 AND personal_space_id = ?3`,
    )
    .bind(now, integrationId, personalSpaceId)
    .run();
  if (Number(result.meta?.changes ?? 0) !== 1) {
    throw new ConsoleError(404, "integration_not_found", "Integration was not found.");
  }
  const row = await database
    .prepare(
      `SELECT
         id, personal_space_id AS personalSpaceId, platform,
         installation_fingerprint AS installationFingerprint,
         display_name AS displayName, status, health,
         capabilities_json AS capabilitiesJson,
         client_version AS clientVersion,
         integration_version AS integrationVersion,
         last_error_code AS lastErrorCode,
         last_seen_at AS lastSeenAt, last_healthy_at AS lastHealthyAt,
         created_at AS createdAt, updated_at AS updatedAt,
         disconnected_at AS disconnectedAt, version
       FROM personal_integrations
       WHERE id = ?1 AND personal_space_id = ?2
       LIMIT 1`,
    )
    .bind(integrationId, personalSpaceId)
    .first<PersonalIntegrationRow>();
  if (!row) throw new ConsoleError(404, "integration_not_found", "Integration was not found.");
  return personalIntegrationView(row);
}

function personalIntegrationView(row: PersonalIntegrationRow) {
  return {
    id: row.id,
    platform: row.platform,
    displayName: row.displayName,
    status: row.status,
    health: row.health,
    capabilities: parseStringArray(row.capabilitiesJson),
    clientVersion: row.clientVersion,
    integrationVersion: row.integrationVersion,
    lastErrorCode: row.lastErrorCode,
    lastSeenAt: row.lastSeenAt,
    lastHealthyAt: row.lastHealthyAt,
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
    disconnectedAt: row.disconnectedAt,
    version: row.version,
  };
}

export async function listPersonalApiKeys(personalSpaceId: string) {
  const database = getD1();
  await ensureConsoleSchema(database);
  const now = Date.now();
  const result = await database
    .prepare(
      `SELECT
         personal_space_id AS personalSpaceId,
         token_id AS tokenId,
         token_prefix AS tokenPrefix,
         permissions_json AS permissionsJson,
         name,
         status,
         expires_at AS expiresAt,
         created_at AS createdAt,
         revoked_at AS revokedAt
       FROM personal_api_keys
       WHERE personal_space_id = ?1
       ORDER BY token_id ASC`,
    )
    .bind(personalSpaceId)
    .all<PersonalApiKeyRow>();
  return result.results.map((row) => ({
    tokenId: row.tokenId,
    tokenPrefix: row.tokenPrefix,
    permissions: parseStringArray(row.permissionsJson),
    name: row.name,
    status: row.status === "active" && row.expiresAt <= now ? "expired" : row.status,
    expiresAt: row.expiresAt,
    createdAt: row.createdAt,
    revokedAt: row.revokedAt,
  }));
}

export async function getPersonalApiKey(personalSpaceId: string, tokenId: string) {
  const database = getD1();
  await ensureConsoleSchema(database);
  const row = await database
    .prepare(
      `SELECT
         personal_space_id AS personalSpaceId,
         token_id AS tokenId,
         token_prefix AS tokenPrefix,
         permissions_json AS permissionsJson,
         name,
         status,
         expires_at AS expiresAt,
         created_at AS createdAt,
         revoked_at AS revokedAt
       FROM personal_api_keys
       WHERE personal_space_id = ?1 AND token_id = ?2
       LIMIT 1`,
    )
    .bind(personalSpaceId, tokenId)
    .first<PersonalApiKeyRow>();
  return row ? {
    tokenId: row.tokenId,
    tokenPrefix: row.tokenPrefix,
    permissions: parseStringArray(row.permissionsJson),
    name: row.name,
    status: row.status === "active" && row.expiresAt <= Date.now() ? "expired" : row.status,
    expiresAt: row.expiresAt,
    createdAt: row.createdAt,
    revokedAt: row.revokedAt,
  } : null;
}

export async function storePersonalApiKey(input: {
  personalSpaceId: string;
  tokenId: string;
  tokenPrefix: string;
  permissions: readonly string[];
  name: string;
  expiresAt: number;
  createdAt: number;
}) {
  const database = getD1();
  await ensureConsoleSchema(database);
  const result = await database
    .prepare(
      `INSERT INTO personal_api_keys (
         personal_space_id, token_id, token_prefix, permissions_json,
         name, status, expires_at, created_at
       ) VALUES (?1, ?2, ?3, ?4, ?5, 'active', ?6, ?7)
       ON CONFLICT(token_id) DO UPDATE SET
         token_prefix = excluded.token_prefix,
         permissions_json = excluded.permissions_json,
         name = excluded.name,
         status = 'active',
         expires_at = excluded.expires_at,
         revoked_at = NULL
       WHERE personal_api_keys.personal_space_id = excluded.personal_space_id`,
    )
    .bind(
      input.personalSpaceId,
      input.tokenId,
      input.tokenPrefix,
      JSON.stringify(input.permissions),
      input.name,
      input.expiresAt,
      input.createdAt,
    )
    .run();
  if (Number(result.meta.changes ?? 0) !== 1) {
    throw new ConsoleError(409, "personal_api_key_conflict", "The API key belongs to another personal space.");
  }
}

export async function markPersonalApiKeyRevoked(
  personalSpaceId: string,
  tokenId: string,
  revokedAt = Date.now(),
) {
  const database = getD1();
  await ensureConsoleSchema(database);
  const result = await database
    .prepare(
      `UPDATE personal_api_keys
       SET status = 'revoked', revoked_at = COALESCE(revoked_at, ?3)
       WHERE personal_space_id = ?1 AND token_id = ?2`,
    )
    .bind(personalSpaceId, tokenId, revokedAt)
    .run();
  if (Number(result.meta.changes ?? 0) !== 1) {
    throw new ConsoleError(404, "personal_api_key_not_found", "Personal API key not found.");
  }
}

export async function getConsoleSnapshot(
  identity: ConsoleIdentity,
  options: { organizationId?: string; agentId?: string; eventLimit?: number } = {},
) {
  const database = getD1();
  const actor = await bootstrapActor(database, identity);
  await requireAccountType(database, actor.id, "enterprise");
  const memberships = await listActorMemberships(database, actor.id);

  if (memberships.length === 0) {
    throw new ConsoleError(
      403,
      "no_active_organization",
      "Your account does not have an active organization.",
    );
  }

  const membership = options.organizationId
    ? memberships.find((entry) => entry.organizationId === options.organizationId)
    : memberships[0];
  if (!membership) {
    throw new ConsoleError(404, "organization_not_found", "Organization not found.");
  }

  const agentResult = await database
    .prepare(
      `SELECT
         a.id,
         a.organization_id AS organizationId,
         a.name,
         a.slug,
         a.description,
         a.status,
         a.version,
         a.created_at AS createdAt,
         a.updated_at AS updatedAt,
         a.archived_at AS archivedAt,
         COUNT(e.id) AS eventCount,
         MAX(e.occurred_at) AS lastEventAt
       FROM agents AS a
       LEFT JOIN memory_events AS e
         ON e.organization_id = a.organization_id
        AND e.agent_id = a.id
       WHERE a.organization_id = ?1
       GROUP BY a.id
       ORDER BY CASE a.status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END,
                a.updated_at DESC,
                a.id
       LIMIT 100`,
    )
    .bind(membership.organizationId)
    .all<AgentRow>();
  const agentRows = agentResult.results;
  const selectedAgent = options.agentId
    ? agentRows.find((agent) => agent.id === options.agentId)
    : agentRows.find((agent) => agent.status !== "archived") ?? agentRows[0];
  if (options.agentId && !selectedAgent) {
    throw new ConsoleError(404, "agent_not_found", "Agent not found.");
  }

  const eventLimit = clampInteger(options.eventLimit ?? 60, 1, 100);
  const canManageOrganization = hasMinimumRole(membership.role, "admin");
  const selectedAgentId = selectedAgent?.id ?? null;

  const membersPromise = database
    .prepare(
      `SELECT
         m.user_id AS id,
         u.email_display AS email,
         u.display_name AS displayName,
         m.role,
         m.status,
         m.joined_at AS joinedAt,
         m.created_at AS createdAt,
         m.version
       FROM organization_members AS m
       JOIN users AS u ON u.id = m.user_id
       WHERE m.organization_id = ?1
       ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 WHEN 'developer' THEN 2 ELSE 3 END,
                u.display_name COLLATE NOCASE`,
    )
    .bind(membership.organizationId)
    .all();

  const eventsPromise = selectedAgentId
    ? database
        .prepare(
          `SELECT
             id,
             agent_id AS agentId,
             event_type AS eventType,
             content_text AS content,
             metadata_json AS metadataJson,
             source,
             occurred_at AS occurredAt,
             created_at AS createdAt,
             redacted_at AS redactedAt
           FROM memory_events
           WHERE organization_id = ?1 AND agent_id = ?2
           ORDER BY occurred_at DESC, id DESC
           LIMIT ?3`,
        )
        .bind(membership.organizationId, selectedAgentId, eventLimit)
        .all()
    : Promise.resolve({ results: [] as Record<string, unknown>[] });

  const edgesPromise = selectedAgentId
    ? database
        .prepare(
          `SELECT
             id,
             agent_id AS agentId,
             source_event_id AS sourceEventId,
             target_event_id AS targetEventId,
             relation,
             weight,
             created_at AS createdAt
           FROM memory_event_edges
           WHERE organization_id = ?1 AND agent_id = ?2
           ORDER BY created_at DESC, id DESC
           LIMIT 250`,
        )
        .bind(membership.organizationId, selectedAgentId)
        .all()
    : Promise.resolve({ results: [] as Record<string, unknown>[] });

  const apiKeysPromise = canManageOrganization
    ? database
        .prepare(
          `SELECT
             id,
             name,
             token_prefix AS tokenPrefix,
             scopes_json AS scopesJson,
             created_at AS createdAt,
             expires_at AS expiresAt,
             last_used_at AS lastUsedAt,
             revoked_at AS revokedAt
           FROM api_keys
           WHERE organization_id = ?1
           ORDER BY created_at DESC, id DESC
           LIMIT 100`,
        )
        .bind(membership.organizationId)
        .all()
    : Promise.resolve({ results: [] as Record<string, unknown>[] });

  const auditPromise: Promise<{ results: AuditSnapshotRow[] }> = canManageOrganization
    ? database
        .prepare(
          `SELECT
             l.id,
             l.action,
             l.target_type AS targetType,
             l.target_id AS targetId,
             l.metadata_json AS metadataJson,
             l.request_id AS requestId,
             l.created_at AS createdAt,
             COALESCE(u.display_name, k.name, 'System') AS actorName,
             l.actor_type AS actorType
           FROM audit_logs AS l
           LEFT JOIN users AS u ON u.id = l.actor_user_id
           LEFT JOIN api_keys AS k ON k.id = l.actor_api_key_id
           WHERE l.organization_id = ?1
           ORDER BY l.created_at DESC, l.id DESC
           LIMIT 80`,
        )
        .bind(membership.organizationId)
        .all<AuditSnapshotRow>()
    : Promise.resolve({ results: [] });

  const metricsPromise = database
    .prepare(
      `SELECT
         (SELECT COUNT(*) FROM agents WHERE organization_id = ?1 AND status <> 'archived') AS activeAgents,
         (SELECT COUNT(*) FROM memory_events WHERE organization_id = ?1) AS memoryEvents,
         (SELECT COUNT(*) FROM organization_members WHERE organization_id = ?1 AND status = 'active') AS members,
         (SELECT COUNT(*) FROM api_keys WHERE organization_id = ?1 AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?2)) AS activeApiKeys,
         (SELECT COUNT(*) FROM memory_events WHERE organization_id = ?1 AND created_at >= ?3) AS events24h,
         (SELECT COUNT(*) FROM memory_event_edges WHERE organization_id = ?1) AS memoryEdges`,
    )
    .bind(membership.organizationId, Date.now(), Date.now() - 86_400_000)
    .first<Record<string, number>>();

  const usagePromise = database
    .prepare(
      `SELECT
         strftime('%Y-%m-%d', created_at / 1000, 'unixepoch') AS day,
         COUNT(*) AS events
       FROM memory_events
       WHERE organization_id = ?1 AND created_at >= ?2
       GROUP BY day
       ORDER BY day`,
    )
    .bind(membership.organizationId, startOfUtcDay(Date.now() - 6 * 86_400_000))
    .all<{ day: string; events: number }>();

  const [members, events, edges, keyRows, auditRows, metrics, usageRows] =
    await Promise.all([
      membersPromise,
      eventsPromise,
      edgesPromise,
      apiKeysPromise,
      auditPromise,
      metricsPromise,
      usagePromise,
    ]);

  const auditLogs = auditRows.results.map((row) => ({
    ...row,
    metadata: parseJsonObject(row.metadataJson),
    metadataJson: undefined,
  }));

  return {
    actor: {
      id: actor.id,
      email: actor.emailDisplay,
      displayName: actor.displayName,
      role: membership.role,
      organizations: memberships.map((entry) => ({
        id: entry.organizationId,
        name: entry.organizationName,
        slug: entry.organizationSlug,
        role: entry.role,
        sampleMode: entry.sampleMode === 1,
      })),
    },
    organization: {
      id: membership.organizationId,
      name: membership.organizationName,
      slug: membership.organizationSlug,
      status: membership.organizationStatus,
      role: membership.role,
      sampleMode: membership.sampleMode === 1,
    },
    metrics: metrics ?? {
      activeAgents: 0,
      memoryEvents: 0,
      members: 0,
      activeApiKeys: 0,
      events24h: 0,
      memoryEdges: 0,
    },
    agents: agentRows.map(normalizeAgent),
    selectedAgentId,
    events: events.results.map((row) => ({
      ...row,
      metadata: parseJsonObject(row.metadataJson),
      metadataJson: undefined,
    })),
    edges: edges.results,
    members: members.results,
    apiKeys: keyRows.results.map((row) => ({
      ...row,
      scopes: parseStringArray(row.scopesJson),
      scopesJson: undefined,
    })),
    operations: auditLogs.slice(0, 8).map((entry) => ({
      id: entry.id,
      type: entry.action,
      status: "completed",
      targetType: entry.targetType,
      targetId: entry.targetId,
      actorName: entry.actorName,
      createdAt: entry.createdAt,
    })),
    auditLogs,
    usageDaily: fillUsageDays(usageRows.results),
  };
}

export async function executeConsoleAction(
  identity: ConsoleIdentity,
  requestId: string,
  action: string,
  rawPayload: unknown,
): Promise<Record<string, unknown>> {
  const database = getD1();
  const actor = await bootstrapActor(database, identity);
  await requireAccountType(database, actor.id, "enterprise");
  const payload = asObject(rawPayload, "payload");
  const organizationId = requiredString(payload.organizationId, "organizationId", 80);
  const membership = await requireMembership(database, actor.id, organizationId);

  if (membership.sampleMode === 1 && action !== "sample.load" && action !== "load_sample") {
    throw new ConsoleError(403, "sample_read_only", "Sample workspaces are read-only.");
  }

  try {
    switch (action) {
      case "organization.update":
      case "update_organization":
        return await updateOrganization(database, actor, membership, requestId, payload);
      case "member.add":
      case "add_member":
        return await addMember(database, actor, membership, requestId, payload);
      case "member.update":
      case "update_member":
        return await updateMember(database, actor, membership, requestId, payload);
      case "member.remove":
      case "remove_member":
        return await removeMember(database, actor, membership, requestId, payload);
      case "agent.create":
      case "create_agent":
        return await createAgent(database, actor, membership, requestId, payload);
      case "agent.update":
      case "update_agent":
        return await updateAgent(database, actor, membership, requestId, payload);
      case "agent.archive":
      case "archive_agent":
        return await archiveAgent(database, actor, membership, requestId, payload);
      case "memory.create":
      case "create_memory_event":
        return await createMemoryEvent(database, actor, membership, requestId, payload);
      case "memory.redact":
      case "redact_memory_event":
        return await redactMemoryEvent(database, actor, membership, requestId, payload);
      case "api_key.create":
      case "create_api_key":
        return await createApiKey(database, actor, membership, requestId, payload);
      case "api_key.revoke":
      case "revoke_api_key":
        return await revokeApiKey(database, actor, membership, requestId, payload);
      case "sample.load":
      case "load_sample":
        return await loadSample(database, actor, membership, requestId);
      default:
        throw new ConsoleError(400, "unknown_action", "Unknown console action.");
    }
  } catch (error) {
    throw mapDatabaseError(error);
  }
}

async function updateOrganization(
  database: D1Database,
  actor: ActorRow,
  membership: MembershipRow,
  requestId: string,
  payload: Record<string, unknown>,
) {
  requireRole(membership.role, ["owner"]);
  const name = requiredString(payload.name, "name", 80);
  const slug = parseSlug(payload.slug, 48);
  const now = Date.now();
  await database.batch([
    database
      .prepare(
        `UPDATE organizations
         SET name = ?1, slug = ?2, version = version + 1, updated_at = ?3
         WHERE id = ?4 AND status = 'active'`,
      )
      .bind(name, slug, now, membership.organizationId),
    auditStatement(database, {
      organizationId: membership.organizationId,
      actorUserId: actor.id,
      action: "organization.updated",
      targetType: "organization",
      targetId: membership.organizationId,
      requestId,
      metadata: { name, slug },
    }),
  ]);
  return { organization: { id: membership.organizationId, name, slug } };
}

async function addMember(
  database: D1Database,
  actor: ActorRow,
  membership: MembershipRow,
  requestId: string,
  payload: Record<string, unknown>,
) {
  requireRole(membership.role, ["owner", "admin"]);
  const email = normalizeEmail(requiredString(payload.email, "email", 254));
  const role = parseRole(payload.role ?? "viewer");
  if (membership.role === "admin" && (role === "owner" || role === "admin")) {
    throw new ConsoleError(403, "role_forbidden", "Admins can add developers or viewers only.");
  }
  const existing = await database
    .prepare(
      `SELECT 1
       FROM organization_members AS m
       JOIN users AS u ON u.id = m.user_id
       WHERE m.organization_id = ?1 AND u.email_normalized = ?2
       LIMIT 1`,
    )
    .bind(membership.organizationId, email)
    .first();
  if (existing) {
    throw new ConsoleError(409, "member_exists", "This person is already a member.");
  }

  const userId = newId("usr");
  const displayName = email.split("@")[0] || email;
  const now = Date.now();
  await database.batch([
    database
      .prepare(
        `INSERT INTO users (
           id, email_normalized, email_display, display_name, created_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?5)
         ON CONFLICT(email_normalized) DO UPDATE SET
           email_display = excluded.email_display,
           updated_at = excluded.updated_at`,
      )
      .bind(userId, email, email, displayName, now),
    database
      .prepare(
        `INSERT INTO organization_members (
           organization_id, user_id, role, status, invited_by_user_id, created_at, updated_at
         )
         SELECT ?1, id, ?2, 'invited', ?3, ?4, ?4
         FROM users WHERE email_normalized = ?5`,
      )
      .bind(membership.organizationId, role, actor.id, now, email),
    auditStatement(database, {
      organizationId: membership.organizationId,
      actorUserId: actor.id,
      action: "member.added",
      targetType: "member_email",
      targetId: email,
      requestId,
      metadata: { email, role, status: "invited" },
    }),
  ]);
  return { member: { email, displayName, role, status: "invited" } };
}

async function updateMember(
  database: D1Database,
  actor: ActorRow,
  membership: MembershipRow,
  requestId: string,
  payload: Record<string, unknown>,
) {
  requireRole(membership.role, ["owner", "admin"]);
  const userId = requiredString(payload.userId, "userId", 80);
  const target = await getMember(database, membership.organizationId, userId);
  if (!target) throw new ConsoleError(404, "member_not_found", "Member not found.");
  const role = payload.role === undefined ? target.role : parseRole(payload.role);
  const status =
    payload.status === undefined
      ? target.status
      : parseEnum(payload.status, "status", ["invited", "active", "suspended"] as const);
  if (
    membership.role === "admin" &&
    (target.role === "owner" ||
      target.role === "admin" ||
      role === "owner" ||
      role === "admin")
  ) {
    throw new ConsoleError(403, "role_forbidden", "Admins cannot manage owners or admins.");
  }
  const now = Date.now();
  await database.batch([
    database
      .prepare(
        `UPDATE organization_members
         SET role = ?1, status = ?2, version = version + 1, updated_at = ?3,
             joined_at = CASE WHEN ?2 = 'active' THEN COALESCE(joined_at, ?3) ELSE joined_at END
         WHERE organization_id = ?4 AND user_id = ?5`,
      )
      .bind(role, status, now, membership.organizationId, userId),
    auditStatement(database, {
      organizationId: membership.organizationId,
      actorUserId: actor.id,
      action: "member.updated",
      targetType: "member",
      targetId: userId,
      requestId,
      metadata: { fromRole: target.role, role, fromStatus: target.status, status },
    }),
  ]);
  return { member: { id: userId, role, status } };
}

async function removeMember(
  database: D1Database,
  actor: ActorRow,
  membership: MembershipRow,
  requestId: string,
  payload: Record<string, unknown>,
) {
  requireRole(membership.role, ["owner", "admin"]);
  const userId = requiredString(payload.userId, "userId", 80);
  const target = await getMember(database, membership.organizationId, userId);
  if (!target) throw new ConsoleError(404, "member_not_found", "Member not found.");
  if (membership.role === "admin" && (target.role === "owner" || target.role === "admin")) {
    throw new ConsoleError(403, "role_forbidden", "Admins cannot remove owners or admins.");
  }
  await database.batch([
    database
      .prepare(
        `DELETE FROM organization_members
         WHERE organization_id = ?1 AND user_id = ?2`,
      )
      .bind(membership.organizationId, userId),
    auditStatement(database, {
      organizationId: membership.organizationId,
      actorUserId: actor.id,
      action: "member.removed",
      targetType: "member",
      targetId: userId,
      requestId,
      metadata: { role: target.role, status: target.status },
    }),
  ]);
  return { removedUserId: userId };
}

async function createAgent(
  database: D1Database,
  actor: ActorRow,
  membership: MembershipRow,
  requestId: string,
  payload: Record<string, unknown>,
) {
  requireRole(membership.role, ["owner", "admin", "developer"]);
  const name = requiredString(payload.name, "name", 80);
  const slug = parseSlug(payload.slug ?? slugify(name), 64);
  const description = optionalString(payload.description, "description", 2_000) ?? "";
  const id = newId("agt");
  const now = Date.now();
  await database.batch([
    database
      .prepare(
        `INSERT INTO agents (
           id, organization_id, name, slug, description, status,
           created_by_user_id, created_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, 'active', ?6, ?7, ?7)`,
      )
      .bind(id, membership.organizationId, name, slug, description, actor.id, now),
    auditStatement(database, {
      organizationId: membership.organizationId,
      actorUserId: actor.id,
      action: "agent.created",
      targetType: "agent",
      targetId: id,
      requestId,
      metadata: { name, slug },
    }),
  ]);
  return { agent: { id, name, slug, description, status: "active", createdAt: now, updatedAt: now } };
}

async function updateAgent(
  database: D1Database,
  actor: ActorRow,
  membership: MembershipRow,
  requestId: string,
  payload: Record<string, unknown>,
) {
  requireRole(membership.role, ["owner", "admin", "developer"]);
  const agentId = requiredString(payload.agentId, "agentId", 80);
  const current = await requireAgent(database, membership.organizationId, agentId);
  const name = payload.name === undefined ? current.name : requiredString(payload.name, "name", 80);
  const slug = payload.slug === undefined ? current.slug : parseSlug(payload.slug, 64);
  const description =
    payload.description === undefined
      ? current.description
      : optionalString(payload.description, "description", 2_000) ?? "";
  const status =
    payload.status === undefined
      ? current.status
      : parseEnum(payload.status, "status", ["active", "paused"] as const);
  const now = Date.now();
  await database.batch([
    database
      .prepare(
        `UPDATE agents
         SET name = ?1, slug = ?2, description = ?3, status = ?4,
             version = version + 1, updated_at = ?5, archived_at = NULL
         WHERE organization_id = ?6 AND id = ?7`,
      )
      .bind(name, slug, description, status, now, membership.organizationId, agentId),
    auditStatement(database, {
      organizationId: membership.organizationId,
      actorUserId: actor.id,
      action: "agent.updated",
      targetType: "agent",
      targetId: agentId,
      requestId,
      metadata: { name, slug, status },
    }),
  ]);
  return { agent: { id: agentId, name, slug, description, status, updatedAt: now } };
}

async function archiveAgent(
  database: D1Database,
  actor: ActorRow,
  membership: MembershipRow,
  requestId: string,
  payload: Record<string, unknown>,
) {
  requireRole(membership.role, ["owner", "admin", "developer"]);
  const agentId = requiredString(payload.agentId, "agentId", 80);
  await requireAgent(database, membership.organizationId, agentId);
  const now = Date.now();
  await database.batch([
    database
      .prepare(
        `UPDATE agents
         SET status = 'archived', archived_at = ?1, updated_at = ?1, version = version + 1
         WHERE organization_id = ?2 AND id = ?3`,
      )
      .bind(now, membership.organizationId, agentId),
    auditStatement(database, {
      organizationId: membership.organizationId,
      actorUserId: actor.id,
      action: "agent.archived",
      targetType: "agent",
      targetId: agentId,
      requestId,
    }),
  ]);
  return { agent: { id: agentId, status: "archived", archivedAt: now } };
}

async function createMemoryEvent(
  database: D1Database,
  actor: ActorRow,
  membership: MembershipRow,
  requestId: string,
  payload: Record<string, unknown>,
) {
  requireRole(membership.role, ["owner", "admin", "developer"]);
  const agentId = requiredString(payload.agentId, "agentId", 80);
  const agent = await requireAgent(database, membership.organizationId, agentId);
  if (agent.status === "archived") {
    throw new ConsoleError(409, "agent_archived", "Archived agents cannot receive events.");
  }
  const eventType = parseToken(payload.eventType ?? "observation", "eventType", 64);
  const content = requiredString(payload.content, "content", 65_536);
  assertUtf8Size(content, 65_536, "content");
  const metadataJson = stringifyMetadata(payload.metadata);
  const source = parseToken(payload.source ?? "console", "source", 64);
  const occurredAt = parseTimestamp(payload.occurredAt ?? Date.now(), "occurredAt");
  const idempotencyKey =
    payload.idempotencyKey === undefined
      ? null
      : parseToken(payload.idempotencyKey, "idempotencyKey", 128);
  const id = newId("evt");
  const now = Date.now();
  await database.batch([
    database
      .prepare(
        `INSERT INTO memory_events (
           id, organization_id, agent_id, event_type, content_text, metadata_json,
           source, idempotency_key, occurred_at, created_by_type,
           created_by_user_id, created_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, 'user', ?10, ?11)`,
      )
      .bind(
        id,
        membership.organizationId,
        agentId,
        eventType,
        content,
        metadataJson,
        source,
        idempotencyKey,
        occurredAt,
        actor.id,
        now,
      ),
    database
      .prepare(`UPDATE agents SET updated_at = ?1 WHERE organization_id = ?2 AND id = ?3`)
      .bind(now, membership.organizationId, agentId),
    auditStatement(database, {
      organizationId: membership.organizationId,
      actorUserId: actor.id,
      action: "memory.created",
      targetType: "memory_event",
      targetId: id,
      requestId,
      metadata: { agentId, eventType, source },
    }),
  ]);
  return {
    event: { id, agentId, eventType, content, metadata: parseJsonObject(metadataJson), source, occurredAt, createdAt: now },
  };
}

async function redactMemoryEvent(
  database: D1Database,
  actor: ActorRow,
  membership: MembershipRow,
  requestId: string,
  payload: Record<string, unknown>,
) {
  requireRole(membership.role, ["owner", "admin", "developer"]);
  const eventId = requiredString(payload.eventId, "eventId", 80);
  const event = await database
    .prepare(
      `SELECT id, agent_id AS agentId, redacted_at AS redactedAt
       FROM memory_events
       WHERE organization_id = ?1 AND id = ?2
       LIMIT 1`,
    )
    .bind(membership.organizationId, eventId)
    .first<{ id: string; agentId: string; redactedAt: number | null }>();
  if (!event) throw new ConsoleError(404, "event_not_found", "Memory event not found.");
  if (event.redactedAt) return { event: { id: eventId, redactedAt: event.redactedAt } };
  const now = Date.now();
  await database.batch([
    database
      .prepare(
        `UPDATE memory_events
         SET content_text = '', metadata_json = '{}', redacted_at = ?1
         WHERE organization_id = ?2 AND id = ?3`,
      )
      .bind(now, membership.organizationId, eventId),
    auditStatement(database, {
      organizationId: membership.organizationId,
      actorUserId: actor.id,
      action: "memory.redacted",
      targetType: "memory_event",
      targetId: eventId,
      requestId,
      metadata: { agentId: event.agentId },
    }),
  ]);
  return { event: { id: eventId, redactedAt: now } };
}

async function createApiKey(
  database: D1Database,
  actor: ActorRow,
  membership: MembershipRow,
  requestId: string,
  payload: Record<string, unknown>,
) {
  requireRole(membership.role, ["owner", "admin"]);
  const name = requiredString(payload.name, "name", 80);
  const scopes = parseScopes(payload.scopes);
  const expiresAt =
    payload.expiresAt === undefined || payload.expiresAt === null
      ? null
      : parseTimestamp(payload.expiresAt, "expiresAt");
  const now = Date.now();
  if (expiresAt !== null && (expiresAt <= now || expiresAt > now + 366 * 86_400_000)) {
    throw new ConsoleError(422, "invalid_expiry", "API key expiry must be within the next year.");
  }

  const id = newId("key");
  const secretPart = randomBase64Url(32);
  const secret = `tmcra_sk_live_${id}.${secretPart}`;
  const secretHash = await sha256Base64Url(secret);
  const tokenPrefix = `tmcra_sk_live_${id.slice(0, 12)}`;
  await database.batch([
    database
      .prepare(
        `INSERT INTO api_keys (
           id, organization_id, name, token_prefix, secret_hash, hash_version,
           scopes_json, created_by_user_id, created_at, expires_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, 1, ?6, ?7, ?8, ?9)`,
      )
      .bind(
        id,
        membership.organizationId,
        name,
        tokenPrefix,
        secretHash,
        JSON.stringify(scopes),
        actor.id,
        now,
        expiresAt,
      ),
    auditStatement(database, {
      organizationId: membership.organizationId,
      actorUserId: actor.id,
      action: "api_key.created",
      targetType: "api_key",
      targetId: id,
      requestId,
      metadata: { name, tokenPrefix, scopes, expiresAt },
    }),
  ]);
  return {
    apiKey: { id, name, tokenPrefix, scopes, createdAt: now, expiresAt, lastUsedAt: null, revokedAt: null },
    secret,
    secretShownOnce: true,
  };
}

async function revokeApiKey(
  database: D1Database,
  actor: ActorRow,
  membership: MembershipRow,
  requestId: string,
  payload: Record<string, unknown>,
) {
  requireRole(membership.role, ["owner", "admin"]);
  const keyId = requiredString(payload.keyId, "keyId", 80);
  const key = await database
    .prepare(
      `SELECT id, name, revoked_at AS revokedAt
       FROM api_keys
       WHERE organization_id = ?1 AND id = ?2
       LIMIT 1`,
    )
    .bind(membership.organizationId, keyId)
    .first<{ id: string; name: string; revokedAt: number | null }>();
  if (!key) throw new ConsoleError(404, "api_key_not_found", "API key not found.");
  if (key.revokedAt) return { apiKey: { id: keyId, revokedAt: key.revokedAt } };
  const now = Date.now();
  await database.batch([
    database
      .prepare(
        `UPDATE api_keys
         SET revoked_at = ?1, revoked_by_user_id = ?2
         WHERE organization_id = ?3 AND id = ?4`,
      )
      .bind(now, actor.id, membership.organizationId, keyId),
    auditStatement(database, {
      organizationId: membership.organizationId,
      actorUserId: actor.id,
      action: "api_key.revoked",
      targetType: "api_key",
      targetId: keyId,
      requestId,
      metadata: { name: key.name },
    }),
  ]);
  return { apiKey: { id: keyId, revokedAt: now } };
}

async function loadSample(
  database: D1Database,
  actor: ActorRow,
  membership: MembershipRow,
  requestId: string,
) {
  requireRole(membership.role, ["owner", "admin"]);
  const workspaceState = await database
    .prepare(
      `SELECT
         sample_mode AS sampleMode,
         (SELECT COUNT(*) FROM agents WHERE organization_id = ?1) AS agentCount
       FROM organizations
       WHERE id = ?1
       LIMIT 1`,
    )
    .bind(membership.organizationId)
    .first<{ sampleMode: number; agentCount: number }>();
  if (!workspaceState) {
    throw new ConsoleError(404, "organization_not_found", "Organization not found.");
  }
  if (workspaceState.sampleMode !== 1 && Number(workspaceState.agentCount) > 0) {
    throw new ConsoleError(
      409,
      "workspace_not_empty",
      "Sample data can only be loaded into an empty workspace.",
    );
  }
  const existingAgent = await database
    .prepare(
      `SELECT id FROM agents
       WHERE organization_id = ?1 AND slug = 'tmcra-sample-atlas'
       LIMIT 1`,
    )
    .bind(membership.organizationId)
    .first<{ id: string }>();
  const agentId = existingAgent?.id ?? `agt_${await shortHash(`${membership.organizationId}:sample:atlas`)}`;
  const eventIds = await Promise.all(
    [1, 2, 3, 4].map(async (index) =>
      `evt_${await shortHash(`${membership.organizationId}:sample:atlas:${index}`)}`,
    ),
  );
  const edgeIds = await Promise.all(
    [1, 2, 3].map(async (index) =>
      `edg_${await shortHash(`${membership.organizationId}:sample:atlas:edge:${index}`)}`,
    ),
  );
  const now = Date.now();
  const days = 86_400_000;
  const sampleEvents = [
    [eventIds[0], "preference", "User prefers concise reports.", now - 42 * days, "sample-preference"],
    [eventIds[1], "project", "User starts Project Atlas.", now - 37 * days, "sample-project"],
    [eventIds[2], "change", "Project Atlas changes its target market.", now - 25 * days, "sample-change"],
    [eventIds[3], "request", "User asks for a launch plan.", now, "sample-request"],
  ] as const;

  const statements: D1PreparedStatement[] = [];
  statements.push(
    database
      .prepare(
        `UPDATE organizations
         SET sample_mode = 1, version = version + 1, updated_at = ?1
         WHERE id = ?2 AND sample_mode = 0`,
      )
      .bind(now, membership.organizationId),
  );
  if (!existingAgent) {
    statements.push(
      database
        .prepare(
          `INSERT INTO agents (
             id, organization_id, name, slug, description, status,
             created_by_user_id, created_at, updated_at
           ) VALUES (?1, ?2, 'Project Atlas', 'tmcra-sample-atlas',
             'Explicitly loaded TMCRA recall-path sample.', 'active', ?3, ?4, ?4)`,
        )
        .bind(agentId, membership.organizationId, actor.id, now),
    );
  }
  for (const [id, eventType, content, occurredAt, idempotencyKey] of sampleEvents) {
    statements.push(
      database
        .prepare(
          `INSERT OR IGNORE INTO memory_events (
             id, organization_id, agent_id, event_type, content_text, metadata_json,
             source, idempotency_key, occurred_at, created_by_type,
             created_by_user_id, created_at
           ) VALUES (?1, ?2, ?3, ?4, ?5, '{"sample":true}', 'sample', ?6, ?7, 'user', ?8, ?9)`,
        )
        .bind(
          id,
          membership.organizationId,
          agentId,
          eventType,
          content,
          idempotencyKey,
          occurredAt,
          actor.id,
          now,
        ),
    );
  }
  const relations = ["precedes", "changes", "informs"] as const;
  for (let index = 0; index < relations.length; index += 1) {
    statements.push(
      database
        .prepare(
          `INSERT OR IGNORE INTO memory_event_edges (
             id, organization_id, agent_id, source_event_id, target_event_id,
             relation, weight, created_at
           ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, 1, ?7)`,
        )
        .bind(
          edgeIds[index],
          membership.organizationId,
          agentId,
          eventIds[index],
          eventIds[index + 1],
          relations[index],
          now,
        ),
    );
  }
  statements.push(
    auditStatement(database, {
      organizationId: membership.organizationId,
      actorUserId: actor.id,
      action: "sample.loaded",
      targetType: "agent",
      targetId: agentId,
      requestId,
      metadata: { sample: "project-atlas", eventCount: 4 },
    }),
  );
  await database.batch(statements);
  return { sampleLoaded: true, agentId, eventIds };
}

export async function authenticateApiKey(
  token: string,
  requiredScope: string,
): Promise<{
  keyId: string;
  organizationId: string;
  name: string;
  scopes: string[];
}> {
  if (!API_KEY_SCOPES.has(requiredScope)) {
    throw new ConsoleError(500, "invalid_required_scope", "Invalid server-side API key scope.");
  }
  const match = /^tmcra_sk_live_(key_[a-f0-9]{32})\.([A-Za-z0-9_-]{43})$/.exec(token);
  if (!match) throw new ConsoleError(401, "invalid_api_key", "Invalid API key.");
  const database = getD1();
  await ensureConsoleSchema(database);
  const row = await database
    .prepare(
      `SELECT
         k.id,
         k.organization_id AS organizationId,
         o.status AS organizationStatus,
         k.name,
         k.token_prefix AS tokenPrefix,
         k.secret_hash AS secretHash,
         k.hash_version AS hashVersion,
         k.scopes_json AS scopesJson,
         k.created_at AS createdAt,
         k.expires_at AS expiresAt,
         k.last_used_at AS lastUsedAt,
         k.revoked_at AS revokedAt
       FROM api_keys AS k
       JOIN organizations AS o ON o.id = k.organization_id
       WHERE k.id = ?1
       LIMIT 1`,
    )
    .bind(match[1])
    .first<ApiKeyRow>();
  const presentedHash = await sha256Base64Url(token);
  if (
    !row ||
    row.organizationStatus !== "active" ||
    row.hashVersion !== 1 ||
    !constantTimeEqual(presentedHash, row.secretHash) ||
    row.revokedAt !== null ||
    (row.expiresAt !== null && row.expiresAt <= Date.now())
  ) {
    throw new ConsoleError(401, "invalid_api_key", "Invalid API key.");
  }
  const scopes = parseStringArray(row.scopesJson);
  if (!scopes.includes(requiredScope)) {
    throw new ConsoleError(403, "api_key_scope_denied", "API key scope denied.");
  }
  if (row.lastUsedAt === null || row.lastUsedAt < Date.now() - 15 * 60_000) {
    await database
      .prepare(
        `UPDATE api_keys SET last_used_at = ?1
         WHERE id = ?2 AND (last_used_at IS NULL OR last_used_at < ?3)`,
      )
      .bind(Date.now(), row.id, Date.now() - 15 * 60_000)
      .run();
  }
  return { keyId: row.id, organizationId: row.organizationId, name: row.name, scopes };
}

async function bootstrapActor(database: D1Database, identity: ConsoleIdentity): Promise<ActorRow> {
  await ensureConsoleSchema(database);
  const email = normalizeEmail(identity.email);
  const displayName = cleanDisplayName(identity.fullName ?? identity.displayName, email);
  const now = Date.now();
  await database
    .prepare(
      `INSERT INTO users (
         id, email_normalized, email_display, display_name,
         created_at, updated_at, last_seen_at
       ) VALUES (?1, ?2, ?3, ?4, ?5, ?5, ?5)
       ON CONFLICT(email_normalized) DO UPDATE SET
         email_display = excluded.email_display,
         display_name = excluded.display_name,
         updated_at = excluded.updated_at,
         last_seen_at = excluded.last_seen_at`,
    )
    .bind(newId("usr"), email, identity.email.trim(), displayName, now)
    .run();

  const actor = await database
    .prepare(
      `SELECT
         id,
         email_normalized AS emailNormalized,
         email_display AS emailDisplay,
         display_name AS displayName,
         bootstrap_completed_at AS bootstrapCompletedAt
       FROM users WHERE email_normalized = ?1 LIMIT 1`,
    )
    .bind(email)
    .first<ActorRow>();
  if (!actor) throw new ConsoleError(500, "actor_bootstrap_failed", "Unable to initialize account.");

  await database
    .prepare(
      `INSERT INTO account_profiles (user_id, account_type, status, created_at, updated_at)
       VALUES (?1, NULL, 'active', ?2, ?2)
       ON CONFLICT(user_id) DO NOTHING`,
    )
    .bind(actor.id, now)
    .run();

  const membershipState = await database
    .prepare(
      `SELECT
         COUNT(*) AS membershipCount,
         SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS activeCount
       FROM organization_members
       WHERE user_id = ?1 AND status IN ('active', 'invited')`,
    )
    .bind(actor.id)
    .first<{ membershipCount: number; activeCount: number | null }>();
  let profile = await getAccountProfile(database, actor.id);
  if (profile.accountType === null && Number(membershipState?.membershipCount ?? 0) > 0) {
    await database
      .prepare(
        `UPDATE account_profiles
         SET account_type = 'enterprise', selected_at = COALESCE(selected_at, ?1),
             updated_at = ?1
         WHERE user_id = ?2 AND account_type IS NULL`,
      )
      .bind(now, actor.id)
      .run();
    profile = await getAccountProfile(database, actor.id);
  }

  if (profile.accountType === "personal" && Number(membershipState?.activeCount ?? 0) > 0) {
    throw new ConsoleError(
      409,
      "account_boundary_conflict",
      "This identity has both personal and enterprise resources. Internal support must resolve the account boundary.",
    );
  }
  if (profile.accountType === "enterprise" && profile.status === "active") {
    await database
      .prepare(
        `UPDATE organization_members
         SET status = 'active', joined_at = COALESCE(joined_at, ?1),
             updated_at = ?1, version = version + 1
         WHERE user_id = ?2 AND status = 'invited'`,
      )
      .bind(now, actor.id)
      .run();
  }

  if (actor.bootstrapCompletedAt === null) {
    await database
      .prepare(`UPDATE users SET bootstrap_completed_at = ?1 WHERE id = ?2`)
      .bind(now, actor.id)
      .run();
    actor.bootstrapCompletedAt = now;
  }
  return actor;
}

async function getAccountProfile(
  database: D1Database,
  userId: string,
): Promise<AccountProfileRow> {
  const profile = await database
    .prepare(
      `SELECT
         account_type AS accountType,
         status,
         selected_at AS selectedAt
       FROM account_profiles
       WHERE user_id = ?1
       LIMIT 1`,
    )
    .bind(userId)
    .first<AccountProfileRow>();
  if (!profile) {
    throw new ConsoleError(500, "account_profile_missing", "Unable to initialize account profile.");
  }
  return profile;
}

async function requireAccountType(
  database: D1Database,
  userId: string,
  expected: AccountType,
) {
  const profile = await getAccountProfile(database, userId);
  if (profile.status !== "active") {
    throw new ConsoleError(403, "account_suspended", "This account is suspended.");
  }
  if (profile.accountType !== expected) {
    throw new ConsoleError(
      403,
      "account_type_mismatch",
      `This endpoint is restricted to ${expected} accounts.`,
    );
  }
  return profile;
}

async function listActorMemberships(database: D1Database, userId: string): Promise<MembershipRow[]> {
  const result = await database
    .prepare(
      `SELECT
         o.id AS organizationId,
         o.name AS organizationName,
         o.slug AS organizationSlug,
         o.status AS organizationStatus,
         o.sample_mode AS sampleMode,
         m.role,
         m.status AS membershipStatus
       FROM organization_members AS m
       JOIN organizations AS o ON o.id = m.organization_id
       WHERE m.user_id = ?1 AND m.status = 'active' AND o.status = 'active'
       ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 WHEN 'developer' THEN 2 ELSE 3 END,
                m.created_at,
                o.id`,
    )
    .bind(userId)
    .all<MembershipRow>();
  return result.results;
}

async function requireMembership(
  database: D1Database,
  userId: string,
  organizationId: string,
): Promise<MembershipRow> {
  const row = await database
    .prepare(
      `SELECT
         o.id AS organizationId,
         o.name AS organizationName,
         o.slug AS organizationSlug,
         o.status AS organizationStatus,
         o.sample_mode AS sampleMode,
         m.role,
         m.status AS membershipStatus
       FROM organization_members AS m
       JOIN organizations AS o ON o.id = m.organization_id
       WHERE m.user_id = ?1 AND m.organization_id = ?2
         AND m.status = 'active' AND o.status = 'active'
       LIMIT 1`,
    )
    .bind(userId, organizationId)
    .first<MembershipRow>();
  if (!row) throw new ConsoleError(404, "organization_not_found", "Organization not found.");
  return row;
}

async function getMember(database: D1Database, organizationId: string, userId: string) {
  return database
    .prepare(
      `SELECT user_id AS id, role, status, version
       FROM organization_members
       WHERE organization_id = ?1 AND user_id = ?2
       LIMIT 1`,
    )
    .bind(organizationId, userId)
    .first<{ id: string; role: ConsoleRole; status: "invited" | "active" | "suspended"; version: number }>();
}

async function requireAgent(
  database: D1Database,
  organizationId: string,
  agentId: string,
): Promise<AgentRow> {
  const row = await database
    .prepare(
      `SELECT
         id,
         organization_id AS organizationId,
         name,
         slug,
         description,
         status,
         version,
         created_at AS createdAt,
         updated_at AS updatedAt,
         archived_at AS archivedAt
       FROM agents
       WHERE organization_id = ?1 AND id = ?2
       LIMIT 1`,
    )
    .bind(organizationId, agentId)
    .first<AgentRow>();
  if (!row) throw new ConsoleError(404, "agent_not_found", "Agent not found.");
  return row;
}

function auditStatement(database: D1Database, input: AuditInput): D1PreparedStatement {
  return database
    .prepare(
      `INSERT INTO audit_logs (
         id, organization_id, actor_type, actor_user_id,
         action, target_type, target_id, request_id, metadata_json, created_at
       ) VALUES (?1, ?2, 'user', ?3, ?4, ?5, ?6, ?7, ?8, ?9)`,
    )
    .bind(
      newId("aud"),
      input.organizationId,
      input.actorUserId,
      input.action,
      input.targetType,
      input.targetId,
      input.requestId,
      JSON.stringify(input.metadata ?? {}),
      Date.now(),
    );
}

function requireRole(current: ConsoleRole, allowed: ConsoleRole[]) {
  if (!allowed.includes(current)) {
    throw new ConsoleError(403, "forbidden", "You do not have permission for this action.");
  }
}

function hasMinimumRole(current: ConsoleRole, minimum: ConsoleRole) {
  return ROLE_ORDER[current] >= ROLE_ORDER[minimum];
}

function parseRole(value: unknown): ConsoleRole {
  return parseEnum(value, "role", ["owner", "admin", "developer", "viewer"] as const);
}

function asObject(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ConsoleError(400, "invalid_payload", `${field} must be an object.`);
  }
  return value as Record<string, unknown>;
}

function requiredString(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== "string") {
    throw new ConsoleError(422, "invalid_field", `${field} must be a string.`);
  }
  const clean = value.trim();
  if (!clean || clean.length > maxLength) {
    throw new ConsoleError(422, "invalid_field", `${field} must be 1-${maxLength} characters.`);
  }
  return clean;
}

function optionalString(
  value: unknown,
  field: string,
  maxLength: number,
): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== "string") {
    throw new ConsoleError(422, "invalid_field", `${field} must be a string.`);
  }
  const clean = value.trim();
  if (clean.length > maxLength) {
    throw new ConsoleError(422, "invalid_field", `${field} is too long.`);
  }
  return clean;
}

function parseEnum<const T extends readonly string[]>(
  value: unknown,
  field: string,
  allowed: T,
): T[number] {
  if (typeof value !== "string" || !allowed.includes(value)) {
    throw new ConsoleError(422, "invalid_field", `${field} is invalid.`);
  }
  return value as T[number];
}

function parseSlug(value: unknown, maxLength: number): string {
  const slug = requiredString(value, "slug", maxLength).toLowerCase();
  if (!/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(slug)) {
    throw new ConsoleError(422, "invalid_slug", "slug may contain lowercase letters, numbers, and hyphens.");
  }
  return slug;
}

function parseToken(value: unknown, field: string, maxLength: number): string {
  const token = requiredString(value, field, maxLength);
  if (!/^[A-Za-z0-9._:-]+$/.test(token)) {
    throw new ConsoleError(422, "invalid_field", `${field} contains unsupported characters.`);
  }
  return token;
}

function normalizeEmail(value: string): string {
  const email = value.trim().toLowerCase();
  if (email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    throw new ConsoleError(422, "invalid_email", "A valid email address is required.");
  }
  return email;
}

function cleanDisplayName(value: string, fallbackEmail: string): string {
  const clean = value.trim().replace(/\s+/g, " ").slice(0, 100);
  return clean || fallbackEmail;
}

function slugify(value: string): string {
  const slug = value
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 56)
    .replace(/-+$/g, "");
  return slug || `agent-${crypto.randomUUID().slice(0, 8)}`;
}

function stringifyMetadata(value: unknown): string {
  if (value === undefined) return "{}";
  const object = asObject(value, "metadata");
  const serialized = JSON.stringify(object);
  assertUtf8Size(serialized, 32_768, "metadata");
  return serialized;
}

function assertUtf8Size(value: string, maxBytes: number, field: string) {
  if (new TextEncoder().encode(value).byteLength > maxBytes) {
    throw new ConsoleError(422, "field_too_large", `${field} is too large.`);
  }
}

function parseTimestamp(value: unknown, field: string): number {
  const parsed =
    typeof value === "number"
      ? value
      : typeof value === "string"
        ? Date.parse(value)
        : Number.NaN;
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 8_640_000_000_000_000) {
    throw new ConsoleError(422, "invalid_timestamp", `${field} is invalid.`);
  }
  return Math.trunc(parsed);
}

function parseScopes(value: unknown): string[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > API_KEY_SCOPES.size) {
    throw new ConsoleError(422, "invalid_scopes", "At least one valid API key scope is required.");
  }
  const scopes = [...new Set(value)];
  if (scopes.some((scope) => typeof scope !== "string" || !API_KEY_SCOPES.has(scope))) {
    throw new ConsoleError(422, "invalid_scopes", "API key scopes are invalid.");
  }
  return scopes as string[];
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

function normalizeAgent(agent: AgentRow) {
  return {
    ...agent,
    eventCount: Number(agent.eventCount ?? 0),
    lastEventAt: agent.lastEventAt ?? null,
  };
}

function fillUsageDays(rows: { day: string; events: number }[]) {
  const counts = new Map(rows.map((row) => [row.day, Number(row.events)]));
  const start = startOfUtcDay(Date.now() - 6 * 86_400_000);
  return Array.from({ length: 7 }, (_, index) => {
    const date = new Date(start + index * 86_400_000).toISOString().slice(0, 10);
    return { date, events: counts.get(date) ?? 0, apiRequests: 0 };
  });
}

function startOfUtcDay(value: number): number {
  const date = new Date(value);
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
}

function clampInteger(value: number, minimum: number, maximum: number) {
  if (!Number.isFinite(value)) return minimum;
  return Math.min(maximum, Math.max(minimum, Math.trunc(value)));
}

function newId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID().replace(/-/g, "")}`;
}

function randomBase64Url(byteLength: number): string {
  const bytes = crypto.getRandomValues(new Uint8Array(byteLength));
  return bytesToBase64Url(bytes);
}

async function sha256Base64Url(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return bytesToBase64Url(new Uint8Array(digest));
}

async function shortHash(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest).slice(0, 16), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

function bytesToBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function constantTimeEqual(left: string, right: string): boolean {
  const maxLength = Math.max(left.length, right.length);
  let difference = left.length ^ right.length;
  for (let index = 0; index < maxLength; index += 1) {
    difference |= (left.charCodeAt(index) || 0) ^ (right.charCodeAt(index) || 0);
  }
  return difference === 0;
}

function mapDatabaseError(error: unknown): Error {
  if (error instanceof ConsoleError) return error;
  const message = error instanceof Error ? `${error.message}\n${String(error.cause ?? "")}` : String(error);
  if (message.includes("last_active_owner")) {
    return new ConsoleError(409, "last_owner", "An organization must keep at least one active owner.");
  }
  if (message.includes("UNIQUE constraint failed")) {
    return new ConsoleError(409, "conflict", "A record with these values already exists.");
  }
  if (message.includes("FOREIGN KEY constraint failed")) {
    return new ConsoleError(409, "related_record_conflict", "A related record changed; refresh and retry.");
  }
  return error instanceof Error ? error : new Error("Unexpected database error");
}
