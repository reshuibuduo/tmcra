"use client";

import Image from "next/image";
import { MarketingFooter, MarketingHeader } from "../MarketingShell";
import { useLanguage } from "../i18n";

export default function DownloadPage() {
  const { t } = useLanguage();

  return (
    <main className="public-page">
      <MarketingHeader />
      <section className="public-hero download-hero section-shell">
        <div>
          <p className="eyebrow"><span /> {t("TMCRA APPS / PUBLIC PREVIEW", "TMCRA 应用 / 公开预览版")}</p>
          <h1>{t("Install TMCRA Memory.", "安装 TMCRA Memory。")}</h1>
          <p className="public-lede">
            {t(
              "Connect Codex, Claude Code, ZCode, DeepSeek Harness, OpenClaw, Hermes, and MCP from one desktop app. TMCRA also provides automatic memory, Personal Knowledge, memory graphs, and Obsidian sync.",
              "通过一个桌面应用连接 Codex、Claude Code、ZCode、DeepSeek Harness、OpenClaw、Hermes 与 MCP，并使用自动记忆、个人知识库、记忆图谱和 Obsidian 同步。",
            )}
          </p>
          <div className="hero-actions">
            <a className="button button-primary" href="/downloads/TMCRA-Memory-Setup-latest.exe">{t("Windows x64", "Windows x64")} <span aria-hidden="true">→</span></a>
            <a className="button button-secondary" href="/downloads/TMCRA-Memory-latest-arm64.dmg">{t("Mac Apple Silicon", "Mac Apple 芯片")} <span aria-hidden="true">→</span></a>
            <a className="button button-secondary" href="/downloads/TMCRA-Memory-latest-x64.dmg">{t("Mac Intel", "Mac Intel")} <span aria-hidden="true">→</span></a>
            <a className="button button-secondary" href="/downloads/TMCRA-Memory-Mobile-latest.apk">{t("Android", "Android")} <span aria-hidden="true">→</span></a>
          </div>
          <dl className="download-facts">
            <div><dt>{t("Platform", "平台")}</dt><dd>Windows 10/11 · macOS 12+ · Android 7+</dd></div>
            <div><dt>{t("Channel", "发布通道")}</dt><dd>{t("Public preview", "公开预览")}</dd></div>
            <div><dt>{t("Integrity", "完整性")}</dt><dd>SHA-256</dd></div>
          </dl>
        </div>
        <div className="download-visual" aria-label={t("TMCRA desktop application", "TMCRA 桌面应用")}>
          <Image src="/brand/tmcra-app-icon.png" alt="TMCRA" width={512} height={512} priority unoptimized />
          <div><span>TMCRA MEMORY</span><strong>{t("Desktop and mobile", "桌面端与移动端")}</strong><small>PUBLIC PREVIEW / 2026</small></div>
        </div>
      </section>

      <section className="download-preview-band" aria-labelledby="desktop-release-title">
        <div className="section-shell download-preview-layout">
          <div>
            <p className="section-index">PACKAGES / VERIFIED OUTPUTS</p>
            <h2 id="desktop-release-title">{t("Choose the package for your device.", "选择与你的设备匹配的安装包。")}</h2>
            <p>
              {t(
                "These are preview builds. Windows and macOS packages are not yet backed by commercial code-signing certificates, so the operating system may show an unidentified-developer warning. The Android APK is release-signed by TMCRA.",
                "这些是预览版本。Windows 与 macOS 安装包尚未使用商业代码签名证书，因此操作系统可能显示未知开发者警告；Android APK 已使用 TMCRA 发布证书签名。",
              )}
            </p>
            <p className="download-preview-links">
              <a href="/downloads/TMCRA-Memory-Setup-latest.exe.sha256">Windows SHA-256</a>
              <a href="/downloads/TMCRA-Memory-latest-arm64.dmg.sha256">Mac arm64 SHA-256</a>
              <a href="/downloads/TMCRA-Memory-latest-x64.dmg.sha256">Mac x64 SHA-256</a>
              <a href="/downloads/TMCRA-Memory-Mobile-latest.apk.sha256">Android SHA-256</a>
            </p>
          </div>
          <ol>
            <li><span>01</span><p>{t("Windows: run the installer and confirm the SmartScreen prompt if it appears.", "Windows：运行安装程序；如出现 SmartScreen 提示，请核对文件与 SHA-256 后确认。")}</p></li>
            <li><span>02</span><p>{t("macOS: install the app, try to open it once, then follow the detailed Gatekeeper steps below.", "macOS：安装应用并尝试打开一次，然后按照下方的 Gatekeeper 详细步骤操作。")}</p></li>
            <li><span>03</span><p>{t("Android: allow installation from this source once, then install the signed APK.", "Android：仅为当前来源允许一次安装，然后安装已签名 APK。")}</p></li>
          </ol>
        </div>
      </section>

      <section className="integration-flow-section section-shell section-block" aria-labelledby="mac-unsigned-install-title">
        <div className="section-heading split-heading">
          <div>
            <p className="section-index">MACOS / UNSIGNED PREVIEW</p>
            <h2 id="mac-unsigned-install-title">{t("Open the unsigned macOS preview safely.", "安全打开未签名的 macOS 预览版。")}</h2>
          </div>
          <p>{t(
            "TMCRA does not yet have an Apple Developer signing certificate. macOS therefore blocks the first launch by default. Follow this app-specific exception flow only after downloading from tmcra.com and verifying the published SHA-256.",
            "TMCRA 目前还没有 Apple Developer 签名证书，因此 macOS 默认会阻止首次启动。请仅在确认安装包来自 tmcra.com，并核对页面公布的 SHA-256 后，再为该应用执行一次性放行。",
          )}</p>
        </div>

        <ol className="integration-flow codex-install-steps">
          <li><span>01</span><div><h3>{t("Choose the correct Mac package", "选择正确的 Mac 安装包")}</h3><p>{t("Open Apple menu > About This Mac. Choose Apple Silicon for M1, M2, M3, M4 or later Apple chips; choose Intel only for an Intel processor.", "打开苹果菜单 > 关于本机。M1、M2、M3、M4 或更新的 Apple 芯片请选择 Apple Silicon；只有 Intel 处理器才选择 Intel 版本。")}</p></div></li>
          <li><span>02</span><div><h3>{t("Verify the downloaded DMG", "校验下载的 DMG")}</h3><p>{t("Open Terminal and run the command matching your package. Compare the result with the SHA-256 link on this page before continuing.", "打开“终端”，运行与你下载版本对应的命令，并把结果与本页的 SHA-256 校验文件进行比对。")}</p><pre><code>{`# Apple Silicon
shasum -a 256 ~/Downloads/TMCRA-Memory-latest-arm64.dmg

# Intel
shasum -a 256 ~/Downloads/TMCRA-Memory-latest-x64.dmg`}</code></pre></div></li>
          <li><span>03</span><div><h3>{t("Install and attempt the first launch", "安装并尝试首次启动")}</h3><p>{t("Open the DMG, drag TMCRA Memory into Applications, eject the DMG, then open TMCRA Memory from Applications once. When macOS shows the unidentified-developer warning, close the dialog.", "打开 DMG，把 TMCRA Memory 拖入“应用程序”，推出 DMG，然后从“应用程序”中尝试打开 TMCRA Memory。macOS 显示未知开发者警告后，关闭该提示框。")}</p></div></li>
          <li><span>04</span><div><h3>{t("Open Privacy & Security", "打开“隐私与安全性”")}</h3><p>{t("Choose Apple menu > System Settings > Privacy & Security. Scroll down to the Security section and find the message that TMCRA Memory was blocked.", "依次打开苹果菜单 > 系统设置 > 隐私与安全性，向下滚动到“安全性”区域，找到 TMCRA Memory 被阻止的提示。")}</p></div></li>
          <li><span>05</span><div><h3>{t("Click Open Anyway", "点击“仍要打开”")}</h3><p>{t("Click Open Anyway next to the blocked-app message. Apple makes this button available for about an hour after you try to open the app. If it is missing, try opening TMCRA Memory again and return to Privacy & Security.", "在被阻止应用的提示旁点击“仍要打开”。Apple 说明该按钮会在尝试打开应用后的约一小时内出现；如果没有看到，请再次尝试启动 TMCRA Memory，然后返回“隐私与安全性”。")}</p></div></li>
          <li><span>06</span><div><h3>{t("Authenticate and confirm once", "验证身份并确认一次")}</h3><p>{t("Enter your Mac login password when requested, click OK, then confirm Open in the final dialog. macOS saves TMCRA Memory as an app-specific exception, so future launches work by double-clicking it normally.", "按提示输入 Mac 登录密码并点击“好”，再在最后的对话框中确认“打开”。macOS 会把 TMCRA Memory 保存为该应用专属的例外，之后即可正常双击启动。")}</p></div></li>
        </ol>

        <div className="codex-history-note">
          <strong>{t("Do not disable Gatekeeper globally", "不要全局关闭 Gatekeeper")}</strong>
          <p>{t(
            "This procedure grants an exception only to TMCRA Memory. Do not disable macOS security globally or paste unverified Terminal bypass commands. Stop if the download domain, filename, file size or SHA-256 does not match the values published here.",
            "以上流程只为 TMCRA Memory 添加应用级例外。不要全局关闭 macOS 安全机制，也不要粘贴未经核验的终端绕过命令。如果下载域名、文件名、文件大小或 SHA-256 与本页公布内容不一致，请立即停止安装。",
          )}</p>
          <p><a href="https://support.apple.com/guide/mac-help/mh40617/mac" target="_blank" rel="noreferrer">{t("Read Apple's official Open Anyway instructions", "查看 Apple 官方“仍要打开”说明")}</a></p>
        </div>
      </section>

      <section className="public-band">
        <div className="section-shell public-columns">
          <article><span>01</span><h2>{t("Verify before installing", "安装前校验")}</h2><p>{t("Compare the downloaded file with the SHA-256 published above.", "将下载文件与上方发布的 SHA-256 进行比对。")}</p></article>
          <article><span>02</span><h2>{t("Connect your tools", "连接你的工具")}</h2><p>{t("Use the desktop app for one-click connection, then inspect each integration and its memory activity in one place.", "使用桌面应用一键连接，并在同一处查看各平台接入状态与记忆活动。")}</p></article>
          <article><span>03</span><h2>{t("iPhone and iPad", "iPhone 与 iPad")}</h2><p>{t("The native iOS project is ready, but an installable IPA still requires Apple Developer signing. Use the mobile web console until TestFlight opens.", "iOS 原生工程已经就绪，但可安装 IPA 仍需 Apple Developer 签名；TestFlight 开放前可使用移动网页控制台。")}</p></article>
        </div>
      </section>
      <MarketingFooter />
    </main>
  );
}
