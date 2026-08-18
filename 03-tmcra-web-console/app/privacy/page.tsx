"use client";

import { MarketingFooter, MarketingHeader } from "../MarketingShell";
import { useLanguage } from "../i18n";

export default function PrivacyPage() {
  const { t } = useLanguage();
  return <main className="public-page"><MarketingHeader /><article className="legal-document section-shell"><p className="eyebrow"><span /> {t("LEGAL / PRIVACY", "法律 / 隐私")}</p><h1>{t("Privacy Policy", "隐私政策")}</h1><p className="legal-date">{t("Effective: July 18, 2026", "生效日期：2026 年 7 月 18 日")}</p>
    <section><h2>{t("1. Scope", "1. 适用范围")}</h2><p>{t("This policy applies to the TMCRA website, account system, desktop client, APIs, and memory services. Customer-controlled content remains separated by tenant and scope boundaries.", "本政策适用于 TMCRA 网站、账户系统、桌面客户端、API 与记忆服务。客户控制的内容按照 Tenant 与 Scope 边界隔离。")}</p></section>
    <section><h2>{t("2. Data we process", "2. 我们处理的数据")}</h2><p>{t("We process account identifiers, security and device events, API usage records, support requests, and memory content submitted by an authorized customer or agent. We do not require customers to send data unrelated to their chosen use case.", "我们会处理账户标识、安全与设备事件、API 用量记录、支持请求，以及由授权客户或 Agent 提交的记忆内容。我们不要求客户提交与其使用场景无关的数据。")}</p></section>
    <section><h2>{t("3. Purpose and legal basis", "3. 处理目的与依据")}</h2><p>{t("Data is used to provide and secure the service, isolate tenants, execute requested memory operations, prevent abuse, support users, and meet legal obligations. Promotional email is sent only when the account has opted in and can be disabled independently.", "数据用于提供和保护服务、实施租户隔离、执行请求的记忆操作、防止滥用、提供支持以及履行法律义务。推广邮件仅向主动选择接收的账户发送，并可单独关闭。")}</p></section>
    <section><h2>{t("4. Retention and deletion", "4. 保留与删除")}</h2><p>{t("Operational and memory data is retained according to the active service and scope policy. Authorized users can request export or deletion; limited security, billing, and audit records may be retained where required for integrity or law.", "运维数据与记忆数据按照当前服务及 Scope 策略保留。授权用户可以申请导出或删除；为保持完整性或满足法律要求，部分安全、计费与审计记录可能继续保留。")}</p></section>
    <section><h2>{t("5. Service providers and transfers", "5. 服务提供方与跨境处理")}</h2><p>{t("TMCRA may use infrastructure, email, authentication, and model providers to operate the requested service. Providers receive only the data needed for their function and are subject to applicable contractual and security controls.", "TMCRA 可能使用基础设施、邮件、身份认证和模型服务提供方来完成客户请求。相关提供方仅接收履行其功能所需的数据，并受适用的合同与安全控制约束。")}</p></section>
    <section><h2>{t("6. Security and your choices", "6. 安全与您的选择")}</h2><p>{t("We use encrypted transport, scoped credentials, account verification, source-IP controls for internal administration, and auditable service operations. You are responsible for protecting credentials and configuring what your agents submit.", "我们使用加密传输、最小范围凭据、账户验证、内部管理源 IP 控制和可审计的服务操作。您有责任保护凭据，并配置 Agent 可以提交的内容。")}</p></section>
    <section><h2>{t("7. Contact", "7. 联系方式")}</h2><p>{t("For privacy questions or rights requests, use the official access and support form so the request can be verified and tracked.", "如需咨询隐私问题或提出数据权利请求，请通过官方申请与支持表单提交，以便完成身份核验和处理跟踪。")}</p><a className="legal-link" href="/access">{t("Open contact form", "打开联系表单")} <span aria-hidden="true">→</span></a></section>
  </article><MarketingFooter /></main>;
}
