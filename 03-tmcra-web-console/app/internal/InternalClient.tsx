"use client";

import Link from "next/link";
import type { FormEvent, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { LanguageToggle, type Language, useLanguage } from "../i18n";
import BrandMark from "../BrandMark";

type UnknownRecord = Record<string, unknown>;
type ViewId = "overview" | "organizations" | "access" | "agents" | "memory" | "operations" | "staff" | "audit" | "system";

type Actor = {
  displayName: string;
  email: string;
  role: string;
};

type PlatformMetrics = {
  organizations: number | null;
  users: number | null;
  agents: number | null;
  memoryEvents: number | null;
  memoryEdges: number | null;
  apiKeys: number | null;
  accessRequests: number | null;
  newAccessRequests: number | null;
  writes7d: number | null;
  updatedAt: string | null;
};

type Organization = {
  id: string;
  version: number | null;
  name: string;
  slug: string;
  status: string;
  plan: string;
  ownerEmail: string;
  memberCount: number | null;
  agentCount: number | null;
  memoryEventCount: number | null;
  memoryEdgeCount: number | null;
  apiKeyCount: number | null;
  writes7d: number | null;
  dataRegion: string;
  retentionDays: number | null;
  createdAt: string | null;
  updatedAt: string | null;
};

type Agent = {
  id: string;
  organizationId: string;
  organizationName: string;
  name: string;
  slug: string;
  environment: string;
  status: string;
  eventCount: number | null;
  edgeCount: number | null;
  lastWriteAt: string | null;
  createdAt: string | null;
};

type StaffMember = {
  id: string;
  name: string;
  email: string;
  role: string;
  status: string;
  joinedAt: string | null;
  lastSeenAt: string | null;
};

type AuditLog = {
  id: string;
  createdAt: string | null;
  actor: string;
  action: string;
  targetType: string;
  targetId: string;
  organizationId: string;
  result: string;
  requestId: string;
};

type UsageDay = {
  date: string;
  writes: number | null;
};

type AccessRequest = {
  id: string;
  version: number | null;
  email: string;
  contactName: string;
  companyName: string;
  industry: string;
  companySize: string;
  primaryUseCase: string;
  platforms: string[];
  timeline: string;
  source: string;
  status: string;
  reviewNote: string;
  lastReviewedBy: string;
  lastReviewedAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
};

type SystemStatus = {
  accessGate: string | null;
  authMode: string | null;
  database: string | null;
  databaseBinding: string | null;
  auditProtection: string | null;
  environment: string | null;
  dataRegion: string | null;
  schemaVersion: string | null;
};

type Availability = "available" | "partial" | "unavailable";

type OperationSource = {
  availability: Availability;
  reason: string | null;
  source: string;
  checkedAt: string | null;
  httpStatus: number | null;
  probeLatencyMs: number | null;
  serviceLatencyMs: number | null;
};

type InternalOperations = {
  collectedAt: string | null;
  health: OperationSource & {
    status: string;
    service: string | null;
    version: string | null;
  };
  readiness: OperationSource & {
    status: string;
    service: string | null;
    version: string | null;
    checks: Record<string, boolean> | null;
    snapshotStale: boolean | null;
    snapshotAgeSeconds: number | null;
    monitorGeneration: number | null;
  };
  deployment: OperationSource & {
    status: string;
    service: string | null;
    release: string | null;
    upstreamStatus: number | null;
  };
  startupPreflight: {
    availability: Availability;
    reason: string;
    status: string | null;
    mode: string | null;
    releaseId: string | null;
    completedAt: string | null;
    failedChecks: string[];
  };
  queue: {
    availability: Availability;
    reason: string;
    pending: number | null;
    running: number | null;
    failed: number | null;
    active: number | null;
    activeLimit: number | null;
    recentErrorTotal: number | null;
    recentErrors: string[];
  };
  latency: {
    availability: Availability;
    reason: string;
    healthProbeMs: number | null;
    healthServiceMs: number | null;
    readinessProbeMs: number | null;
    readinessServiceMs: number | null;
    deploymentProbeMs: number | null;
    sampleWindowSeconds: number | null;
    sampleCount: number | null;
    p50Ms: number | null;
    p95Ms: number | null;
    p99Ms: number | null;
    recallP50Ms: number | null;
    recallP95Ms: number | null;
    recallP99Ms: number | null;
    writeP50Ms: number | null;
    writeP95Ms: number | null;
    writeP99Ms: number | null;
  };
  costs: {
    availability: Availability;
    reason: string;
    currency: string | null;
    periodStart: string | null;
    periodEnd: string | null;
    knownCostMicroCny: number | null;
    unknownCallCount: number | null;
    registeredCallCount: number | null;
    completedCallCount: number | null;
    failedCallCount: number | null;
    inputTokens: number | null;
    outputTokens: number | null;
  };
  release: {
    availability: Availability;
    reason: string;
    websiteRelease: string | null;
    apiVersion: string | null;
    apiReleaseId: string | null;
    releaseSha256: string | null;
    channel: string | null;
    canaryPercent: number | null;
    previousRelease: string | null;
  };
};

type ReleasePolicy = {
  availability: Availability;
  applicationMode: string;
  applied: boolean;
  version: number;
  targetReleaseId: string | null;
  rollbackReleaseId: string | null;
  channel: "stable" | "canary" | null;
  canaryPercent: number | null;
  reason: string | null;
  updatedAt: string | null;
  updatedBy: string | null;
};

type InternalSnapshot = {
  actor: Actor;
  metrics: PlatformMetrics;
  organizations: Organization[];
  selectedOrganization: Organization | null;
  agents: Agent[];
  staff: StaffMember[];
  auditLogs: AuditLog[];
  accessRequests: AccessRequest[];
  usageDaily: UsageDay[];
  system: SystemStatus;
  operations: InternalOperations;
  releasePolicy: ReleasePolicy;
};

const NAV: Array<{ id: ViewId; code: string; label: string; labelZh: string; group: string }> = [
  { id: "overview", code: "01", label: "Platform overview", labelZh: "平台总览", group: "PLATFORM" },
  { id: "organizations", code: "02", label: "Organizations", labelZh: "客户组织", group: "PLATFORM" },
  { id: "access", code: "03", label: "Pilot applications", labelZh: "试用申请", group: "PLATFORM" },
  { id: "agents", code: "04", label: "Agents", labelZh: "Agents", group: "OPERATIONS" },
  { id: "memory", code: "05", label: "Memory usage", labelZh: "记忆用量", group: "OPERATIONS" },
  { id: "operations", code: "06", label: "Runtime operations", labelZh: "运行监控", group: "OPERATIONS" },
  { id: "staff", code: "07", label: "Internal staff", labelZh: "内部成员", group: "GOVERNANCE" },
  { id: "audit", code: "08", label: "Platform Audit", labelZh: "平台 Audit", group: "GOVERNANCE" },
  { id: "system", code: "09", label: "System", labelZh: "系统", group: "GOVERNANCE" },
];

const GROUP_LABELS: Record<string, string> = { PLATFORM: "平台", OPERATIONS: "运行", GOVERNANCE: "治理" };

const isRecord = (value: unknown): value is UnknownRecord => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const records = (value: unknown) => (Array.isArray(value) ? value.filter(isRecord) : []);
const pick = (source: UnknownRecord, keys: string[]) => {
  for (const key of keys) {
    const value = source[key];
    if (value !== undefined && value !== null) return value;
  }
  return undefined;
};
const stringValue = (source: UnknownRecord, keys: string[], fallback = "") => {
  const value = pick(source, keys);
  return typeof value === "string" || typeof value === "number" ? String(value) : fallback;
};
const numberValue = (source: UnknownRecord, keys: string[]): number | null => {
  const value = pick(source, keys);
  if (value === undefined || value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};
const booleanValue = (source: UnknownRecord, keys: string[]): boolean | null => {
  const value = pick(source, keys);
  return typeof value === "boolean" ? value : null;
};
const dateValue = (source: UnknownRecord, keys: string[]) => {
  const value = pick(source, keys);
  if (typeof value === "number" && Number.isFinite(value)) {
    return new Date(value).toISOString();
  }
  if (typeof value === "string" && /^\d{12,}$/.test(value)) {
    const timestamp = Number(value);
    if (Number.isFinite(timestamp)) return new Date(timestamp).toISOString();
  }
  return typeof value === "string" ? value : null;
};

function normalizeOrganization(item: UnknownRecord, index = 0): Organization {
  return {
    id: stringValue(item, ["id", "organizationId", "organization_id"], `organization-${index + 1}`),
    version: numberValue(item, ["version", "rowVersion", "row_version", "expectedVersion", "expected_version"]),
    name: stringValue(item, ["name", "organizationName", "organization_name"], "Unnamed organization"),
    slug: stringValue(item, ["slug", "organizationSlug", "organization_slug"]),
    status: stringValue(item, ["status", "state"], "unknown").toLowerCase(),
    plan: stringValue(item, ["plan", "tier"], "Not reported"),
    ownerEmail: stringValue(item, ["ownerEmail", "owner_email"]),
    memberCount: numberValue(item, ["memberCount", "member_count", "users", "userCount", "user_count"]),
    agentCount: numberValue(item, ["agentCount", "agent_count", "agents"]),
    memoryEventCount: numberValue(item, ["memoryEventCount", "memory_event_count", "memoryEvents", "memory_events"]),
    memoryEdgeCount: numberValue(item, ["memoryEdgeCount", "memory_edge_count", "memoryEdges", "memory_edges"]),
    apiKeyCount: numberValue(item, ["apiKeyCount", "api_key_count", "apiKeys", "api_keys"]),
    writes7d: numberValue(item, ["writes7d", "writes_7d", "eventWrites7d", "event_writes_7d"]),
    dataRegion: stringValue(item, ["dataRegion", "data_region", "region"], "Not reported"),
    retentionDays: numberValue(item, ["retentionDays", "retention_days"]),
    createdAt: dateValue(item, ["createdAt", "created_at"]),
    updatedAt: dateValue(item, ["updatedAt", "updated_at"]),
  };
}

function availabilityValue(source: UnknownRecord): Availability {
  const value = stringValue(source, ["availability"]).toLowerCase();
  return value === "available" || value === "partial" ? value : "unavailable";
}

function normalizeOperationSource(source: UnknownRecord): OperationSource {
  return {
    availability: availabilityValue(source),
    reason: stringValue(source, ["reason"]) || null,
    source: stringValue(source, ["source"]),
    checkedAt: dateValue(source, ["checkedAt", "checked_at"]),
    httpStatus: numberValue(source, ["httpStatus", "http_status"]),
    probeLatencyMs: numberValue(source, ["probeLatencyMs", "probe_latency_ms"]),
    serviceLatencyMs: numberValue(source, ["serviceLatencyMs", "service_latency_ms"]),
  };
}

function booleanChecks(value: unknown): Record<string, boolean> | null {
  if (!isRecord(value)) return null;
  const entries = Object.entries(value).filter(([, entry]) => typeof entry === "boolean");
  return entries.length ? Object.fromEntries(entries) as Record<string, boolean> : null;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((entry): entry is string => typeof entry === "string")
    : [];
}

function normalizeOperations(value: unknown): InternalOperations {
  const source = isRecord(value) ? value : {};
  const healthRaw = isRecord(source.health) ? source.health : {};
  const readinessRaw = isRecord(source.readiness) ? source.readiness : {};
  const deploymentRaw = isRecord(source.deployment) ? source.deployment : {};
  const preflightRaw = isRecord(source.startupPreflight) ? source.startupPreflight : {};
  const queueRaw = isRecord(source.queue) ? source.queue : {};
  const latencyRaw = isRecord(source.latency) ? source.latency : {};
  const costsRaw = isRecord(source.costs) ? source.costs : {};
  const releaseRaw = isRecord(source.release) ? source.release : {};
  const absentReason = "Internal API did not return this operational data source.";

  return {
    collectedAt: dateValue(source, ["collectedAt", "collected_at"]),
    health: {
      ...normalizeOperationSource(healthRaw),
      status: stringValue(healthRaw, ["status"], "unavailable"),
      service: stringValue(healthRaw, ["service"]) || null,
      version: stringValue(healthRaw, ["version"]) || null,
    },
    readiness: {
      ...normalizeOperationSource(readinessRaw),
      status: stringValue(readinessRaw, ["status"], "unavailable"),
      service: stringValue(readinessRaw, ["service"]) || null,
      version: stringValue(readinessRaw, ["version"]) || null,
      checks: booleanChecks(readinessRaw.checks),
      snapshotStale: booleanValue(readinessRaw, ["snapshotStale", "snapshot_stale"]),
      snapshotAgeSeconds: numberValue(readinessRaw, ["snapshotAgeSeconds", "snapshot_age_seconds"]),
      monitorGeneration: numberValue(readinessRaw, ["monitorGeneration", "monitor_generation"]),
    },
    deployment: {
      ...normalizeOperationSource(deploymentRaw),
      status: stringValue(deploymentRaw, ["status"], "unavailable"),
      service: stringValue(deploymentRaw, ["service"]) || null,
      release: stringValue(deploymentRaw, ["release"]) || null,
      upstreamStatus: numberValue(deploymentRaw, ["upstreamStatus", "upstream_status"]),
    },
    startupPreflight: {
      availability: availabilityValue(preflightRaw),
      reason: stringValue(preflightRaw, ["reason"], absentReason),
      status: stringValue(preflightRaw, ["status"]) || null,
      mode: stringValue(preflightRaw, ["mode"]) || null,
      releaseId: stringValue(preflightRaw, ["releaseId", "release_id"]) || null,
      completedAt: dateValue(preflightRaw, ["completedAt", "completed_at"]),
      failedChecks: stringList(preflightRaw.failedChecks ?? preflightRaw.failed_checks),
    },
    queue: {
      availability: availabilityValue(queueRaw),
      reason: stringValue(queueRaw, ["reason"], absentReason),
      pending: numberValue(queueRaw, ["pending"]),
      running: numberValue(queueRaw, ["running"]),
      failed: numberValue(queueRaw, ["failed"]),
      active: numberValue(queueRaw, ["active"]),
      activeLimit: numberValue(queueRaw, ["activeLimit", "active_limit"]),
      recentErrorTotal: numberValue(queueRaw, ["recentErrorTotal", "recent_error_total"]),
      recentErrors: stringList(queueRaw.recentErrors ?? queueRaw.recent_errors),
    },
    latency: {
      availability: availabilityValue(latencyRaw),
      reason: stringValue(latencyRaw, ["reason"], absentReason),
      healthProbeMs: numberValue(latencyRaw, ["healthProbeMs", "health_probe_ms"]),
      healthServiceMs: numberValue(latencyRaw, ["healthServiceMs", "health_service_ms"]),
      readinessProbeMs: numberValue(latencyRaw, ["readinessProbeMs", "readiness_probe_ms"]),
      readinessServiceMs: numberValue(latencyRaw, ["readinessServiceMs", "readiness_service_ms"]),
      deploymentProbeMs: numberValue(latencyRaw, ["deploymentProbeMs", "deployment_probe_ms"]),
      sampleWindowSeconds: numberValue(latencyRaw, ["sampleWindowSeconds", "sample_window_seconds"]),
      sampleCount: numberValue(latencyRaw, ["sampleCount", "sample_count"]),
      p50Ms: numberValue(latencyRaw, ["p50Ms", "p50_ms"]),
      p95Ms: numberValue(latencyRaw, ["p95Ms", "p95_ms"]),
      p99Ms: numberValue(latencyRaw, ["p99Ms", "p99_ms"]),
      recallP50Ms: numberValue(latencyRaw, ["recallP50Ms", "recall_p50_ms"]),
      recallP95Ms: numberValue(latencyRaw, ["recallP95Ms", "recall_p95_ms"]),
      recallP99Ms: numberValue(latencyRaw, ["recallP99Ms", "recall_p99_ms"]),
      writeP50Ms: numberValue(latencyRaw, ["writeP50Ms", "write_p50_ms"]),
      writeP95Ms: numberValue(latencyRaw, ["writeP95Ms", "write_p95_ms"]),
      writeP99Ms: numberValue(latencyRaw, ["writeP99Ms", "write_p99_ms"]),
    },
    costs: {
      availability: availabilityValue(costsRaw),
      reason: stringValue(costsRaw, ["reason"], absentReason),
      currency: stringValue(costsRaw, ["currency"]) || null,
      periodStart: dateValue(costsRaw, ["periodStart", "period_start"]),
      periodEnd: dateValue(costsRaw, ["periodEnd", "period_end"]),
      knownCostMicroCny: numberValue(costsRaw, ["knownCostMicroCny", "known_cost_micro_cny"]),
      unknownCallCount: numberValue(costsRaw, ["unknownCallCount", "unknown_call_count"]),
      registeredCallCount: numberValue(costsRaw, ["registeredCallCount", "registered_call_count"]),
      completedCallCount: numberValue(costsRaw, ["completedCallCount", "completed_call_count"]),
      failedCallCount: numberValue(costsRaw, ["failedCallCount", "failed_call_count"]),
      inputTokens: numberValue(costsRaw, ["inputTokens", "input_tokens"]),
      outputTokens: numberValue(costsRaw, ["outputTokens", "output_tokens"]),
    },
    release: {
      availability: availabilityValue(releaseRaw),
      reason: stringValue(releaseRaw, ["reason"], absentReason),
      websiteRelease: stringValue(releaseRaw, ["websiteRelease", "website_release"]) || null,
      apiVersion: stringValue(releaseRaw, ["apiVersion", "api_version"]) || null,
      apiReleaseId: stringValue(releaseRaw, ["apiReleaseId", "api_release_id"]) || null,
      releaseSha256: stringValue(releaseRaw, ["releaseSha256", "release_sha256"]) || null,
      channel: stringValue(releaseRaw, ["channel"]) || null,
      canaryPercent: numberValue(releaseRaw, ["canaryPercent", "canary_percent"]),
      previousRelease: stringValue(releaseRaw, ["previousRelease", "previous_release"]) || null,
    },
  };
}

function normalizeSnapshot(input: unknown, fallbackActor: Actor): InternalSnapshot {
  const envelope = isRecord(input) ? input : {};
  const source = isRecord(envelope.snapshot) ? envelope.snapshot : isRecord(envelope.data) ? envelope.data : envelope;
  const actorRaw = isRecord(source.actor) ? source.actor : {};
  const metricRaw = isRecord(source.metrics) ? source.metrics : isRecord(source.platformMetrics) ? source.platformMetrics : {};
  const systemRaw = isRecord(source.system) ? source.system : isRecord(source.systemStatus) ? source.systemStatus : {};
  const releasePolicyRaw = isRecord(source.releasePolicy)
    ? source.releasePolicy
    : isRecord(source.release_policy)
      ? source.release_policy
      : {};

  const organizations = records(source.organizations ?? source.orgs).map(normalizeOrganization);
  const detailRaw = isRecord(source.detail) ? source.detail : {};
  const selectedRaw = isRecord(source.selectedOrganization)
    ? source.selectedOrganization
    : isRecord(source.selected_organization)
      ? source.selected_organization
      : isRecord(source.organizationDetail)
        ? source.organizationDetail
        : isRecord(source.organization_detail)
          ? source.organization_detail
          : isRecord(detailRaw.organization)
            ? detailRaw.organization
      : isRecord(source.organization)
        ? source.organization
        : null;
  const selectedOrganization = selectedRaw
    ? normalizeOrganization(selectedRaw)
    : organizations.find((organization) => organization.id === stringValue(source, ["selectedOrganizationId", "selected_organization_id"])) ?? null;

  const organizationNames = new Map(organizations.map((organization) => [organization.id, organization.name]));
  const agentRows = records(source.agents);
  if (selectedRaw && Array.isArray(selectedRaw.agents)) agentRows.push(...records(selectedRaw.agents));

  const agents = agentRows.map((item, index): Agent => {
    const organizationId = stringValue(item, ["organizationId", "organization_id", "orgId", "org_id"]);
    return {
      id: stringValue(item, ["id", "agentId", "agent_id"], `agent-${index + 1}`),
      organizationId,
      organizationName: stringValue(item, ["organizationName", "organization_name"], organizationNames.get(organizationId) ?? "Unknown organization"),
      name: stringValue(item, ["name", "displayName", "display_name"], "Unnamed agent"),
      slug: stringValue(item, ["slug"]),
      environment: stringValue(item, ["environment", "env"], "Not reported"),
      status: stringValue(item, ["status", "state"], "unknown").toLowerCase(),
      eventCount: numberValue(item, ["eventCount", "event_count", "memoryEventCount", "memory_event_count"]),
      edgeCount: numberValue(item, ["edgeCount", "edge_count", "memoryEdgeCount", "memory_edge_count"]),
      lastWriteAt: dateValue(item, ["lastWriteAt", "last_write_at", "lastEventAt", "last_event_at"]),
      createdAt: dateValue(item, ["createdAt", "created_at"]),
    };
  }).filter((agent, index, all) => all.findIndex((candidate) => candidate.organizationId === agent.organizationId && candidate.id === agent.id) === index);

  const staffSource = source.staff ?? source.internalStaff ?? source.internal_staff;
  const auditSource = source.auditLogs ?? source.audit_logs ?? source.audit;
  const accessSource = source.accessRequests ?? source.access_requests ?? source.earlyAccessRequests ?? source.early_access_requests;
  const usageSource = source.usageDaily ?? source.usage_daily ?? source.writesDaily ?? source.writes_daily;

  return {
    actor: {
      displayName: stringValue(actorRaw, ["displayName", "display_name", "name"], fallbackActor.displayName),
      email: stringValue(actorRaw, ["email"], fallbackActor.email),
      role: stringValue(actorRaw, ["role"], fallbackActor.role),
    },
    metrics: {
      organizations: numberValue(metricRaw, ["organizations", "organizationCount", "organization_count"]),
      users: numberValue(metricRaw, ["users", "userCount", "user_count", "members"]),
      agents: numberValue(metricRaw, ["agents", "agentCount", "agent_count"]),
      memoryEvents: numberValue(metricRaw, ["memoryEvents", "memory_events", "eventCount", "event_count"]),
      memoryEdges: numberValue(metricRaw, ["memoryEdges", "memory_edges", "edgeCount", "edge_count"]),
      apiKeys: numberValue(metricRaw, ["apiKeys", "api_keys", "apiKeyCount", "api_key_count"]),
      accessRequests: numberValue(metricRaw, ["accessRequests", "access_requests", "earlyAccessRequests", "early_access_requests"]),
      newAccessRequests: numberValue(metricRaw, ["newAccessRequests", "new_access_requests"]),
      writes7d: numberValue(metricRaw, ["writes7d", "writes_7d", "eventWrites7d", "event_writes_7d"]),
      updatedAt: dateValue(metricRaw, ["updatedAt", "updated_at", "asOf", "as_of"]),
    },
    organizations,
    selectedOrganization,
    agents,
    staff: records(staffSource).map((item, index) => ({
      id: stringValue(item, ["id", "staffId", "staff_id", "userId", "user_id"], `staff-${index + 1}`),
      name: stringValue(item, ["name", "displayName", "display_name"], "Pending member"),
      email: stringValue(item, ["email"]),
      role: stringValue(item, ["role"], "viewer"),
      status: stringValue(item, ["status"], "unknown").toLowerCase(),
      joinedAt: dateValue(item, ["joinedAt", "joined_at", "createdAt", "created_at"]),
      lastSeenAt: dateValue(item, ["lastSeenAt", "last_seen_at", "lastActiveAt", "last_active_at"]),
    })),
    auditLogs: records(auditSource).map((item, index) => ({
      id: stringValue(item, ["id", "auditId", "audit_id"], `audit-${index + 1}`),
      createdAt: dateValue(item, ["createdAt", "created_at", "timestamp"]),
      actor: stringValue(item, ["actor", "actorEmail", "actor_email", "actorId", "actor_id"], "System"),
      action: stringValue(item, ["action"], "unknown"),
      targetType: stringValue(item, ["targetType", "target_type", "resourceType", "resource_type"], "resource"),
      targetId: stringValue(item, ["targetId", "target_id", "resourceId", "resource_id"], "—"),
      organizationId: stringValue(item, ["organizationId", "organization_id"], "—"),
      result: stringValue(item, ["result", "status"], "unknown").toLowerCase(),
      requestId: stringValue(item, ["requestId", "request_id", "traceId", "trace_id"], "—"),
    })),
    accessRequests: records(accessSource).map((item, index) => ({
      id: stringValue(item, ["id", "requestId", "request_id"], `access-${index + 1}`),
      version: numberValue(item, ["version"]),
      email: stringValue(item, ["email", "emailDisplay", "email_display"]),
      contactName: stringValue(item, ["contactName", "contact_name"], "—"),
      companyName: stringValue(item, ["companyName", "company_name"], "—"),
      industry: stringValue(item, ["industry"], "other"),
      companySize: stringValue(item, ["companySize", "company_size"], "—"),
      primaryUseCase: stringValue(item, ["primaryUseCase", "primary_use_case", "useCase", "use_case"]),
      platforms: Array.isArray(item.platforms) ? item.platforms.filter((value): value is string => typeof value === "string") : [],
      timeline: stringValue(item, ["timeline"], "—"),
      source: stringValue(item, ["source"], "website"),
      status: stringValue(item, ["status"], "new").toLowerCase(),
      reviewNote: stringValue(item, ["reviewNote", "review_note"]),
      lastReviewedBy: stringValue(item, ["lastReviewedBy", "last_reviewed_by"]),
      lastReviewedAt: dateValue(item, ["lastReviewedAt", "last_reviewed_at"]),
      createdAt: dateValue(item, ["createdAt", "created_at"]),
      updatedAt: dateValue(item, ["updatedAt", "updated_at"]),
    })),
    usageDaily: records(usageSource).map((item) => ({
      date: stringValue(item, ["date", "day"]),
      writes: numberValue(item, ["writes", "eventWrites", "event_writes", "events"]),
    })),
    system: {
      accessGate: stringValue(systemRaw, ["accessGate", "access_gate", "sitesAccess", "sites_access", "accessPolicy", "access_policy"]) || null,
      authMode: stringValue(systemRaw, ["authMode", "auth_mode", "authentication"]) || null,
      database: stringValue(systemRaw, ["database", "storage", "databaseType", "database_type"]) || null,
      databaseBinding: stringValue(systemRaw, ["databaseBinding", "database_binding", "d1Binding", "d1_binding", "d1"]) || null,
      auditProtection: stringValue(systemRaw, ["auditProtection", "audit_protection", "auditMode", "audit_mode", "auditPolicy", "audit_policy"]) || null,
      environment: stringValue(systemRaw, ["environment", "runtime"]) || null,
      dataRegion: stringValue(systemRaw, ["dataRegion", "data_region", "region"]) || null,
      schemaVersion: stringValue(systemRaw, ["schemaVersion", "schema_version"]) || null,
    },
    operations: normalizeOperations(source.operations),
    releasePolicy: {
      availability: availabilityValue(releasePolicyRaw),
      applicationMode: stringValue(releasePolicyRaw, ["applicationMode", "application_mode"], "configuration_only"),
      applied: booleanValue(releasePolicyRaw, ["applied"]) === true,
      version: numberValue(releasePolicyRaw, ["version"]) ?? 0,
      targetReleaseId: stringValue(releasePolicyRaw, ["targetReleaseId", "target_release_id"]) || null,
      rollbackReleaseId: stringValue(releasePolicyRaw, ["rollbackReleaseId", "rollback_release_id"]) || null,
      channel: ["stable", "canary"].includes(stringValue(releasePolicyRaw, ["channel"]))
        ? stringValue(releasePolicyRaw, ["channel"]) as "stable" | "canary"
        : null,
      canaryPercent: numberValue(releasePolicyRaw, ["canaryPercent", "canary_percent"]),
      reason: stringValue(releasePolicyRaw, ["reason"]) || null,
      updatedAt: dateValue(releasePolicyRaw, ["updatedAt", "updated_at"]),
      updatedBy: stringValue(releasePolicyRaw, ["updatedBy", "updated_by"]) || null,
    },
  };
}

function formatNumber(value: number | null) {
  return value === null ? "—" : new Intl.NumberFormat("en-US", { notation: value >= 1_000_000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(value);
}

function formatMilliseconds(value: number | null) {
  return value === null ? null : `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value)} ms`;
}

function formatDate(value: string | null, includeTime = true, language: Language = "en") {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(language === "zh" ? "zh-CN" : "en-GB", includeTime
    ? { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }
    : { day: "2-digit", month: "short" }).format(date);
}

function statusTone(status: string) {
  const normalized = status.toLowerCase();
  if (["unavailable", "not_ready", "unhealthy", "degraded", "archived", "revoked", "suspended", "failed", "error", "closed"].some((value) => normalized.includes(value))) return "bad";
  if (["active", "enabled", "success", "healthy", "ready", "protected", "private", "bound", "qualified", "available"].some((value) => normalized.includes(value))) return "good";
  return "neutral";
}

function Status({ value }: { value: string }) {
  const { t } = useLanguage();
  const labels: Record<string, string> = { active: "已启用", enabled: "已启用", success: "成功", healthy: "健康", ready: "就绪", protected: "已保护", private: "私有", bound: "已绑定", available: "可用", partial: "部分可用", unavailable: "不可用", not_ready: "未就绪", unhealthy: "不健康", degraded: "降级", archived: "已停用", revoked: "已吊销", suspended: "已暂停", failed: "失败", error: "错误", invited: "待接受", pending: "待处理", new: "待审核", contacted: "已联系", qualified: "符合条件", closed: "已关闭", unknown: "未知" };
  const normalized = (value || "unknown").toLowerCase();
  return <span className={`internal-status is-${statusTone(value)}`}><i aria-hidden="true" />{t(value || "unknown", (labels[normalized] ?? value) || "未知")}</span>;
}

function EmptyState({ code, title, children }: { code: string; title: string; children: ReactNode }) {
  const { t } = useLanguage();
  return (
    <section className="internal-empty">
      <span aria-hidden="true">{code}</span>
      <div><p className="internal-kicker">{t("No records", "暂无记录")}</p><h2>{title}</h2><div>{children}</div></div>
    </section>
  );
}

export default function InternalClient({ initialActor, signOutPath }: { initialActor: Actor; signOutPath: string }) {
  const { language, t } = useLanguage();
  const [snapshot, setSnapshot] = useState<InternalSnapshot | null>(null);
  const [view, setView] = useState<ViewId>("overview");
  const [selectedOrganizationId, setSelectedOrganizationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const [pendingInvitation, setPendingInvitation] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [navOpen, setNavOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [accessSearch, setAccessSearch] = useState("");
  const [inviteOpen, setInviteOpen] = useState(false);

  const load = useCallback(async (organizationId?: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const query = organizationId ? `?organizationId=${encodeURIComponent(organizationId)}` : "";
      const response = await fetch(`/api/internal${query}`, { credentials: "same-origin", cache: "no-store" });
      if (response.status === 403) {
        const body: unknown = await response.json().catch(() => ({}));
        const root = isRecord(body) ? body : {};
        const errorBody = isRecord(root.error) ? root.error : {};
        const code = stringValue(root, ["code"]) || stringValue(errorBody, ["code"]);
        const invitationPending = code === "internal_invitation_pending";
        setPendingInvitation(invitationPending);
        setAccessDenied(!invitationPending);
        return;
      }
      if (!response.ok) throw new Error(response.status === 401 ? t("Your session has expired. Sign in again.", "登录状态已过期，请重新登录。") : t("Internal platform data could not be loaded.", "暂时无法读取内部平台数据。"));
      const data: unknown = await response.json();
      const normalized = normalizeSnapshot(data, initialActor);
      setSnapshot(normalized);
      setAccessDenied(false);
      setPendingInvitation(false);
      const selectedId = normalized.selectedOrganization?.id ?? organizationId ?? null;
      setSelectedOrganizationId(selectedId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("Internal platform data could not be loaded.", "暂时无法读取内部平台数据。"));
    } finally {
      setLoading(false);
    }
  }, [initialActor, t]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void load(); });
    return () => window.cancelAnimationFrame(frame);
  }, [load]);
  useEffect(() => {
    document.title = t("TMCRA Internal — Control Plane", "TMCRA Internal — 平台控制面");
  }, [t]);
  useEffect(() => {
    const applyHash = () => {
      const candidate = window.location.hash.slice(1) as ViewId;
      if (NAV.some((item) => item.id === candidate)) setView(candidate);
    };
    applyHash();
    window.addEventListener("hashchange", applyHash);
    return () => window.removeEventListener("hashchange", applyHash);
  }, []);

  const postAction = useCallback(async (action: string, payload: UnknownRecord, successMessage: string) => {
    setMutating(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch("/api/internal", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, payload }),
      });
      if (response.status === 403) {
        setError(t("You do not have permission to perform this action.", "你没有执行此操作的权限。"));
        return false;
      }
      if (!response.ok) {
        if (response.status === 401) throw new Error(t("Your session has expired. Sign in again.", "登录状态已过期，请重新登录。"));
        if (response.status === 409) throw new Error(t("The record changed before this request completed. Refresh and try again.", "提交期间记录已经发生变化，请刷新后重试。"));
        if (response.status === 422) throw new Error(t("Check the submitted values and try again.", "请检查提交内容后重试。"));
        throw new Error(t("The platform change could not be completed.", "平台变更未能完成。"));
      }
      setNotice(successMessage);
      await load(selectedOrganizationId);
      return true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("The platform change could not be completed.", "平台变更未能完成。"));
      return false;
    } finally {
      setMutating(false);
    }
  }, [load, selectedOrganizationId, t]);

  const navigate = useCallback((nextView: ViewId) => {
    setView(nextView);
    setNavOpen(false);
    window.history.replaceState(null, "", `#${nextView}`);
  }, []);

  const selectOrganization = useCallback((organization: Organization) => {
    setSelectedOrganizationId(organization.id);
    navigate("organizations");
    void load(organization.id);
  }, [load, navigate]);

  const selectedOrganization = snapshot?.selectedOrganization
    ?? snapshot?.organizations.find((organization) => organization.id === selectedOrganizationId)
    ?? null;

  const filteredOrganizations = useMemo(() => {
    const value = search.trim().toLowerCase();
    if (!snapshot || !value) return snapshot?.organizations ?? [];
    return snapshot.organizations.filter((organization) => [organization.name, organization.slug, organization.id, organization.ownerEmail].some((field) => field.toLowerCase().includes(value)));
  }, [search, snapshot]);

  const filteredAccessRequests = useMemo(() => {
    const value = accessSearch.trim().toLowerCase();
    if (!snapshot || !value) return snapshot?.accessRequests ?? [];
    return snapshot.accessRequests.filter((request) => [request.contactName, request.email, request.companyName, request.industry, request.primaryUseCase, ...request.platforms].some((field) => field.toLowerCase().includes(value)));
  }, [accessSearch, snapshot]);

  const changeOrganizationStatus = useCallback(async (organization: Organization) => {
    const nextStatus = organization.status === "archived" ? "active" : "archived";
    const verb = nextStatus === "archived" ? t("Archive", "停用") : t("Reactivate", "恢复");
    const confirmSlug = window.prompt(t(`${verb} ${organization.name}?\n\nType the exact organization slug to continue:\n${organization.slug}`, `${verb} ${organization.name}？\n\n请输入完整 organization slug 以继续：\n${organization.slug}`));
    if (confirmSlug === null) return;
    if (confirmSlug !== organization.slug) {
      setError(t("Confirmation did not match the exact organization slug.", "输入内容与 organization slug 不一致。"));
      return;
    }
    const reasonInput = window.prompt(t("Enter an Audit reason (10–500 characters):", "请输入 Audit 原因（10–500 个字符）："));
    if (reasonInput === null) return;
    const reason = reasonInput.trim();
    if (reason.length < 10 || reason.length > 500) {
      setError(t("Audit reason must contain 10–500 characters.", "Audit 原因必须为 10–500 个字符。"));
      return;
    }
    await postAction("organization.set_status", {
      organizationId: organization.id,
      status: nextStatus,
      confirmSlug,
      reason,
      expectedVersion: organization.version,
    }, t(`${organization.name} is now ${nextStatus}.`, `${organization.name} 的状态已更新为 ${nextStatus}。`));
  }, [postAction, t]);

  if (pendingInvitation) {
    return (
      <main className="internal-state-page">
        <span className="internal-state-brand"><BrandMark /> TMCRA / INTERNAL</span>
        <LanguageToggle className="internal-language-toggle" />
        <p className="internal-kicker">{t("Invitation found · Internal RBAC", "已找到邀请 · Internal RBAC")}</p>
        <h1>{t("Invitation pending", "待接受内部邀请")}</h1>
        <p>{t("Your verified TMCRA Account matches an internal invitation. Accept it to activate your assigned control-plane Role.", "当前已验证的 TMCRA 账户与一条内部邀请匹配。接受邀请后，你被分配的 control-plane Role 才会生效。")}</p>
        {error && <p role="alert">{error}</p>}
        <div className="internal-state-actions">
          <button type="button" disabled={mutating} onClick={() => void postAction("staff.accept_invite", {}, t("Invitation accepted.", "邀请已接受。"))}>{mutating ? t("Accepting…", "正在接受...") : t("Accept invitation", "接受邀请")}</button>
          <a href={signOutPath}>{t("Sign out", "退出登录")}</a>
        </div>
      </main>
    );
  }

  if (accessDenied) {
    return (
      <main className="internal-state-page">
        <span className="internal-state-brand"><BrandMark /> TMCRA / INTERNAL</span>
        <LanguageToggle className="internal-language-toggle" />
        <p className="internal-kicker">403 · Internal RBAC</p>
        <h1>{t("Access denied", "无权访问")}</h1>
        <p>{t("Your TMCRA Account is authenticated, but it does not have an active internal Role. Ask a platform Owner to invite your verified email.", "TMCRA 账户已经通过验证，但当前账号没有有效的内部 Role。请联系平台 Owner 邀请你的已验证邮箱。")}</p>
        <div className="internal-state-actions"><Link href="/">{t("Return to TMCRA", "返回 TMCRA 官网")}</Link><a href={signOutPath}>{t("Sign out", "退出登录")}</a></div>
      </main>
    );
  }

  if (loading && !snapshot) {
    return (
      <main className="internal-state-page" aria-busy="true">
        <span className="internal-state-brand"><BrandMark /> TMCRA / INTERNAL</span>
        <LanguageToggle className="internal-language-toggle" />
        <p className="internal-kicker">{t("Establishing internal session", "正在建立内部 session")}</p>
        <h1>{t("Loading platform control plane", "正在加载平台 control plane")}</h1>
        <div className="internal-loading" aria-hidden="true"><i /><i /><i /><i /></div>
        <span className="sr-only">{t("Loading internal platform data", "正在加载内部平台数据")}</span>
      </main>
    );
  }

  if (!snapshot) {
    return (
      <main className="internal-state-page">
        <span className="internal-state-brand"><BrandMark /> TMCRA / INTERNAL</span>
        <LanguageToggle className="internal-language-toggle" />
        <p className="internal-kicker">{t("Platform unavailable", "平台暂不可用")}</p>
        <h1>{t("Internal data could not be loaded", "无法读取内部平台数据")}</h1>
        <p>{error ?? t("The internal API returned no platform snapshot.", "Internal API 没有返回 platform snapshot。")}</p>
        <button type="button" onClick={() => void load()}>{t("Retry", "重试")}</button>
      </main>
    );
  }

  const currentNav = NAV.find((item) => item.id === view) ?? NAV[0];
  const usageMax = Math.max(0, ...snapshot.usageDaily.map((day) => day.writes ?? 0));
  const readinessFailures = Object.entries(snapshot.operations.readiness.checks ?? {})
    .filter(([, ok]) => !ok)
    .map(([name]) => name);
  const readinessWarnings = [
    ...(snapshot.operations.readiness.availability === "unavailable"
      ? [snapshot.operations.readiness.reason || t("Readiness status is unavailable.", "运行就绪状态不可用。")]
      : []),
    ...(snapshot.operations.readiness.snapshotStale === true
      ? [t("The continuous readiness snapshot is stale.", "持续就绪快照已过期。")]
      : []),
    ...readinessFailures.map((name) => t(`Readiness check failed: ${name}`, `就绪检查失败：${name}`)),
  ];
  const availableOperationalSources = [
    snapshot.operations.health,
    snapshot.operations.readiness,
    snapshot.operations.deployment,
  ].filter((source) => source.availability === "available").length;

  return (
    <div className={`internal-shell${navOpen ? " nav-open" : ""}`}>
      <a className="skip-link" href="#internal-main">{t("Skip to main content", "跳到主内容")}</a>
      <aside className="internal-sidebar" aria-label={t("Internal navigation", "Internal 导航")}>
        <Link className="internal-logo" href="/internal" onClick={() => setNavOpen(false)}>
          <BrandMark /><b>TMCRA</b><i>INTERNAL</i>
        </Link>
        <nav>
          {["PLATFORM", "OPERATIONS", "GOVERNANCE"].map((group) => (
            <section className="internal-nav-group" key={group}>
              <span>{t(group, GROUP_LABELS[group])}</span>
              {NAV.filter((item) => item.group === group).map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={view === item.id ? "is-active" : ""}
                  aria-current={view === item.id ? "page" : undefined}
                  onClick={() => navigate(item.id)}
                >
                  <i>{item.code}</i><b>{t(item.label, item.labelZh)}</b>
                </button>
              ))}
            </section>
          ))}
        </nav>
        <div className="internal-auth-note">
          <span className="internal-kicker">{t("ACCESS CONTROL", "访问控制")}</span>
          <b>TMCRA Account + internal RBAC</b>
          <p>{t("No password surface is hosted by this application.", "本应用不保存也不处理密码。")}</p>
        </div>
      </aside>

      <div className="internal-workspace">
        <header className="internal-topbar">
          <button className="internal-nav-toggle" type="button" aria-label={t("Toggle navigation", "打开或关闭导航")} aria-expanded={navOpen} onClick={() => setNavOpen((open) => !open)}><span /><span /></button>
          <div className="internal-breadcrumb"><span>TMCRA CONTROL PLANE</span><i>/</i><b>{t(currentNav.label, currentNav.labelZh)}</b></div>
          <div className="internal-top-actions">
            <Link href="/console">{t("Customer Console", "客户 Console")}</Link>
            <LanguageToggle className="internal-language-toggle" />
            <details className="internal-actor">
              <summary><span>{snapshot.actor.displayName.slice(0, 1).toUpperCase()}</span><b>{snapshot.actor.displayName}</b><i>{snapshot.actor.role}</i></summary>
              <div><b>{snapshot.actor.email}</b><span>{snapshot.actor.role}</span><a href={signOutPath}>{t("Sign out", "退出登录")}</a></div>
            </details>
          </div>
        </header>

        <main id="internal-main" tabIndex={-1}>
          {error && <div className="internal-alert is-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError(null)} aria-label={t("Dismiss error", "关闭错误提示")}>×</button></div>}
          {notice && <div className="internal-alert" role="status"><span>{notice}</span><button type="button" onClick={() => setNotice(null)} aria-label={t("Dismiss notice", "关闭提示")}>×</button></div>}
          {loading && <div className="internal-refresh" role="status">{t("Refreshing platform data…", "正在刷新平台数据...")}</div>}

          {view === "overview" && (
            <>
              <PageHeader eyebrow="INTERNAL / 01" title={t("Platform overview", "平台总览")} description={t("Global operational inventory across every TMCRA organization. Values are reported by the internal API; unavailable fields remain blank.", "集中查看全部 TMCRA 客户组织的运行库存。数据以 Internal API 返回为准；没有可靠数据的字段不会推测填充。")} actions={<button type="button" className="button secondary" onClick={() => void load(selectedOrganizationId)} disabled={loading}>{t("Refresh", "刷新")}</button>} />
              <section className="internal-metrics" aria-label={t("Platform metrics", "平台指标")}>
                <Metric label={t("Organizations", "客户组织")} value={snapshot.metrics.organizations} note={t("All tenant records", "全部 tenant 记录")} />
                <Metric label={t("Users", "用户")} value={snapshot.metrics.users} note={t("Organization members", "组织成员总数")} />
                <Metric label="Agents" value={snapshot.metrics.agents} note={t("Across environments", "跨全部环境")} />
                <Metric label={t("Memory events", "记忆事件")} value={snapshot.metrics.memoryEvents} note={t("Stored event nodes", "已存事件节点")} />
                <Metric label={t("Memory edges", "记忆关系边")} value={snapshot.metrics.memoryEdges} note={t("Stored relationships", "已存关系")} />
                <Metric label="API Keys" value={snapshot.metrics.apiKeys} note={t("Metadata only", "仅统计 metadata")} />
                <Metric label={t("New applications", "待审核申请")} value={snapshot.metrics.newAccessRequests} note={t("Pilot review queue", "试用审核队列")} />
              </section>
              <div className="internal-overview-grid">
                <section className="internal-panel usage-panel">
                  <PanelHeader code="WRITES / 7D" title={t("Memory ingestion", "记忆写入")} meta={snapshot.metrics.updatedAt ? t(`As of ${formatDate(snapshot.metrics.updatedAt, true, language)}`, `更新于 ${formatDate(snapshot.metrics.updatedAt, true, language)}`) : t("No snapshot timestamp", "没有 snapshot 时间")} />
                  {snapshot.usageDaily.length ? (
                    <div className="internal-chart" aria-label={t("Seven day memory write counts", "最近 7 天记忆写入量")}>
                      {snapshot.usageDaily.map((day) => {
                        const height = day.writes !== null && usageMax > 0 ? Math.max(3, (day.writes / usageMax) * 100) : 0;
                        return <div key={day.date}><span>{formatNumber(day.writes)}</span><i style={{ height: `${height}%` }} /><small>{formatDate(day.date, false, language)}</small></div>;
                      })}
                    </div>
                  ) : <PanelEmpty>{t("No daily write series has been reported.", "API 尚未返回每日写入序列。")}</PanelEmpty>}
                  <footer><span>{t("7 day writes", "7 天写入量")}</span><b>{formatNumber(snapshot.metrics.writes7d)}</b></footer>
                </section>
                <section className="internal-panel">
                  <PanelHeader code="TENANTS" title={t("Organizations", "客户组织")} meta={t(`${snapshot.organizations.length} rows returned`, `返回 ${snapshot.organizations.length} 条记录`)} />
                  {snapshot.organizations.length ? (
                    <div className="internal-compact-list">
                      {snapshot.organizations.slice(0, 7).map((organization) => (
                        <button type="button" key={organization.id} onClick={() => selectOrganization(organization)}>
                          <span><b>{organization.name}</b><small>{organization.slug || organization.id}</small></span>
                          <Status value={organization.status} />
                        </button>
                      ))}
                    </div>
                  ) : <PanelEmpty>{t("No organizations exist yet. Only the server-configured internal email can initialize the first Owner.", "目前还没有客户组织。只有服务端预先配置的内部邮箱才能初始化首位 Owner。")}</PanelEmpty>}
                </section>
              </div>
            </>
          )}

          {view === "organizations" && (
            <>
              <PageHeader eyebrow="PLATFORM / 02" title={t("Organizations", "客户组织")} description={t("Inspect tenant inventory and control organization lifecycle. Archive and reactivation actions require explicit confirmation.", "查看 tenant 库存并管理组织生命周期。停用或恢复组织都必须经过明确确认。")} />
              {!snapshot.organizations.length ? (
                <EmptyState code="00" title={t("No organizations found", "没有客户组织")}><p>{t("The platform returned an empty tenant inventory. Owner initialization is restricted to the server allowlist; organization creation remains server-controlled.", "平台当前没有客户组织。Owner 初始化仅对服务端白名单邮箱开放，组织创建仍由服务端控制。")}</p></EmptyState>
              ) : (
                <div className="internal-org-layout">
                  <section className="internal-panel">
                    <div className="internal-table-controls"><label>{t("Filter organizations", "筛选客户组织")}<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t("Name, slug, ID, or Owner", "名称、slug、ID 或 Owner")}/></label><span>{filteredOrganizations.length} / {snapshot.organizations.length}</span></div>
                    <TableShell>
                      <table className="internal-table">
                        <thead><tr><th>{t("Organization", "客户组织")}</th><th>{t("Status", "状态")}</th><th>Plan</th><th>Agents</th><th>{t("Events", "事件")}</th><th>{t("7d writes", "7 天写入")}</th><th /></tr></thead>
                        <tbody>{filteredOrganizations.map((organization) => (
                          <tr key={organization.id} className={selectedOrganization?.id === organization.id ? "is-selected" : ""}>
                            <td><b>{organization.name}</b><code>{organization.slug || organization.id}</code></td>
                            <td><Status value={organization.status} /></td><td>{organization.plan}</td><td>{formatNumber(organization.agentCount)}</td><td>{formatNumber(organization.memoryEventCount)}</td><td>{formatNumber(organization.writes7d)}</td>
                            <td><button type="button" className="table-link" onClick={() => selectOrganization(organization)}>{t("Inspect", "查看")}</button></td>
                          </tr>
                        ))}</tbody>
                      </table>
                    </TableShell>
                  </section>
                  <OrganizationInspector organization={selectedOrganization} mutating={mutating} onStatusChange={changeOrganizationStatus} />
                </div>
              )}
            </>
          )}

          {view === "access" && (
            <>
              <PageHeader eyebrow="PLATFORM / 03" title={t("Pilot applications", "试用申请")} description={t("Review application context, qualify technical fit and record the next step. Applications submitted on the public site enter this queue directly.", "集中处理官网提交的试用申请：查看业务与技术背景、判断接入适配度，并记录后续跟进状态。") } actions={<button type="button" className="button secondary" onClick={() => void load(selectedOrganizationId)} disabled={loading}>{t("Refresh", "刷新")}</button>} />
              <div className="internal-application-controls"><label>{t("Search applications", "搜索申请")}<input type="search" value={accessSearch} onChange={(event) => setAccessSearch(event.target.value)} placeholder={t("Name, email, company, industry, or platform", "姓名、邮箱、公司、行业或平台")} /></label><span>{filteredAccessRequests.length} / {snapshot.accessRequests.length}</span></div>
              {!snapshot.accessRequests.length ? <EmptyState code="AP" title={t("No pilot applications yet", "暂时没有试用申请")}><p>{t("New submissions from /access will appear here for Platform Owner, Platform Admin and Support roles.", "用户从 /access 提交申请后，Platform Owner、Platform Admin 和 Support 会在这里看到记录。")}</p></EmptyState> : !filteredAccessRequests.length ? <EmptyState code="00" title={t("No matching applications", "没有符合条件的申请")}><p>{t("Clear the search field or use a broader term.", "请清空搜索条件，或换一个更宽泛的关键词。")}</p></EmptyState> : (
                <div className="internal-application-grid">
                  {filteredAccessRequests.map((request) => <AccessRequestCard key={`${request.id}:${request.version}`} request={request} mutating={mutating} onSubmit={(status, reviewNote) => postAction("access_request.update", { requestId: request.id, status, reviewNote, expectedVersion: request.version }, t(`Application for ${request.companyName} updated.`, `${request.companyName} 的申请已更新。`))} />)}
                </div>
              )}
            </>
          )}

          {view === "agents" && (
            <>
              <PageHeader eyebrow="OPERATIONS / 04" title="Agents" description={t("Platform-wide Agent inventory. This surface is for internal operations and does not expose customer memory contents.", "查看全平台 Agent 库存。这个页面只服务内部运营，不展示客户的记忆正文。")} />
              {!snapshot.agents.length ? <EmptyState code="AG" title={t("No Agent records returned", "没有 Agent 记录")}><p>{t("Agents will appear here after an organization provisions them.", "客户组织创建 Agent 后，记录会显示在这里。")}</p></EmptyState> : (
                <TableShell><table className="internal-table"><thead><tr><th>Agent</th><th>{t("Organization", "客户组织")}</th><th>{t("Environment", "环境")}</th><th>{t("Status", "状态")}</th><th>{t("Events", "事件")}</th><th>{t("Edges", "关系边")}</th><th>{t("Last write", "最近写入")}</th></tr></thead><tbody>
                  {snapshot.agents.map((agent) => <tr key={`${agent.organizationId}:${agent.id}`}><td><b>{agent.name}</b><code>{agent.slug || agent.id}</code></td><td><button type="button" className="table-link" onClick={() => { const org = snapshot.organizations.find((item) => item.id === agent.organizationId); if (org) selectOrganization(org); }}>{agent.organizationName}</button></td><td>{agent.environment}</td><td><Status value={agent.status} /></td><td>{formatNumber(agent.eventCount)}</td><td>{formatNumber(agent.edgeCount)}</td><td>{formatDate(agent.lastWriteAt, true, language)}</td></tr>)}
                </tbody></table></TableShell>
              )}
            </>
          )}

          {view === "memory" && (
            <>
              <PageHeader eyebrow="OPERATIONS / 05" title={t("Memory usage", "记忆用量")} description={t("Aggregate memory footprint by organization. No memory text, API Tokens, or Key hashes are exposed in this internal surface.", "按客户组织汇总记忆占用。此内部页面不会暴露记忆正文、API Token 或 Key hash。")} />
              {!snapshot.organizations.length ? <EmptyState code="MX" title={t("No memory inventory available", "没有记忆用量数据")}><p>{t("Memory totals will appear when organizations are provisioned.", "客户组织完成创建后，这里会显示记忆总量。")}</p></EmptyState> : (
                <TableShell><table className="internal-table"><thead><tr><th>{t("Organization", "客户组织")}</th><th>{t("Status", "状态")}</th><th>{t("Memory events", "记忆事件")}</th><th>{t("Memory edges", "记忆关系边")}</th><th>{t("7d writes", "7 天写入")}</th><th>Agents</th><th>Retention</th></tr></thead><tbody>
                  {snapshot.organizations.map((organization) => <tr key={organization.id}><td><button type="button" className="table-link strong" onClick={() => selectOrganization(organization)}>{organization.name}</button><code>{organization.slug || organization.id}</code></td><td><Status value={organization.status} /></td><td>{formatNumber(organization.memoryEventCount)}</td><td>{formatNumber(organization.memoryEdgeCount)}</td><td>{formatNumber(organization.writes7d)}</td><td>{formatNumber(organization.agentCount)}</td><td>{organization.retentionDays === null ? "—" : t(`${organization.retentionDays} days`, `${organization.retentionDays} 天`)}</td></tr>)}
                </tbody></table></TableShell>
              )}
            </>
          )}

          {view === "operations" && (
            <>
              <PageHeader
                eyebrow="OPERATIONS / 06"
                title={t("Runtime operations", "运行监控")}
                description={t(
                  "Live operational facts collected after internal RBAC succeeds. Missing staff telemetry is reported as unavailable with its backend reason; no value is inferred from tenant data.",
                  "仅在内部权限校验通过后采集实时运行数据。缺少平台级遥测时会明确标为暂不可用并说明原因，不会根据租户数据猜测或补全。",
                )}
                actions={<button type="button" className="button secondary" onClick={() => void load(selectedOrganizationId)} disabled={loading}>{t("Refresh probes", "刷新探针")}</button>}
              />

              <section className="internal-operation-signals" aria-label={t("Operational status summary", "运行状态摘要")}>
                <OperationSignal code="LIVE" title={t("API liveness", "API 存活")} status={snapshot.operations.health.status} detail={snapshot.operations.health.service ?? "unavailable"} />
                <OperationSignal code="READY" title={t("API readiness", "API 就绪")} status={snapshot.operations.readiness.status} detail={snapshot.operations.readiness.snapshotAgeSeconds === null ? t("snapshot age unavailable", "暂无快照时间") : t(`${snapshot.operations.readiness.snapshotAgeSeconds}s snapshot age`, `快照生成于 ${snapshot.operations.readiness.snapshotAgeSeconds} 秒前`)} />
                <OperationSignal code="SITE" title={t("Website deployment", "网站部署")} status={snapshot.operations.deployment.status} detail={snapshot.operations.deployment.release ?? "unavailable"} />
                <OperationSignal code="DATA" title={t("Telemetry coverage", "遥测覆盖")} status={availableOperationalSources > 0 ? "partial" : "unavailable"} detail={t(`${availableOperationalSources}/3 live probe sources available`, `${availableOperationalSources}/3 个实时探针数据源可用`)} />
              </section>

              <div className="internal-operation-stack">
                <OperationBand code="RUNTIME" title={t("Health service and current version", "健康服务与当前版本")} status={snapshot.operations.readiness.status} reason={snapshot.operations.readiness.reason}>
                  <div className="internal-operation-fields">
                    <OperationalField label={t("Liveness", "存活状态")} value={<Status value={snapshot.operations.health.status} />} reason={snapshot.operations.health.reason} />
                    <OperationalField label={t("Readiness", "就绪状态")} value={<Status value={snapshot.operations.readiness.status} />} reason={snapshot.operations.readiness.reason} />
                    <OperationalField label={t("API service", "API 服务")} value={snapshot.operations.health.service} reason={snapshot.operations.health.reason} />
                    <OperationalField label={t("API version", "API 版本")} value={snapshot.operations.health.version} reason={snapshot.operations.health.reason} />
                    <OperationalField label={t("Website release", "网站版本")} value={snapshot.operations.deployment.release} reason={snapshot.operations.deployment.reason} />
                    <OperationalField label={t("Deployment upstream", "部署上游状态")} value={snapshot.operations.deployment.upstreamStatus === null ? null : `HTTP ${snapshot.operations.deployment.upstreamStatus}`} reason={snapshot.operations.deployment.reason} />
                    <OperationalField label={t("Probe collection time", "探针采集时间")} value={snapshot.operations.collectedAt ? formatDate(snapshot.operations.collectedAt, true, language) : null} reason={t("The probe collector did not return a timestamp.", "探针采集器未返回时间戳。")} />
                    <OperationalField label={t("Readiness generation", "就绪监控代次")} value={snapshot.operations.readiness.monitorGeneration === null ? null : String(snapshot.operations.readiness.monitorGeneration)} reason={snapshot.operations.readiness.reason} />
                  </div>
                  <div className="internal-readiness-checks">
                    <header><b>{t("Continuous readiness checks", "持续就绪检查")}</b><small>{snapshot.operations.readiness.source || "GET /readyz"}</small></header>
                    {snapshot.operations.readiness.checks ? Object.entries(snapshot.operations.readiness.checks).map(([name, ok]) => (
                      <div key={name}><code>{name}</code><Status value={ok ? "ready" : "failed"} /></div>
                    )) : <UnavailableBlock reason={snapshot.operations.readiness.reason || t("The readiness check map was not returned.", "服务端未返回就绪检查明细。")} />}
                  </div>
                </OperationBand>

                <OperationBand code="PREFLIGHT" title={t("Startup preflight warnings", "启动预检告警")} status={snapshot.operations.startupPreflight.availability} reason={snapshot.operations.startupPreflight.reason}>
                  {snapshot.operations.startupPreflight.availability === "unavailable" ? <UnavailableBlock reason={snapshot.operations.startupPreflight.reason} /> : <div className="internal-operation-fields">
                    <OperationalField label={t("Preflight status", "预检状态")} value={snapshot.operations.startupPreflight.status} reason={snapshot.operations.startupPreflight.reason} />
                    <OperationalField label={t("Preflight mode", "预检模式")} value={snapshot.operations.startupPreflight.mode} reason={snapshot.operations.startupPreflight.reason} />
                    <OperationalField label={t("Release ID", "Release ID")} value={snapshot.operations.startupPreflight.releaseId} reason={snapshot.operations.startupPreflight.reason} />
                    <OperationalField label={t("Completed", "完成时间")} value={snapshot.operations.startupPreflight.completedAt ? formatDate(snapshot.operations.startupPreflight.completedAt, true, language) : null} reason={snapshot.operations.startupPreflight.reason} />
                  </div>}
                  <div className="internal-live-warnings">
                    <b>{t("Live readiness warnings", "实时就绪告警")}</b>
                    {readinessWarnings.length ? <ul>{readinessWarnings.map((warning) => <li key={warning}>{warning}</li>)}</ul> : <p><Status value="ready" /> {t("No active warning is present in the current readiness snapshot.", "当前就绪快照中没有活动告警。")}</p>}
                  </div>
                </OperationBand>

                <OperationBand code="QUEUE" title={t("Queue errors", "队列错误")} status={snapshot.operations.queue.availability} reason={snapshot.operations.queue.reason}>
                  {snapshot.operations.queue.availability === "unavailable" ? <UnavailableBlock reason={snapshot.operations.queue.reason} /> : <>
                    <div className="internal-operation-fields">
                      <OperationalField label={t("Pending", "等待中")} value={snapshot.operations.queue.pending === null ? null : formatNumber(snapshot.operations.queue.pending)} reason={snapshot.operations.queue.reason} />
                      <OperationalField label={t("Running", "运行中")} value={snapshot.operations.queue.running === null ? null : formatNumber(snapshot.operations.queue.running)} reason={snapshot.operations.queue.reason} />
                      <OperationalField label={t("Failed", "失败")} value={snapshot.operations.queue.failed === null ? null : formatNumber(snapshot.operations.queue.failed)} reason={snapshot.operations.queue.reason} />
                      <OperationalField label={t("Active job capacity", "活动任务容量")} value={snapshot.operations.queue.active === null || snapshot.operations.queue.activeLimit === null ? null : `${snapshot.operations.queue.active} / ${snapshot.operations.queue.activeLimit}`} reason={snapshot.operations.queue.reason} />
                      <OperationalField label={t("Recent error total", "近期错误总数")} value={snapshot.operations.queue.recentErrorTotal === null ? null : formatNumber(snapshot.operations.queue.recentErrorTotal)} reason={snapshot.operations.queue.reason} />
                    </div>
                    {snapshot.operations.queue.recentErrors.length ? <ul className="internal-error-list">{snapshot.operations.queue.recentErrors.map((entry) => <li key={entry}>{entry}</li>)}</ul> : <p className="internal-operation-empty">{t("No recent queue error was returned.", "服务端未返回近期队列错误。")}</p>}
                  </>}
                </OperationBand>

                <OperationBand code="LATENCY" title={t("Latency monitoring", "延迟监控")} status={snapshot.operations.latency.availability} reason={snapshot.operations.latency.reason}>
                  <p className="internal-operation-boundary">{snapshot.operations.latency.reason}</p>
                  <div className="internal-operation-fields">
                    <OperationalField label={t("Health probe round trip", "健康探针往返")} value={formatMilliseconds(snapshot.operations.latency.healthProbeMs)} reason={snapshot.operations.health.reason} />
                    <OperationalField label={t("Health service processing", "健康服务处理")} value={formatMilliseconds(snapshot.operations.latency.healthServiceMs)} reason={snapshot.operations.health.reason} />
                    <OperationalField label={t("Readiness probe round trip", "就绪探针往返")} value={formatMilliseconds(snapshot.operations.latency.readinessProbeMs)} reason={snapshot.operations.readiness.reason} />
                    <OperationalField label={t("Readiness service processing", "就绪服务处理")} value={formatMilliseconds(snapshot.operations.latency.readinessServiceMs)} reason={snapshot.operations.readiness.reason} />
                    <OperationalField label={t("Website deployment probe", "网站部署探针")} value={formatMilliseconds(snapshot.operations.latency.deploymentProbeMs)} reason={snapshot.operations.deployment.reason} />
                    <OperationalField label="p50" value={formatMilliseconds(snapshot.operations.latency.p50Ms)} reason={snapshot.operations.latency.reason} />
                    <OperationalField label="p95" value={formatMilliseconds(snapshot.operations.latency.p95Ms)} reason={snapshot.operations.latency.reason} />
                    <OperationalField label="p99" value={formatMilliseconds(snapshot.operations.latency.p99Ms)} reason={snapshot.operations.latency.reason} />
                    <OperationalField label={t("Recall p50", "召回 p50")} value={formatMilliseconds(snapshot.operations.latency.recallP50Ms)} reason={t("The API has not exposed recall-specific percentiles.", "API 尚未提供召回专用延迟分位数。")} />
                    <OperationalField label={t("Recall p95", "召回 p95")} value={formatMilliseconds(snapshot.operations.latency.recallP95Ms)} reason={t("The API has not exposed recall-specific percentiles.", "API 尚未提供召回专用延迟分位数。")} />
                    <OperationalField label={t("Recall p99", "召回 p99")} value={formatMilliseconds(snapshot.operations.latency.recallP99Ms)} reason={t("The API has not exposed recall-specific percentiles.", "API 尚未提供召回专用延迟分位数。")} />
                    <OperationalField label={t("Write p50", "写入 p50")} value={formatMilliseconds(snapshot.operations.latency.writeP50Ms)} reason={t("The API has not exposed write-specific percentiles.", "API 尚未提供写入专用延迟分位数。")} />
                    <OperationalField label={t("Write p95", "写入 p95")} value={formatMilliseconds(snapshot.operations.latency.writeP95Ms)} reason={t("The API has not exposed write-specific percentiles.", "API 尚未提供写入专用延迟分位数。")} />
                    <OperationalField label={t("Write p99", "写入 p99")} value={formatMilliseconds(snapshot.operations.latency.writeP99Ms)} reason={t("The API has not exposed write-specific percentiles.", "API 尚未提供写入专用延迟分位数。")} />
                  </div>
                </OperationBand>

                <OperationBand code="COST" title={t("Platform cost monitoring", "平台成本监控")} status={snapshot.operations.costs.availability} reason={snapshot.operations.costs.reason}>
                  {snapshot.operations.costs.availability === "unavailable" ? <UnavailableBlock reason={snapshot.operations.costs.reason} /> : <div className="internal-operation-fields">
                    <OperationalField label={t("Currency", "币种")} value={snapshot.operations.costs.currency} reason={snapshot.operations.costs.reason} />
                    <OperationalField label={t("Known cost", "已知成本")} value={snapshot.operations.costs.knownCostMicroCny === null ? null : `${snapshot.operations.costs.knownCostMicroCny} micro CNY`} reason={snapshot.operations.costs.reason} />
                    <OperationalField label={t("Unknown calls", "未知计费调用")} value={snapshot.operations.costs.unknownCallCount === null ? null : formatNumber(snapshot.operations.costs.unknownCallCount)} reason={snapshot.operations.costs.reason} />
                    <OperationalField label={t("Registered calls", "已登记调用")} value={snapshot.operations.costs.registeredCallCount === null ? null : formatNumber(snapshot.operations.costs.registeredCallCount)} reason={snapshot.operations.costs.reason} />
                    <OperationalField label={t("Completed calls", "已完成调用")} value={snapshot.operations.costs.completedCallCount === null ? null : formatNumber(snapshot.operations.costs.completedCallCount)} reason={snapshot.operations.costs.reason} />
                    <OperationalField label={t("Failed calls", "失败调用")} value={snapshot.operations.costs.failedCallCount === null ? null : formatNumber(snapshot.operations.costs.failedCallCount)} reason={snapshot.operations.costs.reason} />
                    <OperationalField label={t("Input tokens", "输入 tokens")} value={snapshot.operations.costs.inputTokens === null ? null : formatNumber(snapshot.operations.costs.inputTokens)} reason={snapshot.operations.costs.reason} />
                    <OperationalField label={t("Output tokens", "输出 tokens")} value={snapshot.operations.costs.outputTokens === null ? null : formatNumber(snapshot.operations.costs.outputTokens)} reason={snapshot.operations.costs.reason} />
                  </div>}
                </OperationBand>

                <OperationBand code="RELEASE" title={t("Version and canary release", "版本与灰度发布")} status={snapshot.operations.release.availability} reason={snapshot.operations.release.reason}>
                  <p className="internal-operation-boundary">{snapshot.operations.release.reason}</p>
                  <div className="internal-operation-fields">
                    <OperationalField label={t("Website release", "网站版本")} value={snapshot.operations.release.websiteRelease} reason={snapshot.operations.deployment.reason} />
                    <OperationalField label={t("API version", "API 版本")} value={snapshot.operations.release.apiVersion} reason={snapshot.operations.health.reason} />
                    <OperationalField label="API Release ID" value={snapshot.operations.release.apiReleaseId} reason={snapshot.operations.release.reason} />
                    <OperationalField label={t("Release SHA-256", "Release SHA-256")} value={snapshot.operations.release.releaseSha256} reason={snapshot.operations.release.reason} />
                    <OperationalField label={t("Release channel", "发布通道")} value={snapshot.operations.release.channel} reason={snapshot.operations.release.reason} />
                    <OperationalField label={t("Canary allocation", "灰度流量")} value={snapshot.operations.release.canaryPercent === null ? null : `${snapshot.operations.release.canaryPercent}%`} reason={snapshot.operations.release.reason} />
                    <OperationalField label={t("Rollback target", "回滚目标")} value={snapshot.operations.release.previousRelease} reason={snapshot.operations.release.reason} />
                  </div>
                  <ReleasePolicyForm
                    key={snapshot.releasePolicy.version}
                    policy={snapshot.releasePolicy}
                    mutating={mutating}
                    canEdit={["platform_owner", "platform_admin"].includes(snapshot.actor.role)}
                    onSubmit={(payload) => postAction(
                      "release_policy.update",
                      payload,
                      t("Desired release policy saved and audited. It has not been applied to production.", "期望发布策略已保存并写入审计记录，但尚未应用到生产环境。"),
                    )}
                  />
                </OperationBand>
              </div>
              <p className="internal-system-note"><b>{t("Reporting boundary.", "数据边界。")}</b> {t("unavailable means the server did not expose a verifiable staff data source. It never means zero, healthy, or no errors.", "“暂不可用”表示服务端没有提供可验证的平台级数据源；它绝不等于数值为零、系统健康或没有错误。")}</p>
            </>
          )}

          {view === "staff" && (
            <>
              <PageHeader eyebrow="GOVERNANCE / 07" title={t("Internal staff", "内部成员")} description={t("Manage access to the TMCRA control plane. Authentication uses verified TMCRA Accounts; Roles are enforced by internal RBAC.", "管理 TMCRA control plane 的访问权限。身份认证使用已验证的 TMCRA 账户，Role 由 internal RBAC 强制执行。")} actions={<button type="button" className="button primary" onClick={() => setInviteOpen((open) => !open)}>{inviteOpen ? t("Close invite", "收起邀请表单") : t("Invite staff", "邀请内部成员")}</button>} />
              {inviteOpen && <InviteStaffForm mutating={mutating} onCancel={() => setInviteOpen(false)} onSubmit={async (email, role) => { const saved = await postAction("staff.add", { email, role }, t(`Invitation recorded for ${email}.`, `已记录发给 ${email} 的邀请。`)); if (saved) setInviteOpen(false); }} />}
              {!snapshot.staff.length ? <EmptyState code="RB" title={t("No staff roster returned", "没有内部成员记录")}><p>{t("The server-allowlisted internal email must sign in to initialize the first Owner. Additional staff can then be invited through RBAC.", "服务端白名单中的内部邮箱需要先登录并初始化首位 Owner，之后再通过 RBAC 邀请其他内部成员。")}</p></EmptyState> : (
                <TableShell><table className="internal-table"><thead><tr><th>{t("Staff member", "内部成员")}</th><th>Role</th><th>{t("Status", "状态")}</th><th>{t("Joined", "加入时间")}</th><th>{t("Last seen", "最近活动")}</th></tr></thead><tbody>
                  {snapshot.staff.map((member) => <tr key={member.id}><td><b>{member.name}</b><code>{member.email}</code></td><td>{member.role}</td><td><Status value={member.status} /></td><td>{formatDate(member.joinedAt, true, language)}</td><td>{formatDate(member.lastSeenAt, true, language)}</td></tr>)}
                </tbody></table></TableShell>
              )}
            </>
          )}

          {view === "audit" && (
            <>
              <PageHeader eyebrow="GOVERNANCE / 08" title={t("Platform Audit", "平台 Audit")} description={t("Immutable platform-level actions attributed to internal identities. Customer workspace Audit remains in each organization Console.", "平台级操作会以不可变记录归因到内部身份。客户工作区的 Audit 仍保留在各自的 Console 中。")} />
              {!snapshot.auditLogs.length ? <EmptyState code="AU" title={t("No platform Audit records returned", "没有平台 Audit 记录")}><p>{t("The Audit stream is empty or has not been made available to this Role.", "Audit stream 为空，或当前 Role 无权查看。")}</p></EmptyState> : (
                <TableShell><table className="internal-table audit-table"><thead><tr><th>{t("Time", "时间")}</th><th>Actor</th><th>Action</th><th>{t("Target", "目标")}</th><th>{t("Organization", "客户组织")}</th><th>{t("Result", "结果")}</th><th>Request</th></tr></thead><tbody>
                  {snapshot.auditLogs.map((log) => <tr key={log.id}><td>{formatDate(log.createdAt, true, language)}</td><td><code>{log.actor}</code></td><td><b>{log.action}</b></td><td>{log.targetType}<code>{log.targetId}</code></td><td><code>{log.organizationId}</code></td><td><Status value={log.result} /></td><td><code>{log.requestId}</code></td></tr>)}
                </tbody></table></TableShell>
              )}
            </>
          )}

          {view === "system" && (
            <>
              <PageHeader eyebrow="GOVERNANCE / 09" title={t("System", "系统")} description={t("Deployment facts reported by the server. This page intentionally does not infer uptime, health, replication, or regional residency.", "只展示服务端明确返回的部署事实，不推测 uptime、health、replication 或数据驻留区域。")} />
              <div className="internal-system-grid">
                <SystemPanel title={t("Access & identity", "访问与身份")} code="AUTH" items={[
                  [t("Sites access gate", "Sites 访问门禁"), snapshot.system.accessGate],
                  ["Authentication", snapshot.system.authMode],
                  ["Authorization", snapshot.actor.role === "Pending verification" ? null : `Internal RBAC · ${snapshot.actor.role}`],
                ]} />
                <SystemPanel title={t("Data plane", "Data plane")} code="DATA" items={[
                  ["Database", snapshot.system.database],
                  ["D1 binding", snapshot.system.databaseBinding],
                  [t("Data region", "Data region"), snapshot.system.dataRegion],
                  [t("Schema version", "Schema version"), snapshot.system.schemaVersion],
                ]} />
                <SystemPanel title={t("Audit controls", "Audit 控制")} code="AUDIT" items={[
                  [t("Audit protection", "Audit 保护"), snapshot.system.auditProtection],
                  [t("Runtime environment", "运行环境"), snapshot.system.environment],
                ]} />
              </div>
              <p className="internal-system-note"><b>{t("Reporting boundary.", "数据边界。")}</b> {t("“Not reported” means the Internal API did not provide a verifiable value. It is not interpreted as healthy or unhealthy.", "“暂无可验证数据”表示 Internal API 没有返回可核实的值；它既不代表系统正常，也不代表异常。")}</p>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

function PageHeader({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description: string; actions?: ReactNode }) {
  return <header className="internal-page-header"><div><p className="internal-kicker">{eyebrow}</p><h1>{title}</h1><p>{description}</p></div>{actions && <div className="internal-page-actions">{actions}</div>}</header>;
}

function Metric({ label, value, note }: { label: string; value: number | null; note: string }) {
  const { t } = useLanguage();
  return <article><span>{label}</span><b>{formatNumber(value)}</b><small>{value === null ? t("Not reported", "暂无可验证数据") : note}</small></article>;
}

function OperationSignal({ code, title, status, detail }: { code: string; title: string; status: string; detail: string }) {
  return <article><span className="internal-kicker">{code}</span><div><b>{title}</b><Status value={status} /></div><small>{detail}</small></article>;
}

function OperationBand({ code, title, status, reason, children }: { code: string; title: string; status: string; reason: string | null; children: ReactNode }) {
  return <section className="internal-operation-band"><header><div><span className="internal-kicker">{code}</span><h2>{title}</h2></div><div><Status value={status} />{reason && <small title={reason}>{reason}</small>}</div></header><div className="internal-operation-content">{children}</div></section>;
}

function OperationalField({ label, value, reason }: { label: string; value: ReactNode | null; reason: string | null }) {
  const { t } = useLanguage();
  const unavailable = value === null || value === undefined || value === "";
  return <div className={unavailable ? "is-unavailable" : ""}><dt>{label}</dt><dd>{unavailable ? <><code>unavailable</code><small>{reason || t("No verifiable data source was returned.", "服务端未返回可验证的数据源。")}</small></> : value}</dd></div>;
}

function UnavailableBlock({ reason }: { reason: string }) {
  return <div className="internal-unavailable" role="note"><code>unavailable</code><p>{reason}</p></div>;
}

function PanelHeader({ code, title, meta }: { code: string; title: string; meta: string }) {
  return <header className="internal-panel-header"><div><span className="internal-kicker">{code}</span><h2>{title}</h2></div><small>{meta}</small></header>;
}

function PanelEmpty({ children }: { children: ReactNode }) {
  return <div className="internal-panel-empty"><span aria-hidden="true">∅</span><p>{children}</p></div>;
}

function TableShell({ children }: { children: ReactNode }) {
  return <div className="internal-table-shell">{children}</div>;
}

function OrganizationInspector({ organization, mutating, onStatusChange }: { organization: Organization | null; mutating: boolean; onStatusChange: (organization: Organization) => void }) {
  const { language, t } = useLanguage();
  if (!organization) return <aside className="internal-inspector"><p className="internal-kicker">{t("ORGANIZATION DETAIL", "客户组织详情")}</p><h2>{t("Select an organization", "选择一个客户组织")}</h2><p>{t("Choose a row to load its server-reported detail.", "选择表格中的一行，查看服务端返回的详细信息。")}</p></aside>;
  const archived = organization.status === "archived";
  return <aside className="internal-inspector"><div className="internal-inspector-head"><div><p className="internal-kicker">{t("ORGANIZATION DETAIL", "客户组织详情")}</p><h2>{organization.name}</h2><code>{organization.id}</code></div><Status value={organization.status} /></div><dl>
    <div><dt>Slug</dt><dd>{organization.slug || "—"}</dd></div><div><dt>Version</dt><dd>{formatNumber(organization.version)}</dd></div><div><dt>Plan</dt><dd>{organization.plan}</dd></div><div><dt>Owner</dt><dd>{organization.ownerEmail || "—"}</dd></div><div><dt>{t("Members", "成员")}</dt><dd>{formatNumber(organization.memberCount)}</dd></div><div><dt>Agents</dt><dd>{formatNumber(organization.agentCount)}</dd></div><div><dt>{t("Memory events", "记忆事件")}</dt><dd>{formatNumber(organization.memoryEventCount)}</dd></div><div><dt>{t("Memory edges", "记忆关系边")}</dt><dd>{formatNumber(organization.memoryEdgeCount)}</dd></div><div><dt>API Keys</dt><dd>{formatNumber(organization.apiKeyCount)}</dd></div><div><dt>Data region</dt><dd>{organization.dataRegion}</dd></div><div><dt>{t("Created", "创建时间")}</dt><dd>{formatDate(organization.createdAt, true, language)}</dd></div>
  </dl><div className="internal-danger-zone"><b>{archived ? t("Reactivate organization", "恢复客户组织") : t("Archive organization", "停用客户组织")}</b><p>{archived ? t("Restore organization access after explicit confirmation.", "经过明确确认后恢复该组织的访问权限。") : t("Disable organization operations while preserving records.", "停止该组织的运行操作，但保留现有记录。")}</p><button type="button" disabled={mutating || !organization.slug} onClick={() => onStatusChange(organization)}>{mutating ? t("Applying…", "正在处理...") : archived ? t("Reactivate", "恢复") : t("Archive", "停用")}</button>{!organization.slug && <small>{t("Server did not return a slug; lifecycle confirmation is unavailable.", "服务端没有返回 slug，无法执行需要确认的生命周期操作。")}</small>}</div></aside>;
}

function AccessRequestCard({ request, mutating, onSubmit }: { request: AccessRequest; mutating: boolean; onSubmit: (status: string, reviewNote: string) => Promise<boolean> }) {
  const { language, t } = useLanguage();
  const [status, setStatus] = useState(request.status);
  const [reviewNote, setReviewNote] = useState(request.reviewNote);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await onSubmit(status, reviewNote.trim());
  };

  return (
    <article className="internal-application-card">
      <header><div><p className="internal-kicker">{request.source.toUpperCase()} · {formatDate(request.createdAt, false, language)}</p><h2>{request.companyName}</h2><span>{request.contactName} · <a href={`mailto:${request.email}`}>{request.email}</a></span></div><Status value={request.status} /></header>
      <dl>
        <div><dt>{t("Industry", "所属行业")}</dt><dd>{applicationLabel(request.industry, language)}</dd></div>
        <div><dt>{t("Team size", "团队规模")}</dt><dd>{request.companySize}</dd></div>
        <div><dt>{t("Timeline", "计划时间")}</dt><dd>{applicationLabel(request.timeline, language)}</dd></div>
        <div><dt>{t("Last update", "最近更新")}</dt><dd>{formatDate(request.updatedAt, true, language)}</dd></div>
      </dl>
      <div className="internal-application-platforms">{request.platforms.map((platform) => <span key={platform}>{platform}</span>)}</div>
      <section><span className="internal-kicker">{t("PILOT USE CASE", "试用场景")}</span><p>{request.primaryUseCase}</p></section>
      <form onSubmit={submit}>
        <label>{t("Review status", "审核状态")}<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="new">{t("New", "待审核")}</option><option value="contacted">{t("Contacted", "已联系")}</option><option value="qualified">{t("Qualified", "符合条件")}</option><option value="closed">{t("Closed", "已关闭")}</option></select></label>
        <label>{t("Internal review note", "内部审核备注")}<textarea rows={4} maxLength={2000} value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} placeholder={t("Record fit, owner, next step, or reason for closing.", "记录适配判断、跟进人、下一步安排或关闭原因。")}/></label>
        <div><small>{request.lastReviewedAt ? t(`Last reviewed by ${request.lastReviewedBy || "internal staff"} on ${formatDate(request.lastReviewedAt, true, language)}`, `上次由 ${request.lastReviewedBy || "内部成员"} 于 ${formatDate(request.lastReviewedAt, true, language)} 更新`) : t("Not reviewed yet", "尚未审核")}</small><button type="submit" className="button primary" disabled={mutating || request.version === null || (status === request.status && reviewNote.trim() === request.reviewNote)}>{mutating ? t("Saving…", "正在保存…") : t("Save review", "保存审核结果")}</button></div>
      </form>
    </article>
  );
}

function applicationLabel(value: string, language: Language) {
  const labels: Record<string, [string, string]> = {
    "ai-software": ["AI / Software", "AI / 软件"],
    "enterprise-services": ["Enterprise services", "企业服务"],
    consumer: ["Consumer products", "消费产品"],
    finance: ["Finance", "金融"],
    healthcare: ["Healthcare", "医疗健康"],
    education: ["Education", "教育"],
    robotics: ["Robotics", "机器人"],
    research: ["Research", "研究机构"],
    other: ["Other", "其他"],
    now: ["Ready now", "现在即可开始"],
    "30-days": ["Within 30 days", "30 天内"],
    quarter: ["This quarter", "本季度"],
    exploring: ["Exploring", "前期调研"],
  };
  return labels[value]?.[language === "zh" ? 1 : 0] ?? value;
}

function ReleasePolicyForm({
  policy,
  mutating,
  canEdit,
  onSubmit,
}: {
  policy: ReleasePolicy;
  mutating: boolean;
  canEdit: boolean;
  onSubmit: (payload: UnknownRecord) => Promise<unknown>;
}) {
  const { language, t } = useLanguage();
  const [targetReleaseId, setTargetReleaseId] = useState(policy.targetReleaseId ?? "");
  const [rollbackReleaseId, setRollbackReleaseId] = useState(policy.rollbackReleaseId ?? "");
  const [channel, setChannel] = useState<"stable" | "canary">(policy.channel ?? "stable");
  const [canaryPercent, setCanaryPercent] = useState(
    String(policy.canaryPercent ?? (policy.channel === "canary" ? 5 : 100)),
  );
  const [reason, setReason] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await onSubmit({
      targetReleaseId: targetReleaseId.trim(),
      rollbackReleaseId: rollbackReleaseId.trim() || null,
      channel,
      canaryPercent: Number(canaryPercent),
      reason: reason.trim(),
      expectedVersion: policy.version,
    });
  };

  const changeChannel = (next: "stable" | "canary") => {
    setChannel(next);
    setCanaryPercent(next === "stable" ? "100" : "5");
  };
  const validPercentage = Number.isFinite(Number(canaryPercent))
    && (channel === "stable"
      ? Number(canaryPercent) === 100
      : Number(canaryPercent) > 0 && Number(canaryPercent) < 100);
  const unchanged = targetReleaseId.trim() === (policy.targetReleaseId ?? "")
    && (rollbackReleaseId.trim() || null) === policy.rollbackReleaseId
    && channel === policy.channel
    && Number(canaryPercent) === policy.canaryPercent;

  return (
    <section className="internal-release-policy" aria-labelledby="release-policy-title">
      <header>
        <div>
          <p className="internal-kicker">DESIRED RELEASE POLICY / V{policy.version}</p>
          <h3 id="release-policy-title">{t("Audited release intent", "经审计的发布意图")}</h3>
        </div>
        <Status value={policy.applied ? "applied" : "configuration_only"} />
      </header>
      <p>{t(
        "Saving this form records an audited desired state. It does not deploy, restart, or shift production traffic.",
        "保存此表单只会记录并审计期望状态，不会直接部署、重启服务或切换生产流量。",
      )}</p>
      <form onSubmit={submit}>
        <label>{t("Target release ID", "目标版本 ID")}<input required maxLength={128} value={targetReleaseId} onChange={(event) => setTargetReleaseId(event.target.value)} disabled={!canEdit || mutating} placeholder="release-20260719-1" /></label>
        <label>{t("Rollback release ID", "回滚版本 ID")}<input maxLength={128} value={rollbackReleaseId} onChange={(event) => setRollbackReleaseId(event.target.value)} disabled={!canEdit || mutating} placeholder={t("Optional", "可选")} /></label>
        <label>{t("Channel", "发布通道")}<select value={channel} onChange={(event) => changeChannel(event.target.value as "stable" | "canary")} disabled={!canEdit || mutating}><option value="stable">stable</option><option value="canary">canary</option></select></label>
        <label>{t("Traffic allocation (%)", "流量比例（%）")}<input type="number" min={channel === "stable" ? 100 : 0.1} max={channel === "stable" ? 100 : 99.9} step="0.1" required value={canaryPercent} onChange={(event) => setCanaryPercent(event.target.value)} disabled={!canEdit || mutating || channel === "stable"} /></label>
        <label className="is-wide">{t("Audit reason", "审计原因")}<textarea required minLength={10} maxLength={500} rows={3} value={reason} onChange={(event) => setReason(event.target.value)} disabled={!canEdit || mutating} placeholder={t("Explain why this release target and allocation are required.", "说明为什么需要这个目标版本和流量比例。")}/></label>
        <div className="internal-release-policy-meta">
          <small>{policy.updatedAt ? t(`Last updated ${formatDate(policy.updatedAt, true, language)} by ${policy.updatedBy ?? "internal operator"}.`, `上次由 ${policy.updatedBy ?? "内部操作员"} 于 ${formatDate(policy.updatedAt, true, language)} 更新。`) : t("No desired release policy has been recorded yet.", "尚未记录期望发布策略。")}</small>
          <button type="submit" className="button primary" disabled={!canEdit || mutating || !validPercentage || reason.trim().length < 10 || unchanged}>{mutating ? t("Saving…", "正在保存…") : t("Save audited intent", "保存并写入审计")}</button>
        </div>
      </form>
      {!canEdit && <p className="internal-operation-boundary">{t("Only platform owners and administrators may change release intent.", "只有平台所有者和管理员可以修改发布意图。")}</p>}
    </section>
  );
}

function InviteStaffForm({ mutating, onCancel, onSubmit }: { mutating: boolean; onCancel: () => void; onSubmit: (email: string, role: string) => Promise<void> }) {
  const { t } = useLanguage();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("viewer");
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); await onSubmit(email.trim(), role); };
  return <section className="internal-form-panel" aria-labelledby="invite-staff-title"><div><p className="internal-kicker">INTERNAL RBAC</p><h2 id="invite-staff-title">{t("Invite staff member", "邀请内部成员")}</h2><p>{t("The invitation grants control-plane access after the email owner signs in with a verified TMCRA Account.", "对方使用已验证邮箱登录 TMCRA 账户并接受邀请后，才会获得 control-plane 访问权限。")}</p></div><form onSubmit={submit}><label>Email<input type="email" required autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="operator@tmcra.example" /></label><label>Role<select value={role} onChange={(event) => setRole(event.target.value)}><option value="viewer">Viewer</option><option value="operator">Operator</option><option value="admin">Admin</option><option value="owner">Owner</option></select></label><div><button type="button" className="button secondary" onClick={onCancel}>{t("Cancel", "取消")}</button><button type="submit" className="button primary" disabled={mutating}>{mutating ? t("Inviting…", "正在邀请...") : t("Invite staff", "发送邀请")}</button></div></form></section>;
}

function SystemPanel({ title, code, items }: { title: string; code: string; items: Array<[string, string | null]> }) {
  const { t } = useLanguage();
  return <section className="internal-system-panel"><header><span className="internal-kicker">{code}</span><h2>{title}</h2></header><dl>{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd className={value ? "" : "is-unknown"}>{value || t("Not reported", "暂无可验证数据")}</dd></div>)}</dl></section>;
}
