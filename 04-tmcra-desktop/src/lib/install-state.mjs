const STEP_ORDER = ["environment", "plugin", "authorization", "hooks"];
const STEP_STATUSES = new Set(["pending", "running", "completed", "failed", "action_required"]);
const PHASES = new Set([
  "idle",
  "checking",
  "installing",
  "awaiting_authorization",
  "verifying_remote",
  "connected_pending_hooks",
  "ready",
  "cancelled",
  "error",
]);

export function createInstallState() {
  return {
    phase: "idle",
    connected: false,
    busy: false,
    hookAcknowledged: false,
    error: null,
    authorization: null,
    steps: STEP_ORDER.map((id) => ({ id, status: "pending" })),
  };
}

function updateStep(state, stepId, status) {
  if (!STEP_ORDER.includes(stepId) || !STEP_STATUSES.has(status)) return state;
  return {
    ...state,
    steps: state.steps.map((step) => (step.id === stepId ? { ...step, status } : step)),
  };
}

function markBeforeCompleted(state, stepId) {
  const boundary = STEP_ORDER.indexOf(stepId);
  return {
    ...state,
    steps: state.steps.map((step, index) =>
      index < boundary && step.status !== "failed" ? { ...step, status: "completed" } : step,
    ),
  };
}

function activeStep(state) {
  return state.steps.find((step) => step.status === "running" || step.status === "action_required");
}

export function reduceInstallState(current, event) {
  const state = normalizePublicState(current);
  if (!event || typeof event !== "object") return state;

  switch (event.type) {
    case "reset":
      return createInstallState();

    case "start":
      return updateStep(
        {
          ...createInstallState(),
          phase: "checking",
          busy: true,
        },
        "environment",
        "running",
      );

    case "progress": {
      if (!STEP_ORDER.includes(event.step) || !STEP_STATUSES.has(event.status)) return state;
      const awaitingAuthorization =
        event.step === "authorization" &&
        event.status === "running" &&
        state.authorization !== null;
      let next = markBeforeCompleted(state, event.step);
      next = updateStep(next, event.step, awaitingAuthorization ? "action_required" : event.status);
      return {
        ...next,
        phase:
          (event.step === "authorization" && event.status === "action_required") || awaitingAuthorization
            ? "awaiting_authorization"
            : event.step === "environment"
              ? "checking"
              : "installing",
        busy: true,
        error: null,
      };
    }

    case "authorization_required": {
      let next = markBeforeCompleted(state, "authorization");
      next = updateStep(next, "authorization", "action_required");
      return {
        ...next,
        phase: "awaiting_authorization",
        busy: true,
        authorization: {
          userCode: String(event.userCode || ""),
          verificationUrl: String(event.verificationUrl || ""),
          expiresAt: event.expiresAt || null,
        },
      };
    }

    case "remote_verification": {
      let next = markBeforeCompleted(state, "authorization");
      next = updateStep(next, "authorization", "running");
      return {
        ...next,
        phase: "verifying_remote",
        connected: false,
        busy: true,
        error: null,
        authorization: null,
      };
    }

    case "complete": {
      let next = state;
      for (const stepId of ["environment", "plugin", "authorization"]) {
        next = updateStep(next, stepId, "completed");
      }
      next = updateStep(next, "hooks", state.hookAcknowledged ? "completed" : "action_required");
      return {
        ...next,
        phase: state.hookAcknowledged ? "ready" : "connected_pending_hooks",
        connected: true,
        busy: false,
        error: null,
        authorization: null,
      };
    }

    case "acknowledge_hooks":
      if (!state.connected) return state;
      return {
        ...updateStep(state, "hooks", "completed"),
        hookAcknowledged: true,
        phase: "ready",
      };

    case "cancel":
      return {
        ...state,
        phase: "cancelled",
        busy: false,
        connected: false,
        authorization: null,
        error: null,
        steps: state.steps.map((step) =>
          step.status === "running" || step.status === "action_required"
            ? { ...step, status: "pending" }
            : step,
        ),
      };

    case "error": {
      const step = activeStep(state);
      const failed = step ? updateStep(state, step.id, "failed") : state;
      return {
        ...failed,
        phase: "error",
        busy: false,
        connected: false,
        authorization: null,
        error: {
          code: safeCode(event.code),
          message: safeMessage(event.message),
        },
      };
    }

    default:
      return state;
  }
}

function safeCode(value) {
  const code = String(value || "setup_failed").replace(/[^A-Za-z0-9_.-]/gu, "_");
  return code.slice(0, 80) || "setup_failed";
}

function safeMessage(value) {
  return String(value || "")
    .replace(/[\u0000-\u001f\u007f]/gu, " ")
    .trim()
    .slice(0, 400);
}

export function normalizePublicState(value) {
  const base = createInstallState();
  if (!value || typeof value !== "object") return base;

  const phase = PHASES.has(value.phase) ? value.phase : base.phase;
  const stepsById = new Map(
    Array.isArray(value.steps)
      ? value.steps
          .filter((step) => STEP_ORDER.includes(step?.id) && STEP_STATUSES.has(step?.status))
          .map((step) => [step.id, { id: step.id, status: step.status }])
      : [],
  );
  const steps = STEP_ORDER.map((id) => stepsById.get(id) ?? { id, status: "pending" });

  let authorization = null;
  if (value.authorization && typeof value.authorization === "object") {
    const userCode = String(value.authorization.userCode || "");
    const verificationUrl = String(value.authorization.verificationUrl || "");
    if (/^[A-Za-z0-9-]{4,32}$/u.test(userCode) && verificationUrl.length <= 2048) {
      authorization = {
        userCode,
        verificationUrl,
        expiresAt:
          typeof value.authorization.expiresAt === "string"
            ? value.authorization.expiresAt.slice(0, 64)
            : null,
      };
    }
  }

  const error = value.error && typeof value.error === "object"
    ? { code: safeCode(value.error.code), message: safeMessage(value.error.message) }
    : null;

  return {
    phase,
    connected: value.connected === true,
    busy: value.busy === true,
    hookAcknowledged: value.hookAcknowledged === true,
    error,
    authorization,
    steps,
  };
}
