"use client";

import { useEffect } from "react";
import { MarketingFooter, MarketingHeader } from "../../MarketingShell";
import { useLanguage } from "../../i18n";

const MANUAL_PACKAGE_PATH = "/downloads/tmcra-codex-latest.zip";
const CHECKSUM_PATH = `${MANUAL_PACKAGE_PATH}.sha256`;
const RELEASE_MANIFEST_PATH = "/downloads/tmcra-codex-release.json";

export default function CodexIntegrationPage() {
  const { t } = useLanguage();

  useEffect(() => {
    document.title = t("TMCRA for Codex", "TMCRA Codex 自动记忆接入");
  }, [t]);

  return (
    <main className="marketing-page codex-integration-page">
      <MarketingHeader />

      <section className="branch-hero section-shell">
        <div>
          <p className="eyebrow"><span /> {t("Codex desktop memory / 01", "Codex 桌面记忆 / 01")}</p>
          <h1>{t("Let Codex continue the project, not restart the context.", "让 Codex 接着做项目，而不是每次重新认识项目。")}</h1>
          <p>{t(
            "The TMCRA Memory app installs the Codex integration and lets you sign in and connect from one place. After you submit a prompt and before Codex answers, it recalls and injects relevant memory. When the answer finishes, it captures both your message and the final assistant response as separate actor records. Both remain recallable: user records carry requirements and facts, while Codex records carry prior progress and results. Codex output is never promoted to a user statement. New projects start automatically; existing projects can import retained Codex history instead of beginning from zero.",
            "TMCRA Memory 应用把安装、登录和连接 Codex 放在一个地方完成。你提交问题后、Codex 开始回答前，系统会按这条新问题召回并注入相关记忆；回答结束后，再分别记录你的消息与 Codex 的最终回答，并保留清晰的对话主体和来源。两类记录都可以在后续对话中被召回：用户记录承载要求与事实，Codex 记录承载已经完成的进度与结果；Codex 的回答不会被当成用户本人陈述。新项目会自动建立记忆边界；旧项目也能导入本机仍保留的 Codex 历史，不必从零接手。",
          )}</p>
          <div className="hero-actions">
            <a className="button button-primary" href="/download">{t("Download desktop app", "下载桌面应用")} <span aria-hidden="true">→</span></a>
            <a className="button button-secondary" href="#manual-install">{t("Use the manual Codex package", "使用 Codex 手动接入包")} <span aria-hidden="true">→</span></a>
            <a className="button button-secondary" href="/personal">{t("Open memory console", "打开记忆控制台")} <span aria-hidden="true">→</span></a>
          </div>
        </div>
        <aside className="branch-hero-aside">
          <span>{t("LIFECYCLE COVERAGE", "生命周期覆盖")}</span>
          <strong>03</strong>
          <p>SessionStart · UserPromptSubmit · Stop</p>
        </aside>
      </section>

      <section className="integration-flow-section section-shell section-block" id="install">
        <div className="section-heading split-heading">
          <div><p className="section-index">02 / {t("DESKTOP SETUP", "桌面端接入")}</p><h2>{t("Install TMCRA Memory, then connect Codex.", "安装 TMCRA Memory，然后连接 Codex。")}</h2></div>
          <p>{t("Download the Windows or macOS preview, verify its published SHA-256, then finish account authorization and Codex connection in the desktop app.", "下载 Windows 或 macOS 预览版并核对页面公布的 SHA-256，然后在桌面应用内完成账户授权和 Codex 连接。")}</p>
        </div>
        <ol className="integration-flow codex-install-steps">
          <li><span>01</span><div><h3>{t("Install TMCRA Memory", "安装 TMCRA Memory")}</h3><p>{t("Download the Windows or macOS preview from the release page and verify its published SHA-256 before installation.", "从下载页获取 Windows 或 macOS 预览版，并在安装前核对页面公布的 SHA-256。")}</p><p><a href="/download">{t("Download the desktop app", "下载桌面应用")}</a></p></div></li>
          <li><span>02</span><div><h3>{t("Sign in and connect Codex in the app", "在应用内登录并连接 Codex")}</h3><p>{t("Open TMCRA Memory, sign in to your account, and choose Connect Codex. Review the authorization shown in the app and approve it to finish the connection. The scoped Token is written directly to protected local configuration; you do not copy it by hand.", "打开 TMCRA Memory，登录你的账户，再选择“连接 Codex”。确认应用中展示的授权信息后完成连接。受 Scope 限制的 Token 会直接写入本机受保护的配置，不需要手动复制。")}</p></div></li>
          <li><span>03</span><div><h3>{t("Restart Codex and confirm three Hooks", "重启 Codex，并确认三项 Hook")}</h3><p>{t("Restart Codex and confirm TMCRA Memory is enabled in the Plugins page. Enter /hooks in a Codex task, review all three TMCRA lifecycle Hooks, and trust them once. Codex keeps this decision in your hands; neither the installer nor the app can silently approve Hooks.", "重启 Codex，并在插件页面确认 TMCRA Memory 已启用。在任一 Codex 任务中输入 /hooks，逐项审核三项 TMCRA 生命周期 Hook 后再确认信任。决定权始终在你手里，安装器和应用都不能静默替你批准 Hook。")}</p><pre><code>{`/hooks`}</code></pre></div></li>
          <li><span>04</span><div><h3>{t("Preview and import retained Codex history", "预览并导入保留的 Codex 历史")}</h3><p>{t("The History migration panel is visible before sign-in. Select one project for a read-only local preview; no history is uploaded during preview. After connecting your account, confirm the import separately. Reasoning, tool logs, developer instructions, passwords, keys, verification codes and credential-like messages are excluded.", "“历史迁移”面板在登录前也可见。先选择一个项目进行本机只读预览，预览不会上传任何历史；连接账户后还要单独确认导入。推理、工具日志、开发者指令、密码、密钥、验证码及疑似凭据消息都会被排除。")}</p></div></li>
          <li><span>05</span><div><h3>{t("View quota and the memory graph", "查看额度与记忆图谱")}</h3><p>{t("Use the app or personal console to inspect memory Scope and Session structure, view the memory graph, check server-reported usage and quota, or revoke one Codex connection without affecting the others.", "你可以在应用或个人控制台中查看 Scope、Session 和记忆图谱，核对服务端返回的用量与剩余额度，也可以单独吊销某一台 Codex，而不影响其他连接。")}</p></div></li>
        </ol>
        <div className="codex-history-note">
          <strong>{t("Inspect what TMCRA recalled for an answer", "查看回答前召回了哪些记忆")}</strong>
          <p>{t(
            "Automatic recall stays silent during normal work. After Codex finishes an answer, ask \"Show the TMCRA memory used for my latest answer\" to display the global and project evidence injected before that answer. The next inspection prompt cannot overwrite the completed-answer receipt. This reads the exact Hook receipt rather than running a second search, and omits internal identifiers and retrieval diagnostics.",
            "自动召回在正常使用时不会打断回答。Codex 完成回答后，在任务中输入“查看上一轮回答的召回”，即可查看该次回答前注入的全局记忆、项目记忆及数量。新的查看请求不会覆盖已完成回答的回执；这里也不会重新检索或显示内部标识、检索调试信息。",
          )}</p>
          <pre><code>{t("Show the TMCRA memory used for my latest answer", "查看上一轮回答的召回")}</code></pre>
        </div>
      </section>

      <section className="integration-flow-section section-shell section-block codex-history-section" id="manual-install">
        <div className="section-heading split-heading">
          <div><p className="section-index">03 / {t("ADVANCED MANUAL SETUP", "高级手动接入")}</p><h2>{t("For CLI-first workflows and Linux systems.", "需要命令行或使用 Linux 时，再走手动接入。")}</h2></div>
          <p>{t("The ZIP package remains available for developers who want to inspect or control each step. It is the secondary path, not the default desktop experience.", "如果你希望检查并控制每一步，仍然可以使用 ZIP 接入包。它面向开发者，不再是普通桌面用户的默认流程。")}</p>
        </div>
        <ol className="integration-flow codex-install-steps">
          <li><span>01</span><div><h3>{t("Download and extract the package", "下载并解压接入包")}</h3><p>{t("Extract it to a stable local directory. Do not leave the integration inside a temporary download folder that the system may clean up.", "把接入包解压到稳定目录，不要把插件长期放在可能被系统清理的临时下载目录中。")}</p><p><a href={MANUAL_PACKAGE_PATH}>{t("Download the manual Codex package", "下载 Codex 手动接入包")}</a> · <a href={CHECKSUM_PATH}>{t("SHA-256 checksum", "SHA-256 校验文件")}</a> · <a href={RELEASE_MANIFEST_PATH}>{t("Release manifest", "发行清单")}</a></p></div></li>
          <li><span>02</span><div><h3>{t("Run the Windows PowerShell installer", "使用 Windows PowerShell 安装")}</h3><pre><code>{`.\\Install-TMCRA.ps1`}</code></pre><p>{t("The script registers the Codex plugin, checks the local setup, and starts TMCRA account authorization.", "脚本会注册 Codex 插件、检查本机配置，并发起 TMCRA 账户授权。")}</p></div></li>
          <li><span>03</span><div><h3>{t("Run the macOS or Linux installer", "在 macOS 或 Linux 上安装")}</h3><pre><code>{`sh ./install.sh`}</code></pre><p>{t("After installation, follow the authorization prompt, then restart Codex and review the same three Hook requests.", "安装后按提示完成账户授权，再重启 Codex，并逐项审核同样的三项 Hook。")}</p></div></li>
          <li><span>04</span><div><h3>{t("Keep the authorization boundary intact", "保留清晰的授权边界")}</h3><p>{t("Manual setup still does not require server login, SSH access, or copying an API Key. Sign in and approve the short code yourself; a Token limited by Scope is delivered to protected local configuration.", "手动接入同样不需要登录服务器、使用 SSH 或复制 API Key。你需要亲自登录并确认短码，受 Scope 限制的 Token 才会写入本机受保护的配置。")}</p></div></li>
        </ol>
      </section>

      <section className="integration-contract section-shell section-block codex-scope-contract">
        <div>
          <p className="section-index">04 / {t("MEMORY BOUNDARIES", "记忆边界")}</p>
          <h2>{t("Global facts cross projects. Project work does not.", "基础资料可以跨项目，项目内容不能串门。")}</h2>
          <p>{t(
            "TMCRA keeps one user-global memory layer for stable preferences and identity. Every project receives a separate scope, while each Codex task remains a session inside that project. This preserves cross-session continuity without combining ten projects into one graph.",
            "TMCRA 用一层用户全局记忆保存稳定偏好和基础资料；每个项目使用独立 Scope，每个 Codex 任务则作为该项目下的 Session。这样既能在同一项目内跨会话延续，也不会把十个项目混进一张图。",
          )}</p>
        </div>
        <div className="contract-code" aria-label={t("TMCRA scope model", "TMCRA 分层结构")}>
          <span>memory.boundaries</span>
          <pre><code>{`user
├── global
└── project_scope
    ├── session_a
    ├── session_b
    └── session_c`}</code></pre>
        </div>
      </section>

      <section className="integration-flow-section section-shell section-block codex-history-section">
        <div className="section-heading split-heading">
          <div><p className="section-index">05 / {t("EXISTING PROJECTS", "接管旧项目")}</p><h2>{t("Import what Codex still retains.", "把 Codex 仍然保留的历史接进来。")}</h2></div>
          <p>{t("Historical import is never automatic. Preview first, choose one project, then confirm the upload. Only user and assistant messages are imported; reasoning, developer instructions, tool logs and credential-like messages are excluded.", "历史导入不会自动发生。先查看预览，再选择一个项目并明确确认。导入内容只包含用户与助手消息，不包含推理过程、开发者指令、工具日志及疑似凭据消息。")}</p>
        </div>
        <div className="codex-command-grid">
          <article><span>{t("Preview only", "只查看，不上传")}</span><pre><code>{`node .\\plugins\\tmcra-memory\\scripts\\history_import.mjs preview`}</code></pre></article>
          <article><span>{t("Import one project", "导入指定项目")}</span><pre><code>{`node .\\plugins\\tmcra-memory\\scripts\\history_import.mjs import --project "D:\\work\\my-project" --confirm --wait`}</code></pre></article>
          <article><span>{t("Preview a repository baseline", "预览当前仓库基线")}</span><pre><code>{`node .\\plugins\\tmcra-memory\\scripts\\project_bootstrap.mjs preview --project "D:\\work\\my-project"`}</code></pre></article>
          <article><span>{t("Confirm the repository baseline", "确认写入仓库基线")}</span><pre><code>{`node .\\plugins\\tmcra-memory\\scripts\\project_bootstrap.mjs import --project "D:\\work\\my-project" --confirm`}</code></pre></article>
        </div>
        <p className="codex-history-note">{t(
          "If the old transcript no longer exists locally, TMCRA cannot reconstruct it. The repository baseline uses selected project documents and recent Git history, labels them as current repository evidence, and still requires explicit confirmation before upload.",
          "如果旧对话在本机已经不存在，TMCRA 无法凭空恢复。仓库基线会读取部分项目文档和近期 Git 历史，并明确标记为“当前仓库证据”；写入前仍然需要用户确认。",
        )}</p>
      </section>

      <section className="branch-cta"><div className="section-shell"><div><p className="section-index">06 / {t("GET STARTED", "开始使用")}</p><h2>{t("Install the app or use the manual Codex path.", "安装桌面应用，或使用 Codex 手动接入。")}</h2><p>{t("The desktop app handles account authorization and local setup. The manual package remains available for controlled or scripted installations.", "桌面应用负责账户授权与本机配置；手动接入包继续用于受控安装或脚本化部署。")}</p></div><div className="hero-actions"><a className="button button-primary" href="/download">{t("Download desktop app", "下载桌面应用")} <span aria-hidden="true">→</span></a><a className="button button-secondary" href="#manual-install">{t("Manual Codex setup", "Codex 手动接入")} <span aria-hidden="true">→</span></a><a className="button button-secondary" href="/access">{t("Request pilot access", "申请试用")} <span aria-hidden="true">→</span></a></div></div></section>
      <MarketingFooter />
    </main>
  );
}
