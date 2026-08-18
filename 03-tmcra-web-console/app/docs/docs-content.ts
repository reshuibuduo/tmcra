import type { LocalizedText } from "../i18n";

export type EndpointMethod = "GET" | "POST" | "PUT" | "DELETE";

export type EndpointGroup = {
  id: string;
  label: LocalizedText;
  description: LocalizedText;
};

export type EndpointDoc = {
  group: string;
  method: EndpointMethod;
  path: string;
  title: LocalizedText;
  description: LocalizedText;
  note?: LocalizedText;
};

export const endpointGroups: EndpointGroup[] = [
  {
    id: "credentials",
    label: { en: "Credentials", zh: "访问凭据" },
    description: {
      en: "Issue, inspect, and revoke least-privilege access tokens.",
      zh: "签发、查看和撤销最小权限访问令牌。",
    },
  },
  {
    id: "memory",
    label: { en: "Memory", zh: "记忆读写" },
    description: {
      en: "Ingest conversation events, recall evidence, and trigger slow-memory evolution.",
      zh: "写入对话事件、召回证据，并触发慢图演化。",
    },
  },
  {
    id: "jobs",
    label: { en: "Jobs", zh: "异步任务" },
    description: {
      en: "Observe and control asynchronous work without replaying successful writes.",
      zh: "查看和控制异步任务，不重放已经成功的写入。",
    },
  },
  {
    id: "graph",
    label: { en: "Memory graph", zh: "记忆图谱" },
    description: {
      en: "Inspect slow memory first, then expand Fast and Source evidence lazily.",
      zh: "先查看慢图，再按需展开快图和 Source 原始证据。",
    },
  },
  {
    id: "governance",
    label: { en: "Governance", zh: "治理与生命周期" },
    description: {
      en: "Export, retain, delete, reopen, and evaluate memory scopes.",
      zh: "导出、保留、删除、重开并评价记忆 Scope。",
    },
  },
  {
    id: "webhooks",
    label: { en: "Webhooks", zh: "Webhook" },
    description: {
      en: "Receive signed lifecycle events without exposing memory content.",
      zh: "接收带签名的生命周期事件，不暴露记忆正文。",
    },
  },
  {
    id: "usage",
    label: { en: "Usage", zh: "用量" },
    description: {
      en: "Read tenant-level usage and cost ledger data.",
      zh: "查看租户级用量与成本账本。",
    },
  },
];

export const endpoints: EndpointDoc[] = [
  {
    group: "credentials",
    method: "GET",
    path: "/v1/session",
    title: { en: "Get authenticated session", zh: "获取当前认证会话" },
    description: {
      en: "Returns the authenticated tenant, credential identity, permissions, and allowed memory scopes for the current Token.",
      zh: "返回当前 Token 对应的租户、凭据身份、权限和允许访问的记忆 Scope。",
    },
  },
  {
    group: "credentials",
    method: "GET",
    path: "/v1/access-tokens",
    title: { en: "List access tokens", zh: "列出访问令牌" },
    description: {
      en: "Returns token metadata for the current tenant. This listing never includes token secrets.",
      zh: "返回当前租户的令牌元数据；令牌密钥不会再次返回。",
    },
  },
  {
    group: "credentials",
    method: "POST",
    path: "/v1/access-tokens",
    title: { en: "Issue an access token", zh: "签发访问令牌" },
    description: {
      en: "Creates an expiring token with explicit permissions and a scope allowlist.",
      zh: "创建带有效期、明确权限和 Scope 白名单的访问令牌。",
    },
    note: {
      en: "Requires Idempotency-Key. Retrying the same body with the same key returns the same Token; reusing the key for another body returns 409.",
      zh: "必须提供 Idempotency-Key。同一个 Key 重试相同请求会返回同一个 Token；若改动请求体则返回 409。",
    },
  },
  {
    group: "credentials",
    method: "POST",
    path: "/v1/access-tokens/{token_id}/confirm",
    title: { en: "Confirm provisional token delivery", zh: "确认临时 Token 已安全交付" },
    description: {
      en: "Idempotently activates the full lifetime of a provisionally delivered Token.",
      zh: "幂等确认临时 Token 已由客户端安全保存，并启用其完整有效期。",
    },
  },
  {
    group: "credentials",
    method: "DELETE",
    path: "/v1/access-tokens/{token_id}",
    title: { en: "Revoke an access token", zh: "撤销访问令牌" },
    description: {
      en: "Revokes one tenant-owned token immediately.",
      zh: "立即撤销当前租户拥有的指定令牌。",
    },
  },
  {
    group: "memory",
    method: "POST",
    path: "/v1/scopes/{scope_name}/ingest",
    title: { en: "Ingest memory events", zh: "写入记忆事件" },
    description: {
      en: "Commits one session of ordered messages through the durable asynchronous writer chain.",
      zh: "通过可靠异步写入链提交一个 Session 的有序消息。",
    },
    note: {
      en: "Requires Idempotency-Key. Use read_your_writes when the next recall depends on this write.",
      zh: "必须提供 Idempotency-Key；下一次召回依赖本次写入时使用 read_your_writes。",
    },
  },
  {
    group: "memory",
    method: "POST",
    path: "/v1/scopes/{scope_name}/ingest/batch",
    title: { en: "Bulk ingest", zh: "批量写入" },
    description: {
      en: "Atomically reserves capacity and idempotency for up to 100 independent ingest items.",
      zh: "为最多 100 个独立写入项原子预留容量和幂等记录。",
    },
  },
  {
    group: "memory",
    method: "POST",
    path: "/v1/scopes/{scope_name}/recall",
    title: { en: "Recall memory", zh: "召回记忆" },
    description: {
      en: "Returns structured evidence and bounded prompt_evidence for the caller's answer model.",
      zh: "返回结构化证据和有界 prompt_evidence，交由调用方的回答模型使用。",
    },
    note: {
      en: "TMCRA does not generate the final answer. recall_profile defaults to quality; use interactive only for latency-bounded hooks. response_projection defaults to full; prompt_only returns the same prompt evidence without duplicating raw evidence objects. max_windows is omitted or fixed to 8.",
      zh: "TMCRA 不生成最终回答；recall_profile 默认使用 quality，仅在有严格延迟上限的钩子中使用 interactive；response_projection 默认使用 full，prompt_only 会保留相同的提示证据但不重复返回原始 evidence 对象；max_windows 省略或固定为 8。",
    },
  },
  {
    group: "memory",
    method: "POST",
    path: "/v1/scopes/{scope_name}/consolidate",
    title: { en: "Consolidate slow memory", zh: "整理慢图记忆" },
    description: {
      en: "Queues policy-gated slow-memory evolution for a scope.",
      zh: "为指定 Scope 排队执行受策略门控的慢图演化。",
    },
  },
  {
    group: "memory",
    method: "GET",
    path: "/v1/scopes",
    title: { en: "List memory scopes", zh: "列出记忆范围" },
    description: {
      en: "Lists the scopes visible to the current credential, optionally filtered by an allowed prefix.",
      zh: "列出当前凭据有权访问的 Scope，并可按获准前缀筛选。",
    },
  },
  {
    group: "memory",
    method: "GET",
    path: "/v1/scopes/{scope_name}/summary",
    title: { en: "Get scope summary", zh: "查看 Scope 摘要" },
    description: {
      en: "Returns server-recorded ingest, recall, message, and session activity for one scope.",
      zh: "返回指定 Scope 在服务端记录的写入、召回、消息和 Session 活动。",
    },
  },
  {
    group: "jobs",
    method: "GET",
    path: "/v1/jobs/{job_id}",
    title: { en: "Get job", zh: "查询任务" },
    description: {
      en: "Returns the current state, result, or typed error for an asynchronous job.",
      zh: "返回异步任务的当前状态、结果或结构化错误。",
    },
  },
  {
    group: "jobs",
    method: "POST",
    path: "/v1/jobs/{job_id}/cancel",
    title: { en: "Cancel job", zh: "取消任务" },
    description: {
      en: "Requests cancellation for work that has not reached a terminal state.",
      zh: "请求取消尚未进入终态的任务。",
    },
  },
  {
    group: "jobs",
    method: "POST",
    path: "/v1/jobs/{job_id}/retry",
    title: { en: "Retry failed job", zh: "重试失败任务" },
    description: {
      en: "Creates a new bounded retry for an eligible failed job.",
      zh: "为符合条件的失败任务创建一次新的有界重试。",
    },
    note: { en: "Requires Idempotency-Key.", zh: "必须提供 Idempotency-Key。" },
  },
  {
    group: "graph",
    method: "GET",
    path: "/v1/scopes/{scope_name}/memory-graph",
    title: { en: "Get graph overview", zh: "获取图谱概览" },
    description: {
      en: "Returns a paginated slow-memory-first graph overview and layer counts.",
      zh: "返回分页的慢图优先概览和各层数量。",
    },
  },
  {
    group: "graph",
    method: "GET",
    path: "/v1/scopes/{scope_name}/memory-graph/nodes/{memory_id}/neighbors",
    title: { en: "Expand graph neighbors", zh: "展开相邻节点" },
    description: {
      en: "Lazily expands related Slow, Fast, and Source nodes around one memory.",
      zh: "按需展开指定记忆周围的 Slow、Fast 和 Source 节点。",
    },
  },
  {
    group: "graph",
    method: "GET",
    path: "/v1/scopes/{scope_name}/memory-graph/nodes/{memory_id}/evidence",
    title: { en: "Read source evidence", zh: "读取原始证据" },
    description: {
      en: "Returns explicitly requested verbatim Source evidence and provenance.",
      zh: "返回显式请求的 Source 原文证据及其来源信息。",
    },
  },
  {
    group: "graph",
    method: "POST",
    path: "/v1/scopes/{scope_name}/memory-graph/trace",
    title: { en: "Trace recall", zh: "追踪召回链路" },
    description: {
      en: "Runs the production recall planner and returns routing diagnostics without an answer-model call.",
      zh: "运行生产召回规划器并返回路由诊断，不调用回答模型。",
    },
  },
  {
    group: "governance",
    method: "POST",
    path: "/v1/scopes/{scope_name}/exports",
    title: { en: "Create scope export", zh: "创建 Scope 导出" },
    description: {
      en: "Queues a consistent portable export of scope-owned memory data.",
      zh: "排队生成指定 Scope 的一致、可迁移记忆导出。",
    },
    note: { en: "Requires Idempotency-Key.", zh: "必须提供 Idempotency-Key。" },
  },
  {
    group: "governance",
    method: "GET",
    path: "/v1/scopes/{scope_name}/exports/{export_id}",
    title: { en: "Download scope export", zh: "下载 Scope 导出" },
    description: {
      en: "Downloads a completed export from the same tenant and scope boundary.",
      zh: "在同一租户与 Scope 边界内下载已经完成的导出。",
    },
  },
  {
    group: "governance",
    method: "GET",
    path: "/v1/scopes/{scope_name}/retention",
    title: { en: "Get retention policy", zh: "获取保留策略" },
    description: {
      en: "Returns the effective inactivity retention policy for a scope.",
      zh: "返回指定 Scope 当前生效的非活跃数据保留策略。",
    },
  },
  {
    group: "governance",
    method: "PUT",
    path: "/v1/scopes/{scope_name}/retention",
    title: { en: "Set retention policy", zh: "设置保留策略" },
    description: {
      en: "Enables, disables, or changes inactivity-based retention from 1 to 3650 days.",
      zh: "启用、停用或修改 1 至 3650 天的非活跃保留策略。",
    },
  },
  {
    group: "governance",
    method: "POST",
    path: "/v1/scopes/{scope_name}/feedback",
    title: { en: "Submit recall feedback", zh: "提交召回反馈" },
    description: {
      en: "Records helpful, incorrect, stale, unsafe, or missing evidence feedback.",
      zh: "记录 helpful、incorrect、stale、unsafe 或 missing 类型的证据反馈。",
    },
  },
  {
    group: "governance",
    method: "DELETE",
    path: "/v1/scopes/{scope_name}",
    title: { en: "Delete scope", zh: "删除 Scope" },
    description: {
      en: "Queues physical artifact deletion while preserving lifecycle and audit tombstones.",
      zh: "排队物理删除记忆产物，同时保留生命周期与审计墓碑。",
    },
    note: {
      en: "Requires Idempotency-Key and X-TMCRA-Confirm-Scope with the exact scope name.",
      zh: "必须提供 Idempotency-Key，并通过 X-TMCRA-Confirm-Scope 精确确认 Scope 名称。",
    },
  },
  {
    group: "governance",
    method: "POST",
    path: "/v1/scopes/{scope_name}/reopen",
    title: { en: "Reopen deleted scope", zh: "重开已删除 Scope" },
    description: {
      en: "Returns an eligible tombstoned scope to active without restoring deleted artifacts.",
      zh: "将符合条件的墓碑 Scope 恢复为 active，但不会恢复已经删除的记忆产物。",
    },
  },
  {
    group: "webhooks",
    method: "GET",
    path: "/v1/webhooks",
    title: { en: "List webhooks", zh: "列出 Webhook" },
    description: {
      en: "Lists endpoint metadata and subscribed events without revealing signing secrets.",
      zh: "列出端点元数据和订阅事件，不返回签名密钥。",
    },
  },
  {
    group: "webhooks",
    method: "POST",
    path: "/v1/webhooks",
    title: { en: "Create webhook", zh: "创建 Webhook" },
    description: {
      en: "Registers an HTTPS endpoint and returns its HMAC signing secret once.",
      zh: "注册 HTTPS 端点，并一次性返回 HMAC 签名密钥。",
    },
  },
  {
    group: "webhooks",
    method: "DELETE",
    path: "/v1/webhooks/{endpoint_id}",
    title: { en: "Disable webhook", zh: "停用 Webhook" },
    description: {
      en: "Disables one tenant-owned delivery endpoint.",
      zh: "停用当前租户拥有的指定投递端点。",
    },
  },
  {
    group: "usage",
    method: "PUT",
    path: "/v1/usage/entitlements/{subject}",
    title: { en: "Set subject entitlements", zh: "设置用户额度" },
    description: {
      en: "Sets authoritative ingest-token and recall-request limits for one subject.",
      zh: "为指定用户设置服务端生效的写入 Token 与召回次数上限。",
    },
    note: {
      en: "Requires a tokens:manage API key; ordinary access tokens cannot use this operation.",
      zh: "仅限具备 tokens:manage 权限的 API Key；普通访问令牌不能调用。",
    },
  },
  {
    group: "usage",
    method: "GET",
    path: "/v1/usage/quota",
    title: { en: "Get quota", zh: "查看额度" },
    description: {
      en: "Returns server-authoritative usage, limits, and remaining quota for the authenticated principal or an authorized subject.",
      zh: "返回当前调用主体或获准用户的服务端用量、上限与剩余额度。",
    },
  },
  {
    group: "usage",
    method: "PUT",
    path: "/v1/usage/quota",
    title: { en: "Set quota entitlement", zh: "更新额度上限" },
    description: {
      en: "Updates authoritative quota limits for an explicitly selected subject.",
      zh: "更新明确指定用户的服务端额度上限。",
    },
    note: {
      en: "Requires a tokens:manage API key.",
      zh: "需要具备 tokens:manage 权限的 API Key。",
    },
  },
  {
    group: "usage",
    method: "GET",
    path: "/v1/usage/costs",
    title: { en: "Get usage costs", zh: "获取用量成本" },
    description: {
      en: "Returns the tenant cost ledger for operational accounting and limits.",
      zh: "返回租户成本账本，用于运营核算与限额管理。",
    },
  },
];

export const errorRows: Array<{
  status: string;
  action: LocalizedText;
}> = [
  { status: "400 / 422", action: { en: "Fix validation or request semantics before retrying.", zh: "修正参数校验或请求语义后再重试。" } },
  { status: "401 / 403", action: { en: "Replace or re-scope the credential; do not retry unchanged.", zh: "更换或调整凭据权限，不要原样重试。" } },
  { status: "404", action: { en: "The resource does not exist or is outside this credential boundary.", zh: "资源不存在，或超出当前凭据边界。" } },
  { status: "409", action: { en: "Inspect lifecycle, idempotency, or consistency conflict details.", zh: "检查生命周期、幂等或一致性冲突详情。" } },
  { status: "429", action: { en: "Honor Retry-After and reuse the same idempotency key.", zh: "遵守 Retry-After，并复用相同幂等键。" } },
  { status: "5xx", action: { en: "Retry only GETs and idempotent writes with bounded backoff.", zh: "仅对 GET 和幂等写入进行有界退避重试。" } },
];
