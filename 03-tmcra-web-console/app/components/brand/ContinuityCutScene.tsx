"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useLanguage } from "../../i18n";
import styles from "./ContinuityCutScene.module.css";

const DURATION = 8_800;
const PHASES = [0, 0.13, 0.28, 0.47, 0.66, 0.84, 1];

type Point = { x: number; y: number };

function clamp(value: number) {
  return Math.max(0, Math.min(1, value));
}

function phaseFor(progress: number) {
  let active = 0;
  PHASES.forEach((threshold, index) => {
    if (progress >= threshold) active = index;
  });
  return active;
}

export default function ContinuityCutScene() {
  const { language, t } = useLanguage();
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef<number | null>(null);
  const startRef = useRef<number | null>(null);
  const progressRef = useRef(0);
  const visibleRef = useRef(false);
  const reducedMotionRef = useRef(false);
  const completedRef = useRef(false);
  const startedRef = useRef(false);
  const [phase, setPhase] = useState(0);
  const [run, setRun] = useState(0);

  const nodePoint = useCallback((name: string, edge: "left" | "right" | "center" = "center"): Point | null => {
    const stage = stageRef.current;
    const node = stage?.querySelector<HTMLElement>(`[data-cut-node="${name}"]`);
    if (!stage || !node) return null;
    const stageBox = stage.getBoundingClientRect();
    const box = node.getBoundingClientRect();
    const x = edge === "left" ? box.left : edge === "right" ? box.right : box.left + box.width / 2;
    return { x: x - stageBox.left, y: box.top + box.height / 2 - stageBox.top };
  }, []);

  const draw = useCallback((progress: number) => {
    const stage = stageRef.current;
    const canvas = canvasRef.current;
    if (!stage || !canvas) return;

    const bounds = stage.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(bounds.width));
    const height = Math.max(1, Math.round(bounds.height));
    const pixelWidth = Math.round(width * ratio);
    const pixelHeight = Math.round(height * ratio);
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }

    const context = canvas.getContext("2d");
    if (!context) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    if (width < 720) return;

    const query = nodePoint("query", "right");
    const cut = nodePoint("cut");
    const global = nodePoint("global", "left");
    const project = nodePoint("project", "left");
    const source = nodePoint("source", "right");
    const fast = nodePoint("fast", "right");
    const slow = nodePoint("slow", "right");
    const prompt = nodePoint("prompt", "left");
    const promptOut = nodePoint("prompt", "right");
    const action = nodePoint("action", "left");
    if (!query || !cut || !global || !project || !source || !fast || !slow || !prompt || !promptOut || !action) return;

    const line = (from: Point, to: Point, color: string, reveal: number, widthValue = 1) => {
      const amount = clamp(reveal);
      const x = from.x + (to.x - from.x) * amount;
      const y = from.y + (to.y - from.y) * amount;
      context.beginPath();
      context.moveTo(from.x, from.y);
      context.lineTo(x, y);
      context.strokeStyle = color;
      context.lineWidth = widthValue;
      context.stroke();
    };

    const curve = (from: Point, to: Point, color: string, reveal: number) => {
      const amount = clamp(reveal);
      context.save();
      context.beginPath();
      context.moveTo(from.x, from.y);
      const bend = Math.max(24, (to.x - from.x) * 0.44);
      context.bezierCurveTo(from.x + bend, from.y, to.x - bend, to.y, to.x, to.y);
      context.strokeStyle = color;
      context.lineWidth = 1.4;
      context.setLineDash([4, 7]);
      context.lineDashOffset = 18 * (1 - amount);
      context.globalAlpha = amount;
      context.stroke();
      context.restore();
    };

    const queryReveal = clamp((progress - 0.02) / 0.13);
    line(query, cut, "rgba(213, 151, 41, .9)", queryReveal, 1.6);

    const scopeReveal = clamp((progress - 0.15) / 0.18);
    line(cut, global, "rgba(37, 80, 168, .52)", scopeReveal);
    line(cut, project, "rgba(37, 80, 168, .52)", scopeReveal);

    const evidenceReveal = clamp((progress - 0.48) / 0.25);
    curve(source, prompt, "rgba(37, 80, 168, .9)", evidenceReveal);
    curve(fast, prompt, "rgba(192, 70, 42, .9)", evidenceReveal);
    curve(slow, prompt, "rgba(94, 119, 91, .9)", evidenceReveal);

    const actionReveal = clamp((progress - 0.78) / 0.2);
    line(promptOut, action, "rgba(213, 151, 41, .95)", actionReveal, 1.8);

    if (actionReveal > 0.02) {
      const pulse = 2.5 + actionReveal * 4;
      context.beginPath();
      context.arc(action.x, action.y, pulse, 0, Math.PI * 2);
      context.fillStyle = `rgba(213, 151, 41, ${0.12 + actionReveal * 0.36})`;
      context.fill();
    }
  }, [nodePoint]);

  const stop = useCallback(() => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    startRef.current = null;
  }, []);

  const begin = useCallback((restart = false) => {
    stop();
    if (reducedMotionRef.current) {
      progressRef.current = 1;
      completedRef.current = true;
      setPhase(PHASES.length - 1);
      draw(1);
      return;
    }
    if (restart) {
      progressRef.current = 0;
      completedRef.current = false;
      setPhase(0);
      setRun((value) => value + 1);
    }
    if (!visibleRef.current || document.hidden || completedRef.current) return;

    const tick = (time: number) => {
      if (!visibleRef.current || document.hidden) {
        stop();
        return;
      }
      if (startRef.current === null) startRef.current = time - progressRef.current * DURATION;
      const progress = clamp((time - startRef.current) / DURATION);
      progressRef.current = progress;
      draw(progress);
      const nextPhase = phaseFor(progress);
      setPhase((current) => current === nextPhase ? current : nextPhase);
      if (progress >= 1) {
        completedRef.current = true;
        frameRef.current = null;
        return;
      }
      frameRef.current = requestAnimationFrame(tick);
    };
    frameRef.current = requestAnimationFrame(tick);
  }, [draw, stop]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;

    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const applyMotionPreference = () => {
      reducedMotionRef.current = motion.matches;
      if (motion.matches) begin(false);
    };
    applyMotionPreference();
    motion.addEventListener("change", applyMotionPreference);

    const observer = new IntersectionObserver(([entry]) => {
      visibleRef.current = Boolean(entry?.isIntersecting);
      if (!visibleRef.current) {
        stop();
        return;
      }
      if (!startedRef.current) {
        startedRef.current = true;
        begin(true);
      } else if (!completedRef.current) {
        begin(false);
      } else {
        draw(1);
      }
    }, { threshold: 0.18 });
    observer.observe(stage);

    const onVisibility = () => {
      if (document.hidden) stop();
      else if (visibleRef.current && !completedRef.current) begin(false);
    };
    document.addEventListener("visibilitychange", onVisibility);

    const resize = new ResizeObserver(() => draw(progressRef.current));
    resize.observe(stage);

    return () => {
      stop();
      observer.disconnect();
      resize.disconnect();
      motion.removeEventListener("change", applyMotionPreference);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [begin, draw, stop]);

  useEffect(() => {
    const frame = requestAnimationFrame(() => draw(progressRef.current));
    return () => cancelAnimationFrame(frame);
  }, [draw, language]);

  return (
    <section className={styles.scene} aria-labelledby="continuity-cut-title">
      <header className={styles.sceneHeader}>
        <div>
          <p className={styles.kicker}>THE CONTINUITY CUT / 01</p>
          <h2 id="continuity-cut-title">{t("Work stops at a conversation boundary. Evidence carries it forward.", "工作停在对话边界，证据把它接续下去。")}</h2>
        </div>
        <button className={styles.replay} type="button" onClick={() => begin(true)}>
          <span aria-hidden="true">↻</span>
          {t("Replay the cut", "重播接续")}
        </button>
      </header>

      <div className={styles.stage} ref={stageRef} data-phase={phase} data-run={run}>
        <canvas className={styles.canvas} ref={canvasRef} aria-hidden="true" />

        <article className={styles.question} data-cut-node="query">
          <div className={styles.fragmentMeta}><span>CURRENT QUESTION</span><time>09:14:03</time></div>
          <p>{t("Continue the integration tests. Fix any failures you find.", "继续完成集成测试，遇到失败项就修复。")}</p>
          <small>USER · Session 18</small>
        </article>

        <div className={styles.cut} data-cut-node="cut" aria-hidden="true">
          <span>CUT 018</span>
          <i />
          <b>{t("conversation boundary", "对话边界")}</b>
        </div>

        <div className={`${styles.scope} ${styles.globalScope}`} data-cut-node="global">
          <span className={styles.scopeIndex}>ALLOW / 01</span>
          <strong>USER GLOBAL</strong>
          <small>{t("Owner-level facts only", "仅限用户级稳定信息")}</small>
        </div>

        <div className={`${styles.scope} ${styles.projectScope}`} data-cut-node="project">
          <span className={styles.scopeIndex}>ALLOW / 02</span>
          <strong>PROJECT / memory-sdk</strong>
          <small>Session 16—18</small>
        </div>

        <div className={`${styles.actorRail} ${styles.userRail}`}><span>USER</span><i /><small>{t("requirements", "要求")}</small></div>
        <div className={`${styles.actorRail} ${styles.agentRail}`}><span>AGENT</span><i /><small>{t("progress", "进度")}</small></div>

        <article className={`${styles.evidence} ${styles.source}`} data-cut-node="source">
          <header><span>SOURCE / USER</span><b>Session 16</b></header>
          <blockquote>{t("“Keep the public API backward-compatible.”", "“保持公开 API 向后兼容。”")}</blockquote>
          <footer><span>{t("verbatim constraint", "原始约束")}</span><time>MON · 10:08</time></footer>
        </article>

        <article className={`${styles.evidence} ${styles.fast}`} data-cut-node="fast">
          <header><span>FAST / AGENT</span><b>Session 17</b></header>
          <p>{t("Boundary audit complete.", "边界检查完成。")}</p>
          <strong>{t("retry-contract tests remain", "retry-contract 测试尚未完成")}</strong>
          <footer><span>{t("working state", "工作状态")}</span><time>{t("YESTERDAY", "昨天")} · 16:42</time></footer>
        </article>

        <article className={`${styles.evidence} ${styles.slow}`} data-cut-node="slow">
          <header><span>SLOW / USER</span><b>GLOBAL</b></header>
          <p>{t("Engineering handoffs stay concise and executable.", "工程交接保持简洁、可执行。")}</p>
          <footer><span>{t("settled preference", "稳定偏好")}</span><time>08 JUN</time></footer>
        </article>

        <article className={styles.prompt} data-cut-node="prompt">
          <header><span>PROMPT EVIDENCE</span><b>03 / 03</b></header>
          <ol>
            <li><span>01</span>{t("Resume pending retry-contract tests", "从待完成的 retry-contract 测试继续")}</li>
            <li><span>02</span>{t("Preserve public API compatibility", "保持公开 API 向后兼容")}</li>
            <li><span>03</span>{t("Return a concise handoff", "完成后给出简洁交接")}</li>
          </ol>
          <small>{t("actor · scope · time · source retained", "主体 · 范围 · 时间 · 来源均保留")}</small>
        </article>

        <article className={styles.action} data-cut-node="action">
          <span>NEXT ACTION</span>
          <strong>{t("Run the retry-contract suite", "运行 retry-contract 测试集")}</strong>
          <p>{t("Continue from the last verified state.", "从最后一个已验证状态继续。")}</p>
        </article>

        <div className={styles.phaseLabel} aria-live="polite">
          <span>{String(phase + 1).padStart(2, "0")} / 07</span>
          <b>{[
            t("Question arrives", "问题抵达"),
            t("Boundary found", "识别边界"),
            t("Allowed scopes open", "打开获准范围"),
            t("Evidence returns", "证据返回"),
            t("Actors align", "主体对齐"),
            t("Prompt evidence forms", "形成提示证据"),
            t("Work continues", "工作接续"),
          ][phase]}</b>
        </div>
      </div>
    </section>
  );
}
