import { sql } from "drizzle-orm";
import {
  check,
  foreignKey,
  index,
  integer,
  primaryKey,
  real,
  sqliteTable,
  text,
  uniqueIndex,
} from "drizzle-orm/sqlite-core";

const nowMs = sql`(unixepoch() * 1000)`;

export const schemaMeta = sqliteTable("schema_meta", {
  key: text("key").primaryKey(),
  value: text("value").notNull(),
  updatedAt: integer("updated_at").notNull().default(nowMs),
});

export const users = sqliteTable(
  "users",
  {
    id: text("id").primaryKey(),
    emailNormalized: text("email_normalized").notNull(),
    emailDisplay: text("email_display").notNull(),
    displayName: text("display_name").notNull(),
    bootstrapCompletedAt: integer("bootstrap_completed_at"),
    createdAt: integer("created_at").notNull().default(nowMs),
    updatedAt: integer("updated_at").notNull().default(nowMs),
    lastSeenAt: integer("last_seen_at"),
  },
  (table) => [
    uniqueIndex("users_email_normalized_uq").on(table.emailNormalized),
  ],
);

export const accountProfiles = sqliteTable(
  "account_profiles",
  {
    userId: text("user_id")
      .primaryKey()
      .references(() => users.id, { onDelete: "cascade" }),
    accountType: text("account_type"),
    status: text("status").notNull().default("active"),
    selectedAt: integer("selected_at"),
    createdAt: integer("created_at").notNull().default(nowMs),
    updatedAt: integer("updated_at").notNull().default(nowMs),
  },
  (table) => [
    index("account_profiles_type_status_idx").on(
      table.accountType,
      table.status,
      table.updatedAt,
    ),
    check(
      "account_profiles_type_check",
      sql`${table.accountType} IS NULL OR ${table.accountType} IN ('personal', 'enterprise')`,
    ),
    check(
      "account_profiles_status_check",
      sql`${table.status} IN ('active', 'suspended')`,
    ),
  ],
);

export const personalMemorySpaces = sqliteTable(
  "personal_memory_spaces",
  {
    id: text("id").primaryKey(),
    userId: text("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    scopeName: text("scope_name").notNull(),
    displayName: text("display_name").notNull(),
    status: text("status").notNull().default("active"),
    version: integer("version").notNull().default(1),
    createdAt: integer("created_at").notNull().default(nowMs),
    updatedAt: integer("updated_at").notNull().default(nowMs),
  },
  (table) => [
    uniqueIndex("personal_memory_spaces_user_uq").on(table.userId),
    uniqueIndex("personal_memory_spaces_scope_uq").on(table.scopeName),
    index("personal_memory_spaces_status_updated_idx").on(
      table.status,
      table.updatedAt,
    ),
    check(
      "personal_memory_spaces_status_check",
      sql`${table.status} IN ('active', 'deleting', 'deleted')`,
    ),
  ],
);

export const personalIntegrations = sqliteTable(
  "personal_integrations",
  {
    id: text("id").primaryKey(),
    personalSpaceId: text("personal_space_id")
      .notNull()
      .references(() => personalMemorySpaces.id, { onDelete: "cascade" }),
    platform: text("platform").notNull(),
    installationFingerprint: text("installation_fingerprint").notNull(),
    displayName: text("display_name").notNull(),
    status: text("status").notNull().default("detected"),
    health: text("health").notNull().default("unknown"),
    capabilitiesJson: text("capabilities_json").notNull().default("[]"),
    clientVersion: text("client_version"),
    integrationVersion: text("integration_version"),
    lastErrorCode: text("last_error_code"),
    lastSeenAt: integer("last_seen_at").notNull().default(nowMs),
    lastHealthyAt: integer("last_healthy_at"),
    createdAt: integer("created_at").notNull().default(nowMs),
    updatedAt: integer("updated_at").notNull().default(nowMs),
    disconnectedAt: integer("disconnected_at"),
    version: integer("version").notNull().default(1),
  },
  (table) => [
    uniqueIndex("personal_integrations_installation_uq").on(
      table.personalSpaceId,
      table.platform,
      table.installationFingerprint,
    ),
    index("personal_integrations_space_status_idx").on(
      table.personalSpaceId,
      table.status,
      table.updatedAt,
    ),
    check(
      "personal_integrations_platform_check",
      sql`${table.platform} IN ('codex', 'openclaw', 'hermes', 'claude_code', 'deepseek_harness')`,
    ),
    check(
      "personal_integrations_status_check",
      sql`${table.status} IN ('detected', 'configured', 'connected', 'attention_required', 'disconnected')`,
    ),
    check(
      "personal_integrations_health_check",
      sql`${table.health} IN ('unknown', 'healthy', 'degraded', 'failed')`,
    ),
    check(
      "personal_integrations_capabilities_json_check",
      sql`json_valid(${table.capabilitiesJson})`,
    ),
    check(
      "personal_integrations_version_check",
      sql`${table.version} > 0`,
    ),
  ],
);

export const deviceAuthorizations = sqliteTable(
  "device_authorizations",
  {
    id: text("id").primaryKey(),
    deviceCodeHash: text("device_code_hash").notNull(),
    userCodeHash: text("user_code_hash").notNull(),
    codeChallenge: text("code_challenge").notNull(),
    codeChallengeMethod: text("code_challenge_method").notNull().default("S256"),
    provider: text("provider").notNull().default("codex"),
    clientName: text("client_name").notNull().default("Codex"),
    sourceHash: text("source_hash").notNull().default("legacy"),
    status: text("status").notNull().default("pending"),
    intervalSeconds: integer("interval_seconds").notNull().default(5),
    pollCount: integer("poll_count").notNull().default(0),
    lastPolledAt: integer("last_polled_at"),
    approvedByUserId: text("approved_by_user_id").references(() => users.id, {
      onDelete: "set null",
    }),
    personalSpaceId: text("personal_space_id").references(
      () => personalMemorySpaces.id,
      { onDelete: "set null" },
    ),
    tokenCiphertext: text("token_ciphertext"),
    tokenIv: text("token_iv"),
    createdAt: integer("created_at").notNull().default(nowMs),
    updatedAt: integer("updated_at").notNull().default(nowMs),
    expiresAt: integer("expires_at").notNull(),
    approvedAt: integer("approved_at"),
    claimedAt: integer("claimed_at"),
    issuanceRequestId: text("issuance_request_id"),
    deliveryReceiptHash: text("delivery_receipt_hash"),
  },
  (table) => [
    uniqueIndex("device_authorizations_device_hash_uq").on(
      table.deviceCodeHash,
    ),
    uniqueIndex("device_authorizations_user_hash_uq").on(table.userCodeHash),
    index("device_authorizations_status_expires_idx").on(
      table.status,
      table.expiresAt,
    ),
    index("device_authorizations_space_created_idx").on(
      table.personalSpaceId,
      table.createdAt,
    ),
    check(
      "device_authorizations_status_check",
      sql`${table.status} IN ('pending', 'authorizing', 'approved', 'denied', 'claimed', 'expired')`,
    ),
    check(
      "device_authorizations_pkce_method_check",
      sql`${table.codeChallengeMethod} = 'S256'`,
    ),
    check(
      "device_authorizations_provider_check",
      sql`${table.provider} IN ('codex', 'deepseek_harness')`,
    ),
    check(
      "device_authorizations_interval_check",
      sql`${table.intervalSeconds} BETWEEN 1 AND 60`,
    ),
    check(
      "device_authorizations_token_pair_check",
      sql`(${table.tokenCiphertext} IS NULL) = (${table.tokenIv} IS NULL)`,
    ),
  ],
);

export const deviceFlowRateLimits = sqliteTable(
  "device_flow_rate_limits",
  {
    limitKey: text("limit_key").notNull(),
    bucketStart: integer("bucket_start").notNull(),
    requestCount: integer("request_count").notNull().default(1),
    lastAdmissionId: text("last_admission_id").notNull(),
    updatedAt: integer("updated_at").notNull().default(nowMs),
  },
  (table) => [
    primaryKey({ columns: [table.limitKey, table.bucketStart] }),
    index("device_flow_rate_limits_bucket_idx").on(table.bucketStart),
    check(
      "device_flow_rate_limits_count_check",
      sql`${table.requestCount} > 0`,
    ),
  ],
);

export const deviceConnections = sqliteTable(
  "device_connections",
  {
    id: text("id").primaryKey(),
    authorizationId: text("authorization_id")
      .notNull()
      .references(() => deviceAuthorizations.id, { onDelete: "cascade" }),
    userId: text("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    personalSpaceId: text("personal_space_id")
      .notNull()
      .references(() => personalMemorySpaces.id, { onDelete: "cascade" }),
    provider: text("provider").notNull().default("codex"),
    displayName: text("display_name").notNull().default("Codex"),
    tokenId: text("token_id").notNull(),
    tokenPrefix: text("token_prefix").notNull(),
    scopePrefix: text("scope_prefix").notNull(),
    permissionsJson: text("permissions_json").notNull().default("[]"),
    status: text("status").notNull().default("active"),
    tokenExpiresAt: integer("token_expires_at").notNull(),
    createdAt: integer("created_at").notNull().default(nowMs),
    updatedAt: integer("updated_at").notNull().default(nowMs),
    lastConnectedAt: integer("last_connected_at"),
    revokedAt: integer("revoked_at"),
  },
  (table) => [
    uniqueIndex("device_connections_authorization_uq").on(
      table.authorizationId,
    ),
    uniqueIndex("device_connections_token_id_uq").on(table.tokenId),
    index("device_connections_space_status_idx").on(
      table.personalSpaceId,
      table.status,
      table.createdAt,
    ),
    index("device_connections_user_status_idx").on(
      table.userId,
      table.status,
      table.createdAt,
    ),
    check(
      "device_connections_provider_check",
      sql`${table.provider} IN ('codex', 'deepseek_harness')`,
    ),
    check(
      "device_connections_status_check",
      sql`${table.status} IN ('active', 'revoked', 'expired')`,
    ),
    check(
      "device_connections_permissions_json_check",
      sql`json_valid(${table.permissionsJson})`,
    ),
  ],
);

export const deviceRevocationOutbox = sqliteTable(
  "device_revocation_outbox",
  {
    id: text("id").primaryKey(),
    tokenId: text("token_id").notNull(),
    connectionId: text("connection_id").references(() => deviceConnections.id, {
      onDelete: "set null",
    }),
    reason: text("reason").notNull(),
    status: text("status").notNull().default("pending"),
    attemptCount: integer("attempt_count").notNull().default(0),
    nextAttemptAt: integer("next_attempt_at").notNull().default(nowMs),
    lastAttemptAt: integer("last_attempt_at"),
    lastErrorCode: text("last_error_code"),
    createdAt: integer("created_at").notNull().default(nowMs),
    updatedAt: integer("updated_at").notNull().default(nowMs),
    completedAt: integer("completed_at"),
  },
  (table) => [
    uniqueIndex("device_revocation_outbox_token_uq").on(table.tokenId),
    index("device_revocation_outbox_due_idx").on(
      table.status,
      table.nextAttemptAt,
    ),
    index("device_revocation_outbox_connection_idx").on(table.connectionId),
    check(
      "device_revocation_outbox_status_check",
      sql`${table.status} IN ('pending', 'processing', 'completed')`,
    ),
    check(
      "device_revocation_outbox_attempt_check",
      sql`${table.attemptCount} >= 0`,
    ),
  ],
);

export const organizations = sqliteTable(
  "organizations",
  {
    id: text("id").primaryKey(),
    name: text("name").notNull(),
    slug: text("slug").notNull(),
    status: text("status").notNull().default("active"),
    sampleMode: integer("sample_mode").notNull().default(0),
    bootstrapOwnerUserId: text("bootstrap_owner_user_id").references(
      () => users.id,
      { onDelete: "restrict" },
    ),
    createdByUserId: text("created_by_user_id")
      .notNull()
      .references(() => users.id, { onDelete: "restrict" }),
    version: integer("version").notNull().default(1),
    createdAt: integer("created_at").notNull().default(nowMs),
    updatedAt: integer("updated_at").notNull().default(nowMs),
  },
  (table) => [
    uniqueIndex("organizations_slug_uq").on(table.slug),
    uniqueIndex("organizations_bootstrap_owner_uq")
      .on(table.bootstrapOwnerUserId)
      .where(sql`${table.bootstrapOwnerUserId} IS NOT NULL`),
    check(
      "organizations_status_check",
      sql`${table.status} IN ('active', 'archived')`,
    ),
    check(
      "organizations_sample_mode_check",
      sql`${table.sampleMode} IN (0, 1)`,
    ),
  ],
);

export const organizationMembers = sqliteTable(
  "organization_members",
  {
    organizationId: text("organization_id")
      .notNull()
      .references(() => organizations.id, { onDelete: "cascade" }),
    userId: text("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "restrict" }),
    role: text("role").notNull(),
    status: text("status").notNull().default("invited"),
    invitedByUserId: text("invited_by_user_id").references(() => users.id, {
      onDelete: "set null",
    }),
    joinedAt: integer("joined_at"),
    version: integer("version").notNull().default(1),
    createdAt: integer("created_at").notNull().default(nowMs),
    updatedAt: integer("updated_at").notNull().default(nowMs),
  },
  (table) => [
    primaryKey({ columns: [table.organizationId, table.userId] }),
    index("organization_members_user_status_idx").on(
      table.userId,
      table.status,
      table.organizationId,
    ),
    index("organization_members_org_status_role_idx").on(
      table.organizationId,
      table.status,
      table.role,
    ),
    check(
      "organization_members_role_check",
      sql`${table.role} IN ('owner', 'admin', 'developer', 'viewer')`,
    ),
    check(
      "organization_members_status_check",
      sql`${table.status} IN ('invited', 'active', 'suspended')`,
    ),
  ],
);

export const agents = sqliteTable(
  "agents",
  {
    id: text("id").primaryKey(),
    organizationId: text("organization_id")
      .notNull()
      .references(() => organizations.id, { onDelete: "cascade" }),
    name: text("name").notNull(),
    slug: text("slug").notNull(),
    description: text("description").notNull().default(""),
    status: text("status").notNull().default("active"),
    version: integer("version").notNull().default(1),
    createdByUserId: text("created_by_user_id")
      .notNull()
      .references(() => users.id, { onDelete: "restrict" }),
    createdAt: integer("created_at").notNull().default(nowMs),
    updatedAt: integer("updated_at").notNull().default(nowMs),
    archivedAt: integer("archived_at"),
  },
  (table) => [
    uniqueIndex("agents_org_slug_uq").on(table.organizationId, table.slug),
    uniqueIndex("agents_org_id_uq").on(table.organizationId, table.id),
    index("agents_org_status_updated_idx").on(
      table.organizationId,
      table.status,
      table.updatedAt,
      table.id,
    ),
    check(
      "agents_status_check",
      sql`${table.status} IN ('active', 'paused', 'archived')`,
    ),
  ],
);

export const apiKeys = sqliteTable(
  "api_keys",
  {
    id: text("id").primaryKey(),
    organizationId: text("organization_id")
      .notNull()
      .references(() => organizations.id, { onDelete: "cascade" }),
    name: text("name").notNull(),
    tokenPrefix: text("token_prefix").notNull(),
    secretHash: text("secret_hash").notNull(),
    hashVersion: integer("hash_version").notNull().default(1),
    scopesJson: text("scopes_json").notNull().default("[]"),
    createdByUserId: text("created_by_user_id")
      .notNull()
      .references(() => users.id, { onDelete: "restrict" }),
    createdAt: integer("created_at").notNull().default(nowMs),
    expiresAt: integer("expires_at"),
    lastUsedAt: integer("last_used_at"),
    revokedAt: integer("revoked_at"),
    revokedByUserId: text("revoked_by_user_id").references(() => users.id, {
      onDelete: "set null",
    }),
  },
  (table) => [
    index("api_keys_org_revoked_created_idx").on(
      table.organizationId,
      table.revokedAt,
      table.createdAt,
      table.id,
    ),
    check("api_keys_scopes_json_check", sql`json_valid(${table.scopesJson})`),
  ],
);

export const memoryEvents = sqliteTable(
  "memory_events",
  {
    id: text("id").primaryKey(),
    organizationId: text("organization_id").notNull(),
    agentId: text("agent_id").notNull(),
    eventType: text("event_type").notNull(),
    contentText: text("content_text").notNull(),
    metadataJson: text("metadata_json").notNull().default("{}"),
    source: text("source").notNull().default("console"),
    idempotencyKey: text("idempotency_key"),
    occurredAt: integer("occurred_at").notNull(),
    createdByType: text("created_by_type").notNull().default("user"),
    createdByUserId: text("created_by_user_id").references(() => users.id, {
      onDelete: "set null",
    }),
    createdByApiKeyId: text("created_by_api_key_id").references(
      () => apiKeys.id,
      { onDelete: "set null" },
    ),
    createdAt: integer("created_at").notNull().default(nowMs),
    redactedAt: integer("redacted_at"),
  },
  (table) => [
    foreignKey({
      columns: [table.organizationId, table.agentId],
      foreignColumns: [agents.organizationId, agents.id],
    }).onDelete("cascade"),
    uniqueIndex("memory_events_org_agent_id_uq").on(
      table.organizationId,
      table.agentId,
      table.id,
    ),
    uniqueIndex("memory_events_agent_idempotency_uq")
      .on(table.agentId, table.idempotencyKey)
      .where(sql`${table.idempotencyKey} IS NOT NULL`),
    index("memory_events_org_agent_occurred_idx").on(
      table.organizationId,
      table.agentId,
      table.occurredAt,
      table.id,
    ),
    index("memory_events_org_created_idx").on(
      table.organizationId,
      table.createdAt,
      table.id,
    ),
    check(
      "memory_events_created_by_type_check",
      sql`${table.createdByType} IN ('user', 'api_key', 'system')`,
    ),
    check(
      "memory_events_metadata_json_check",
      sql`json_valid(${table.metadataJson})`,
    ),
  ],
);

export const memoryEventEdges = sqliteTable(
  "memory_event_edges",
  {
    id: text("id").primaryKey(),
    organizationId: text("organization_id").notNull(),
    agentId: text("agent_id").notNull(),
    sourceEventId: text("source_event_id").notNull(),
    targetEventId: text("target_event_id").notNull(),
    relation: text("relation").notNull(),
    weight: real("weight").notNull().default(1),
    createdAt: integer("created_at").notNull().default(nowMs),
  },
  (table) => [
    foreignKey({
      columns: [table.organizationId, table.agentId],
      foreignColumns: [agents.organizationId, agents.id],
    }).onDelete("cascade"),
    foreignKey({
      columns: [table.organizationId, table.agentId, table.sourceEventId],
      foreignColumns: [
        memoryEvents.organizationId,
        memoryEvents.agentId,
        memoryEvents.id,
      ],
    }).onDelete("cascade"),
    foreignKey({
      columns: [table.organizationId, table.agentId, table.targetEventId],
      foreignColumns: [
        memoryEvents.organizationId,
        memoryEvents.agentId,
        memoryEvents.id,
      ],
    }).onDelete("cascade"),
    uniqueIndex("memory_event_edges_unique_uq").on(
      table.agentId,
      table.sourceEventId,
      table.targetEventId,
      table.relation,
    ),
    index("memory_event_edges_org_agent_idx").on(
      table.organizationId,
      table.agentId,
      table.createdAt,
      table.id,
    ),
  ],
);

export const auditLogs = sqliteTable(
  "audit_logs",
  {
    id: text("id").primaryKey(),
    organizationId: text("organization_id")
      .notNull()
      .references(() => organizations.id, { onDelete: "cascade" }),
    actorType: text("actor_type").notNull(),
    actorUserId: text("actor_user_id").references(() => users.id, {
      onDelete: "set null",
    }),
    actorApiKeyId: text("actor_api_key_id").references(() => apiKeys.id, {
      onDelete: "set null",
    }),
    action: text("action").notNull(),
    targetType: text("target_type").notNull(),
    targetId: text("target_id").notNull(),
    requestId: text("request_id").notNull(),
    metadataJson: text("metadata_json").notNull().default("{}"),
    createdAt: integer("created_at").notNull().default(nowMs),
  },
  (table) => [
    index("audit_logs_org_created_idx").on(
      table.organizationId,
      table.createdAt,
      table.id,
    ),
    index("audit_logs_org_target_created_idx").on(
      table.organizationId,
      table.targetType,
      table.targetId,
      table.createdAt,
    ),
    index("audit_logs_org_actor_created_idx").on(
      table.organizationId,
      table.actorUserId,
      table.createdAt,
    ),
    check(
      "audit_logs_actor_type_check",
      sql`${table.actorType} IN ('user', 'api_key', 'system')`,
    ),
    check("audit_logs_metadata_json_check", sql`json_valid(${table.metadataJson})`),
  ],
);

export const internalMeta = sqliteTable("internal_meta", {
  key: text("key").primaryKey(),
  value: text("value").notNull(),
  updatedAt: integer("updated_at").notNull().default(nowMs),
});

export const internalStaff = sqliteTable(
  "internal_staff",
  {
    id: text("id").primaryKey(),
    emailNormalized: text("email_normalized").notNull(),
    emailDisplay: text("email_display").notNull(),
    displayName: text("display_name").notNull(),
    role: text("role").notNull(),
    status: text("status").notNull().default("invited"),
    invitedByStaffId: text("invited_by_staff_id"),
    joinedAt: integer("joined_at"),
    lastSeenAt: integer("last_seen_at"),
    createdAt: integer("created_at").notNull().default(nowMs),
    updatedAt: integer("updated_at").notNull().default(nowMs),
  },
  (table) => [
    uniqueIndex("internal_staff_email_normalized_uq").on(
      table.emailNormalized,
    ),
    index("internal_staff_status_role_idx").on(
      table.status,
      table.role,
      table.createdAt,
    ),
    check(
      "internal_staff_role_check",
      sql`${table.role} IN ('platform_owner', 'platform_admin', 'support', 'security', 'analyst')`,
    ),
    check(
      "internal_staff_status_check",
      sql`${table.status} IN ('invited', 'active', 'suspended')`,
    ),
  ],
);

export const internalAuditLogs = sqliteTable(
  "internal_audit_logs",
  {
    id: text("id").primaryKey(),
    actorStaffId: text("actor_staff_id"),
    actorEmail: text("actor_email").notNull(),
    actorRole: text("actor_role").notNull(),
    action: text("action").notNull(),
    targetType: text("target_type").notNull(),
    targetId: text("target_id").notNull(),
    requestId: text("request_id").notNull(),
    metadataJson: text("metadata_json").notNull().default("{}"),
    createdAt: integer("created_at").notNull().default(nowMs),
  },
  (table) => [
    index("internal_audit_logs_created_idx").on(
      table.createdAt,
      table.id,
    ),
    index("internal_audit_logs_actor_created_idx").on(
      table.actorStaffId,
      table.createdAt,
    ),
    index("internal_audit_logs_target_created_idx").on(
      table.targetType,
      table.targetId,
      table.createdAt,
    ),
    check(
      "internal_audit_logs_actor_role_check",
      sql`${table.actorRole} IN ('platform_owner', 'platform_admin', 'support', 'security', 'analyst')`,
    ),
    check(
      "internal_audit_logs_metadata_json_check",
      sql`json_valid(${table.metadataJson})`,
    ),
  ],
);

export const internalActionLimits = sqliteTable(
  "internal_action_limits",
  {
    actorStaffId: text("actor_staff_id").notNull(),
    bucketStart: integer("bucket_start").notNull(),
    mutationCount: integer("mutation_count").notNull().default(1),
    updatedAt: integer("updated_at").notNull().default(nowMs),
  },
  (table) => [
    primaryKey({ columns: [table.actorStaffId, table.bucketStart] }),
    index("internal_action_limits_bucket_idx").on(table.bucketStart),
    check(
      "internal_action_limits_count_check",
      sql`${table.mutationCount} > 0`,
    ),
  ],
);

export const earlyAccessRequests = sqliteTable(
  "early_access_requests",
  {
    id: text("id").primaryKey(),
    emailNormalized: text("email_normalized").notNull(),
    emailDisplay: text("email_display").notNull(),
    contactName: text("contact_name").notNull().default(""),
    companyName: text("company_name").notNull().default(""),
    industry: text("industry").notNull().default(""),
    companySize: text("company_size").notNull().default(""),
    primaryUseCase: text("primary_use_case").notNull().default(""),
    platformsJson: text("platforms_json").notNull().default("[]"),
    timeline: text("timeline").notNull().default(""),
    source: text("source").notNull().default("website"),
    status: text("status").notNull().default("new"),
    reviewNote: text("review_note").notNull().default(""),
    lastReviewedBy: text("last_reviewed_by"),
    lastReviewedAt: integer("last_reviewed_at"),
    version: integer("version").notNull().default(1),
    createdAt: integer("created_at").notNull().default(nowMs),
    updatedAt: integer("updated_at").notNull().default(nowMs),
  },
  (table) => [
    uniqueIndex("early_access_requests_email_uq").on(table.emailNormalized),
    index("early_access_requests_status_created_idx").on(
      table.status,
      table.createdAt,
    ),
    check(
      "early_access_requests_status_check",
      sql`${table.status} IN ('new', 'contacted', 'qualified', 'closed')`,
    ),
    check(
      "early_access_requests_platforms_json_check",
      sql`json_valid(${table.platformsJson})`,
    ),
  ],
);
