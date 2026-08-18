package com.tmcra.memory.mobile.audio;

import java.util.ArrayDeque;
import java.util.Arrays;

/** Adaptive, deterministic VAD used before any ASR work. */
public final class VadSegmenter {
    public interface Listener {
        void onLevel(float level, boolean speechActive);
        void onSegment(short[] pcm, int sampleRate);
    }

    private static final int SAMPLE_RATE = 16_000;
    private static final int FRAME_SAMPLES = 320; // 20 ms
    private static final int PRE_ROLL_FRAMES = 13;
    private static final int START_FRAMES = 3;
    private static final int END_SILENCE_FRAMES = 35;
    private static final int MAX_SEGMENT_FRAMES = 1_500;
    private static final int MIN_SEGMENT_SAMPLES = 6_400;

    private final Listener listener;
    private final ArrayDeque<short[]> preRoll = new ArrayDeque<>();
    private final ShortAccumulator active = new ShortAccumulator(SAMPLE_RATE * 8);
    private final short[] pendingFrame = new short[FRAME_SAMPLES];
    private double noiseFloor = 260.0;
    private int pendingSamples;
    private int candidateFrames;
    private int silenceFrames;
    private int activeFrames;
    private boolean speaking;

    public VadSegmenter(Listener listener) {
        this.listener = listener;
    }

    public void accept(short[] samples, int count) {
        if (samples == null || count < 0 || count > samples.length) {
            throw new IllegalArgumentException("Invalid PCM buffer");
        }
        int offset = 0;
        while (offset < count) {
            int copied = Math.min(FRAME_SAMPLES - pendingSamples, count - offset);
            System.arraycopy(samples, offset, pendingFrame, pendingSamples, copied);
            pendingSamples += copied;
            offset += copied;
            if (pendingSamples == FRAME_SAMPLES) {
                acceptFrame(Arrays.copyOf(pendingFrame, pendingFrame.length));
                pendingSamples = 0;
            }
        }
    }

    public void finish() {
        if (speaking && active.size() >= MIN_SEGMENT_SAMPLES) emit();
        reset();
        pendingSamples = 0;
    }

    private void acceptFrame(short[] frame) {
        double rms = rms(frame);
        double threshold = Math.max(560.0, noiseFloor * 2.9);
        boolean voiced = rms >= threshold;
        listener.onLevel((float) Math.min(1.0, rms / 6_000.0), speaking || voiced);

        if (!speaking) {
            noiseFloor = Math.max(80.0, Math.min(1_500.0, noiseFloor * 0.985 + rms * 0.015));
            addPreRoll(frame);
            candidateFrames = voiced ? candidateFrames + 1 : 0;
            if (candidateFrames >= START_FRAMES) {
                speaking = true;
                silenceFrames = 0;
                activeFrames = 0;
                for (short[] buffered : preRoll) active.write(buffered);
                preRoll.clear();
            }
            return;
        }

        active.write(frame);
        activeFrames += 1;
        silenceFrames = voiced ? 0 : silenceFrames + 1;
        if (silenceFrames >= END_SILENCE_FRAMES || activeFrames >= MAX_SEGMENT_FRAMES) {
            if (active.size() >= MIN_SEGMENT_SAMPLES) emit();
            reset();
        }
    }

    private void emit() {
        listener.onSegment(active.toArray(), SAMPLE_RATE);
    }

    private void reset() {
        speaking = false;
        candidateFrames = 0;
        silenceFrames = 0;
        activeFrames = 0;
        active.clear();
        preRoll.clear();
    }

    private void addPreRoll(short[] frame) {
        preRoll.addLast(frame);
        while (preRoll.size() > PRE_ROLL_FRAMES) preRoll.removeFirst();
    }

    private static double rms(short[] frame) {
        double sum = 0;
        for (short value : frame) {
            double sample = value;
            sum += sample * sample;
        }
        return Math.sqrt(sum / frame.length);
    }

    private static final class ShortAccumulator {
        private short[] values;
        private int size;

        ShortAccumulator(int initialCapacity) {
            values = new short[initialCapacity];
        }

        void write(short[] input) {
            ensure(size + input.length);
            System.arraycopy(input, 0, values, size, input.length);
            size += input.length;
        }

        int size() {
            return size;
        }

        short[] toArray() {
            return Arrays.copyOf(values, size);
        }

        void clear() {
            size = 0;
        }

        private void ensure(int required) {
            if (required <= values.length) return;
            int next = Math.max(required, values.length + values.length / 2);
            values = Arrays.copyOf(values, next);
        }
    }
}
