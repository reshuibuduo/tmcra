package com.tmcra.memory.mobile.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class SpeakerMatchPolicyTest {
    @Test
    public void knownSpeakerNeedsThresholdAndSeparationFromRunnerUp() {
        assertTrue(SpeakerMatchPolicy.accepts(0.74f, 0.66f, "known"));
        assertFalse(SpeakerMatchPolicy.accepts(0.67f, 0.10f, "known"));
        assertFalse(SpeakerMatchPolicy.accepts(0.74f, 0.72f, "known"));
    }

    @Test
    public void unknownClusterUsesMoreConservativeThreshold() {
        assertFalse(SpeakerMatchPolicy.accepts(0.70f, -1f, "unknown"));
        assertTrue(SpeakerMatchPolicy.accepts(0.76f, 0.68f, "unknown"));
    }

    @Test
    public void cosineRejectsInvalidVectorsAndMatchesDirection() {
        assertEquals(1f, SpeakerMatchPolicy.cosine(new float[]{1, 2}, new float[]{2, 4}), 0.0001f);
        assertEquals(0f, SpeakerMatchPolicy.cosine(new float[]{1, 0}, new float[]{0, 1}), 0.0001f);
        assertEquals(-1f, SpeakerMatchPolicy.cosine(new float[]{1}, new float[]{1, 2}), 0.0001f);
    }
}
