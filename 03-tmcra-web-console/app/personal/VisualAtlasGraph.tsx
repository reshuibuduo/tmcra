"use client";

import cytoscape, {
  type Core,
  type ElementDefinition,
  type LayoutOptions,
  type StylesheetJson,
} from "cytoscape";
import elk from "cytoscape-elk";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";

import type { VisualEdge, VisualNode } from "./VisualMemoryAtlas";

cytoscape.use(elk);

export type VisualAtlasViewMode = "global" | "threads" | "evolution" | "relations" | "evidence";

export type VisualAtlasGraphHandle = {
  fit: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
};

type VisualAtlasGraphProps = {
  nodes: VisualNode[];
  edges: VisualEdge[];
  mode: VisualAtlasViewMode;
  selectedKey: string | null;
  scopeLabel: string;
  language: "zh" | "en";
  onSelect: (node: VisualNode) => void;
};

type GraphView = {
  elements: ElementDefinition[];
  rootId: string | null;
  shown: number;
  total: number;
  emptyMessage?: string;
  emptyHint?: string;
};

const ROOT_ID = "__tmcra_atlas_root__";
const DEFAULT_GLOBAL_BRANCHES = 8;
const GLOBAL_BRANCH_STEP = 8;
const MAX_RELATIONS = 6;
const MAX_SOURCES = 10;
const NARRATIVE_COLORS = ["#86a9e8", "#dfa0a2", "#94c69e", "#dcb475", "#b29adb", "#72b5c2"];

const GRAPH_STYLE: StylesheetJson = [
  {
    selector: "node",
    style: {
      width: "data(width)",
      height: "data(height)",
      shape: "round-rectangle",
      "background-color": "#fbfaf7",
      "background-opacity": 1,
      "border-color": "data(color)",
      "border-width": 1.5,
      label: "data(label)",
      color: "#30363b",
      "font-family": "Inter, ui-sans-serif, system-ui, sans-serif",
      "font-size": 11,
      "font-weight": 500,
      "text-wrap": "wrap",
      "text-overflow-wrap": "anywhere",
      "text-max-width": "data(textWidth)",
      "text-valign": "center",
      "text-halign": "center",
      "text-justification": "center",
      "overlay-opacity": 0,
      "min-zoomed-font-size": 0,
    },
  },
  {
    selector: "node[root = 1]",
    style: {
      width: 210,
      height: 76,
      "background-color": "#edf2fc",
      "border-width": 2.5,
      "font-size": 14,
      "font-weight": 600,
      "text-max-width": "176px",
    },
  },
  {
    selector: "node[kind = 'galaxy']",
    style: {
      "background-color": "#edf5f1",
      color: "#31574d",
    },
  },
  {
    selector: "node[kind = 'session']",
    style: {
      "background-color": "#eff2fb",
      color: "#394b72",
    },
  },
  {
    selector: "node[kind = 'chapter']",
    style: {
      "background-color": "#f8f2e8",
      color: "#6d5636",
    },
  },
  {
    selector: "node[kind = 'memory']",
    style: {
      "background-color": "#f2f3f4",
      color: "#333a40",
    },
  },
  {
    selector: "node[source = 1]",
    style: {
      "background-color": "#fbf7f2",
      "border-style": "dashed",
      color: "#565d62",
      "font-size": 10,
    },
  },
  // These selectors must follow the card styles above. Cytoscape resolves
  // matching selectors in order, and the narrative view is not a card view.
  {
    selector: "node[visual = 'sketch']",
    style: {
      width: "data(width)",
      height: "data(height)",
      shape: "ellipse",
      "background-color": "data(color)",
      "background-opacity": 0.14,
      "background-image": "data(icon)",
      "background-fit": "cover",
      "background-clip": "node",
      "background-image-opacity": 0.72,
      "border-color": "data(color)",
      "border-opacity": 0.82,
      "border-width": 2,
      color: "#6f777c",
      "font-size": 12,
      "font-weight": 500,
      "text-wrap": "wrap",
      "text-overflow-wrap": "whitespace",
      "text-max-width": "154px",
      "text-valign": "bottom",
      "text-halign": "center",
      "text-margin-y": 16,
      "min-zoomed-font-size": 0,
    },
  },
  {
    selector: "node[visual = 'sketch'][root = 1]",
    style: {
      width: 94,
      height: 94,
      "background-color": "#3658D6",
      "background-opacity": 0.14,
      "border-color": "#3658D6",
      "border-opacity": 0.9,
      "border-style": "solid",
      "border-width": 2,
      color: "#356fd2",
      "font-size": 16,
      "font-weight": 500,
      "text-max-width": "190px",
      "text-margin-y": 20,
    },
  },
  {
    selector: "node:selected",
    style: {
      "border-color": "#3658d6",
      "border-width": 3,
      "background-color": "#edf2fc",
    },
  },
  {
    selector: "node[visual = 'sketch']:selected",
    style: {
      "background-color": "#3658D6",
      "background-opacity": 0.2,
      "border-color": "#3658D6",
      "border-opacity": 1,
      "border-style": "solid",
      "border-width": 3,
    },
  },
  {
    selector: "node[visual = 'sketch'].label-top",
    style: {
      "text-valign": "top",
      "text-halign": "center",
      "text-margin-y": -14,
    },
  },
  {
    selector: "node[visual = 'sketch'].label-bottom",
    style: {
      "text-valign": "bottom",
      "text-halign": "center",
      "text-margin-y": 14,
    },
  },
  {
    selector: "node[visual = 'sketch'].label-left",
    style: {
      "text-valign": "center",
      "text-halign": "left",
      "text-margin-x": -14,
      "text-margin-y": 0,
    },
  },
  {
    selector: "node[visual = 'sketch'].label-right",
    style: {
      "text-valign": "center",
      "text-halign": "right",
      "text-margin-x": 14,
      "text-margin-y": 0,
    },
  },
  {
    selector: "edge",
    style: {
      width: 1.2,
      "line-color": "#52606c",
      "target-arrow-color": "#52606c",
      "target-arrow-shape": "triangle",
      "arrow-scale": 0.72,
      "curve-style": "bezier",
      opacity: 0.72,
      label: "data(label)",
      color: "#606a72",
      "font-size": 8,
      "text-background-color": "#fbfaf7",
      "text-background-opacity": 0.94,
      "text-background-padding": "3px",
      "text-rotation": "autorotate",
      "min-zoomed-font-size": 7,
    },
  },
  {
    selector: "edge[kind = 'branch']",
    style: {
      "target-arrow-shape": "none",
      "line-color": "#687581",
      "curve-style": "unbundled-bezier",
      "control-point-distances": "data(bend)",
      "control-point-weights": 0.5,
      width: 1,
    },
  },
  {
    selector: "edge[kind = 'timeline']",
    style: {
      "curve-style": "bezier",
      "line-color": "#8a7556",
      "target-arrow-color": "#8a7556",
      width: 1.5,
    },
  },
  {
    selector: "edge[type = 'contradicts']",
    style: {
      "line-color": "#ec7774",
      "target-arrow-color": "#ec7774",
      "line-style": "dashed",
    },
  },
  {
    selector: "edge[type = 'updates']",
    style: {
      "line-color": "#e1aa59",
      "target-arrow-color": "#e1aa59",
    },
  },
  {
    selector: "edge[type = 'branches'], edge[type = 'converges']",
    style: {
      "line-color": "#8ea8ff",
      "target-arrow-color": "#8ea8ff",
    },
  },
  {
    selector: "edge[layoutOnly = 1]",
    style: {
      opacity: 0,
      "target-arrow-shape": "none",
    },
  },
];

const VisualAtlasGraph = forwardRef<VisualAtlasGraphHandle, VisualAtlasGraphProps>(
  function VisualAtlasGraph({ nodes, edges, mode, selectedKey, scopeLabel, language, onSelect }, ref) {
    const containerRef = useRef<HTMLDivElement>(null);
    const cyRef = useRef<Core | null>(null);
    const onSelectRef = useRef(onSelect);
    const nodeByKeyRef = useRef(new Map<string, VisualNode>());
    const layoutSignatureRef = useRef("");
    const reducedMotionRef = useRef(false);
    const [globalBranchLimit, setGlobalBranchLimit] = useState(DEFAULT_GLOBAL_BRANCHES);
    const nodeByKey = useMemo(() => new Map(nodes.map((node) => [node.key, node])), [nodes]);
    const view = useMemo(
      () => buildGraphView(nodes, edges, nodeByKey, mode, selectedKey, scopeLabel, language, globalBranchLimit),
      [edges, globalBranchLimit, language, mode, nodeByKey, nodes, scopeLabel, selectedKey],
    );
    const visibleNodes = useMemo(() => view.elements.flatMap((element) => {
      const key = typeof element.data?.nodeKey === "string" ? element.data.nodeKey : "";
      const node = key ? nodeByKey.get(key) : null;
      return node ? [node] : [];
    }), [nodeByKey, view.elements]);

    useEffect(() => {
      onSelectRef.current = onSelect;
    }, [onSelect]);

    useEffect(() => {
      nodeByKeyRef.current = nodeByKey;
    }, [nodeByKey]);

    useEffect(() => {
      setGlobalBranchLimit(DEFAULT_GLOBAL_BRANCHES);
    }, [mode, selectedKey]);

    useImperativeHandle(ref, () => ({
      fit: () => cyRef.current?.fit(undefined, 86),
      zoomIn: () => zoomBy(cyRef.current, 1.2),
      zoomOut: () => zoomBy(cyRef.current, 0.82),
    }), []);

    useEffect(() => {
      const container = containerRef.current;
      if (!container) return;
      layoutSignatureRef.current = "";

      const cy = cytoscape({
        container,
        elements: [],
        style: GRAPH_STYLE,
        minZoom: 0.28,
        maxZoom: 3.2,
        boxSelectionEnabled: false,
        autoungrabify: false,
      });
      cyRef.current = cy;
      const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
      const syncMotionPreference = () => {
        reducedMotionRef.current = motionQuery.matches;
      };
      syncMotionPreference();
      motionQuery.addEventListener?.("change", syncMotionPreference);

      cy.on("tap", "node", (event) => {
        const key = String(event.target.data("nodeKey") ?? "");
        const node = nodeByKeyRef.current.get(key);
        if (node) onSelectRef.current(node);
      });
      cy.on("mouseover", "node", (event) => {
        event.target.style("border-width", event.target.selected() ? 3 : 2.5);
        container.style.cursor = event.target.data("virtual") ? "default" : "pointer";
      });
      cy.on("mouseout", "node", (event) => {
        event.target.style("border-width", event.target.data("root") ? 2.5 : 1.5);
        container.style.cursor = "grab";
      });

      const resizeObserver = new ResizeObserver(() => {
        cy.resize();
      });
      resizeObserver.observe(container);

      return () => {
        resizeObserver.disconnect();
        motionQuery.removeEventListener?.("change", syncMotionPreference);
        cy.destroy();
        if (cyRef.current === cy) cyRef.current = null;
      };
    }, []);

    useEffect(() => {
      const cy = cyRef.current;
      if (!cy) return;
      const desiredIds = new Set(view.elements.flatMap((element) => {
        const id = element.data?.id;
        return typeof id === "string" ? [id] : [];
      }));
      const structuralSignature = `${mode}|${view.rootId ?? ""}|${[...desiredIds].sort().join("|")}`;
      const structureChanged = structuralSignature !== layoutSignatureRef.current;

      cy.batch(() => {
        cy.elements().forEach((element) => {
          if (!desiredIds.has(element.id())) element.remove();
        });
        for (const definition of view.elements) {
          const id = typeof definition.data?.id === "string" ? definition.data.id : "";
          if (!id) continue;
          const current = cy.getElementById(id);
          if (current.length) current.data(definition.data ?? {});
          else cy.add(definition);
        }
        cy.$(":selected").unselect();
        if (selectedKey && cy.getElementById(selectedKey).length) cy.getElementById(selectedKey).select();
        else if (view.rootId && cy.getElementById(view.rootId).length) cy.getElementById(view.rootId).select();
      });

      if (structureChanged) {
        if (cy.nodes().length) runLayout(cy, mode, view.rootId, !reducedMotionRef.current);
        layoutSignatureRef.current = structuralSignature;
      }
    }, [mode, selectedKey, view.elements, view.rootId]);

    return (
      <div className="tmcra-vma-graph-stage">
        <div
          ref={containerRef}
          className="tmcra-vma-cytoscape"
          role="application"
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === "+" || event.key === "=") {
              event.preventDefault();
              zoomBy(cyRef.current, 1.2);
            } else if (event.key === "-") {
              event.preventDefault();
              zoomBy(cyRef.current, 0.82);
            } else if (event.key === "0") {
              event.preventDefault();
              cyRef.current?.fit(undefined, 86);
            }
          }}
          aria-label={graphAriaLabel(mode, language)}
          data-mode={mode}
          data-visible-nodes={view.shown}
          data-total-nodes={view.total}
        />
        {view.shown < view.total && (
          <div className="tmcra-vma-view-limit">
            <span>
              {language === "zh"
                ? `当前展示 ${view.shown} / ${view.total} 个关键节点`
                : `Showing ${view.shown} of ${view.total} key nodes`}
            </span>
            {(mode === "global" || mode === "threads" || mode === "evolution" || mode === "relations") && (
              <button type="button" onClick={() => setGlobalBranchLimit((current) => current + GLOBAL_BRANCH_STEP)}>
                {language === "zh" ? "展开更多" : "Show more"}
              </button>
            )}
          </div>
        )}
        {view.emptyMessage && (
          <div className="tmcra-vma-graph-empty">
            <strong>{view.emptyMessage}</strong>
            <span>{view.emptyHint ?? (language === "zh" ? "在证据脉络中打开阶段并选择记忆节点" : "Open an episode in Evidence trail, then select a memory node")}</span>
          </div>
        )}
        <details className="tmcra-vma-keyboard-view">
          <summary>{language === "zh" ? "列表视图" : "List view"}</summary>
          <p>{language === "zh" ? "按层级浏览当前画布中的节点；选择后会同步检查器。" : "Browse the nodes currently on the canvas by hierarchy; selection is mirrored in the inspector."}</p>
          <div>
            {visibleNodes.map((node) => (
              <button key={node.key} type="button" onClick={() => onSelect(node)} aria-current={node.key === selectedKey ? "true" : undefined}>
                <span>{visualKindLabel(node, language, mode)}</span>
                <b>{node.label}</b>
              </button>
            ))}
          </div>
        </details>
      </div>
    );
  },
);

export default VisualAtlasGraph;

function buildGraphView(
  nodes: VisualNode[],
  edges: VisualEdge[],
  nodeByKey: Map<string, VisualNode>,
  mode: VisualAtlasViewMode,
  selectedKey: string | null,
  scopeLabel: string,
  language: "zh" | "en",
  globalBranchLimit: number,
): GraphView {
  if (mode === "global") {
    return buildGlobalMemoryView(nodes, edges, nodeByKey, selectedKey, scopeLabel, language, globalBranchLimit);
  }
  if (mode === "evolution") return buildEvolutionView(nodes, nodeByKey, selectedKey, globalBranchLimit);
  if (mode === "relations") return buildRelationView(nodes, edges, selectedKey, language, globalBranchLimit);
  if (mode === "evidence") return buildEvidenceView(nodes, nodeByKey, selectedKey, language);
  return buildThreadView(nodes, nodeByKey, selectedKey, scopeLabel, language, globalBranchLimit);
}

function buildGlobalMemoryView(
  nodes: VisualNode[],
  edges: VisualEdge[],
  nodeByKey: Map<string, VisualNode>,
  selectedKey: string | null,
  scopeLabel: string,
  language: "zh" | "en",
  limit: number,
): GraphView {
  const selected = selectedKey ? nodeByKey.get(selectedKey) ?? null : null;
  const focus = resolveGlobalFocus(nodeByKey, selected);
  const elements: ElementDefinition[] = [];

  if (!focus) {
    const allDomains = rankNodes(nodes.filter((node) => node.kind === "galaxy"), Number.POSITIVE_INFINITY);
    const domains = allDomains.slice(0, limit);
    elements.push(virtualRoot(scopeLabel || (language === "zh" ? "我的长期记忆" : "My long-term memory")));
    domains.forEach((node, index) => {
      elements.push(nodeElement(node, false, "sketch", NARRATIVE_COLORS[index % NARRATIVE_COLORS.length]));
      elements.push(branchEdge(ROOT_ID, node.key));
    });
    return { elements, rootId: ROOT_ID, shown: domains.length, total: allDomains.length };
  }

  if (focus.kind === "galaxy") {
    const sessionKeys = new Set(nodes
      .filter((node) => node.kind === "session" && node.parentKey === focus.key)
      .map((node) => node.key));
    const allEpisodes = rankNodes(
      nodes.filter((node) => node.kind === "chapter" && node.parentKey && sessionKeys.has(node.parentKey)),
      Number.POSITIVE_INFINITY,
    );
    const fallbackMemories = allEpisodes.length
      ? []
      : rankNodes(
        nodes.filter((node) => node.kind === "memory" && node.parentKey && sessionKeys.has(node.parentKey) && node.status !== "immutable-source"),
        Number.POSITIVE_INFINITY,
      );
    const allChildren = allEpisodes.length ? allEpisodes : fallbackMemories;
    const children = allChildren.slice(0, limit);
    elements.push(nodeElement(focus, true, "sketch"));
    children.forEach((child, index) => {
      elements.push(nodeElement(child, false, "sketch", NARRATIVE_COLORS[index % NARRATIVE_COLORS.length]));
      elements.push(branchEdge(focus.key, child.key));
    });
    appendVisibleSemanticRelations(elements, edges, new Set(children.map((node) => node.key)));
    return { elements, rootId: focus.key, shown: children.length, total: allChildren.length };
  }

  const episode = focus.kind === "chapter" ? focus : findEpisodeAncestor(nodeByKey, focus);
  if (!episode) {
    return { elements: [nodeElement(focus, true, "sketch")], rootId: focus.key, shown: 1, total: 1 };
  }
  const allMemories = rankNodes(
    nodes.filter((node) => node.kind === "memory" && node.parentKey === episode.key && node.status !== "immutable-source"),
    Number.POSITIVE_INFINITY,
  );
  const memories = allMemories.slice(0, limit);
  elements.push(nodeElement(episode, true, "sketch"));
  memories.forEach((memory, index) => {
    elements.push(nodeElement(memory, false, "sketch", NARRATIVE_COLORS[index % NARRATIVE_COLORS.length]));
    elements.push(branchEdge(episode.key, memory.key));
  });
  appendVisibleSemanticRelations(elements, edges, new Set(memories.map((node) => node.key)));
  return { elements, rootId: episode.key, shown: memories.length, total: allMemories.length };
}

function resolveGlobalFocus(nodeByKey: Map<string, VisualNode>, selected: VisualNode | null): VisualNode | null {
  if (!selected) return null;
  if (selected.kind === "galaxy" || selected.kind === "chapter" || selected.kind === "memory") return selected;
  return selected.parentKey ? nodeByKey.get(selected.parentKey) ?? null : null;
}

function findEpisodeAncestor(nodeByKey: Map<string, VisualNode>, node: VisualNode): VisualNode | null {
  let current: VisualNode | null = node;
  while (current) {
    if (current.kind === "chapter") return current;
    current = current.parentKey ? nodeByKey.get(current.parentKey) ?? null : null;
  }
  return null;
}

function appendVisibleSemanticRelations(
  elements: ElementDefinition[],
  edges: VisualEdge[],
  visibleKeys: Set<string>,
) {
  const semantic = edges
    .filter((edge) => edge.origin !== "hierarchy" && edge.type.toLowerCase() !== "continues")
    .filter((edge) => visibleKeys.has(edge.source) && visibleKeys.has(edge.target))
    .sort((a, b) => b.weight - a.weight || a.id.localeCompare(b.id))
    .slice(0, MAX_RELATIONS);
  for (const edge of semantic) elements.push(relationEdge(edge));
}

function buildThreadView(
  nodes: VisualNode[],
  nodeByKey: Map<string, VisualNode>,
  selectedKey: string | null,
  scopeLabel: string,
  language: "zh" | "en",
  limit: number,
): GraphView {
  const selected = selectedKey ? nodeByKey.get(selectedKey) ?? null : null;
  const elements: ElementDefinition[] = [];

  if (!selected) {
    const allGalaxies = rankNodes(nodes.filter((node) => node.kind === "galaxy"), Number.POSITIVE_INFINITY);
    const galaxies = allGalaxies.slice(0, limit);
    elements.push(virtualRoot(scopeLabel || (language === "zh" ? "我的长期记忆" : "My long-term memory")));
    galaxies.forEach((node, index) => {
      elements.push(nodeElement(node, false, "sketch", NARRATIVE_COLORS[index % NARRATIVE_COLORS.length]));
      elements.push(branchEdge(ROOT_ID, node.key));
    });
    return { elements, rootId: ROOT_ID, shown: galaxies.length, total: allGalaxies.length };
  }

  if (selected.kind === "memory" && selected.status !== "immutable-source") {
    const sourceIds = new Set(selected.sourceRecordIds);
    const allSources = rankNodes(
      nodes.filter((node) => (
        node.status === "immutable-source"
        && node.sourceRecordIds.some((id) => sourceIds.has(id))
      )),
      Number.POSITIVE_INFINITY,
    );
    const sources = allSources.slice(0, limit);
    elements.push(nodeElement(selected, true, "sketch"));
    for (const source of sources) {
      elements.push(nodeElement(source, false, "sketch"));
      elements.push(evidenceEdge(selected.key, source.key));
    }
    return { elements, rootId: selected.key, shown: sources.length, total: allSources.length };
  }

  const allChildren = rankNodes(
    nodes.filter((node) => node.parentKey === selected.key && node.status !== "immutable-source"),
    Number.POSITIVE_INFINITY,
  );
  const children = allChildren.slice(0, limit);
  elements.push(nodeElement(selected, true, "sketch"));
  for (const [index, child] of children.entries()) {
    elements.push(nodeElement(child, false, "sketch", NARRATIVE_COLORS[index % NARRATIVE_COLORS.length]));
    elements.push(branchEdge(selected.key, child.key));
  }
  return {
    elements,
    rootId: selected.key,
    shown: children.length,
    total: allChildren.length,
  };
}

function buildEvolutionView(
  nodes: VisualNode[],
  nodeByKey: Map<string, VisualNode>,
  selectedKey: string | null,
  limit: number,
): GraphView {
  const selectedNode = selectedKey ? nodeByKey.get(selectedKey) ?? null : null;
  const domain = selectedNode?.kind === "galaxy" ? selectedNode : null;
  const session = domain ? null : findSession(nodes, nodeByKey, selectedKey);
  const root = domain ?? session;
  if (!root) return { elements: [], rootId: null, shown: 0, total: 0 };
  const domainSessionKeys = domain
    ? new Set(nodes.filter((node) => node.kind === "session" && node.parentKey === domain.key).map((node) => node.key))
    : null;
  const chapters = nodes
    .filter((node) => (
      node.kind === "chapter"
      && (domainSessionKeys ? Boolean(node.parentKey && domainSessionKeys.has(node.parentKey)) : node.parentKey === session?.key)
    ))
    .sort(domain ? compareAcrossSessions : compareChronology);
  const selected = selectRepresentativeStages(chapters, limit);
  const elements: ElementDefinition[] = [nodeElement(root, true)];
  let previous = root.key;
  for (const chapter of selected) {
    elements.push(nodeElement(chapter, false));
    elements.push(timelineEdge(previous, chapter.key));
    previous = chapter.key;
  }
  return { elements, rootId: root.key, shown: 1 + selected.length, total: 1 + chapters.length };
}

function buildRelationView(
  nodes: VisualNode[],
  edges: VisualEdge[],
  selectedKey: string | null,
  language: "zh" | "en",
  limit: number,
): GraphView {
  const nonSources = new Map(nodes.filter((node) => node.status !== "immutable-source").map((node) => [node.key, node]));
  const rankedEdges = edges
    .filter((edge) => (
      edge.origin !== "hierarchy"
      && edge.type.toLowerCase() !== "continues"
      && nonSources.has(edge.source)
      && nonSources.has(edge.target)
    ))
    .filter((edge) => !selectedKey || edge.source === selectedKey || edge.target === selectedKey || sameSession(nonSources.get(edge.source), nonSources.get(selectedKey)))
    .sort((a, b) => b.weight - a.weight || a.id.localeCompare(b.id));
  const allRelatedNodeKeys = new Set(rankedEdges.flatMap((edge) => [edge.source, edge.target]));
  const candidateEdges = pickConnectedRelations(rankedEdges, limit, selectedKey);
  const nodeKeys = new Set(candidateEdges.flatMap((edge) => [edge.source, edge.target]));
  const elements: ElementDefinition[] = [];
  for (const key of nodeKeys) {
    const node = nonSources.get(key);
    if (node) elements.push(nodeElement(node, key === selectedKey, "sketch"));
  }
  for (const edge of candidateEdges) elements.push(relationEdge(edge));
  if (!candidateEdges.length) {
    return {
      elements,
      rootId: null,
      shown: 0,
      total: 0,
      emptyMessage: language === "zh" ? "当前范围还没有内容关系" : "No content relations in this scope yet",
      emptyHint: language === "zh"
        ? "关系图只展示支持、依赖、更新、冲突等语义联系；时间先后请查看演化流。"
        : "Relations shows semantic links such as support, dependency, updates, and conflicts. Use Evolution for chronology.",
    };
  }
  return {
    elements,
    rootId: selectedKey && nodeKeys.has(selectedKey) ? selectedKey : null,
    shown: nodeKeys.size,
    total: allRelatedNodeKeys.size,
  };
}

function buildEvidenceView(
  nodes: VisualNode[],
  nodeByKey: Map<string, VisualNode>,
  selectedKey: string | null,
  language: "zh" | "en",
): GraphView {
  const selected = selectedKey ? nodeByKey.get(selectedKey) ?? null : null;
  if (!selected || selected.kind !== "memory" || selected.status === "immutable-source") {
    return {
      elements: [],
      rootId: null,
      shown: 0,
      total: 0,
      emptyMessage: language === "zh" ? "选择一条记忆，查看它对应的原始证据" : "Select a memory to inspect its source evidence",
    };
  }
  const sourceIds = new Set(selected.sourceRecordIds);
  const sources = rankNodes(
    nodes.filter((node) => node.status === "immutable-source" && node.sourceRecordIds.some((id) => sourceIds.has(id))),
    MAX_SOURCES,
  );
  const elements: ElementDefinition[] = [nodeElement(selected, true)];
  for (const source of sources) {
    elements.push(nodeElement(source, false));
    elements.push(evidenceEdge(selected.key, source.key));
  }
  return { elements, rootId: selected.key, shown: 1 + sources.length, total: 1 + sourceIds.size };
}

function nodeElement(
  node: VisualNode,
  root: boolean,
  visual: "card" | "sketch" = "card",
  sketchColorOverride?: string,
): ElementDefinition {
  const source = node.status === "immutable-source";
  const sketch = visual === "sketch";
  const sketchColor = root ? "#4f83df" : sketchColorOverride ?? narrativeColor(node.key);
  const labelLimit = root && !sketch ? 34 : root ? 48 : source ? 34 : 38;
  const compact = compactLabel(node.label, labelLimit);
  const turnRange = !sketch && node.kind === "chapter" && node.turnStart != null
    ? `\nT${node.turnStart}${node.turnEnd != null && node.turnEnd !== node.turnStart ? `-${node.turnEnd}` : ""}`
    : "";
  return {
    data: {
      id: node.key,
      nodeKey: node.key,
      label: `${compact}${turnRange}`,
      kind: node.kind,
      source: source ? 1 : 0,
      root: root ? 1 : 0,
      visual,
      color: sketch ? sketchColor : node.color,
      width: sketch ? (root ? 88 : 48) : root ? 210 : source ? 174 : node.kind === "memory" ? 178 : 164,
      height: sketch ? (root ? 88 : 48) : root ? 76 : source ? 60 : node.kind === "chapter" ? 68 : 62,
      textWidth: root ? 176 : source ? 142 : 136,
      icon: sketch ? sketchPatternDataUrl(sketchColor) : undefined,
    },
    selected: root,
  };
}

function virtualRoot(label: string, visual: "card" | "sketch" = "sketch"): ElementDefinition {
  const sketch = visual === "sketch";
  return {
    data: {
      id: ROOT_ID,
      label: compactLabel(label, 44),
      kind: "galaxy",
      virtual: 1,
      root: 1,
      visual,
      color: "#3658d6",
      width: sketch ? 88 : 210,
      height: sketch ? 88 : 76,
      textWidth: 176,
      icon: sketch ? sketchPatternDataUrl("#4f83df") : undefined,
    },
  };
}

function branchEdge(source: string, target: string): ElementDefinition {
  const hash = deterministicHash(target);
  const bend = (hash % 2 === 0 ? 1 : -1) * (8 + hash % 9);
  return { data: { id: `branch:${source}:${target}`, source, target, kind: "branch", label: "", bend } };
}

function timelineEdge(source: string, target: string): ElementDefinition {
  return { data: { id: `timeline:${source}:${target}`, source, target, kind: "timeline", label: "" } };
}

function evidenceEdge(source: string, target: string): ElementDefinition {
  return { data: { id: `evidence:${source}:${target}`, source, target, kind: "evidence", label: "Source" } };
}

function relationEdge(edge: VisualEdge): ElementDefinition {
  return {
    data: {
      id: `relation:${edge.id}`,
      source: edge.source,
      target: edge.target,
      kind: "relation",
      type: edge.type,
      label: edge.type.toLowerCase() === "continues" ? "" : relationLabel(edge.type),
    },
  };
}

function pickConnectedRelations(edges: VisualEdge[], limit: number, seed: string | null) {
  if (!edges.length) return [];
  const first = seed
    ? edges.find((edge) => edge.source === seed || edge.target === seed) ?? edges[0]
    : edges[0];
  const selected: VisualEdge[] = [first];
  const selectedIds = new Set([first.id]);
  const connected = new Set([first.source, first.target]);
  while (selected.length < limit) {
    const next = edges.find((edge) => !selectedIds.has(edge.id) && (connected.has(edge.source) || connected.has(edge.target)));
    if (!next) break;
    selected.push(next);
    selectedIds.add(next.id);
    connected.add(next.source);
    connected.add(next.target);
  }
  return selected;
}

function runLayout(cy: Core, mode: VisualAtlasViewMode, rootId: string | null, animate: boolean) {
  if (mode === "global" || mode === "threads") {
    const branches = cy.nodes("[root != 1]").toArray().sort((a, b) => a.id().localeCompare(b.id()));
    const branchCount = branches.length;
    const compact = cy.width() < 560;
    const positions: Record<string, { x: number; y: number }> = {};
    const root = cy.nodes("[root = 1]").first();
    if (root.length) positions[root.id()] = compact ? { x: 0, y: -210 } : { x: 0, y: 0 };
    const radiusX = branchCount <= 4 ? 225 : 255;
    const radiusY = branchCount <= 4 ? 175 : 205;
    const startAngle = branchCount === 4 ? -Math.PI * 3 / 4 : -Math.PI / 2;
    branches.forEach((node, index) => {
      if (compact) {
        positions[node.id()] = {
          x: index % 2 === 0 ? -96 : 96,
          y: -20 + Math.floor(index / 2) * 185,
        };
      } else {
        const angle = startAngle + index * Math.PI * 2 / Math.max(branchCount, 1);
        positions[node.id()] = { x: Math.cos(angle) * radiusX, y: Math.sin(angle) * radiusY };
      }
    });
    const threadLayout = cy.layout({
      name: "preset",
      positions,
      fit: false,
      animate,
      animationDuration: animate ? 340 : 0,
      animationEasing: "cubic-bezier(.2,.8,.2,1)",
    } as LayoutOptions);
    const centerThreadView = () => {
      cy.zoom(compact ? 0.92 : 1.05);
      cy.center(cy.elements());
      applyThreadLabelDirections(cy, rootId, compact);
    };
    if (animate) cy.one("layoutstop", centerThreadView);
    threadLayout.run();
    if (!animate) centerThreadView();
    return;
  }
  const isVertical = mode === "evolution" || mode === "relations";
  const graphLayout = cy.layout({
    name: "elk",
    fit: true,
    padding: isVertical ? 74 : 96,
    animate,
    animationDuration: animate ? 340 : 0,
    animationEasing: "cubic-bezier(.2,.8,.2,1)",
    nodeDimensionsIncludeLabels: true,
    elk: {
      algorithm: "layered",
      "elk.direction": isVertical ? "DOWN" : "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.spacing.nodeNode": "38",
      "elk.layered.spacing.nodeNodeBetweenLayers": isVertical ? "24" : "74",
      "elk.layered.spacing.edgeNodeBetweenLayers": isVertical ? "18" : "34",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      "elk.randomSeed": "1",
    },
  } as LayoutOptions);
  const capAutomaticZoom = () => {
    const maximum = mode === "evidence" ? 1.05 : 1.15;
    if (cy.zoom() <= maximum) return;
    cy.zoom(maximum);
    cy.center(cy.elements());
  };
  if (animate) cy.one("layoutstop", capAutomaticZoom);
  graphLayout.run();
  if (!animate) capAutomaticZoom();
}

function applyThreadLabelDirections(cy: Core, rootId: string | null, compact: boolean) {
  const root = rootId ? cy.getElementById(rootId) : cy.nodes("[root = 1]").first();
  if (!root.length) return;
  const center = root.position();
  root.removeClass("label-top label-bottom label-left label-right");
  root.addClass(compact ? "label-top" : "label-bottom");
  cy.nodes("[visual = 'sketch'][root != 1]").forEach((node) => {
    node.removeClass("label-top label-bottom label-left label-right");
    if (compact) {
      node.addClass("label-bottom");
      return;
    }
    const position = node.position();
    const dx = position.x - center.x;
    const dy = position.y - center.y;
    if (Math.abs(dx) > Math.abs(dy) * 1.1) node.addClass(dx > 0 ? "label-right" : "label-left");
    else node.addClass(dy > 0 ? "label-bottom" : "label-top");
  });
}

function narrativeColor(key: string) {
  const hash = deterministicHash(key);
  return NARRATIVE_COLORS[hash % NARRATIVE_COLORS.length];
}

function deterministicHash(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  return hash;
}

function sketchPatternDataUrl(color: string) {
  const safeColor = /^#[0-9a-f]{3,8}$/i.test(color) ? color : "#86a9e8";
  const lines = Array.from({ length: 11 }, (_, index) => {
    const start = -48 + index * 12;
    return `<path d="M ${start} 96 L ${start + 96} 0"/>`;
  }).join("");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96"><g fill="none" stroke="${safeColor}" stroke-width="2.2" stroke-linecap="round" opacity=".78">${lines}</g></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function zoomBy(cy: Core | null, factor: number) {
  if (!cy) return;
  const current = cy.zoom();
  cy.zoom({ level: Math.max(cy.minZoom(), Math.min(cy.maxZoom(), current * factor)), renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
}

function rankNodes(nodes: VisualNode[], limit: number) {
  return [...nodes]
    .sort((a, b) => b.salience - a.salience || compareChronology(a, b) || a.key.localeCompare(b.key))
    .slice(0, limit);
}

function selectRepresentativeStages(nodes: VisualNode[], limit: number) {
  if (nodes.length <= limit) return nodes;
  const required = new Set([nodes[0].key, nodes[nodes.length - 1].key]);
  const ranked = [...nodes].sort((a, b) => b.salience - a.salience || compareChronology(a, b));
  for (const node of ranked) {
    if (required.size >= limit) break;
    required.add(node.key);
  }
  return nodes.filter((node) => required.has(node.key));
}

function findSession(nodes: VisualNode[], nodeByKey: Map<string, VisualNode>, selectedKey: string | null) {
  let current = selectedKey ? nodeByKey.get(selectedKey) ?? null : null;
  while (current && current.kind !== "session") current = current.parentKey ? nodeByKey.get(current.parentKey) ?? null : null;
  if (current?.kind === "session") return current;
  return [...nodes].filter((node) => node.kind === "session").sort((a, b) => compareChronology(b, a))[0] ?? null;
}

function compareChronology(a: VisualNode, b: VisualNode) {
  const aTurn = a.turnStart ?? Number.MAX_SAFE_INTEGER;
  const bTurn = b.turnStart ?? Number.MAX_SAFE_INTEGER;
  if (aTurn !== bTurn) return aTurn - bTurn;
  const aTime = Date.parse(a.occurredAt ?? "") || Number.MAX_SAFE_INTEGER;
  const bTime = Date.parse(b.occurredAt ?? "") || Number.MAX_SAFE_INTEGER;
  return aTime - bTime;
}

function compareAcrossSessions(a: VisualNode, b: VisualNode) {
  const aTime = Date.parse(a.occurredAt ?? "") || Number.MAX_SAFE_INTEGER;
  const bTime = Date.parse(b.occurredAt ?? "") || Number.MAX_SAFE_INTEGER;
  if (aTime !== bTime) return aTime - bTime;
  return compareChronology(a, b);
}

function sameSession(a?: VisualNode, b?: VisualNode) {
  if (!a || !b) return false;
  const aSession = a.kind === "session" ? a.key : a.kind === "chapter" ? a.parentKey : a.memory?.session_id ? `session:${a.memory.session_id}` : null;
  const bSession = b.kind === "session" ? b.key : b.kind === "chapter" ? b.parentKey : b.memory?.session_id ? `session:${b.memory.session_id}` : null;
  return Boolean(aSession && aSession === bSession);
}

function relationLabel(type: string) {
  return ({
    branches: "branches",
    converges: "converges",
    contradicts: "conflicts",
    updates: "updates",
    depends_on: "depends",
    supports: "supports",
  } as Record<string, string>)[type] ?? type.replace(/_/g, " ");
}

function visualKindLabel(node: VisualNode, language: "zh" | "en", mode: VisualAtlasViewMode) {
  if (node.status === "immutable-source") return language === "zh" ? "Source 原文" : "Verbatim Source";
  const technical = mode === "threads" || mode === "evidence";
  const labels: Record<VisualNode["kind"], [string, string]> = technical
    ? {
        galaxy: ["Theme group", "主题分组"],
        session: ["Session", "会话"],
        chapter: ["Episode", "阶段"],
        memory: ["Writer memory", "Writer 记忆"],
      }
    : {
        galaxy: ["Work area", "工作领域"],
        session: ["Conversation", "相关对话"],
        chapter: ["Milestone", "事项与里程碑"],
        memory: ["Key memory", "关键记忆"],
      };
  return language === "zh" ? labels[node.kind][1] : labels[node.kind][0];
}

function graphAriaLabel(mode: VisualAtlasViewMode, language: "zh" | "en") {
  const labels: Record<VisualAtlasViewMode, [string, string]> = {
    global: ["Zoomable global memory map", "可缩放的全局记忆图"],
    threads: ["Zoomable evidence trail", "可缩放的证据脉络图"],
    evolution: ["Zoomable memory evolution timeline", "可缩放的记忆演化流"],
    relations: ["Zoomable semantic relation map", "可缩放的语义关系图"],
    evidence: ["Zoomable Source evidence map", "可缩放的 Source 证据图"],
  };
  return language === "zh" ? labels[mode][1] : labels[mode][0];
}

function compactLabel(value: string, limit: number) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (text.length <= limit) return text;
  return `${text.slice(0, Math.max(1, limit - 1)).trimEnd()}…`;
}
