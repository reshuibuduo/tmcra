"use client";

import { MarketingFooter, MarketingHeader, TMCRA_GITHUB } from "../MarketingShell";
import { useLanguage } from "../i18n";
import "../knowledge-pages.css";

const taskScores = [
  ["Knowledge Update", "知识更新", "71 / 78", 91.03],
  ["Multi Session", "跨会话综合", "90 / 133", 67.67],
  ["Single Session · Assistant", "单会话 · 助手信息", "55 / 56", 98.21],
  ["Single Session · Preference", "单会话 · 偏好记忆", "27 / 30", 90],
  ["Single Session · User", "单会话 · 用户信息", "67 / 70", 95.71],
  ["Temporal Reasoning", "时间推理", "101 / 133", 75.94],
] as const;

export default function BenchmarksPage() {
  const { t } = useLanguage();

  return (
    <main className="knowledge-page benchmark-page">
      <MarketingHeader />
      <section className="knowledge-hero section-shell">
        <div>
          <p className="knowledge-kicker">BENCHMARK / RECORDED RESULT</p>
          <h1>{t("82.2% on the recorded LongMemEval run.", "LongMemEval 已记录成绩：82.2%。")}</h1>
          <p>{t("411 correct answers out of 500, with all task categories reported below.", "500 道题中答对 411 道，所有任务分项成绩如下。")}</p>
          <div className="knowledge-actions"><a className="button button-primary" href={TMCRA_GITHUB} target="_blank" rel="noreferrer">{t("Open the repository", "查看代码仓库")} ↗</a><a className="button button-secondary" href="/architecture">{t("Read the architecture", "了解架构")} →</a></div>
        </div>
        <aside className="benchmark-total">
          <span>LONGMEMEVAL</span>
          <strong>82.2%</strong>
          <p>411 / 500</p>
        </aside>
      </section>

      <section className="knowledge-section section-shell">
        <div className="knowledge-heading split"><div><p>01 / {t("TASK RESULTS", "分项成绩")}</p><h2>{t("Report the uneven parts, not only the total.", "总分之外，也完整展示各类任务表现。")}</h2></div><p>{t("The bars use the same zero baseline. Exact values remain visible so the chart does not replace the result table.", "所有条形图使用同一零点，并始终显示精确数值，不让图形代替结果本身。")}</p></div>
        <div className="benchmark-table" role="table" aria-label={t("LongMemEval task results", "LongMemEval 分项成绩")}>
          {taskScores.map(([en, zh, count, score]) => (
            <div role="row" key={en}>
              <div role="cell"><strong>{t(en, zh)}</strong><span>{count}</span></div>
              <div role="cell" className="benchmark-track"><i style={{ width: `${score}%` }} /></div>
              <div role="cell"><b>{score.toFixed(2)}%</b></div>
            </div>
          ))}
        </div>
      </section>

      <section className="knowledge-section section-shell">
        <div className="knowledge-heading split">
          <div><p>02 / LOCOMO</p><h2>{t("80.92% under the Mem0-style LLM Judge protocol.", "Mem0 风格 LLM Judge 协议成绩：80.92%。")}</h2></div>
          <p>{t("This is an auxiliary five-run mean over Categories 1–4. The full 1,986-question deterministic scores are reported separately.", "这是一项辅助指标，覆盖 Category 1–4，并取五次运行均值；全量 1,986 道题的确定性指标单独列出。")}</p>
        </div>
        <div className="locomo-result-board" role="group" aria-label={t("LoCoMo recorded results", "LoCoMo 已记录成绩")}>
          <article className="is-judge">
            <span>MEM0-STYLE LLM JUDGE</span>
            <strong>80.92<sup>%</sup></strong>
            <p>{t("Auxiliary measure · Categories 1–4 · N = 1,540 · five-run mean", "辅助指标 · Category 1–4 · N = 1,540 · 五次运行均值")}</p>
          </article>
          <article>
            <span>OFFICIAL TOKEN F1</span>
            <strong>55.20</strong>
            <p>{t("Deterministic scorer · all 1,986 questions", "确定性评分器 · 全部 1,986 道题")}</p>
          </article>
          <article>
            <span>EVIDENCE RECALL</span>
            <strong>82.00<sup>%</sup></strong>
            <p>{t("Evidence retrieval coverage · all 1,986 questions", "证据召回覆盖 · 全部 1,986 道题")}</p>
          </article>
        </div>
        <p className="benchmark-protocol-note">{t("The 80.92% Judge score does not include Category 5 and is not presented as full-set official accuracy.", "80.92% 的 Judge 成绩不包含 Category 5，也不作为全量官方准确率展示。")}</p>
      </section>

      <section className="knowledge-section section-shell">
        <div className="knowledge-heading"><p>03 / {t("RECORDED RECALL", "已记录召回")}</p><h2>{t("1.322 seconds for the recorded core recall trace.", "核心召回链路已记录用时：1.322 秒。")}</h2></div>
        <div className="benchmark-notes">
          <article><span>{t("WHAT IT IS", "这项数据表示什么")}</span><p>{t("A recorded result from the measured recall path used by the project.", "这是项目实测召回链路中的一项已记录结果。")}</p></article>
          <article><span>{t("WHAT IT IS NOT", "这项数据不表示什么")}</span><p>{t("It is not a live status value, uptime guarantee, or latency SLA for every deployment and request.", "它不是实时状态，也不代表所有部署与请求的可用性或延迟 SLA。")}</p></article>
          <article><span>{t("HOW IT IS SHOWN", "网站如何展示")}</span><p>{t("The website keeps the result static and labeled. It does not animate the number as if telemetry were streaming.", "网站以静态、明确标注的方式展示，不把它包装成持续更新的遥测数据。")}</p></article>
        </div>
      </section>

      <section className="knowledge-callout"><div className="section-shell"><div><p>04 / {t("REPRODUCTION", "复现")}</p><h2>{t("Use the repository instructions to run the benchmark.", "按照仓库说明运行并复现 Benchmark。")}</h2><p>{t("The public repository contains the architecture description, task breakdown, and reproduction entry points.", "公开仓库提供架构说明、任务分项和复现入口。")}</p></div><a className="button button-primary" href={TMCRA_GITHUB} target="_blank" rel="noreferrer">GitHub ↗</a></div></section>
      <MarketingFooter />
    </main>
  );
}
