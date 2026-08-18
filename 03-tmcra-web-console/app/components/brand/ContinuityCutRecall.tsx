"use client";

import {
  type CSSProperties,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";

import { type LocalizedText, useLanguage } from "../../i18n";
import styles from "./ContinuityCutRecall.module.css";

type Stage = {
  id: string;
  label: LocalizedText;
  duration: number;
};

type Candidate = {
  id: string;
  layer: "SOURCE" | "FAST" | "SLOW";
  actor: "USER" | "AGENT";
  origin: LocalizedText;
  session: string;
  text: LocalizedText;
  match: LocalizedText;
  evidence: LocalizedText;
};

const STAGES: Stage[] = [
  { id: "prompt", label: { en: "Current prompt", zh: "当前问题" }, duration: 1050 },
  { id: "scope", label: { en: "Open scopes", zh: "展开范围" }, duration: 1100 },
  { id: "candidates", label: { en: "Find candidates", zh: "寻找候选" }, duration: 1450 },
  { id: "match", label: { en: "Mark the match", zh: "标记关联" }, duration: 1350 },
  { id: "splice", label: { en: "Splice evidence", zh: "接合证据" }, duration: 1450 },
  { id: "delivery", label: { en: "Prompt evidence", zh: "交付证据" }, duration: 1250 },
  { id: "next", label: { en: "Next action", zh: "确定下一步" }, duration: 1250 },
  { id: "answer", label: { en: "Answer completes", zh: "完成回答" }, duration: 950 },
  { id: "writeback", label: { en: "Split writeback", zh: "分轨写回" }, duration: 1400 },
];

const CANDIDATES: Candidate[] = [
  {
    id: "SRC-016",
    layer: "SOURCE",
    actor: "USER",
    origin: { en: "Project / memory-sdk", zh: "项目 / memory-sdk" },
    session: "Session 16",
    text: {
      en: "Keep the public API backward-compatible.",
      zh: "保持公开 API 向后兼容。",
    },
    match: {
      en: "The constraint still governs every integration fix.",
      zh: "这条约束仍然影响本轮的每一项集成修复。",
    },
    evidence: {
      en: "Preserve public API compatibility while fixing failures.",
      zh: "修复失败项时保持公开 API 向后兼容。",
    },
  },
  {
    id: "FST-017",
    layer: "FAST",
    actor: "AGENT",
    origin: { en: "Project / memory-sdk", zh: "项目 / memory-sdk" },
    session: "Session 17",
    text: {
      en: "Boundary audit complete; retry-contract tests remain.",
      zh: "边界审查已经完成，retry-contract 测试仍待处理。",
    },
    match: {
      en: "Unfinished progress directly answers where work should resume.",
      zh: "未完成进度直接说明这一次应该从哪里继续。",
    },
    evidence: {
      en: "Resume from the pending retry-contract tests.",
      zh: "从尚未完成的 retry-contract 测试继续。",
    },
  },
  {
    id: "SLW-G01",
    layer: "SLOW",
    actor: "USER",
    origin: { en: "User Global", zh: "用户全局" },
    session: "Cross-project",
    text: {
      en: "Use concise handoff notes for engineering work.",
      zh: "工程协作使用简洁、可执行的交接说明。",
    },
    match: {
      en: "An allowed owner preference defines the response form.",
      zh: "获准使用的用户偏好决定回答的表达方式。",
    },
    evidence: {
      en: "Return a concise engineering handoff after completion.",
      zh: "完成后给出简洁、可执行的工程交接。",
    },
  },
];

const clamp = (value: number) => Math.max(0, Math.min(STAGES.length - 1, value));

export default function ContinuityCutRecall() {
  const { t, localize } = useLanguage();
  const [stage, setStage] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [run, setRun] = useState(0);
  const [reducedMotion, setReducedMotion] = useState(false);
  const recallRef = useRef<HTMLElement>(null);
  const autoStarted = useRef(false);
  const timelineButtons = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => {
      setReducedMotion(query.matches);
      if (query.matches) {
        setStage(STAGES.length - 1);
        setPlaying(false);
      }
    };
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    if (!playing || reducedMotion) return;
    const timer = window.setTimeout(() => {
      if (stage === STAGES.length - 1) {
        setPlaying(false);
        return;
      }
      setStage((current) => current + 1);
    }, STAGES[stage].duration);
    return () => window.clearTimeout(timer);
  }, [playing, reducedMotion, run, stage]);

  useEffect(() => {
    const pauseWhenHidden = () => {
      if (document.hidden) setPlaying(false);
    };
    document.addEventListener("visibilitychange", pauseWhenHidden);
    return () => document.removeEventListener("visibilitychange", pauseWhenHidden);
  }, []);

  useEffect(() => {
    const node = recallRef.current;
    if (!node || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const observer = new IntersectionObserver(([entry]) => {
      if (!entry?.isIntersecting || autoStarted.current) return;
      autoStarted.current = true;
      setStage(0);
      setRun((current) => current + 1);
      setPlaying(true);
    }, { threshold: 0.08 });

    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const reached = (index: number) => stage >= index;
  const selectStage = (next: number, focus = false) => {
    const safeStage = clamp(next);
    setPlaying(false);
    setStage(safeStage);
    if (focus) window.requestAnimationFrame(() => timelineButtons.current[safeStage]?.focus());
  };

  const replay = () => {
    setPlaying(false);
    setStage(0);
    if (reducedMotion) return;
    window.requestAnimationFrame(() => {
      setRun((current) => current + 1);
      setPlaying(true);
    });
  };

  const togglePlayback = () => {
    if (reducedMotion) return;
    if (!playing && stage === STAGES.length - 1) {
      replay();
      return;
    }
    setPlaying((current) => !current);
  };

  const handleStageKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = index;
    if (event.key === "ArrowLeft") next = index - 1;
    else if (event.key === "ArrowRight") next = index + 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = STAGES.length - 1;
    else return;
    event.preventDefault();
    selectStage(next, true);
  };

  return (
    <section
      ref={recallRef}
      className={styles.recall}
      data-stage={STAGES[stage].id}
      data-playing={playing ? "true" : "false"}
      data-run={run}
      aria-label={t("Recorded TMCRA recall sequence", "TMCRA 召回结果回放")}
    >
      <header className={styles.header}>
        <div className={styles.edition}>
          <span>CONTINUITY CUT</span>
          <span>PROJECT / MEMORY-SDK</span>
          <span>RECORDED / 09:14:22</span>
        </div>
        <div className={styles.status} aria-live="polite">
          <span>{String(stage + 1).padStart(2, "0")} / 09</span>
          <strong>{localize(STAGES[stage].label)}</strong>
        </div>
        <div className={styles.controls}>
          <button type="button" onClick={togglePlayback} disabled={reducedMotion} aria-pressed={playing}>
            <span aria-hidden="true">{playing ? "Ⅱ" : "▶"}</span>
            {playing ? t("Pause", "暂停") : t("Play", "播放")}
          </button>
          <button type="button" onClick={replay}>
            <span aria-hidden="true">↺</span>
            {t("Replay", "重播")}
          </button>
        </div>
      </header>

      <div className={styles.stage}>
        <div className={styles.cutAxis} aria-hidden="true">
          <span>BEFORE</span>
          <i />
          <b>CUT / 018</b>
          <i />
          <span>AFTER</span>
        </div>

        <div className={`${styles.prompt} ${reached(0) ? styles.visible : ""}`}>
          <div className={styles.promptIndex}>
            <span>CURRENT PROMPT</span>
            <span>USER · Session 18</span>
          </div>
          <p>
            {t("Continue the ", "继续完成")}
            <mark>{t("integration tests", "集成测试")}</mark>
            {t(" and ", "，遇到问题就")}
            <mark>{t("fix anything that fails", "修复失败项")}</mark>
            {t(".", "。")}
          </p>
          <div className={styles.intentMarks} aria-hidden="true">
            <span>{t("unfinished work", "未完成进度")}</span>
            <span>{t("active constraints", "当前约束")}</span>
            <span>{t("answer form", "回答方式")}</span>
          </div>
        </div>

        <div className={`${styles.scopeField} ${reached(1) ? styles.visible : ""}`}>
          <div className={styles.scopeCaption}>
            <span>01 / ALLOWED SCOPE</span>
            <p>{t("The question opens only the permitted continuity fields.", "当前问题只展开获准使用的接续范围。")}</p>
          </div>
          <div className={styles.scopeTracks}>
            <div className={`${styles.scopeTrack} ${styles.globalTrack}`}>
              <span>USER GLOBAL</span>
              <i />
              <strong>{t("Owner preference", "用户稳定偏好")}</strong>
            </div>
            <div className={`${styles.scopeTrack} ${styles.projectTrack}`}>
              <span>PROJECT / memory-sdk</span>
              <i />
              <div className={styles.sessions}>
                <span>Session 16</span>
                <span>Session 17</span>
                <span className={styles.currentSession}>Session 18</span>
              </div>
            </div>
          </div>
          <small>{t("Session remains a grouping inside Project.", "Session 始终作为 Project 内部的分组。")}</small>
        </div>

        <div className={`${styles.candidateField} ${reached(2) ? styles.visible : ""}`}>
          <div className={styles.fieldLabel}>
            <span>02 / CANDIDATE TRACKS</span>
            <strong>{reached(3) ? t("3 MATCHES RETAINED", "保留 3 条匹配") : t("SEARCHING THREE MEMORY LAYERS", "检索三层记忆")}</strong>
          </div>
          <div className={styles.candidateTracks}>
            {CANDIDATES.map((candidate, index) => (
              <article
                className={`${styles.candidate} ${styles[candidate.layer.toLowerCase()]} ${reached(3) ? styles.matched : ""}`}
                key={candidate.id}
                style={{ "--track": index } as CSSProperties}
              >
                <div className={styles.candidateHead}>
                  <span>{candidate.layer}</span>
                  <b>{candidate.actor}</b>
                  <small>{candidate.id}</small>
                </div>
                <div className={styles.candidateBody}>
                  <div>
                    <span>{localize(candidate.origin)}</span>
                    <span>{candidate.session}</span>
                  </div>
                  <p>{localize(candidate.text)}</p>
                </div>
                <div className={styles.matchMark}>
                  <i aria-hidden="true">↳</i>
                  <div>
                    <span>MATCH / {String(index + 1).padStart(2, "0")}</span>
                    <p>{localize(candidate.match)}</p>
                  </div>
                </div>
              </article>
            ))}
          </div>
        </div>

        <div className={`${styles.spliceField} ${reached(4) ? styles.visible : ""}`}>
          <div className={styles.fieldLabel}>
            <span>03 / EVIDENCE STRIP</span>
            <strong>evidence_windows[3]</strong>
          </div>
          <div className={styles.evidenceStrip}>
            <span className={styles.stripLimit}>BOUND / START</span>
            {CANDIDATES.map((candidate, index) => (
              <div
                className={`${styles.fragment} ${candidate.actor === "USER" ? styles.userFragment : styles.agentFragment}`}
                key={candidate.id}
                style={{ "--fragment": index } as CSSProperties}
              >
                <span>{String(index + 1).padStart(2, "0")} · {candidate.actor} · {candidate.layer}</span>
                <p>{localize(candidate.evidence)}</p>
                <small>{candidate.id} · {candidate.session}</small>
              </div>
            ))}
            <span className={styles.stripLimit}>BOUND / END</span>
            <div className={styles.spliceMark} aria-hidden="true"><i /><b>SPLICE</b><i /></div>
          </div>
        </div>

        <div className={`${styles.handoff} ${reached(5) ? styles.visible : ""}`}>
          <div className={styles.promptEvidence}>
            <span>PROMPT EVIDENCE / 03 WINDOWS</span>
            <strong>{t("Evidence delivered with actor, source, scope and time intact.", "证据连同主体、来源、范围与时间一并交付。")}</strong>
            <div><i /> USER {t("requirements", "要求")}<i /> AGENT {t("progress", "进度")}</div>
          </div>
          <div className={`${styles.nextAction} ${reached(6) ? styles.visible : ""}`}>
            <span>AGENT NEXT / RESOLVED</span>
            <strong>{t("Run the pending integration suite.", "运行尚未完成的集成测试。")}</strong>
            <p>{t("Preserve API compatibility · return a concise handoff", "保持 API 兼容 · 完成后给出简洁交接")}</p>
          </div>
          <div className={`${styles.answerCut} ${reached(7) ? styles.visible : ""}`}>
            <i aria-hidden="true" />
            <span>{t("ANSWER COMPLETED", "回答完成")}</span>
            <b>{t("The continuation now has a new cut point.", "这次接续形成了新的切点。")}</b>
          </div>
        </div>

        <div className={`${styles.writeback} ${reached(8) ? styles.visible : ""}`}>
          <div className={styles.writebackTitle}>
            <span>04 / AFTER THE ANSWER</span>
            <strong>{t("Two speakers remain two records.", "两个对话主体，分别形成两条记录。")}</strong>
          </div>
          <div className={styles.roleTracks}>
            <div className={styles.userTrack}>
              <span>USER PROMPT</span><i /><b>USER RECORD</b>
            </div>
            <div className={styles.agentTrack}>
              <span>AGENT ANSWER</span><i /><b>AGENT RECORD</b>
            </div>
          </div>
          <small>{t("Recorded sequence · no live server progress is implied", "已记录的流程回放 · 不表示服务端实时进度")}</small>
        </div>
      </div>

      <nav className={styles.timeline} aria-label={t("Recall sequence stages", "召回流程阶段")}>
        <div className={styles.timelineProgress} aria-hidden="true">
          <i style={{ "--progress": `${(stage / (STAGES.length - 1)) * 100}%` } as CSSProperties} />
        </div>
        <div className={styles.timelineSteps} role="tablist">
          {STAGES.map((item, index) => (
            <button
              key={item.id}
              ref={(node) => { timelineButtons.current[index] = node; }}
              type="button"
              role="tab"
              aria-selected={stage === index}
              tabIndex={stage === index ? 0 : -1}
              className={`${reached(index) ? styles.reached : ""} ${stage === index ? styles.active : ""}`}
              onClick={() => selectStage(index)}
              onKeyDown={(event) => handleStageKey(event, index)}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>
              <b>{localize(item.label)}</b>
            </button>
          ))}
        </div>
      </nav>
    </section>
  );
}
