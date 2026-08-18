"use client";

import { MarketingFooter, MarketingHeader } from "../MarketingShell";
import { useLanguage } from "../i18n";
import "../knowledge-pages.css";

export default function SecurityPage() {
  const { t } = useLanguage();
  return (
    <main className="knowledge-page security-page">
      <MarketingHeader />
      <section className="knowledge-hero section-shell">
        <div><p className="knowledge-kicker">SECURITY / IMPLEMENTED BOUNDARIES</p><h1>{t("Memory access is constrained before it reaches the browser.", "记忆访问在进入浏览器之前就受到边界约束。")}</h1><p>{t("This page documents controls present in the current product and deployment. It does not claim certifications that have not been completed.", "本页只说明当前产品与部署中已经存在的控制措施，不声明尚未完成的安全认证。")}</p></div>
        <aside className="knowledge-instrument"><span>CONTROL PLANE</span><dl><div><dt>Public API</dt><dd>HTTPS</dd></div><div><dt>Browser access</dt><dd>Server BFF</dd></div><div><dt>Memory boundary</dt><dd>Tenant + Scope</dd></div><div><dt>Internal admin</dt><dd>9443 + IP allowlist</dd></div></dl></aside>
      </section>

      <section className="knowledge-section section-shell">
        <div className="knowledge-heading"><p>01 / {t("IMPLEMENTED CONTROLS", "已实施控制")}</p><h2>{t("Security follows the same boundaries as memory.", "安全控制与记忆边界使用同一套结构。")}</h2></div>
        <div className="security-control-grid">
          <article><span>IDENTITY</span><h3>{t("Verified account routing", "经过验证的账户分流")}</h3><p>{t("Account state determines whether the user reaches personal, enterprise, setup, suspended, or internal surfaces.", "账户状态决定用户进入个人、企业、开通、暂停或内部管理界面。")}</p></article>
          <article><span>CREDENTIALS</span><h3>{t("Scoped device authorization", "受 Scope 限制的设备授权")}</h3><p>{t("Codex connections use a browser approval flow. Protected credentials stay in the Electron main process or server environment rather than the browser UI.", "Codex 连接通过浏览器确认授权；受保护凭据保留在 Electron 主进程或服务端环境，不进入浏览器界面。")}</p></article>
          <article><span>ISOLATION</span><h3>{t("Tenant and Scope separation", "Tenant 与 Scope 隔离")}</h3><p>{t("Personal, enterprise, and project memory requests are resolved through server-controlled account and scope boundaries.", "个人、企业与项目记忆请求通过服务端控制的账户与 Scope 边界解析。")}</p></article>
          <article><span>BROWSER</span><h3>{t("Server-side memory proxy", "服务端记忆代理")}</h3><p>{t("Personal and enterprise clients call same-origin server routes. Tenant control credentials are not placed in browser JavaScript.", "个人与企业客户端调用同源服务端路由，Tenant 控制凭据不会进入浏览器 JavaScript。")}</p></article>
          <article><span>OPERATIONS</span><h3>{t("Separate internal control plane", "独立内部控制面")}</h3><p>{t("The internal administration surface is deployed separately on port 9443 and restricted by a source-IP allowlist in the production topology.", "生产拓扑中，内部管理界面单独部署在 9443 端口，并通过来源 IP 白名单限制访问。")}</p></article>
          <article><span>AUDIT</span><h3>{t("Attributable operations", "可追溯操作")}</h3><p>{t("Memory evidence preserves actor and source provenance; the internal surface includes role and operational audit boundaries.", "记忆证据保留主体与来源；内部管理界面包含角色与运维审计边界。")}</p></article>
        </div>
      </section>

      <section className="knowledge-section section-shell">
        <div className="knowledge-heading split"><div><p>02 / {t("CUSTOMER RESPONSIBILITY", "用户责任")}</p><h2>{t("The integration still controls what is submitted.", "接入方仍需控制提交什么内容。")}</h2></div><p>{t("Do not place root keys, passwords, verification codes, or unrelated sensitive content in prompts or source files. Use protected environment configuration and revoke compromised credentials.", "不要把 Root Key、密码、验证码或无关敏感内容放进 Prompt 或源码。请使用受保护的环境配置，并及时撤销已泄露凭据。")}</p></div>
        <div className="benchmark-notes"><article><span>{t("DESKTOP", "桌面端")}</span><p>{t("Review the account and Codex authorization shown by the app before approving it.", "批准前核对应用中显示的账户与 Codex 授权信息。")}</p></article><article><span>{t("API", "API")}</span><p>{t("Keep API Keys in trusted server or local environments, never in a public browser bundle.", "API Key 只保存在可信服务端或本地环境中，不进入公开浏览器包。")}</p></article><article><span>{t("SCOPE", "Scope")}</span><p>{t("Use separate project scopes and explicit Agent attribution when several tools share memory.", "多个工具共享记忆时，应分离项目 Scope，并明确标记不同 Agent。")}</p></article></div>
      </section>

      <section className="knowledge-callout"><div className="section-shell"><div><p>03 / {t("SECURITY CONTACT", "安全联系")}</p><h2>{t("Report a security concern through a tracked request.", "通过可跟踪的申请提交安全问题。")}</h2><p>{t("Use the official form with a verified work email and include enough detail for the team to reproduce and classify the issue.", "请使用经过验证的工作邮箱提交官方表单，并提供足够信息，便于团队复现和判断问题。")}</p></div><a className="button button-primary" href="/access">{t("Open the contact form", "打开联系表单")} →</a></div></section>
      <MarketingFooter />
    </main>
  );
}
