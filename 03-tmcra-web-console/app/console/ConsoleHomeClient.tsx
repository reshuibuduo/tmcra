"use client";

import Image from "next/image";
import Link from "next/link";

import { LanguageToggle, useLanguage } from "../i18n";
import "./console-home.css";

type AccountType = "personal" | "enterprise" | null;

type ConsoleHomeClientProps = {
  actor: {
    displayName: string;
    email: string;
  };
  account: {
    type: AccountType;
    hasPersonalSpace: boolean;
    hasEnterpriseMembership: boolean;
  };
  signOutPath: string;
};

export default function ConsoleHomeClient({
  actor,
  account,
  signOutPath,
}: ConsoleHomeClientProps) {
  const { t } = useLanguage();
  const personalReady = account.type === "personal" && account.hasPersonalSpace;
  const enterpriseReady = account.type === "enterprise" && account.hasEnterpriseMembership;
  const accountLabel = account.type === "personal"
    ? t("Personal", "个人账户")
    : account.type === "enterprise"
      ? t("Enterprise", "企业账户")
      : t("Not configured", "尚未配置");

  return (
    <main className="console-home">
      <header className="console-home-header">
        <Link className="console-home-brand" href="/" aria-label="TMCRA home">
          <Image
            src="/brand/tmcra-app-icon.png"
            width={34}
            height={34}
            alt=""
            priority
            unoptimized
          />
          <span>TMCRA</span>
          <i>CONSOLE</i>
        </Link>
        <nav aria-label={t("Console navigation", "控制台导航")}>
          <Link href="/docs">{t("Docs", "文档")}</Link>
          <Link href="/download">{t("Desktop status", "桌面端状态")}</Link>
          <LanguageToggle className="console-home-language" />
          <a href={signOutPath}>{t("Sign out", "退出登录")}</a>
        </nav>
      </header>

      <div className="console-home-content">
        <section className="console-home-intro" aria-labelledby="console-home-title">
          <div>
            <p className="console-home-eyebrow">ACCOUNT / WORKSPACE</p>
            <h1 id="console-home-title">{t("Your TMCRA workspace", "你的 TMCRA 工作台")}</h1>
            <p>{t("Choose the product surface you want to operate.", "选择你现在要管理的产品空间。")}</p>
          </div>
          <dl className="console-home-identity">
            <div>
              <dt>{t("Signed in", "当前账户")}</dt>
              <dd>{actor.displayName}</dd>
              <small>{actor.email}</small>
            </div>
            <div>
              <dt>{t("Account class", "账户类型")}</dt>
              <dd>{accountLabel}</dd>
              <small>{t("Identity verified", "身份已验证")}</small>
            </div>
          </dl>
        </section>

        <section className="console-home-products" aria-labelledby="products-title">
          <div className="console-home-section-heading">
            <p>01 / PRODUCTS</p>
            <h2 id="products-title">{t("Product spaces", "产品空间")}</h2>
          </div>

          <article className="console-product console-product-personal">
            <div className="console-product-index">P</div>
            <div className="console-product-copy">
              <div className="console-product-title-row">
                <h3>{t("Personal memory", "个人记忆")}</h3>
                <Status ready={personalReady} blocked={account.type === "enterprise"} />
              </div>
              <p>{t("Memory space, connections, sessions and graph explorer.", "管理记忆空间、连接、会话与记忆图谱。")}</p>
            </div>
            <div className="console-product-action">
              {personalReady ? (
                <Link className="console-home-button primary" href="/personal">
                  {t("Open", "打开")}
                </Link>
              ) : account.type === "enterprise" ? (
                <span>{t("Use a separate personal account", "需使用独立个人账户")}</span>
              ) : (
                <Link
                  className="console-home-button primary"
                  href="/account-setup?return_to=%2Fpersonal"
                >
                  {t("Set up", "开通")}
                </Link>
              )}
            </div>
          </article>

          <article className="console-product console-product-enterprise">
            <div className="console-product-index">E</div>
            <div className="console-product-copy">
              <div className="console-product-title-row">
                <h3>{t("Enterprise workspace", "企业工作区")}</h3>
                <Status ready={enterpriseReady} blocked={account.type === "personal"} />
              </div>
              <p>{t("Organizations, agents, API keys, usage and workspace audit.", "管理组织、Agent、API Key、用量与工作区审计。")}</p>
            </div>
            <div className="console-product-action">
              {enterpriseReady ? (
                <Link className="console-home-button primary" href="/enterprise">
                  {t("Open", "打开")}
                </Link>
              ) : (
                <Link className="console-home-button secondary" href="/access">
                  {t("Request access", "申请企业接入")}
                </Link>
              )}
            </div>
          </article>

          <article className="console-product console-product-developer">
            <div className="console-product-index">D</div>
            <div className="console-product-copy">
              <div className="console-product-title-row">
                <h3>{t("Developer tools", "开发者工具")}</h3>
                <span className="console-status is-ready">{t("Available", "可用")}</span>
              </div>
              <p>{t("API reference, SDKs, adapters and desktop integration.", "查看 API、SDK、平台适配与桌面端接入。")}</p>
            </div>
            <div className="console-product-action console-product-action-split">
              <Link className="console-home-button secondary" href="/docs">
                {t("Open docs", "打开文档")}
              </Link>
              <Link className="console-home-button secondary" href="/download">
                {t("Desktop status", "桌面端状态")}
              </Link>
            </div>
          </article>
        </section>

        <section className="console-home-quick" aria-labelledby="quick-title">
          <div className="console-home-section-heading">
            <p>02 / ACCOUNT</p>
            <h2 id="quick-title">{t("Account controls", "账户管理")}</h2>
          </div>
          <div className="console-home-quick-grid">
            <Link href="/forgot-password?return_to=%2Fconsole">
              <span>SEC</span>
              <strong>{t("Password and security", "密码与安全")}</strong>
            </Link>
            <Link href="/pricing">
              <span>PLAN</span>
              <strong>{t("Plans and pricing", "套餐与定价")}</strong>
            </Link>
            <Link href="/docs/api">
              <span>API</span>
              <strong>{t("API reference", "API 参考")}</strong>
            </Link>
          </div>
        </section>
      </div>
    </main>
  );
}

function Status({ ready, blocked }: { ready: boolean; blocked: boolean }) {
  const { t } = useLanguage();
  return (
    <span className={`console-status ${ready ? "is-ready" : blocked ? "is-separate" : "is-pending"}`}>
      {ready
        ? t("Active", "已开通")
        : blocked
          ? t("Separate account", "独立账户")
          : t("Not configured", "未配置")}
    </span>
  );
}
