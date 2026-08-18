"use client";

import { LanguageToggle, useLanguage } from "../i18n";

export default function AccountSuspendedClient({ signOutPath }: { signOutPath: string }) {
  const { t } = useLanguage();

  return (
    <main className="account-boundary-page">
      <section className="account-boundary-main">
        <div className="account-boundary-topline">
          <p className="account-boundary-brand">TMCRA / ACCESS STATUS</p>
          <LanguageToggle />
        </div>
        <p className="account-boundary-step">ACCOUNT / SUSPENDED</p>
        <h1>{t("This account is suspended.", "当前账户已暂停使用。")}</h1>
        <p>{t(
          "Personal, enterprise, and internal control surfaces remain unavailable until the account status is resolved.",
          "账户状态恢复前，个人空间、企业控制台和内部管理界面均不可使用。",
        )}</p>
        <div className="account-boundary-error" role="alert">
          {t(
            "If you believe this is a mistake, contact TMCRA support with the verified account email. Do not create a second account to bypass the restriction.",
            "如果你认为账户被误暂停，请使用已验证邮箱联系 TMCRA 支持。请勿通过注册其他账户绕过限制。",
          )}
        </div>
        <div className="account-boundary-actions">
          <a href={signOutPath}>{t("Sign out", "退出当前账户")}</a>
        </div>
      </section>
    </main>
  );
}
