"use client";

import { MarketingFooter, MarketingHeader } from "../MarketingShell";
import { useLanguage } from "../i18n";

export default function TermsPage() {
  const { t } = useLanguage();
  return <main className="public-page"><MarketingHeader /><article className="legal-document section-shell"><p className="eyebrow"><span /> {t("LEGAL / TERMS", "法律 / 条款")}</p><h1>{t("Terms of Service", "服务条款")}</h1><p className="legal-date">{t("Effective: July 18, 2026", "生效日期：2026 年 7 月 18 日")}</p>
    <section><h2>{t("1. Agreement", "1. 条款接受")}</h2><p>{t("By creating an account, installing the desktop client, or using a TMCRA API, you agree to these terms and the Privacy Policy. If you use TMCRA for an organization, you confirm that you may bind that organization.", "创建账户、安装桌面客户端或使用 TMCRA API，即表示您同意本条款与隐私政策。若您代表组织使用 TMCRA，您确认有权使该组织受本条款约束。")}</p></section>
    <section><h2>{t("2. Accounts and credentials", "2. 账户与凭据")}</h2><p>{t("Provide accurate account information, keep credentials confidential, and promptly revoke compromised tokens. You are responsible for authorized activity under your account and tenant.", "请提供准确的账户信息、妥善保护凭据，并及时撤销已泄露的 Token。您应对账户及 Tenant 下的授权活动负责。")}</p></section>
    <section><h2>{t("3. Acceptable use", "3. 可接受使用")}</h2><p>{t("Do not use TMCRA to violate law, infringe rights, bypass security, distribute malware, overload the service, or submit content you are not authorized to process.", "不得利用 TMCRA 违法、侵害他人权利、绕过安全机制、传播恶意软件、使服务过载，或提交未经授权处理的内容。")}</p></section>
    <section><h2>{t("4. Preview service", "4. 试用服务")}</h2><p>{t("Early access may be usage-limited, changed, suspended, or ended while capacity and commercial controls are validated. Paid use begins only after separate commercial terms are presented and accepted.", "早期试用可能受到用量限制，也可能在容量和商业控制验证期间调整、暂停或结束。只有在另行展示并接受商业条款后，才会开始付费使用。")}</p></section>
    <section><h2>{t("5. Customer content and output", "5. 客户内容与输出")}</h2><p>{t("You retain rights in content you submit. You grant TMCRA the limited permission needed to store, transform, index, retrieve, secure, and return that content as requested. You remain responsible for reviewing model inputs and outputs used with the service.", "您保留对提交内容的权利，并授予 TMCRA 为按请求存储、转换、索引、召回、保护和返回相关内容所需的有限许可。您仍需负责审核与本服务配合使用的模型输入和输出。")}</p></section>
    <section><h2>{t("6. Availability and changes", "6. 可用性与变更")}</h2><p>{t("We work to operate TMCRA reliably but do not promise uninterrupted preview availability. Security, compatibility, or operational updates may be deployed without stopping the public API where practicable.", "我们会努力可靠运行 TMCRA，但不保证试用服务始终不中断。在可行情况下，安全、兼容性或运维更新会通过不中断公共 API 的方式发布。")}</p></section>
    <section><h2>{t("7. Suspension and termination", "7. 暂停与终止")}</h2><p>{t("Access may be suspended for security risk, abuse, non-payment under future commercial terms, legal requirements, or material breach. You may stop using the service and request account or scope deletion subject to applicable retention duties.", "如存在安全风险、滥用、未来商业条款下的欠费、法律要求或重大违约，访问权限可能被暂停。您可以停止使用服务，并在适用保留义务范围内申请删除账户或 Scope。")}</p></section>
    <section><h2>{t("8. Contact", "8. 联系方式")}</h2><p>{t("Questions about these terms can be submitted through the official access and support form.", "如对本条款有疑问，请通过官方申请与支持表单提交。")}</p><a className="legal-link" href="/access">{t("Open contact form", "打开联系表单")} <span aria-hidden="true">→</span></a></section>
  </article><MarketingFooter /></main>;
}
