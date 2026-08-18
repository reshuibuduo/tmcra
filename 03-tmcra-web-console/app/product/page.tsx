"use client";

import { MarketingFooter, MarketingHeader } from "../MarketingShell";
import { useLanguage } from "../i18n";
import "../knowledge-pages.css";

const productStages = [
  ["01", "CAPTURE", "采集", "Record the completed user and Agent turn as separate, attributable messages.", "把本轮用户消息与 Agent 回答分别记录，并保留各自来源。"],
  ["02", "SCOPE", "归入范围", "Keep stable user facts in Global memory and project work inside its own Project Scope.", "稳定的用户资料进入 Global；项目内容只进入对应的 Project Scope。"],
  ["03", "RECALL", "按问题召回", "Use the current prompt to select relevant evidence from the allowed scopes.", "根据这一轮的新问题，从获准范围中找回相关证据。"],
  ["04", "CONTINUE", "接续工作", "Deliver bounded Prompt Evidence so the Agent resumes from the right project state.", "把有边界的 Prompt Evidence 交给 Agent，让工作从正确位置继续。"],
] as const;

export default function ProductPage() {
  const { t } = useLanguage();

  return (
    <main className="knowledge-page">
      <MarketingHeader />

      <section className="knowledge-hero section-shell">
        <div>
          <p className="knowledge-kicker">PRODUCT / CONTINUITY</p>
          <h1>{t("A memory layer for work that spans conversations.", "让跨越多次对话的工作拥有连续记忆。")}</h1>
          <p>{t(
            "TMCRA gives Agent systems a controlled place to retain project state, user requirements, prior progress, and source evidence—then recover only what matters to the current question.",
            "TMCRA 为 Agent 系统保存项目状态、用户要求、既有进度和来源证据，并在下一轮只找回与当前问题有关的内容。",
          )}</p>
          <div className="knowledge-actions">
            <a className="button button-primary" href="/access">{t("Request a pilot", "申请试用")} <span aria-hidden="true">→</span></a>
            <a className="button button-secondary" href="/download">{t("Desktop release status", "桌面端发布状态")} <span aria-hidden="true">→</span></a>
          </div>
        </div>
        <aside className="knowledge-instrument">
          <span>PRODUCT CONTRACT</span>
          <dl>
            <div><dt>{t("Primary value", "首要价值")}</dt><dd>{t("Continue work", "继续工作")}</dd></div>
            <div><dt>{t("Boundary", "记忆边界")}</dt><dd>Global + Project</dd></div>
            <div><dt>{t("Attribution", "主体来源")}</dt><dd>USER / AGENT</dd></div>
            <div><dt>{t("Delivery", "交付形式")}</dt><dd>Prompt Evidence</dd></div>
          </dl>
        </aside>
      </section>

      <section className="knowledge-section section-shell">
        <div className="knowledge-heading">
          <p>01 / {t("WORKFLOW", "工作流程")}</p>
          <h2>{t("One continuity thread, from a completed turn to the next action.", "一条接续主线，从本轮结束贯穿到下一步行动。")}</h2>
        </div>
        <ol className="knowledge-stage-grid">
          {productStages.map(([number, enLabel, zhLabel, enCopy, zhCopy]) => (
            <li key={number}>
              <span>{number}</span>
              <small>{t(enLabel, zhLabel)}</small>
              <h3>{t(enCopy, zhCopy)}</h3>
            </li>
          ))}
        </ol>
      </section>

      <section className="knowledge-section section-shell">
        <div className="knowledge-heading split">
          <div><p>02 / {t("CONTINUITY GAPS", "接续断点")}</p><h2>{t("A stored history still needs a way back into the work.", "保存了历史，还要让历史准确回到当前工作。")}</h2></div>
          <p>{t("Long-running Agent work breaks in recognizable ways. TMCRA treats these as product constraints, not decorative use cases.", "长期运行的 Agent 工作会在几个明确位置断裂。TMCRA 将这些问题作为产品约束来处理。")}</p>
        </div>
        <div className="continuity-gap-grid">
          <article><span>01 / SESSION-BOUND</span><h3>{t("A new chat loses the working position.", "新对话丢失上次做到的位置。")}</h3><p>{t("The prior conversation may still exist, but the next Agent does not receive the relevant decisions, requirements, and unfinished work automatically.", "历史对话可能还在，下一位 Agent 却无法自动获得相关决策、要求和未完成事项。")}</p></article>
          <article><span>02 / FLAT RETRIEVAL</span><h3>{t("Similar text does not resolve changing state.", "相似文本无法解释状态变化。")}</h3><p>{t("A useful memory layer must preserve who said something, when it changed, where it belongs, and which source supports it.", "可用的记忆层需要保留说话者、变化时间、所属范围，以及支撑它的来源。")}</p></article>
          <article><span>03 / HISTORY ONLY</span><h3>{t("A log is not yet prompt-ready evidence.", "历史记录还不是可直接使用的 Prompt Evidence。")}</h3><p>{t("The current task needs a bounded selection that separates user requirements from Agent progress and carries both into the next action.", "当前任务需要一份有边界的证据选择，区分用户要求与 Agent 进度，再共同交给下一步。")}</p></article>
        </div>
      </section>

      <section className="knowledge-section section-shell">
        <div className="knowledge-heading split">
          <div><p>03 / {t("PRODUCT SURFACES", "产品界面")}</p><h2>{t("Use memory without operating a memory server.", "使用长期记忆，无需自行维护记忆服务器。")}</h2></div>
          <p>{t("The same memory model appears at different densities across the desktop app, web console, API, and native adapters.", "同一套记忆模型以不同信息密度呈现在桌面应用、网页控制台、API 与原生适配器中。")}</p>
        </div>
        <div className="knowledge-surface-grid">
          <article><span>DESKTOP</span><h3>{t("Continue and manage", "继续工作与管理")}</h3><p>{t("See the current project, prior progress, connections, memory, imports, usage, and account state in one application.", "在一个应用中查看当前项目、既有进度、连接状态、记忆、历史导入、用量与账户状态。")}</p><a href="/download">{t("View release status", "查看发布状态")} →</a></article>
          <article><span>CONSOLE</span><h3>{t("Inspect and govern", "检查与治理")}</h3><p>{t("Inspect scopes, sessions, evidence, recall results, API Keys, and server-reported quota.", "检查 Scope、Session、证据、召回结果、API Key 和服务端额度。")}</p><a href="/console">{t("Open console", "打开控制台")} →</a></article>
          <article><span>API + SDK</span><h3>{t("Build into your runtime", "接入现有 Runtime")}</h3><p>{t("Use the stable HTTP contract or preview lifecycle clients without replacing your model or Agent runtime.", "通过稳定 HTTP 合同或预览版生命周期客户端接入，无需替换现有模型或 Agent Runtime。")}</p><a href="/developers">{t("View integrations", "查看接入方式")} →</a></article>
          <article><span>VISUALIZER</span><h3>{t("Trace the evidence", "追踪召回证据")}</h3><p>{t("See where a memory came from, which scope it belongs to, and how the returned evidence was composed.", "查看记忆来自哪里、属于哪个范围，以及本次返回的证据如何组成。")}</p><a href="/architecture">{t("View architecture", "查看架构")} →</a></article>
        </div>
      </section>

      <section className="knowledge-section section-shell">
        <div className="knowledge-heading split">
          <div><p>04 / {t("MEMORY APPROACHES", "记忆方式对照")}</p><h2>{t("Choose the memory boundary before choosing the interface.", "先确定记忆边界，再选择接入界面。")}</h2></div>
          <p>{t("The comparison focuses on continuity, changing state, isolation, and provenance—the parts that decide whether earlier work can safely affect the next answer.", "这里比较接续、状态变化、隔离和来源；这些能力决定历史工作能否安全地影响下一次回答。")}</p>
        </div>
        <div className="product-comparison" role="table" aria-label={t("Memory approach comparison", "记忆方式对照")}>
          <div className="comparison-head" role="row"><span role="columnheader">{t("Approach", "方式")}</span><span role="columnheader">{t("Across conversations", "跨对话")}</span><span role="columnheader">{t("Changing state", "状态变化")}</span><span role="columnheader">{t("Boundary and source", "边界与来源")}</span></div>
          <div role="row"><strong role="cell">Context Window</strong><span role="cell">{t("Current conversation", "当前对话")}</span><span role="cell">{t("Prompt order only", "仅依赖 Prompt 顺序")}</span><span role="cell">{t("Conversation boundary", "对话边界")}</span></div>
          <div role="row"><strong role="cell">Vector RAG</strong><span role="cell">{t("Retrieves stored chunks", "召回已存片段")}</span><span role="cell">{t("Similarity-led", "以相似度为主")}</span><span role="cell">{t("Depends on index design", "取决于索引设计")}</span></div>
          <div className="is-tmcra" role="row"><strong role="cell">TMCRA</strong><span role="cell">Global + Project</span><span role="cell">Source + Fast + Slow</span><span role="cell">Actor · Session · Time · Source</span></div>
        </div>
      </section>

      <section className="knowledge-section section-shell">
        <div className="knowledge-heading"><p>05 / {t("WHERE IT FITS", "适用场景")}</p><h2>{t("Four systems that depend on remembered work.", "四类依赖长期工作记忆的系统。")}</h2></div>
        <div className="use-case-grid">
          <article><span>PERSONAL AI</span><h3>{t("Preferences, commitments, and ongoing projects", "偏好、承诺与持续项目")}</h3><p>{t("Carry stable user information across tools while keeping every project in its own scope.", "让稳定用户资料跨工具发挥作用，同时保持不同项目彼此隔离。")}</p></article>
          <article><span>AUTONOMOUS AGENTS</span><h3>{t("Plans, actions, and unresolved work", "计划、行动与未完成事项")}</h3><p>{t("Specialized Agents can share project state while preserving their own actor and session provenance.", "不同分工的 Agent 可以共享项目状态，并保留各自的主体与 Session 来源。")}</p></article>
          <article><span>ENTERPRISE ASSISTANTS</span><h3>{t("Governed memory for repeated workflows", "面向重复工作流的可治理记忆")}</h3><p>{t("Use controlled scopes, attributable evidence, deletion, export, usage, and audit surfaces.", "通过受控 Scope、可追溯证据、删除、导出、用量和审计界面进行治理。")}</p></article>
          <article><span>EMBODIED AI</span><h3>{t("Experience that survives task boundaries", "跨越任务边界的经验")}</h3><p>{t("Preserve events, corrections, and operational context as a system moves between sessions and tasks.", "系统跨 Session 与任务运行时，持续保留事件、修正和操作上下文。")}</p></article>
        </div>
      </section>

      <section className="knowledge-callout">
        <div className="section-shell">
          <div><p>06 / {t("BOUNDARIES", "边界")}</p><h2>{t("Ten projects do not become one graph.", "十个项目不会被混进一张图。")}</h2><p>{t("Global memory carries stable user information. Every project keeps its own scope, and every conversation remains a session inside that project.", "Global 只承载稳定的用户资料；每个项目拥有独立 Scope，每段对话作为项目内部的 Session 保存。")}</p></div>
          <a className="button button-primary" href="/architecture">{t("Read the architecture", "了解完整架构")} →</a>
        </div>
      </section>

      <MarketingFooter />
    </main>
  );
}
