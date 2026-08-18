"use client";

import { useState } from "react";

import { type LocalizedText, useLanguage } from "../../i18n";

type SurfaceStatus = "STABLE" | "PREVIEW" | "PILOT";
type TriggerMode = "explicit" | "wrapper" | "native";

type SurfacePhase = {
  key: "enter" | "recall" | "answer" | "write" | "delegate";
  label: LocalizedText;
  event: string;
  note: LocalizedText;
};

type ProductSurface = {
  id: string;
  index: string;
  name: string;
  kind: LocalizedText;
  status: SurfaceStatus;
  triggerMode: TriggerMode;
  summary: LocalizedText;
  boundary: LocalizedText;
  phases: SurfacePhase[];
  href: string;
  cta: LocalizedText;
};

const surfaces: ProductSurface[] = [
  {
    id: "rest",
    index: "01",
    name: "REST / OpenAPI",
    kind: { en: "VERSIONED CONTRACT", zh: "版本化合同" },
    status: "STABLE",
    triggerMode: "explicit",
    summary: {
      en: "A production HTTP contract for services that control their own model lifecycle.",
      zh: "面向自行管理模型生命周期的服务端生产合同。",
    },
    boundary: {
      en: "The caller explicitly starts recall and ingest; TMCRA does not observe the model call by itself.",
      zh: "调用方主动发起召回与写入；TMCRA 不会自行观察模型调用。",
    },
    phases: [
      { key: "recall", label: { en: "Recall", zh: "召回" }, event: "POST …/recall", note: { en: "Send the current query and allowed scopes.", zh: "提交当前问题与获准范围。" } },
      { key: "answer", label: { en: "Answer", zh: "回答" }, event: "caller model", note: { en: "The caller injects returned prompt evidence.", zh: "调用方注入返回的 Prompt Evidence。" } },
      { key: "write", label: { en: "Writeback", zh: "写回" }, event: "POST …/ingest", note: { en: "Persist USER and AGENT as separate messages.", zh: "将 USER 与 AGENT 作为两条消息写入。" } },
    ],
    href: "/docs#reference",
    cta: { en: "Open API reference", zh: "打开 API 参考" },
  },
  {
    id: "codex",
    index: "02",
    name: "Codex",
    kind: { en: "NATIVE HOOKS", zh: "原生 Hooks" },
    status: "PREVIEW",
    triggerMode: "native",
    summary: {
      en: "Project-aware recall before each prompt and paired capture after a completed answer.",
      zh: "每次提问前按项目召回，回答完成后成对采集本轮对话。",
    },
    boundary: {
      en: "Session remains inside the current Project. Stop writes USER and AGENT records separately.",
      zh: "Session 归属当前 Project；Stop 分别写入 USER 与 AGENT。",
    },
    phases: [
      { key: "enter", label: { en: "Session", zh: "会话" }, event: "SessionStart", note: { en: "Resolve project and stable session identity.", zh: "确定项目与稳定 Session 标识。" } },
      { key: "recall", label: { en: "Recall", zh: "召回" }, event: "UserPromptSubmit", note: { en: "Inject evidence for the exact current prompt.", zh: "按当前问题注入相关证据。" } },
      { key: "write", label: { en: "Writeback", zh: "写回" }, event: "Stop", note: { en: "Capture the completed USER / AGENT pair.", zh: "采集完成后的 USER / AGENT 对话对。" } },
    ],
    href: "/developers/codex",
    cta: { en: "Read the Codex guide", zh: "查看 Codex 指南" },
  },
  {
    id: "python",
    index: "03",
    name: "Python SDK",
    kind: { en: "LIFECYCLE WRAPPER", zh: "生命周期封装" },
    status: "PREVIEW",
    triggerMode: "wrapper",
    summary: {
      en: "Sync and async wrappers place recall and writeback around your model callback.",
      zh: "同步与异步封装将召回和写回放在模型回调前后。",
    },
    boundary: {
      en: "commit_turn runs only after a non-empty assistant answer exists.",
      zh: "只有拿到非空助手回复后，commit_turn 才会执行。",
    },
    phases: [
      { key: "recall", label: { en: "Prepare", zh: "准备" }, event: "prepare_turn", note: { en: "Recall and return fenced model messages.", zh: "召回并返回带边界的模型消息。" } },
      { key: "answer", label: { en: "Answer", zh: "回答" }, event: "model callback", note: { en: "Your callable produces the assistant answer.", zh: "由你的回调生成助手回复。" } },
      { key: "write", label: { en: "Commit", zh: "提交" }, event: "commit_turn", note: { en: "Write the completed turn after success.", zh: "成功后写入完整对话轮次。" } },
    ],
    href: "/developers/automatic-memory#python",
    cta: { en: "Open Python integration", zh: "查看 Python 接入" },
  },
  {
    id: "typescript",
    index: "04",
    name: "JavaScript / TypeScript",
    kind: { en: "TYPED LIFECYCLE", zh: "类型化生命周期" },
    status: "PREVIEW",
    triggerMode: "wrapper",
    summary: {
      en: "Typed helpers connect prompt evidence, model messages, and paired writeback.",
      zh: "类型化工具串联 Prompt Evidence、模型消息与成对写回。",
    },
    boundary: {
      en: "Agents may share projectScope while agentMetadata preserves who performed the work.",
      zh: "多个 Agent 可共享 projectScope，并通过 agentMetadata 保留执行主体。",
    },
    phases: [
      { key: "recall", label: { en: "Prepare", zh: "准备" }, event: "prepareTurn", note: { en: "Recall configured scopes in parallel.", zh: "并行召回已配置的范围。" } },
      { key: "answer", label: { en: "Messages", zh: "消息" }, event: "modelMessages", note: { en: "Add bounded evidence to the model input.", zh: "将有边界的证据加入模型输入。" } },
      { key: "write", label: { en: "Commit", zh: "提交" }, event: "commitTurn", note: { en: "Persist both actors with Agent metadata.", zh: "携带 Agent 元数据写入两个主体。" } },
    ],
    href: "/developers/automatic-memory#javascript-typescript",
    cta: { en: "Open TypeScript integration", zh: "查看 TypeScript 接入" },
  },
  {
    id: "mcp",
    index: "05",
    name: "MCP Server",
    kind: { en: "EXPLICIT TOOLS", zh: "显式工具" },
    status: "PREVIEW",
    triggerMode: "explicit",
    summary: {
      en: "Local stdio tools expose recall, ingest, job status, and waiting to an MCP host.",
      zh: "通过本地 stdio 向 MCP 宿主提供召回、写入、任务状态与等待工具。",
    },
    boundary: {
      en: "Connecting stdio alone cannot observe host turns; the host must call recall and ingest.",
      zh: "仅连接 stdio 无法观察宿主对话；宿主需要主动调用召回与写入。",
    },
    phases: [
      { key: "recall", label: { en: "Tool call", zh: "工具调用" }, event: "tmcra_recall", note: { en: "The host requests memory for the current prompt.", zh: "宿主按当前问题请求记忆。" } },
      { key: "answer", label: { en: "Host", zh: "宿主" }, event: "host answer", note: { en: "The host decides how to use returned evidence.", zh: "宿主决定如何使用返回证据。" } },
      { key: "write", label: { en: "Tool call", zh: "工具调用" }, event: "tmcra_ingest", note: { en: "The host explicitly writes the completed turn.", zh: "宿主显式写入已完成轮次。" } },
    ],
    href: "/developers/automatic-memory#mcp",
    cta: { en: "Read the MCP guide", zh: "查看 MCP 指南" },
  },
  {
    id: "openclaw",
    index: "06",
    name: "OpenClaw",
    kind: { en: "MULTI-AGENT HOOKS", zh: "多 Agent Hooks" },
    status: "PILOT",
    triggerMode: "native",
    summary: {
      en: "Native hooks let specialized Agents share project memory with preserved attribution.",
      zh: "原生 Hook 让不同分工的 Agent 共享项目记忆，并保留各自来源。",
    },
    boundary: {
      en: "Agents share a Project scope; every record retains Agent and Session attribution.",
      zh: "多个 Agent 共享 Project 范围；每条记录保留 Agent 与 Session 来源。",
    },
    phases: [
      { key: "recall", label: { en: "Before prompt", zh: "提问前" }, event: "before_prompt_build", note: { en: "Recall global and shared-project evidence.", zh: "召回全局与共享项目证据。" } },
      { key: "answer", label: { en: "Agent work", zh: "Agent 工作" }, event: "shared project", note: { en: "Specialized Agents continue from common state.", zh: "不同分工的 Agent 沿共享状态继续。" } },
      { key: "write", label: { en: "Agent end", zh: "Agent 结束" }, event: "agent_end", note: { en: "Queue USER and current AGENT separately.", zh: "分别排队写入 USER 与当前 AGENT。" } },
    ],
    href: "/developers/automatic-memory#openclaw",
    cta: { en: "Read the OpenClaw guide", zh: "查看 OpenClaw 指南" },
  },
  {
    id: "hermes",
    index: "07",
    name: "Hermes Agent",
    kind: { en: "MEMORY PROVIDER", zh: "记忆 Provider" },
    status: "PILOT",
    triggerMode: "native",
    summary: {
      en: "A provider recalls the current query, tracks delegation, and queues completed turns.",
      zh: "Provider 召回当前问题、记录委派过程，并排队写入完整轮次。",
    },
    boundary: {
      en: "Delegated work remains assistant-side and keeps parent / child Agent attribution.",
      zh: "委派工作归属助手侧，并保留父 Agent 与子 Agent 来源。",
    },
    phases: [
      { key: "recall", label: { en: "Prefetch", zh: "预取" }, event: "prefetch", note: { en: "Recall the exact current query.", zh: "召回当前的精确问题。" } },
      { key: "delegate", label: { en: "Delegation", zh: "委派" }, event: "on_delegation", note: { en: "Record parent and child Agent work as assistant-side.", zh: "将父、子 Agent 工作记录为助手侧内容。" } },
      { key: "write", label: { en: "Sync turn", zh: "同步轮次" }, event: "sync_turn", note: { en: "Queue the completed primary USER / AGENT pair.", zh: "排队写入主对话的 USER / AGENT 对。" } },
    ],
    href: "/developers/automatic-memory#hermes",
    cta: { en: "Read the Hermes guide", zh: "查看 Hermes 指南" },
  },
];

export default function IntegrationExplorer() {
  const { t, localize } = useLanguage();
  const [selectedId, setSelectedId] = useState("rest");
  const [previewId, setPreviewId] = useState<string | null>(null);
  const visibleId = previewId ?? selectedId;
  const activeSurface = surfaces.find((surface) => surface.id === visibleId) ?? surfaces[1];

  const selectAt = (index: number) => {
    const normalized = (index + surfaces.length) % surfaces.length;
    const next = surfaces[normalized];
    setPreviewId(null);
    setSelectedId(next.id);
    window.requestAnimationFrame(() => document.getElementById(`surface-tab-${next.id}`)?.focus());
  };

  return (
    <div className="surface-browser" data-surface={activeSurface.id} data-trigger={activeSurface.triggerMode}>
      <div
        className="surface-tabs"
        role="tablist"
        aria-label={t("Integration paths", "接入方式")}
        onPointerLeave={() => setPreviewId(null)}
      >
        {surfaces.map((surface, index) => {
          const visible = visibleId === surface.id;
          return (
            <button
              type="button"
              id={`surface-tab-${surface.id}`}
              role="tab"
              aria-selected={selectedId === surface.id}
              aria-controls="surface-panel"
              tabIndex={selectedId === surface.id ? 0 : -1}
              data-visible={visible ? "true" : "false"}
              data-status={surface.status.toLowerCase()}
              key={surface.id}
              onPointerEnter={(event) => {
                if (event.pointerType === "mouse") setPreviewId(surface.id);
              }}
              onFocus={() => {
                setPreviewId(null);
                setSelectedId(surface.id);
              }}
              onClick={() => {
                setPreviewId(null);
                setSelectedId(surface.id);
              }}
              onKeyDown={(event) => {
                if (event.key === "ArrowRight" || event.key === "ArrowDown") {
                  event.preventDefault();
                  selectAt(index + 1);
                } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
                  event.preventDefault();
                  selectAt(index - 1);
                } else if (event.key === "Home") {
                  event.preventDefault();
                  selectAt(0);
                } else if (event.key === "End") {
                  event.preventDefault();
                  selectAt(surfaces.length - 1);
                }
              }}
            >
              <span className="surface-tab-index">{surface.index}</span>
              <span className={`surface-status is-${surface.status.toLowerCase()}`}>{surface.status}</span>
              <strong className="surface-tab-title">{surface.name}</strong>
              <small>{localize(surface.kind)}</small>
              <span className="surface-tab-trace" aria-hidden="true">
                {surface.phases.map((phase) => <i key={phase.event} />)}
              </span>
              <code>{surface.phases.map((phase) => phase.event).join(" · ")}</code>
            </button>
          );
        })}
      </div>

      <article
        className="surface-observatory"
        id="surface-panel"
        role="tabpanel"
        aria-labelledby={`surface-tab-${activeSurface.id}`}
        key={activeSurface.id}
      >
        <header>
          <div>
            <span>{activeSurface.index} / {localize(activeSurface.kind)}</span>
            <h3>{activeSurface.name}</h3>
          </div>
          <div><span className={`surface-status is-${activeSurface.status.toLowerCase()}`}>{activeSurface.status}</span><b>{activeSurface.triggerMode.toUpperCase()}</b></div>
        </header>
        <p className="surface-observatory-summary">{localize(activeSurface.summary)}</p>
        <ol className="surface-lifecycle">
          {activeSurface.phases.map((phase, index) => (
            <li key={phase.event} style={{ "--phase-order": index } as React.CSSProperties}>
              <span>{String(index + 1).padStart(2, "0")} / {localize(phase.label)}</span>
              <strong>{phase.event}</strong>
              <p>{localize(phase.note)}</p>
            </li>
          ))}
        </ol>
        <div className="surface-boundary">
          <span>BOUNDARY</span>
          <p>{localize(activeSurface.boundary)}</p>
        </div>
        <a href={activeSurface.href}>{localize(activeSurface.cta)} <span aria-hidden="true">→</span></a>
      </article>
    </div>
  );
}
