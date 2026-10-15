import { classifyPromptOrigin, permitsPromptCapture } from "../scripts/prompt_origin.mjs";
import { emit, failOpen, readHookInput, recallForContext, rememberPrompt } from "./hook_common.mjs";

const input = await readHookInput();
await failOpen("user_prompt_submit", async () => {
  const origin = classifyPromptOrigin(input);
  if (!permitsPromptCapture(origin)) {
    process.stderr.write(`${JSON.stringify({event:"tmcra_capture",status:"skipped",reason:"automatic_origin_excluded",origin_kind:origin.origin_kind})}\n`);
    emit({ continue: true }); return;
  }
  if (origin.origin_kind === "unknown") process.stderr.write(`${JSON.stringify({event:"tmcra_capture",status:"source_only",reason:"origin_unverified_source_only",origin_kind:"unknown"})}\n`);
  const remembered = await rememberPrompt(input);
  const query = String(input.prompt || "").trim();
  if (!query) {
    emit({ continue: true });
    return;
  }
  const result = await recallForContext(
    remembered?.turnId ? { ...input, tmcra_turn_id: remembered.turnId } : input,
    query,
  );
  if (!result.context) {
    emit({ continue: true });
    return;
  }
  emit({
    continue: true,
    hookSpecificOutput: {
      hookEventName: "UserPromptSubmit",
      additionalContext: result.context,
    },
  });
});
