package com.tmcra.memory.mobile.audio;

import org.junit.Test;

import java.util.ArrayList;
import java.util.List;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

public class VadSegmenterTest {
    @Test
    public void silenceDoesNotCreateMemorySegments() {
        List<short[]> segments = new ArrayList<>();
        VadSegmenter vad = new VadSegmenter(new Listener(segments));
        short[] silence = new short[320];
        for (int index = 0; index < 150; index++) vad.accept(silence, silence.length);
        vad.finish();
        assertEquals(0, segments.size());
    }

    @Test
    public void speechWithTrailingSilenceProducesOneBoundedSegment() {
        List<short[]> segments = new ArrayList<>();
        VadSegmenter vad = new VadSegmenter(new Listener(segments));
        short[] silence = new short[320];
        short[] voice = new short[320];
        for (int index = 0; index < voice.length; index++) {
            voice[index] = (short) (index % 2 == 0 ? 4_200 : -4_200);
        }
        for (int index = 0; index < 20; index++) vad.accept(silence, silence.length);
        for (int index = 0; index < 55; index++) vad.accept(voice, voice.length);
        for (int index = 0; index < 40; index++) vad.accept(silence, silence.length);
        assertEquals(1, segments.size());
        assertTrue(segments.get(0).length >= 16_000);
        assertTrue(segments.get(0).length < 40_000);
    }

    @Test
    public void irregularRecorderReadsDoNotDropPcmFrames() {
        List<short[]> segments = new ArrayList<>();
        VadSegmenter vad = new VadSegmenter(new Listener(segments));
        short[] voice = new short[137];
        for (int index = 0; index < voice.length; index++) {
            voice[index] = (short) (index % 2 == 0 ? 4_500 : -4_500);
        }
        short[] silence = new short[211];
        for (int index = 0; index < 160; index++) vad.accept(voice, voice.length);
        for (int index = 0; index < 70; index++) vad.accept(silence, silence.length);
        vad.finish();
        assertEquals(1, segments.size());
        assertTrue(segments.get(0).length >= 16_000);
    }

    private static final class Listener implements VadSegmenter.Listener {
        private final List<short[]> segments;

        Listener(List<short[]> segments) {
            this.segments = segments;
        }

        @Override public void onLevel(float level, boolean speechActive) {}
        @Override public void onSegment(short[] pcm, int sampleRate) { segments.add(pcm); }
    }
}
