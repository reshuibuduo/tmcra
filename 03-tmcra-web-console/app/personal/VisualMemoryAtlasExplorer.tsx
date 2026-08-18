"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { useLanguage } from "../i18n";
import VisualMemoryAtlas, {
  type VisualAtlasData,
  type VisualAtlasMemory,
  type VisualAtlasSourceEvidence,
} from "./VisualMemoryAtlas";

type UnknownRecord = Record<string, unknown>;

export type RawVisualAtlas = {
  schema_version: "tmcra.visual-atlas.1";
  scope_name: string;
  snapshot_id: string;
  projection_state: "fallback" | "building" | "ready" | "stale" | "failed";
  generated_by: string;
  model?: string | null;
  full_projection: true;
  truncated: false;
  nodes: UnknownRecord[];
  edges: UnknownRecord[];
  refresh?: { state?: string; attempts?: number; updated_at?: number; error?: string | null } | null;
};

export default function VisualMemoryAtlasExplorer({ scopeName }: { scopeName: string }) {
  const { t } = useLanguage();
  const [raw, setRaw] = useState<RawVisualAtlas | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    if (!scopeName) {
      setRaw(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const graph = await sessionGraphRequest<RawVisualAtlas>(
        { action: "visual-atlas", scope: scopeName },
        { signal },
      );
      assertRawVisualAtlas(graph);
      setRaw(graph);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setError(caught instanceof Error ? caught.message : t(
        "The visual memory atlas is unavailable.",
        "暂时无法读取可视记忆星图。",
      ));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [scopeName, t]);

  useEffect(() => {
    const controller = new AbortController();
    queueMicrotask(() => void load(controller.signal));
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    if (!raw?.refresh?.state || !["dirty", "running"].includes(raw.refresh.state)) return;
    const timer = window.setTimeout(() => void load(), 4_000);
    return () => window.clearTimeout(timer);
  }, [load, raw?.refresh?.state]);

  const data = useMemo(() => raw ? normalizeVisualAtlas(raw) : null, [raw]);
  const showingPreviousScope = Boolean(raw && raw.scope_name !== scopeName);
  const refreshState = raw?.refresh?.state ?? null;
  const visualState = error
    ? "error"
    : loading && !raw
      ? "loading"
      : showingPreviousScope
        ? "stale"
        : refreshState && ["dirty", "queued", "running"].includes(refreshState)
          ? refreshState
          : raw
            ? raw.projection_state
            : "empty";

  const refresh = async () => {
    setRefreshing(true);
    setError(null);
    try {
      await sessionGraphRequest(
        { action: "refresh-visual-atlas", scope: scopeName },
        { method: "POST" },
      );
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t(
        "The atlas refresh could not be scheduled.",
        "无法提交星图刷新任务。",
      ));
    } finally {
      setRefreshing(false);
    }
  };

  const loadSourceEvidence = useCallback(async (memory: VisualAtlasMemory) => {
    const memoryId = memory.evidence_lookup_id?.trim();
    if (!memoryId) return [];
    const search = new URLSearchParams({
      action: "evidence",
      scope: scopeName,
      memoryId,
      limit: "25",
    });
    const response = await fetch(`/api/personal/graph?${search}`, {
      credentials: "same-origin",
      cache: "no-store",
    });
    const body: unknown = await response.json().catch(() => null);
    if (!response.ok || !isRecord(body) || body.ok !== true || !isRecord(body.graph) || !Array.isArray(body.graph.items)) {
      const detail = isRecord(body) && isRecord(body.error) ? body.error : {};
      throw new Error(typeof detail.message === "string" ? detail.message : "Source evidence could not be loaded.");
    }
    return body.graph.items.filter(isRecord).map(mapSourceEvidence);
  }, [scopeName]);

  return (
    <div className="tmcra-visual-atlas-shell" data-state={visualState} data-stale={showingPreviousScope || undefined} aria-busy={loading || refreshing}>
      <div className="tmcra-visual-atlas-status" aria-live="polite">
        <div>
          <b>{showingPreviousScope
            ? t("Switching scope; previous topology remains visible", "正在切换范围；旧拓扑暂时保留")
            : projectionLabel(raw?.projection_state, raw?.refresh?.state, t)}</b>
          <span>{raw?.model ?? t("Deterministic evidence projection", "确定性证据投影")}</span>
          {raw?.refresh?.updated_at && <time dateTime={new Date(raw.refresh.updated_at * 1000).toISOString()}>
            {t("Last successful snapshot", "最近成功快照")} {new Date(raw.refresh.updated_at * 1000).toLocaleString()}
          </time>}
        </div>
        <button type="button" onClick={() => void refresh()} disabled={refreshing || loading || !scopeName}>
          {refreshing ? t("Scheduling…", "正在提交…") : t("Rebuild semantic atlas", "重建语义星图")}
        </button>
      </div>
      {error && <div className="tmcra-visual-atlas-error" role="alert">
        <b>{raw ? t("Refresh failed; the last successful topology is still shown.", "刷新失败；仍显示最近一次成功拓扑。") : t("Atlas unavailable", "图谱暂不可用")}</b>
        <span>{error}</span>
      </div>}
      {raw?.refresh?.error && !error && <div className="tmcra-visual-atlas-error" role="status">
        <b>{t("Semantic rebuild needs attention; the previous snapshot remains valid.", "语义重建需要处理；旧快照仍可查看。")}</b>
        <span>{raw.refresh.error}</span>
      </div>}
      {loading && !data && <div className="tmcra-visual-atlas-loading">{t("Loading the complete topology…", "正在读取完整拓扑…")}</div>}
      <VisualMemoryAtlas data={data} onRequestSourceEvidence={loadSourceEvidence} />
    </div>
  );
}

export function normalizeVisualAtlas(raw: RawVisualAtlas): VisualAtlasData {
  const domains = raw.nodes.filter((node) => node.level === "domain");
  const sessions = raw.nodes.filter((node) => node.level === "session");
  const episodes = raw.nodes.filter((node) => node.level === "episode");
  const evidence = raw.nodes.filter((node) => node.level === "evidence");
  const sessionNodeByExactId = new Map(
    sessions.map((node) => [text(node.session_id), text(node.id)]),
  );

  return {
    schema_version: "tmcra.visual-atlas.1",
    scope_name: raw.scope_name,
    snapshot_id: raw.snapshot_id,
    projection_state: raw.projection_state,
    generated_by: raw.generated_by,
    model: raw.model ?? null,
    galaxies: domains.map((node) => ({
      id: text(node.id),
      label: text(node.label) || "Theme galaxy",
      summary: nullableText(node.summary),
      session_ids: stringList(node.session_ids).map((id) => sessionNodeByExactId.get(id) ?? `session:${id}`),
      memory_count: numberValue(node.evidence_count),
      salience: 0.72,
    })),
    sessions: sessions.map((node) => ({
      id: text(node.id),
      galaxy_id: nullableText(node.domain_id),
      label: text(node.label) || `Session ${text(node.session_id).slice(0, 12)}`,
      summary: nullableText(node.summary),
      status: nullableText(node.status),
      chapter_ids: episodes.filter((episode) => episode.session_id === node.session_id).map((episode) => text(episode.id)),
      memory_count: numberValue(node.evidence_count),
      created_at: dateText(node.created_at),
      updated_at: dateText(node.updated_at),
      source_app: nullableText(node.source_app),
      salience: 0.64,
    })),
    chapters: episodes.map((node) => ({
      id: text(node.id),
      session_id: sessionNodeByExactId.get(text(node.session_id)) ?? `session:${text(node.session_id)}`,
      label: text(node.label) || "Conversation chapter",
      summary: nullableText(node.summary),
      memory_ids: stringList(node.evidence_ids),
      turn_start: nullableNumber(node.first_turn),
      turn_end: nullableNumber(node.last_turn),
      salience: 0.54,
    })),
    memories: evidence.map((node) => {
      const sourceRecordId = nullableText(node.source_record_id);
      const sourceRecordIds = sourceRecordId ? [sourceRecordId] : stringList(node.source_record_ids);
      const exactSessionId = stringList(node.session_ids)[0] ?? "";
      return {
        id: text(node.id),
        session_id: sessionNodeByExactId.get(exactSessionId) ?? `session:${exactSessionId}`,
        chapter_id: stringList(node.episode_ids)[0] ?? null,
        label: text(node.label) || (sourceRecordId ? "Source evidence" : "Memory evidence"),
        summary: nullableText(node.summary),
        occurred_at: dateText(node.occurred_at),
        role: nullableText(node.actor_role) ?? nullableText(node.role),
        tags: stringList(node.tags),
        source_record_ids: sourceRecordIds,
        confidence: numberValue(node.confidence, 0.82),
        salience: numberValue(node.salience, sourceRecordId ? 0.3 : 0.46),
        state: sourceRecordId ? "immutable-source" : "committed-memory",
        evidence_lookup_id: nullableText(node.memory_id) ?? sourceRecordId,
      };
    }),
    edges: raw.edges.map((edge) => ({
      id: text(edge.id),
      source: text(edge.source),
      target: text(edge.target),
      type: text(edge.type) || "related",
      weight: numberValue(edge.weight, 0.58),
      origin: edge.type === "contains" || edge.type === "parent" ? "hierarchy" : "derived",
      evidence_ids: stringList(edge.evidence_ids),
    })),
  };
}

async function sessionGraphRequest<T = UnknownRecord>(params: Record<string, string>, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/personal/session-graph?${new URLSearchParams(params)}`, {
    credentials: "same-origin",
    cache: "no-store",
    ...init,
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok || !isRecord(body) || body.ok !== true || !isRecord(body.graph)) {
    const detail = isRecord(body) && isRecord(body.error) ? body.error : {};
    throw new Error(typeof detail.message === "string" ? detail.message : "The visual atlas request failed.");
  }
  return body.graph as T;
}

function assertRawVisualAtlas(value: RawVisualAtlas) {
  if (
    value.schema_version !== "tmcra.visual-atlas.1"
    || value.full_projection !== true
    || value.truncated !== false
    || !Array.isArray(value.nodes)
    || !Array.isArray(value.edges)
  ) {
    throw new Error("The service returned an incomplete visual atlas.");
  }
}

function mapSourceEvidence(item: UnknownRecord): VisualAtlasSourceEvidence {
  return {
    source_record_id: text(item.source_record_id),
    text: text(item.text),
    role: nullableText(item.role),
    actor_role: nullableText(item.actor_role),
    occurred_at: nullableText(item.occurred_at),
    message_id: nullableText(item.message_id),
    evidence_char_start: nullableNumber(item.evidence_char_start),
    evidence_char_end: nullableNumber(item.evidence_char_end),
    text_sha256: nullableText(item.text_sha256),
  };
}

function projectionLabel(
  projection: string | undefined,
  refresh: string | undefined,
  t: (english: string, chinese: string) => string,
) {
  if (refresh === "running") return t("Rebuilding; showing the last successful topology", "正在重建；继续显示最近一次成功拓扑");
  if (refresh === "dirty" || refresh === "queued") return t("Semantic rebuild queued; current topology may be stale", "语义重建已排队；当前拓扑可能已过期");
  if (refresh === "failed") return t("Rebuild failed; showing the last successful topology", "重建失败；继续显示最近一次成功拓扑");
  if (projection === "ready") return t("Evidence-bound semantic atlas", "证据绑定的语义星图");
  return t("Deterministic atlas fallback", "确定性星图底图");
}

function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function text(value: unknown) {
  return typeof value === "string" ? value : "";
}

function nullableText(value: unknown) {
  const clean = text(value).trim();
  return clean || null;
}

function nullableNumber(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function numberValue(value: unknown, fallback = 0) {
  return nullableNumber(value) ?? fallback;
}

function stringList(value: unknown) {
  return Array.isArray(value) ? value.map(text).filter(Boolean) : [];
}

function dateText(value: unknown) {
  if (typeof value === "string" && value) return value;
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds > 0 ? new Date(seconds * 1000).toISOString() : null;
}
