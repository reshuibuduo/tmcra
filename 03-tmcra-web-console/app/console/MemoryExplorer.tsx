"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useLanguage } from "../i18n";

import MemoryGraph, {
  type GraphEvidenceItem,
  type MemoryEdge,
  type MemoryEvent,
  type MemoryLayer,
  type NarrativeGraphSummary,
  type NarrativeGraphThread,
} from "./MemoryGraph";

type UnknownRecord = Record<string, unknown>;

type GraphNodeDto = {
  id: string;
  layer: MemoryLayer;
  kind: string;
  category: string;
  label: string;
  summary: string;
  state: string;
  status: string;
  confidence: number;
  salience: number;
  occurred_at: string | null;
  subject_id: string | null;
  cluster_id: string | null;
  source_kind: string | null;
  actor_role: string | null;
  actor_roles: string[];
  authority: string | null;
  provenance_source: string | null;
  evidence_count: number;
  visible_neighbor_count: number;
  expandable: boolean;
  attributes: UnknownRecord;
};

type GraphEdgeDto = {
  id: string;
  source: string;
  target: string;
  type: string;
  weight: number;
  origin: "stored" | "derived";
  provenance: UnknownRecord;
};

type GraphDto = {
  snapshot_id: string;
  snapshot_state?: string;
  provisional?: boolean;
  view: "overview" | "neighbors" | "recall_trace" | "narrative";
  nodes: GraphNodeDto[];
  edges: GraphEdgeDto[];
  page: { next_cursor: string | null; truncated: boolean };
  threads?: NarrativeGraphThread[];
  narrative?: NarrativeGraphSummary | null;
  selected_memory_ids?: string[];
  missing_memory_ids?: string[];
  retrieval_summary?: {
    evidence_window_count?: number;
    persisted_memory_id_count?: number;
    projected_memory_id_count?: number;
  } | null;
  index_job_id?: string | null;
  query_id?: string | null;
};

type MemoryExplorerProps = {
  organizationId?: string;
  agentId?: string | null;
  sample?: boolean;
  fallbackEvents: MemoryEvent[];
  fallbackEdges: MemoryEdge[];
  graphEndpoint?: string;
  requestContext?: Record<string, string>;
  enabled?: boolean;
  narrativeView?: boolean;
};

export default function MemoryExplorer({
  organizationId,
  agentId,
  sample,
  fallbackEvents,
  fallbackEdges,
  graphEndpoint = "/api/enterprise/graph",
  requestContext,
  enabled,
  narrativeView = false,
}: MemoryExplorerProps) {
  const { t } = useLanguage();
  const [graph, setGraph] = useState<GraphDto | null>(null);
  const [overviewCursor, setOverviewCursor] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [requestState, setRequestState] = useState<"idle" | "refreshing" | "recalling">("idle");
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadingNodeId, setLoadingNodeId] = useState<string | null>(null);
  const [traceQuery, setTraceQuery] = useState("");
  const [traceActive, setTraceActive] = useState(false);
  const [evidenceByNode, setEvidenceByNode] = useState<Record<string, GraphEvidenceItem[]>>({});
  const graphEnabled = enabled ?? Boolean(organizationId && agentId && !sample);
  const loading = requestState !== "idle";
  const baseParams = useMemo(
    () => requestContext ?? (organizationId && agentId ? { organizationId, agentId } : {}),
    [agentId, organizationId, requestContext],
  );

  const loadOverview = useCallback(async (signal?: AbortSignal) => {
    if (!graphEnabled) {
      setGraph(null);
      setOverviewCursor(null);
      setRequestState("idle");
      setConnectionError(sample ? t("Sample workspace: control-plane events only.", "示例工作区只展示 control-plane 事件。") : t("Production graph is not connected.", "生产环境 Memory Graph 尚未接入。"));
      return;
    }
    setConnectionError(null);
    setRequestState("refreshing");
    try {
      const response = await graphRequest(
        graphEndpoint,
        {
          ...baseParams,
          action: narrativeView ? "narrative" : "overview",
          limit: narrativeView ? "36" : "180",
          ...(narrativeView ? { focus: "all" } : {}),
        },
        { signal },
      );
      setGraph(response);
      setOverviewCursor(response.page.next_cursor);
      setTraceActive(false);
      setEvidenceByNode({});
      setConnectionError(null);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setConnectionError(error instanceof Error ? error.message : t("Production graph is unavailable.", "暂时无法读取生产环境 Memory Graph。"));
    } finally {
      if (!signal?.aborted) setRequestState("idle");
    }
  }, [baseParams, graphEnabled, graphEndpoint, narrativeView, sample, t]);

  useEffect(() => {
    const controller = new AbortController();
    queueMicrotask(() => {
      if (!controller.signal.aborted) void loadOverview(controller.signal);
    });
    return () => controller.abort();
  }, [loadOverview]);

  const productionEvents = useMemo(
    () => graph?.nodes.map((node) => graphNodeToEvent(node, agentId ?? "")) ?? [],
    [agentId, graph],
  );
  const productionEdges = useMemo(
    () => graph?.edges.map(graphEdgeToMemoryEdge) ?? [],
    [graph],
  );
  const events = graph ? productionEvents : fallbackEvents;
  const edges = graph ? productionEdges : fallbackEdges;
  const sourceLabel = graph
    ? traceActive
      ? t(`Recall result replay / snapshot ${shortId(graph.snapshot_id)}`, `本次召回结果重放 / snapshot ${shortId(graph.snapshot_id)}`)
      : narrativeView
        ? t(`Evidence-bound storyline / snapshot ${shortId(graph.snapshot_id)}`, `可追溯记忆脉络 / snapshot ${shortId(graph.snapshot_id)}`)
        : t(`Production Slow Graph / snapshot ${shortId(graph.snapshot_id)}`, `生产环境 Slow Graph / snapshot ${shortId(graph.snapshot_id)}`)
    : t("Control-plane event projection / production graph not connected", "Control-plane 事件投影 / 生产环境 Graph 尚未接入");

  const loadMore = async () => {
    if (!graphEnabled || !overviewCursor || narrativeView) return;
    setLoadingMore(true);
    try {
      const next = await graphRequest(graphEndpoint, {
        ...baseParams,
        action: "overview",
        limit: "180",
        cursor: overviewCursor,
      });
      setGraph((current) => current ? mergeGraph(current, next) : next);
      setOverviewCursor(next.page.next_cursor);
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : t("Could not load more memory nodes.", "未能加载更多记忆节点。"));
    } finally {
      setLoadingMore(false);
    }
  };

  const expandNode = async (event: MemoryEvent) => {
    if (!graphEnabled || !graph) return;
    setLoadingNodeId(event.id);
    try {
      const expanded = await graphRequest(graphEndpoint, {
        ...baseParams,
        action: "neighbors",
        memoryId: event.id,
        depth: "1",
        limit: "120",
      });
      setGraph((current) => current ? mergeGraph(current, expanded) : expanded);
      setConnectionError(null);
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : t("Could not expand this memory node.", "未能展开这个记忆节点。"));
    } finally {
      setLoadingNodeId(null);
    }
  };

  const loadEvidence = async (event: MemoryEvent) => {
    if (!graphEnabled || !graph) return;
    setLoadingNodeId(event.id);
    try {
      const response = await graphEvidenceRequest(graphEndpoint, {
        ...baseParams,
        action: "evidence",
        memoryId: event.id,
        limit: "25",
      });
      setEvidenceByNode((current) => ({ ...current, [event.id]: response }));
      setConnectionError(null);
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : t("Could not load Source evidence.", "未能加载 Source 证据。"));
    } finally {
      setLoadingNodeId(null);
    }
  };

  const runTrace = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!graphEnabled || !traceQuery.trim() || !graph) return;
    setConnectionError(null);
    setRequestState("recalling");
    try {
      const traced = await graphRequest(
        graphEndpoint,
        { ...baseParams, action: "trace" },
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: traceQuery.trim(), max_windows: 8 }),
        },
      );
      setGraph(traced);
      setOverviewCursor(null);
      setTraceActive(true);
      setConnectionError(null);
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : t("Recall trace failed.", "召回路径追踪失败。"));
    } finally {
      setRequestState("idle");
    }
  };

  return (
    <div
      className="memory-explorer"
      data-state={connectionError && graph ? "stale" : connectionError ? "error" : requestState === "recalling" ? "running" : requestState === "refreshing" && graph ? "stale" : requestState === "refreshing" ? "loading" : graph?.provisional || graph?.missing_memory_ids?.length ? "partial" : graph ? "ready" : "empty"}
      aria-busy={loading}
    >
      <div className="graph-runtime-bar">
        <div>
          <i className={graph ? "is-connected" : ""} />
          <span>{requestState === "recalling"
            ? t("Running recall; the previous result remains visible", "正在执行召回；旧结果暂时保留")
            : requestState === "refreshing" && graph
              ? t("Refreshing; the previous snapshot remains visible", "正在刷新；旧快照暂时保留")
              : requestState === "refreshing"
                ? t("Loading production graph", "正在加载生产环境 Graph")
                : graph?.provisional
                  ? t("Partial graph result", "部分图谱结果")
                  : graph
                    ? t("Production graph connected", "生产环境 Graph 已连接")
                    : t("Control-plane projection", "Control-plane 投影")}</span>
        </div>
        {graph && (
          <form onSubmit={runTrace}>
            <label><span className="sr-only">{t("Run a recall query", "执行一次召回查询")}</span><input value={traceQuery} onChange={(event) => setTraceQuery(event.target.value)} placeholder={t("Run a recall query", "输入要执行的召回查询")}/></label>
            <button type="submit" disabled={loading || !traceQuery.trim()}>{t("Run recall", "执行召回")}</button>
            {traceActive && <button type="button" onClick={() => void loadOverview()}>{t("Reset", "重置")}</button>}
          </form>
        )}
      </div>
      {traceActive && graph && (
        <div className="graph-result-contract" role="status">
          <div>
            <b>{t("Recall result replay", "本次召回结果重放")}</b>
            <span>{t("This view is built after the request completes. It does not claim live planner or reranker stages.", "该视图在请求完成后生成，不表示实时 Planner 或 Reranker 阶段。")}</span>
          </div>
          <dl>
            <div><dt>{t("Selected memories", "选中记忆")}</dt><dd>{graph.selected_memory_ids?.length ?? graph.retrieval_summary?.persisted_memory_id_count ?? 0}</dd></div>
            <div><dt>{t("Evidence windows", "证据窗口")}</dt><dd>{graph.retrieval_summary?.evidence_window_count ?? 0}</dd></div>
            <div><dt>{t("Missing IDs", "缺失 ID")}</dt><dd>{graph.missing_memory_ids?.length ?? 0}</dd></div>
            <div><dt>{t("Result state", "结果状态")}</dt><dd>{graph.provisional || graph.missing_memory_ids?.length ? "PARTIAL" : (graph.snapshot_state ?? "READY").toUpperCase()}</dd></div>
          </dl>
          {(graph.query_id || graph.index_job_id) && <code>{graph.query_id ? `query ${shortId(graph.query_id)}` : ""}{graph.query_id && graph.index_job_id ? " · " : ""}{graph.index_job_id ? `index ${shortId(graph.index_job_id)}` : ""}</code>}
        </div>
      )}
      {connectionError && <div className="graph-connection-note" role="alert">
        <b>{graph ? t("Request failed; the previous graph remains visible.", "请求失败；旧图仍保留显示。") : t("Graph unavailable", "图谱暂不可用")}</b>
        <span>{connectionError}</span>
      </div>}
      {events.length > 0 ? (
        <MemoryGraph
          events={events}
          edges={edges}
          dataSourceLabel={sourceLabel}
          narrative={graph?.narrative ?? undefined}
          narrativeThreads={graph?.threads ?? []}
          evidenceByNode={evidenceByNode}
          loadingNodeId={loadingNodeId}
          hasMore={Boolean(graph && overviewCursor && !traceActive && !narrativeView)}
          loadingMore={loadingMore}
          onExpand={graph && !narrativeView ? expandNode : undefined}
          onLoadEvidence={graph ? loadEvidence : undefined}
          onLoadMore={graph ? loadMore : undefined}
        />
      ) : (
        <div className="graph-empty">
          <span className="system-label">GLOBAL MEMORY GRAPH</span>
          <h2>{t("No committed memory nodes", "还没有已提交的记忆节点")}</h2>
          <p>{t("The selected Agent has no visible production snapshot or control-plane events.", "当前 Agent 没有可见的生产环境 snapshot，也没有 control-plane 事件。")}</p>
        </div>
      )}
    </div>
  );
}

function graphNodeToEvent(node: GraphNodeDto, agentId: string): MemoryEvent {
  return {
    id: node.id,
    agentId,
    subjectId: node.subject_id ?? "unassigned",
    type: node.category,
    summary: node.label,
    content: node.summary,
    source: node.source_kind ?? node.layer,
    occurredAt: node.occurred_at ?? "",
    ingestedAt: "",
    confidence: node.confidence,
    recallCount: 0,
    lastRecalledAt: null,
    tags: [node.layer, node.status],
    layer: node.layer,
    kind: node.kind,
    state: node.state,
    status: node.status,
    salience: node.salience,
    clusterId: node.cluster_id,
    evidenceCount: node.evidence_count,
    visibleNeighborCount: node.visible_neighbor_count,
    expandable: node.expandable,
    actorRole: nullableText(node.actor_role),
    actorRoles: stringList(node.actor_roles),
    authority: nullableText(node.authority),
    provenanceSource: nullableText(node.provenance_source),
    attributes: node.attributes,
  };
}

function graphEdgeToMemoryEdge(edge: GraphEdgeDto): MemoryEdge {
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: edge.type,
    weight: edge.weight,
    createdAt: "",
    origin: edge.origin,
    provenance: isRecord(edge.provenance) ? edge.provenance : {},
  };
}

function mergeGraph(current: GraphDto, incoming: GraphDto): GraphDto {
  if (current.snapshot_id !== incoming.snapshot_id) return incoming;
  const nodes = new Map(current.nodes.map((node) => [node.id, node]));
  const edges = new Map(current.edges.map((edge) => [edge.id, edge]));
  incoming.nodes.forEach((node) => nodes.set(node.id, node));
  incoming.edges.forEach((edge) => edges.set(edge.id, edge));
  return {
    ...current,
    nodes: [...nodes.values()],
    edges: [...edges.values()],
    page: incoming.page,
  };
}

async function graphRequest(
  endpoint: string,
  params: Record<string, string>,
  init: RequestInit = {},
): Promise<GraphDto> {
  const body = await graphEnvelope(endpoint, params, init);
  const graph = body.graph;
  if (!isRecord(graph) || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) {
    throw new Error("The memory graph response is invalid.");
  }
  return graph as unknown as GraphDto;
}

async function graphEvidenceRequest(endpoint: string, params: Record<string, string>): Promise<GraphEvidenceItem[]> {
  const body = await graphEnvelope(endpoint, params);
  const graph = body.graph;
  if (!isRecord(graph) || !Array.isArray(graph.items)) {
    throw new Error("The evidence response is invalid.");
  }
  return graph.items.filter(isRecord).map((item) => ({
    sourceRecordId: String(item.source_record_id ?? ""),
    relationship: String(item.relationship ?? "source"),
    sessionId: nullableText(item.session_id),
    messageId: nullableText(item.message_id),
    role: nullableText(item.role),
    actorRole: nullableText(item.actor_role) ?? nullableText(item.role),
    occurredAt: nullableText(item.occurred_at),
    text: String(item.text ?? ""),
    textSha256: String(item.text_sha256 ?? ""),
    evidenceCharStart: nullableNumber(item.evidence_char_start),
    evidenceCharEnd: nullableNumber(item.evidence_char_end),
  }));
}

async function graphEnvelope(endpoint: string, params: Record<string, string>, init: RequestInit = {}) {
  const search = new URLSearchParams(params);
  const response = await fetch(`${endpoint}?${search}`, {
    credentials: "same-origin",
    cache: "no-store",
    ...init,
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok || !isRecord(body) || body.ok !== true) {
    const error = isRecord(body) && isRecord(body.error) ? body.error : {};
    throw new Error(typeof error.message === "string" ? error.message : "The memory graph request failed.");
  }
  return body;
}

function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function nullableText(value: unknown) {
  return typeof value === "string" && value ? value : null;
}

function nullableNumber(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function stringList(value: unknown) {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
    : [];
}

function shortId(value: string) {
  return value.length > 18 ? `${value.slice(0, 18)}...` : value;
}
