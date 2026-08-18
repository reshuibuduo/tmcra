"use client";

import { useState } from "react";

import { LanguageToggle, useLanguage } from "../i18n";

export default function AccountSetupClient({
  email,
  returnTo,
  signOutPath,
}: {
  email: string;
  returnTo: string;
  signOutPath: string;
}) {
  const { t } = useLanguage();
  const [state, setState] = useState<"idle" | "saving" | "error">("idle");
  const [error, setError] = useState("");

  const createPersonalAccount = async () => {
    setState("saving");
    setError("");
    try {
      const response = await fetch("/api/account", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "create_personal" }),
      });
      const result = (await response.json().catch(() => null)) as
        | { ok?: boolean; destination?: string; error?: { message?: string } }
        | null;
      if (!response.ok || !result?.ok) {
        throw new Error(result?.error?.message || "account_setup_failed");
      }
      window.location.assign(returnTo || result.destination || "/personal");
    } catch (cause) {
      setError(
        cause instanceof Error && cause.message !== "account_setup_failed"
          ? cause.message
          : t(
              "The personal space could not be created. Try again.",
              "个人空间暂时无法创建，请稍后重试。",
            ),
      );
      setState("error");
    }
  };

  return (
    <main className="account-boundary-page">
      <section className="account-boundary-main">
        <div className="account-boundary-topline">
          <p className="account-boundary-brand">TMCRA / ACCOUNT SETUP</p>
          <LanguageToggle />
        </div>
        <p className="account-boundary-step">01 / PERSONAL MEMORY</p>
        <h1>{t("Create your personal memory space.", "创建你的个人记忆空间。")}</h1>
        <p>
          {t(
            "TMCRA will create one isolated memory namespace for this ChatGPT account. Project Scope and conversation Session data stay inside that boundary.",
            "TMCRA 会为当前 ChatGPT 账号建立独立的记忆命名空间。每个项目的 Scope 和对话 Session 都归在这个边界内，不会与其他账号混用。",
          )}
        </p>

        <div className="account-boundary-identity">
          <span>{t("Signed in as", "当前账号")}</span>
          <strong>{email}</strong>
        </div>

        <div className="account-boundary-types" aria-label={t("Account setup details", "账号开通说明")}>
          <section>
            <p className="account-boundary-kicker">ISOLATION</p>
            <h2>{t("A stable, private namespace", "稳定且独立的命名空间")}</h2>
            <p>
              {t(
                "The Scope namespace is derived from an internal random identity. Your email is never embedded in its name.",
                "Scope 名称由内部随机身份稳定派生，不会包含邮箱或其他可识别个人身份的信息。",
              )}
            </p>
          </section>
          <section>
            <p className="account-boundary-kicker">CODEX</p>
            <h2>{t("Connect after setup", "开通后连接 Codex")}</h2>
            <p>
              {t(
                "The Codex installer uses browser device authorization. You do not need to paste a root API Key into the plugin.",
                "Codex 安装器会通过浏览器完成设备授权，不需要把 Root API Key 复制到插件里。",
              )}
            </p>
          </section>
        </div>

        {error && <p className="account-boundary-error" role="alert">{error}</p>}
        <div className="account-boundary-actions">
          <button
            type="button"
            disabled={state === "saving"}
            onClick={() => void createPersonalAccount()}
          >
            {state === "saving"
              ? t("Creating…", "正在创建…")
              : t("Create personal space", "创建个人空间")}
            <span aria-hidden="true">→</span>
          </button>
          <a href={signOutPath}>{t("Use another account", "切换账号")}</a>
        </div>
        <p className="account-boundary-footnote">
          {t(
            "This operation is atomic and safe to retry. Repeating it will return the same personal space.",
            "该操作会原子完成，也可以安全重试；重复提交不会创建多个个人空间。",
          )}
        </p>
      </section>
    </main>
  );
}
