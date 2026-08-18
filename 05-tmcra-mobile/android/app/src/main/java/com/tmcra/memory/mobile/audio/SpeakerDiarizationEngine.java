package com.tmcra.memory.mobile.audio;

import android.content.Context;

import com.k2fsa.sherpa.onnx.FastClusteringConfig;
import com.k2fsa.sherpa.onnx.OfflineSpeakerDiarization;
import com.k2fsa.sherpa.onnx.OfflineSpeakerDiarizationConfig;
import com.k2fsa.sherpa.onnx.OfflineSpeakerDiarizationSegment;
import com.k2fsa.sherpa.onnx.OfflineSpeakerSegmentationModelConfig;
import com.k2fsa.sherpa.onnx.OfflineSpeakerSegmentationPyannoteModelConfig;
import com.k2fsa.sherpa.onnx.SpeakerEmbeddingExtractorConfig;

import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Local speaker diarization: detects speaker turns before persistent voiceprint matching.
 *
 * The diarizer's integer speaker labels are scoped to one audio window. They must never be
 * persisted as a person's identity; SpeakerIdentityEngine performs that separate mapping.
 */
public final class SpeakerDiarizationEngine implements AutoCloseable {
    public static final String MODEL_ID = "pyannote/segmentation-3.0-int8@sherpa-onnx-v1.13.4";
    public static final String MODEL_NAME = "tmcra-pyannote-segmentation-3.0.int8.onnx";
    // Calibrated on-device against the official sherpa-onnx 4-speaker Chinese
    // fixture and two 2-speaker English fixtures. 0.85/0.90 merged 4 speakers
    // into 3 on the target phone, while 0.70 recovered the known count in all
    // three fixtures (including the overlapping-conversation sample).
    public static final float UNKNOWN_SPEAKER_THRESHOLD = 0.70f;

    private static final float MIN_DURATION_ON_SECONDS = 0.20f;
    private static final float MIN_DURATION_OFF_SECONDS = 0.50f;
    private static final float MERGE_GAP_SECONDS = 0.12f;

    private final Context context;
    private OfflineSpeakerDiarization diarizer;
    private String unavailableReason;

    public SpeakerDiarizationEngine(Context context) {
        this(context, UNKNOWN_SPEAKER_THRESHOLD, -1);
    }

    /** Visible for the debug calibration harness; production uses unknown speaker count. */
    public SpeakerDiarizationEngine(Context context, float clusteringThreshold, int numClusters) {
        this.context = context.getApplicationContext();
        if (!assetExists(MODEL_NAME) || !assetExists(SpeakerIdentityEngine.MODEL_NAME)) {
            unavailableReason = "diarization_asset_missing";
            return;
        }
        try {
            OfflineSpeakerSegmentationModelConfig segmentation =
                    new OfflineSpeakerSegmentationModelConfig(
                            new OfflineSpeakerSegmentationPyannoteModelConfig(MODEL_NAME),
                            2,
                            false,
                            "cpu");
            SpeakerEmbeddingExtractorConfig embedding = new SpeakerEmbeddingExtractorConfig(
                    SpeakerIdentityEngine.MODEL_NAME,
                    2,
                    false,
                    "cpu");
            FastClusteringConfig clustering = new FastClusteringConfig(
                    numClusters,
                    Math.max(0.01f, Math.min(0.99f, clusteringThreshold)));
            diarizer = new OfflineSpeakerDiarization(
                    context.getAssets(),
                    new OfflineSpeakerDiarizationConfig(
                            segmentation,
                            embedding,
                            clustering,
                            MIN_DURATION_ON_SECONDS,
                            MIN_DURATION_OFF_SECONDS));
        } catch (Throwable error) {
            diarizer = null;
            unavailableReason = "diarization_initialization_" + error.getClass().getSimpleName();
        }
    }

    public boolean isAvailable() {
        return diarizer != null;
    }

    public String unavailableReason() {
        return unavailableReason == null ? "diarization_unavailable" : unavailableReason;
    }

    public synchronized Result diarize(short[] pcm, int sampleRate) {
        if (diarizer == null) return Result.unavailable(unavailableReason());
        if (pcm == null || pcm.length == 0 || sampleRate <= 0) {
            return Result.unavailable("diarization_audio_empty");
        }
        try {
            int targetRate = diarizer.sampleRate();
            short[] normalized = sampleRate == targetRate
                    ? pcm
                    : linearResample(pcm, sampleRate, targetRate);
            float[] samples = new float[normalized.length];
            for (int index = 0; index < normalized.length; index++) {
                samples[index] = normalized[index] / 32768.0f;
            }
            OfflineSpeakerDiarizationSegment[] raw = diarizer.process(samples);
            ArrayList<Turn> turns = new ArrayList<>();
            if (raw != null) {
                float duration = normalized.length / (float) targetRate;
                for (OfflineSpeakerDiarizationSegment segment : raw) {
                    float start = clamp(segment.getStart(), 0, duration);
                    float end = clamp(segment.getEnd(), start, duration);
                    if (end - start < 0.05f) continue;
                    turns.add(new Turn(start, end, segment.getSpeaker(), false));
                }
            }
            Collections.sort(turns, Comparator
                    .comparingDouble((Turn item) -> item.startSeconds)
                    .thenComparingDouble(item -> item.endSeconds)
                    .thenComparingInt(item -> item.localSpeaker));
            turns = mergeAdjacent(turns);
            turns = markOverlaps(turns);
            Set<Integer> speakers = new HashSet<>();
            for (Turn turn : turns) speakers.add(turn.localSpeaker);
            return new Result(true, null, targetRate, turns, speakers.size());
        } catch (Throwable error) {
            return Result.unavailable("diarization_process_" + error.getClass().getSimpleName());
        }
    }

    private static ArrayList<Turn> mergeAdjacent(List<Turn> source) {
        ArrayList<Turn> result = new ArrayList<>();
        for (Turn current : source) {
            if (!result.isEmpty()) {
                Turn previous = result.get(result.size() - 1);
                float gap = current.startSeconds - previous.endSeconds;
                if (previous.localSpeaker == current.localSpeaker
                        && gap >= 0
                        && gap <= MERGE_GAP_SECONDS) {
                    result.set(result.size() - 1, new Turn(
                            previous.startSeconds,
                            Math.max(previous.endSeconds, current.endSeconds),
                            previous.localSpeaker,
                            false));
                    continue;
                }
            }
            result.add(current);
        }
        return result;
    }

    private static ArrayList<Turn> markOverlaps(List<Turn> source) {
        ArrayList<Turn> result = new ArrayList<>(source.size());
        for (int index = 0; index < source.size(); index++) {
            Turn current = source.get(index);
            boolean overlap = false;
            for (int otherIndex = 0; otherIndex < source.size(); otherIndex++) {
                if (index == otherIndex) continue;
                Turn other = source.get(otherIndex);
                if (current.localSpeaker == other.localSpeaker) continue;
                if (Math.min(current.endSeconds, other.endSeconds)
                        - Math.max(current.startSeconds, other.startSeconds) >= 0.05f) {
                    overlap = true;
                    break;
                }
            }
            result.add(new Turn(
                    current.startSeconds,
                    current.endSeconds,
                    current.localSpeaker,
                    overlap));
        }
        return result;
    }

    /**
     * Converts overlapping diarizer tracks into an exclusive timeline suitable for persistence.
     * A span with multiple active speakers is emitted once with localSpeaker=-1. This prevents
     * the same mixed PCM from being transcribed or enrolled into multiple voiceprints.
     */
    public static List<Turn> exclusiveTimeline(List<Turn> source) {
        if (source == null || source.isEmpty()) return Collections.emptyList();
        ArrayList<Float> boundaries = new ArrayList<>();
        for (Turn turn : source) {
            if (turn == null || turn.endSeconds - turn.startSeconds < 0.05f) continue;
            boundaries.add(turn.startSeconds);
            boundaries.add(turn.endSeconds);
        }
        Collections.sort(boundaries);
        ArrayList<Float> unique = new ArrayList<>();
        for (Float boundary : boundaries) {
            if (unique.isEmpty() || Math.abs(boundary - unique.get(unique.size() - 1)) >= 0.001f) {
                unique.add(boundary);
            }
        }

        ArrayList<Turn> atomic = new ArrayList<>();
        for (int index = 0; index + 1 < unique.size(); index++) {
            float start = unique.get(index);
            float end = unique.get(index + 1);
            if (end - start < 0.05f) continue;
            float midpoint = (start + end) / 2f;
            Set<Integer> active = new HashSet<>();
            for (Turn turn : source) {
                if (turn.startSeconds <= midpoint && turn.endSeconds > midpoint) {
                    active.add(turn.localSpeaker);
                }
            }
            if (active.isEmpty()) continue;
            boolean overlap = active.size() > 1;
            int speaker = overlap ? -1 : active.iterator().next();
            atomic.add(new Turn(start, end, speaker, overlap));
        }

        ArrayList<Turn> result = new ArrayList<>();
        for (Turn current : atomic) {
            if (!result.isEmpty()) {
                Turn previous = result.get(result.size() - 1);
                if (previous.localSpeaker == current.localSpeaker
                        && previous.overlap == current.overlap
                        && current.startSeconds - previous.endSeconds <= 0.02f) {
                    result.set(result.size() - 1, new Turn(
                            previous.startSeconds,
                            current.endSeconds,
                            previous.localSpeaker,
                            previous.overlap));
                    continue;
                }
            }
            result.add(current);
        }
        return Collections.unmodifiableList(result);
    }

    private static short[] linearResample(short[] input, int sourceRate, int targetRate) {
        if (sourceRate == targetRate) return input;
        int outputLength = Math.max(1, (int) Math.round(input.length * (double) targetRate / sourceRate));
        short[] output = new short[outputLength];
        double ratio = sourceRate / (double) targetRate;
        for (int index = 0; index < outputLength; index++) {
            double position = index * ratio;
            int left = Math.min(input.length - 1, (int) position);
            int right = Math.min(input.length - 1, left + 1);
            double weight = position - left;
            output[index] = (short) Math.round(input[left] * (1 - weight) + input[right] * weight);
        }
        return output;
    }

    private boolean assetExists(String name) {
        try (InputStream ignored = context.getAssets().open(name)) {
            return true;
        } catch (IOException error) {
            return false;
        }
    }

    private static float clamp(float value, float minimum, float maximum) {
        return Math.max(minimum, Math.min(maximum, value));
    }

    @Override
    public synchronized void close() {
        if (diarizer != null) diarizer.release();
        diarizer = null;
    }

    public static final class Turn {
        public final float startSeconds;
        public final float endSeconds;
        public final int localSpeaker;
        public final boolean overlap;

        Turn(float startSeconds, float endSeconds, int localSpeaker, boolean overlap) {
            this.startSeconds = startSeconds;
            this.endSeconds = endSeconds;
            this.localSpeaker = localSpeaker;
            this.overlap = overlap;
        }

        public float durationSeconds() {
            return Math.max(0, endSeconds - startSeconds);
        }
    }

    public static final class Result {
        public final boolean available;
        public final String reason;
        public final int sampleRate;
        public final List<Turn> turns;
        public final int speakerCount;

        Result(
                boolean available,
                String reason,
                int sampleRate,
                List<Turn> turns,
                int speakerCount) {
            this.available = available;
            this.reason = reason;
            this.sampleRate = sampleRate;
            this.turns = Collections.unmodifiableList(new ArrayList<>(turns));
            this.speakerCount = speakerCount;
        }

        static Result unavailable(String reason) {
            return new Result(false, reason, 0, Collections.emptyList(), 0);
        }
    }
}
