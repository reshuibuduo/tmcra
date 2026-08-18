package com.tmcra.memory.mobile.audio;

/** Pure matching policy kept separate from the native embedding runtime for deterministic tests. */
final class SpeakerMatchPolicy {
    static final float KNOWN_MATCH_THRESHOLD = 0.68f;
    static final float UNKNOWN_CLUSTER_THRESHOLD = 0.72f;
    static final float MIN_MATCH_MARGIN = 0.04f;
    static final float ADAPT_THRESHOLD = 0.82f;

    private SpeakerMatchPolicy() {}

    static boolean accepts(float bestScore, float secondScore, String relation) {
        float threshold = "unknown".equals(relation)
                ? UNKNOWN_CLUSTER_THRESHOLD
                : KNOWN_MATCH_THRESHOLD;
        boolean marginAccepted = secondScore < 0 || bestScore - secondScore >= MIN_MATCH_MARGIN;
        return Float.isFinite(bestScore) && bestScore >= threshold && marginAccepted;
    }

    static float cosine(float[] left, float[] right) {
        if (left == null || right == null || left.length == 0 || left.length != right.length) return -1;
        double dot = 0;
        double leftNorm = 0;
        double rightNorm = 0;
        for (int index = 0; index < left.length; index++) {
            dot += left[index] * right[index];
            leftNorm += left[index] * left[index];
            rightNorm += right[index] * right[index];
        }
        if (leftNorm <= 0 || rightNorm <= 0) return -1;
        return (float) (dot / Math.sqrt(leftNorm * rightNorm));
    }
}
