"use client";

import { useEffect } from "react";
import Link from "next/link";
import { MarketingFooter, MarketingHeader, TMCRA_GITHUB } from "../MarketingShell";
import { type LocalizedText, useLanguage } from "../i18n";

type Integration = {
  name: string;
  kind: LocalizedText;
  availability: LocalizedText;
  description: LocalizedText;
  detail: string;
  href?: string;
};

const integrations: Integration[] = [
  { name: "REST / OpenAPI", kind: { en: "CORE INTERFACE", zh: "通用接口" }, availability: { en: "STABLE", zh: "稳定" }, description: { en: "Use the public, versioned HTTP contract from any trusted backend stack.", zh: "从任意可信后端技术栈接入公开、版本化的 HTTP 合同。" }, detail: "HTTPS · JSON · api.tmcra.com", href: "/docs" },
  { name: "Python SDK", kind: { en: "CLIENT + LIFECYCLE", zh: "客户端 + 生命周期" }, availability: { en: "PREVIEW", zh: "预览" }, description: { en: "Typed sync and async clients with an optional recall → answer → write lifecycle wrapper for your own Agent runtime.", zh: "类型完善的同步与异步客户端，并提供可选的“召回 → 回答 → 写入”生命周期封装，接入你现有的 Agent Runtime。" }, detail: "SyncMemoryLifecycle · AsyncMemoryLifecycle", href: "/developers/automatic-memory#python" },
  { name: "JavaScript / TypeScript SDK", kind: { en: "CLIENT + LIFECYCLE", zh: "客户端 + 生命周期" }, availability: { en: "PREVIEW", zh: "预览" }, description: { en: "One compiled package for JavaScript and TypeScript, with an optional automatic turn wrapper and multi-Agent attribution.", zh: "同一个编译包同时支持 JavaScript 与 TypeScript，并提供可选的自动轮次封装和多 Agent 来源标记。" }, detail: "TMCRAMemoryLifecycle · @tmcra/typescript", href: "/developers/automatic-memory#javascript-typescript" },
  { name: "MCP Server", kind: { en: "COMPATIBILITY LAYER", zh: "兼容层" }, availability: { en: "PREVIEW", zh: "预览" }, description: { en: "Explicit recall and ingest tools over local stdio. Automatic turns require lifecycle support from the host, such as the Codex Hooks mode.", zh: "通过本地 stdio 提供显式召回与写入工具。要实现自动轮次，宿主还需提供生命周期能力，例如 Codex Hooks 模式。" }, detail: "explicit MCP · optional Codex Hooks", href: "/developers/automatic-memory#mcp" },
  { name: "Codex", kind: { en: "NATIVE ADAPTER", zh: "原生适配" }, availability: { en: "PREVIEW", zh: "预览" }, description: { en: "A downloadable preview that recalls before Codex answers, records completed main and subagent turns, isolates projects, and can import retained Codex history.", zh: "可下载的预览版：在 Codex 回答前召回，记录主任务与子智能体的完整轮次，隔离不同项目，并可导入仍保留的 Codex 历史。" }, detail: "SessionStart · SubagentStart · UserPromptSubmit · Stop · SubagentStop", href: "/developers/codex" },
  { name: "DeepSeek Harness", kind: { en: "NATIVE ADAPTER", zh: "原生适配" }, availability: { en: "PREVIEW", zh: "预览" }, description: { en: "Install the Harness package, sign in to TMCRA through device authorization, then recall before each answer and write completed user and Agent turns back with project and multi-Agent provenance.", zh: "安装 Harness 插件后，通过设备授权登录 TMCRA。此后每轮会在回答前自动召回，并把完成的用户消息与 Agent 回答分开写回，同时保留项目和多 Agent 来源。" }, detail: "account login · automatic recall · role-separated writeback", href: "https://github.com/reshuibuduo/tmcra-deepseek-harness-memory" },
  { name: "OpenClaw", kind: { en: "NATIVE LIFECYCLE", zh: "原生生命周期" }, availability: { en: "PILOT", zh: "试用" }, description: { en: "Native Hooks automatically recall on the current prompt and capture the completed turn. Specialized Agents share project memory without losing Agent or session attribution.", zh: "原生 Hook 会按当前问题自动召回，并在回答完成后采集整轮对话。不同分工的 Agent 共享项目记忆，同时保留 Agent 与 Session 来源。" }, detail: "before_prompt_build · agent_end", href: "/developers/automatic-memory#openclaw" },
  { name: "Hermes Agent", kind: { en: "MEMORY PROVIDER", zh: "记忆 Provider" }, availability: { en: "PILOT", zh: "试用" }, description: { en: "The native MemoryProvider recalls before an answer, queues completed turns, and records delegated Agent work without mislabeling it as user speech.", zh: "原生 MemoryProvider 在回答前召回、在回答后排队写入，并记录委派给子 Agent 的工作，不会把它误标成用户发言。" }, detail: "prefetch · sync_turn · on_delegation", href: "/developers/automatic-memory#hermes" },
];

const flow: Array<[string, LocalizedText, LocalizedText]> = [
  ["01", { en: "Keep credentials out of prompts", zh: "不要让凭据进入对话" }, { en: "Store the API Key in a protected local or server-side environment. Never place it in a prompt, browser bundle or project source file.", zh: "API Key 应保存在受保护的本地配置或服务端环境中，不能写进 Prompt、浏览器代码或项目源码。" }],
  ["02", { en: "Define stable memory scopes", zh: "建立稳定的记忆边界" }, { en: "Separate user-global memory from project memory, then retain each conversation as a session inside its project.", zh: "把用户全局资料与项目记忆分开；每段对话作为项目内的独立 Session 保存，既能跨会话，也不会串项目。" }],
  ["03", { en: "Recall from the current question", zh: "根据当前问题召回" }, { en: "After the user submits a prompt and before the model answers, recall allowed scopes and inject bounded prompt_evidence.content.", zh: "用户提交新问题后、模型回答前，从获准的 Scope 召回内容，并注入有界的 prompt_evidence.content。" }],
  ["04", { en: "Write the completed turn idempotently", zh: "在回答完成后幂等写入" }, { en: "Persist user and assistant messages separately in shared project memory, preserving role, Agent, session, and source provenance.", zh: "把用户消息与助手回复分别写入项目共享记忆，同时保留 role、Agent、Session 与来源信息。" }],
];

export default function DevelopersPage() {
  const { t, localize } = useLanguage();

  useEffect(() => {
    document.title = t("TMCRA Developer Integrations", "TMCRA 开发者接入");
  }, [t]);

  return (
    <main className="marketing-page">
      <MarketingHeader />

      <section className="branch-hero section-shell">
        <div>
          <p className="eyebrow"><span /> {t("Developer integrations / 01", "开发者接入 / 01")}</p>
          <h1>{t("Connect TMCRA to the agent stack you already run.", "把 TMCRA 接入你正在运行的 Agent 技术栈。")}</h1>
          <p>{t("Use the stable HTTP contract directly, wrap your model call with a preview SDK lifecycle, or connect a native host adapter. The labels below describe release availability, not merely whether source code exists.", "你可以直接调用稳定的 HTTP 合同，也可以用预览版 SDK 包裹现有模型调用，或接入宿主平台的原生适配器。下方标识描述的是发布可用性，而不只是仓库中是否存在源码。")}</p>
          <div className="hero-actions">
            <a className="button button-primary" href="/access">{t("Request integration access", "申请接入试用")} <span aria-hidden="true">→</span></a>
            <a className="button button-secondary" href={TMCRA_GITHUB} target="_blank" rel="noreferrer">GitHub <span aria-hidden="true">↗</span></a>
          </div>
        </div>
        <aside className="branch-hero-aside">
          <span>{t("RELEASE STATUS", "发布状态")}</span>
          <strong>1·5·2</strong>
          <p>{t("1 stable contract, 5 preview integrations, and 2 pilot-only native adapters.", "1 项稳定合同、5 项预览接入，以及 2 项仅限试用的原生适配。")}</p>
        </aside>
      </section>

      <section className="integration-section section-shell section-block">
        <div className="section-heading split-heading">
          <div><p className="section-index">{t("02 / INTEGRATION SURFACES", "02 / 接入方式")}</p><h2>{t("Start at the layer that matches your system.", "从最适合现有系统的一层开始接入。")}</h2></div>
          <p>{t("Stable means the public contract is supported. Preview means the integration is testable but still evolving. Pilot means access is reviewed and the native adapter is not yet a generally published package.", "Stable 表示公开合同受支持；Preview 表示接入已经可以测试但仍在演进；Pilot 表示需要审核接入，原生适配器尚未作为通用软件包公开发布。")}</p>
        </div>
        <div className="integration-grid">
          {integrations.map((integration, index) => (
            <article key={integration.name}>
              <header><span>{String(index + 1).padStart(2, "0")}</span><em>{localize(integration.kind)} / {localize(integration.availability)}</em></header>
              <h3>{integration.name}</h3>
              <p>{localize(integration.description)}</p>
              <code>{integration.detail}</code>
              {integration.href && <Link href={integration.href}>{t("Open integration guide", "查看接入指南")} <span aria-hidden="true">→</span></Link>}
            </article>
          ))}
        </div>
      </section>

      <section className="integration-flow-section section-shell section-block">
        <div className="section-heading"><p className="section-index">{t("03 / PRODUCTION FLOW", "03 / 生产接入流程")}</p><h2>{t("The question comes before recall; the answer comes before write.", "先有问题，再召回；先完成回答，再写入。")}</h2></div>
        <ol className="integration-flow">
          {flow.map(([number, title, copy]) => <li key={number}><span>{number}</span><div><h3>{localize(title)}</h3><p>{localize(copy)}</p></div></li>)}
        </ol>
      </section>

      <section className="integration-contract section-shell section-block">
        <div>
          <p className="section-index">{t("04 / AUTOMATIC LIFECYCLE", "04 / 自动生命周期")}</p>
          <h2>{t("One project can contain many Agents.", "一个项目可以由多个 Agent 协作。")}</h2>
          <p>{t("Use a shared project Scope across specialized Agents, distinct Sessions for their conversations, and explicit actor provenance on every record. The optional Agent-private layer is recall-only and off by default.", "不同分工的 Agent 应共享同一个项目 Scope，各自的对话使用不同 Session，每条记录都保留明确的主体来源。可选的 Agent 私有层只参与召回，并且默认关闭。")}</p>
          <Link className="button button-secondary" href="/developers/automatic-memory">{t("Read the automatic memory and multi-Agent guide", "查看自动记忆与多 Agent 接入指南")} <span aria-hidden="true">→</span></Link>
        </div>
        <div className="contract-code" aria-label={t("Automatic lifecycle order", "自动生命周期顺序")}>
          <span>turn.lifecycle</span>
          <pre><code>{`user.prompt
  → recall + inject
  → agent.answer
  → write(user, assistant)`}</code></pre>
        </div>
      </section>

      <section className="integration-contract section-shell section-block">
        <div>
          <p className="section-index">{t("05 / DELIVERY CONTRACT", "05 / 交付契约")}</p>
          <h2>{t("Your model stays yours.", "继续使用你现有的模型。")}</h2>
          <p>{t("TMCRA does not replace the model or Agent runtime. It supplies traceable evidence and a deterministic prompt context at the point where your system needs memory.", "TMCRA 不替换模型，也不接管 Agent Runtime。它只在系统需要记忆的位置，提供可追溯的证据和稳定的 Prompt 上下文。")}</p>
        </div>
        <div className="contract-code" aria-label={t("Recall response contract", "召回响应契约")}>
          <span>recall.response</span>
          <pre><code>{`{
  "evidence": [...],
  "prompt_evidence": {
    "content": "..."
  }
}`}</code></pre>
        </div>
      </section>

      <section className="branch-cta"><div className="section-shell"><div><p className="section-index">{t("06 / PILOT", "06 / 试用")}</p><h2>{t("Tell us what you need to connect.", "告诉我们你的接入场景。")}</h2><p>{t("Submit your stack, use case and rollout timeline. The TMCRA team will review the application in the Internal control plane.", "提交技术栈、使用场景和上线计划。TMCRA 团队会在内部管理后台完成评估与跟进。")}</p></div><a className="button button-primary" href="/access">{t("Open the application", "填写试用申请")} <span aria-hidden="true">→</span></a></div></section>
      <MarketingFooter />
    </main>
  );
}
