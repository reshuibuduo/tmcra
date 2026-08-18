package com.tmcra.memory.mobile.reminder;

import java.util.regex.Pattern;

/**
 * Conservative on-device decision gate for proactive recall delivery.
 *
 * This class consumes final text and the server recall receipt only. It never
 * sees audio or voiceprint vectors. A future tiny text model can implement the
 * same Decision contract without changing the capture, recall, or TTS chain.
 */
public final class LocalRecallDecisionPolicy {
    private static final Pattern EXPLICIT_RECALL_INTENT = Pattern.compile(
            "忘了|忘记|想不起来|还记得|之前.{0,12}(?:说|做|定|放|约)|" +
                    "刚才.{0,12}(?:什么|哪|谁)|上次|什么时候|在哪里|放哪|谁说的|" +
                    "forgot|can't remember|cannot remember|do you remember|what did|where did|when did",
            Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);

    private LocalRecallDecisionPolicy() {}

    public static Decision evaluate(String finalTranscript, String recallStatus, String recallSummary) {
        String transcript = finalTranscript == null ? "" : finalTranscript.trim();
        String summary = recallSummary == null ? "" : recallSummary.trim();
        if (!"matched".equals(recallStatus) || summary.isEmpty()) {
            return Decision.suppressed("no_relevant_evidence");
        }
        if (!EXPLICIT_RECALL_INTENT.matcher(transcript).find()) {
            return Decision.suppressed("no_explicit_recall_intent");
        }
        String compact = summary.length() <= 260 ? summary : summary.substring(0, 260) + "…";
        return new Decision(true, "explicit_recall_intent", "我找到了相关记录：" + compact, true);
    }

    public static final class Decision {
        public final boolean deliver;
        public final String reason;
        public final String message;
        public final boolean speakEligible;

        Decision(boolean deliver, String reason, String message, boolean speakEligible) {
            this.deliver = deliver;
            this.reason = reason;
            this.message = message;
            this.speakEligible = speakEligible;
        }

        static Decision suppressed(String reason) {
            return new Decision(false, reason, "", false);
        }
    }
}
