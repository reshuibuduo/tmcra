"use client";

import { type FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { MarketingFooter, MarketingHeader } from "../MarketingShell";
import { useLanguage } from "../i18n";

const PLATFORM_OPTIONS = [
  { value: "REST / OpenAPI", status: { en: "Stable", zh: "稳定" } },
  { value: "Python SDK", status: { en: "Preview", zh: "预览" } },
  { value: "TypeScript SDK", status: { en: "Preview", zh: "预览" } },
  { value: "MCP Server", status: { en: "Preview", zh: "预览" } },
  { value: "Codex", status: { en: "Preview", zh: "预览" } },
  { value: "OpenClaw", status: { en: "Pilot", zh: "试用" } },
  { value: "Hermes Agent", status: { en: "Pilot", zh: "试用" } },
] as const;

type RequestForm = {
  contactName: string;
  email: string;
  companyName: string;
  industry: string;
  companySize: string;
  useCase: string;
  timeline: string;
  platforms: string[];
  consent: boolean;
  website: string;
};

const initialForm: RequestForm = { contactName: "", email: "", companyName: "", industry: "", companySize: "", useCase: "", timeline: "", platforms: [], consent: false, website: "" };

export default function AccessPage() {
  const { t } = useLanguage();
  const [form, setForm] = useState<RequestForm>(initialForm);
  const [state, setState] = useState<"idle" | "submitting" | "saved" | "error">("idle");
  const [requestId, setRequestId] = useState("");

  useEffect(() => {
    document.title = t("Request TMCRA Pilot Access", "申请 TMCRA 试用");
  }, [t]);

  const update = <K extends keyof RequestForm>(key: K, value: RequestForm[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
    if (state === "error") setState("idle");
  };

  const togglePlatform = (platform: string) => {
    update("platforms", form.platforms.includes(platform) ? form.platforms.filter((item) => item !== platform) : [...form.platforms, platform]);
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setState("submitting");
    try {
      const response = await fetch("/api/access", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form) });
      const result = await response.json().catch(() => ({})) as { requestId?: string };
      if (!response.ok) throw new Error("request_failed");
      setRequestId(result.requestId ?? "");
      setState("saved");
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch {
      setState("error");
    }
  };

  return (
    <main className="marketing-page">
      <MarketingHeader />
      <section className="application-hero section-shell">
        <div><p className="eyebrow"><span /> {t("Pilot application / 01", "试用申请 / 01")}</p><h1>{t("Apply for a TMCRA pilot.", "申请 TMCRA 试用。")}</h1><p>{t("Tell us what you are building, where memory fits and when you plan to test. This information goes directly to the TMCRA Internal review queue.", "请介绍你正在构建的产品、记忆能力的使用位置以及计划测试时间。提交后，资料会直接进入 TMCRA 的内部审核队列。")}</p></div>
        <dl><div><dt>{t("Destination", "提交去向")}</dt><dd>TMCRA Internal</dd></div><div><dt>{t("Review", "处理方式")}</dt><dd>{t("Manual qualification", "人工评估与跟进")}</dd></div><div><dt>{t("Data", "数据用途")}</dt><dd>{t("Pilot communication only", "仅用于试用沟通")}</dd></div></dl>
      </section>

      <section className="application-section section-shell section-block">
        {state === "saved" ? (
          <div className="application-success" role="status">
            <span aria-hidden="true">✓</span>
            <p className="section-index">{t("APPLICATION RECEIVED", "申请已提交")}</p>
            <h2>{t("Your request is now in the review queue.", "你的申请已进入审核队列。")}</h2>
            <p>{t("The TMCRA team will review the use case and contact you through your work email. Submitting again with the same email updates the existing application.", "TMCRA 团队会评估你的使用场景，并通过工作邮箱与你联系。使用同一邮箱再次提交时，会更新已有申请。")}</p>
            {requestId && <code>{t("Reference", "申请编号")} · {requestId}</code>}
            <div className="hero-actions"><Link className="button button-secondary" href="/developers">{t("Review integrations", "查看接入方式")}</Link><Link className="button button-primary" href="/">{t("Return home", "返回首页")}</Link></div>
          </div>
        ) : (
          <div className="application-layout">
            <aside>
              <p className="section-index">{t("02 / QUALIFICATION", "02 / 试用评估")}</p>
              <h2>{t("Enough detail for a useful first conversation.", "先把关键信息说清楚，后续沟通才更有效。")}</h2>
              <p>{t("We use the application to understand technical fit, expected scale and which integration path should be prepared.", "我们会根据申请判断技术适配度、预期规模，以及需要提前准备哪种接入方案。")}</p>
              <ol><li><span>01</span>{t("Product and industry context", "产品与行业背景")}</li><li><span>02</span>{t("Memory use case and supported platform", "记忆使用场景与目标平台")}</li><li><span>03</span>{t("Pilot timeline and team scale", "试用时间和团队规模")}</li></ol>
            </aside>

            <form className="application-form" onSubmit={submit}>
              <div className="form-grid two-columns">
                <label><span>{t("Your name", "姓名")} *</span><input required maxLength={120} autoComplete="name" value={form.contactName} onChange={(event) => update("contactName", event.target.value)} placeholder={t("How should we address you?", "我们该如何称呼你？")} /></label>
                <label><span>{t("Work email", "工作邮箱")} *</span><input required maxLength={254} type="email" autoComplete="email" value={form.email} onChange={(event) => update("email", event.target.value)} placeholder="you@company.com" /></label>
                <label><span>{t("Company or team", "公司或团队")} *</span><input required maxLength={160} autoComplete="organization" value={form.companyName} onChange={(event) => update("companyName", event.target.value)} placeholder={t("Organization name", "公司或团队名称")} /></label>
                <label><span>{t("Industry", "所属行业")} *</span><select required value={form.industry} onChange={(event) => update("industry", event.target.value)}><option value="">{t("Select an industry", "请选择行业")}</option><option value="ai-software">{t("AI / Software", "AI / 软件")}</option><option value="enterprise-services">{t("Enterprise services", "企业服务")}</option><option value="consumer">{t("Consumer products", "消费产品")}</option><option value="finance">{t("Finance", "金融")}</option><option value="healthcare">{t("Healthcare", "医疗健康")}</option><option value="education">{t("Education", "教育")}</option><option value="robotics">{t("Robotics", "机器人")}</option><option value="research">{t("Research", "研究机构")}</option><option value="other">{t("Other", "其他")}</option></select></label>
                <label><span>{t("Company size", "团队规模")} *</span><select required value={form.companySize} onChange={(event) => update("companySize", event.target.value)}><option value="">{t("Select team size", "请选择规模")}</option><option value="1-10">1–10</option><option value="11-50">11–50</option><option value="51-200">51–200</option><option value="201-1000">201–1,000</option><option value="1000+">1,000+</option></select></label>
                <label><span>{t("Pilot timeline", "计划试用时间")} *</span><select required value={form.timeline} onChange={(event) => update("timeline", event.target.value)}><option value="">{t("Select a timeline", "请选择时间")}</option><option value="now">{t("Ready now", "现在即可开始")}</option><option value="30-days">{t("Within 30 days", "30 天内")}</option><option value="quarter">{t("This quarter", "本季度")}</option><option value="exploring">{t("Exploring", "前期调研")}</option></select></label>
              </div>

              <fieldset><legend>{t("Which interfaces or platforms are relevant?", "计划使用哪些接口或平台？")} *</legend><p>{t("Select at least one. Preview integrations may change; Pilot adapters require an approved onboarding.", "请至少选择一项。Preview 接入仍可能调整；Pilot 适配器需要通过试用审核。")}</p><div className="platform-options">{PLATFORM_OPTIONS.map((platform) => <label key={platform.value} className={form.platforms.includes(platform.value) ? "is-selected" : ""}><input type="checkbox" name="platforms" value={platform.value} checked={form.platforms.includes(platform.value)} onChange={() => togglePlatform(platform.value)} /><span>{platform.value} · {t(platform.status.en, platform.status.zh)}</span></label>)}</div></fieldset>

              <label className="form-full"><span>{t("What do you want TMCRA to remember?", "你希望 TMCRA 解决什么记忆问题？")} *</span><textarea required minLength={30} maxLength={3000} rows={7} value={form.useCase} onChange={(event) => update("useCase", event.target.value)} placeholder={t("Describe the Agent, the information that changes over time, current limitations and what a successful pilot should prove.", "请说明 Agent 的用途、哪些信息会持续变化、当前方案的限制，以及你希望通过试用验证什么。")}/><small>{form.useCase.length} / 3000</small></label>

              <label className="honeypot" aria-hidden="true">Website<input tabIndex={-1} autoComplete="off" value={form.website} onChange={(event) => update("website", event.target.value)} /></label>
              <label className="consent-row"><input required type="checkbox" checked={form.consent} onChange={(event) => update("consent", event.target.checked)} /><span>{t("I agree that TMCRA may use these details to evaluate the pilot and contact me about access.", "我同意 TMCRA 使用以上信息评估试用申请，并就接入事宜与我联系。")}</span></label>

              <div className="form-submit-row"><p aria-live="polite">{state === "error" ? t("Submission failed. Check the form and try again.", "提交失败，请检查填写内容后重试。") : t("Submissions are stored in the TMCRA D1 database and managed through the authenticated Internal control plane.", "申请会保存到 TMCRA 的 D1 数据库，并由经过身份验证的内部管理后台统一处理。")}</p><button className="button button-primary" type="submit" disabled={state === "submitting" || form.platforms.length === 0}>{state === "submitting" ? t("Submitting…", "正在提交…") : t("Submit application", "提交申请")} <span aria-hidden="true">→</span></button></div>
            </form>
          </div>
        )}
      </section>
      <MarketingFooter />
    </main>
  );
}
