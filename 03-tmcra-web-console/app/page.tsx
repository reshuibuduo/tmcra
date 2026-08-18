"use client";

import { type CSSProperties, useEffect } from "react";

import ContinuityCutRecall from "./components/brand/ContinuityCutRecall";
import ContinuityCutScene from "./components/brand/ContinuityCutScene";
import IntegrationExplorer from "./components/marketing/IntegrationExplorer";
import { type LocalizedText, useLanguage } from "./i18n";
import { MarketingFooter, MarketingHeader, TMCRA_GITHUB } from "./MarketingShell";
import styles from "./continuity-cut.module.css";

type BenchmarkTask = {
  name: LocalizedText;
  count: string;
  score: string;
};

const benchmarkTasks: BenchmarkTask[] = [
  { name: { en: "Knowledge Update", zh: "知识更新" }, count: "71 / 78", score: "91.03%" },
  { name: { en: "Multi Session", zh: "跨会话综合" }, count: "90 / 133", score: "67.67%" },
  { name: { en: "Single Session Assistant", zh: "单会话 · 助手信息" }, count: "55 / 56", score: "98.21%" },
  { name: { en: "Single Session Preference", zh: "单会话 · 偏好记忆" }, count: "27 / 30", score: "90.00%" },
  { name: { en: "Single Session User", zh: "单会话 · 用户信息" }, count: "67 / 70", score: "95.71%" },
  { name: { en: "Temporal Reasoning", zh: "时间推理" }, count: "101 / 133", score: "75.94%" },
];

const memoryConditions = [
  {
    index: "01",
    key: "WHO",
    title: { en: "Keep the speaker", zh: "保留主体" },
    copy: {
      en: "USER requirements and AGENT progress can meet inside one answer while remaining separate records.",
      zh: "用户要求与 Agent 进度可以共同支撑一次回答，同时保持为两种独立记录。",
    },
  },
  {
    index: "02",
    key: "WHEN",
    title: { en: "Keep the sequence", zh: "保留时间" },
    copy: {
      en: "Corrections and revisions add a new state without erasing what was previously said.",
      zh: "修正与版本变化会形成新的状态，旧陈述及其发生时间仍被保存。",
    },
  },
  {
    index: "03",
    key: "WHERE",
    title: { en: "Keep the boundary", zh: "保留边界" },
    copy: {
      en: "Global and Project memory can continue across sessions without crossing into unrelated work.",
      zh: "全局与项目记忆可以跨会话接续，同时避免进入无关项目。",
    },
  },
  {
    index: "04",
    key: "SOURCE",
    title: { en: "Keep the proof", zh: "保留依据" },
    copy: {
      en: "Every derived state remains linked to the exact Source that permits it to influence the next step.",
      zh: "每条派生状态都保留精确 Source，使它能够以可核验的方式影响下一步。",
    },
  },
];

const memoryLayers = [
  {
    key: "source",
    index: "01",
    label: "SOURCE LEDGER",
    title: { en: "What happened remains intact.", zh: "发生过的内容保持完整。" },
    body: {
      en: "Exact text, actor, time, session position, and content identity form the immutable record.",
      zh: "原文、主体、时间、会话位置与内容标识共同构成不可变记录。",
    },
    sample: { en: "Keep the public API backward-compatible.", zh: "保持公开 API 向后兼容。" },
    meta: "USER · Session 16 · 10:08",
  },
  {
    key: "fast",
    index: "02",
    label: "FAST VIEW",
    title: { en: "The current state can change.", zh: "当前状态可以变化。" },
    body: {
      en: "Fresh requirements, decisions, and work progress become compact searchable state with Source provenance.",
      zh: "最新要求、决定与工作进度形成紧凑的可检索状态，并继续绑定 Source。",
    },
    sample: { en: "Integration audit complete; retry-contract remains.", zh: "接入边界检查完成；retry-contract 仍待处理。" },
    meta: "AGENT · Session 17 · 16:42",
  },
  {
    key: "slow",
    index: "03",
    label: "SLOW VIEW",
    title: { en: "Long-lived meaning keeps its revisions.", zh: "长期理解保留每次修订。" },
    body: {
      en: "Durable capsules organize support, counterevidence, challenges, resolutions, and prior versions.",
      zh: "长期胶囊组织支持证据、反证、挑战、解决状态与历史版本。",
    },
    sample: { en: "Compatibility is a durable project constraint.", zh: "兼容性是该项目的长期约束。" },
    meta: "PROJECT · revision 04 · SOURCE BOUND",
  },
  {
    key: "evidence",
    index: "04",
    label: "READER EVIDENCE",
    title: { en: "Evidence is prepared before action.", zh: "行动之前，证据先被组织。" },
    body: {
      en: "The current task, bounded Source support, verified operations, and provenance become a Reader-ready view.",
      zh: "当前任务、有界 Source 支持、已验证操作与来源共同形成 Reader 可用的证据视图。",
    },
    sample: { en: "Resume the pending retry-contract tests.", zh: "从待完成的 retry-contract 测试继续。" },
    meta: "PROMPT EVIDENCE · TOP-K BOUNDED",
  },
];

export default function Home() {
  const { t, localize } = useLanguage();

  useEffect(() => {
    document.title = t("TMCRA — Continue work across conversations", "TMCRA — 跨对话继续工作");
    const description = document.querySelector<HTMLMetaElement>('meta[name="description"]');
    description?.setAttribute(
      "content",
      t(
        "Traceable memory infrastructure that carries verified work state across conversations and Agent tools.",
        "让经过来源、主体、时间与范围验证的工作状态跨对话接续。",
      ),
    );
  }, [t]);

  useEffect(() => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const nodes = Array.from(document.querySelectorAll<HTMLElement>("[data-cut-reveal]"));
    if (reduceMotion) {
      nodes.forEach((node) => node.setAttribute("data-cut-visible", "true"));
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        (entry.target as HTMLElement).setAttribute("data-cut-visible", "true");
        observer.unobserve(entry.target);
      }),
      { threshold: 0.14, rootMargin: "0px 0px -7%" },
    );
    nodes.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, []);

  return (
    <main className={styles.page}>
      <MarketingHeader tone="continuity" />

      <section className={styles.hero} id="top">
        <div className={styles.heroRegistration} aria-hidden="true">
          <span>01</span><i /><span>CONTINUITY CUT</span>
        </div>
        <div className={styles.heroCopy} data-cut-reveal>
          <p className={styles.kicker}>{t("THE MEMORY LAYER BETWEEN PAST WORK AND THE NEXT ACTION", "连接过去工作与下一步行动的记忆层")}</p>
          <h1>
            <span>{t("Continue work", "跨对话")}</span>
            <span>{t("across conversations.", "继续工作。")}</span>
          </h1>
          <p className={styles.heroLead}>
            {t(
              "TMCRA lets earlier work enter the present with its actor, time, scope, and Source intact — so an Agent can continue from evidence instead of starting over.",
              "TMCRA 让过去的工作带着主体、时间、范围与 Source 进入当前问题，让 Agent 沿证据继续，无需重新从头解释。",
            )}
          </p>
          <div className={styles.heroActions}>
            <a className={styles.primaryAction} href="/access">{t("Request pilot access", "申请试用")} <span aria-hidden="true">↗</span></a>
            <a className={styles.secondaryAction} href="/download">{t("Desktop release status", "桌面端发布状态")} <span aria-hidden="true">→</span></a>
            <a className={styles.textAction} href="/developers">{t("Explore integrations", "查看接入方式")} <span aria-hidden="true">→</span></a>
          </div>
          <div className={styles.heroStatement}>
            <span>TMCRA / PRINCIPLE 01</span>
            <p>{t("Memory may shape the next action only when its origin remains visible.", "记忆要影响下一步，必须保留它的来处。")}</p>
          </div>
        </div>

        <div className={styles.heroScene} data-cut-reveal>
          <ContinuityCutScene />
        </div>

        <dl className={styles.verificationRail} aria-label={t("Recorded product evidence", "已记录的产品证据")}>
          <div><dt>LONGMEMEVAL</dt><dd>82.2%</dd><span>411 / 500</span></div>
          <div><dt>{t("CORE RECALL TRACE", "核心召回记录")}</dt><dd>1.322 s</dd><span>{t("recorded run", "已记录运行")}</span></div>
          <div><dt>{t("ACTOR BOUNDARY", "主体边界")}</dt><dd>USER / AGENT</dd><span>{t("separate records", "分别保存")}</span></div>
          <div><dt>{t("RECALL SCOPES", "召回范围")}</dt><dd>GLOBAL + PROJECT</dd><span>{t("parallel", "并行")}</span></div>
        </dl>
      </section>

      <section className={styles.manifesto} id="continuity">
        <div className={styles.sectionMeta} data-cut-reveal>
          <span>02</span><i /><p>{t("THE CONDITIONS OF CONTINUITY", "接续成立的条件")}</p>
        </div>
        <header className={styles.manifestoHeading} data-cut-reveal>
          <p>{t("A memory can enter the next action after four questions are answered.", "一段记忆进入下一步之前，需要回答四个问题。")}</p>
          <h2>{t("Who said it. When it happened. Where it belongs. What proves it.", "谁说的。何时发生。属于哪里。依据什么。")}</h2>
        </header>
        <div className={styles.conditionRail} data-cut-reveal>
          {memoryConditions.map((condition) => (
            <article key={condition.key} data-key={condition.key}>
              <div><span>{condition.index}</span><strong>{condition.key}</strong></div>
              <h3>{localize(condition.title)}</h3>
              <p>{localize(condition.copy)}</p>
            </article>
          ))}
        </div>
        <p className={styles.manifestoFoot} data-cut-reveal>
          {t(
            "Current instruction leads. Historical user requirements follow. Agent progress contributes without becoming the user's voice.",
            "当前指令拥有最高优先级；历史用户要求随后参与；Agent 进度可以提供依据，但不会被改写成用户陈述。",
          )}
        </p>
      </section>

      <section className={styles.memoryMatter} id="architecture">
        <div className={styles.sectionMeta} data-cut-reveal>
          <span>03</span><i /><p>{t("SOURCE / STATE / EVIDENCE", "来源 / 状态 / 证据")}</p>
        </div>
        <header className={styles.splitHeading} data-cut-reveal>
          <h2>{t("The past stays intact. Its usable meaning can evolve.", "过去保持完整，可用的理解持续演化。")}</h2>
          <p>{t("TMCRA separates the immutable record from revisable memory views, then binds selected evidence to the current task.", "TMCRA 将不可变记录与可修订记忆视图区分开，再把选中的证据绑定到当前任务。")}</p>
        </header>
        <div className={styles.layerStage} data-cut-reveal>
          <div className={styles.layerAxis} aria-hidden="true"><span>PAST</span><i /><span>NEXT ACTION</span></div>
          {memoryLayers.map((layer) => (
            <article className={`${styles.layerStrip} ${styles[`layer_${layer.key}`]}`} key={layer.key}>
              <div className={styles.layerIndex}><span>{layer.index}</span><strong>{layer.label}</strong></div>
              <div className={styles.layerCopy}><h3>{localize(layer.title)}</h3><p>{localize(layer.body)}</p></div>
              <blockquote><p>{localize(layer.sample)}</p><footer>{layer.meta}</footer></blockquote>
              <span className={styles.matchMark} aria-hidden="true" />
            </article>
          ))}
        </div>
      </section>

      <section className={styles.boundaries}>
        <div className={styles.sectionMeta} data-cut-reveal>
          <span>04</span><i /><p>{t("BOUNDARIES KEEP CONTINUITY TRUSTWORTHY", "边界让接续保持可信")}</p>
        </div>
        <header className={styles.splitHeading} data-cut-reveal>
          <h2>{t("Shared context, distinct identities.", "共享上下文，保留不同身份。")}</h2>
          <p>{t("A project can span sessions and specialized Agents while every contribution keeps its own actor and Source.", "一个项目可以跨越多个 Session 与专业 Agent，每次贡献仍保留自己的主体与 Source。")}</p>
        </header>
        <div className={styles.boundaryStage} data-cut-reveal>
          <div className={styles.currentPromptMark}>
            <span>{t("CURRENT PROMPT", "当前问题")}</span>
            <strong>{t("Continue the integration tests.", "继续完成集成测试。")}</strong>
            <small>{t("opens two allowed recall scopes", "打开两个获准召回范围")}</small>
          </div>
          <div className={`${styles.scopeTrack} ${styles.globalTrack}`}>
            <header><span>USER GLOBAL</span><em>{t("ALLOWED", "获准")}</em></header>
            <div><b>USER</b><p>{t("Use concise engineering handoffs.", "工程工作使用简洁交接。")}</p><small>SLOW · stable preference</small></div>
          </div>
          <div className={`${styles.scopeTrack} ${styles.projectTrack}`}>
            <header><span>PROJECT / memory-sdk</span><em>{t("ALLOWED", "获准")}</em></header>
            <div className={styles.sessionMarks}><span>Session 16</span><span>Session 17</span><span className={styles.currentSession}>Session 18</span></div>
            <div className={styles.actorTracks}>
              <div><b>USER</b><p>{t("Keep the public API backward-compatible.", "保持公开 API 向后兼容。")}</p><small>SOURCE · Session 16</small></div>
              <div><b>AGENT</b><p>{t("Retry-contract tests remain.", "retry-contract 测试仍待完成。")}</p><small>FAST · Session 17</small></div>
            </div>
          </div>
          <div className={styles.scopeResult}>
            <span>PROMPT EVIDENCE</span>
            <strong>{t("Resume at retry-contract. Preserve compatibility. Return a concise handoff.", "从 retry-contract 继续；保留兼容性；完成后提供简洁交接。")}</strong>
          </div>
        </div>
      </section>

      <section className={styles.recallSection} id="playground">
        <div className={styles.sectionMeta} data-cut-reveal>
          <span>05</span><i /><p>{t("A COMPLETED RECALL, REPLAYED", "一次已完成召回的回放")}</p>
        </div>
        <header className={styles.recallHeading} data-cut-reveal>
          <h2>{t("Watch the exact cut that lets work continue.", "看清工作如何跨过断点继续。")}</h2>
          <p>{t("This is a replay of a completed result: scope opening, candidate return, matching, evidence splice, prompt delivery, and role-safe writeback.", "这里回放一份已经完成的结果：范围打开、候选返回、匹配、证据接片、上下文交付与分主体写回。")}</p>
        </header>
        <div data-cut-reveal><ContinuityCutRecall /></div>
      </section>

      <section className={styles.proof} id="benchmarks">
        <div className={styles.sectionMeta} data-cut-reveal>
          <span>06</span><i /><p>{t("VERIFICATION SLATE", "验证记录")}</p>
        </div>
        <header className={styles.splitHeading} data-cut-reveal>
          <h2>{t("Recorded results, with every protocol kept visible.", "已记录成绩，每种评测口径都清楚标明。")}</h2>
          <p>{t("LongMemEval reports all 500 questions task by task. LoCoMo keeps the Mem0-style Judge subset separate from full-set Token F1 and Evidence Recall.", "LongMemEval 按任务展示全部 500 道题；LoCoMo 将 Mem0 风格 LLM Judge 子集，与全量 Token F1、Evidence Recall 分开呈现。")}</p>
        </header>
        <div className={styles.proofLayout} data-cut-reveal>
          <div className={styles.scorePlate}>
            <span>LONGMEMEVAL / RECORDED</span>
            <strong>82.2<sup>%</sup></strong>
            <div><b>411 / 500</b><i /></div>
            <small>TMCRA · RESULT LOCK 2026</small>
          </div>
          <div className={styles.taskMeasures} role="table" aria-label={t("LongMemEval score by task", "LongMemEval 分项成绩")}>
            {benchmarkTasks.map((task, index) => (
              <div role="row" key={task.name.en} style={{ "--measure": task.score, "--delay": `${index * 70}ms` } as CSSProperties}>
                <span role="cell">{String(index + 1).padStart(2, "0")}</span>
                <strong role="cell">{localize(task.name)}</strong>
                <i aria-hidden="true"><b /></i>
                <span role="cell">{task.count}</span>
                <em role="cell">{task.score}</em>
              </div>
            ))}
          </div>
          <div className={styles.locomoPlate}>
            <div className={styles.locomoPrimary}>
              <span>LOCOMO / MEM0-STYLE LLM JUDGE</span>
              <strong>80.92<sup>%</sup></strong>
              <small>{t("auxiliary measure · five-run mean · Categories 1–4 · N = 1,540", "辅助指标 · 五次运行均值 · Category 1–4 · N = 1,540")}</small>
            </div>
            <div className={styles.locomoMetric}>
              <span>OFFICIAL TOKEN F1</span>
              <strong>55.20</strong>
              <small>{t("all 1,986 questions", "全部 1,986 道题")}</small>
            </div>
            <div className={styles.locomoMetric}>
              <span>EVIDENCE RECALL</span>
              <strong>82.00<sup>%</sup></strong>
              <small>{t("all 1,986 questions", "全部 1,986 道题")}</small>
            </div>
          </div>
          <div className={styles.latencyPlate}>
            <span>{t("CORE RECALL TRACE", "核心召回记录")}</span>
            <strong>1.322 <small>{t("seconds", "秒")}</small></strong>
            <p>{t("Recorded retrieval, reranking, and evidence packing trace. Planner and answer generation are excluded.", "已记录的检索、重排与证据打包链路；不包含 Planner 与回答生成。")}</p>
          </div>
        </div>
        <a className={styles.benchmarkLink} href="/benchmarks">{t("Read protocols and complete benchmark notes", "查看评测口径与完整说明")} <span aria-hidden="true">→</span></a>
      </section>

      <section className={styles.platforms} id="product">
        <div className={styles.sectionMeta} data-cut-reveal>
          <span>07</span><i /><p>{t("ONE CONTRACT, MULTIPLE HOSTS", "同一份合同，多种宿主")}</p>
        </div>
        <header className={styles.splitHeading} data-cut-reveal>
          <h2>{t("Connect the cut to the tools your Agents already use.", "把接续能力接入现有 Agent 工具。")}</h2>
          <p>{t("Each host reaches the same recall and writeback contract through its own lifecycle events.", "每种宿主通过自己的生命周期事件进入同一套召回与写回合同。")}</p>
        </header>
        <div className={styles.platformExplorer} data-cut-reveal><IntegrationExplorer /></div>

        <div className={styles.researchRail} data-cut-reveal>
          <div><span>RESEARCH / OPEN SOURCE</span><h3>{t("Inspect the architecture. Reproduce the benchmark.", "检查架构，复现 Benchmark。")}</h3></div>
          <p>{t("The repository contains the research architecture, evaluation protocol, and benchmark adapters used by the project.", "代码仓库包含项目使用的研究架构、评测协议与 Benchmark Adapter。")}</p>
          <a href={TMCRA_GITHUB} target="_blank" rel="noreferrer">GitHub <span aria-hidden="true">↗</span></a>
        </div>
      </section>

      <section className={styles.finalCut}>
        <div className={styles.finalPast} aria-hidden="true"><span>PAST WORK</span><i /></div>
        <div className={styles.finalCopy} data-cut-reveal>
          <span>NEXT ACTION / READY</span>
          <h2>{t("Continue from where the work actually stopped.", "从工作真正停下的位置继续。")}</h2>
          <p>{t("Choose the integration path that matches your current Agent workflow.", "选择与你当前 Agent 工作流匹配的接入方式。")}</p>
          <div className={styles.heroActions}>
            <a className={styles.primaryAction} href="/access">{t("Request pilot access", "申请试用")} <span aria-hidden="true">↗</span></a>
            <a className={styles.secondaryAction} href="/download">{t("Desktop release status", "桌面端发布状态")} <span aria-hidden="true">→</span></a>
            <a className={styles.textAction} href="/developers">{t("Read integration guides", "查看接入文档")} <span aria-hidden="true">→</span></a>
          </div>
        </div>
      </section>

      <MarketingFooter />
    </main>
  );
}
