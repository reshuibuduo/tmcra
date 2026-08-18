import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  session as electronSession,
} from "electron";

import {
  createInstallState,
  normalizePublicState,
  reduceInstallState,
} from "./lib/install-state.mjs";
import {
  stateEventBeforeInstallerExit,
  stateEventForInstallerExit,
} from "./lib/installer-policy.mjs";
import { preparePluginBundle } from "./lib/plugin-bundle.mjs";
import { NdjsonLineBuffer, parseInstallerEvent } from "./lib/progress-events.mjs";
import {
  resolveProductResourcePath,
  resolveProductScriptPath,
} from "./lib/resource-paths.mjs";
import {
  allowedRemoteOrigins,
  buildConsoleUrl,
  isHttpsResource,
  normalizeAuthorizationBaseUrl,
  validateRemoteNavigation,
  validateVerificationUrl,
} from "./lib/security.mjs";

const SOURCE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PACKAGE_JSON = JSON.parse(await readFile(join(SOURCE_ROOT, "package.json"), "utf8"));
const PRODUCT_CONFIG = PACKAGE_JSON.tmcra ?? {};
const AUTHORIZATION_BASE_URL = normalizeAuthorizationBaseUrl(
  PRODUCT_CONFIG.authorizationBaseUrl,
);
const AUTHORIZATION_ORIGIN = new URL(AUTHORIZATION_BASE_URL).origin;
const REMOTE_ORIGINS = allowedRemoteOrigins(
  AUTHORIZATION_ORIGIN,
  PRODUCT_CONFIG.allowedAuthenticationOrigins ?? [],
);
const CONSOLE_URL = buildConsoleUrl(
  AUTHORIZATION_BASE_URL,
  PRODUCT_CONFIG.consolePath ?? "/personal",
);
const REMOTE_PARTITION = "persist:tmcra-account";
const POWERSHELL_ARGUMENTS = [
  "-NoLogo",
  "-NoProfile",
  "-NonInteractive",
  "-ExecutionPolicy",
  "Bypass",
];

let mainWindow = null;
let authorizationWindow = null;
let consoleWindow = null;
let installState = createInstallState();
let activeSetup = null;
let quitting = false;
let remoteSessionConfigured = false;

class CancelledError extends Error {
  constructor() {
    super("TMCRA setup was cancelled.");
    this.name = "CancelledError";
    this.code = "setup_cancelled";
  }
}

function productResourcePath(name) {
  return resolveProductResourcePath(
    { isPackaged: app.isPackaged, resourcesPath: process.resourcesPath, sourceRoot: SOURCE_ROOT },
    name,
  );
}

function productScriptPath(name) {
  return resolveProductScriptPath(
    { isPackaged: app.isPackaged, resourcesPath: process.resourcesPath, sourceRoot: SOURCE_ROOT },
    name,
  );
}

function localIntegrationRoot() {
  const localAppData = process.env.LOCALAPPDATA || join(homedir(), "AppData", "Local");
  return join(localAppData, "TMCRA", "CodexIntegration");
}

function publishState() {
  installState = normalizePublicState(installState);
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("tmcra:state-changed", installState);
  }
}

function dispatch(event) {
  installState = reduceInstallState(installState, event);
  publishState();
}

function assertTrustedIpc(event) {
  if (!mainWindow || mainWindow.isDestroyed() || event.sender !== mainWindow.webContents) {
    throw new Error("Untrusted TMCRA IPC sender.");
  }
  const senderUrl = event.senderFrame?.url || "";
  if (!senderUrl.startsWith("file:")) {
    throw new Error("TMCRA IPC is available only to the local application UI.");
  }
}

function createMainWindow() {
  const window = new BrowserWindow({
    width: 1160,
    height: 760,
    minWidth: 940,
    minHeight: 660,
    show: false,
    backgroundColor: "#071019",
    title: "TMCRA Memory",
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(SOURCE_ROOT, "src", "preload.cjs"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      devTools: !app.isPackaged,
    },
  });

  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  window.webContents.on("will-attach-webview", (event) => event.preventDefault());
  window.once("ready-to-show", () => window.show());
  window.on("close", (event) => {
    if (!quitting && activeSetup) {
      event.preventDefault();
      void cancelSetup().finally(() => {
        quitting = true;
        app.quit();
      });
    }
  });
  window.on("closed", () => {
    mainWindow = null;
  });
  void window.loadFile(join(SOURCE_ROOT, "src", "renderer", "index.html"));
  return window;
}

function configureRemoteSession() {
  const remoteSession = electronSession.fromPartition(REMOTE_PARTITION, { cache: true });
  if (remoteSessionConfigured) return remoteSession;
  remoteSessionConfigured = true;

  remoteSession.setPermissionCheckHandler(() => false);
  remoteSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
  remoteSession.webRequest.onBeforeRequest({ urls: ["*://*/*", "file://*/*"] }, (details, callback) => {
    callback({ cancel: !isHttpsResource(details.url) });
  });
  return remoteSession;
}

function secureRemoteWindow(window, initialUrl) {
  const navigate = (candidate) => {
    try {
      return validateRemoteNavigation(candidate, REMOTE_ORIGINS);
    } catch {
      return null;
    }
  };

  window.webContents.setWindowOpenHandler(({ url }) => {
    const safeUrl = navigate(url);
    if (safeUrl) {
      queueMicrotask(() => {
        if (!window.isDestroyed()) void window.loadURL(safeUrl);
      });
    }
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) => {
    if (!navigate(url)) {
      event.preventDefault();
      void showBlockedNavigation(window);
    }
  });
  window.webContents.on("will-redirect", (event, url) => {
    if (!navigate(url)) {
      event.preventDefault();
      void showBlockedNavigation(window);
    }
  });
  window.webContents.on("will-attach-webview", (event) => event.preventDefault());
  window.webContents.on("render-process-gone", () => {
    if (!window.isDestroyed()) window.close();
  });
  void window.loadURL(navigate(initialUrl));
}

async function showBlockedNavigation(window) {
  if (!window || window.isDestroyed()) return;
  await dialog.showMessageBox(window, {
    type: "warning",
    title: "TMCRA Memory",
    message: "已阻止离开受信任登录站点的跳转",
    detail: "为了保护本机授权，应用内窗口只允许 TMCRA 与明确配置的登录来源。",
    buttons: ["知道了"],
    noLink: true,
  });
}

function createRemoteWindow({ title, url, parent = mainWindow }) {
  configureRemoteSession();
  const window = new BrowserWindow({
    width: 1060,
    height: 760,
    minWidth: 820,
    minHeight: 620,
    show: false,
    parent: parent && !parent.isDestroyed() ? parent : undefined,
    modal: false,
    title,
    autoHideMenuBar: true,
    backgroundColor: "#071019",
    webPreferences: {
      partition: REMOTE_PARTITION,
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      devTools: false,
    },
  });
  window.once("ready-to-show", () => window.show());
  secureRemoteWindow(window, url);
  return window;
}

function openAuthorizationWindow() {
  const candidate = installState.authorization?.verificationUrl;
  if (!candidate) return false;

  let verificationUrl;
  try {
    verificationUrl = validateVerificationUrl(candidate, AUTHORIZATION_BASE_URL);
  } catch {
    return false;
  }
  if (authorizationWindow && !authorizationWindow.isDestroyed()) {
    authorizationWindow.focus();
    return true;
  }
  authorizationWindow = createRemoteWindow({
    title: "登录并授权 · TMCRA Memory",
    url: verificationUrl,
  });
  authorizationWindow.on("closed", () => {
    authorizationWindow = null;
  });
  return true;
}

function openConsoleWindow() {
  if (consoleWindow && !consoleWindow.isDestroyed()) {
    consoleWindow.focus();
    return;
  }
  consoleWindow = createRemoteWindow({
    title: "个人控制台 · TMCRA Memory",
    url: CONSOLE_URL,
  });
  consoleWindow.on("closed", () => {
    consoleWindow = null;
  });
}

function closeAuthorizationWindow() {
  if (authorizationWindow && !authorizationWindow.isDestroyed()) authorizationWindow.close();
  authorizationWindow = null;
}

function assertActive(setup) {
  if (activeSetup !== setup || setup.cancelled) throw new CancelledError();
}

function runOwnedProcess(setup, command, args, options = {}) {
  assertActive(setup);
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, {
      windowsHide: true,
      shell: false,
      ...options,
    });
    setup.child = child;

    let settled = false;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      if (setup.child === child) setup.child = null;
      callback(value);
    };
    child.once("error", (error) => finish(reject, error));
    child.once("close", (code, signal) => finish(resolvePromise, { code, signal }));
  });
}

async function detectCodex(setup) {
  const script = productScriptPath("find-codex.ps1");
  if (!existsSync(script)) {
    throw setupError("desktop_resource_missing", "The Codex detection script is missing.");
  }

  const child = spawn("powershell.exe", [...POWERSHELL_ARGUMENTS, "-File", script], {
    windowsHide: true,
    shell: false,
    stdio: ["ignore", "pipe", "ignore"],
  });
  setup.child = child;
  let output = "";
  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    if (output.length < 8192) output += chunk;
  });
  const result = await new Promise((resolvePromise, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => resolvePromise({ code, signal }));
  });
  if (setup.child === child) setup.child = null;
  assertActive(setup);
  if (result.code !== 0 || !output.trim()) {
    throw setupError(
      "codex_not_found",
      "Codex with plugin support was not found on this Windows account.",
    );
  }
  return output.trim().split(/\r?\n/u)[0];
}

async function extractPlugin(setup, archivePath, destinationPath) {
  const script = productScriptPath("expand-plugin.ps1");
  if (!existsSync(script)) {
    throw setupError("desktop_resource_missing", "The plugin extraction script is missing.");
  }
  const result = await runOwnedProcess(
    setup,
    "powershell.exe",
    [
      ...POWERSHELL_ARGUMENTS,
      "-File",
      script,
      "-ArchivePath",
      archivePath,
      "-DestinationPath",
      destinationPath,
    ],
    { stdio: "ignore" },
  );
  assertActive(setup);
  if (result.code !== 0) {
    throw setupError("plugin_extract_failed", "Windows could not unpack the TMCRA plugin.");
  }
}

async function runInstaller(setup, installerPath) {
  const stdout = new NdjsonLineBuffer();
  const stderr = new NdjsonLineBuffer();
  const handleLine = (line) => {
    const event = parseInstallerEvent(line, { authorizationBaseUrl: AUTHORIZATION_BASE_URL });
    if (!event) return;
    if (event.type === "authorization_required") {
      dispatch(event);
      openAuthorizationWindow();
      return;
    }
    const stateEvent = stateEventBeforeInstallerExit(event);
    if (stateEvent?.type === "remote_verification") {
      closeAuthorizationWindow();
      dispatch(stateEvent);
      return;
    }
    // A progress producer may announce completion before PowerShell has run
    // check_config.mjs. The renderer is connected only after the owned process
    // exits successfully, proving the authenticated non-metered service check passed.
    if (stateEvent) dispatch(stateEvent);
  };

  const child = spawn(
    "powershell.exe",
    [
      ...POWERSHELL_ARGUMENTS,
      "-File",
      installerPath,
      "-NodePath",
      process.execPath,
      "-AuthorizationUrl",
      AUTHORIZATION_BASE_URL,
      "-NoBrowser",
      "-ProgressJson",
    ],
    {
      windowsHide: true,
      shell: false,
      env: {
        ...process.env,
        ELECTRON_RUN_AS_NODE: "1",
        TMCRA_DESKTOP_APP: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  setup.child = child;
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => stdout.push(chunk).forEach(handleLine));
  child.stderr.on("data", (chunk) => stderr.push(chunk).forEach(handleLine));

  const result = await new Promise((resolvePromise, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => {
      stdout.flush().forEach(handleLine);
      stderr.flush().forEach(handleLine);
      resolvePromise({ code, signal });
    });
  });
  if (setup.child === child) setup.child = null;
  assertActive(setup);
  const exitEvent = stateEventForInstallerExit(result.code);
  if (exitEvent.type === "error") throw setupError(exitEvent.code, exitEvent.message);
  dispatch(exitEvent);
}

async function startSetup() {
  if (activeSetup) return normalizePublicState(installState);
  const setup = { cancelled: false, child: null };
  activeSetup = setup;
  dispatch({ type: "start" });

  try {
    await detectCodex(setup);
    assertActive(setup);
    dispatch({ type: "progress", step: "environment", status: "completed" });
    dispatch({ type: "progress", step: "plugin", status: "running" });

    const archiveName = PRODUCT_CONFIG.pluginArchive ?? "tmcra-codex-latest.zip";
    const manifestName = PRODUCT_CONFIG.pluginReleaseManifest ?? "tmcra-codex-release.json";
    const bundle = await preparePluginBundle({
      archivePath: productResourcePath(archiveName),
      releaseManifestPath: productResourcePath(manifestName),
      integrationRoot: localIntegrationRoot(),
      fallbackVersion: PRODUCT_CONFIG.fallbackPluginVersion,
      runExtraction: (archivePath, destinationPath) =>
        extractPlugin(setup, archivePath, destinationPath),
      assertActive: () => assertActive(setup),
    });

    assertActive(setup);
    await runInstaller(setup, bundle.installerPath);
    assertActive(setup);
    closeAuthorizationWindow();
  } catch (error) {
    closeAuthorizationWindow();
    if (error instanceof CancelledError || setup.cancelled) {
      if (installState.phase !== "cancelled") dispatch({ type: "cancel" });
    } else {
      dispatch({
        type: "error",
        code: error?.code || "setup_failed",
        message: error?.message || "TMCRA setup failed.",
      });
    }
  } finally {
    if (activeSetup === setup) activeSetup = null;
  }
  return normalizePublicState(installState);
}

async function terminateProcessTree(child) {
  if (!child || !Number.isSafeInteger(child.pid)) return;
  if (process.platform === "win32") {
    await new Promise((resolvePromise) => {
      const killer = spawn("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], {
        windowsHide: true,
        stdio: "ignore",
      });
      const timer = setTimeout(() => {
        killer.kill();
        resolvePromise();
      }, 5000);
      killer.once("error", () => {
        clearTimeout(timer);
        resolvePromise();
      });
      killer.once("close", () => {
        clearTimeout(timer);
        resolvePromise();
      });
    });
  } else {
    child.kill("SIGTERM");
  }
}

async function cancelSetup() {
  const setup = activeSetup;
  if (!setup) return normalizePublicState(installState);
  setup.cancelled = true;
  const child = setup.child;
  closeAuthorizationWindow();
  await terminateProcessTree(child);
  if (setup.child === child) setup.child = null;
  if (installState.phase !== "cancelled") dispatch({ type: "cancel" });
  return normalizePublicState(installState);
}

function setupError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

async function preferencePath() {
  const directory = app.getPath("userData");
  await mkdir(directory, { recursive: true });
  return join(directory, "preferences.json");
}

async function writeHookAcknowledgement() {
  await writeFile(
    await preferencePath(),
    `${JSON.stringify({ schemaVersion: 1, hookAcknowledged: true }, null, 2)}\n`,
    "utf8",
  );
}

async function readHookAcknowledgement() {
  try {
    const preferences = JSON.parse(await readFile(await preferencePath(), "utf8"));
    return preferences?.schemaVersion === 1 && preferences.hookAcknowledged === true;
  } catch {
    return false;
  }
}

async function bundledPluginVersion() {
  const manifestName = PRODUCT_CONFIG.pluginReleaseManifest ?? "tmcra-codex-release.json";
  const manifestPath = productResourcePath(manifestName);
  if (!existsSync(manifestPath)) return null;
  try {
    const release = JSON.parse(await readFile(manifestPath, "utf8"));
    const version = String(release?.plugin?.version || "");
    return /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/u.test(version) ? version : null;
  } catch {
    return null;
  }
}

async function verifyCodexPluginRegistration(setup, codexPath, version, marketplaceRoot) {
  const script = productScriptPath("verify-codex-plugin.ps1");
  if (!existsSync(script)) {
    throw setupError("desktop_resource_missing", "The Codex plugin verification script is missing.");
  }
  const result = await runOwnedProcess(
    setup,
    "powershell.exe",
    [
      ...POWERSHELL_ARGUMENTS,
      "-File",
      script,
      "-CodexPath",
      codexPath,
      "-ExpectedVersion",
      version,
      "-ExpectedMarketplaceRoot",
      marketplaceRoot,
    ],
    { stdio: "ignore" },
  );
  assertActive(setup);
  if (result.code !== 0) {
    throw setupError(
      "plugin_not_active",
      "Codex does not have the matching TMCRA Memory plugin registration.",
    );
  }
}

async function runRemoteServiceProbe(setup, marketplaceRoot) {
  const script = join(
    marketplaceRoot,
    "plugins",
    "tmcra-memory",
    "scripts",
    "check_config.mjs",
  );
  if (!existsSync(script)) {
    throw setupError("plugin_archive_invalid", "The installed TMCRA remote check is missing.");
  }
  const result = await runOwnedProcess(setup, process.execPath, [script], {
    cwd: marketplaceRoot,
    env: {
      ...process.env,
      ELECTRON_RUN_AS_NODE: "1",
      TMCRA_DESKTOP_APP: "1",
    },
    stdio: "ignore",
  });
  assertActive(setup);
  if (result.code !== 0) {
    throw setupError(
      "remote_probe_failed",
      "The authenticated TMCRA service check did not complete successfully.",
    );
  }
}

async function restoreVerifiedStatus() {
  if (activeSetup) return;
  const configPath = process.env.TMCRA_CONFIG_FILE || join(homedir(), ".config", "tmcra", "config.json");
  const version = await bundledPluginVersion();
  if (!version || !existsSync(configPath)) return;

  const marketplaceRoot = join(localIntegrationRoot(), version);
  const pluginManifestPath = join(
    marketplaceRoot,
    "plugins",
    "tmcra-memory",
    ".codex-plugin",
    "plugin.json",
  );
  if (!existsSync(pluginManifestPath)) return;
  try {
    const plugin = JSON.parse(await readFile(pluginManifestPath, "utf8"));
    if (plugin?.name !== "tmcra-memory" || plugin?.version !== version) return;
  } catch {
    return;
  }

  const setup = { cancelled: false, child: null, kind: "startup-verification" };
  activeSetup = setup;
  dispatch({ type: "start" });
  try {
    const codexPath = await detectCodex(setup);
    assertActive(setup);
    dispatch({ type: "progress", step: "environment", status: "completed" });
    dispatch({ type: "progress", step: "plugin", status: "running" });
    await verifyCodexPluginRegistration(setup, codexPath, version, marketplaceRoot);
    dispatch({ type: "progress", step: "plugin", status: "completed" });
    dispatch({ type: "remote_verification" });
    await runRemoteServiceProbe(setup, marketplaceRoot);
    dispatch({ type: "complete" });
    if (await readHookAcknowledgement()) dispatch({ type: "acknowledge_hooks" });
  } catch (error) {
    if (error instanceof CancelledError || setup.cancelled) {
      dispatch({ type: "reset" });
    } else {
      dispatch({
        type: "error",
        code: error?.code || "remote_probe_failed",
        message: error?.message || "TMCRA startup verification failed.",
      });
    }
  } finally {
    if (activeSetup === setup) activeSetup = null;
  }
}

function registerIpc() {
  ipcMain.handle("tmcra:get-state", (event) => {
    assertTrustedIpc(event);
    return normalizePublicState(installState);
  });
  ipcMain.handle("tmcra:start-setup", (event) => {
    assertTrustedIpc(event);
    void startSetup();
    return normalizePublicState(installState);
  });
  ipcMain.handle("tmcra:cancel-setup", async (event) => {
    assertTrustedIpc(event);
    return cancelSetup();
  });
  ipcMain.handle("tmcra:open-authorization", (event) => {
    assertTrustedIpc(event);
    return openAuthorizationWindow();
  });
  ipcMain.handle("tmcra:open-console", (event) => {
    assertTrustedIpc(event);
    openConsoleWindow();
    return true;
  });
  ipcMain.handle("tmcra:acknowledge-hooks", async (event) => {
    assertTrustedIpc(event);
    if (!installState.connected) return normalizePublicState(installState);
    await writeHookAcknowledgement();
    dispatch({ type: "acknowledge_hooks" });
    return normalizePublicState(installState);
  });
}

app.setAppUserModelId("com.tmcra.memory");
const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) app.quit();
app.on("second-instance", () => {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
});
app.on("certificate-error", (event, _webContents, _url, _error, _certificate, callback) => {
  event.preventDefault();
  callback(false);
});
app.on("before-quit", () => {
  quitting = true;
  const child = activeSetup?.child;
  if (child && Number.isSafeInteger(child.pid) && process.platform === "win32") {
    spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], {
      windowsHide: true,
      stdio: "ignore",
      timeout: 5000,
    });
  }
});
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
app.on("activate", () => {
  if (!mainWindow) mainWindow = createMainWindow();
});

if (hasSingleInstanceLock) {
  await app.whenReady();
  configureRemoteSession();
  registerIpc();
  mainWindow = createMainWindow();
  void restoreVerifiedStatus();
}
