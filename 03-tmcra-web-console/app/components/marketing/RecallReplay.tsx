"use client";

import { useEffect, useRef, useState } from "react";

import { type LocalizedText, useLanguage } from "../../i18n";
import EvidenceRecord from "./EvidenceRecord";

type RecallCue = {
  id: string;
  label: LocalizedText;
  duration: number;
};

type Candidate = {
  id: string;
  actor: "USER" | "AGENT";
  layer: "SOURCE" | "FAST" | "SLOW";
  scope: LocalizedText;
  session: LocalizedText;
  time: LocalizedText;
  text: LocalizedText;
  reason: LocalizedText;
  packed: LocalizedText;
};

const recallCues: RecallCue[] = [
  { id: "ready", label: { en: "Result ready", zh: "结果就绪" }, duration: 700 },
  { id: "query", label: { en: "Read the question", zh: "读取问题" }, duration: 1200 },
  { id: "scopes", label: { en: "Open allowed scopes", zh: "展开获准范围" }, duration: 1300 },
  { id: "candidates", label: { en: "Collect candidates", zh: "汇集候选证据" }, duration: 1600 },
  { id: "selection", label: { en: "Explain selection", zh: "解释入选原因" }, duration: 1500 },
  { id: "packing", label: { en: "Pack evidence windows", zh: "打包证据窗口" }, duration: 1500 },
  { id: "delivered", label: { en: "Deliver prompt evidence", zh: "交付提示证据" }, duration: 1250 },
  { id: "next", label: { en: "Resolve the next action", zh: "确定下一步" }, duration: 1100 },
  { id: "writeback", label: { en: "Keep writeback roles separate", zh: "按主体分别写回" }, duration: 1200 },
];

const candidates: Candidate[] = [
  {
    id: "ev-fast-17",
    actor: "AGENT",
    layer: "FAST",
    scope: { en: "Project / memory-sdk", zh: "项目 / memory-sdk" },
    session: { en: "Session 17", zh: "Session 17" },
    time: { en: "Yesterday · 16:42", zh: "昨天 · 16:42" },
    text: {
      en: "API boundary audit complete; retry-contract tests remain.",
      zh: "API 边界检查已完成；retry-contract 测试尚未完成。",
    },
    reason: {
      en: "Unfinished work directly matches “continue integration tests”.",
      zh: "未完成进度与“继续集成测试”直接相关。",
    },
    packed: {
      en: "Resume at the pending retry-contract tests.",
      zh: "从尚未完成的 retry-contract 测试继续。",
    },
  },
  {
    id: "ev-source-16",
    actor: "USER",
    layer: "SOURCE",
    scope: { en: "Project / memory-sdk", zh: "项目 / memory-sdk" },
    session: { en: "Session 16", zh: "Session 16" },
    time: { en: "Mon · 10:08", zh: "周一 · 10:08" },
    text: {
      en: "Keep the public API backward-compatible.",
      zh: "保持公开 API 向后兼容。",
    },
    reason: {
      en: "This project constraint still applies while fixing failures.",
      zh: "修复失败项时，这条项目约束仍然有效。",
    },
    packed: {
      en: "Preserve public API compatibility while fixing failures.",
      zh: "修复问题时保持公开 API 向后兼容。",
    },
  },
  {
    id: "ev-slow-global",
    actor: "USER",
    layer: "SLOW",
    scope: { en: "User Global", zh: "用户全局" },
    session: { en: "Cross-project", zh: "跨项目" },
    time: { en: "Updated Jun 08", zh: "更新于 6 月 8 日" },
    text: {
      en: "Use concise handoff notes for engineering work.",
      zh: "工程协作使用简洁、可执行的交接说明。",
    },
    reason: {
      en: "An allowed global preference defines the response format.",
      zh: "获准使用的全局偏好决定了输出形式。",
    },
    packed: {
      en: "Return a concise engineering handoff after completion.",
      zh: "完成后输出简洁、可执行的工程交接。",
    },
  },
];

export default function RecallReplay() {
  const { t, localize } = useLanguage();
  const [cue, setCue] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [runId, setRunId] = useState(0);
  const stageRef = useRef<HTMLDivElement>(null);
  const autoPlayedRef = useRef(false);

  useEffect(() => {
    const node = stageRef.current;
    if (!node) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion) {
      const frame = window.requestAnimationFrame(() => setCue(recallCues.length - 1));
      return () => window.cancelAnimationFrame(frame);
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry?.isIntersecting || autoPlayedRef.current) return;
        autoPlayedRef.current = true;
        setCue(1);
        setPlaying(true);
        setRunId((value) => value + 1);
      },
      { threshold: 0.05, rootMargin: "0px 0px -10%" },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!playing) return;
    const current = recallCues[cue];
    if (!current) return;

    const timer = window.setTimeout(() => {
      if (cue >= recallCues.length - 1) {
        setPlaying(false);
        return;
      }
      setCue((value) => value + 1);
    }, current.duration);

    return () => window.clearTimeout(timer);
  }, [cue, playing, runId]);

  useEffect(() => {
    const pauseWhenHidden = () => {
      if (document.hidden) setPlaying(false);
    };
    document.addEventListener("visibilitychange", pauseWhenHidden);
    return () => document.removeEventListener("visibilitychange", pauseWhenHidden);
  }, []);

  const reached = (index: number) => cue >= index;
  const replay = () => {
    setPlaying(false);
    setCue(0);
    window.requestAnimationFrame(() => {
      setRunId((value) => value + 1);
      setCue(1);
      setPlaying(true);
    });
  };

  const selectCue = (index: number) => {
    setPlaying(false);
    setCue(index);
  };

  const handleTimelineKey = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight" && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    if (event.key === "Home") selectCue(0);
    else if (event.key === "End") selectCue(recallCues.length - 1);
    else selectCue(Math.max(0, Math.min(recallCues.length - 1, cue + (event.key === "ArrowRight" ? 1 : -1))));
  };

  return (
    <div
      className="recall-replay recall-workbench"
      ref={stageRef}
      data-phase={recallCues[cue]?.id ?? "ready"}
      data-playing={playing ? "true" : "false"}
      data-run={runId}
    >
      <div className="recall-replay-toolbar">
        <div>
          <span>{t("RECORDED RESULT / PROJECT memory-sdk", "已记录结果 / 项目 memory-sdk")}</span>
          <strong aria-live="polite">{localize(recallCues[cue]?.label ?? recallCues[0].label)}</strong>
        </div>
        <div className="recall-playback-controls">
          <button type="button" onClick={() => setPlaying((value) => !value)} aria-pressed={playing}>
            <span className="recall-control-symbol" aria-hidden="true">{playing ? "Ⅱ" : "▶"}</span>
            {playing ? t("Pause", "暂停") : t("Play", "播放")}
          </button>
          <button type="button" onClick={replay}>
            <span className="recall-control-symbol" aria-hidden="true">↻</span>
            {t("Replay", "重新播放")}
          </button>
        </div>
      </div>

      <div className={`recall-question-band${reached(1) ? " is-reached" : ""}`}>
        <div className="recall-question-meta">
          <span>CURRENT PROMPT</span>
          <span>USER · Session 18 · 09:14</span>
        </div>
        <blockquote>
          {t("Continue the ", "继续完成")}
          <mark>{t("integration tests", "集成测试")}</mark>
          {t(" and ", "，遇到问题就")}
          <mark>{t("fix anything that fails", "修复失败项")}</mark>
          {t(".", "。")}
        </blockquote>
        <div className="recall-query-intent" aria-hidden={!reached(1)}>
          <span>{t("unfinished work", "未完成工作")}</span>
          <span>{t("active constraints", "当前约束")}</span>
          <span>{t("response preference", "输出偏好")}</span>
        </div>
      </div>

      <div className="recall-flow-board">
        <aside className={`recall-scope-column${reached(2) ? " is-reached" : ""}`}>
          <header><span>01</span><strong>{t("ALLOWED SCOPES", "获准召回范围")}</strong></header>
          <article className="recall-scope-node is-global">
            <div><span>USER GLOBAL</span><b>{t("Allowed", "已获准")}</b></div>
            <p>{t("Stable owner-level preference", "账户级稳定偏好")}</p>
          </article>
          <article className="recall-scope-node is-project">
            <div><span>PROJECT</span><b>memory-sdk</b></div>
            <p>{t("Current project evidence", "当前项目证据")}</p>
            <div className="recall-session-stack">
              <span>Session 16</span><span>Session 17</span><span className="is-current">Session 18</span>
            </div>
          </article>
          <small>{t("Session stays inside Project.", "Session 保持在 Project 内部。")}</small>
        </aside>

        <section className={`recall-route-column${reached(3) ? " is-reached" : ""}`}>
          <header>
            <div><span>02</span><strong>{t("PARALLEL CANDIDATE ROUTES", "并行候选路径")}</strong></div>
            <b>{reached(4) ? t("3 selected / 3 shown", "已选 3 / 展示 3") : t("Collecting returned records", "汇集已返回记录")}</b>
          </header>
          <div className="recall-candidate-lanes">
            {candidates.map((candidate, index) => (
              <article
                className={`recall-candidate-lane is-${candidate.layer.toLowerCase()}${reached(4) ? " is-selected" : ""}`}
                key={candidate.id}
                style={{ "--candidate-order": index } as React.CSSProperties}
              >
                <div className="recall-lane-label">
                  <span>{candidate.layer}</span>
                  <i aria-hidden="true" />
                  <small>{candidate.id}</small>
                </div>
                <EvidenceRecord
                  actor={candidate.actor}
                  layer={candidate.layer}
                  scope={localize(candidate.scope)}
                  session={localize(candidate.session)}
                  time={localize(candidate.time)}
                  active={reached(3)}
                  compact
                >
                  {localize(candidate.text)}
                </EvidenceRecord>
                <div className="recall-match-reason">
                  <span>{t("WHY SELECTED", "入选原因")}</span>
                  <p>{localize(candidate.reason)}</p>
                </div>
              </article>
            ))}
          </div>
        </section>

        <aside className={`recall-packing-column${reached(5) ? " is-reached" : ""}`}>
          <header><span>03</span><strong>evidence_windows[]</strong></header>
          <div className="recall-window-stack">
            {candidates.map((candidate, index) => (
              <article
                className={`recall-window-fragment is-${candidate.actor.toLowerCase()}`}
                key={candidate.id}
                style={{ "--fragment-order": index } as React.CSSProperties}
              >
                <header><span>{String(index + 1).padStart(2, "0")}</span><b>{candidate.actor} · {candidate.layer}</b></header>
                <p>{localize(candidate.packed)}</p>
                <small>{localize(candidate.scope)} · {localize(candidate.session)}</small>
              </article>
            ))}
          </div>
          <div className={`recall-prompt-delivery${reached(6) ? " is-reached" : ""}`}>
            <span>PROMPT EVIDENCE</span>
            <strong>{t("Delivered with actor provenance", "携带主体来源并交付")}</strong>
            <div><i /> USER {t("requirements", "要求")}<i /> AGENT {t("progress", "进度")}</div>
          </div>
          <div className={`recall-next-action${reached(7) ? " is-reached" : ""}`}>
            <span>AGENT NEXT</span>
            <strong>{t("Run the pending integration suite", "运行待完成的集成测试")}</strong>
            <p>{t("Preserve API compatibility · return a concise handoff", "保持 API 兼容 · 完成后给出简洁交接")}</p>
          </div>
        </aside>
      </div>

      <div className={`recall-writeback-note${reached(8) ? " is-reached" : ""}`}>
        <div><span>{t("AFTER THE ANSWER", "回答完成后")}</span><strong>{t("Two roles, two records", "两个主体，分别写入")}</strong></div>
        <div className="recall-writeback-lanes">
          <span className="is-user"><i /> USER PROMPT <b>→</b> USER RECORD</span>
          <span className="is-agent"><i /> AGENT ANSWER <b>→</b> AGENT RECORD</span>
        </div>
        <small>{t("Lifecycle note · this replay does not claim live server progress", "生命周期说明 · 此回放不表示服务端实时进度")}</small>
      </div>

      <div className="recall-storyboard" role="tablist" aria-label={t("Recall replay storyboard", "召回回放分镜")} onKeyDown={handleTimelineKey}>
        <div className="recall-storyboard-track" aria-hidden="true"><i style={{ width: `${(cue / (recallCues.length - 1)) * 100}%` }} /></div>
        {recallCues.map((item, index) => (
          <button
            type="button"
            role="tab"
            aria-selected={cue === index}
            tabIndex={cue === index ? 0 : -1}
            className={cue >= index ? "is-reached" : ""}
            key={item.id}
            onClick={() => selectCue(index)}
          >
            <span>{String(index + 1).padStart(2, "0")}</span>
            <b>{localize(item.label)}</b>
          </button>
        ))}
      </div>
    </div>
  );
}
