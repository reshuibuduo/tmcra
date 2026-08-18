"use strict";

const translations = {
  zh: {
    localOnly: "本地安全控制",
    eyebrow: "TMCRA WINDOWS 应用",
    heroTitle: "让 Codex 记住项目，<br />但不把项目混在一起。",
    heroCopy: "一次完成安装、登录和连接。每个项目保留自己的记忆边界，同一项目可以跨会话继续工作。",
    authorizationCode: "本次授权码",
    authorizationHelp: "请在应用打开的 TMCRA 登录窗口中确认授权。关闭窗口不会泄露凭据，可以重新打开。",
    reopenAuthorization: "重新打开授权窗口 →",
    cancelSetup: "取消本次连接",
    openConsole: "打开个人控制台",
    securityTitle: "Token 不经过应用界面",
    securityCopy: "授权凭据由安装进程直接写入当前 Windows 用户的受保护配置。应用只接收步骤状态，不读取、展示或复制 Token。",
    progressTitle: "连接进度",
    stepEnvironment: "检查 Codex",
    stepEnvironmentCopy: "确认本机 Codex 支持插件安装。",
    stepPlugin: "安装记忆插件",
    stepPluginCopy: "校验安装包，并放入稳定的用户目录。",
    stepAuthorization: "登录并验证远端服务",
    stepAuthorizationCopy: "批准这台设备，再用不计费的身份与服务能力检查确认远端可用。",
    stepHooks: "在 Codex 确认 Hooks",
    stepHooksCopy: "重启 Codex，逐项审阅并信任三项生命周期 Hook。",
    hookBoundaryTitle: "最后一步必须由你确认",
    hookBoundaryCopy: "应用不能替你在 Codex 中批准 Hook。重启 Codex、启用 TMCRA Memory，并检查三项权限请求后再确认下面的按钮。",
    acknowledgeHooks: "我已在 Codex 完成确认 →",
    acknowledgeBoundary: "这只记录你的确认，不代表应用绕过或自动检测了 Codex 的信任决定。",
    consoleTitle: "个人控制台",
    consoleCopy: "登录后查看服务端统计的额度、剩余额度、用量、连接和记忆图。",
  },
  en: {
    localOnly: "LOCAL SECURE CONTROL",
    eyebrow: "TMCRA WINDOWS APP",
    heroTitle: "Give Codex project memory<br />without mixing projects.",
    heroCopy: "Install, sign in and connect once. Each project keeps its own memory boundary while sessions inside that project stay continuous.",
    authorizationCode: "Authorization code",
    authorizationHelp: "Approve the connection in the TMCRA sign-in window opened by the app. If you close it, you can safely reopen it.",
    reopenAuthorization: "Reopen authorization →",
    cancelSetup: "Cancel connection",
    openConsole: "Open personal console",
    securityTitle: "Tokens never enter this interface",
    securityCopy: "The installer writes the credential directly to protected configuration for the current Windows user. The app receives step status only; it never reads, displays or copies the Token.",
    progressTitle: "Connection progress",
    stepEnvironment: "Check Codex",
    stepEnvironmentCopy: "Confirm this Codex installation supports plugins.",
    stepPlugin: "Install memory plugin",
    stepPluginCopy: "Verify the package and place it in a stable user directory.",
    stepAuthorization: "Sign in and verify service",
    stepAuthorizationCopy: "Approve this device, then confirm the remote service with a non-metered identity and capability check.",
    stepHooks: "Confirm Hooks in Codex",
    stepHooksCopy: "Restart Codex, then review and trust the three lifecycle Hooks.",
    hookBoundaryTitle: "The final step stays under your control",
    hookBoundaryCopy: "The app cannot approve Hooks for you. Restart Codex, enable TMCRA Memory and inspect all three permission requests before confirming below.",
    acknowledgeHooks: "I confirmed the Hooks in Codex →",
    acknowledgeBoundary: "This records your acknowledgement only. It does not bypass or automatically detect Codex's trust decision.",
    consoleTitle: "Personal console",
    consoleCopy: "View server-reported quota, remaining quota, usage, connections and your memory graph.",
  },
};

const phases = {
  zh: {
    idle: ["准备连接 Codex", "应用会检查 Codex、安装记忆插件，再在安全窗口内完成登录。"],
    checking: ["正在检查 Codex", "确认本机版本支持插件安装，这通常只需要几秒。"],
    installing: ["正在建立安全连接", "应用正在校验安装包、安装插件并检查本机配置。"],
    awaiting_authorization: ["等待你完成登录授权", "请在 TMCRA 登录窗口确认这台设备；凭据会直接写入本机保护配置。"],
    verifying_remote: ["正在验证远端 TMCRA 服务", "授权已完成。应用正在发起不计费的身份与服务能力检查；只有验证成功才会显示为已连接。额度和剩余额度由服务端统计。"],
    connected_pending_hooks: ["TMCRA 已连接，还差 Codex 内确认", "连接和授权已经完成。现在请重启 Codex，并逐项确认三项 Hook。"],
    ready: ["TMCRA Memory 已就绪", "Codex 连接已完成。你可以从个人控制台查看额度、连接与记忆边界。"],
    cancelled: ["本次连接已取消", "没有继续运行安装进程。准备好后可以重新开始。"],
    error: ["连接没有完成", "请查看下面的原因并重试。"],
  },
  en: {
    idle: ["Ready to connect Codex", "The app will check Codex, install the memory plugin and complete sign-in in a secure window."],
    checking: ["Checking Codex", "Confirming that this Codex version supports plugin installation."],
    installing: ["Building the secure connection", "The app is verifying the package, installing the plugin and checking local configuration."],
    awaiting_authorization: ["Waiting for your authorization", "Approve this device in the TMCRA sign-in window. The credential is written directly to protected local configuration."],
    verifying_remote: ["Verifying the remote TMCRA service", "Authorization is complete. A non-metered identity and capability check must pass before the app shows connected; quota and remaining quota are reported by the service."],
    connected_pending_hooks: ["Connected — one Codex confirmation remains", "Connection and authorization are complete. Restart Codex and confirm the three Hooks."],
    ready: ["TMCRA Memory is ready", "Codex is connected. Open the personal console to view quota, connections and memory boundaries."],
    cancelled: ["Connection cancelled", "No setup process is still running. You can start again when ready."],
    error: ["Connection did not finish", "Review the reason below and try again."],
  },
};

const statusLabels = {
  zh: { pending: "等待", running: "进行中", completed: "完成", failed: "失败", action_required: "需确认" },
  en: { pending: "WAITING", running: "IN PROGRESS", completed: "DONE", failed: "FAILED", action_required: "ACTION" },
};

const connectionLabels = {
  zh: { idle: "远端服务未连接", busy: "正在验证远端服务", connected: "远端服务已连接", error: "远端服务未连接" },
  en: { idle: "REMOTE NOT CONNECTED", busy: "VERIFYING REMOTE", connected: "REMOTE CONNECTED", error: "REMOTE NOT CONNECTED" },
};

const errorMessages = {
  zh: {
    codex_not_found: "没有找到支持插件的 Codex。请先安装或更新 Codex 桌面应用，再重试。",
    plugin_archive_missing: "应用安装包里缺少 TMCRA Codex 插件。请重新下载安装程序。",
    plugin_manifest_invalid: "插件发布清单无效。请重新下载安装程序。",
    plugin_manifest_mismatch: "插件版本与发布清单不一致，应用已停止安装。请重新下载安装程序。",
    plugin_archive_mismatch: "插件完整性校验失败，应用已停止安装。请重新下载安装程序。",
    plugin_archive_invalid: "插件包内容不完整。请重新下载安装程序。",
    plugin_installer_incompatible: "这个插件包不支持桌面应用安装流程。请更新 TMCRA Memory。",
    plugin_extract_failed: "Windows 无法解压 TMCRA 插件。请确认当前用户目录可写后重试。",
    plugin_not_active: "本机存在 TMCRA 文件，但 Codex 没有启用对应版本。请重新连接 Codex。",
    remote_probe_failed: "远端 TMCRA 身份与服务验证没有通过。可能是网络中断、授权失效或服务暂不可用；请重新连接。",
    installer_failed: "安装或授权没有完成。请确认网络可用，并在登录窗口中完成授权后重试。",
    desktop_resource_missing: "应用本身缺少安装资源。请重新安装 TMCRA Memory。",
    setup_failed: "安装流程遇到未预期的问题。请重试；如果持续出现，请联系 TMCRA。",
  },
  en: {
    codex_not_found: "Codex with plugin support was not found. Install or update the Codex desktop app, then try again.",
    plugin_archive_missing: "The TMCRA Codex plugin is missing from this app installation. Download the installer again.",
    plugin_manifest_invalid: "The plugin release manifest is invalid. Download the installer again.",
    plugin_manifest_mismatch: "The plugin version does not match its release manifest. Installation was stopped.",
    plugin_archive_mismatch: "The plugin integrity check failed. Installation was stopped; download the installer again.",
    plugin_archive_invalid: "The plugin package is incomplete. Download the installer again.",
    plugin_installer_incompatible: "This plugin package does not support the desktop installation flow. Update TMCRA Memory.",
    plugin_extract_failed: "Windows could not unpack the TMCRA plugin. Check that your user directory is writable and retry.",
    plugin_not_active: "TMCRA files exist locally, but Codex is not using the matching plugin version. Reconnect Codex.",
    remote_probe_failed: "The remote TMCRA identity and service check failed. The network, authorization or service may be unavailable; reconnect to verify.",
    installer_failed: "Installation or authorization did not complete. Check the network, finish sign-in and retry.",
    desktop_resource_missing: "This app installation is missing a required setup resource. Reinstall TMCRA Memory.",
    setup_failed: "Setup encountered an unexpected problem. Retry, or contact TMCRA if it continues.",
  },
};

const elements = {
  languageToggle: document.querySelector("#languageToggle"),
  connectionPill: document.querySelector("#connectionPill"),
  connectionLabel: document.querySelector("#connectionLabel"),
  phaseTitle: document.querySelector("#phaseTitle"),
  phaseDescription: document.querySelector("#phaseDescription"),
  setupButton: document.querySelector("#setupButton"),
  setupButtonLabel: document.querySelector("#setupButtonLabel"),
  cancelButton: document.querySelector("#cancelButton"),
  consoleButton: document.querySelector("#consoleButton"),
  errorPanel: document.querySelector("#errorPanel"),
  errorDetail: document.querySelector("#errorDetail"),
  authorizationPanel: document.querySelector("#authorizationPanel"),
  authorizationCode: document.querySelector("#authorizationCode"),
  openAuthorizationButton: document.querySelector("#openAuthorizationButton"),
  hookBoundary: document.querySelector("#hookBoundary"),
  acknowledgeHooksButton: document.querySelector("#acknowledgeHooksButton"),
};

let locale = localStorage.getItem("tmcra-locale") === "en" ? "en" : "zh";
let state = {
  phase: "idle",
  connected: false,
  busy: false,
  error: null,
  authorization: null,
  steps: [],
};

function applyStaticTranslations() {
  document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    const key = node.dataset.i18n;
    const value = translations[locale][key];
    if (typeof value !== "string") return;
    if (key === "heroTitle") node.innerHTML = value;
    else node.textContent = value;
  });
  elements.languageToggle.dataset.locale = locale;
  elements.languageToggle.setAttribute(
    "aria-label",
    locale === "zh" ? "Switch to English" : "切换到中文",
  );
}

function render() {
  applyStaticTranslations();
  const phase = phases[locale][state.phase] ?? phases[locale].idle;
  elements.phaseTitle.textContent = phase[0];
  elements.phaseDescription.textContent = phase[1];

  const connectionStatus = state.connected
    ? "connected"
    : state.busy
      ? "busy"
      : state.phase === "error"
        ? "error"
        : "idle";
  elements.connectionPill.dataset.status = connectionStatus;
  elements.connectionLabel.textContent = connectionLabels[locale][connectionStatus];

  elements.setupButton.disabled = state.busy;
  elements.setupButtonLabel.textContent = state.busy
    ? locale === "zh" ? "正在连接…" : "Connecting…"
    : state.connected
      ? locale === "zh" ? "重新连接 Codex" : "Reconnect Codex"
      : locale === "zh" ? "登录并连接 Codex" : "Sign in and connect Codex";
  elements.cancelButton.hidden = !state.busy;

  elements.errorPanel.hidden = state.phase !== "error" || !state.error;
  if (state.error) {
    elements.errorDetail.textContent =
      errorMessages[locale][state.error.code] ??
      errorMessages[locale].setup_failed;
  }

  elements.authorizationPanel.hidden = !state.authorization;
  elements.authorizationCode.textContent = state.authorization?.userCode ?? "—";

  const stepById = new Map((state.steps ?? []).map((step) => [step.id, step]));
  document.querySelectorAll(".step").forEach((node) => {
    const step = stepById.get(node.dataset.step) ?? { status: "pending" };
    node.dataset.status = step.status;
    const label = node.querySelector(".step-status");
    label.textContent = statusLabels[locale][step.status] ?? statusLabels[locale].pending;
  });

  elements.hookBoundary.hidden =
    state.phase !== "connected_pending_hooks" || state.hookAcknowledged === true;
}

elements.languageToggle.addEventListener("click", () => {
  document.body.classList.add("language-switching");
  window.setTimeout(() => {
    locale = locale === "zh" ? "en" : "zh";
    localStorage.setItem("tmcra-locale", locale);
    render();
    window.setTimeout(() => document.body.classList.remove("language-switching"), 180);
  }, 100);
});

elements.setupButton.addEventListener("click", () => window.tmcra.startSetup());
elements.cancelButton.addEventListener("click", () => window.tmcra.cancelSetup());
elements.consoleButton.addEventListener("click", () => window.tmcra.openConsole());
elements.openAuthorizationButton.addEventListener("click", () => window.tmcra.openAuthorization());
elements.acknowledgeHooksButton.addEventListener("click", () => window.tmcra.acknowledgeHooks());

window.tmcra.onStateChanged((nextState) => {
  state = nextState;
  render();
});

void window.tmcra.getState().then((nextState) => {
  state = nextState;
  render();
});

render();
