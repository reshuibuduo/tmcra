package com.tmcra.memory.mobile.audio;

import android.content.Context;

import com.k2fsa.sherpa.onnx.OnlineStream;
import com.k2fsa.sherpa.onnx.SpeakerEmbeddingExtractor;
import com.k2fsa.sherpa.onnx.SpeakerEmbeddingExtractorConfig;
import com.tmcra.memory.mobile.data.AudioMemoryStore;
import com.tmcra.memory.mobile.security.CryptoBox;

/**
 * On-device speaker embedding and conservative local matching.
 *
 * Embeddings are encrypted with Android Keystore before SQLite persistence.
 * Neither embeddings nor PCM leave this class through the TMCRA memory API.
 *
 * Embedding extraction stays here; the match-or-create lifecycle lives in
 * {@link VoiceprintMatcher} so it can be validated deterministically without
 * the native model runtime.
 */
public final class SpeakerIdentityEngine implements AutoCloseable {
    public static final String MODEL_ID =
            "iic/speech_eres2netv2_sv_zh-cn_16k-common@v1.0.1";
    public static final String MODEL_NAME =
            "3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx";

    private static final int MIN_SPEAKER_SAMPLES_16K = 24_000;
    private static final String VOICEPRINT_KEY_ALIAS = "tmcra_mobile_voiceprints_v1";

    private final AudioMemoryStore store;
    private final CryptoBox crypto;
    private final VoiceprintMatcher matcher;
    private SpeakerEmbeddingExtractor extractor;
    private String unavailableReason;

    public SpeakerIdentityEngine(Context context, AudioMemoryStore store) {
        this.store = store;
        this.crypto = new CryptoBox(VOICEPRINT_KEY_ALIAS);
        this.matcher = new VoiceprintMatcher(store, crypto);
        try {
            extractor = new SpeakerEmbeddingExtractor(
                    context.getAssets(),
                    new SpeakerEmbeddingExtractorConfig(MODEL_NAME, 2, false, "cpu"));
        } catch (Throwable error) {
            extractor = null;
            unavailableReason = error.getClass().getSimpleName();
        }
    }

    public boolean isAvailable() {
        return extractor != null;
    }

    public String unavailableReason() {
        return unavailableReason == null ? "speaker_model_unavailable" : unavailableReason;
    }

    public synchronized Result identify(short[] pcm, int sampleRate) {
        if (extractor == null) return Result.unresolved("model_unavailable");
        if (pcm == null || sampleRate <= 0 || pcm.length * 16_000L / sampleRate < MIN_SPEAKER_SAMPLES_16K) {
            return Result.unresolved("segment_too_short");
        }
        float[] embedding;
        try {
            embedding = extract(pcm, sampleRate);
        } catch (Throwable error) {
            return Result.unresolved("embedding_failed");
        }
        if (embedding == null) return Result.unresolved("segment_too_short");
        return matcher.identify(embedding);
    }

    private float[] extract(short[] pcm, int sampleRate) {
        float[] samples = new float[pcm.length];
        for (int index = 0; index < pcm.length; index++) samples[index] = pcm[index] / 32768.0f;
        OnlineStream stream = extractor.createStream();
        try {
            stream.acceptWaveform(samples, sampleRate);
            stream.inputFinished();
            if (!extractor.isReady(stream)) return null;
            return VoiceprintMatcher.normalize(extractor.compute(stream));
        } finally {
            stream.release();
        }
    }

    @Override
    public synchronized void close() {
        if (extractor != null) extractor.release();
        extractor = null;
    }

    public static final class Result {
        public final String localId;
        public final String label;
        public final String relation;
        public final double confidence;
        public final String reason;

        Result(String localId, String label, String relation, double confidence, String reason) {
            this.localId = localId;
            this.label = label;
            this.relation = relation;
            this.confidence = confidence;
            this.reason = reason;
        }

        static Result unresolved(String reason) {
            return new Result("spk_unknown_default", null, "unknown", 0, reason);
        }

        static Result unresolved(String localId, String reason) {
            String stableLocalId = localId == null || localId.trim().isEmpty()
                    ? "spk_unknown_default"
                    : localId;
            return new Result(stableLocalId, null, "unknown", 0, reason);
        }
    }
}
