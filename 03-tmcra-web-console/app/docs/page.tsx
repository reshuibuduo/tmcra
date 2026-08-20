"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import BrandMark from "../BrandMark";
import { LanguageToggle, useLanguage } from "../i18n";
import { endpointGroups, endpoints, errorRows, type EndpointMethod } from "./docs-content";

const API_BASE = "https://api.tmcra.com";

const snippets = {
  curl: `curl -X POST "$TMCRA_BASE_URL/v1/scopes/user_123/ingest" \\
  -H "Authorization: Bearer $TMCRA_TOKEN" \\
  -H "Content-Type: application/json" \\
  -H "Idempotency-Key: ingest-session-42-v1" \\
  -d '{
    "session_id": "session_42",
    "consistency": "read_your_writes",
    "slow_policy": "auto",
    "messages": [{
      "message_id": "msg_001",
      "role": "user",
      "content": "I prefer concise answers.",
      "timestamp": "2026-07-16T08:00:00Z"
    }]
  }'`,
  python: `import os
from datetime import datetime, timezone
from tmcra_client import IngestRequest, MemoryMessage, SyncClient

request = IngestRequest(
    session_id="session_42",
    messages=[MemoryMessage(
        message_id="msg_001",
        role="user",
        content="I prefer concise answers.",
        timestamp=datetime.now(timezone.utc),
    )],
    consistency="read_your_writes",
)

with SyncClient(os.environ["TMCRA_BASE_URL"], api_key=os.environ["TMCRA_TOKEN"]) as client:
    job = client.ingest("user_123", request, idempotency_key="ingest-session-42-v1")
    completed = client.wait_for_job(job.job_id)
    recalled = client.recall("user_123", {
        "query": "How should I answer?",
        "wait_for_job_id": completed.job_id,
    })
    print(recalled.prompt_evidence.content)`,
  typescript: `import { TMCRAClient } from "@tmcra/typescript";

const client = new TMCRAClient({
  baseUrl: process.env.TMCRA_BASE_URL!,
  apiKey: process.env.TMCRA_TOKEN!,
});

const job = await client.ingest("user_123", {
  session_id: "session_42",
  messages: [{
    message_id: "msg_001",
    role: "user",
    content: "I prefer concise answers.",
    timestamp: new Date(),
  }],
  consistency: "read_your_writes",
});

const completed = await client.waitForJob(job.job_id);
const recalled = await client.recall("user_123", {
  query: "How should I answer?",
  wait_for_job_id: completed.job_id,
});
const promptEvidence = recalled.prompt_evidence;
const promptContext =
  promptEvidence !== null &&
  typeof promptEvidence === "object" &&
  !Array.isArray(promptEvidence) &&
  typeof promptEvidence.content === "string"
    ? promptEvidence.content
    : null;

if (promptContext) {
  console.log(promptContext);
}`,
} as const;

type SnippetName = keyof typeof snippets;

const guideLinks = [
  ["overview", "Overview", "概览"],
  ["quickstart", "Quickstart", "快速接入"],
  ["identity", "Identity and isolation", "身份与隔离"],
  ["consistency", "Consistency", "一致性"],
  ["webhook-guide", "Webhooks", "Webhook"],
  ["errors", "Errors and retries", "错误与重试"],
  ["sdks", "SDKs and adapters", "SDK 与适配器"],
] as const;

function methodClass(method: EndpointMethod) {
  return `docs-method is-${method.toLowerCase()}`;
}

export default function ApiDocsPage() {
  const { localize, t } = useLanguage();
  const [query, setQuery] = useState("");
  const [snippetName, setSnippetName] = useState<SnippetName>("curl");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    document.title = t("TMCRA API Documentation", "TMCRA API 文档");
  }, [t]);

  const normalizedQuery = query.trim().toLowerCase();
  const filteredEndpoints = useMemo(() => {
    if (!normalizedQuery) return endpoints;
    return endpoints.filter((endpoint) => [
      endpoint.method,
      endpoint.path,
      endpoint.title.en,
      endpoint.title.zh,
      endpoint.description.en,
      endpoint.description.zh,
    ].some((value) => value.toLowerCase().includes(normalizedQuery)));
  }, [normalizedQuery]);

  const copySnippet = async () => {
    try {
      await window.navigator.clipboard.writeText(snippets[snippetName]);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  };

  return (
    <main className="docs-page">
      <header className="docs-topbar">
        <Link className="docs-brand" href="/" aria-label={t("TMCRA home", "TMCRA 首页")}>
          <BrandMark />
          <span>TMCRA</span>
          <i>{t("API Documentation", "API 文档")}</i>
        </Link>
        <label className="docs-search">
          <span className="sr-only">{t("Search API documentation", "搜索 API 文档")}</span>
          <span aria-hidden="true">/</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("Search endpoints, paths, concepts", "搜索端点、路径或概念")}
          />
        </label>
        <LanguageToggle className="docs-language-toggle" />
        <Link className="docs-console-link" href="/console">{t("Console", "控制台")}</Link>
      </header>

      <div className="docs-shell">
        <aside className="docs-sidebar" aria-label={t("Documentation navigation", "文档导航")}>
          <div className="docs-version"><span>API</span><strong>v0.3.0-rc2</strong><i>{t("Release candidate", "候选发布版")}</i></div>
          <nav>
            <p>{t("Guides", "指南")}</p>
            {guideLinks.map(([id, en, zh]) => <a key={id} href={`#${id}`}>{t(en, zh)}</a>)}
            <p>{t("API reference", "API 参考")}</p>
            {endpointGroups.map((group) => <a key={group.id} href={`#reference-${group.id}`}>{localize(group.label)}</a>)}
          </nav>
          <div className="docs-sidebar-links">
            <a href="/openapi.json" download>{t("OpenAPI JSON", "下载 OpenAPI JSON")}</a>
            <Link href="/developers">{t("Platform integrations", "平台适配")}</Link>
          </div>
        </aside>

        <article className="docs-content">
          <section className="docs-intro" id="overview">
            <p className="docs-kicker">TMCRA MEMORY API / v0.3.0-rc2</p>
            <h1>{t("Persistent memory for production AI agents.", "面向生产级 AI Agent 的持久记忆 API。")}</h1>
            <p className="docs-lead">{t(
              "Turn ordered conversation events into tenant-isolated, temporal, prompt-ready evidence. TMCRA owns memory; your application keeps control of the agent runtime and final answer model.",
              "把有序对话事件转化为租户隔离、具备时间结构、可直接供模型使用的证据。TMCRA 负责记忆，你的应用继续掌控 Agent Runtime 和最终回答模型。",
            )}</p>
            <div className="docs-base-url">
              <span>{t("Production base URL", "生产基地址")}</span>
              <code>{API_BASE}</code>
              <a href={`${API_BASE}/openapi.json`} target="_blank" rel="noreferrer">{t("Live schema", "在线 Schema")}</a>
            </div>
            <dl className="docs-contract-strip">
              <div><dt>{t("Protocol", "协议")}</dt><dd>HTTPS / JSON</dd></div>
              <div><dt>{t("Authentication", "鉴权")}</dt><dd>Bearer token</dd></div>
              <div><dt>{t("Write model", "写入模型")}</dt><dd>{t("Asynchronous jobs", "异步任务")}</dd></div>
              <div><dt>{t("Recall output", "召回输出")}</dt><dd>{t("Evidence, not answers", "证据，不代答")}</dd></div>
            </dl>
          </section>

          <section className="docs-section" id="quickstart">
            <div className="docs-section-heading"><span>01</span><div><p>{t("Quickstart", "快速接入")}</p><h2>{t("Write once, then recall with read-your-writes.", "完成一次写入，再以读己之写方式召回。")}</h2></div></div>
            <ol className="docs-steps">
              <li><strong>1</strong><div><h3>{t("Keep credentials on your server", "只在服务端保存凭据")}</h3><p>{t("Use the root key only in trusted infrastructure. Issue scoped tokens for integrations.", "Root Key 只用于可信基础设施；外部集成使用 Scoped Token。")}</p></div></li>
              <li><strong>2</strong><div><h3>{t("Choose a stable scope", "选择稳定的 Scope")}</h3><p>{t("Map a tenant user, persona, or workspace to one durable opaque scope_name.", "把租户用户、Persona 或 Workspace 映射到长期稳定且不透明的 scope_name。")}</p></div></li>
              <li><strong>3</strong><div><h3>{t("Ingest idempotently", "幂等写入")}</h3><p>{t("Send ordered messages with stable message IDs and an Idempotency-Key.", "使用稳定 Message ID 和 Idempotency-Key 提交有序消息。")}</p></div></li>
              <li><strong>4</strong><div><h3>{t("Recall before the model call", "在模型调用前召回")}</h3><p>{t("Pass prompt_evidence.content to the answer model together with recent conversation context.", "把 prompt_evidence.content 与近期对话上下文一起交给回答模型。")}</p></div></li>
            </ol>
            <div className="docs-code-panel">
              <div className="docs-code-toolbar">
                <div role="tablist" aria-label={t("Code example language", "代码示例语言")}>
                  {(["curl", "python", "typescript"] as SnippetName[]).map((name) => (
                    <button key={name} className={snippetName === name ? "is-active" : ""} type="button" role="tab" aria-selected={snippetName === name} onClick={() => setSnippetName(name)}>{name === "typescript" ? "TypeScript" : name === "python" ? "Python" : "cURL"}</button>
                  ))}
                </div>
                <button type="button" onClick={copySnippet}>{copied ? t("Copied", "已复制") : t("Copy", "复制")}</button>
              </div>
              <pre><code>{snippets[snippetName]}</code></pre>
            </div>
          </section>

          <section className="docs-section" id="identity">
            <div className="docs-section-heading"><span>02</span><div><p>{t("Identity and isolation", "身份与隔离")}</p><h2>{t("Tenant, scope, and session are different boundaries.", "Tenant、Scope 与 Session 是三种不同边界。")}</h2></div></div>
            <div className="docs-definition-grid">
              <div><code>tenant</code><p>{t("The billing, credential, and administrative boundary identified by a root API key.", "由 Root API Key 标识的计费、凭据和管理边界。")}</p></div>
              <div><code>scope_name</code><p>{t("The durable memory isolation boundary for one user, persona, or workspace.", "面向一个用户、Persona 或 Workspace 的持久记忆隔离边界。")}</p></div>
              <div><code>session_id</code><p>{t("One ordered conversation inside a scope. It is not inferred from message text.", "Scope 内的一段有序对话，不从消息正文推断。")}</p></div>
            </div>
            <aside className="docs-callout is-warning"><strong>{t("Security rule", "安全规则")}</strong><p>{t("Never ship a root key to a browser, desktop plugin, chat transcript, or untrusted agent. Use an expiring scoped token with the minimum permissions and an explicit scope allowlist.", "不要把 Root Key 放进浏览器、桌面插件、聊天正文或不可信 Agent。应使用具备最小权限、明确 Scope 白名单且会过期的 Scoped Token。")}</p></aside>
          </section>

          <section className="docs-section" id="consistency">
            <div className="docs-section-heading"><span>03</span><div><p>{t("Consistency", "一致性")}</p><h2>{t("Successful messages commit once; later stages are recoverable.", "成功消息只提交一次，后续阶段可以独立恢复。")}</h2></div></div>
            <div className="docs-table-scroll">
              <table className="docs-table"><thead><tr><th>{t("Mode", "模式")}</th><th>{t("Use when", "适用场景")}</th><th>{t("Behavior", "行为")}</th></tr></thead><tbody>
                <tr><td><code>eventual</code></td><td>{t("The next turn does not depend on this write.", "下一轮不依赖本次写入。")}</td><td>{t("Returns an async job immediately.", "立即返回异步 Job。")}</td></tr>
                <tr><td><code>read_your_writes</code></td><td>{t("The next recall must include committed data.", "下一次召回必须包含本次提交。")}</td><td>{t("Wait for the job, then pass wait_for_job_id to recall.", "等待 Job 完成，再把 wait_for_job_id 传给召回。")}</td></tr>
              </tbody></table>
            </div>
            <p>{t("A successful committed message is not replayed because graph evolution, indexing, webhook delivery, or a caller-owned answer operation later fails.", "消息成功提交后，不会因为慢图演化、索引、Webhook 投递或调用方回答阶段随后失败而被重复写入。")}</p>
          </section>

          <section className="docs-section" id="webhook-guide">
            <div className="docs-section-heading"><span>04</span><div><p>Webhooks</p><h2>{t("Signed lifecycle notifications with content-safe payloads.", "带签名、且不暴露记忆正文的生命周期通知。")}</h2></div></div>
            <p>{t("Webhook delivery is at least once. Verify the HMAC-SHA256 signature, deduplicate by event ID, and return a successful response quickly. Payloads contain sanitized job and scope metadata, never prompts, messages, evidence, or credentials.", "Webhook 采用至少一次投递。请验证 HMAC-SHA256 签名、按 Event ID 去重并快速返回成功响应。Payload 只包含脱敏 Job 与 Scope 元数据，不包含 Prompt、消息、证据或凭据。")}</p>
            <div className="docs-event-list"><code>job.succeeded</code><code>job.failed</code><code>job.cancelled</code><code>ingest.completed</code><code>consolidation.completed</code><code>index.completed</code><code>export.ready</code><code>scope.deleted</code></div>
          </section>

          <section className="docs-section" id="errors">
            <div className="docs-section-heading"><span>05</span><div><p>{t("Errors and retries", "错误与重试")}</p><h2>{t("Retry by operation semantics, not by status alone.", "根据操作语义重试，而不是只看状态码。")}</h2></div></div>
            <div className="docs-table-scroll">
              <table className="docs-table"><thead><tr><th>{t("Status", "状态码")}</th><th>{t("Caller action", "调用方处理")}</th></tr></thead><tbody>
                {errorRows.map((row) => <tr key={row.status}><td><code>{row.status}</code></td><td>{localize(row.action)}</td></tr>)}
              </tbody></table>
            </div>
            <aside className="docs-callout"><strong>{t("Replay safety", "重放安全")}</strong><p>{t("Do not automatically retry recall, feedback, cancellation, or an unkeyed mutation. Safe automatic retries are limited to GETs and writes protected by a stable idempotency key.", "不要自动重试召回、反馈、取消或没有幂等键的修改操作。自动安全重试只适用于 GET 和受稳定幂等键保护的写入。")}</p></aside>
          </section>

          <section className="docs-section" id="sdks">
            <div className="docs-section-heading"><span>06</span><div><p>{t("SDKs and adapters", "SDK 与适配器")}</p><h2>{t("Use the stable contract directly; evaluate packages by release status.", "直接使用稳定 API 合同，并按发布状态评估各接入包。")}</h2></div></div>
            <aside className="docs-callout"><strong>{t("Availability labels", "可用性标识")}</strong><p>{t("Stable is the public HTTP contract. Preview packages are testable but may still change. Pilot source adapters are supplied only through approved pilots and are not generally published packages.", "Stable 表示公开 HTTP 合同；Preview 表示可测试但仍可能调整的接入包；Pilot source 表示仅向获批试用提供的源码适配器，并非已普遍发布的软件包。")}</p></aside>
            <div className="docs-integration-table">
              <div><strong>Python SDK</strong><code>PREVIEW · tmcra-client</code><p>{t("Typed sync and async clients available for pilot evaluation.", "供试用评估的类型化同步与异步客户端。")}</p></div>
              <div><strong>TypeScript SDK</strong><code>PREVIEW · @tmcra/typescript</code><p>{t("A testable fetch client whose package surface may still change.", "可测试的 Fetch 客户端，软件包接口仍可能调整。")}</p></div>
              <div><strong>MCP Server</strong><code>PREVIEW · stdio</code><p>{t("A local compatibility bridge for approved test deployments.", "面向获批测试部署的本地兼容层。")}</p></div>
              <div><strong>LangGraph</strong><code>PILOT SOURCE · TMCRALangGraphMemory</code><p>{t("Validated source adapter; no generally published package is promised.", "已验证的源码适配器，不代表已有公开发行包。")}</p></div>
              <div><strong>OpenAI Agents SDK</strong><code>PILOT SOURCE · TMCRAAgentsMemory</code><p>{t("Validated source adapter available through approved pilots.", "已验证的源码适配器，仅通过获批试用提供。")}</p></div>
              <div><strong>Vercel AI SDK</strong><code>PILOT SOURCE · LanguageModelV3Middleware</code><p>{t("Validated source middleware available through approved pilots.", "已验证的源码中间件，仅通过获批试用提供。")}</p></div>
              <div><strong>Microsoft Agent Framework</strong><code>PILOT SOURCE · TmcraAIContextProvider</code><p>{t("Validated source provider available through approved pilots.", "已验证的源码 Provider，仅通过获批试用提供。")}</p></div>
            </div>
            <Link className="docs-inline-link" href="/developers">{t("Open platform integration guide", "查看平台适配指南")} <span aria-hidden="true">-&gt;</span></Link>
          </section>

          <section className="docs-reference" id="reference">
            <div className="docs-section-heading"><span>07</span><div><p>{t("API reference", "API 参考")}</p><h2>{t(`${endpoints.length} documented API operations.`, `${endpoints.length} 个已记录的 API 操作。`)}</h2></div></div>
            {normalizedQuery && <p className="docs-filter-summary">{t(`${filteredEndpoints.length} matching endpoints`, `找到 ${filteredEndpoints.length} 个匹配端点`)}</p>}
            {endpointGroups.map((group) => {
              const groupEndpoints = filteredEndpoints.filter((endpoint) => endpoint.group === group.id);
              if (groupEndpoints.length === 0) return null;
              return (
                <section className="docs-endpoint-group" id={`reference-${group.id}`} key={group.id}>
                  <div className="docs-endpoint-group-heading"><h3>{localize(group.label)}</h3><p>{localize(group.description)}</p><span>{String(groupEndpoints.length).padStart(2, "0")}</span></div>
                  <div className="docs-endpoint-list">
                    {groupEndpoints.map((endpoint) => (
                      <details key={`${endpoint.method}-${endpoint.path}`}>
                        <summary>
                          <span className={methodClass(endpoint.method)}>{endpoint.method}</span>
                          <code>{endpoint.path}</code>
                          <strong>{localize(endpoint.title)}</strong>
                          <i aria-hidden="true">+</i>
                        </summary>
                        <div className="docs-endpoint-detail">
                          <p>{localize(endpoint.description)}</p>
                          {endpoint.note && <aside>{localize(endpoint.note)}</aside>}
                          <a href="/openapi.json" target="_blank" rel="noreferrer">{t("Inspect OpenAPI schema", "查看 OpenAPI Schema")}</a>
                        </div>
                      </details>
                    ))}
                  </div>
                </section>
              );
            })}
            {filteredEndpoints.length === 0 && <div className="docs-empty"><strong>{t("No matching endpoint", "没有匹配的端点")}</strong><p>{t("Try a method, path, or concept such as recall, graph, export, or token.", "可以搜索方法、路径或 recall、graph、export、token 等概念。")}</p></div>}
          </section>
        </article>

        <aside className="docs-rail">
          <div><span>{t("Contract", "合同状态")}</span><strong>{t("Release candidate", "候选发布版")}</strong></div>
          <div><span>{t("Availability", "可用性")}</span><strong>{t("Private pilot", "内部试用")}</strong></div>
          <div><span>{t("Answer model", "回答模型")}</span><strong>{t("Caller-owned", "调用方持有")}</strong></div>
          <Link href="/access">{t("Request API access", "申请 API 访问")}</Link>
        </aside>
      </div>
    </main>
  );
}
