import { createHmac } from "node:crypto";

function text(value) {
  return typeof value === "string" ? value : String(value ?? "");
}

export function opaqueId(secret, kind, material) {
  const digest = createHmac("sha256", secret)
    .update(`${kind}\0${text(material)}`, "utf8")
    .digest("hex")
    .slice(0, 40);
  return `ocw_${kind}_${digest}`;
}

export function deriveIdentity({ config, context = {}, runId = "" }) {
  const sessionKey = text(
    context.sessionKey || context.sessionId || context.chatId || "unknown-session",
  );
  const agentId = text(context.agentId || "default-agent");
  const channel = text(context.channel || context.messageProvider || "unknown-channel");
  const sender = text(
    context.senderId || context.channelContext?.sender?.id || "",
  );
  const chat = text(context.chatId || context.channelContext?.chat?.id || "");
  const owner = sender ? `sender:${sender}` : chat ? `chat:${chat}` : `session:${sessionKey}`;
  const scopeMaterial = [config.tenantId, config.scopeNamespace, agentId, channel, owner].join("\0");
  const scopeName = opaqueId(config.identitySecret, "scope", scopeMaterial);
  const sessionId = opaqueId(
    config.identitySecret,
    "session",
    `${scopeName}\0${sessionKey}`,
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

export function messageId({ config, identity, role, sourceId = "", content = "", ordinal = 0 }) {
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

export function ingestIdempotencyKey({ config, identity, userMessageId, assistantMessageId }) {
  return opaqueId(
    config.identitySecret,
    "ingest",
    [identity.scopeName, identity.sessionId, userMessageId, assistantMessageId].join("\0"),
  );
}
