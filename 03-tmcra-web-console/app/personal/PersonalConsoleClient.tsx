"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { LanguageToggle, type Language, useLanguage } from "../i18n";
import MemoryExplorer from "../console/MemoryExplorer";
import PersonalUsageLedger from "./PersonalUsageLedger";
import VisualMemoryAtlasExplorer from "./VisualMemoryAtlasExplorer";

type Actor = { id: string; displayName: string; email: string };
type Space = { id: string; displayName: string; status: string };
type Retention = {
  enabled: boolean;
  inactive_days: number;
  created_at: number | null;
  updated_at: number | null;
};
type DateValue = string | number | null;
type CatalogItem = string | {
  id?: string;
  name?: string;
  displayName?: string;
  scopeName?: string;
  scope_name?: string;
  projectId?: string;
  project_id?: string;
  sessionId?: string;
  session_id?: string;
  status?: string;
  createdAt?: DateValue;
  created_at?: DateValue;
  updatedAt?: DateValue;
  updated_at?: DateValue;
};
type Connection = {
  id: string;
  provider?: "codex" | "deepseek_harness";
  displayName?: string;
  status?: string;
  createdAt?: DateValue;
  firstConnectedAt?: DateValue;
  lastConnectedAt?: DateValue;
  revocationPending?: boolean;
  expiresAt?: DateValue;
};
type QuotaMetric = {
  id?: string;
  name?: string;
  label?: string;
  used?: number | null;
  limit?: number | null;
  remaining?: number | null;
  unlimited?: boolean;
};
type Quota = {
  plan?: string | null;
  status?: string | null;
  metrics?: Record<string, QuotaMetric> | QuotaMetric[] | null;
};
type Snapshot = {
  actor: Actor;
  space: Space;
  retention?: Retention | null;
  quota?: Quota | null;
  scopes?: CatalogItem[];
  projects?: CatalogItem[];
  sessions?: CatalogItem[];
  sessionTotal?: number | null;
  connections?: Connection[];
};
type ExportState = {
  jobId: string;
  status: "pending" | "ready" | "failed" | "expired";
  exportId?: string;
  downloadUrl?: string | null;
  expiresAt?: number | null;
  sizeBytes?: number | null;
};
type View = "overview" | "memory" | "codex" | "usage" | "privacy";
type Translate = (english: string, chinese: string) => string;

const PERSONAL_GRAPH_CONTEXT: Record<string, string> = {};
const RATINGS = ["helpful", "incorrect", "stale", "unsafe", "missing"] as const;

export default function PersonalConsoleClient({
  initialActor,
  initialSpace,
}: {
  initialActor: Actor;
  initialSpace: Space;
}) {
  const { language, t } = useLanguage();
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [view, setView] = useState<View>("overview");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [retentionEnabled, setRetentionEnabled] = useState(false);
  const [inactiveDays, setInactiveDays] = useState(365);
  const [rating, setRating] = useState<(typeof RATINGS)[number]>("helpful");
  const [comment, setComment] = useState("");
  const [exportState, setExportState] = useState<ExportState | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/personal", {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      const body = await readResponse(
        response,
        t("We could not load your memory space.", "暂时无法读取你的记忆空间。"),
      );
      const next = body as unknown as Snapshot;
      setSnapshot(next);
      if (next.retention) {
        setRetentionEnabled(Boolean(next.retention.enabled));
        setInactiveDays(Number(next.retention.inactive_days) || 365);
      }
    } catch (caught) {
      setError(caught instanceof Error
        ? caught.message
        : t("We could not load your memory space.", "暂时无法读取你的记忆空间。"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const runAction = async (
    action: string,
    payload: Record<string, unknown>,
    idempotencyKey?: string,
  ) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch("/api/personal", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
        },
        body: JSON.stringify({ action, payload }),
      });
      const body = await readResponse(
        response,
        t("The operation could not be completed.", "操作没有完成，请稍后重试。"),
      );
      if (action === "export.start") {
        const jobId = exportJobId(body.result);
        if (!jobId) throw new Error(t("The export job could not be started.", "导出任务没有正确启动，请重试。"));
        setExportState({ jobId, status: "pending" });
      }
      setNotice(action === "export.start"
        ? t("The export is being prepared. This page will update automatically.", "正在准备导出文件，页面会自动更新状态。")
        : action === "feedback.submit"
          ? t("Thank you. Your feedback has been recorded.", "谢谢，你的反馈已记录。")
          : action === "connection.revoke"
            ? t("The connection has been revoked.", "连接已吊销。")
            : t("The retention policy has been updated.", "记忆保留策略已更新。"));
      if (action === "retention.set" || action === "connection.revoke") await load();
      return body;
    } catch (caught) {
      setError(caught instanceof Error
        ? caught.message
        : t("The operation could not be completed.", "操作没有完成，请稍后重试。"));
      return null;
    } finally {
      setBusy(false);
    }
  };

  const pendingExportJobId = exportState?.status === "pending" ? exportState.jobId : null;
  useEffect(() => {
    if (!pendingExportJobId) return;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      try {
        const response = await fetch(
          `/api/personal/export/status?job_id=${encodeURIComponent(pendingExportJobId)}`,
          { headers: { Accept: "application/json" }, cache: "no-store" },
        );
        const body = await readResponse(
          response,
          t("We could not check the export status.", "暂时无法查询导出状态。"),
        );
        const next = normalizeExportState(body.export, pendingExportJobId);
        if (!cancelled) {
          setExportState(next);
          if (next.status === "ready") {
            setNotice(t("Your export is ready to download.", "导出文件已经准备好，可以下载了。"));
          } else if (next.status === "failed") {
            setNotice(t("The export failed. You can start a new export.", "本次导出失败，可以重新发起导出。"));
          } else if (next.status === "expired") {
            setNotice(t("The export has expired. Start a new export to download fresh data.", "导出文件已过期，请重新导出最新数据。"));
          }
        }
        if (!cancelled && next.status === "pending") {
          timer = window.setTimeout(() => void poll(), 2_000);
        }
      } catch {
        if (!cancelled) timer = window.setTimeout(() => void poll(), 5_000);
      }
    };
    timer = window.setTimeout(() => void poll(), 800);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [pendingExportJobId, t]);

  const resolvedActor = snapshot?.actor ?? initialActor;
  const resolvedSpace = snapshot?.space ?? initialSpace;
  const connections = snapshot?.connections;
  const quotaMetrics = useMemo(() => normalizeQuotaMetrics(snapshot?.quota?.metrics), [snapshot?.quota?.metrics]);
  const catalogRows = useMemo(
    () => [
      ...normalizeCatalog(snapshot?.scopes, "scope"),
      ...normalizeCatalog(snapshot?.projects, "project"),
      ...normalizeCatalog(snapshot?.sessions, "session"),
    ],
    [snapshot?.projects, snapshot?.scopes, snapshot?.sessions],
  );
  const activeConnections = connections?.filter((connection) =>
    ["active", "connected", "ready"].includes((connection.status ?? "").toLowerCase())).length;

  const navigation: Array<{ id: View; number: string; en: string; zh: string }> = [
    { id: "overview", number: "01", en: "Overview", zh: "概览" },
    { id: "memory", number: "02", en: "Memory", zh: "记忆" },
    { id: "codex", number: "03", en: "Connections", zh: "工具连接" },
    { id: "usage", number: "04", en: "Usage", zh: "用量与额度" },
    { id: "privacy", number: "05", en: "Privacy", zh: "隐私与数据" },
  ];

  return (
    <div className="personal-shell console-shell">
      <a className="skip-link" href="#personal-main">{t("Skip to content", "跳到主要内容")}</a>
      <aside className="personal-sidebar">
        <div className="personal-brand"><b>TMCRA</b><span>{t("PERSONAL", "个人版")}</span></div>
        <nav aria-label={t("Personal memory console", "个人记忆控制台")}>
          {navigation.map((item) => (
            <button
              key={item.id}
              type="button"
              className={view === item.id ? "is-active" : ""}
              aria-current={view === item.id ? "page" : undefined}
              onClick={() => setView(item.id)}
            >
              <i>{item.number}</i><span>{t(item.en, item.zh)}</span>
            </button>
          ))}
        </nav>
        <div className="personal-identity">
          <span>{resolvedActor.displayName}</span>
          <small>{resolvedActor.email}</small>
        </div>
      </aside>

      <main id="personal-main" className="personal-main">
        <header className="personal-header">
          <div>
            <p>{t("PERSONAL MEMORY SPACE", "个人记忆空间")}</p>
            <h1>{resolvedSpace.displayName}</h1>
          </div>
          <div className="personal-status" aria-live="polite">
            <LanguageToggle />
            <i className={error ? "is-error" : ""} aria-hidden="true" />
            <span>{loading
              ? t("Checking", "正在检查")
              : error
                ? t("Needs attention", "需要处理")
                : localizedStatus(resolvedSpace.status, t)}</span>
            <button type="button" onClick={() => void load()} disabled={loading}>
              {t("Refresh", "刷新")}
            </button>
          </div>
        </header>

        {error && <div className="personal-alert" role="alert">{error}</div>}
        {notice && <div className="personal-notice" role="status">{notice}</div>}

        {loading && !snapshot ? (
          <section className="empty-state" aria-live="polite" aria-busy="true">
            <span className="empty-code" aria-hidden="true">···</span>
            <div>
              <span className="system-label">{t("LOADING", "正在加载")}</span>
              <h2>{t("Opening your memory space", "正在打开你的记忆空间")}</h2>
              <p>{t("We are reading the latest connection, usage, and memory status.", "正在读取最新的连接、用量和记忆状态。")}</p>
            </div>
          </section>
        ) : view === "overview" ? (
          <OverviewView
            snapshot={snapshot}
            space={resolvedSpace}
            activeConnections={activeConnections}
            t={t}
          />
        ) : view === "memory" ? (
          <MemoryView rows={catalogRows} t={t} language={language} onCatalogChanged={load} />
        ) : view === "codex" ? (
          <CodexView
            connections={connections}
            busy={busy}
            language={language}
            t={t}
            onRevoke={(connection) => {
              const confirmed = window.confirm(t(
                `Revoke ${connection.displayName || "this connection"}? Its Token will stop working immediately.`,
                `确定吊销${connection.displayName ? `“${connection.displayName}”` : "这个连接"}吗？对应 Token 将立即失效。`,
              ));
              if (confirmed) void runAction("connection.revoke", { connectionId: connection.id });
            }}
          />
        ) : view === "usage" ? (
          <UsageView quota={snapshot?.quota} metrics={quotaMetrics} language={language} t={t} />
        ) : (
          <PrivacyView
            retention={snapshot?.retention}
            retentionEnabled={retentionEnabled}
            inactiveDays={inactiveDays}
            rating={rating}
            comment={comment}
            exportState={exportState}
            busy={busy}
            language={language}
            t={t}
            onRetentionEnabled={setRetentionEnabled}
            onInactiveDays={setInactiveDays}
            onRating={setRating}
            onComment={setComment}
            onRunAction={runAction}
          />
        )}
      </main>
    </div>
  );
}

function OverviewView({
  snapshot,
  space,
  activeConnections,
  t,
}: {
  snapshot: Snapshot | null;
  space: Space;
  activeConnections: number | undefined;
  t: Translate;
}) {
  return (
    <div>
      <section className="personal-memory-surface" aria-labelledby="overview-title">
        <div className="personal-section-heading">
          <div><span>{t("01 / OVERVIEW", "01 / 概览")}</span><h2 id="overview-title">{t("Your TMCRA memory", "你的 TMCRA 记忆")}</h2></div>
          <p>{t("Only verified service data is shown", "这里只展示服务端确认的数据")}</p>
        </div>
        <div className="metric-strip" aria-label={t("Account summary", "账户概况")}>
          <SummaryMetric value={localizedStatus(space.status, t)} label={t("Memory space", "记忆空间")} />
          <SummaryMetric value={arrayCount(snapshot?.scopes)} label="Scope" />
          <SummaryMetric value={arrayCount(snapshot?.projects)} label={t("Projects", "项目")} />
          <SummaryMetric
            value={typeof snapshot?.sessionTotal === "number" ? String(snapshot.sessionTotal) : arrayCount(snapshot?.sessions)}
            label="Session"
          />
          <SummaryMetric
            value={snapshot?.connections ? String(activeConnections ?? 0) : "—"}
            label={t("Active tool connections", "有效工具连接")}
          />
        </div>
      </section>

      <div className="personal-governance">
        <section aria-labelledby="organization-title">
          <div className="personal-section-heading">
            <div><span>{t("MEMORY BOUNDARIES", "记忆边界")}</span><h2 id="organization-title">{t("One account, clear boundaries", "同一账户，边界清晰")}</h2></div>
          </div>
          <p>{t(
            "Account-level memory keeps durable preferences. Each project has its own Scope, and each conversation is tracked as a Session inside that project. This enables cross-session continuity without mixing unrelated projects.",
            "账户级记忆保存长期稳定的个人偏好；每个项目使用独立 Scope，每次对话则作为该项目下的 Session。这样既能跨 Session 延续上下文，也不会把不同项目的记忆混在一起。",
          )}</p>
        </section>

        <section aria-labelledby="connection-summary-title">
          <div className="personal-section-heading">
            <div><span>{t("CONNECTED TOOLS", "已连接工具")}</span><h2 id="connection-summary-title">{t("Connection status", "连接状态")}</h2></div>
          </div>
          <p>{snapshot?.connections === undefined
            ? t("Connection status has not been reported yet.", "服务端尚未返回工具连接状态。")
            : activeConnections
              ? t(`${activeConnections} tool connection${activeConnections === 1 ? " is" : "s are"} active.`, `当前有 ${activeConnections} 个有效的工具连接。`)
              : t("No tool connection has been authorized for this account.", "这个账户还没有授权任何工具连接。")}</p>
          {!activeConnections && <div className="hero-actions"><a className="button primary" href="/console/connect/codex">{t("Connect Codex", "连接 Codex")}</a><a className="button secondary" href="/console/connect/deepseek-harness">{t("Connect Harness", "连接 Harness")}</a></div>}
        </section>

        <section aria-labelledby="quota-source-title">
          <div className="personal-section-heading">
            <div><span>{t("USAGE SOURCE", "用量来源")}</span><h2 id="quota-source-title">{t("Server-authoritative usage", "以服务端数据为准")}</h2></div>
          </div>
          <p>{t(
            "Usage and remaining quota come from the TMCRA service. If a value is not reported, the console leaves it blank instead of estimating it locally.",
            "用量和剩余额度都来自 TMCRA 服务端。某项数据尚未返回时，控制台会明确留空，不会在本地估算。",
          )}</p>
        </section>
      </div>
    </div>
  );
}

function MemoryView({
  rows,
  t,
  language,
  onCatalogChanged,
}: {
  rows: NormalizedCatalogRow[];
  t: Translate;
  language: Language;
  onCatalogChanged: () => Promise<void>;
}) {
  const scopeRows = useMemo(() => rows.filter((row) => row.kind === "scope"), [rows]);
  const [selectedScope, setSelectedScope] = useState("");
  const [graphMode, setGraphMode] = useState<"sessions" | "layers">("sessions");
  const [sessionRows, setSessionRows] = useState<NormalizedCatalogRow[]>([]);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [sessionNotice, setSessionNotice] = useState<{
    tone: "working" | "success" | "error";
    text: string;
  } | null>(null);
  const effectiveScope = scopeRows.some((row) => row.identifier === selectedScope)
    ? selectedScope
    : scopeRows.find((row) => row.identifier.endsWith("-global"))?.identifier ??
      scopeRows[0]?.identifier ??
      "";
  const selectedScopeRow = scopeRows.find((row) => row.identifier === effectiveScope) ?? null;
  const isGlobalScope = Boolean(selectedScopeRow?.identifier.endsWith("-global"));
  const boundaryRows = useMemo(() => rows.filter((row) => row.kind !== "session"), [rows]);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      if (!effectiveScope || isGlobalScope) {
        if (!cancelled) {
          setSessionRows([]);
          setSessionError(null);
        }
        return;
      }
      if (!cancelled) {
        setSessionLoading(true);
        setSessionError(null);
      }
      try {
        const response = await fetch(
          `/api/personal/sessions?scope=${encodeURIComponent(effectiveScope)}`,
          { headers: { Accept: "application/json" }, cache: "no-store" },
        );
        const body = await readResponse(
          response,
          t("We could not load Sessions for this Scope.", "暂时无法读取这个 Scope 下的 Session。"),
        );
        if (!cancelled) {
          setSessionRows(normalizeCatalog(Array.isArray(body.sessions) ? body.sessions as CatalogItem[] : [], "session"));
        }
      } catch (caught) {
        if (!cancelled) {
          setSessionRows([]);
          setSessionError(caught instanceof Error
            ? caught.message
            : t("We could not load Sessions for this Scope.", "暂时无法读取这个 Scope 下的 Session。"));
        }
      } finally {
        if (!cancelled) setSessionLoading(false);
      }
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [effectiveScope, isGlobalScope, t]);

  const deleteSession = async (row: NormalizedCatalogRow) => {
    if (!effectiveScope || deletingSessionId) return;
    const confirmed = window.confirm(t(
      `Delete Session “${row.displayName}” and all memory stored inside it? This action cannot be undone.`,
      `删除 Session“${row.displayName}”及其中保存的全部记忆吗？此操作无法撤销。`,
    ));
    if (!confirmed) return;

    setDeletingSessionId(row.identifier);
    setSessionNotice({
      tone: "working",
      text: t("Deleting this Session and rebuilding its project memory…", "正在删除这个 Session，并重新整理项目记忆…"),
    });
    try {
      const response = await fetch("/api/personal/memory-control", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "Idempotency-Key": `personal-session-delete-${crypto.randomUUID()}`,
        },
        body: JSON.stringify({ action: "session.delete", scope: effectiveScope, sessionId: row.identifier }),
      });
      const body = await readResponse(
        response,
        t("The Session deletion could not be started.", "无法启动 Session 删除任务。"),
      );
      const deletion = objectRecord(body.result);
      const deletionId = typeof deletion?.deletion_id === "string" ? deletion.deletion_id : "";
      if (!/^del_[a-f0-9]{32}$/u.test(deletionId)) {
        throw new Error(t("The service returned an invalid deletion receipt.", "服务端返回的删除凭据无效。"));
      }

      for (let attempt = 0; attempt < 90; attempt += 1) {
        const statusResponse = await fetch(
          `/api/personal/memory-control?action=deletion&scope=${encodeURIComponent(effectiveScope)}&deletionId=${encodeURIComponent(deletionId)}`,
          { headers: { Accept: "application/json" }, cache: "no-store" },
        );
        const statusBody = await readResponse(
          statusResponse,
          t("We could not confirm the deletion status.", "暂时无法确认删除状态。"),
        );
        const statusResult = objectRecord(statusBody.result);
        const state = typeof statusResult?.state === "string" ? statusResult.state : "";
        if (state === "completed") {
          setSessionRows((current) => current.filter((item) => item.identifier !== row.identifier));
          setSessionNotice({
            tone: "success",
            text: t("The Session and its stored memory have been deleted.", "这个 Session 及其中保存的记忆已经删除。"),
          });
          await onCatalogChanged();
          return;
        }
        if (state === "failed") {
          throw new Error(t("The deletion failed. No local success state was recorded.", "删除任务失败，页面没有把它标记为成功。"));
        }
        await new Promise((resolve) => window.setTimeout(resolve, 1_000));
      }
      throw new Error(t("Deletion is still running. Refresh this page to check again.", "删除任务仍在进行，请刷新页面查看最新状态。"));
    } catch (caught) {
      setSessionNotice({
        tone: "error",
        text: caught instanceof Error ? caught.message : t("The Session could not be deleted.", "这个 Session 暂时无法删除。"),
      });
    } finally {
      setDeletingSessionId(null);
    }
  };

  return (
    <section className="personal-memory-surface" aria-labelledby="memory-title">
      <div className="personal-section-heading">
        <div><span>{t("02 / MEMORY", "02 / 记忆")}</span><h2 id="memory-title">{t("Memory visualization", "记忆可视化")}</h2></div>
        <p>{t("Slow, Fast, and Source layers", "Slow、Fast 与 Source 三层记忆")}</p>
      </div>

      {boundaryRows.length ? (
        <div className="table-shell" aria-label={t("Memory boundaries", "记忆边界")}>
          <table className="data-table">
            <thead><tr>
              <th>{t("Level", "层级")}</th>
              <th>{t("Name", "名称")}</th>
              <th>Scope / ID</th>
              <th>{t("Parent", "归属")}</th>
              <th>{t("Status", "状态")}</th>
              <th>{t("Last activity", "最近活动")}</th>
            </tr></thead>
            <tbody>{boundaryRows.map((row) => (
              <tr key={row.key}>
                <td>{catalogKindLabel(row.kind, t)}</td>
                <td><b>{row.displayName}</b></td>
                <td><code>{row.identifier}</code></td>
                <td><code>{row.parent || "—"}</code></td>
                <td>{row.status ? localizedStatus(row.status, t) : "—"}</td>
                <td>{formatDate(row.updatedAt, language, t("Not reported", "尚未返回"))}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : (
        <p className="panel-empty">{t(
          "No Scope, project, or Session catalog has been reported yet. The graph below will populate after Codex writes the first memory.",
          "服务端尚未返回 Scope、项目或 Session 目录。Codex 首次写入记忆后，下方图谱会开始显示内容。",
        )}</p>
      )}

      {scopeRows.length > 0 && (
        <div className="personal-scope-picker">
          <label htmlFor="personal-memory-scope">{t("MEMORY SCOPE", "查看范围")}</label>
          <select id="personal-memory-scope" value={effectiveScope} onChange={(event) => setSelectedScope(event.target.value)}>
            {scopeRows.map((row) => <option key={row.identifier} value={row.identifier}>{row.displayName}</option>)}
          </select>
          <small className={sessionError ? "is-error" : ""} aria-live="polite">
            {isGlobalScope
              ? t("Account-level Global Scope stores durable user facts and preferences; Sessions exist only inside a Project Scope.", "账户级 Global Scope 保存长期用户事实与偏好；Session 只存在于 Project Scope 内。")
              : sessionLoading
              ? t("Loading Sessions for this Scope…", "正在读取这个 Scope 下的 Session…")
              : sessionError ?? t(`${sessionRows.length} Session record${sessionRows.length === 1 ? "" : "s"} loaded for this Scope.`, `已读取这个 Scope 下的 ${sessionRows.length} 条 Session 记录。`)}
          </small>
        </div>
      )}

      {!isGlobalScope && effectiveScope && (
        <section className="personal-session-boundary" aria-labelledby="project-sessions-title">
          <header>
            <div><span>PROJECT SCOPE / SESSION</span><h3 id="project-sessions-title">{t("Sessions inside this project", "当前项目内的 Session")}</h3></div>
            <code>{effectiveScope}</code>
          </header>
          {sessionRows.length ? (
            <div className="table-shell">
              <table className="data-table">
                <thead><tr><th>Session</th><th>{t("Parent project scope", "所属项目范围")}</th><th>{t("Status", "状态")}</th><th>{t("Last activity", "最近活动")}</th><th>{t("Action", "操作")}</th></tr></thead>
                <tbody>{sessionRows.map((row) => (
                  <tr key={row.key}>
                    <td><b>{row.displayName}</b><br /><code>{row.identifier}</code></td>
                    <td><code>{effectiveScope}</code></td>
                    <td>{row.status ? localizedStatus(row.status, t) : "—"}</td>
                    <td>{formatDate(row.updatedAt, language, t("Not reported", "尚未返回"))}</td>
                    <td>
                      <button
                        type="button"
                        className="session-delete-button"
                        disabled={Boolean(deletingSessionId)}
                        onClick={() => void deleteSession(row)}
                      >
                        {deletingSessionId === row.identifier
                          ? t("Deleting…", "正在删除…")
                          : t("Delete Session", "删除 Session")}
                      </button>
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          ) : !sessionLoading && !sessionError ? <p>{t("No Session has been reported for this Project Scope.", "这个 Project Scope 下还没有 Session。")}</p> : null}
          {sessionNotice && (
            <p
              className={`session-delete-notice is-${sessionNotice.tone}`}
              role={sessionNotice.tone === "error" ? "alert" : "status"}
              aria-live="polite"
            >
              {sessionNotice.text}
            </p>
          )}
        </section>
      )}

      <div className="personal-graph-mode" role="tablist" aria-label={t("Memory graph view", "记忆图谱视图")}>
        <button
          type="button"
          role="tab"
          aria-selected={graphMode === "sessions"}
          className={graphMode === "sessions" ? "is-active" : ""}
          onClick={() => setGraphMode("sessions")}
        >
          {t("Visual memory atlas", "可视记忆星图")}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={graphMode === "layers"}
          className={graphMode === "layers" ? "is-active" : ""}
          onClick={() => setGraphMode("layers")}
        >
          {t("Memory layers", "记忆分层")}
        </button>
      </div>

      {graphMode === "sessions" ? (
        effectiveScope ? <VisualMemoryAtlasExplorer scopeName={effectiveScope} /> : null
      ) : (
        <MemoryExplorer
          enabled
          narrativeView
          graphEndpoint="/api/personal/graph"
          requestContext={effectiveScope ? { scope: effectiveScope } : PERSONAL_GRAPH_CONTEXT}
          agentId="personal"
          fallbackEvents={[]}
          fallbackEdges={[]}
        />
      )}
    </section>
  );
}

function CodexView({
  connections,
  busy,
  language,
  t,
  onRevoke,
}: {
  connections: Connection[] | undefined;
  busy: boolean;
  language: Language;
  t: Translate;
  onRevoke: (connection: Connection) => void;
}) {
  return (
    <section className="personal-memory-surface" aria-labelledby="codex-title">
      <div className="personal-section-heading">
        <div><span>{t("03 / TOOL CONNECTIONS", "03 / 工具连接")}</span><h2 id="codex-title">{t("Authorized installations", "已授权的工具")}</h2></div>
        <div className="hero-actions"><a className="button primary" href="/console/connect/codex">{t("Connect Codex", "连接 Codex")}</a><a className="button secondary" href="/console/connect/deepseek-harness">{t("Connect Harness", "连接 Harness")}</a></div>
      </div>
      <p>{t(
        "Each tool connection receives its own scoped Token. Revoking one Token does not affect your other tools or stored memory.",
        "每个工具连接都有独立、受 Scope 限制的 Token。吊销其中一个 Token 不会影响其他工具，也不会删除已经保存的记忆。",
      )}</p>

      {connections === undefined ? (
        <section className="empty-state">
          <span className="empty-code" aria-hidden="true">03</span>
          <div><span className="system-label">{t("NOT REPORTED", "尚未返回")}</span><h2>{t("Connection data is not available yet", "连接信息暂不可用")}</h2><p>{t("Refresh after the control service is connected.", "控制服务接通后，请刷新页面重试。")}</p></div>
        </section>
      ) : connections.length ? (
        <div className="table-shell">
          <table className="data-table">
            <thead><tr>
              <th>{t("Connection", "连接")}</th>
              <th>{t("Status", "状态")}</th>
              <th>{t("Authorized", "授权时间")}</th>
              <th>{t("First connected", "首次连接")}</th>
              <th>{t("Expires", "到期时间")}</th>
              <th>{t("Action", "操作")}</th>
            </tr></thead>
            <tbody>{connections.map((connection) => {
              const status = (connection.status ?? "unknown").toLowerCase();
              const displayStatus = connection.revocationPending ? "revocation_pending" : status;
              const revocable = !["revoked", "expired"].includes(status);
              return (
                <tr key={connection.id}>
                  <td><b>{connection.displayName || providerLabel(connection.provider, t)}</b><code>{providerLabel(connection.provider, t)} · {connection.id}</code></td>
                  <td>{localizedStatus(displayStatus, t)}</td>
                  <td>{formatDate(connection.createdAt, language, t("Not reported", "尚未返回"))}</td>
                  <td>{formatDate(connection.firstConnectedAt ?? connection.lastConnectedAt, language, t("Not connected yet", "尚未完成连接"))}</td>
                  <td>{formatDate(connection.expiresAt, language, t("No expiry reported", "尚未返回到期时间"))}</td>
                  <td><button className="table-action danger" type="button" disabled={busy || !revocable} onClick={() => onRevoke(connection)}>{t("Revoke", "吊销")}</button></td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
      ) : (
        <section className="empty-state">
          <span className="empty-code" aria-hidden="true">03</span>
          <div>
            <span className="system-label">{t("NO CONNECTIONS", "还没有连接")}</span>
            <h2>{t("Connect a tool without copying an API Key", "无需复制 API Key，也能连接工具")}</h2>
            <p>{t("Start device authorization in the tool, then approve it here. The scoped Token is delivered directly to that installation.", "在工具中发起设备授权，再回到这里确认。受限 Token 会直接交付给对应安装，无需手动复制。")}</p>
            <div className="hero-actions"><a className="button primary" href="/console/connect/codex">Codex</a><a className="button secondary" href="/console/connect/deepseek-harness">DeepSeek Harness</a></div>
          </div>
        </section>
      )}
    </section>
  );
}

function providerLabel(provider: Connection["provider"], t: Translate) {
  return provider === "deepseek_harness" ? "DeepSeek Harness" : t("Codex connection", "Codex 连接");
}

function UsageView({
  quota,
  metrics,
  language,
  t,
}: {
  quota: Quota | null | undefined;
  metrics: NormalizedQuotaMetric[];
  language: Language;
  t: Translate;
}) {
  return (
    <section className="personal-memory-surface" aria-labelledby="usage-title">
      <div className="personal-section-heading">
        <div><span>{t("04 / USAGE AND QUOTA", "04 / 用量与额度")}</span><h2 id="usage-title">{t("Service usage", "服务用量")}</h2></div>
        <p>{t("Reported by the TMCRA service", "数据来自 TMCRA 服务端")}</p>
      </div>

      {quota ? (
        <div className="metric-strip" aria-label={t("Plan and quota status", "方案与额度状态")}>
          <SummaryMetric value={quota.plan?.trim() || "—"} label={t("Plan", "当前方案")} />
          <SummaryMetric value={quota.status ? localizedStatus(quota.status, t) : "—"} label={t("Quota status", "额度状态")} />
          <SummaryMetric value={String(metrics.length)} label={t("Reported metrics", "已返回指标")} />
        </div>
      ) : null}

      {!quota ? (
        <section className="empty-state">
          <span className="empty-code" aria-hidden="true">04</span>
          <div><span className="system-label">{t("NO QUOTA DATA", "暂无额度数据")}</span><h2>{t("Usage has not been reported yet", "服务端尚未返回用量")}</h2><p>{t("The console will not estimate usage locally. Refresh after the quota service is enabled for this account.", "控制台不会在本地估算用量。为该账户启用额度服务后，请刷新页面查看。")}</p></div>
        </section>
      ) : metrics.length ? (
        <div className="table-shell">
          <table className="data-table">
            <thead><tr>
              <th>{t("Metric", "指标")}</th>
              <th>{t("Used", "已使用")}</th>
              <th>{t("Limit", "额度上限")}</th>
              <th>{t("Remaining", "剩余")}</th>
            </tr></thead>
            <tbody>{metrics.map((metric) => (
              <tr key={metric.id}>
                <td><b>{quotaMetricLabel(metric, t)}</b><code>{metric.id}</code></td>
                <td>{formatAmount(metric.used, language)}</td>
                <td>{metric.unlimited ? t("Unlimited", "不设上限") : formatAmount(metric.limit, language)}</td>
                <td>{metric.unlimited ? t("Unlimited", "不设上限") : formatAmount(metric.remaining, language)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : (
        <p className="panel-empty">{t("The quota record exists, but no usage metrics have been reported.", "额度记录已建立，但服务端尚未返回任何用量指标。")}</p>
      )}
      <PersonalUsageLedger language={language} t={t} />
    </section>
  );
}

function PrivacyView({
  retention,
  retentionEnabled,
  inactiveDays,
  rating,
  comment,
  exportState,
  busy,
  language,
  t,
  onRetentionEnabled,
  onInactiveDays,
  onRating,
  onComment,
  onRunAction,
}: {
  retention: Retention | null | undefined;
  retentionEnabled: boolean;
  inactiveDays: number;
  rating: (typeof RATINGS)[number];
  comment: string;
  exportState: ExportState | null;
  busy: boolean;
  language: Language;
  t: Translate;
  onRetentionEnabled: (enabled: boolean) => void;
  onInactiveDays: (days: number) => void;
  onRating: (rating: (typeof RATINGS)[number]) => void;
  onComment: (comment: string) => void;
  onRunAction: (action: string, payload: Record<string, unknown>, idempotencyKey?: string) => Promise<Record<string, unknown> | null>;
}) {
  return (
    <div className="personal-governance">
      <section aria-labelledby="retention-title">
        <div className="personal-section-heading">
          <div><span>{t("05A / RETENTION", "05A / 保留策略")}</span><h2 id="retention-title">{t("Automatic cleanup", "自动清理")}</h2></div>
          <b>{retention?.updated_at
            ? formatDate(retention.updated_at, language, t("Updated", "已更新"))
            : t("No custom policy reported", "尚未返回自定义策略")}</b>
        </div>
        <form onSubmit={(event) => {
          event.preventDefault();
          void onRunAction("retention.set", { enabled: retentionEnabled, inactiveDays });
        }}>
          <label className="personal-toggle">
            <input type="checkbox" checked={retentionEnabled} onChange={(event) => onRetentionEnabled(event.target.checked)} />
            <span>{t("Delete memory after a long period without activity", "连续长期未活跃后自动删除记忆")}</span>
          </label>
          <label>
            <span>{t("INACTIVE DAYS", "连续未活跃天数")}</span>
            <input
              type="number"
              min={1}
              max={3650}
              value={inactiveDays}
              aria-describedby="retention-help"
              onChange={(event) => onInactiveDays(Number(event.target.value))}
            />
          </label>
          <small id="retention-help">{t("No automatic cleanup runs while this policy is disabled.", "关闭此策略后，系统不会自动清理记忆。")}</small>
          <button type="submit" disabled={busy}>{t("Save policy", "保存策略")}</button>
        </form>
      </section>

      <section aria-labelledby="export-title">
        <div className="personal-section-heading">
          <div><span>{t("05B / PORTABILITY", "05B / 数据可携带")}</span><h2 id="export-title">{t("Memory export", "记忆导出")}</h2></div>
        </div>
        <p>{t("Create a portable copy of your memory data. The export is prepared in the background.", "创建一份可携带的记忆数据副本。导出任务会在后台处理。")}</p>
        <button type="button" disabled={busy || exportState?.status === "pending"} onClick={() => void onRunAction("export.start", {}, `personal-export-${crypto.randomUUID()}`)}>
          {exportState?.status === "pending" ? t("Preparing export…", "正在准备导出…") : t("Start export", "开始导出")}
        </button>
        {exportState && (
          <div className={`personal-export-status is-${exportState.status}`} aria-live="polite">
            <b>{exportStatusLabel(exportState.status, t)}</b>
            {exportState.status === "ready" && exportState.downloadUrl && (
              <>
                <small>{t(
                  `ZIP file${typeof exportState.sizeBytes === "number" ? ` · ${formatBytes(exportState.sizeBytes, language)}` : ""}${exportState.expiresAt ? ` · expires ${formatDate(exportState.expiresAt, language, "")}` : ""}`,
                  `ZIP 文件${typeof exportState.sizeBytes === "number" ? ` · ${formatBytes(exportState.sizeBytes, language)}` : ""}${exportState.expiresAt ? ` · ${formatDate(exportState.expiresAt, language, "")} 过期` : ""}`,
                )}</small>
                <a className="button primary" href={exportState.downloadUrl} download>
                  {t("Download ZIP", "下载 ZIP")}
                </a>
              </>
            )}
            {exportState.status === "failed" && <small>{t("No file was created. Start a new export to try again.", "本次没有生成文件，可以重新发起导出。")}</small>}
            {exportState.status === "expired" && <small>{t("The temporary ZIP is no longer available. Start a new export.", "临时 ZIP 文件已失效，请重新导出。")}</small>}
          </div>
        )}
      </section>

      <section aria-labelledby="feedback-title">
        <div className="personal-section-heading">
          <div><span>{t("05C / MEMORY QUALITY", "05C / 记忆质量")}</span><h2 id="feedback-title">{t("Give memory feedback", "反馈记忆质量")}</h2></div>
          <p>{t("Feedback helps correct future recall", "反馈将用于改进后续召回")}</p>
        </div>
        <form onSubmit={(event) => {
          event.preventDefault();
          void onRunAction("feedback.submit", { rating, comment }).then((result) => {
            if (result) onComment("");
          });
        }}>
          <label>
            <span>{t("RATING", "反馈类型")}</span>
            <select value={rating} onChange={(event) => onRating(event.target.value as (typeof RATINGS)[number])}>
              {RATINGS.map((value) => <option key={value} value={value}>{ratingLabel(value, t)}</option>)}
            </select>
          </label>
          <label>
            <span>{t("COMMENT", "补充说明")}</span>
            <textarea
              maxLength={4000}
              value={comment}
              placeholder={t("What should TMCRA remember differently?", "请说明哪部分记忆需要纠正或补充。")}
              onChange={(event) => onComment(event.target.value)}
            />
          </label>
          <button type="submit" disabled={busy}>{t("Submit feedback", "提交反馈")}</button>
        </form>
      </section>
    </div>
  );
}

function SummaryMetric({ value, label }: { value: string; label: string }) {
  return <div><b>{value}</b><small>{label}</small></div>;
}

type NormalizedCatalogRow = {
  key: string;
  kind: "scope" | "project" | "session";
  displayName: string;
  identifier: string;
  parent: string | null;
  status: string | null;
  updatedAt: DateValue;
};

function normalizeCatalog(items: CatalogItem[] | undefined, kind: NormalizedCatalogRow["kind"]): NormalizedCatalogRow[] {
  if (!Array.isArray(items)) return [];
  return items.map((item, index) => {
    if (typeof item === "string") {
      return { key: `${kind}-${item}-${index}`, kind, displayName: item, identifier: item, parent: null, status: null, updatedAt: null };
    }
    const identifier = item.scopeName ?? item.scope_name ?? item.id ?? item.name ?? `${kind}-${index + 1}`;
    return {
      key: `${kind}-${identifier}-${index}`,
      kind,
      displayName: item.displayName ?? item.name ?? identifier,
      identifier,
      parent: item.projectId ?? item.project_id ?? (kind === "session" ? item.scopeName ?? item.scope_name ?? null : null),
      status: item.status ?? null,
      updatedAt: item.updatedAt ?? item.updated_at ?? item.createdAt ?? item.created_at ?? null,
    };
  });
}

type NormalizedQuotaMetric = QuotaMetric & { id: string };

function normalizeQuotaMetrics(metrics: Quota["metrics"]): NormalizedQuotaMetric[] {
  if (!metrics) return [];
  if (Array.isArray(metrics)) {
    return metrics.map((metric, index) => ({ ...metric, id: metric.id ?? metric.name ?? `metric-${index + 1}` }));
  }
  return Object.entries(metrics).map(([id, metric]) => ({ ...metric, id }));
}

function quotaMetricLabel(metric: NormalizedQuotaMetric, t: Translate) {
  if (metric.label) return metric.label;
  if (metric.id === "ingest_raw_tokens") return t("Ingested Token volume", "写入 Token 量");
  if (metric.id === "recall_requests") return t("Recall requests", "召回请求数");
  if (metric.id === "memory_writes") return t("Memory writes", "记忆写入次数");
  return metric.name || metric.id.replaceAll("_", " ");
}

function catalogKindLabel(kind: NormalizedCatalogRow["kind"], t: Translate) {
  if (kind === "project") return t("Project", "项目");
  if (kind === "session") return "Session";
  return "Scope";
}

function ratingLabel(rating: (typeof RATINGS)[number], t: Translate) {
  const labels: Record<(typeof RATINGS)[number], [string, string]> = {
    helpful: ["Helpful", "有帮助"],
    incorrect: ["Incorrect", "内容不正确"],
    stale: ["Out of date", "信息已过期"],
    unsafe: ["Should not be retained", "不应保留"],
    missing: ["Missing key context", "缺少关键信息"],
  };
  return t(...labels[rating]);
}

function localizedStatus(value: string, t: Translate) {
  const status = value.trim().toLowerCase();
  const labels: Record<string, [string, string]> = {
    active: ["Active", "可用"],
    ready: ["Ready", "已就绪"],
    connected: ["Connected", "已连接"],
    pending: ["Pending", "等待处理"],
    pending_approval: ["Pending approval", "等待授权"],
    revocation_pending: ["Revocation pending", "正在完成吊销"],
    revoked: ["Revoked", "已吊销"],
    expired: ["Expired", "已过期"],
    disabled: ["Disabled", "已停用"],
    suspended: ["Suspended", "已暂停"],
    unlimited: ["Unlimited", "不设上限"],
    unknown: ["Not reported", "尚未返回"],
  };
  const label = labels[status];
  return label ? t(...label) : value;
}

function arrayCount(value: unknown[] | undefined) {
  return Array.isArray(value) ? String(value.length) : "—";
}

function objectRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function formatAmount(value: number | null | undefined, language: Language) {
  return typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat(language === "zh" ? "zh-CN" : "en-US").format(value)
    : "—";
}

async function readResponse(response: Response, fallbackMessage: string): Promise<Record<string, unknown>> {
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok || !body || typeof body !== "object" || Array.isArray(body)) {
    throw new Error(fallbackMessage);
  }
  const value = body as Record<string, unknown>;
  if (value.ok !== true) throw new Error(fallbackMessage);
  return value;
}

function exportJobId(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const jobId = (value as Record<string, unknown>).job_id;
  return typeof jobId === "string" && /^[a-f0-9]{32}$/.test(jobId) ? jobId : null;
}

function normalizeExportState(value: unknown, expectedJobId: string): ExportState {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid export status");
  const record = value as Record<string, unknown>;
  const jobId = exportJobId({ job_id: record.jobId });
  const status = record.status;
  if (jobId !== expectedJobId || !["pending", "ready", "failed", "expired"].includes(String(status))) {
    throw new Error("Invalid export status");
  }
  const downloadUrl = typeof record.downloadUrl === "string" && record.downloadUrl.startsWith("/api/personal/export/download?")
    ? record.downloadUrl
    : null;
  if (status === "ready" && !downloadUrl) throw new Error("Invalid export download");
  return {
    jobId,
    status: status as ExportState["status"],
    exportId: typeof record.exportId === "string" ? record.exportId : undefined,
    downloadUrl,
    expiresAt: typeof record.expiresAt === "number" ? record.expiresAt : null,
    sizeBytes: typeof record.sizeBytes === "number" ? record.sizeBytes : null,
  };
}

function exportStatusLabel(status: ExportState["status"], t: Translate) {
  if (status === "ready") return t("Export ready", "导出文件已就绪");
  if (status === "failed") return t("Export failed", "导出失败");
  if (status === "expired") return t("Export expired", "导出文件已过期");
  return t("Preparing export", "正在准备导出文件");
}

function formatBytes(value: number, language: Language) {
  if (!Number.isFinite(value) || value < 0) return "—";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB"];
  let amount = value / 1024;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${new Intl.NumberFormat(language === "zh" ? "zh-CN" : "en-US", { maximumFractionDigits: 1 }).format(amount)} ${units[index]}`;
}

function formatDate(value: DateValue | undefined, language: Language, fallback: string) {
  if (value === undefined || value === null || value === "") return fallback;
  const normalized = typeof value === "number" && value < 10_000_000_000 ? value * 1000 : value;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime())
    ? fallback
    : date.toLocaleString(language === "zh" ? "zh-CN" : "en-US");
}
