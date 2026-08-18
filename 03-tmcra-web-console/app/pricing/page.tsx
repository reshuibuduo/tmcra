"use client";

import { MarketingFooter, MarketingHeader } from "../MarketingShell";
import { useLanguage } from "../i18n";

export default function PricingPage() {
  const { t } = useLanguage();

  return (
    <main className="public-page pricing-page">
      <MarketingHeader />

      <section className="public-hero section-shell compact-public-hero">
        <div>
          <p className="eyebrow"><span /> {t("EARLY ACCESS / PRICING", "早期开放 / 定价")}</p>
          <h1>{t("Prove the memory workflow before paying for scale.", "先验证记忆流程，再讨论规模化付费。")}</h1>
          <p className="public-lede">{t(
            "TMCRA is opening in controlled batches while production capacity, support, and billing controls are validated. The current preview is usage-limited and free; no payment method is collected.",
            "TMCRA 目前按批次开放，用于验证生产容量、支持流程和计费控制。当前试用版免费但有用量限制，也不会要求绑定支付方式。",
          )}</p>
          <div className="hero-actions">
            <a className="button button-primary" href="/access">{t("Request preview access", "申请试用")} <span aria-hidden="true">→</span></a>
            <a className="button button-secondary" href="/docs">{t("Review the API contract", "查看 API 合同")} <span aria-hidden="true">→</span></a>
          </div>
        </div>
        <aside className="branch-hero-aside">
          <span>{t("CURRENT COMMERCIAL STATE", "当前商业状态")}</span>
          <strong>{t("Preview", "试用")}</strong>
          <p>{t("Free · usage-limited · reviewed onboarding · no automatic billing", "免费 · 限量 · 审核接入 · 不自动扣费")}</p>
        </aside>
      </section>

      <section className="public-band">
        <div className="section-shell pricing-grid">
          <article>
            <p>{t("Individual preview", "个人试用")}</p>
            <h2>{t("Usage-limited free", "限量免费")}</h2>
            <ul>
              <li>{t("Production memory API access after approval", "审核通过后使用生产记忆 API")}</li>
              <li>{t("Desktop app and published preview integrations", "桌面应用与已发布的预览接入")}</li>
              <li>{t("Server-reported usage and remaining quota", "查看服务端返回的用量与剩余额度")}</li>
              <li>{t("No card and no automatic conversion to paid use", "无需绑卡，也不会自动转为付费")}</li>
            </ul>
            <a className="button button-primary" href="/access">{t("Apply for preview", "申请个人试用")}</a>
          </article>
          <article>
            <p>{t("Team pilot", "团队试点")}</p>
            <h2>{t("Commercial terms by review", "按场景评估商业条款")}</h2>
            <ul>
              <li>{t("Isolated tenant, project scopes, and multi-Agent attribution", "独立 Tenant、项目 Scope 与多 Agent 来源标记")}</li>
              <li>{t("Capacity, security, and rollout review", "容量、安全与上线方案评估")}</li>
              <li>{t("Operational support and usage-cost review", "运维支持与用量成本核算")}</li>
              <li>{t("Paid use starts only after separate terms are accepted", "只有另行确认商业条款后才开始付费")}</li>
            </ul>
            <a className="button button-secondary" href="/access">{t("Describe a team pilot", "提交团队试点需求")}</a>
          </article>
        </div>
      </section>

      <section className="integration-flow-section section-shell section-block">
        <div className="section-heading split-heading">
          <div>
            <p className="section-index">02 / {t("ACCESS PROCESS", "开放流程")}</p>
            <h2>{t("A review replaces a premature checkout page.", "现阶段先评估接入，不做形式化购买页面。")}</h2>
          </div>
          <p>{t(
            "The application tells us which integration, expected workload, and data boundary you need. We then confirm technical fit and an initial quota before access is enabled.",
            "申请信息用于确认所需接入方式、预计负载和数据边界。完成技术评估与初始额度确认后，才会开放账户权限。",
          )}</p>
        </div>
        <ol className="integration-flow">
          <li><span>01</span><div><h3>{t("Submit the real use case", "提交真实使用场景")}</h3><p>{t("Tell us the Agent stack, the information it needs to remember, and the planned pilot window.", "说明 Agent 技术栈、需要持续记住的信息，以及计划试用时间。")}</p></div></li>
          <li><span>02</span><div><h3>{t("Review the integration boundary", "确认接入边界")}</h3><p>{t("We verify scopes, expected traffic, supported adapters, and operational constraints.", "共同确认 Scope、预计流量、可用适配器和运维约束。")}</p></div></li>
          <li><span>03</span><div><h3>{t("Enable a measured preview", "开放可核算的试用")}</h3><p>{t("Usage and quota come from the service ledger. Limits are visible in the app and console.", "用量与额度以服务端账本为准，并在桌面应用和控制台中显示。")}</p></div></li>
        </ol>
      </section>

      <section className="integration-contract section-shell section-block">
        <div>
          <p className="section-index">03 / {t("BILLING BOUNDARY", "计费边界")}</p>
          <h2>{t("No hidden paid state.", "不会出现隐性的付费状态。")}</h2>
          <p>{t(
            "Invoices, cards, self-serve checkout, and automatic plan upgrades are not currently open. The application shows usage and quota, but does not present unavailable billing controls as working actions.",
            "发票、绑卡、自助购买和自动升级目前尚未开放。应用会显示用量和额度，但不会把尚未接通的计费功能伪装成可用按钮。",
          )}</p>
        </div>
        <div className="contract-code" aria-label={t("Current pricing state", "当前定价状态")}>
          <span>commercial.state</span>
          <pre><code>{`preview_access = reviewed
payment_method = not_required
automatic_billing = disabled
paid_terms = separate_acceptance`}</code></pre>
        </div>
      </section>

      <section className="branch-cta">
        <div className="section-shell">
          <div><p className="section-index">04 / {t("APPLY", "申请")}</p><h2>{t("Start with a bounded, observable pilot.", "从边界清楚、用量可见的试点开始。")}</h2><p>{t("Describe the system you are building and the proof you need from memory.", "说明你正在构建的系统，以及希望通过记忆能力验证什么。")}</p></div>
          <a className="button button-primary" href="/access">{t("Open the application", "填写试用申请")} <span aria-hidden="true">→</span></a>
        </div>
      </section>
      <MarketingFooter />
    </main>
  );
}
