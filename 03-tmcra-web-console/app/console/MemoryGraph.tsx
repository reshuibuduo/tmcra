"use client";

import type Sigma from "sigma";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type Language, useLanguage } from "../i18n";

export type MemoryLayer = "slow" | "fast" | "source";
export type GraphView = "semantic" | "timeline" | "table";
type ActorBucket = "user" | "assistant" | "mixed" | "unknown";

export type MemoryEvent = {
  id: string;
  agentId: string;
  subjectId: string;
  type: string;
  summary: string;
  content: string;
  source: string;
  occurredAt: string;
  ingestedAt: string;
  confidence: number;
  recallCount: number;
  lastRecalledAt: string | null;
  tags: string[];
  layer?: MemoryLayer;
  kind?: string;
  state?: string;
  status?: string;
  salience?: number;
  clusterId?: string | null;
  evidenceCount?: number;
  visibleNeighborCount?: number;
  expandable?: boolean;
  actorRole?: string | null;
  actorRoles?: string[];
  authority?: string | null;
  provenanceSource?: string | null;
  attributes?: Record<string, unknown>;
};

export type MemoryEdge = {
  id: string;
  source: string;
  target: string;
  type: string;
  weight: number;
  createdAt: string;
  origin?: "stored" | "derived";
  provenance?: Record<string, unknown>;
};

export type GraphEvidenceItem = {
  sourceRecordId: string;
  relationship: string;
  sessionId: string | null;
  messageId: string | null;
  role: string | null;
  actorRole: string | null;
  occurredAt: string | null;
  text: string;
  textSha256: string;
  evidenceCharStart: number | null;
  evidenceCharEnd: number | null;
};

export type NarrativeGraphThread = {
  id: string;
  title: string;
  summary: string;
  kind: string;
  status: string;
  node_ids: string[];
  memory_count: number;
  evidence_count: number;
  started_at?: string | null;
  updated_at?: string | null;
};

export type NarrativeGraphSummary = {
  headline: string;
  summary: string;
  thread_count: number;
  key_moment_count: number;
  evidence_count: number;
  started_at?: string | null;
  updated_at?: string | null;
  focus: string;
};

type NodeAttributes = {
  x: number;
  y: number;
  size: number;
  label: string;
  color: string;
  forceLabel?: boolean;
  zIndex?: number;
};

type EdgeAttributes = {
  size: number;
  color: string;
  type: string;
  relation: string;
  zIndex?: number;
};

type CameraSnapshot = { x: number; y: number; angle: number; ratio: number };

type MemoryGraphProps = {
  events: MemoryEvent[];
  edges: MemoryEdge[];
  compact?: boolean;
  initialView?: GraphView | "graph";
  dataSourceLabel?: string;
  narrative?: NarrativeGraphSummary;
  narrativeThreads?: NarrativeGraphThread[];
  evidenceByNode?: Record<string, GraphEvidenceItem[]>;
  loadingNodeId?: string | null;
  hasMore?: boolean;
  loadingMore?: boolean;
  onSelectEvent?: (event: MemoryEvent | null) => void;
  onExpand?: (event: MemoryEvent) => void | Promise<void>;
  onLoadEvidence?: (event: MemoryEvent) => void | Promise<void>;
  onLoadMore?: () => void | Promise<void>;
};

const LAYER_COLORS: Record<MemoryLayer, string> = {
  slow: "#111419",
  fast: "#626A76",
  source: "#D6D2C8",
};

const ACTOR_COLORS: Record<ActorBucket, string> = {
  user: "#B85D41",
  assistant: "#557763",
  mixed: "#A86F23",
  unknown: "#626A76",
};

const LAYERS: MemoryLayer[] = ["slow", "fast", "source"];
const ACTOR_BUCKETS: ActorBucket[] = ["user", "assistant", "mixed", "unknown"];

function memoryLayer(event: MemoryEvent): MemoryLayer {
  if (event.layer) return event.layer;
  const source = `${event.kind ?? ""} ${event.source} ${event.type}`.toLowerCase();
  if (source.includes("slow") || source.includes("capsule")) return "slow";
  if (source.includes("source_message") || source.includes("immutable_source")) return "source";
  return "fast";
}

function normalizedActorRole(value: unknown): "user" | "assistant" | "other" | null {
  if (typeof value !== "string") return null;
  const role = value.trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (["user", "human", "end_user", "customer"].includes(role)) return "user";
  if (["assistant", "codex", "ai", "model", "agent"].includes(role)) return "assistant";
  return role ? "other" : null;
}

function eventActorRoles(event: MemoryEvent): Set<"user" | "assistant" | "other"> {
  const attributes = event.attributes ?? {};
  const supplied = [
    event.actorRole,
    ...(event.actorRoles ?? []),
    attributes.actor_role,
    attributes.role,
    attributes.speaker,
  ];
  const normalized = supplied.map(normalizedActorRole).filter((role): role is "user" | "assistant" | "other" => Boolean(role));
  if (normalized.length) return new Set(normalized);

  // Older graph snapshots did not expose actor_role. Restrict this fallback to
  // explicit source naming; semantic records remain unattributed rather than
  // being silently promoted to user-authored facts.
  const legacySource = `${event.source} ${event.kind ?? ""} ${event.provenanceSource ?? ""}`.toLowerCase();
  if (/(^|[_\s-])assistant([_\s-]|$)|(^|[_\s-])codex([_\s-]|$)/.test(legacySource)) return new Set(["assistant"]);
  if (/(^|[_\s-])user([_\s-]|$)|(^|[_\s-])human([_\s-]|$)/.test(legacySource)) return new Set(["user"]);
  return new Set<"user" | "assistant" | "other">();
}

function actorBucket(event: MemoryEvent): ActorBucket {
  const authority = (event.authority ?? "").trim().toLowerCase();
  const roles = eventActorRoles(event);
  // A record with more than one speaking actor is never safe to present as a
  // single-actor fact. This also covers user/system, assistant/tool and other
  // combinations emitted by the generic MCP surface.
  if (roles.size > 1) return "mixed";
  if (roles.has("assistant")) {
    return authority === "user_assertion" || authority === "user_statement" ? "mixed" : "assistant";
  }
  if (roles.has("user")) {
    return ["assistant_source", "assistant_statement", "model_output", "advisory_not_answerability"].includes(authority)
      ? "mixed"
      : "user";
  }
  if (authority === "user_assertion" || authority === "user_statement") return "user";
  if (["assistant_source", "assistant_statement", "model_output", "advisory_not_answerability"].includes(authority)) return "assistant";
  return "unknown";
}

function actorLabel(bucket: ActorBucket, language: Language) {
  if (language === "zh") {
    return { user: "用户 · 要求/事实", assistant: "Agent · 进度/结果", mixed: "混合主体", unknown: "未标注" }[bucket];
  }
  return { user: "User · facts", assistant: "Agent · progress", mixed: "Mixed actors", unknown: "Unattributed" }[bucket];
}

function actorBadgeLabel(event: MemoryEvent, language: Language) {
  const bucket = actorBucket(event);
  const source = memoryLayer(event) === "source";
  if (language === "zh") {
    if (bucket === "assistant") return source ? "Agent 回答原文" : "Agent 进度 / 结果";
    if (bucket === "user") return source ? "用户原话" : "用户要求 / 事实";
    if (bucket === "mixed") return "角色冲突 / 混合来源";
    return "来源未标注";
  }
  if (bucket === "assistant") return source ? "AGENT VERBATIM" : "AGENT PROGRESS / RESULT";
  if (bucket === "user") return source ? "USER VERBATIM" : "USER REQUIREMENT / FACT";
  if (bucket === "mixed") return "ROLE CONFLICT / MIXED SOURCE";
  return "UNATTRIBUTED";
}

function actorBoundaryCopy(event: MemoryEvent, language: Language) {
  const bucket = actorBucket(event);
  const source = memoryLayer(event) === "source";
  if (language === "zh") {
    if (bucket === "mixed") return "这个节点包含多个对话主体，不能直接当作单一用户事实或 Agent 结果。请依据原文证据判断。";
    return source
      ? "这是 Agent 回答原文，用于支撑进度或结果记录；它不代表用户提出的要求或事实。"
      : "这个节点记录 Agent 的执行进度或结果；召回时必须保留 Agent 角色，不能改写成用户事实。";
  }
  if (bucket === "mixed") return "This node has a role conflict. Do not treat it directly as either a user fact or a Codex result; inspect the verbatim evidence.";
  return source
    ? "This is Agent verbatim evidence for progress or result records. It is not a user requirement or fact."
    : "This node records Agent progress or a result. Recall must preserve the Agent role rather than rewriting it as a user fact.";
}

function nodeColor(event: MemoryEvent) {
  return ACTOR_COLORS[actorBucket(event)];
}

function evidenceActorBucket(item: GraphEvidenceItem): ActorBucket {
  const role = normalizedActorRole(item.actorRole ?? item.role);
  return role === "user" ? "user" : role === "assistant" ? "assistant" : "unknown";
}

function evidenceActorLabel(item: GraphEvidenceItem, language: Language) {
  const bucket = evidenceActorBucket(item);
  if (language === "zh") {
    if (bucket === "assistant") return "Agent 回答原文 · 进度/结果来源";
    if (bucket === "user") return "用户原话";
    return "来源主体未标注";
  }
  if (bucket === "assistant") return "AGENT VERBATIM · PROGRESS/RESULT SOURCE";
  if (bucket === "user") return "USER VERBATIM";
  return "UNATTRIBUTED SOURCE";
}

function edgeProvenanceLabel(edge: MemoryEdge, language: Language) {
  const provenance = edge.provenance ?? {};
  const candidates = [
    provenance.provenance_source,
    provenance.source,
    provenance.kind,
    provenance.method,
    provenance.derivation,
  ];
  const value = candidates.find((item) => typeof item === "string" && item.trim());
  if (typeof value === "string") return value;
  return edge.origin === "derived"
    ? language === "zh" ? "派生关系" : "derived"
    : language === "zh" ? "存储关系" : "stored";
}

function safeDate(value: string, fallback: number) {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function formatDate(value: string | null, language: Language = "en") {
  if (!value) return language === "zh" ? "未知" : "Unknown";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat(language === "zh" ? "zh-CN" : "en", {
    month: "short",
    day: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function short(value: string, max = 38) {
  return value.length > max ? `${value.slice(0, max - 3)}...` : value;
}

function hashUnit(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

function semanticPosition(event: MemoryEvent, index: number) {
  const layer = memoryLayer(event);
  const cluster = event.clusterId || event.subjectId || event.type || "unassigned";
  const layerX: Record<MemoryLayer, number> = { slow: -5.4, fast: 0, source: 5.4 };
  const actorY: Record<ActorBucket, number> = { user: -4.2, mixed: 0, assistant: 4.2, unknown: 7.8 };
  const clusterJitter = (hashUnit(cluster) - 0.5) * 2.2;
  return {
    x: layerX[layer] + clusterJitter + (hashUnit(`${event.id}:x`) - 0.5) * 1.1,
    y: actorY[actorBucket(event)] + (hashUnit(`${event.id}:y`) - 0.5) * 1.7 + index * 0.001,
  };
}

export default function MemoryGraph({
  events,
  edges,
  compact = false,
  initialView = "semantic",
  dataSourceLabel = "Memory graph snapshot",
  narrative,
  narrativeThreads = [],
  evidenceByNode = {},
  loadingNodeId = null,
  hasMore = false,
  loadingMore = false,
  onSelectEvent,
  onExpand,
  onLoadEvidence,
  onLoadMore,
}: MemoryGraphProps) {
  const { language, t } = useLanguage();
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<Sigma<NodeAttributes, EdgeAttributes> | null>(null);
  const cameraStateRef = useRef<{ view: GraphView; state: CameraSnapshot } | null>(null);
  const selectedRef = useRef<string | null>(events[0]?.id ?? null);
  const resolvedInitialView: GraphView = initialView === "graph" ? "semantic" : initialView;
  const [view, setView] = useState<GraphView>(resolvedInitialView);
  const [selectedId, setSelectedId] = useState<string | null>(events[0]?.id ?? null);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [query, setQuery] = useState("");
  const [visibleLayers, setVisibleLayers] = useState<Record<MemoryLayer, boolean>>({
    slow: true,
    fast: true,
    source: true,
  });
  const [visibleActors, setVisibleActors] = useState<Record<ActorBucket, boolean>>({
    user: true,
    assistant: true,
    mixed: true,
    // Pre-provenance snapshots remain available behind an explicit filter, but
    // are never included in the default role-partitioned view.
    unknown: false,
  });

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReducedMotion(query.matches);
    sync();
    query.addEventListener?.("change", sync);
    return () => query.removeEventListener?.("change", sync);
  }, []);

  const layerCounts = useMemo(
    () => events.reduce<Record<MemoryLayer, number>>(
      (counts, event) => ({ ...counts, [memoryLayer(event)]: counts[memoryLayer(event)] + 1 }),
      { slow: 0, fast: 0, source: 0 },
    ),
    [events],
  );
  const actorCounts = useMemo(
    () => events.reduce<Record<ActorBucket, number>>(
      (counts, event) => ({ ...counts, [actorBucket(event)]: counts[actorBucket(event)] + 1 }),
      { user: 0, assistant: 0, mixed: 0, unknown: 0 },
    ),
    [events],
  );
  const visibleEvents = useMemo(() => {
    const clean = query.trim().toLowerCase();
    return events.filter((event) => {
      if (!visibleLayers[memoryLayer(event)]) return false;
      if (!visibleActors[actorBucket(event)]) return false;
      if (!clean) return true;
      return `${event.summary} ${event.content} ${event.type} ${event.subjectId}`
        .toLowerCase()
        .includes(clean);
    });
  }, [events, query, visibleActors, visibleLayers]);
  const visibleIds = useMemo(() => new Set(visibleEvents.map((event) => event.id)), [visibleEvents]);
  const visibleEdges = useMemo(
    () => edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
    [edges, visibleIds],
  );
  const resolvedSelectedId = selectedId && visibleIds.has(selectedId)
    ? selectedId
    : visibleEvents[0]?.id ?? null;
  const selectedEvent = useMemo(
    () => visibleEvents.find((event) => event.id === resolvedSelectedId) ?? null,
    [visibleEvents, resolvedSelectedId],
  );
  const selectedEvidence = selectedEvent ? evidenceByNode[selectedEvent.id] ?? [] : [];
  const relatedEdges = useMemo(
    () => resolvedSelectedId
      ? visibleEdges.filter((edge) => edge.source === resolvedSelectedId || edge.target === resolvedSelectedId)
      : [],
    [visibleEdges, resolvedSelectedId],
  );

  const chooseEvent = useCallback((event: MemoryEvent | null) => {
    const id = event?.id ?? null;
    selectedRef.current = id;
    setSelectedId(id);
    onSelectEvent?.(event);
    rendererRef.current?.refresh();
  }, [onSelectEvent]);

  useEffect(() => {
    const container = containerRef.current;
    if (view === "table" || !container || visibleEvents.length === 0) return;
    let disposed = false;
    let activeRenderer: Sigma<NodeAttributes, EdgeAttributes> | null = null;

    const initialize = async () => {
      const [{ default: Graph }, { default: SigmaRenderer }] = await Promise.all([
        import("graphology"),
        import("sigma"),
      ]);
      if (disposed) return;
      const graph = new Graph<NodeAttributes, EdgeAttributes>({ type: "directed", multi: true });
      const ordered = view === "timeline"
        ? [...visibleEvents].sort((a, b) => safeDate(a.occurredAt, 0) - safeDate(b.occurredAt, 0))
        : visibleEvents;
      const times = ordered.map((event, index) => safeDate(event.occurredAt, index));
      const minTime = Math.min(...times);
      const maxTime = Math.max(...times);
      const span = Math.max(1, maxTime - minTime);
      const actorLaneY: Record<ActorBucket, number> = { user: -3, mixed: 0, assistant: 3, unknown: 6 };

      ordered.forEach((event, index) => {
        const layer = memoryLayer(event);
        const temporal = {
          x: ((safeDate(event.occurredAt, index) - minTime) / span) * 12 - 6,
          y: actorLaneY[actorBucket(event)] + (hashUnit(event.id) - 0.5) * 0.46,
        };
        const position = view === "timeline" ? temporal : semanticPosition(event, index);
        const salience = Math.max(0, Math.min(1, event.salience ?? event.confidence ?? 0.5));
        const layerBoost = layer === "slow" ? 3.2 : layer === "source" ? -0.6 : 0.8;
        graph.addNode(event.id, {
          ...position,
          size: Math.max(4.5, Math.min(14, 5.2 + layerBoost + salience * 4.2)),
          label: short(event.summary),
          color: nodeColor(event),
          forceLabel: event.id === selectedRef.current || layer === "slow",
          zIndex: event.id === selectedRef.current ? 3 : layer === "slow" ? 2 : 1,
        });
      });

      visibleEdges.forEach((edge, index) => {
        if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) return;
        const baseKey = edge.id || `edge-${index}`;
        const key = graph.hasEdge(baseKey) ? `${baseKey}:${index}` : baseKey;
        graph.addEdgeWithKey(key, edge.source, edge.target, {
          size: Math.max(0.55, Math.min(2.6, edge.weight * 1.8)),
          color: edge.origin === "derived" ? "rgba(98, 106, 118, .34)" : "rgba(214, 210, 200, .34)",
          type: "line",
          relation: edge.type,
          zIndex: 0,
        });
      });

      const renderer = new SigmaRenderer<NodeAttributes, EdgeAttributes>(graph, container, {
        allowInvalidContainer: true,
        defaultNodeColor: ACTOR_COLORS.user,
        defaultEdgeColor: "rgba(214, 210, 200, .34)",
        renderEdgeLabels: false,
        enableEdgeEvents: true,
        labelFont: "monospace",
        labelSize: compact ? 10 : 11,
        labelColor: { color: "#F5F3EE" },
        labelDensity: 0.8,
        labelGridCellSize: 94,
        labelRenderedSizeThreshold: 6,
        stagePadding: compact ? 30 : 48,
        minCameraRatio: 0.28,
        maxCameraRatio: 7,
        zIndex: true,
        nodeReducer: (node, data) => {
          const selected = selectedRef.current;
          if (!selected || !graph.hasNode(selected)) return data;
          if (node === selected) {
            return { ...data, color: "#f4f7f8", size: data.size * 1.32, zIndex: 4, forceLabel: true };
          }
          if (graph.areNeighbors(node, selected)) return { ...data, zIndex: 3, forceLabel: true };
          return { ...data, color: "#4f5960", zIndex: 0, forceLabel: false };
        },
        edgeReducer: (edge, data) => {
          const selected = selectedRef.current;
          if (!selected || !graph.hasNode(selected)) return data;
          const [source, target] = graph.extremities(edge);
          const active = source === selected || target === selected;
          return active
            ? { ...data, color: "rgba(54, 88, 214, .95)", size: Math.max(1.8, data.size), zIndex: 3 }
            : { ...data, color: "rgba(80, 91, 96, .16)", zIndex: 0 };
        },
      });
      renderer.on("clickNode", ({ node }) => {
        chooseEvent(visibleEvents.find((event) => event.id === node) ?? null);
      });
      renderer.on("clickStage", () => chooseEvent(null));
      renderer.on("enterNode", () => { container.style.cursor = "pointer"; });
      renderer.on("leaveNode", () => { container.style.cursor = "grab"; });
      activeRenderer = renderer;
      rendererRef.current = renderer;
      const previousCamera = cameraStateRef.current;
      if (previousCamera?.view === view) renderer.getCamera().setState(previousCamera.state);
      else if (!reducedMotion) renderer.getCamera().animatedReset({ duration: 340 });
    };

    void initialize();
    return () => {
      disposed = true;
      if (activeRenderer) cameraStateRef.current = { view, state: activeRenderer.getCamera().getState() };
      activeRenderer?.kill();
      if (rendererRef.current === activeRenderer) rendererRef.current = null;
    };
  }, [chooseEvent, compact, reducedMotion, view, visibleEdges, visibleEvents]);

  useEffect(() => {
    selectedRef.current = resolvedSelectedId;
    rendererRef.current?.refresh();
  }, [resolvedSelectedId]);

  const moveSelection = (direction: -1 | 1) => {
    if (!visibleEvents.length) return;
    const index = Math.max(0, visibleEvents.findIndex((event) => event.id === resolvedSelectedId));
    chooseEvent(visibleEvents[(index + direction + visibleEvents.length) % visibleEvents.length]);
  };

  return (
    <section className={`memory-graph ${compact ? "is-compact" : ""}`} aria-label={t("Global memory topology", "全局记忆拓扑")}>
      {narrative && (
        <section className="narrative-overview-band" aria-labelledby="narrative-overview-title">
          <header>
            <div>
              <span className="system-label">{t("MEMORY STORYLINE", "记忆脉络")}</span>
              <h2 id="narrative-overview-title">{narrative.headline}</h2>
              <p>{narrative.summary || t("Evidence-bound themes and key moments.", "由可追溯证据构成的主题与关键节点。")}</p>
            </div>
            <dl>
              <div><dt>{t("Storylines", "主题主线")}</dt><dd>{narrative.thread_count}</dd></div>
              <div><dt>{t("Key moments", "关键节点")}</dt><dd>{narrative.key_moment_count}</dd></div>
              <div><dt>{t("Evidence", "证据")}</dt><dd>{narrative.evidence_count}</dd></div>
            </dl>
          </header>
          <ol className="narrative-thread-list">
            {narrativeThreads.map((thread) => {
              const firstNode = thread.node_ids
                .map((id) => visibleEvents.find((event) => event.id === id))
                .find((event): event is MemoryEvent => Boolean(event));
              return (
                <li key={thread.id}>
                  <button type="button" disabled={!firstNode} onClick={() => chooseEvent(firstNode ?? null)}>
                    <span>{thread.kind}</span>
                    <strong>{thread.title}</strong>
                    <small>{thread.summary}</small>
                    <time>{formatDate(thread.updated_at ?? thread.started_at ?? null, language)}</time>
                  </button>
                </li>
              );
            })}
          </ol>
        </section>
      )}
      <div className="graph-toolbar">
        <div className="graph-heading">
          <span className="system-label">GLOBAL MEMORY GRAPH</span>
          <b>{t(`${visibleEvents.length} nodes / ${visibleEdges.length} edges`, `${visibleEvents.length} 个节点 / ${visibleEdges.length} 条边`)}</b>
          <small>{dataSourceLabel}</small>
          <small>{t("User requirements/facts and Agent progress/results are shown together without merging their roles.", "默认同时展示用户要求/事实与 Agent 进度/结果，但不会合并对话主体。")}</small>
        </div>
        <div className="graph-toolbar-actions">
          {!compact && (
            <label className="graph-search">
              <span className="sr-only">{t("Search visible memory", "搜索当前可见记忆")}</span>
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("Search memory", "搜索记忆")} />
            </label>
          )}
          <div className="graph-filter-group">
            <span className="graph-filter-title">{t("Memory layers", "记忆层")}</span>
            <div className="layer-filters" aria-label={t("Visible memory layers", "当前显示的记忆层")}>
              {LAYERS.map((layer) => (
                <label key={layer}>
                  <input
                    type="checkbox"
                    checked={visibleLayers[layer]}
                    onChange={() => setVisibleLayers((current) => ({ ...current, [layer]: !current[layer] }))}
                  />
                  <i style={{ background: LAYER_COLORS[layer] }} />
                  <span>{layer}</span>
                  <b>{layerCounts[layer]}</b>
                </label>
              ))}
            </div>
          </div>
          <div className="graph-filter-group is-actor-filter">
            <span className="graph-filter-title">{t("Memory roles", "记忆角色")}</span>
            <div
              className="layer-filters"
              aria-label={t("Visible memory roles", "当前显示的记忆角色")}
              title={t("User and Codex roles are visible by default; conflicts remain a separate role.", "用户与 Codex 默认同时可见；角色冲突单独显示。")}
            >
              {ACTOR_BUCKETS.map((actor) => (
                <label key={actor}>
                  <input
                    type="checkbox"
                    checked={visibleActors[actor]}
                    onChange={() => setVisibleActors((current) => ({ ...current, [actor]: !current[actor] }))}
                  />
                  <i style={{ background: ACTOR_COLORS[actor] }} />
                  <span>{actorLabel(actor, language)}</span>
                  <b>{actorCounts[actor]}</b>
                </label>
              ))}
            </div>
          </div>
          <div className="segmented" aria-label={t("Memory view", "记忆视图")}>
            <button className={view === "semantic" ? "is-active" : ""} type="button" onClick={() => setView("semantic")} aria-pressed={view === "semantic"}>Graph</button>
            <button className={view === "timeline" ? "is-active" : ""} type="button" onClick={() => setView("timeline")} aria-pressed={view === "timeline"}>{t("Timeline", "时间线")}</button>
            <button className={view === "table" ? "is-active" : ""} type="button" onClick={() => setView("table")} aria-pressed={view === "table"}>{t("Table", "表格")}</button>
          </div>
        </div>
      </div>

      {view !== "table" ? (
        <div className="graph-stage-wrap">
          <div className="graph-actor-legend" aria-label={t("Memory role color key", "记忆角色颜色说明")}>
            {(["user", "assistant", "mixed"] as ActorBucket[]).map((actor) => (
              <span key={actor}><i style={{ background: ACTOR_COLORS[actor] }} /><b>{actorLabel(actor, language)}</b></span>
            ))}
          </div>
          <div
            ref={containerRef}
            className="sigma-stage"
            role="img"
            tabIndex={0}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
                event.preventDefault();
                moveSelection(-1);
              } else if (event.key === "ArrowRight" || event.key === "ArrowDown") {
                event.preventDefault();
                moveSelection(1);
              } else if (event.key === "+" || event.key === "=") {
                event.preventDefault();
                rendererRef.current?.getCamera().animatedZoom({ duration: reducedMotion ? 0 : 150 });
              } else if (event.key === "-") {
                event.preventDefault();
                rendererRef.current?.getCamera().animatedUnzoom({ duration: reducedMotion ? 0 : 150 });
              } else if (event.key === "0") {
                event.preventDefault();
                rendererRef.current?.getCamera().animatedReset({ duration: reducedMotion ? 0 : 200 });
              }
            }}
            aria-label={t(`Interactive ${view} Memory Graph with ${visibleEvents.length} nodes. Use Table view for keyboard access.`, `包含 ${visibleEvents.length} 个节点的交互式 Memory Graph；使用表格视图可以通过键盘操作。`)}
          />
          {visibleEvents.length === 0 && (
            <div className="graph-filter-empty" role="status">
              <b>{t("No nodes match the current filters", "当前筛选条件下没有节点")}</b>
              <span>{t("Enable another actor or memory layer, or clear the search query.", "请启用其他对话主体或记忆层，或者清空搜索词。")}</span>
            </div>
          )}
          {view === "timeline" ? (
            <div className="graph-axis" aria-hidden="true"><span>{t("EARLIER", "更早")}</span><i /><span>{t("NOW", "现在")}</span></div>
          ) : (
            <div className="graph-layer-axis" aria-hidden="true"><span>SLOW CORE</span><i /><span>FAST</span><i /><span>SOURCE</span></div>
          )}
          <div className="graph-camera" aria-label={t("Graph camera controls", "Graph 视图控制")}>
            <button type="button" aria-label={t("Zoom in", "放大")} onClick={() => rendererRef.current?.getCamera().animatedZoom({ duration: reducedMotion ? 0 : 150 })}>+</button>
            <button type="button" aria-label={t("Zoom out", "缩小")} onClick={() => rendererRef.current?.getCamera().animatedUnzoom({ duration: reducedMotion ? 0 : 150 })}>-</button>
            <button type="button" aria-label={t("Fit Graph", "显示完整 Graph")} onClick={() => rendererRef.current?.getCamera().animatedReset({ duration: reducedMotion ? 0 : 200 })}>{t("FIT", "适应")}</button>
          </div>
          {hasMore && onLoadMore && (
            <button className="graph-load-more" type="button" disabled={loadingMore} onClick={() => void onLoadMore()}>
              {loadingMore ? t("Loading", "正在加载") : t("Load more", "加载更多")}
            </button>
          )}
        </div>
      ) : (
        <div className="graph-table-wrap">
          <table className="data-table graph-data-table">
            <caption className="sr-only">{t("Equivalent table of all visible Memory Graph nodes", "当前可见 Memory Graph 节点的等价表格")}</caption>
            <thead><tr><th>{t("Layer", "层")}</th><th>{t("Memory role", "记忆角色")}</th><th>{t("Occurred", "发生时间")}</th><th>{t("Type", "类型")}</th><th>{t("Memory", "记忆")}</th><th>{t("Subject", "记忆对象")}</th><th>{t("Confidence", "置信度")}</th><th>{t("Evidence", "证据")}</th></tr></thead>
            <tbody>
              {visibleEvents.map((event) => (
                <tr key={event.id} className={event.id === resolvedSelectedId ? "is-selected" : ""}>
                  <td><span className={`layer-chip is-${memoryLayer(event)}`}>{memoryLayer(event)}</span></td>
                  <td>
                    <span
                      className="type-chip"
                      style={{ color: ACTOR_COLORS[actorBucket(event)], borderColor: ACTOR_COLORS[actorBucket(event)] }}
                      title={actorBadgeLabel(event, language)}
                    >
                      {actorBadgeLabel(event, language)}
                    </span>
                  </td>
                  <td>{formatDate(event.occurredAt, language)}</td>
                  <td><span className="type-chip">{event.type}</span></td>
                  <td><button className="table-link" type="button" onClick={() => chooseEvent(event)}>{event.summary}</button></td>
                  <td className="mono">{event.subjectId || "unassigned"}</td>
                  <td>{Math.round(event.confidence * 100)}%</td>
                  <td>{event.evidenceCount ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <aside className="memory-inspector" aria-live="polite" aria-label={t("Selected memory inspector", "当前记忆节点详情")}>
        {selectedEvent ? (
          <>
            <div className="inspector-head">
              <div><span className="system-label">{t("NODE INSPECTOR", "节点详情")}</span><h3>{selectedEvent.summary}</h3></div>
              <div className="inspector-stepper">
                <button type="button" onClick={() => moveSelection(-1)} aria-label={t("Previous memory", "上一条记忆")}>{t("PREV", "上一条")}</button>
                <button type="button" onClick={() => moveSelection(1)} aria-label={t("Next memory", "下一条记忆")}>{t("NEXT", "下一条")}</button>
              </div>
            </div>
            <div className="inspector-layer">
              <i style={{ background: nodeColor(selectedEvent) }} />
              <b>{memoryLayer(selectedEvent)}</b>
              <span>{selectedEvent.status || selectedEvent.state || "active"}</span>
              <span
                className="type-chip"
                style={{ color: ACTOR_COLORS[actorBucket(selectedEvent)], borderColor: ACTOR_COLORS[actorBucket(selectedEvent)] }}
              >
                {actorBadgeLabel(selectedEvent, language)}
              </span>
            </div>
            {!compact && (actorBucket(selectedEvent) === "assistant" || actorBucket(selectedEvent) === "mixed") && (
              <p className="actor-boundary-note" style={{ borderColor: ACTOR_COLORS[actorBucket(selectedEvent)], color: ACTOR_COLORS[actorBucket(selectedEvent)] }}>
                {actorBoundaryCopy(selectedEvent, language)}
              </p>
            )}
            {!compact && <p className="inspector-content">{selectedEvent.content}</p>}
            <dl className="inspector-fields">
              <div><dt>Memory ID</dt><dd title={selectedEvent.id}>{selectedEvent.id}</dd></div>
              <div><dt>{t("Kind", "类别")}</dt><dd>{selectedEvent.kind || selectedEvent.type}</dd></div>
              <div><dt>{t("Memory role", "记忆角色")}</dt><dd>{actorBadgeLabel(selectedEvent, language)}</dd></div>
              <div><dt>{t("Subject", "记忆对象")}</dt><dd>{selectedEvent.subjectId || "unassigned"}</dd></div>
              <div><dt>{t("Occurred", "发生时间")}</dt><dd>{formatDate(selectedEvent.occurredAt, language)}</dd></div>
              <div><dt>Source</dt><dd>{selectedEvent.source || "unknown"}</dd></div>
              <div><dt>{t("Statement authority", "陈述权属")}</dt><dd>{selectedEvent.authority || t("not declared", "未声明")}</dd></div>
              <div><dt>{t("Provenance", "来源链路")}</dt><dd>{selectedEvent.provenanceSource || t("not declared", "未声明")}</dd></div>
              <div><dt>{t("Confidence", "置信度")}</dt><dd>{Math.round(selectedEvent.confidence * 100)}%</dd></div>
              <div><dt>{t("Evidence", "证据")}</dt><dd>{selectedEvent.evidenceCount ?? 0}</dd></div>
              <div><dt>{t("Visible links", "可见关系")}</dt><dd>{selectedEvent.visibleNeighborCount ?? relatedEdges.length}</dd></div>
            </dl>
            {!compact && (onExpand || onLoadEvidence) && (
              <div className="inspector-actions">
                {onExpand && (
                  <button type="button" disabled={loadingNodeId === selectedEvent.id} onClick={() => void onExpand(selectedEvent)}>
                    {loadingNodeId === selectedEvent.id ? t("Expanding", "正在展开") : t("Expand links", "展开关系")}
                  </button>
                )}
                {onLoadEvidence && (
                  <button type="button" disabled={loadingNodeId === selectedEvent.id} onClick={() => void onLoadEvidence(selectedEvent)}>
                    {selectedEvidence.length ? t("Refresh evidence", "刷新证据") : t("Open evidence", "查看证据")}
                  </button>
                )}
              </div>
            )}
            {!compact && selectedEvidence.length > 0 && (
              <div className="evidence-list">
                <span className="system-label">{t("VERBATIM SOURCE", "原文证据")} / {selectedEvidence.length}</span>
                {selectedEvidence.map((item) => (
                  <article
                    key={`${item.sourceRecordId}:${item.relationship}`}
                    data-actor-role={evidenceActorBucket(item)}
                    style={{ borderInlineStart: `3px solid ${ACTOR_COLORS[evidenceActorBucket(item)]}`, paddingInlineStart: 10 }}
                  >
                    <header>
                      <b style={{ color: ACTOR_COLORS[evidenceActorBucket(item)] }}>{evidenceActorLabel(item, language)}</b>
                      <time>{formatDate(item.occurredAt, language)}</time>
                    </header>
                    <p>{item.text}</p>
                    <code>{item.sourceRecordId}</code>
                  </article>
                ))}
              </div>
            )}
            {!compact && (
              <div className="relationship-list">
                <span className="system-label">VISIBLE RELATIONSHIPS / {relatedEdges.length}</span>
                {relatedEdges.length ? relatedEdges.map((edge) => (
                  <button
                    key={edge.id}
                    type="button"
                    title={`${edge.type} · ${edgeProvenanceLabel(edge, language)}`}
                    onClick={() => chooseEvent(visibleEvents.find((event) => event.id === (edge.source === resolvedSelectedId ? edge.target : edge.source)) ?? null)}
                  >
                    <span><em>{edge.type}</em><small>{edgeProvenanceLabel(edge, language)}</small></span><b>{edge.source === resolvedSelectedId ? edge.target : edge.source}</b><i>&gt;</i>
                  </button>
                )) : <p>{t("No linked memory in this view.", "当前视图中没有关联记忆。")}</p>}
              </div>
            )}
          </>
        ) : (
          <div className="inspector-empty"><span className="system-label">{t("NODE INSPECTOR", "节点详情")}</span><p>{t("Select a memory node or table row.", "选择一个记忆节点或表格行即可查看详情。")}</p></div>
        )}
      </aside>
    </section>
  );
}
