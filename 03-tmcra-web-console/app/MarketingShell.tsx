"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import BrandMark from "./BrandMark";
import { LanguageToggle, type LocalizedText, useLanguage } from "./i18n";

export const TMCRA_GITHUB = "https://github.com/reshuibuduo/TMCRA-agent-memory-algorithm";

const navItems: Array<[LocalizedText, string]> = [
  [{ en: "Continuity", zh: "接续" }, "/#continuity"],
  [{ en: "Product", zh: "产品" }, "/product"],
  [{ en: "Architecture", zh: "架构" }, "/architecture"],
  [{ en: "Evidence", zh: "证据" }, "/benchmarks"],
  [{ en: "Desktop", zh: "桌面端" }, "/download"],
  [{ en: "Developers", zh: "开发者" }, "/developers"],
  [{ en: "API Docs", zh: "API 文档" }, "/docs"],
];

export function MarketingHeader({ tone = "default" }: { tone?: "default" | "continuity" }) {
  const { t, localize } = useLanguage();
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileOpen(false);
    };
    document.documentElement.toggleAttribute("data-marketing-menu-open", mobileOpen);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.documentElement.removeAttribute("data-marketing-menu-open");
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [mobileOpen]);

  return (
    <header className={`site-header${tone === "continuity" ? " is-continuity" : ""}`}>
      <Link className="brand" href="/#top" aria-label={t("TMCRA home", "TMCRA 首页")}>
        <BrandMark />
        <span>TMCRA</span>
      </Link>

      <nav className="desktop-nav" aria-label={t("Primary navigation", "主导航")}>
        {navItems.map(([label, href]) => <Link key={label.en} href={href}>{localize(label)}</Link>)}
      </nav>

      <LanguageToggle />
      <Link className="header-console" href="/console">{t("Console", "控制台")}</Link>
      <Link className="header-cta" href="/access">{t("Request access", "申请试用")}</Link>

      <button
        className="menu-toggle"
        type="button"
        aria-label={t("Toggle navigation", "打开或关闭导航")}
        aria-expanded={mobileOpen}
        onClick={() => setMobileOpen((open) => !open)}
      >
        <span />
        <span />
      </button>

      <button
        className={`mobile-nav-backdrop${mobileOpen ? " is-open" : ""}`}
        type="button"
        aria-label={t("Close navigation", "关闭导航")}
        tabIndex={mobileOpen ? 0 : -1}
        onClick={() => setMobileOpen(false)}
      />
      <nav
        className={`mobile-nav${mobileOpen ? " is-open" : ""}`}
        aria-label={t("Mobile navigation", "移动端导航")}
        aria-hidden={!mobileOpen}
      >
        {navItems.map(([label, href]) => (
          <Link key={label.en} href={href} tabIndex={mobileOpen ? 0 : -1} onClick={() => setMobileOpen(false)}>{localize(label)}</Link>
        ))}
        <Link href="/console" tabIndex={mobileOpen ? 0 : -1} onClick={() => setMobileOpen(false)}>{t("Console", "控制台")}</Link>
        <Link href="/access" tabIndex={mobileOpen ? 0 : -1} onClick={() => setMobileOpen(false)}>{t("Request access", "申请试用")}</Link>
      </nav>
    </header>
  );
}

export function MarketingFooter() {
  const { t } = useLanguage();
  return (
    <footer className="site-footer section-shell">
      <Link className="brand" href="/#top"><BrandMark /><span>TMCRA</span></Link>
      <p>{t("Structured memory. Persistent agents.", "结构化记忆，让 Agent 持续运行。")}</p>
      <div>
        <Link href="/product">{t("Product", "产品")}</Link>
        <Link href="/architecture">{t("Architecture", "架构")}</Link>
        <Link href="/benchmarks">{t("Research", "研究")}</Link>
        <Link href="/security">{t("Security", "安全")}</Link>
        <Link href="/docs">{t("API Docs", "API 文档")}</Link>
        <Link href="/developers">{t("Integrations", "平台适配")}</Link>
        <Link href="/download">{t("Desktop app", "桌面端")}</Link>
        <Link href="/pricing">{t("Pricing", "定价")}</Link>
        <a href={TMCRA_GITHUB} target="_blank" rel="noreferrer">GitHub</a>
        <Link href="/console">{t("Console", "控制台")}</Link>
        <Link href="/access">{t("Early access", "申请试用")}</Link>
        <Link href="/privacy">{t("Privacy", "隐私")}</Link>
        <Link href="/terms">{t("Terms", "条款")}</Link>
      </div>
      <span>© 2026 TMCRA</span>
    </footer>
  );
}
