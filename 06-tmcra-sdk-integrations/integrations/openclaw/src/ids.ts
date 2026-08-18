import { createHmac } from "node:crypto";

export interface IdentityConfig {
  tenantId: string;
  scopeNamespace: string;
  projectScopePrefix: string;
  identitySecret: string;
  sharedProjectId?: string;
  projectScope?: string;
}

export interface DerivedIdentity {
  scopeName: string;
  sessionId: string;
  runId: string;
  runKey: string;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : String(value ?? "");
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export function opaqueId(secret: string, kind: string, material: unknown): string {
  const digest = createHmac("sha256", secret)
    .update(`${kind}\0${text(material)}`, "utf8")
    .digest("hex")
    .slice(0, 40);
  return `ocw_${kind}_${digest}`;
}

export function deriveIdentity({
  config,
  context = {},
  runId = "",
}: {
  config: IdentityConfig;
  context?: Record<string, unknown>;
  runId?: unknown;
}): DerivedIdentity {
  const channelContext = record(context.channelContext);
  const senderContext = record(channelContext.sender);
  const chatContext = record(channelContext.chat);
  const sessionKey = text(
    context.sessionKey || context.sessionId || context.chatId || "unknown-session",
  );
  const agentId = text(context.agentId || "default-agent");
  const channel = text(context.channel || context.messageProvider || "unknown-channel");
  const sender = text(context.senderId || senderContext.id || "");
  const chat = text(context.chatId || chatContext.id || "");
  const owner = sender ? `sender:${sender}` : chat ? `chat:${chat}` : `session:${sessionKey}`;
  const workspace = text(context.workspaceDir || "");
  const sharedProject = text(config.sharedProjectId || "")
    || workspace
    || (chat ? `${channel}:chat:${chat}` : `${channel}:${owner}`);
  // Agent identity is deliberately excluded from the project scope.  A team
  // of specialized agents shares project memory, while each agent retains a
  // separate session below that scope.
  const scopeMaterial = [config.tenantId, config.scopeNamespace, sharedProject].join("\0");
  const scopeDigest = createHmac("sha256", config.identitySecret)
    .update(`scope\0${scopeMaterial}`, "utf8")
    .digest("hex")
    .slice(0, 40);
  const scopeName = text(config.projectScope || "") || `${config.projectScopePrefix}_${scopeDigest}`;
  const sessionId = opaqueId(
    config.identitySecret,
    "session",
    `${scopeName}\0${agentId}\0${sessionKey}`,
  );
  const rawRunId = text(runId || context.runId || "");
  return {
    scopeName,
    sessionId,
    runId: rawRunId ? opaqueId(config.identitySecret, "run", `${sessionId}\0${rawRunId}`) : "",
    runKey: rawRunId
      ? opaqueId(config.identitySecret, "runkey", `${sessionId}\0${rawRunId}`)
      : `session:${sessionId}`,
  };
}

export function messageId({
  config,
  identity,
  role,
  sourceId = "",
  content = "",
  ordinal = 0,
}: {
  config: IdentityConfig;
  identity: DerivedIdentity;
  role: string;
  sourceId?: unknown;
  content?: unknown;
  ordinal?: number;
}): string {
  const material = [
    identity.sessionId,
    identity.runId,
    role,
    text(sourceId),
    text(content),
    String(ordinal),
  ].join("\0");
  return opaqueId(config.identitySecret, "message", material);
}

export function ingestIdempotencyKey({
  config,
  identity,
  userMessageId,
  assistantMessageId,
}: {
  config: IdentityConfig;
  identity: DerivedIdentity;
  userMessageId: string;
  assistantMessageId: string;
}): string {
  return opaqueId(
    config.identitySecret,
    "ingest",
    [identity.scopeName, identity.sessionId, userMessageId, assistantMessageId].join("\0"),
  );
}
