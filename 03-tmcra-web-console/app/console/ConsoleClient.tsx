"use client";

import type { CSSProperties, ReactNode } from "react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { LanguageToggle, type Language, useLanguage } from "../i18n";
import BrandMark from "../BrandMark";
import MemoryGraph, { type MemoryEdge, type MemoryEvent } from "./MemoryGraph";
import MemoryExplorer from "./MemoryExplorer";

type Actor = { displayName: string; email: string; role: string };
type ViewId = "overview" | "agents" | "memory" | "api-keys" | "team" | "usage" | "audit" | "settings";
type UnknownRecord = Record<string, unknown>;

type Organization = {
  id: string;
  name: string;
  slug: string;
  plan: string;
  environment: string;
  region: string;
  sampleMode: boolean;
};

type Metrics = {
  activeAgents: number;
  eventWrites: number;
  recallRequests: number;
  recallP95Ms: number;
  failureRate: number;
  nodeCount: number;
  edgeCount: number;
  updatedAt: string;
  recallInstrumented: boolean;
  operationInstrumented: boolean;
};

type Agent = {
  id: string;
  displayName: string;
  environment: string;
  state: string;
  policy: string;
  eventCount: number;
  nodeCount: number;
  lastWriteAt: string | null;
  lastRecallAt: string | null;
  recallP95Ms: number;
  tags: string[];
};

type ApiKey = {
  id: string;
  name: string;
  prefix: string;
  environment: string;
  scopes: string[];
  createdBy: string;
  createdAt: string;
  lastUsedAt: string | null;
  expiresAt: string | null;
  status: string;
};

type Member = {
  id: string;
  name: string;
  email: string;
  role: string;
  status: string;
  mfa: boolean;
  lastActiveAt: string | null;
};

type Operation = {
  id: string;
  occurredAt: string;
  type: string;
  agentId: string;
  status: string;
  latencyMs: number;
  requestId: string;
};

type AuditLog = {
  id: string;
  timestamp: string;
  actor: string;
  action: string;
  resourceType: string;
  resourceId: string;
  result: string;
  requestId: string;
  reason: string;
};

type UsageDay = {
  date: string;
  eventWrites: number;
  recalls: number;
  p95Ms: number;
  errorRate: number;
};

type ConsoleSettings = {
  supportEmail: string;
  timezone: string;
  retentionDays: number;
  confidenceThreshold: number;
  requireMfa: boolean;
  piiRedaction: boolean;
  webhookUrl: string;
};

type ConsoleSnapshot = {
  actor: Actor;
  organization: Organization;
  metrics: Metrics;
  agents: Agent[];
  selectedAgentId: string | null;
  events: MemoryEvent[];
  edges: MemoryEdge[];
  members: Member[];
  apiKeys: ApiKey[];
  operations: Operation[];
  auditLogs: AuditLog[];
  usageDaily: UsageDay[];
  settings: ConsoleSettings;
  sample: boolean;
};

const NAV: Array<{ id: ViewId; code: string; label: string; labelZh: string; group: string }> = [
  { id: "overview", code: "01", label: "Overview", labelZh: "总览", group: "OPERATE" },
  { id: "agents", code: "02", label: "Agents", labelZh: "Agents", group: "OPERATE" },
  { id: "memory", code: "03", label: "Memory", labelZh: "记忆", group: "OPERATE" },
  { id: "api-keys", code: "04", label: "API Keys", labelZh: "API Keys", group: "PLATFORM" },
  { id: "usage", code: "05", label: "Usage", labelZh: "用量", group: "PLATFORM" },
  { id: "team", code: "06", label: "Team", labelZh: "团队", group: "ORGANIZATION" },
  { id: "audit", code: "07", label: "Audit", labelZh: "Audit", group: "ORGANIZATION" },
  { id: "settings", code: "08", label: "Settings", labelZh: "设置", group: "ORGANIZATION" },
];

const GROUP_LABELS: Record<string, string> = { OPERATE: "运行", PLATFORM: "平台", ORGANIZATION: "工作区" };

const isRecord = (value: unknown): value is UnknownRecord => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const list = (value: unknown) => Array.isArray(value) ? value : [];
const first = (source: UnknownRecord, keys: string[]) => {
  for (const key of keys) if (source[key] !== undefined && source[key] !== null) return source[key];
  return undefined;
};
const textValue = (source: UnknownRecord, keys: string[], fallback = "") => {
  const value = first(source, keys);
  return typeof value === "string" || typeof value === "number" ? String(value) : fallback;
};
const numberValue = (source: UnknownRecord, keys: string[], fallback = 0) => {
  const value = Number(first(source, keys));
  return Number.isFinite(value) ? value : fallback;
};
const boolValue = (source: UnknownRecord, keys: string[], fallback = false) => {
  const value = first(source, keys);
  if (typeof value === "boolean") return value;
  if (value === 1 || value === "1") return true;
  if (value === 0 || value === "0") return false;
  return fallback;
};
const strings = (source: UnknownRecord, keys: string[]) => list(first(source, keys)).map(String);
const itemRecords = (value: unknown) => list(value).filter(isRecord);

function normalizeSnapshot(input: unknown, fallbackActor: Actor, sample = false): ConsoleSnapshot {
  const envelope = isRecord(input) ? input : {};
  const source = isRecord(envelope.snapshot) ? envelope.snapshot : envelope;
  const actorRaw = isRecord(source.actor) ? source.actor : {};
  const orgRaw = isRecord(source.organization) ? source.organization : {};
  const settingsRaw = isRecord(source.settings) ? source.settings : {};

  const agents: Agent[] = itemRecords(source.agents).map((item, index) => ({
    id: textValue(item, ["id", "agentId", "agent_id"], `agent-${index + 1}`),
    displayName: textValue(item, ["displayName", "display_name", "name"], "Unnamed agent"),
    environment: textValue(item, ["environment", "env"], "production").toLowerCase(),
    state: textValue(item, ["state", "status"], "enabled").toLowerCase(),
    policy: textValue(item, ["policy", "memoryPolicy", "memory_policy"], "default"),
    eventCount: numberValue(item, ["eventCount", "event_count"]),
    nodeCount: numberValue(item, ["nodeCount", "node_count", "eventCount", "event_count"]),
    lastWriteAt: textValue(item, ["lastWriteAt", "last_write_at", "lastEventAt", "last_event_at"]) || null,
    lastRecallAt: textValue(item, ["lastRecallAt", "last_recall_at"]) || null,
    recallP95Ms: numberValue(item, ["recallP95Ms", "recall_p95_ms", "p95LatencyMs"]),
    tags: strings(item, ["tags"]),
  }));

  const events: MemoryEvent[] = itemRecords(source.events).map((item, index) => ({
    id: textValue(item, ["id", "memoryId", "memory_id", "eventId", "event_id"], `memory-${index + 1}`),
    agentId: textValue(item, ["agentId", "agent_id"], agents[0]?.id ?? ""),
    subjectId: textValue(item, ["subjectId", "subject_id"], "unassigned"),
    type: textValue(item, ["type", "eventType", "event_type"], "event"),
    summary: textValue(item, ["summary", "label", "title", "content"], "Untitled memory"),
    content: textValue(item, ["content", "event", "summary"], ""),
    source: textValue(item, ["source", "sourceType", "source_type"], "api"),
    occurredAt: textValue(item, ["occurredAt", "occurred_at", "timestamp", "createdAt", "created_at"]),
    ingestedAt: textValue(item, ["ingestedAt", "ingested_at", "createdAt", "created_at"]),
    confidence: numberValue(item, ["confidence", "confidenceScore", "confidence_score"], 0),
    recallCount: numberValue(item, ["recallCount", "recall_count"]),
    lastRecalledAt: textValue(item, ["lastRecalledAt", "last_recalled_at"]) || null,
    tags: strings(item, ["tags"]),
  }));

  const edges: MemoryEdge[] = itemRecords(source.edges).map((item, index) => ({
    id: textValue(item, ["id", "edgeId", "edge_id"], `edge-${index + 1}`),
    source: textValue(item, ["source", "sourceId", "source_id", "sourceEventId", "source_event_id", "from"]),
    target: textValue(item, ["target", "targetId", "target_id", "targetEventId", "target_event_id", "to"]),
    type: textValue(item, ["type", "relation", "relationshipType", "relationship_type"], "related"),
    weight: numberValue(item, ["weight", "score"], 1),
    createdAt: textValue(item, ["createdAt", "created_at"]),
  }));

  const operations: Operation[] = itemRecords(source.operations).map((item, index) => {
    const rawStatus = textValue(item, ["status", "result"], "success").toLowerCase();
    return {
      id: textValue(item, ["id", "operationId", "operation_id"], `op-${index + 1}`),
      occurredAt: textValue(item, ["occurredAt", "occurred_at", "timestamp", "createdAt", "created_at"]),
      type: textValue(item, ["type", "operationType", "operation_type", "action"], "operation"),
      agentId: textValue(item, ["agentId", "agent_id"]),
      status: rawStatus === "completed" ? "success" : rawStatus,
      latencyMs: numberValue(item, ["latencyMs", "latency_ms", "durationMs", "duration_ms"]),
      requestId: textValue(item, ["requestId", "request_id", "traceId", "trace_id"], "-"),
    };
  });

  const metricRaw = isRecord(source.metrics) ? source.metrics : {};
  const activeAgents = agents.filter((agent) => agent.state !== "archived").length;
  const failedOps = operations.filter((operation) => operation.status !== "success").length;
  const metrics: Metrics = {
    activeAgents: numberValue(metricRaw, ["activeAgents", "active_agents"], activeAgents),
    eventWrites: numberValue(metricRaw, ["eventWrites", "event_writes", "events24h", "events_24h", "memoryEvents", "memory_events"], events.length),
    recallRequests: numberValue(metricRaw, ["recallRequests", "recall_requests"], operations.filter((operation) => operation.type.includes("recall")).length),
    recallP95Ms: numberValue(metricRaw, ["recallP95Ms", "recall_p95_ms"]),
    failureRate: numberValue(metricRaw, ["failureRate", "failure_rate"], operations.length ? failedOps / operations.length : 0),
    nodeCount: numberValue(metricRaw, ["nodeCount", "node_count", "memoryEvents", "memory_events"], events.length),
    edgeCount: numberValue(metricRaw, ["edgeCount", "edge_count", "memoryEdges", "memory_edges"], edges.length),
    updatedAt: textValue(metricRaw, ["updatedAt", "updated_at"], new Date().toISOString()),
    recallInstrumented: first(metricRaw, ["recallRequests", "recall_requests", "recallP95Ms", "recall_p95_ms"]) !== undefined || operations.some((operation) => operation.type.includes("recall")),
    operationInstrumented: first(metricRaw, ["failureRate", "failure_rate"]) !== undefined || operations.some((operation) => ["failed", "error"].includes(operation.status)),
  };

  return {
    actor: {
      displayName: textValue(actorRaw, ["displayName", "display_name", "name"], fallbackActor.displayName),
      email: textValue(actorRaw, ["email"], fallbackActor.email),
      role: textValue(actorRaw, ["role"], fallbackActor.role),
    },
    organization: {
      id: textValue(orgRaw, ["id", "organizationId", "organization_id", "workspaceId", "workspace_id"], ""),
      name: textValue(orgRaw, ["name", "organizationName", "organization_name"], "TMCRA workspace"),
      slug: textValue(orgRaw, ["slug", "workspaceSlug", "workspace_slug"], "tmcra-workspace"),
      plan: textValue(orgRaw, ["plan", "tier"], "Development"),
      environment: textValue(orgRaw, ["environment", "env"], "production").toLowerCase(),
      region: textValue(orgRaw, ["region", "dataRegion", "data_region"], "Not configured"),
      sampleMode: boolValue(orgRaw, ["sampleMode", "sample_mode"]),
    },
    metrics,
    agents,
    selectedAgentId: textValue(source, ["selectedAgentId", "selected_agent_id"]) || agents[0]?.id || null,
    events,
    edges,
    apiKeys: itemRecords(source.apiKeys ?? source.api_keys).map((item, index) => ({
      id: textValue(item, ["id", "keyId", "key_id"], `key-${index + 1}`),
      name: textValue(item, ["name"], "Unnamed key"),
      prefix: textValue(item, ["prefix", "keyPrefix", "key_prefix", "tokenPrefix", "token_prefix"], "tmcra_..."),
      environment: textValue(item, ["environment", "env"], "production").toLowerCase(),
      scopes: strings(item, ["scopes"]),
      createdBy: textValue(item, ["createdBy", "created_by"], "Unknown"),
      createdAt: textValue(item, ["createdAt", "created_at"]),
      lastUsedAt: textValue(item, ["lastUsedAt", "last_used_at"]) || null,
      expiresAt: textValue(item, ["expiresAt", "expires_at"]) || null,
      status: textValue(item, ["status"], first(item, ["revokedAt", "revoked_at"]) ? "revoked" : "active").toLowerCase(),
    })),
    members: itemRecords(source.members).map((item, index) => ({
      id: textValue(item, ["id", "memberId", "member_id"], `member-${index + 1}`),
      name: textValue(item, ["name", "displayName", "display_name"], "Pending member"),
      email: textValue(item, ["email"]),
      role: textValue(item, ["role"], "Developer"),
      status: textValue(item, ["status"], "active").toLowerCase(),
      mfa: boolValue(item, ["mfa", "mfaEnabled", "mfa_enabled"]),
      lastActiveAt: textValue(item, ["lastActiveAt", "last_active_at"]) || null,
    })),
    operations,
    auditLogs: itemRecords(source.auditLogs ?? source.audit_logs).map((item, index) => ({
      id: textValue(item, ["id", "auditId", "audit_id"], `audit-${index + 1}`),
      timestamp: textValue(item, ["timestamp", "createdAt", "created_at"]),
      actor: textValue(item, ["actor", "actorName", "actor_name", "actorId", "actor_id"], "System"),
      action: textValue(item, ["action"], "unknown"),
      resourceType: textValue(item, ["resourceType", "resource_type", "targetType", "target_type"], "resource"),
      resourceId: textValue(item, ["resourceId", "resource_id", "targetId", "target_id"], "-"),
      result: textValue(item, ["result", "status"], "success").toLowerCase(),
      requestId: textValue(item, ["requestId", "request_id", "traceId", "trace_id"], "-"),
      reason: textValue(item, ["reason"]),
    })),
    usageDaily: itemRecords(source.usageDaily ?? source.usage_daily).map((item) => ({
      date: textValue(item, ["date", "day"]),
      eventWrites: numberValue(item, ["eventWrites", "event_writes", "events"]),
      recalls: numberValue(item, ["recalls", "recallRequests", "recall_requests"]),
      p95Ms: numberValue(item, ["p95Ms", "p95_ms", "recallP95Ms", "recall_p95_ms"]),
      errorRate: numberValue(item, ["errorRate", "error_rate"]),
    })),
    settings: {
      supportEmail: textValue(settingsRaw, ["supportEmail", "support_email"]),
      timezone: textValue(settingsRaw, ["timezone"], "UTC"),
      retentionDays: numberValue(settingsRaw, ["retentionDays", "retention_days"], 90),
      confidenceThreshold: numberValue(settingsRaw, ["confidenceThreshold", "confidence_threshold"], 0.7),
      requireMfa: boolValue(settingsRaw, ["requireMfa", "require_mfa"]),
      piiRedaction: boolValue(settingsRaw, ["piiRedaction", "pii_redaction"]),
      webhookUrl: textValue(settingsRaw, ["webhookUrl", "webhook_url"]),
    },
    sample: sample || boolValue(orgRaw, ["sampleMode", "sample_mode"]) || events.some((event) => event.source === "sample"),
  };
}

function createSampleSnapshot(actor: Actor): ConsoleSnapshot {
  return normalizeSnapshot({
    actor: { ...actor, role: "Owner" },
    organization: { id: "ws_sample_atlas", name: "Atlas Research", slug: "atlas-research", plan: "Enterprise sample", environment: "production", region: "ap-southeast-1" },
    metrics: { activeAgents: 3, eventWrites: 12842, recallRequests: 3904, recallP95Ms: 184, failureRate: 0.006, nodeCount: 1284100, edgeCount: 3409218, updatedAt: "2026-07-14T11:42:08+08:00" },
    agents: [
      { id: "atlas-prod", displayName: "Atlas Strategy", environment: "production", state: "enabled", policy: "long-horizon-v2", eventCount: 524181, nodeCount: 186210, lastWriteAt: "2026-07-14T11:41:58+08:00", lastRecallAt: "2026-07-14T11:41:44+08:00", recallP95Ms: 171, tags: ["strategy", "customer-facing"] },
      { id: "ops-copilot", displayName: "Operations Copilot", environment: "production", state: "enabled", policy: "operational-90d", eventCount: 388902, nodeCount: 149030, lastWriteAt: "2026-07-14T11:40:32+08:00", lastRecallAt: "2026-07-14T11:41:10+08:00", recallP95Ms: 206, tags: ["operations"] },
      { id: "research-scout", displayName: "Research Scout", environment: "staging", state: "writes paused", policy: "research", eventCount: 94210, nodeCount: 51812, lastWriteAt: "2026-07-13T19:08:00+08:00", lastRecallAt: "2026-07-14T09:22:00+08:00", recallP95Ms: 230, tags: ["evaluation"] },
    ],
    selectedAgentId: "atlas-prod",
    events: [
      { id: "mem-pref-01", agentId: "atlas-prod", subjectId: "subject-1042", type: "preference", summary: "Prefers concise launch reports", content: "The subject asked that launch recommendations stay concise and include explicit risks.", source: "events-api", occurredAt: "2026-06-02T09:10:00+08:00", ingestedAt: "2026-06-02T09:10:01+08:00", confidence: 0.96, recallCount: 18, lastRecalledAt: "2026-07-14T11:41:44+08:00", tags: ["reporting"] },
      { id: "mem-atlas-02", agentId: "atlas-prod", subjectId: "subject-1042", type: "project", summary: "Project Atlas initiated", content: "Project Atlas began as a regional product launch planning effort.", source: "events-api", occurredAt: "2026-06-08T14:20:00+08:00", ingestedAt: "2026-06-08T14:20:02+08:00", confidence: 0.99, recallCount: 25, lastRecalledAt: "2026-07-14T11:41:44+08:00", tags: ["atlas"] },
      { id: "mem-market-03", agentId: "atlas-prod", subjectId: "subject-1042", type: "decision", summary: "Target market changed to Singapore", content: "The Atlas launch market changed from Australia to Singapore after the regional review.", source: "events-api", occurredAt: "2026-06-20T16:42:00+08:00", ingestedAt: "2026-06-20T16:42:02+08:00", confidence: 0.94, recallCount: 31, lastRecalledAt: "2026-07-14T11:41:44+08:00", tags: ["atlas", "market"] },
      { id: "mem-budget-04", agentId: "atlas-prod", subjectId: "subject-1042", type: "constraint", summary: "Launch budget capped at 480k", content: "Approved launch budget must not exceed SGD 480,000 in the first operating quarter.", source: "policy-import", occurredAt: "2026-06-28T10:05:00+08:00", ingestedAt: "2026-06-28T10:05:08+08:00", confidence: 0.91, recallCount: 12, lastRecalledAt: "2026-07-14T11:41:44+08:00", tags: ["atlas", "budget"] },
      { id: "mem-launch-05", agentId: "atlas-prod", subjectId: "subject-1042", type: "event", summary: "Launch plan requested", content: "The subject requested a launch plan grounded in the latest market and budget constraints.", source: "recall-api", occurredAt: "2026-07-14T11:41:42+08:00", ingestedAt: "2026-07-14T11:41:43+08:00", confidence: 0.98, recallCount: 1, lastRecalledAt: "2026-07-14T11:41:44+08:00", tags: ["atlas", "launch"] },
      { id: "mem-ops-01", agentId: "ops-copilot", subjectId: "team-ops", type: "event", summary: "Warehouse incident resolved", content: "The east warehouse incident was closed after carrier failover.", source: "webhook", occurredAt: "2026-07-14T08:12:00+08:00", ingestedAt: "2026-07-14T08:12:03+08:00", confidence: 0.88, recallCount: 3, lastRecalledAt: "2026-07-14T10:00:00+08:00", tags: ["incident"] },
    ],
    edges: [
      { id: "edge-01", source: "mem-pref-01", target: "mem-atlas-02", type: "context", weight: 0.82, createdAt: "2026-06-08T14:20:02+08:00" },
      { id: "edge-02", source: "mem-atlas-02", target: "mem-market-03", type: "temporal", weight: 0.97, createdAt: "2026-06-20T16:42:02+08:00" },
      { id: "edge-03", source: "mem-market-03", target: "mem-budget-04", type: "constraint", weight: 0.76, createdAt: "2026-06-28T10:05:08+08:00" },
      { id: "edge-04", source: "mem-budget-04", target: "mem-launch-05", type: "recall-path", weight: 0.93, createdAt: "2026-07-14T11:41:44+08:00" },
      { id: "edge-05", source: "mem-market-03", target: "mem-launch-05", type: "causal", weight: 0.91, createdAt: "2026-07-14T11:41:44+08:00" },
    ],
    apiKeys: [
      { id: "key-prod-01", name: "Production ingest", prefix: "tmcra_live_7f21", environment: "production", scopes: ["events:write", "memory:recall"], createdBy: actor.email, createdAt: "2026-05-11T08:00:00Z", lastUsedAt: "2026-07-14T11:41:58+08:00", expiresAt: null, status: "active" },
      { id: "key-ci-02", name: "Evaluation CI", prefix: "tmcra_test_81bd", environment: "staging", scopes: ["events:write", "memory:read"], createdBy: actor.email, createdAt: "2026-06-20T08:00:00Z", lastUsedAt: "2026-07-14T09:20:00+08:00", expiresAt: "2026-09-20T08:00:00Z", status: "active" },
    ],
    members: [
      { id: "member-01", name: actor.displayName, email: actor.email, role: "Owner", status: "active", mfa: true, lastActiveAt: "2026-07-14T11:42:00+08:00" },
      { id: "member-02", name: "Lin Zhao", email: "lin@example.com", role: "Developer", status: "active", mfa: true, lastActiveAt: "2026-07-14T09:31:00+08:00" },
      { id: "member-03", name: "Mira Chen", email: "mira@example.com", role: "Auditor", status: "invited", mfa: false, lastActiveAt: null },
    ],
    operations: [
      { id: "op-01", occurredAt: "2026-07-14T11:41:58+08:00", type: "event.write", agentId: "atlas-prod", status: "success", latencyMs: 48, requestId: "req_01J2AT9F" },
      { id: "op-02", occurredAt: "2026-07-14T11:41:44+08:00", type: "memory.recall", agentId: "atlas-prod", status: "success", latencyMs: 171, requestId: "req_01J2AT8X" },
      { id: "op-03", occurredAt: "2026-07-14T11:40:32+08:00", type: "event.write", agentId: "ops-copilot", status: "success", latencyMs: 53, requestId: "req_01J2AT61" },
      { id: "op-04", occurredAt: "2026-07-14T11:38:06+08:00", type: "memory.recall", agentId: "ops-copilot", status: "failed", latencyMs: 804, requestId: "req_01J2ASZ9" },
    ],
    auditLogs: [
      { id: "audit-01", timestamp: "2026-07-14T10:12:00+08:00", actor: actor.email, action: "agent.configuration_updated", resourceType: "agent", resourceId: "atlas-prod", result: "success", requestId: "req_01J29F", reason: "Raise recall depth for launch planning" },
      { id: "audit-02", timestamp: "2026-07-13T17:05:00+08:00", actor: "lin@example.com", action: "api_key.created", resourceType: "api_key", resourceId: "key-ci-02", result: "success", requestId: "req_01J25D", reason: "Evaluation pipeline" },
      { id: "audit-03", timestamp: "2026-07-12T12:22:00+08:00", actor: "system", action: "memory.retention_applied", resourceType: "memory_policy", resourceId: "operational-90d", result: "success", requestId: "job_01J22Q", reason: "Scheduled policy run" },
    ],
    usageDaily: [
      { date: "2026-07-08", eventWrites: 9801, recalls: 2860, p95Ms: 191, errorRate: 0.008 },
      { date: "2026-07-09", eventWrites: 10420, recalls: 3021, p95Ms: 188, errorRate: 0.006 },
      { date: "2026-07-10", eventWrites: 11209, recalls: 3198, p95Ms: 182, errorRate: 0.007 },
      { date: "2026-07-11", eventWrites: 11880, recalls: 3402, p95Ms: 179, errorRate: 0.005 },
      { date: "2026-07-12", eventWrites: 10910, recalls: 3299, p95Ms: 186, errorRate: 0.006 },
      { date: "2026-07-13", eventWrites: 12124, recalls: 3710, p95Ms: 181, errorRate: 0.005 },
      { date: "2026-07-14", eventWrites: 12842, recalls: 3904, p95Ms: 184, errorRate: 0.006 },
    ],
    settings: { supportEmail: "platform@atlas.example", timezone: "Asia/Shanghai", retentionDays: 365, confidenceThreshold: 0.72, requireMfa: true, piiRedaction: true, webhookUrl: "https://ops.atlas.example/tmcra" },
  }, actor, true);
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("en", { notation: value >= 100000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(value);
}

function formatTime(value: string | null, language: Language = "en") {
  if (!value) return language === "zh" ? "从未" : "Never";
  const normalizedValue = /^\d{11,}$/.test(value) ? Number(value) : value;
  const date = new Date(normalizedValue);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(language === "zh" ? "zh-CN" : "en", { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}

function statusTone(status: string) {
  if (["success", "active", "enabled", "operational", "connected"].includes(status)) return "good";
  if (["failed", "error", "revoked", "disabled"].includes(status)) return "bad";
  return "warn";
}

function StatusPill({ value }: { value: string }) {
  const { t } = useLanguage();
  const labels: Record<string, string> = { success: "成功", active: "已启用", enabled: "已启用", operational: "运行正常", connected: "已连接", failed: "失败", error: "错误", revoked: "已吊销", disabled: "已停用", paused: "已暂停", archived: "已归档", invited: "待接受", pending: "待处理" };
  return <span className={`status-pill is-${statusTone(value)}`}><i />{t(value, labels[value.toLowerCase()] ?? value)}</span>;
}

function PageHeader({ eyebrow, title, copy, actions }: { eyebrow: string; title: string; copy: string; actions?: ReactNode }) {
  return <header className="page-header"><div><span className="system-label">{eyebrow}</span><h1>{title}</h1><p>{copy}</p></div>{actions && <div className="page-actions">{actions}</div>}</header>;
}

function EmptyState({ code, title, copy, actions }: { code: string; title: string; copy: string; actions?: ReactNode }) {
  const { t } = useLanguage();
  return <section className="empty-state"><span className="empty-code">{code}</span><div><span className="system-label">{t("NO RECORDS", "暂无记录")}</span><h2>{title}</h2><p>{copy}</p>{actions && <div className="empty-actions">{actions}</div>}</div></section>;
}

function LoadingScreen() {
  const { t } = useLanguage();
  return <div className="console-state-page"><div className="console-state-brand"><BrandMark />TMCRA / CONSOLE</div><LanguageToggle className="console-language-toggle" /><div className="loading-grid" aria-label={t("Loading console", "正在加载 Console")}><i /><i /><i /><i /><i /><i /></div><p>{t("Connecting to memory operations...", "正在连接记忆服务...")}</p></div>;
}

function Modal({ open, title, copy, onClose, children }: { open: boolean; title: string; copy: string; onClose: () => void; children: ReactNode }) {
  const { t } = useLanguage();
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return <dialog ref={ref} className="console-dialog" onCancel={onClose} onClose={onClose}><button className="dialog-close" type="button" onClick={onClose} aria-label={t("Close dialog", "关闭窗口")}>{t("CLOSE", "关闭")}</button><span className="system-label">{t("CONTROL ACTION", "操作确认")}</span><h2>{title}</h2><p>{copy}</p>{children}</dialog>;
}

export default function ConsoleClient({
  initialActor,
  apiBase = "/api/enterprise",
}: {
  initialActor: Actor;
  apiBase?: string;
}) {
  const { t } = useLanguage();
  const [snapshot, setSnapshot] = useState<ConsoleSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<ViewId>("overview");
  const [environment, setEnvironment] = useState("production");
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [mobileNav, setMobileNav] = useState(false);
  const [dialog, setDialog] = useState<"agent" | "event" | "key" | "member" | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [oneTimeSecret, setOneTimeSecret] = useState<string | null>(null);

  const loadSnapshot = useCallback(async (targetEnvironment?: string) => {
    setLoading(true);
    setError(null);
    try {
      const query = targetEnvironment ? `?environment=${encodeURIComponent(targetEnvironment)}` : "";
      const response = await fetch(`${apiBase}${query}`, { headers: { Accept: "application/json" }, cache: "no-store" });
      if (!response.ok) throw new Error(response.status === 403 ? t("This identity is not authorized for the TMCRA workspace.", "当前身份没有访问此 TMCRA 工作区的权限。") : t(`Console snapshot failed (${response.status}).`, `无法读取 Console 数据（${response.status}）。`));
      const next = normalizeSnapshot(await response.json(), initialActor);
      setSnapshot(next);
      setEnvironment(next.organization.environment);
      setSelectedAgentId(next.selectedAgentId);
    } catch (caught) {
      setSnapshot(null);
      setError(caught instanceof Error ? caught.message : t("Console snapshot could not be loaded.", "暂时无法加载 Console 数据。"));
    } finally {
      setLoading(false);
    }
  }, [apiBase, initialActor, t]);

  useEffect(() => {
    const timer = window.setTimeout(() => { void loadSnapshot(); }, 0);
    return () => window.clearTimeout(timer);
  }, [loadSnapshot]);
  useEffect(() => {
    const syncHash = () => {
      const hash = window.location.hash.slice(1) as ViewId;
      if (NAV.some((item) => item.id === hash)) setActiveView(hash);
    };
    syncHash();
    window.addEventListener("hashchange", syncHash);
    return () => window.removeEventListener("hashchange", syncHash);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 4200);
    return () => window.clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    document.title = t("TMCRA Console — Memory Operations", "TMCRA Console — 记忆运行中心");
  }, [t]);

  const useSampleData = () => {
    const sample = createSampleSnapshot(initialActor);
    setSnapshot(sample);
    setEnvironment(sample.organization.environment);
    setSelectedAgentId(sample.selectedAgentId);
    setError(null);
    setLoading(false);
  };

  const navigate = (view: ViewId) => {
    setActiveView(view);
    setMobileNav(false);
    window.history.replaceState(null, "", `#${view}`);
  };

  const runAction = async (action: string, payload: UnknownRecord): Promise<UnknownRecord | null> => {
    if (!snapshot || snapshot.sample) {
      setToast(t("Sample data is read-only. Exit sample data to make changes.", "示例数据仅供查看，不能执行修改。"));
      return null;
    }
    setBusy(true);
    try {
      const scopedPayload = { organizationId: snapshot.organization.id, ...payload };
      const response = await fetch(apiBase, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ action, payload: scopedPayload }),
      });
      const result: unknown = await response.json().catch(() => ({}));
      if (!response.ok) {
        const resultRecord = isRecord(result) ? result : {};
        const errorRecord = isRecord(resultRecord.error) ? resultRecord.error : resultRecord;
        throw new Error(textValue(errorRecord, ["message"], t(`Action failed (${response.status}).`, `操作失败（${response.status}）。`)));
      }
      const resultRecord = isRecord(result) ? result : {};
      const secret = textValue(resultRecord, ["secret", "apiKey", "api_key"]);
      if (secret) setOneTimeSecret(secret);
      if (resultRecord.snapshot || resultRecord.agents || resultRecord.organization) {
        const next = normalizeSnapshot(resultRecord, initialActor);
        setSnapshot(next);
        setSelectedAgentId(next.selectedAgentId);
      } else {
        await loadSnapshot(environment);
      }
      setDialog(null);
      setToast(t(`${action} completed.`, "操作已完成。"));
      return resultRecord;
    } catch (caught) {
      setToast(caught instanceof Error ? caught.message : t("The action failed.", "操作未能完成。"));
      return null;
    } finally {
      setBusy(false);
    }
  };

  if (loading && !snapshot) return <LoadingScreen />;
  if (!snapshot) {
    return <div className="console-state-page"><div className="console-state-brand"><BrandMark />TMCRA / CONSOLE</div><LanguageToggle className="console-language-toggle" /><span className="system-label">{t("CONNECTION ERROR", "连接失败")}</span><h1>{t("Memory operations are unavailable.", "暂时无法连接记忆服务。")}</h1><p>{error}</p><div className="state-actions"><button className="button primary" type="button" onClick={() => void loadSnapshot()}>{t("Retry connection", "重新连接")}</button><button className="button" type="button" onClick={useSampleData}>{t("Load sample data", "查看示例数据")}</button></div><small>{t("Sample data is local, clearly labeled, and never mixed with workspace records.", "示例数据只在本地展示，并且始终与工作区真实数据分开。")}</small></div>;
  }

  const environments = Array.from(new Set([snapshot.organization.environment, ...snapshot.agents.map((agent) => agent.environment)])).filter(Boolean);
  const visibleAgents = snapshot.agents.filter((agent) => agent.environment === environment);
  const resolvedAgentId = selectedAgentId && visibleAgents.some((agent) => agent.id === selectedAgentId) ? selectedAgentId : visibleAgents[0]?.id ?? null;
  const selectedEvents = snapshot.events.filter((event) => event.agentId === resolvedAgentId);
  const selectedEventIds = new Set(selectedEvents.map((event) => event.id));
  const selectedEdges = snapshot.edges.filter((edge) => selectedEventIds.has(edge.source) && selectedEventIds.has(edge.target));
  const visibleKeys = snapshot.apiKeys.filter((key) => key.environment === environment);
  const visibleOperations = snapshot.operations.filter((operation) => !operation.agentId || visibleAgents.some((agent) => agent.id === operation.agentId));
  const actor = snapshot.actor;

  const changeEnvironment = (next: string) => {
    setEnvironment(next);
    setSelectedAgentId(null);
    if (!snapshot.sample) void loadSnapshot(next);
  };

  return (
    <div className={`console-shell ${mobileNav ? "nav-open" : ""}`}>
      <a className="skip-link" href="#console-main">{t("Skip to console content", "跳到 Console 主内容")}</a>
      <aside className="console-sidebar" aria-label={t("Console navigation", "Console 导航")}>
        <div className="console-logo"><BrandMark /><span>TMCRA</span><i>CONSOLE</i></div>
        <nav>
          {["OPERATE", "PLATFORM", "ORGANIZATION"].map((group) => <div className="nav-group" key={group}><span>{t(group, GROUP_LABELS[group])}</span>{NAV.filter((item) => item.group === group).map((item) => <a key={item.id} href={`#${item.id}`} className={activeView === item.id ? "is-active" : ""} aria-current={activeView === item.id ? "page" : undefined} onClick={(event) => { event.preventDefault(); navigate(item.id); }}><i>{item.code}</i>{t(item.label, item.labelZh)}</a>)}</div>)}
        </nav>
        <div className="sidebar-status"><StatusPill value="operational" /><span>{snapshot.organization.region}</span></div>
      </aside>

      <div className="console-workspace">
        <header className="console-topbar">
          <button className="nav-toggle" type="button" onClick={() => setMobileNav((open) => !open)} aria-expanded={mobileNav} aria-label={t("Toggle console navigation", "打开或关闭 Console 导航")}><span /><span /></button>
          <label className="workspace-select"><span>{t("WORKSPACE", "工作区")}</span><b>{snapshot.organization.name}</b></label>
          <label className="environment-select"><span className="sr-only">{t("Environment", "环境")}</span><select value={environment} onChange={(event) => changeEnvironment(event.target.value)}>{environments.map((item) => <option key={item} value={item}>{item.toUpperCase()}</option>)}</select></label>
          {snapshot.sample && <span className="sample-badge">{t("SAMPLE DATA", "示例数据")}</span>}
          <div className="topbar-spacer" />
          <Link className="topbar-link" href="/docs">Docs</Link>
          <LanguageToggle className="console-language-toggle" />
          <details className="actor-menu"><summary><span>{actor.displayName.slice(0, 1).toUpperCase()}</span><b>{actor.displayName}</b><i>{actor.role}</i></summary><div><span>{actor.email}</span><Link href="/signout-with-chatgpt?return_to=%2F">{t("Sign out", "退出登录")}</Link></div></details>
        </header>

        {snapshot.sample && <div className="sample-banner"><span>{t("This is an isolated, read-only sample workspace. No values are presented as your production data.", "这是一个独立的只读示例工作区，所有数值都不是你的生产数据。")}</span><span>{t("RESET REQUIRES A NEW WORKSPACE", "重新载入需要新建工作区")}</span></div>}
        {error && <div className="inline-error" role="alert"><span>{error}</span><button type="button" onClick={() => void loadSnapshot(environment)}>{t("Retry", "重试")}</button></div>}

        <main id="console-main">
          {activeView === "overview" && <OverviewView snapshot={snapshot} agents={visibleAgents} events={selectedEvents} edges={selectedEdges} operations={visibleOperations} selectedAgentId={resolvedAgentId} onSelectAgent={setSelectedAgentId} onCreateAgent={() => setDialog("agent")} onSample={() => { void runAction("load_sample", {}); }} onNavigate={navigate} />}
          {activeView === "agents" && <AgentsView agents={visibleAgents} sample={snapshot.sample} onCreate={() => setDialog("agent")} onOpenMemory={(id) => { setSelectedAgentId(id); navigate("memory"); }} onToggle={(agent) => { void runAction("agent.update", { agentId: agent.id, status: agent.state === "paused" ? "active" : "paused" }); }} />}
          {activeView === "memory" && <MemoryView organizationId={snapshot.organization.id} agents={visibleAgents} events={selectedEvents} edges={selectedEdges} selectedAgentId={resolvedAgentId} sample={snapshot.sample} onSelectAgent={setSelectedAgentId} onCreate={() => setDialog("event")} />}
          {activeView === "api-keys" && <ApiKeysView keys={visibleKeys} sample={snapshot.sample} oneTimeSecret={oneTimeSecret} onDismissSecret={() => setOneTimeSecret(null)} onCreate={() => setDialog("key")} onRevoke={(id) => { if (window.confirm(t("Revoke this API Key? Existing integrations will stop authenticating immediately.", "确定吊销这个 API Key？现有集成会立即失去认证能力。"))) void runAction("api_key.revoke", { keyId: id }); }} />}
          {activeView === "team" && <TeamView members={snapshot.members} sample={snapshot.sample} onInvite={() => setDialog("member")} />}
          {activeView === "usage" && <UsageView metrics={snapshot.metrics} days={snapshot.usageDaily} />}
          {activeView === "audit" && <AuditView logs={snapshot.auditLogs} />}
          {activeView === "settings" && <SettingsView organization={snapshot.organization} sample={snapshot.sample} busy={busy} onSave={(payload) => void runAction("organization.update", payload)} />}
        </main>
      </div>

      {toast && <div className="console-toast" role="status">{toast}</div>}

      <Modal open={dialog === "agent"} title={t("Create Agent identity", "新建 Agent 身份")} copy={t("A stable infrastructure ID is generated; the slug remains unique inside this workspace.", "系统会生成稳定的 Agent ID；slug 在当前工作区内必须唯一。")} onClose={() => setDialog(null)}>
        <form className="control-form" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); void runAction("agent.create", { name: String(data.get("name") ?? ""), slug: String(data.get("slug") ?? ""), description: String(data.get("description") ?? "") }); }}><label>{t("Display name", "显示名称")}<input name="name" required placeholder="Atlas Strategy" /></label><label>{t("Stable slug", "固定 slug")}<input name="slug" required pattern="[a-z0-9-]+" placeholder="atlas-strategy" /></label><label>{t("Description", "用途说明")}<textarea name="description" rows={3} placeholder={t("Production memory identity for the Atlas workflow.", "供 Atlas 工作流在生产环境中使用的记忆身份。")}/></label><div className="form-actions"><button className="button" type="button" onClick={() => setDialog(null)}>{t("Cancel", "取消")}</button><button className="button primary" disabled={busy || snapshot.sample} type="submit">{busy ? t("Creating...", "正在创建...") : t("Create Agent", "创建 Agent")}</button></div></form>
      </Modal>
      <Modal open={dialog === "event"} title={t("Create memory event", "写入记忆事件")} copy={t("The event is written to D1 and becomes a node in the selected Agent's temporal graph.", "事件会写入 D1，并成为当前 Agent 时序图中的一个节点。")} onClose={() => setDialog(null)}>
        <form className="control-form" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); if (!resolvedAgentId) return; void runAction("memory.create", { agentId: resolvedAgentId, eventType: String(data.get("eventType") ?? "observation"), content: String(data.get("content") ?? ""), source: String(data.get("source") ?? "console"), occurredAt: Date.now() }); }}><label>{t("Event type", "事件类型")}<select name="eventType" defaultValue="observation"><option value="observation">Observation</option><option value="preference">Preference</option><option value="decision">Decision</option><option value="constraint">Constraint</option><option value="project">Project</option></select></label><label>{t("Event content", "事件内容")}<textarea name="content" rows={5} required placeholder={t("The launch market changed to Singapore after the regional review.", "区域评审后，上市市场调整为新加坡。")}/></label><label>Source<input name="source" defaultValue="console" pattern="[A-Za-z0-9._-]+" /></label><div className="form-actions"><button className="button" type="button" onClick={() => setDialog(null)}>{t("Cancel", "取消")}</button><button className="button primary" disabled={busy || snapshot.sample || !resolvedAgentId} type="submit">{busy ? t("Writing...", "正在写入...") : t("Write event", "写入事件")}</button></div></form>
      </Modal>
      <Modal open={dialog === "key"} title={t("Create scoped API Key", "创建带 Scope 的 API Key")} copy={t("The Token is shown once. Store it in your deployment secret manager.", "Token 只显示一次，请立即保存到部署环境的 Secret Manager 中。")} onClose={() => setDialog(null)}>
        <form className="control-form" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); void runAction("api_key.create", { name: String(data.get("name") ?? ""), scopes: data.getAll("scopes").map(String) }); }}><label>{t("Key name", "Key 名称")}<input name="name" required placeholder="Production ingest" /></label><fieldset><legend>Scopes</legend>{["memory:read", "memory:write", "agents:read", "agents:write"].map((scope) => <label className="check-row" key={scope}><input type="checkbox" name="scopes" value={scope} defaultChecked={scope === "memory:read" || scope === "memory:write"} />{scope}</label>)}</fieldset><div className="form-actions"><button className="button" type="button" onClick={() => setDialog(null)}>{t("Cancel", "取消")}</button><button className="button primary" disabled={busy || snapshot.sample} type="submit">{busy ? t("Creating...", "正在创建...") : t("Create Key", "创建 API Key")}</button></div></form>
      </Modal>
      <Modal open={dialog === "member"} title={t("Invite workspace member", "邀请工作区成员")} copy={t("Roles control Console access. API authorization remains server enforced.", "Role 决定成员可以使用哪些 Console 功能；API 权限仍由服务端校验。")} onClose={() => setDialog(null)}>
        <form className="control-form" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); void runAction("member.add", { email: String(data.get("email") ?? ""), role: String(data.get("role") ?? "developer") }); }}><label>Email<input name="email" type="email" required placeholder="developer@company.com" /></label><label>Role<select name="role" defaultValue="developer"><option value="developer">Developer</option><option value="viewer">Viewer</option><option value="admin">Admin</option></select></label><div className="form-actions"><button className="button" type="button" onClick={() => setDialog(null)}>{t("Cancel", "取消")}</button><button className="button primary" disabled={busy || snapshot.sample} type="submit">{busy ? t("Sending...", "正在发送...") : t("Send invite", "发送邀请")}</button></div></form>
      </Modal>
    </div>
  );
}

function OverviewView({ snapshot, agents, events, edges, operations, selectedAgentId, onSelectAgent, onCreateAgent, onSample, onNavigate }: { snapshot: ConsoleSnapshot; agents: Agent[]; events: MemoryEvent[]; edges: MemoryEdge[]; operations: Operation[]; selectedAgentId: string | null; onSelectAgent: (id: string) => void; onCreateAgent: () => void; onSample: () => void; onNavigate: (view: ViewId) => void }) {
  const { language, t } = useLanguage();
  if (!agents.length) return <><PageHeader eyebrow={t("01 / MEMORY OPERATIONS", "01 / 记忆运行")} title={t("Memory operations", "记忆运行中心")} copy={t("Observe memory formation, recall paths, and infrastructure health.", "查看记忆如何形成、召回经过哪些路径，以及基础设施当前的运行状态。")} /><EmptyState code="00" title={t("No Agents in this environment", "当前环境还没有 Agent")} copy={t("Create an Agent identity, issue a scoped API Key, and send the first event. Sample data is always isolated and labeled.", "先创建 Agent 身份，再签发带 Scope 的 API Key，然后写入第一条事件。示例数据始终单独存放并明确标记。")} actions={<><button className="button primary" type="button" onClick={onCreateAgent}>{t("Create Agent", "创建 Agent")}</button>{!snapshot.sample && <button className="button" type="button" onClick={onSample}>{t("Load sample data", "查看示例数据")}</button>}</>} /></>;
  const metrics = snapshot.metrics;
  return <><PageHeader eyebrow={t("01 / MEMORY OPERATIONS", "01 / 记忆运行")} title={t("Memory operations", "记忆运行中心")} copy={t(`${snapshot.organization.name} / ${snapshot.organization.environment}. Updated ${formatTime(metrics.updatedAt, language)}.`, `${snapshot.organization.name} / ${snapshot.organization.environment}，更新于 ${formatTime(metrics.updatedAt, language)}。`)} actions={<><button className="button" type="button" onClick={() => onNavigate("memory")}>{t("Open explorer", "打开 Memory Explorer")}</button><button className="button primary" type="button" onClick={onCreateAgent} disabled={snapshot.sample}>{t("Create Agent", "创建 Agent")}</button></>} /><section className="metric-strip" aria-label={t("Infrastructure metrics", "基础设施指标")}><Metric label={t("Enabled Agents", "已启用 Agent")} value={formatNumber(metrics.activeAgents)} note={t("current workspace", "当前工作区")} /><Metric label={t("Event writes", "事件写入")} value={formatNumber(metrics.eventWrites)} note={t("last 24 hours", "最近 24 小时")} /><Metric label={t("Recall requests", "召回请求")} value={metrics.recallInstrumented ? formatNumber(metrics.recallRequests) : "-"} note={metrics.recallInstrumented ? t("instrumented requests", "已接入监测") : t("telemetry not connected", "Telemetry 尚未接入")} /><Metric label="Recall P95" value={metrics.recallInstrumented ? `${formatNumber(metrics.recallP95Ms)} ms` : "-"} note={metrics.recallInstrumented ? t("end-to-end", "端到端") : t("telemetry not connected", "Telemetry 尚未接入")} /><Metric label={t("Failure rate", "失败率")} value={metrics.operationInstrumented ? `${(metrics.failureRate * 100).toFixed(2)}%` : "-"} note={metrics.operationInstrumented ? t("instrumented operations", "已监测操作") : t("telemetry not connected", "Telemetry 尚未接入")} /></section><section className="overview-workbench"><div className="observatory-panel"><div className="panel-top"><div><span className="system-label">{t("MEMORY OBSERVATORY", "记忆观测台")}</span><select value={selectedAgentId ?? ""} onChange={(event) => onSelectAgent(event.target.value)}>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.displayName} / {agent.id}</option>)}</select></div><span>{t(`${events.length} visible nodes / ${edges.length} edges`, `${events.length} 个可见节点 / ${edges.length} 条边`)}</span></div><MemoryGraph compact events={events} edges={edges} /></div><aside className="operation-panel"><div className="panel-top"><div><span className="system-label">{t("RECENT CONTROL OPERATIONS", "最近操作")}</span><b>{t("Most recent", "按时间倒序")}</b></div><StatusPill value="connected" /></div><div className="operation-list">{operations.length ? operations.slice(0, 7).map((operation) => <article key={operation.id}><time>{formatTime(operation.occurredAt, language)}</time><div><b>{operation.type}</b><span>{operation.agentId || "workspace"}</span></div><div><StatusPill value={operation.status} />{operation.latencyMs > 0 && <span>{operation.latencyMs} ms</span>}</div><code>{operation.requestId}</code></article>) : <p className="panel-empty">{t("No control operations have been recorded.", "还没有记录到控制操作。")}</p>}</div><div className="pipeline-health"><span className="system-label">PIPELINE TELEMETRY</span><p>{t("Stage latency and queue health will appear after the runtime telemetry stream is connected.", "接入运行时 Telemetry 后，这里会显示各阶段延迟和队列状态。")}</p></div></aside></section></>;
}

function Metric({ label, value, note }: { label: string; value: string; note: string }) { return <div><span className="system-label">{label}</span><b>{value}</b><small>{note}</small></div>; }

function AgentsView({ agents, sample, onCreate, onOpenMemory, onToggle }: { agents: Agent[]; sample: boolean; onCreate: () => void; onOpenMemory: (id: string) => void; onToggle: (agent: Agent) => void }) {
  const { language, t } = useLanguage();
  const [query, setQuery] = useState("");
  const visible = agents.filter((agent) => `${agent.displayName} ${agent.id} ${agent.tags.join(" ")}`.toLowerCase().includes(query.toLowerCase()));
  return <><PageHeader eyebrow={t("02 / AGENT IDENTITIES", "02 / AGENT 身份")} title="Agents" copy={t("Manage durable identities and write state without entering a chat surface.", "管理长期稳定的 Agent 身份和写入状态，不需要进入对话界面。")} actions={<button className="button primary" type="button" onClick={onCreate} disabled={sample}>{t("Create Agent", "创建 Agent")}</button>} /><div className="table-controls"><label>{t("Search Agents", "搜索 Agent")}<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("Name, ID, or tag", "名称、ID 或 tag")}/></label><span>{t(`${visible.length} records`, `${visible.length} 条记录`)}</span></div>{visible.length ? <div className="table-shell"><table className="data-table"><thead><tr><th>Agent</th><th>{t("State", "状态")}</th><th>Policy</th><th>{t("Events", "事件")}</th><th>{t("Nodes", "节点")}</th><th>{t("Last write", "最近写入")}</th><th>Recall P95</th><th>{t("Actions", "操作")}</th></tr></thead><tbody>{visible.map((agent) => <tr key={agent.id}><td><b>{agent.displayName}</b><code>{agent.id}</code></td><td><StatusPill value={agent.state} /></td><td>{agent.policy}</td><td>{formatNumber(agent.eventCount)}</td><td>{formatNumber(agent.nodeCount)}</td><td>{formatTime(agent.lastWriteAt, language)}</td><td>{agent.recallP95Ms ? `${agent.recallP95Ms} ms` : "-"}</td><td><div className="row-actions"><button className="table-action" type="button" onClick={() => onOpenMemory(agent.id)}>{t("Inspect", "查看")}</button><button className="table-action" type="button" disabled={sample || agent.state === "archived"} onClick={() => onToggle(agent)}>{agent.state === "paused" ? t("Resume", "恢复") : t("Pause", "暂停")}</button></div></td></tr>)}</tbody></table></div> : <EmptyState code="02" title={t("No matching Agents", "没有符合条件的 Agent")} copy={t("Clear the search or create the first durable Agent identity.", "清除搜索条件，或者创建第一个长期使用的 Agent 身份。")} actions={<button className="button primary" type="button" onClick={onCreate} disabled={sample}>{t("Create Agent", "创建 Agent")}</button>} />}</>;
}

function MemoryView({ organizationId, agents, events, edges, selectedAgentId, sample, onSelectAgent, onCreate }: { organizationId: string; agents: Agent[]; events: MemoryEvent[]; edges: MemoryEdge[]; selectedAgentId: string | null; sample: boolean; onSelectAgent: (id: string) => void; onCreate: () => void }) {
  const { t } = useLanguage();
  if (!agents.length) return <><PageHeader eyebrow={t("03 / MEMORY STUDIO", "03 / MEMORY STUDIO")} title={t("Memory", "记忆")} copy={t("Inspect events, temporal relations, and path-based recall.", "查看事件、时间关系以及基于路径的召回过程。")} /><EmptyState code="03" title={t("No Agent memory to inspect", "还没有可查看的 Agent 记忆")} copy={t("Create an Agent and ingest the first event before opening the memory topology.", "先创建 Agent 并写入第一条事件，随后即可查看 Memory Graph。")} /></>;
  return <><PageHeader eyebrow="03 / MEMORY STUDIO" title={t("Memory", "记忆")} copy={t("Explore Slow Memory, expand Fast and Source evidence, or trace the production recall path.", "浏览 Slow Memory，展开 Fast 与 Source 证据，或追踪生产环境中的真实召回路径。")} actions={<button className="button primary" type="button" onClick={onCreate} disabled={sample}>{t("Add event", "写入事件")}</button>} /><section className="memory-controls"><label><span>AGENT</span><select value={selectedAgentId ?? ""} onChange={(event) => onSelectAgent(event.target.value)}>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.displayName} / {agent.id}</option>)}</select></label></section><MemoryExplorer organizationId={organizationId} agentId={selectedAgentId} sample={sample} fallbackEvents={events} fallbackEdges={edges} /></>;
}

function ApiKeysView({ keys, sample, oneTimeSecret, onDismissSecret, onCreate, onRevoke }: { keys: ApiKey[]; sample: boolean; oneTimeSecret: string | null; onDismissSecret: () => void; onCreate: () => void; onRevoke: (id: string) => void }) {
  const { language, t } = useLanguage();
  return <><PageHeader eyebrow={t("04 / ACCESS CONTROL", "04 / 访问控制")} title="API Keys" copy={t("Issue environment-scoped credentials with the minimum permissions an integration needs.", "按环境签发 API Key，并只授予集成真正需要的最小 Scope。")} actions={<button className="button primary" type="button" onClick={onCreate} disabled={sample}>{t("Create API Key", "创建 API Key")}</button>} />{oneTimeSecret && <div className="secret-panel" role="status"><div><span className="system-label">{t("COPY NOW / SHOWN ONCE", "请立即复制 / TOKEN 仅显示一次")}</span><code>{oneTimeSecret}</code></div><button type="button" onClick={() => void navigator.clipboard?.writeText(oneTimeSecret)}>{t("Copy Token", "复制 Token")}</button><button type="button" onClick={onDismissSecret}>{t("Dismiss", "关闭")}</button></div>}{keys.length ? <div className="table-shell"><table className="data-table"><thead><tr><th>Key</th><th>{t("Environment", "环境")}</th><th>Scopes</th><th>{t("Created by", "创建人")}</th><th>{t("Last used", "最近使用")}</th><th>{t("Expires", "过期时间")}</th><th>{t("Status", "状态")}</th><th /></tr></thead><tbody>{keys.map((key) => <tr key={key.id}><td><b>{key.name}</b><code>{key.prefix}...</code></td><td>{key.environment}</td><td><div className="scope-list">{key.scopes.map((scope) => <span key={scope}>{scope}</span>)}</div></td><td>{key.createdBy}</td><td>{formatTime(key.lastUsedAt, language)}</td><td>{formatTime(key.expiresAt, language)}</td><td><StatusPill value={key.status} /></td><td><button className="table-action danger" type="button" disabled={sample || key.status === "revoked"} onClick={() => onRevoke(key.id)}>{t("Revoke", "吊销")}</button></td></tr>)}</tbody></table></div> : <EmptyState code="04" title={t("No API Keys", "还没有 API Key")} copy={t("Create a scoped API Key. Its Token will be revealed exactly once.", "创建带 Scope 的 API Key；对应 Token 只会显示一次。")} actions={<button className="button primary" type="button" onClick={onCreate} disabled={sample}>{t("Create API Key", "创建 API Key")}</button>} />}</>;
}

function TeamView({ members, sample, onInvite }: { members: Member[]; sample: boolean; onInvite: () => void }) {
  const { language, t } = useLanguage();
  return <><PageHeader eyebrow={t("06 / ORGANIZATION ACCESS", "06 / 工作区权限")} title={t("Team", "团队")} copy={t("Assign operator roles separately from Agent and API credentials.", "人员 Role、Agent 身份和 API 凭证分别管理，避免权限混用。")} actions={<button className="button primary" type="button" onClick={onInvite} disabled={sample}>{t("Invite member", "邀请成员")}</button>} />{members.length ? <div className="table-shell"><table className="data-table"><thead><tr><th>{t("Member", "成员")}</th><th>Role</th><th>{t("Status", "状态")}</th><th>MFA</th><th>{t("Last active", "最近活跃")}</th></tr></thead><tbody>{members.map((member) => <tr key={member.id}><td><b>{member.name}</b><code>{member.email}</code></td><td>{member.role}</td><td><StatusPill value={member.status} /></td><td>{member.mfa ? t("Enforced", "已强制启用") : t("Not enabled", "未启用")}</td><td>{formatTime(member.lastActiveAt, language)}</td></tr>)}</tbody></table></div> : <EmptyState code="06" title={t("No workspace members", "工作区还没有成员")} copy={t("Invite the first Developer or Auditor to this workspace.", "邀请第一位 Developer 或 Auditor 加入当前工作区。")} actions={<button className="button primary" type="button" onClick={onInvite} disabled={sample}>{t("Invite member", "邀请成员")}</button>} />}</>;
}

function UsageView({ metrics, days }: { metrics: Metrics; days: UsageDay[] }) {
  const { t } = useLanguage();
  const max = Math.max(1, ...days.map((day) => day.eventWrites));
  return <><PageHeader eyebrow={t("05 / METERING", "05 / 用量统计")} title={t("Usage", "用量")} copy={t("Trace stored volume and event writes without estimating an unconfigured bill.", "查看真实存储量和事件写入量；在计费规则尚未配置时，不生成推测费用。")} actions={<button className="button" type="button" onClick={() => window.print()}>{t("Export view", "导出当前视图")}</button>} /><section className="metric-strip usage-metrics"><Metric label={t("Event writes", "事件写入")} value={formatNumber(metrics.eventWrites)} note={t("last 24 hours", "最近 24 小时")} /><Metric label={t("Recall requests", "召回请求")} value={metrics.recallInstrumented ? formatNumber(metrics.recallRequests) : "-"} note={metrics.recallInstrumented ? t("instrumented", "已接入监测") : t("not connected", "尚未接入")} /><Metric label={t("Stored nodes", "已存节点")} value={formatNumber(metrics.nodeCount)} note={t("current snapshot", "当前 snapshot")} /><Metric label={t("Stored edges", "已存关系边")} value={formatNumber(metrics.edgeCount)} note={t("current snapshot", "当前 snapshot")} /><Metric label="Recall P95" value={metrics.recallInstrumented ? `${metrics.recallP95Ms} ms` : "-"} note={metrics.recallInstrumented ? t("end-to-end", "端到端") : t("not connected", "尚未接入")} /></section>{days.length ? <section className="usage-panel"><div className="panel-top"><div><span className="system-label">{t("DAILY EVENT WRITES", "每日事件写入")}</span><b>{t("Authoritative D1 records", "以 D1 记录为准")}</b></div><span>{t("UTC day buckets", "按 UTC 日期统计")}</span></div><div className="usage-chart" role="img" aria-label={t(`Daily event writes for ${days.length} days. Peak ${formatNumber(max)} writes.`, `最近 ${days.length} 天的事件写入量，峰值为 ${formatNumber(max)} 次。`)}>{days.map((day) => <div key={day.date}><span className="usage-bar" style={{ "--bar-height": `${Math.max(4, (day.eventWrites / max) * 100)}%` } as CSSProperties}><i /></span><b>{formatNumber(day.eventWrites)}</b><small>{day.date.slice(5)}</small></div>)}</div><div className="usage-table"><span>{t("DATE", "日期")}</span><span>{t("WRITES", "写入")}</span><span>{t("RECALLS", "召回")}</span><span>P95</span><span>{t("ERROR", "错误率")}</span>{days.map((day) => <div key={day.date}><time>{day.date}</time><b>{formatNumber(day.eventWrites)}</b><b>{metrics.recallInstrumented ? formatNumber(day.recalls) : "-"}</b><b>{metrics.recallInstrumented ? `${day.p95Ms} ms` : "-"}</b><b>{metrics.operationInstrumented ? `${(day.errorRate * 100).toFixed(2)}%` : "-"}</b></div>)}</div></section> : <EmptyState code="05" title={t("No usage in this range", "当前时间范围内没有用量")} copy={t("Usage appears after the first event write.", "写入第一条事件后，这里会开始显示用量。")}/>}</>;
}

function AuditView({ logs }: { logs: AuditLog[] }) {
  const { language, t } = useLanguage();
  const [query, setQuery] = useState("");
  const visible = logs.filter((log) => `${log.actor} ${log.action} ${log.resourceId} ${log.requestId}`.toLowerCase().includes(query.toLowerCase()));
  return <><PageHeader eyebrow={t("07 / GOVERNANCE LOG", "07 / 治理记录")} title="Audit" copy={t("Immutable operator, credential, memory, and policy actions for enterprise review.", "以不可变记录保留人员操作、凭证变更、记忆写入和 Policy 调整，供企业审查。")} /><div className="table-controls"><label>{t("Search Audit log", "搜索 Audit 记录")}<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("Actor, action, resource, request", "Actor、action、resource 或 request")}/></label><span>{t(`${visible.length} events`, `${visible.length} 条事件`)}</span></div>{visible.length ? <div className="table-shell"><table className="data-table"><thead><tr><th>{t("Timestamp", "时间")}</th><th>Actor</th><th>Action</th><th>Resource</th><th>{t("Result", "结果")}</th><th>Request ID</th><th>{t("Reason", "原因")}</th></tr></thead><tbody>{visible.map((log) => <tr key={log.id}><td>{formatTime(log.timestamp, language)}</td><td>{log.actor}</td><td><code>{log.action}</code></td><td><b>{log.resourceType}</b><code>{log.resourceId}</code></td><td><StatusPill value={log.result} /></td><td><code>{log.requestId}</code></td><td>{log.reason || "-"}</td></tr>)}</tbody></table></div> : <EmptyState code="07" title={t("No Audit events", "还没有 Audit 事件")} copy={t("Workspace mutations and sensitive operations will appear here.", "工作区变更和敏感操作会记录在这里。")}/>}</>;
}

function SettingsView({ organization, sample, busy, onSave }: { organization: Organization; sample: boolean; busy: boolean; onSave: (payload: UnknownRecord) => void }) {
  const { t } = useLanguage();
  return <><PageHeader eyebrow={t("08 / WORKSPACE POLICY", "08 / 工作区 POLICY")} title={t("Settings", "设置")} copy={t("Manage workspace identity and see which governance controls are connected.", "管理工作区身份，并明确查看哪些治理能力已经接入。")} /><form className="settings-grid" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); onSave({ name: String(data.get("name") ?? ""), slug: String(data.get("slug") ?? "") }); }}><section><span className="system-label">{t("GENERAL", "基本信息")}</span><h2>{t("Workspace identity", "工作区身份")}</h2><label>{t("Organization name", "组织名称")}<input name="name" defaultValue={organization.name} required /></label><label>Workspace slug<input name="slug" defaultValue={organization.slug} required pattern="[a-z0-9-]+" /></label><label>Workspace ID<input value={organization.id || t("Not assigned", "尚未分配")} disabled /></label><label>Data region<input value={organization.region} disabled /></label></section><section><span className="system-label">{t("GOVERNANCE CONNECTIONS", "治理能力接入状态")}</span><h2>Policy services</h2><dl className="settings-status"><div><dt>Retention policy</dt><dd>{t("Not configured", "尚未配置")}</dd></div><div><dt>PII redaction</dt><dd>{t("Not configured", "尚未配置")}</dd></div><div><dt>MFA enforcement</dt><dd>{t("Managed by ChatGPT sign-in", "由 ChatGPT 登录管理")}</dd></div><div><dt>Webhooks</dt><dd>{t("Not configured", "尚未配置")}</dd></div></dl><p className="settings-note">{t("Unconnected controls are shown explicitly and are not treated as active policy.", "尚未接入的控制项会明确标出，不会被当作已经生效的 Policy。")}</p></section><footer><p>{t("Workspace identity changes are attributed to the signed-in Actor and recorded in Audit.", "工作区身份变更会归因到当前登录的 Actor，并写入 Audit。")}</p><button className="button primary" type="submit" disabled={sample || busy}>{busy ? t("Saving...", "正在保存...") : t("Save workspace", "保存工作区")}</button></footer></form></>;
}
