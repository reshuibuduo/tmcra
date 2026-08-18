"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Language } from "../i18n";

type Translate = (english: string, chinese: string) => string;
type UsageGroup = "platform" | "integration" | "agent" | "attribution_source";
type UsageBucket = {
  key: string;
  input_tokens: number;
  output_tokens: number;
  ingest_raw_tokens: number;
  recall_requests: number;
  known_cost_cny: number;
};
type UsageLedger = {
  group_by: UsageGroup;
  currency: string;
  complete_for_registered_calls: boolean;
  uncertain_cost_call_count: number;
  known_cost_cny: number;
  quota_events: { ingest_raw_tokens: number; recall_requests: number };
  calls: { input_tokens: number; output_tokens: number };
  buckets: UsageBucket[];
};

const GROUPS: Array<{ id: UsageGroup; en: string; zh: string }> = [
  { id: "platform", en: "Client platform", zh: "接入平台" },
  { id: "integration", en: "Connection instance", zh: "连接实例" },
  { id: "agent", en: "Agent", zh: "Agent" },
  { id: "attribution_source", en: "Trust source", zh: "归因来源" },
];

export default function PersonalUsageLedger({ language, t }: { language: Language; t: Translate }) {
  const [groupBy, setGroupBy] = useState<UsageGroup>("platform");
  const [ledger, setLedger] = useState<UsageLedger | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSequence = useRef(0);

  const load = useCallback(async () => {
    const request = ++requestSequence.current;
    setLoading(true);
    setError(null);
    try {
      const parameters = new URLSearchParams({ action: "usage", groupBy });
      const response = await fetch(`/api/personal/memory-control?${parameters}`, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      const body = await response.json().catch(() => null) as unknown;
      if (!response.ok) throw new Error(publicError(body, t));
      const next = normalizeLedger(body, groupBy);
      if (!next) throw new Error(t(
        "The usage service returned an invalid response.",
        "用量服务返回的数据格式不完整。",
      ));
      if (request === requestSequence.current) setLedger(next);
    } catch (caught) {
      if (request === requestSequence.current) setError(caught instanceof Error
        ? caught.message
        : t("Usage could not be loaded.", "暂时无法读取用量。"));
    } finally {
      if (request === requestSequence.current) setLoading(false);
    }
  }, [groupBy, t]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const currentLedger = ledger?.group_by === groupBy ? ledger : null;
  const modelTokens = currentLedger
    ? currentLedger.calls.input_tokens + currentLedger.calls.output_tokens
    : null;

  return (
    <section className="personal-usage-ledger" aria-labelledby="usage-ledger-title">
      <div className="personal-usage-ledger-head">
        <div>
          <span>{t("SERVER LEDGER", "服务端账本")}</span>
          <h3 id="usage-ledger-title">{t("Usage attribution", "用量归因")}</h3>
        </div>
        <div className="personal-usage-ledger-controls">
          <label>
            <span>{t("Group by", "分组方式")}</span>
            <select value={groupBy} disabled={loading} onChange={(event) => setGroupBy(event.target.value as UsageGroup)}>
              {GROUPS.map((group) => <option key={group.id} value={group.id}>{t(group.en, group.zh)}</option>)}
            </select>
          </label>
          <button type="button" disabled={loading} onClick={() => void load()}>
            {loading ? t("Loading…", "读取中…") : t("Refresh ledger", "刷新账本")}
          </button>
        </div>
      </div>

      <p className={`personal-usage-ledger-status${error ? " is-error" : loading ? " is-loading" : " is-ready"}`} role="status" aria-live="polite">
        {error ?? (loading
          ? t("Reading the server ledger…", "正在读取服务端账本…")
          : currentLedger
            ? t("Server-confirmed usage loaded.", "已读取服务端确认的用量。")
            : t("No usage record has been returned yet.", "服务端尚未返回用量记录。"))}
      </p>

      <div className="personal-usage-ledger-summary" aria-label={t("Ledger totals", "账本总量")}>
        <Summary value={formatNumber(currentLedger?.quota_events.ingest_raw_tokens, language)} label={t("Ingest tokens", "写入 Token")} />
        <Summary value={formatNumber(currentLedger?.quota_events.recall_requests, language)} label={t("Recall requests", "召回次数")} />
        <Summary value={formatNumber(modelTokens, language)} label={t("Model tokens", "模型 Token")} />
        <Summary value={formatCost(currentLedger?.known_cost_cny, currentLedger?.currency, language)} label={t("Known model API cost", "已知模型 API 成本")} />
      </div>

      <p className="personal-usage-ledger-coverage">
        {currentLedger
          ? currentLedger.complete_for_registered_calls
            ? t("Every registered provider call has a complete cost state.", "已登记的 Provider 调用均有完整成本状态。")
            : t(
              `${currentLedger.uncertain_cost_call_count} registered calls are still running, unpriced, or unknown.`,
              `还有 ${currentLedger.uncertain_cost_call_count} 次已登记调用处于运行中、未定价或未知状态。`,
            )
          : t("Cost completeness will appear after the ledger loads.", "账本读取完成后会显示成本完整度。")}
        {" "}
        {t(
          "Account totals drive quota and billing. Connection and Agent groups are operational attribution.",
          "额度与计费以账户总账本为准；连接和 Agent 分组用于归因与排查。",
        )}
      </p>

      {currentLedger?.buckets.length ? (
        <div className="table-shell personal-usage-ledger-table">
          <table className="data-table">
            <thead><tr>
              <th>{t("Group", "分组")}</th>
              <th>{t("Ingest tokens", "写入 Token")}</th>
              <th>{t("Recall requests", "召回次数")}</th>
              <th>{t("Model tokens", "模型 Token")}</th>
              <th>{t("Known model API cost", "已知模型 API 成本")}</th>
            </tr></thead>
            <tbody>{currentLedger.buckets.map((bucket) => (
              <tr key={bucket.key}>
                <td><b>{bucketLabel(bucket.key, t)}</b><code>{bucket.key}</code></td>
                <td>{formatNumber(bucket.ingest_raw_tokens, language)}</td>
                <td>{formatNumber(bucket.recall_requests, language)}</td>
                <td>{formatNumber(bucket.input_tokens + bucket.output_tokens, language)}</td>
                <td>{formatCost(bucket.known_cost_cny, currentLedger.currency, language)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : (
        <p className="panel-empty">{loading
          ? t("Loading the selected grouping…", "正在读取所选分组…")
          : t("No ledger records exist in this grouping.", "当前分组内暂无账本记录。")}</p>
      )}
    </section>
  );
}

function Summary({ value, label }: { value: string; label: string }) {
  return <div><b>{value}</b><small>{label}</small></div>;
}

function normalizeLedger(value: unknown, expectedGroup: UsageGroup): UsageLedger | null {
  let current = record(value);
  for (let depth = 0; depth < 3 && current; depth += 1) {
    if (current.ok === true && record(current.result)) current = record(current.result);
    else break;
  }
  if (!current || current.group_by !== expectedGroup || !Array.isArray(current.buckets)) return null;
  const quota = record(current.quota_events);
  const calls = record(current.calls);
  if (!quota || !calls || typeof current.complete_for_registered_calls !== "boolean") return null;
  const values = {
    ingest: nonNegative(quota.ingest_raw_tokens), recalls: nonNegative(quota.recall_requests),
    input: nonNegative(calls.input_tokens), output: nonNegative(calls.output_tokens),
    uncertain: nonNegative(current.uncertain_cost_call_count), cost: nonNegative(current.known_cost_cny),
  };
  if (Object.values(values).some((item) => item === null)) return null;
  const buckets = current.buckets.map(normalizeBucket);
  if (buckets.some((bucket) => bucket === null)) return null;
  return {
    group_by: expectedGroup,
    currency: typeof current.currency === "string" && /^[A-Z]{3}$/u.test(current.currency) ? current.currency : "CNY",
    complete_for_registered_calls: current.complete_for_registered_calls,
    uncertain_cost_call_count: values.uncertain!, known_cost_cny: values.cost!,
    quota_events: { ingest_raw_tokens: values.ingest!, recall_requests: values.recalls! },
    calls: { input_tokens: values.input!, output_tokens: values.output! },
    buckets: buckets as UsageBucket[],
  };
}

function normalizeBucket(value: unknown): UsageBucket | null {
  const source = record(value);
  if (!source || typeof source.key !== "string" || !source.key.trim()) return null;
  const values = {
    input_tokens: nonNegative(source.input_tokens), output_tokens: nonNegative(source.output_tokens),
    ingest_raw_tokens: nonNegative(source.ingest_raw_tokens), recall_requests: nonNegative(source.recall_requests),
    known_cost_cny: nonNegative(source.known_cost_cny),
  };
  if (Object.values(values).some((item) => item === null)) return null;
  return { key: source.key, ...values } as UsageBucket;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function nonNegative(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function formatNumber(value: number | null | undefined, language: Language) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "—";
  return new Intl.NumberFormat(language === "zh" ? "zh-CN" : "en-US", { maximumFractionDigits: 2 }).format(value);
}

function formatCost(value: number | null | undefined, currency: string | undefined, language: Language) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "—";
  return new Intl.NumberFormat(language === "zh" ? "zh-CN" : "en-US", {
    style: "currency", currency: currency && /^[A-Z]{3}$/u.test(currency) ? currency : "CNY",
    minimumFractionDigits: 2, maximumFractionDigits: 6,
  }).format(value);
}

function bucketLabel(key: string, t: Translate) {
  const labels: Record<string, [string, string]> = {
    trusted_proxy: ["Trusted proxy", "可信代理"], client_reported: ["Client reported", "客户端上报"],
    system_derived: ["TMCRA system", "TMCRA 系统任务"], unattributed: ["Legacy or unknown", "历史或未知"],
    codex: ["Codex", "Codex"], deepseek_harness: ["DeepSeek Harness", "DeepSeek Harness"],
    openclaw: ["OpenClaw", "OpenClaw"], hermes: ["Hermes", "Hermes"],
    claude_code: ["Claude Code", "Claude Code"], mcp: ["MCP", "MCP"], python: ["Python", "Python"],
    typescript: ["TypeScript", "TypeScript"], rest: ["REST", "REST"], tmcra_internal: ["TMCRA", "TMCRA"],
  };
  return labels[key.toLowerCase()] ? t(...labels[key.toLowerCase()]) : key;
}

function publicError(value: unknown, t: Translate) {
  const root = record(value);
  const error = record(root?.error);
  const requestId = typeof error?.requestId === "string"
    ? error.requestId : typeof error?.request_id === "string" ? error.request_id : null;
  return requestId
    ? `${t("Usage could not be loaded.", "暂时无法读取用量。")} ${t("Support ID", "支持编号")}: ${requestId}`
    : t("Usage could not be loaded.", "暂时无法读取用量。");
}
