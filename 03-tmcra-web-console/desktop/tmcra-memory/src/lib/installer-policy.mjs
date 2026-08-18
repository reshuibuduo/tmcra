export function stateEventBeforeInstallerExit(event) {
  if (!event || typeof event !== "object") return null;
  if (event.type === "complete") return null;
  if (
    event.type === "progress" &&
    event.step === "authorization" &&
    event.status === "completed"
  ) {
    return { type: "remote_verification" };
  }
  return event;
}

export function stateEventForInstallerExit(exitCode) {
  if (exitCode === 0) return { type: "complete" };
  return {
    type: "error",
    code: "installer_failed",
    message: `The TMCRA installer stopped before completion (exit ${exitCode ?? "unknown"}).`,
  };
}
