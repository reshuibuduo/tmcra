"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import { LanguageToggle, useLanguage } from "../../../i18n";

type DeviceProvider = "codex" | "deepseek_harness";

type Connection = {
  id: string;
  provider: DeviceProvider;
  displayName: string;
  tokenId: string;
  tokenPrefix: string;
  scopePrefix: string;
  permissions: string[];
  status: "active" | "revoked" | "expired";
  expiresAt: number;
  createdAt: number;
  firstConnectedAt?: number | null;
  lastConnectedAt: number | null;
  revocationPending?: boolean;
  revokedAt: number | null;
};

type Authorization = {
  userCode: string;
  provider: DeviceProvider;
  clientName: string;
  status: "pending" | "authorizing" | "approved" | "denied" | "claimed";
  expiresAt: number;
  connection: Connection | null;
};

type ApiResult = {
  ok?: boolean;
  authorization?: Authorization | null;
  connections?: Connection[];
  result?: { status?: string; connection?: Connection | null } | Connection;
  error?: { code?: string; message?: string };
};

export default function CodexDeviceAuthorizationClient({
  email,
  initialUserCode,
  provider = "codex",
  providerLabel = "Codex",
  connectPath = "/console/connect/codex",
}: {
  email: string;
  initialUserCode: string;
  provider?: DeviceProvider;
  providerLabel?: string;
  connectPath?: string;
}) {
  const { t } = useLanguage();
  const [userCode, setUserCode] = useState(initialUserCode);
  const [authorization, setAuthorization] = useState<Authorization | null>(null);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(Boolean(initialUserCode));
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async (code: string) => {
    setLoading(true);
    setError("");
    try {
      const query = code ? `?user_code=${encodeURIComponent(code)}` : "";
      const response = await fetch(
        `/api/console/v1/device-authorizations${query}`,
        { cache: "no-store" },
      );
      const result = (await response.json().catch(() => null)) as ApiResult | null;
      if (!response.ok || !result?.ok) {
        throw new Error(result?.error?.message || "authorization_load_failed");
      }
      const nextAuthorization = result.authorization ?? null;
      if (nextAuthorization && nextAuthorization.provider !== provider) {
        throw new Error(
          t(
            `This code belongs to ${providerName(nextAuthorization.provider)}, not ${providerLabel}.`,
            `这个授权码属于 ${providerName(nextAuthorization.provider)}，不能在 ${providerLabel} 连接页确认。`,
          ),
        );
      }
      setAuthorization(nextAuthorization);
      setConnections((result.connections ?? []).filter((connection) => connection.provider === provider));
    } catch (cause) {
      setAuthorization(null);
      setError(
        cause instanceof Error && cause.message !== "authorization_load_failed"
          ? cause.message
          : t(
              "The authorization could not be loaded.",
              "暂时无法读取这次授权，请检查授权码后重试。",
            ),
      );
    } finally {
      setLoading(false);
    }
  }, [provider, providerLabel, t]);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      void load(initialUserCode || "");
    }, 0);
    return () => window.clearTimeout(timeout);
  }, [initialUserCode, load]);

  const submitCode = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const code = normalizeCode(userCode);
    if (!code) {
      setError(t(`Enter the eight-character code shown by ${providerLabel}.`, `请输入 ${providerLabel} 显示的 8 位授权码。`));
      return;
    }
    setUserCode(code);
    window.history.replaceState(null, "", `${connectPath}?user_code=${encodeURIComponent(code)}`);
    void load(code);
  };

  const decide = async (action: "approve" | "deny") => {
    const code = authorization?.userCode || normalizeCode(userCode);
    if (!code) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await postAction({ action, userCode: code });
      if (!result.ok) throw new Error(result.error?.message || "authorization_failed");
      setNotice(
        action === "approve"
          ? t(
              `Authorized. Return to ${providerLabel}; it will finish the connection automatically.`,
              `授权已完成。现在返回 ${providerLabel}，它会自动完成连接。`,
            )
          : t("Authorization denied.", "已拒绝这次授权。"),
      );
      await load(code);
    } catch (cause) {
      setError(
        cause instanceof Error && cause.message !== "authorization_failed"
          ? cause.message
          : t("Authorization failed. Try again.", "授权失败，请稍后重试。"),
      );
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (connection: Connection) => {
    if (!window.confirm(t(`Revoke this ${providerLabel} connection?`, `确定撤销这个 ${providerLabel} 连接吗？`))) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await postAction({ action: "revoke", connectionId: connection.id });
      if (!result.ok) throw new Error(result.error?.message || "revoke_failed");
      setNotice(t(`${providerLabel} connection revoked.`, `${providerLabel} 连接已撤销。`));
      await load(authorization?.userCode ?? "");
    } catch (cause) {
      setError(
        cause instanceof Error && cause.message !== "revoke_failed"
          ? cause.message
          : t("The connection could not be revoked.", "暂时无法撤销连接，请稍后重试。"),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="codex-connect-page">
      <section className="codex-connect-shell">
        <header className="codex-connect-header">
          <div>
            <p>TMCRA / {providerLabel.toUpperCase()} CONNECTION</p>
            <span>{email}</span>
          </div>
          <LanguageToggle />
        </header>

        <section className="codex-connect-hero">
          <p className="codex-connect-step">02 / DEVICE AUTHORIZATION</p>
          <h1>{t(`Connect ${providerLabel} to your memory space.`, `把 ${providerLabel} 连接到你的记忆空间。`)}</h1>
          <p>
            {t(
              `Confirm the code shown by ${providerLabel}. TMCRA will issue a restricted Token for this personal Scope namespace; the production control Key never enters ${providerLabel} or the browser.`,
              `请确认 ${providerLabel} 显示的授权码。TMCRA 会为当前个人 Scope 签发受限 Token；生产控制 Key 不会进入 ${providerLabel}，也不会发送到浏览器。`,
            )}
          </p>
        </section>

        <form className="codex-code-form" onSubmit={submitCode}>
          <label htmlFor="codex-user-code">{t("Authorization code", "授权码")}</label>
          <div>
            <input
              id="codex-user-code"
              value={userCode}
              onChange={(event) => setUserCode(event.target.value.toUpperCase())}
              inputMode="text"
              autoComplete="one-time-code"
              maxLength={9}
              placeholder="ABCD2345"
              aria-describedby="codex-code-note"
            />
            <button type="submit" disabled={loading}>
              {loading ? t("Checking…", "正在核对…") : t("Check code", "核对授权码")}
            </button>
          </div>
          <small id="codex-code-note">
            {t("The code expires after 10 minutes.", "授权码有效期为 10 分钟。")}
          </small>
        </form>

        {error && <p className="codex-connect-alert is-error" role="alert">{error}</p>}
        {notice && <p className="codex-connect-alert is-success" role="status">{notice}</p>}

        {authorization && (
          <section className="codex-approval-card" aria-labelledby="approval-title">
            <div className="codex-approval-status">
              <span>{authorizationStatus(authorization.status, t)}</span>
              <time dateTime={new Date(authorization.expiresAt).toISOString()}>
                {t("Expires", "到期时间")} {formatDate(authorization.expiresAt)}
              </time>
            </div>
            <p>{t("Connection request", "连接请求")}</p>
            <h2 id="approval-title">{authorization.clientName}</h2>
            <strong className="codex-user-code">{authorization.userCode}</strong>
            <dl>
              <div><dt>Scope</dt><dd>{t("Only this personal namespace and its child project Scopes", "仅限当前个人命名空间及其下属项目 Scope")}</dd></div>
              <div><dt>{t("Permissions", "权限")}</dt><dd>memory:read · memory:write · memory:feedback</dd></div>
              <div><dt>Token</dt><dd>{t(`Shown once to ${providerLabel} after approval`, `批准后仅向 ${providerLabel} 返回一次`)}</dd></div>
            </dl>
            {authorization.status === "pending" && (
              <div className="codex-approval-actions">
                <button type="button" disabled={busy} onClick={() => void decide("approve")}>
                  {busy ? t("Authorizing…", "正在授权…") : t(`Authorize ${providerLabel}`, `授权 ${providerLabel}`)}
                </button>
                <button type="button" disabled={busy} onClick={() => void decide("deny")}>
                  {t("Deny", "拒绝")}
                </button>
              </div>
            )}
          </section>
        )}

        <section className="codex-connections" aria-labelledby="connections-title">
          <div>
            <p>03 / CONNECTIONS</p>
            <h2 id="connections-title">{t(`Connected ${providerLabel} clients`, `已连接的 ${providerLabel} 客户端`)}</h2>
          </div>
          {connections.length === 0 ? (
            <p className="codex-connections-empty">
              {t(`No ${providerLabel} connection has been created yet.`, `当前还没有已建立的 ${providerLabel} 连接。`) }
            </p>
          ) : (
            <div className="codex-connection-list">
              {connections.map((connection) => (
                <article key={connection.id}>
                  <div>
                    <span className={`connection-state is-${connection.revocationPending ? "pending" : connection.status}`}>
                      {connection.revocationPending
                        ? t("revocation pending", "正在完成吊销")
                        : connection.status}
                    </span>
                    <h3>{connection.displayName}</h3>
                    <code>{connection.tokenPrefix}</code>
                  </div>
                  <dl>
                    <div><dt>Scope</dt><dd>{connection.scopePrefix}*</dd></div>
                    <div><dt>{t("Authorized", "授权时间")}</dt><dd>{formatDate(connection.createdAt)}</dd></div>
                    <div><dt>{t("First connected", "首次连接")}</dt><dd>{formatConnectionDate(connection.firstConnectedAt ?? connection.lastConnectedAt, t)}</dd></div>
                  </dl>
                  {connection.status === "active" && (
                    <button type="button" disabled={busy} onClick={() => void revoke(connection)}>
                      {t("Revoke connection", "撤销连接")}
                    </button>
                  )}
                </article>
              ))}
            </div>
          )}
        </section>
      </section>
    </main>
  );
}

async function postAction(body: Record<string, unknown>): Promise<ApiResult> {
  const response = await fetch("/api/console/v1/device-authorizations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const result = (await response.json().catch(() => null)) as ApiResult | null;
  if (!response.ok || !result) {
    return {
      ok: false,
      error: result?.error ?? { message: "Request failed." },
    };
  }
  return result;
}

function normalizeCode(value: string) {
  const code = value.toUpperCase().replace(/[\s-]+/g, "");
  return /^[A-HJ-NP-Z2-9]{8}$/.test(code) ? code : "";
}

function formatDate(value: number) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatConnectionDate(
  value: number | null | undefined,
  t: (english: string, chinese: string) => string,
) {
  return value ? formatDate(value) : t("Not connected yet", "尚未完成连接");
}

function providerName(provider: DeviceProvider) {
  return provider === "deepseek_harness" ? "DeepSeek Harness" : "Codex";
}

function authorizationStatus(
  status: Authorization["status"],
  t: (english: string, chinese: string) => string,
) {
  const labels: Record<Authorization["status"], [string, string]> = {
    pending: ["Awaiting approval", "等待确认"],
    authorizing: ["Issuing Token", "正在签发 Token"],
    approved: ["Approved", "已批准"],
    denied: ["Denied", "已拒绝"],
    claimed: ["Connected", "已连接"],
  };
  return t(...labels[status]);
}
