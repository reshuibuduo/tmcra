package com.tmcra.memory.mobile.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.Arrays;
import java.util.List;

public final class SpeakerDiarizationTimelineTest {
    @Test
    public void overlappingTracksBecomeOneExclusiveOverlapSpan() {
        List<SpeakerDiarizationEngine.Turn> result = SpeakerDiarizationEngine.exclusiveTimeline(
                Arrays.asList(
                        new SpeakerDiarizationEngine.Turn(0f, 2f, 0, true),
                        new SpeakerDiarizationEngine.Turn(1f, 3f, 1, true)));

        assertEquals(3, result.size());
        assertTurn(result.get(0), 0f, 1f, 0, false);
        assertTurn(result.get(1), 1f, 2f, -1, true);
        assertTurn(result.get(2), 2f, 3f, 1, false);
    }

    @Test
    public void adjacentAtomicSpansForOneSpeakerAreMerged() {
        List<SpeakerDiarizationEngine.Turn> result = SpeakerDiarizationEngine.exclusiveTimeline(
                Arrays.asList(
                        new SpeakerDiarizationEngine.Turn(0f, 1f, 3, false),
                        new SpeakerDiarizationEngine.Turn(1f, 2f, 3, false)));

        assertEquals(1, result.size());
        assertTurn(result.get(0), 0f, 2f, 3, false);
    }

    private static void assertTurn(
            SpeakerDiarizationEngine.Turn turn,
            float start,
            float end,
            int speaker,
            boolean overlap) {
        assertEquals(start, turn.startSeconds, 0.001f);
        assertEquals(end, turn.endSeconds, 0.001f);
        assertEquals(speaker, turn.localSpeaker);
        if (overlap) assertTrue(turn.overlap);
        else assertFalse(turn.overlap);
    }
}
