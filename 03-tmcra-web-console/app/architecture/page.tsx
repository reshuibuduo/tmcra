"use client";

import { MarketingFooter, MarketingHeader } from "../MarketingShell";
import { useLanguage } from "../i18n";
import "../knowledge-pages.css";

export default function ArchitecturePage() {
  const { t } = useLanguage();

  return (
    <main className="knowledge-page">
      <MarketingHeader />
      <section className="knowledge-hero section-shell">
        <div>
          <p className="knowledge-kicker">ARCHITECTURE / MEMORY EVIDENCE</p>
          <h1>{t("Every recalled fact keeps its boundary and source.", "每一条被召回的信息，都保留边界与来源。")}</h1>
          <p>{t(
            "TMCRA resolves two recall scopes in parallel, composes Source, Fast, and Slow memory paths, preserves actor provenance, and returns bounded Prompt Evidence to the Agent.",
            "TMCRA 并行处理两个召回范围，组织 Source、Fast 与 Slow 三条记忆路径，保留说话者来源，最终向 Agent 返回有边界的 Prompt Evidence。",
          )}</p>
        </div>
        <aside className="knowledge-instrument">
          <span>RECALL CONTRACT</span>
          <ol className="instrument-flow"><li>Current prompt</li><li>Global + Project</li><li>Source / Fast / Slow</li><li>Actor composition</li><li>Prompt Evidence</li></ol>
        </aside>
      </section>

      <section className="knowledge-section section-shell">
        <div className="knowledge-heading split">
          <div><p>01 / {t("SCOPE MODEL", "范围模型")}</p><h2>{t("Cross-session memory without cross-project leakage.", "同一项目可以跨会话，项目之间保持隔离。")}</h2></div>
          <p>{t("Session is a stable grouping and provenance identifier inside a Project Scope. It is not a third recall scope beside Global and Project.", "Session 是 Project Scope 内部的稳定分组和来源标识，不是与 Global、Project 平级的第三个召回范围。")}</p>
        </div>
        <div className="scope-blueprint" aria-label={t("TMCRA scope containment", "TMCRA 记忆范围包含关系")}>
          <div className="scope-account"><span>ACCOUNT</span><strong>{t("One user boundary", "单一用户边界")}</strong></div>
          <div className="scope-branch is-global"><span>USER GLOBAL</span><strong>{t("Stable facts and preferences", "稳定资料与偏好")}</strong></div>
          <div className="scope-branch is-project"><span>PROJECT SCOPE</span><strong>{t("Project state and shared Agent work", "项目状态与 Agent 协作进度")}</strong><div className="scope-session-row"><i>SESSION 17</i><i>SESSION 18</i><i>SESSION 19</i></div></div>
        </div>
      </section>

      <section className="knowledge-section section-shell">
        <div className="knowledge-heading"><p>02 / {t("LAYERED RETRIEVAL", "分层检索")}</p><h2>{t("Three memory paths, one evidence contract.", "三条记忆路径，共同形成一份证据合同。")}</h2></div>
        <div className="architecture-layer-grid">
          <article><span>01</span><small>SOURCE</small><h3>{t("Original evidence", "原始证据")}</h3><p>{t("Verbatim or source-bound records retain the strongest trace back to the completed turn.", "保留逐字内容或明确来源的记录，能够回查到完整对话轮次。")}</p></article>
          <article><span>02</span><small>FAST</small><h3>{t("Recent working state", "近期工作状态")}</h3><p>{t("Fresh events and progress become available quickly for the next relevant question.", "近期事件与工作进度可以尽快进入下一轮相关召回。")}</p></article>
          <article><span>03</span><small>SLOW</small><h3>{t("Longer-term structure", "长期结构")}</h3><p>{t("Background organization resolves subjects, changes, contradictions, and relationships over time.", "后台整理持续处理主体、变化、冲突与长期关系。")}</p></article>
          <article className="is-result"><span>04</span><small>EVIDENCE</small><h3>Prompt Evidence</h3><p>{t("Role composition and bounded packing preserve the selected memory IDs, actors, scopes, sessions, time, and source links.", "角色编排与有界打包会保留选中记忆的 ID、主体、Scope、Session、时间和来源链接。")}</p></article>
        </div>
      </section>

      <section className="knowledge-section section-shell">
        <div className="knowledge-heading split">
          <div><p>03 / {t("ACTOR PROVENANCE", "对话主体")}</p><h2>{t("User requirements and Agent progress remain different records.", "用户要求与 Agent 进度始终是不同记录。")}</h2></div>
          <p>{t("Both can be recalled because both matter to continuity. Their role labels, colors, source fields, and display lanes remain distinct.", "两类信息都会参与召回，因为继续工作同时需要用户要求与既有进度；但它们的角色标签、颜色、来源字段和展示位置始终分离。")}</p>
        </div>
        <div className="actor-contract">
          <article className="is-user"><span>USER · SOURCE</span><h3>{t("Requirements and facts", "用户要求与事实")}</h3><p>{t("“Keep every small section fully designed.”", "“每一个小板块都要详细设计。”")}</p></article>
          <div className="actor-composition"><span>{t("COMPOSE, DO NOT MERGE", "组合，但不混为同一主体")}</span></div>
          <article className="is-agent"><span>AGENT · FAST</span><h3>{t("Progress and results", "工作进度与结果")}</h3><p>{t("“The design architecture is complete; homepage implementation is in progress.”", "“设计总架构已完成，首页正在实施。”")}</p></article>
        </div>
      </section>

      <section className="knowledge-callout">
        <div className="section-shell"><div><p>04 / {t("TURN COMPLETION", "轮次结束")}</p><h2>{t("Recall before the answer. Write after the answer.", "回答前召回，回答后写入。")}</h2><p>{t("When the completed answer is available, TMCRA persists the user and assistant messages as two separate records for future turns.", "回答完成后，TMCRA 将用户消息与助手回答作为两条独立记录写入，供后续对话继续使用。")}</p></div><a className="button button-primary" href="/developers/automatic-memory">{t("View lifecycle integration", "查看生命周期接入")} →</a></div>
      </section>
      <MarketingFooter />
    </main>
  );
}
