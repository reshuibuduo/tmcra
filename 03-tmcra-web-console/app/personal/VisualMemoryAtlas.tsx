"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useLanguage } from "../i18n";
import VisualAtlasGraph, {
  type VisualAtlasGraphHandle,
  type VisualAtlasViewMode,
} from "./VisualAtlasGraph";
import "./VisualMemoryAtlas.css";

export type VisualAtlasNodeKind = "galaxy" | "session" | "chapter" | "memory";

export type VisualAtlasSourceEvidence = {
  source_record_id: string;
  text: string;
  role?: string | null;
  actor_role?: string | null;
  occurred_at?: string | null;
  message_id?: string | null;
  evidence_char_start?: number | null;
  evidence_char_end?: number | null;
  text_sha256?: string | null;
};

export type VisualAtlasGalaxy = {
  id: string;
  label: string;
  summary?: string | null;
  color?: string | null;
  session_ids?: string[];
  memory_count?: number;
  salience?: number;
};

export type VisualAtlasSession = {
  id: string;
  galaxy_id?: string | null;
  label: string;
  summary?: string | null;
  status?: string | null;
  chapter_ids?: string[];
  memory_count?: number;
  created_at?: string | null;
  updated_at?: string | null;
  source_app?: string | null;
  salience?: number;
};

export type VisualAtlasChapter = {
  id: string;
  session_id: string;
  label: string;
  summary?: string | null;
  memory_ids?: string[];
  turn_start?: number | null;
  turn_end?: number | null;
  salience?: number;
};

export type VisualAtlasMemory = {
  id: string;
  session_id: string;
  chapter_id?: string | null;
  label: string;
  summary?: string | null;
  occurred_at?: string | null;
  role?: string | null;
  tags?: string[];
  source_record_ids?: string[];
  source_evidence?: VisualAtlasSourceEvidence[];
  confidence?: number;
  salience?: number;
  state?: string | null;
  evidence_lookup_id?: string | null;
};

export type VisualAtlasEdge = {
  id: string;
  source: string;
  target: string;
  type: string;
  weight?: number;
  origin?: "stored" | "derived" | "hierarchy";
  evidence_ids?: string[];
};

export type VisualAtlasData = {
  schema_version: "tmcra.visual-atlas.1";
  scope_name: string;
  snapshot_id: string;
  generated_at?: string | null;
  projection_state?: "fallback" | "building" | "ready" | "stale" | "failed";
  generated_by?: string | null;
  model?: string | null;
  galaxies: VisualAtlasGalaxy[];
  sessions: VisualAtlasSession[];
  chapters: VisualAtlasChapter[];
  memories: VisualAtlasMemory[];
  edges?: VisualAtlasEdge[];
  source_evidence?: Record<string, VisualAtlasSourceEvidence[]>;
};

export type VisualMemoryAtlasProps = {
  data: VisualAtlasData | null;
  onRequestSourceEvidence?: (
    memory: VisualAtlasMemory,
  ) => Promise<VisualAtlasSourceEvidence[]>;
  className?: string;
};

type Point = { x: number; y: number };

export type VisualNode = {
  key: string;
  id: string;
  kind: VisualAtlasNodeKind;
  label: string;
  summary: string;
  x: number;
  y: number;
  radius: number;
  color: string;
  parentKey: string | null;
  childKeys: string[];
  status: string;
  salience: number;
  confidence: number;
  sourceCount: number;
  sourceRecordIds: string[];
  role: string | null;
  tags: string[];
  occurredAt: string | null;
  sourceApp: string | null;
  turnStart: number | null;
  turnEnd: number | null;
  memory: VisualAtlasMemory | null;
};

export type VisualEdge = {
  id: string;
  source: string;
  target: string;
  type: string;
  weight: number;
  origin: string;
  evidenceIds: string[];
};

type AtlasModel = {
  nodes: VisualNode[];
  nodeByKey: Map<string, VisualNode>;
  edges: VisualEdge[];
  bounds: { minX: number; minY: number; maxX: number; maxY: number };
  counts: { galaxies: number; sessions: number; chapters: number; memories: number; sources: number };
};

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
const GALAXY_COLORS = ["#626A76", "#777E87", "#8C918F", "#756F69"];

export default function VisualMemoryAtlas({
  data,
  onRequestSourceEvidence,
  className = "",
}: VisualMemoryAtlasProps) {
  const { language, t } = useLanguage();
  const model = useMemo(() => (data ? buildAtlasModel(data) : null), [data]);
  const graphRef = useRef<VisualAtlasGraphHandle>(null);
  const loadedScopeRef = useRef<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<VisualAtlasViewMode>("global");
  const [sourceEvidence, setSourceEvidence] = useState<Record<string, VisualAtlasSourceEvidence[]>>({});
  const [sourceLoading, setSourceLoading] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [mobileDetailsOpen, setMobileDetailsOpen] = useState(false);

  useEffect(() => {
    if (!model) return;
    const scopeKey = data?.scope_name ?? "empty";
    if (loadedScopeRef.current === scopeKey) return;
    loadedScopeRef.current = scopeKey;
    setSelectedKey(null);
    setViewMode("global");
    setMobileDetailsOpen(false);
  }, [data?.scope_name, model]);

  const selectedNode = selectedKey && model?.nodeByKey.has(selectedKey)
    ? model.nodeByKey.get(selectedKey) ?? null
    : null;
  const resolvedSelectedKey = selectedNode?.key ?? null;
  const narrativeMode = isNarrativeViewMode(viewMode);
  const focusTrail = useMemo(
    () => narrativeMode ? buildGlobalFocusTrail(model, selectedNode) : buildFocusTrail(model, selectedNode),
    [model, narrativeMode, selectedNode],
  );

  const loadSourceEvidence = useCallback(
    async (node: VisualNode) => {
      if (node.kind !== "memory" || !node.memory) return;
      const embedded = data?.source_evidence?.[node.memory.id] ?? data?.source_evidence?.[node.key] ?? node.memory.source_evidence ?? [];
      if (embedded.length) {
        setSourceEvidence((current) => ({ ...current, [node.key]: embedded }));
        return;
      }
      if (!onRequestSourceEvidence || sourceEvidence[node.key]) return;
      setSourceLoading(true);
      setSourceError(null);
      try {
        const items = await onRequestSourceEvidence(node.memory);
        setSourceEvidence((current) => ({ ...current, [node.key]: items }));
      } catch (caught) {
        setSourceError(caught instanceof Error ? caught.message : "Source evidence unavailable.");
      } finally {
        setSourceLoading(false);
      }
    },
    [data?.source_evidence, onRequestSourceEvidence, sourceEvidence],
  );

  const selectNode = (node: VisualNode) => {
    setSelectedKey(node.key);
    setMobileDetailsOpen(true);
    if (node.kind === "memory") void loadSourceEvidence(node);
  };

  const rootClass = ["tmcra-vma", className].filter(Boolean).join(" ");
  const status = data?.projection_state ?? "ready";
  const scopeDisplayName = readableScopeName(data?.scope_name, language);

  return (
    <section className={rootClass} aria-label={t("Visual memory atlas", "可视记忆星图")}>
      <header className="tmcra-vma-toolbar">
        <div className="tmcra-vma-heading">
          <span className="tmcra-vma-eyebrow">VISUAL MEMORY ATLAS</span>
          <h1 title={data?.scope_name}>{scopeDisplayName}</h1>
          <span className={`tmcra-vma-status is-${status}`}>
            {statusLabel(status, language)}
          </span>
        </div>
        <div className="tmcra-vma-toolbar-meta">
          <span>{data?.snapshot_id ? shortId(data.snapshot_id) : "--"}</span>
          <span>{data?.model ?? "TMCRA projection"}</span>
          <div className="tmcra-vma-controls" aria-label={t("Map controls", "地图控制") }>
            <button type="button" title={t("Zoom out", "缩小")} aria-label={t("Zoom out", "缩小")} onClick={() => graphRef.current?.zoomOut()}>
              −
            </button>
            <button type="button" title={t("Fit atlas", "适配全图")} aria-label={t("Fit atlas", "适配全图")} onClick={() => graphRef.current?.fit()}>
              ◎
            </button>
            <button type="button" title={t("Zoom in", "放大")} aria-label={t("Zoom in", "放大")} onClick={() => graphRef.current?.zoomIn()}>
              +
            </button>
          </div>
          <button
            type="button"
            className="tmcra-vma-detail-toggle"
            onClick={() => setMobileDetailsOpen((current) => !current)}
            aria-expanded={mobileDetailsOpen}
          >
            {t("Details", "详情")}
          </button>
        </div>
      </header>

      <div className="tmcra-vma-body">
        <div className="tmcra-vma-map-column">
          <nav className="tmcra-vma-view-tabs" aria-label={t("Atlas views", "图谱视图") }>
            <AtlasViewTab mode="global" current={viewMode} onSelect={setViewMode} label={t("Global memory", "全局记忆")} />
            <AtlasViewTab mode="threads" current={viewMode} onSelect={setViewMode} label={t("Evidence trail", "证据脉络")} />
            <AtlasViewTab mode="evolution" current={viewMode} onSelect={setViewMode} label={t("Evolution", "演化流")} />
            <AtlasViewTab mode="relations" current={viewMode} onSelect={setViewMode} label={t("Relations", "关系图")} />
            <AtlasViewTab mode="evidence" current={viewMode} onSelect={setViewMode} label={t("Source", "原文证据")} />
          </nav>

          {selectedNode && (
            <div className="tmcra-vma-focus-trail" aria-label={t("Current path", "当前脉络") }>
              <button type="button" onClick={() => { setSelectedKey(null); setViewMode(narrativeMode ? viewMode : "threads"); }}>
                {t("Memory", "记忆")}
              </button>
              {focusTrail.map((node) => (
                <button
                  type="button"
                  key={node.key}
                  className={node.key === resolvedSelectedKey ? "is-current" : ""}
                  onClick={() => {
                    setSelectedKey(node.key);
                    setViewMode(narrativeMode ? viewMode : "threads");
                    if (node.kind === "memory") void loadSourceEvidence(node);
                  }}
                >
                  {node.label}
                </button>
              ))}
            </div>
          )}

          {model && (
            <VisualAtlasGraph
              ref={graphRef}
              nodes={model.nodes}
              edges={model.edges}
              mode={viewMode}
              selectedKey={resolvedSelectedKey}
              scopeLabel={scopeDisplayName}
              language={language}
              onSelect={selectNode}
            />
          )}

          {!model && (
            <div className="tmcra-vma-empty">
              <span className="tmcra-vma-eyebrow">VISUAL MEMORY ATLAS</span>
              <strong>{t("No committed visual atlas", "还没有已提交的可视星图")}</strong>
              <p>{t("The atlas appears after the first committed memory projection.", "完成第一次记忆投影后，可视星图会出现在这里。")}</p>
            </div>
          )}
        </div>

        <AtlasDetails
          node={selectedNode}
          model={model}
          language={language}
          t={t}
          open={mobileDetailsOpen}
          sourceEvidence={selectedNode ? sourceEvidence[selectedNode.key] ?? [] : []}
          sourceLoading={sourceLoading}
          sourceError={sourceError}
          viewMode={viewMode}
          onClose={() => setMobileDetailsOpen(false)}
          onSelect={selectNode}
          onOpenEvidence={(node) => {
            setSelectedKey(node.key);
            setViewMode("evidence");
            void loadSourceEvidence(node);
          }}
        />
      </div>
    </section>
  );
}

function isNarrativeViewMode(mode: VisualAtlasViewMode) {
  return mode === "global" || mode === "evolution" || mode === "relations";
}

function AtlasViewTab({
  mode,
  current,
  onSelect,
  label,
}: {
  mode: VisualAtlasViewMode;
  current: VisualAtlasViewMode;
  onSelect: (mode: VisualAtlasViewMode) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      className={mode === current ? "is-active" : ""}
      aria-current={mode === current ? "page" : undefined}
      onClick={() => onSelect(mode)}
    >
      {label}
    </button>
  );
}

function AtlasDetails({
  node,
  model,
  language,
  t,
  open,
  sourceEvidence,
  sourceLoading,
  sourceError,
  viewMode,
  onClose,
  onSelect,
  onOpenEvidence,
}: {
  node: VisualNode | null;
  model: AtlasModel | null;
  language: "en" | "zh";
  t: (english: string, chinese: string) => string;
  open: boolean;
  sourceEvidence: VisualAtlasSourceEvidence[];
  sourceLoading: boolean;
  sourceError: string | null;
  viewMode: VisualAtlasViewMode;
  onClose: () => void;
  onSelect: (node: VisualNode) => void;
  onOpenEvidence: (node: VisualNode) => void;
}) {
  const globalMode = isNarrativeViewMode(viewMode);
  const children = node && model
    ? globalMode
      ? globalChildren(model, node)
      : node.childKeys
        .map((key) => model.nodeByKey.get(key))
        .filter((child): child is VisualNode => Boolean(child))
        .filter((child) => node.kind !== "chapter" || child.status !== "immutable-source")
    : [];
  const parent = node && model
    ? globalMode ? globalParent(model, node) : node.parentKey ? model.nodeByKey.get(node.parentKey) ?? null : null
    : null;
  const globalStats = node && model ? summarizeGlobalNode(model, node) : null;

  return (
    <aside className={`tmcra-vma-details ${open ? "is-open" : ""}`} aria-label={t("Atlas details", "星图详情")}>
      <header className="tmcra-vma-details-header">
        <div>
          <span className="tmcra-vma-eyebrow">INSPECTOR</span>
          <b>{node ? (globalMode ? globalKindLabel(node.kind, language) : kindLabel(node.kind, language)) : t("Memory overview", "记忆总览")}</b>
        </div>
        <button type="button" title={t("Close details", "关闭详情")} aria-label={t("Close details", "关闭详情")} onClick={onClose}>×</button>
      </header>

      {!node && model && (
        <div className="tmcra-vma-overview">
          <strong>{globalMode ? t("Remember what you worked on", "回看你曾经做过什么") : t("Trace every memory to its evidence", "逐层核对记忆与原始证据")}</strong>
          <p>{globalMode
            ? t("Work areas lead directly to milestones, decisions, results, and unfinished work. Technical sessions stay out of the way until you open the evidence trail.", "从工作领域直接进入里程碑、决策、结果和未完成事项。技术会话层默认隐藏，需要核对时再打开证据脉络。")
            : t("Follow the complete technical path from theme and session to episode, Writer memory, and immutable Source.", "沿主题、会话、阶段、Writer 记忆到不可变 Source，检查完整技术链路。")}</p>
          <div className="tmcra-vma-overview-stat"><span>{t("Work areas", "工作领域")}</span><b>{formatCount(model.counts.galaxies)}</b></div>
          <div className="tmcra-vma-overview-stat"><span>{t("Milestones", "事项与里程碑")}</span><b>{formatCount(model.counts.chapters)}</b></div>
          <div className="tmcra-vma-overview-stat"><span>{t("Key memories", "关键记忆")}</span><b>{formatCount(model.counts.memories)}</b></div>
          <div className="tmcra-vma-overview-stat"><span>{t("Conversations covered", "覆盖会话")}</span><b>{formatCount(model.counts.sessions)}</b></div>
          <div className="tmcra-vma-overview-stat"><span>{t("Verbatim evidence", "可追溯原文")}</span><b>{formatCount(model.counts.sources)}</b></div>
        </div>
      )}

      {node && (
        <div className="tmcra-vma-node-details">
          <div className="tmcra-vma-node-title-line">
            <i style={{ background: node.color }} />
            <span>{globalMode ? globalKindLabel(node.kind, language) : kindLabel(node.kind, language)}</span>
          </div>
          <h2>{node.label}</h2>
          <p>{node.summary || t("No summary attached.", "暂无摘要。")}</p>
          {globalMode && globalStats ? (
            <div className="tmcra-vma-detail-grid">
              <div><span>{t("Milestones", "事项与里程碑")}</span><b>{formatCount(globalStats.chapters)}</b></div>
              <div><span>{t("Key memories", "关键记忆")}</span><b>{formatCount(globalStats.memories)}</b></div>
              <div><span>{t("Conversations", "相关会话")}</span><b>{formatCount(globalStats.sessions)}</b></div>
              <div><span>{t("Verbatim evidence", "原文依据")}</span><b>{formatCount(globalStats.sources)}</b></div>
              <div><span>{t("Progress", "进展状态")}</span><b>{humanStatus(node.status, language)}</b></div>
              <div><span>{t("Range", "时间范围")}</span><b>{model ? humanRange(model, node, language) : "--"}</b></div>
            </div>
          ) : (
            <>
              <div className="tmcra-vma-detail-grid">
                <div><span>{t("Salience", "显著度")}</span><b>{percent(node.salience)}</b></div>
                <div><span>{t("Confidence", "置信度")}</span><b>{percent(node.confidence)}</b></div>
                <div><span>{t("Sources", "Source")}</span><b>{formatCount(node.sourceCount)}</b></div>
                <div><span>{t("State", "状态")}</span><b>{node.status || "--"}</b></div>
                <div><span>{t("Actor", "对话主体")}</span><b>{actorLabel(node.role, language)}</b></div>
                <div><span>{t("Layer", "记忆层")}</span><b>{node.status === "immutable-source" ? "SOURCE" : "MEMORY"}</b></div>
              </div>
              <code className="tmcra-vma-node-id">{node.id}</code>
            </>
          )}
          {parent && (
            <button type="button" className="tmcra-vma-parent-link" onClick={() => onSelect(parent)}>
              <span>{t("In", "归属")}</span><b>{parent.label}</b>
            </button>
          )}
          {node.tags.length > 0 && <div className="tmcra-vma-tags">{node.tags.slice(0, 12).map((tag) => <span key={tag}>{tag}</span>)}</div>}
          {node.kind === "memory" && (
            <section className="tmcra-vma-evidence">
              <header><span>{t("Source evidence", "Source 证据")}</span><b>{formatCount(Math.max(sourceEvidence.length, node.sourceCount))}</b></header>
              {node.status !== "immutable-source" && viewMode !== "evidence" && (
                <button type="button" className="tmcra-vma-open-evidence" onClick={() => onOpenEvidence(node)}>
                  {t("Open evidence chain", "打开原文证据链")}
                </button>
              )}
              {sourceLoading && <p className="tmcra-vma-muted">{t("Loading immutable Source…", "正在读取不可变 Source…")}</p>}
              {sourceError && <p className="tmcra-vma-error">{sourceError}</p>}
              {!sourceLoading && !sourceError && sourceEvidence.length === 0 && (
                <p className="tmcra-vma-muted">
                  {node.sourceCount > 0
                    ? t("Source is linked. Verbatim text is loaded on demand.", "Source 已完成关联，原文将在需要时按需读取。")
                    : t("No Source payload is attached to this memory yet.", "这个记忆节点暂时没有挂载 Source 原文。")}
                </p>
              )}
              {sourceEvidence.map((item) => (
                <article key={`${item.source_record_id}-${item.evidence_char_start ?? 0}`}>
                  <div><code>{shortId(item.source_record_id)}</code><span>{item.actor_role ?? item.role ?? "Source"}</span></div>
                  <blockquote>{item.text}</blockquote>
                  {item.occurred_at && <time>{item.occurred_at}</time>}
                </article>
              ))}
            </section>
          )}
          {children && children.length > 0 && (
            <section className="tmcra-vma-children">
              <header><span>{globalMode ? globalChildrenLabel(node.kind, language) : childrenLabel(node.kind, language)}</span><b>{formatCount(children.length)}</b></header>
              <ul>
                {children.slice(0, 80).map((child) => (
                  <li key={child.key}>
                    <button type="button" onClick={() => onSelect(child)}>
                      <i style={{ background: child.color }} />
                      <span><b>{child.label}</b><small>{globalMode && model ? globalChildMeta(model, child, language) : `${child.sourceCount} ${language === "zh" ? "条 Source" : "sources"}`}</small></span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </aside>
  );
}

function buildAtlasModel(data: VisualAtlasData): AtlasModel {
  const galaxyRecords = [...data.galaxies];
  const knownGalaxyIds = new Set(galaxyRecords.map((item) => item.id));
  const unassignedGalaxyId = "__unassigned__";
  if (data.sessions.some((session) => !session.galaxy_id || !knownGalaxyIds.has(session.galaxy_id))) {
    galaxyRecords.push({ id: unassignedGalaxyId, label: "Unassigned themes", summary: "Sessions awaiting semantic curation.", salience: 0.25 });
  }
  const chapterById = new Map(data.chapters.map((item) => [item.id, item]));
  const galaxyPosition = new Map<string, Point>();
  const sessionPosition = new Map<string, Point>();
  const chapterPosition = new Map<string, Point>();
  const memoryPosition = new Map<string, Point>();
  const galaxyRadius = Math.max(330, galaxyRecords.length * 72);

  galaxyRecords.forEach((galaxy, index) => {
    const radius = galaxyRecords.length === 1 ? 0 : galaxyRadius;
    const angle = index * GOLDEN_ANGLE - Math.PI / 2;
    galaxyPosition.set(galaxy.id, { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius });
  });

  const sessionsByGalaxy = groupBy(data.sessions, (session) => {
    const id = session.galaxy_id;
    return id && knownGalaxyIds.has(id) ? id : unassignedGalaxyId;
  });
  for (const galaxy of galaxyRecords) {
    const sessions = sessionsByGalaxy.get(galaxy.id) ?? [];
    const center = galaxyPosition.get(galaxy.id) ?? { x: 0, y: 0 };
    const ring = 74 + Math.sqrt(Math.max(1, sessions.length)) * 22;
    sessions.forEach((session, index) => {
      const angle = index * GOLDEN_ANGLE + (galaxy.id.length % 11) * 0.13;
      const distance = ring * (0.65 + (index % 3) * 0.17);
      sessionPosition.set(session.id, { x: center.x + Math.cos(angle) * distance, y: center.y + Math.sin(angle) * distance });
    });
  }

  const chaptersBySession = groupBy(data.chapters, (chapter) => chapter.session_id);
  for (const session of data.sessions) {
    const center = sessionPosition.get(session.id) ?? { x: 0, y: 0 };
    const chapters = chaptersBySession.get(session.id) ?? [];
    chapters.forEach((chapter, index) => {
      const angle = index * GOLDEN_ANGLE + session.id.length * 0.07;
      const distance = 33 + Math.sqrt(Math.max(1, chapters.length)) * 8 + (index % 2) * 9;
      chapterPosition.set(chapter.id, { x: center.x + Math.cos(angle) * distance, y: center.y + Math.sin(angle) * distance });
    });
  }

  const memoriesByChapter = groupBy(data.memories, (memory) => memory.chapter_id ?? `session:${memory.session_id}`);
  for (const chapter of data.chapters) {
    const center = chapterPosition.get(chapter.id) ?? sessionPosition.get(chapter.session_id) ?? { x: 0, y: 0 };
    const memories = memoriesByChapter.get(chapter.id) ?? [];
    memories.forEach((memory, index) => {
      const angle = index * GOLDEN_ANGLE + chapter.id.length * 0.11;
      const distance = 15 + Math.sqrt(Math.max(1, memories.length)) * 4.5;
      memoryPosition.set(memory.id, { x: center.x + Math.cos(angle) * distance, y: center.y + Math.sin(angle) * distance });
    });
  }
  for (const memory of data.memories) {
    if (memoryPosition.has(memory.id)) continue;
    const center = sessionPosition.get(memory.session_id) ?? { x: 0, y: 0 };
    const memories = memoriesByChapter.get(`session:${memory.session_id}`) ?? [];
    const index = memories.findIndex((item) => item.id === memory.id);
    const angle = Math.max(0, index) * GOLDEN_ANGLE;
    memoryPosition.set(memory.id, { x: center.x + Math.cos(angle) * 27, y: center.y + Math.sin(angle) * 27 });
  }

  const nodes: VisualNode[] = [];
  const parentByKey = new Map<string, string | null>();
  const pushNode = (node: VisualNode) => {
    nodes.push(node);
    parentByKey.set(node.key, node.parentKey);
  };
  galaxyRecords.forEach((galaxy, index) => {
    const key = nodeKey("galaxy", galaxy.id);
    const center = galaxyPosition.get(galaxy.id) ?? { x: 0, y: 0 };
    pushNode({
      key, id: galaxy.id, kind: "galaxy", label: galaxy.label, summary: galaxy.summary ?? "",
      x: center.x, y: center.y, radius: 25 + Math.min(14, Math.sqrt(galaxy.memory_count ?? 0) * 0.18),
      color: galaxy.color ?? GALAXY_COLORS[index % GALAXY_COLORS.length], parentKey: null, childKeys: [],
      status: "projected", salience: clamp(Number(galaxy.salience ?? 0.5), 0, 1), confidence: 1,
      sourceCount: galaxy.memory_count ?? 0, sourceRecordIds: [], role: null, tags: [], occurredAt: null,
      sourceApp: null, turnStart: null, turnEnd: null, memory: null,
    });
  });
  data.sessions.forEach((session) => {
    const galaxyId = session.galaxy_id && knownGalaxyIds.has(session.galaxy_id) ? session.galaxy_id : unassignedGalaxyId;
    const key = nodeKey("session", session.id);
    const point = sessionPosition.get(session.id) ?? { x: 0, y: 0 };
    pushNode({
      key, id: session.id, kind: "session", label: session.label, summary: session.summary ?? "",
      x: point.x, y: point.y, radius: 10 + Math.min(8, Math.log2((session.memory_count ?? 0) + 1)),
      color: colorForSession(session.source_app, galaxyRecords.findIndex((item) => item.id === galaxyId)),
      parentKey: nodeKey("galaxy", galaxyId), childKeys: [], status: session.status ?? "active",
      salience: clamp(Number(session.salience ?? 0.5), 0, 1), confidence: 1, sourceCount: session.memory_count ?? 0,
      sourceRecordIds: [], role: null, tags: [], occurredAt: session.updated_at ?? session.created_at ?? null,
      sourceApp: session.source_app ?? null, turnStart: null, turnEnd: null, memory: null,
    });
  });
  data.chapters.forEach((chapter) => {
    const key = nodeKey("chapter", chapter.id);
    const point = chapterPosition.get(chapter.id) ?? sessionPosition.get(chapter.session_id) ?? { x: 0, y: 0 };
    pushNode({
      key, id: chapter.id, kind: "chapter", label: chapter.label, summary: chapter.summary ?? "",
      x: point.x, y: point.y, radius: 6.5, color: "#D6D2C8", parentKey: nodeKey("session", chapter.session_id),
      childKeys: [], status: "projected", salience: clamp(Number(chapter.salience ?? 0.45), 0, 1), confidence: 1,
      sourceCount: (chapter.memory_ids ?? []).length, sourceRecordIds: [], role: null, tags: [], occurredAt: null,
      sourceApp: null, turnStart: chapter.turn_start ?? null, turnEnd: chapter.turn_end ?? null, memory: null,
    });
  });
  data.memories.forEach((memory) => {
    const key = nodeKey("memory", memory.id);
    const parentKey = memory.chapter_id && chapterById.has(memory.chapter_id)
      ? nodeKey("chapter", memory.chapter_id)
      : nodeKey("session", memory.session_id);
    const point = memoryPosition.get(memory.id) ?? sessionPosition.get(memory.session_id) ?? { x: 0, y: 0 };
    pushNode({
      key, id: memory.id, kind: "memory", label: memory.label, summary: memory.summary ?? "",
      x: point.x, y: point.y, radius: 3.8, color: memoryColor(memory.role), parentKey, childKeys: [],
      status: memory.state ?? "committed", salience: clamp(Number(memory.salience ?? 0.35), 0, 1),
      confidence: clamp(Number(memory.confidence ?? 0.75), 0, 1), sourceCount: (memory.source_record_ids ?? []).length,
      sourceRecordIds: memory.source_record_ids ?? [], role: memory.role ?? null, tags: memory.tags ?? [],
      occurredAt: memory.occurred_at ?? null, sourceApp: null, turnStart: null, turnEnd: null, memory,
    });
  });

  const nodeByKey = new Map(nodes.map((node) => [node.key, node]));
  const keyByReference = new Map<string, string>();
  const ambiguousReferences = new Set<string>();
  for (const node of nodes) {
    keyByReference.set(node.key, node.key);
    const previous = keyByReference.get(node.id);
    if (previous && previous !== node.key) ambiguousReferences.add(node.id);
    else keyByReference.set(node.id, node.key);
  }
  const resolveReference = (value: string) => {
    if (keyByReference.has(value)) return keyByReference.get(value)!;
    const plain = value.includes(":") ? value.slice(value.indexOf(":") + 1) : value;
    if (ambiguousReferences.has(plain)) return null;
    return keyByReference.get(plain) ?? null;
  };

  const edgeMap = new Map<string, VisualEdge>();
  const addEdge = (edge: VisualEdge) => {
    if (edge.source === edge.target || !nodeByKey.has(edge.source) || !nodeByKey.has(edge.target)) return;
    const edgeKey = `${edge.source}|${edge.target}|${edge.type}`;
    if (!edgeMap.has(edgeKey)) edgeMap.set(edgeKey, edge);
  };
  for (const node of nodes) {
    if (node.parentKey) addEdge({ id: `hierarchy:${node.key}`, source: node.parentKey, target: node.key, type: "contains", weight: 0.55, origin: "hierarchy", evidenceIds: [] });
  }
  for (const edge of data.edges ?? []) {
    const source = resolveReference(edge.source);
    const target = resolveReference(edge.target);
    if (source && target) addEdge({ id: edge.id, source, target, type: edge.type, weight: clamp(Number(edge.weight ?? 0.65), 0.2, 1), origin: edge.origin ?? "stored", evidenceIds: edge.evidence_ids ?? [] });
  }

  for (const node of nodes) {
    const children = nodes.filter((candidate) => candidate.parentKey === node.key).map((child) => child.key);
    node.childKeys.push(...children);
  }
  const edges = [...edgeMap.values()];
  const bounds = nodes.reduce(
    (current, node) => ({
      minX: Math.min(current.minX, node.x - node.radius * 2),
      minY: Math.min(current.minY, node.y - node.radius * 2),
      maxX: Math.max(current.maxX, node.x + node.radius * 2),
      maxY: Math.max(current.maxY, node.y + node.radius * 2),
    }),
    { minX: -100, minY: -100, maxX: 100, maxY: 100 },
  );
  return {
    nodes,
    nodeByKey,
    edges,
    bounds,
    counts: {
      galaxies: galaxyRecords.length,
      sessions: data.sessions.length,
      chapters: data.chapters.length,
      memories: data.memories.filter((memory) => memory.state !== "immutable-source").length,
      sources: data.memories.filter((memory) => memory.state === "immutable-source").length,
    },
  };
}

function nodeKey(kind: VisualAtlasNodeKind, id: string) {
  return `${kind}:${id}`;
}

function groupBy<T>(items: T[], keyOf: (item: T) => string) {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const key = keyOf(item);
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return groups;
}

function colorForSession(sourceApp: string | null | undefined, galaxyIndex: number) {
  void sourceApp;
  return GALAXY_COLORS[Math.max(0, galaxyIndex) % GALAXY_COLORS.length];
}

function memoryColor(role: string | null | undefined) {
  const normalized = (role ?? "").toLowerCase();
  if (normalized.includes("mixed")) return "#A86F23";
  if (normalized.includes("user") || normalized.includes("human")) return "#B85D41";
  if (normalized.includes("assistant") || normalized.includes("agent") || normalized.includes("model")) return "#557763";
  return "#626A76";
}

function actorLabel(role: string | null | undefined, language: "en" | "zh") {
  const normalized = (role ?? "").toLowerCase();
  if (normalized.includes("mixed")) return language === "zh" ? "用户 + Agent" : "USER + AGENT";
  if (normalized.includes("user") || normalized.includes("human")) return language === "zh" ? "用户" : "USER";
  if (normalized.includes("assistant") || normalized.includes("agent") || normalized.includes("model")) return "AGENT";
  return language === "zh" ? "未标注" : "UNKNOWN";
}

function buildFocusTrail(model: AtlasModel | null, node: VisualNode | null) {
  if (!model || !node) return [];
  const trail: VisualNode[] = [];
  let current: VisualNode | null = node;
  while (current) {
    trail.unshift(current);
    current = current.parentKey ? model.nodeByKey.get(current.parentKey) ?? null : null;
  }
  return trail;
}

function buildGlobalFocusTrail(model: AtlasModel | null, node: VisualNode | null) {
  if (!model || !node) return [];
  const trail: VisualNode[] = [];
  const add = (candidate: VisualNode | null | undefined) => {
    if (candidate && !trail.some((item) => item.key === candidate.key)) trail.push(candidate);
  };
  const episode = node.kind === "chapter" ? node : findAncestor(model, node, "chapter");
  const session = node.kind === "session" ? node : findAncestor(model, node, "session");
  const domain = node.kind === "galaxy"
    ? node
    : session?.parentKey
      ? model.nodeByKey.get(session.parentKey) ?? null
      : findAncestor(model, node, "galaxy");
  add(domain);
  add(episode);
  if (node.kind === "memory") add(node);
  return trail;
}

function findAncestor(model: AtlasModel, node: VisualNode, kind: VisualAtlasNodeKind) {
  let current: VisualNode | null = node;
  while (current) {
    if (current.kind === kind) return current;
    current = current.parentKey ? model.nodeByKey.get(current.parentKey) ?? null : null;
  }
  return null;
}

function globalChildren(model: AtlasModel, node: VisualNode) {
  if (node.kind === "galaxy") {
    const sessions = model.nodes.filter((candidate) => candidate.kind === "session" && candidate.parentKey === node.key);
    const sessionKeys = new Set(sessions.map((session) => session.key));
    const episodes = model.nodes.filter((candidate) => candidate.kind === "chapter" && candidate.parentKey && sessionKeys.has(candidate.parentKey));
    if (episodes.length) return sortNarrativeNodes(episodes);
    return sortNarrativeNodes(model.nodes.filter((candidate) => (
      candidate.kind === "memory"
      && candidate.parentKey != null
      && sessionKeys.has(candidate.parentKey)
      && candidate.status !== "immutable-source"
    )));
  }
  if (node.kind === "session") {
    return sortNarrativeNodes(model.nodes.filter((candidate) => candidate.kind === "chapter" && candidate.parentKey === node.key));
  }
  if (node.kind === "chapter") {
    return sortNarrativeNodes(model.nodes.filter((candidate) => (
      candidate.kind === "memory"
      && candidate.parentKey === node.key
      && candidate.status !== "immutable-source"
    )));
  }
  return [];
}

function globalParent(model: AtlasModel, node: VisualNode) {
  if (node.kind === "galaxy") return null;
  if (node.kind === "session") return node.parentKey ? model.nodeByKey.get(node.parentKey) ?? null : null;
  if (node.kind === "chapter") {
    const session = node.parentKey ? model.nodeByKey.get(node.parentKey) ?? null : null;
    return session?.parentKey ? model.nodeByKey.get(session.parentKey) ?? null : null;
  }
  const parent = node.parentKey ? model.nodeByKey.get(node.parentKey) ?? null : null;
  if (parent?.kind === "session") return parent.parentKey ? model.nodeByKey.get(parent.parentKey) ?? null : null;
  return parent;
}

function summarizeGlobalNode(model: AtlasModel, node: VisualNode) {
  const related = [node, ...collectAncestors(model, node), ...collectDescendants(model, node)];
  const writerMemories = related.filter((item) => item.kind === "memory" && item.status !== "immutable-source");
  return {
    sessions: related.filter((item) => item.kind === "session").length,
    chapters: related.filter((item) => item.kind === "chapter").length,
    memories: writerMemories.length,
    sources: writerMemories.reduce((sum, item) => sum + item.sourceCount, 0),
  };
}

function collectAncestors(model: AtlasModel, node: VisualNode) {
  const ancestors: VisualNode[] = [];
  let current = node.parentKey ? model.nodeByKey.get(node.parentKey) ?? null : null;
  while (current) {
    ancestors.push(current);
    current = current.parentKey ? model.nodeByKey.get(current.parentKey) ?? null : null;
  }
  return ancestors;
}

function collectDescendants(model: AtlasModel, node: VisualNode) {
  const descendants: VisualNode[] = [];
  const queue = [...node.childKeys];
  const visited = new Set<string>();
  while (queue.length) {
    const key = queue.shift()!;
    if (visited.has(key)) continue;
    visited.add(key);
    const child = model.nodeByKey.get(key);
    if (!child) continue;
    descendants.push(child);
    queue.push(...child.childKeys);
  }
  return descendants;
}

function sortNarrativeNodes(nodes: VisualNode[]) {
  return [...nodes].sort((a, b) => (
    b.salience - a.salience
    || (b.occurredAt ?? "").localeCompare(a.occurredAt ?? "")
    || a.label.localeCompare(b.label)
  ));
}

function globalChildMeta(model: AtlasModel, node: VisualNode, language: "en" | "zh") {
  const stats = summarizeGlobalNode(model, node);
  if (node.kind === "chapter") {
    return language === "zh"
      ? `${stats.memories} 条关键记忆 · ${stats.sources} 条原文`
      : `${stats.memories} key memories · ${stats.sources} sources`;
  }
  if (node.kind === "memory") {
    return language === "zh" ? `${node.sourceCount} 条原文依据` : `${node.sourceCount} source records`;
  }
  return language === "zh"
    ? `${stats.chapters} 个事项 · ${stats.memories} 条关键记忆`
    : `${stats.chapters} milestones · ${stats.memories} key memories`;
}

function globalKindLabel(kind: VisualAtlasNodeKind, language: "en" | "zh") {
  const labels = {
    galaxy: language === "zh" ? "工作领域" : "Work area",
    session: language === "zh" ? "对话脉络" : "Conversation thread",
    chapter: language === "zh" ? "事项与里程碑" : "Milestone",
    memory: language === "zh" ? "关键记忆" : "Key memory",
  };
  return labels[kind];
}

function globalChildrenLabel(kind: VisualAtlasNodeKind, language: "en" | "zh") {
  if (kind === "galaxy" || kind === "session") return language === "zh" ? "事项与里程碑" : "Milestones";
  if (kind === "chapter") return language === "zh" ? "决策、结果与待办" : "Decisions, results, and open work";
  return language === "zh" ? "相关记忆" : "Related memories";
}

function humanStatus(status: string, language: "en" | "zh") {
  const normalized = status.toLowerCase();
  if (normalized.includes("fail")) return language === "zh" ? "存在失败" : "Has failures";
  if (normalized.includes("stale") || normalized.includes("pending") || normalized.includes("open")) return language === "zh" ? "仍在推进" : "In progress";
  if (normalized.includes("active") || normalized.includes("build") || normalized.includes("running")) return language === "zh" ? "持续进行" : "Active";
  if (normalized.includes("commit") || normalized.includes("ready") || normalized.includes("project")) return language === "zh" ? "已形成记录" : "Recorded";
  return status || (language === "zh" ? "未标注" : "Unspecified");
}

function humanRange(model: AtlasModel, node: VisualNode, language: "en" | "zh") {
  if (node.turnStart != null) {
    return `T${node.turnStart}${node.turnEnd != null && node.turnEnd !== node.turnStart ? `–${node.turnEnd}` : ""}`;
  }
  const candidates = [node, ...collectDescendants(model, node)]
    .map((item) => item.occurredAt)
    .filter((value): value is string => Boolean(value))
    .map((value) => ({ value, timestamp: Date.parse(value) }))
    .filter((item) => Number.isFinite(item.timestamp))
    .sort((a, b) => a.timestamp - b.timestamp);
  if (!candidates.length) return language === "zh" ? "未标注" : "Not recorded";
  const format = (value: string) => {
    const date = new Date(value);
    return new Intl.DateTimeFormat(language === "zh" ? "zh-CN" : "en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
    }).format(date);
  };
  const first = format(candidates[0].value);
  const last = format(candidates[candidates.length - 1].value);
  return first === last ? first : `${first} – ${last}`;
}

function readableScopeName(value: string | null | undefined, language: "en" | "zh") {
  const fallback = language === "zh" ? "我的记忆空间" : "My memory space";
  if (!value) return fallback;
  const projectMarker = "-project-";
  const projectIndex = value.indexOf(projectMarker);
  if (projectIndex >= 0) {
    const projectName = value
      .slice(projectIndex + projectMarker.length)
      .replace(/-[0-9a-f]{12,}$/i, "")
      .replace(/[-_]+/g, " ")
      .trim();
    if (projectName) return projectName;
  }
  if (/^(personal|enterprise)-/i.test(value) || value.length > 54) return fallback;
  return value;
}

function kindLabel(kind: VisualAtlasNodeKind, language: "en" | "zh") {
  const labels = {
    galaxy: language === "zh" ? "主题分组" : "Theme group",
    session: "Session",
    chapter: language === "zh" ? "阶段" : "Episode",
    memory: language === "zh" ? "记忆节点" : "Memory node",
  };
  return labels[kind];
}

function childrenLabel(kind: VisualAtlasNodeKind, language: "en" | "zh") {
  const labels = {
    galaxy: language === "zh" ? "Sessions" : "Sessions",
    session: language === "zh" ? "阶段" : "Episodes",
    chapter: language === "zh" ? "记忆节点" : "Memory nodes",
    memory: language === "zh" ? "下游节点" : "Downstream nodes",
  };
  return labels[kind];
}

function statusLabel(status: NonNullable<VisualAtlasData["projection_state"]>, language: "en" | "zh") {
  const labels = {
    fallback: language === "zh" ? "证据底图" : "Evidence map",
    building: language === "zh" ? "正在构建" : "Building",
    ready: language === "zh" ? "已就绪" : "Ready",
    stale: language === "zh" ? "待更新" : "Stale",
    failed: language === "zh" ? "构建失败" : "Failed",
  };
  return labels[status];
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, Number.isFinite(value) ? value : minimum));
}

function shortId(value: string) {
  return value.length > 20 ? `${value.slice(0, 8)}…${value.slice(-8)}` : value;
}

function formatCount(value: number) {
  return new Intl.NumberFormat("en-US", { notation: value > 9999 ? "compact" : "standard", maximumFractionDigits: 1 }).format(value);
}

function percent(value: number) {
  return `${Math.round(clamp(value, 0, 1) * 100)}%`;
}
