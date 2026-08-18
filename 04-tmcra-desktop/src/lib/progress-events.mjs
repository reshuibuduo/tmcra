import { validateVerificationUrl } from "./security.mjs";

const MAX_LINE_LENGTH = 16_384;
const MAX_MESSAGE_LENGTH = 400;
const EVENT_NAMES = new Map([
  ["tmcra.install.progress", "progress"],
  ["progress", "progress"],
  ["tmcra.authorization.required", "authorization_required"],
  ["authorization_required", "authorization_required"],
  ["tmcra.install.complete", "complete"],
  ["complete", "complete"],
  ["success", "complete"],
  ["tmcra.install.error", "error"],
  ["error", "error"],
]);
const STEP_NAMES = new Map([
  ["detect_codex", "environment"],
  ["codex_detected", "environment"],
  ["environment", "environment"],
  ["extract", "plugin"],
  ["install_plugin", "plugin"],
  ["plugin", "plugin"],
  ["authorize", "authorization"],
  ["authorization", "authorization"],
  ["verify_config", "authorization"],
]);
const STATUSES = new Set(["pending", "running", "completed", "failed", "action_required"]);

function safeText(value, fallback = "") {
  if (typeof value !== "string") return fallback;
  return value.replace(/[\u0000-\u001f\u007f]/gu, " ").trim().slice(0, MAX_MESSAGE_LENGTH);
}

function safeCode(value, fallback) {
  const normalized = safeText(value, fallback).replace(/[^A-Za-z0-9_.-]/gu, "_");
  return normalized.slice(0, 80) || fallback;
}

export function parseInstallerEvent(line, { authorizationBaseUrl } = {}) {
  if (typeof line !== "string" || line.length === 0 || line.length > MAX_LINE_LENGTH) return null;

  let payload;
  try {
    payload = JSON.parse(line);
  } catch {
    return null;
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;

  const rawName = safeText(payload.event ?? payload.type, "").toLowerCase();
  const event = EVENT_NAMES.get(rawName);
  if (!event) return null;

  if (event === "progress") {
    const step = STEP_NAMES.get(safeText(payload.step ?? payload.stage, "").toLowerCase());
    const status = safeText(payload.status, "").toLowerCase();
    if (!step || !STATUSES.has(status)) return null;
    return {
      type: "progress",
      step,
      status,
      message: safeText(payload.message),
    };
  }

  if (event === "authorization_required") {
    if (!authorizationBaseUrl) return null;
    const userCode = safeText(payload.userCode);
    if (!/^[A-Za-z0-9-]{4,32}$/u.test(userCode)) return null;

    let verificationUrl;
    try {
      verificationUrl = validateVerificationUrl(payload.verificationUrl, authorizationBaseUrl);
    } catch {
      return null;
    }

    const expiresAt = safeText(payload.expiresAt);
    if (expiresAt && !Number.isFinite(Date.parse(expiresAt))) return null;
    return {
      type: "authorization_required",
      userCode,
      verificationUrl,
      expiresAt: expiresAt || null,
    };
  }

  if (event === "complete") {
    return { type: "complete" };
  }

  return {
    type: "error",
    code: safeCode(payload.code, "installer_failed"),
    message: safeText(payload.message),
  };
}

export class NdjsonLineBuffer {
  #buffer = "";

  push(chunk) {
    this.#buffer += String(chunk ?? "");
    if (this.#buffer.length > MAX_LINE_LENGTH * 2) {
      this.#buffer = "";
      return [];
    }

    const parts = this.#buffer.split(/\r?\n/u);
    this.#buffer = parts.pop() ?? "";
    return parts.filter((line) => line.length > 0 && line.length <= MAX_LINE_LENGTH);
  }

  flush() {
    const line = this.#buffer;
    this.#buffer = "";
    return line.length > 0 && line.length <= MAX_LINE_LENGTH ? [line] : [];
  }
}
