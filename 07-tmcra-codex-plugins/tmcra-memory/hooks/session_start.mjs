import {
  emit,
  failOpen,
  readHookInput,
  recordSessionStart,
  resumeTaskContinuity,
  workflowForSessionStart,
} from "./hook_common.mjs";

const input = await readHookInput();
await failOpen("session_start", async () => {
  await recordSessionStart(input);
  const source = String(input.source || "startup");
  const workflow = workflowForSessionStart(input);
  const continuity = ["compact", "resume"].includes(source) ? await resumeTaskContinuity(input) : "";
  const context = [continuity, await workflow].filter(Boolean).join("\n\n");
  if (!context) {
    emit({ continue: true });
    return;
  }
  emit({
    continue: true,
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext: context,
    },
  });
});
