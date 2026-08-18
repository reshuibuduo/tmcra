package com.tmcra.memory.mobile.audio;

import com.tmcra.memory.mobile.data.AudioMemoryStore;
import com.tmcra.memory.mobile.security.CryptoBox;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/**
 * Voiceprint match-or-create orchestration, decoupled from the native embedding
 * runtime so the identity lifecycle can be validated deterministically and fast
 * (first enrollment, same-person reuse, stranger addition, ID stability after a
 * store reopen) without running any neural model.
 *
 * Production {@link SpeakerIdentityEngine} delegates here after extracting an
 * embedding; debug and instrumentation probes drive this class directly with
 * synthetic vectors.
 */
final class VoiceprintMatcher {
    private static final int MAX_ADAPT_SAMPLES = 12;

    private final AudioMemoryStore store;
    private final CryptoBox crypto;

    VoiceprintMatcher(AudioMemoryStore store, CryptoBox crypto) {
        this.store = store;
        this.crypto = crypto;
    }

    SpeakerIdentityEngine.Result identify(float[] embedding) {
        if (embedding == null || embedding.length == 0) {
            return SpeakerIdentityEngine.Result.unresolved("embedding_empty");
        }
        List<DecodedProfile> profiles = decodedProfiles();
        DecodedProfile best = null;
        float bestScore = -1f;
        float secondScore = -1f;
        for (DecodedProfile profile : profiles) {
            if (profile.embedding.length != embedding.length) continue;
            float score = SpeakerMatchPolicy.cosine(embedding, profile.embedding);
            if (score > bestScore) {
                secondScore = bestScore;
                bestScore = score;
                best = profile;
            } else if (score > secondScore) {
                secondScore = score;
            }
        }

        if (best != null) {
            if (SpeakerMatchPolicy.accepts(bestScore, secondScore, best.profile.relation)) {
                maybeAdapt(best, embedding, bestScore);
                return new SpeakerIdentityEngine.Result(
                        best.profile.localId,
                        best.profile.displayName,
                        best.profile.relation,
                        clamp01(bestScore),
                        "matched");
            }
        }

        String localId = "spk_local_" + UUID.randomUUID().toString().replace("-", "");
        try {
            store.insertSpeaker(new AudioMemoryStore.SpeakerProfile(
                    localId,
                    null,
                    "unknown",
                    crypto.encrypt(encode(embedding)),
                    1,
                    0,
                    0,
                    "local_only",
                    null));
            return new SpeakerIdentityEngine.Result(localId, null, "unknown", 0, "new_cluster");
        } catch (Exception error) {
            return SpeakerIdentityEngine.Result.unresolved("voiceprint_store_failed");
        }
    }

    private void maybeAdapt(DecodedProfile matched, float[] current, float score) {
        if (score < SpeakerMatchPolicy.ADAPT_THRESHOLD
                || matched.profile.sampleCount >= MAX_ADAPT_SAMPLES) return;
        int count = Math.max(1, matched.profile.sampleCount);
        float[] averaged = new float[current.length];
        for (int index = 0; index < current.length; index++) {
            averaged[index] = (matched.embedding[index] * count + current[index]) / (count + 1f);
        }
        averaged = normalize(averaged);
        try {
            store.updateSpeakerTemplate(
                    matched.profile.localId,
                    crypto.encrypt(encode(averaged)),
                    count + 1,
                    score);
        } catch (Exception ignored) {
            // Identification remains valid even if template adaptation cannot be persisted.
        }
    }

    private List<DecodedProfile> decodedProfiles() {
        ArrayList<DecodedProfile> result = new ArrayList<>();
        for (AudioMemoryStore.SpeakerProfile profile : store.speakers()) {
            try {
                float[] embedding = normalize(decode(crypto.decrypt(profile.encryptedTemplate)));
                if (embedding.length > 0) result.add(new DecodedProfile(profile, embedding));
            } catch (Exception ignored) {
                // A corrupted or invalidated template is ignored instead of weakening attribution.
            }
        }
        return result;
    }

    static float[] normalize(float[] values) {
        double norm = 0;
        for (float value : values) norm += value * value;
        if (norm <= 0) return values;
        float divisor = (float) Math.sqrt(norm);
        for (int index = 0; index < values.length; index++) values[index] /= divisor;
        return values;
    }

    static byte[] encode(float[] embedding) {
        ByteBuffer buffer = ByteBuffer.allocate(embedding.length * 4).order(ByteOrder.LITTLE_ENDIAN);
        for (float value : embedding) buffer.putFloat(value);
        return buffer.array();
    }

    static float[] decode(byte[] value) {
        if (value == null || value.length == 0 || value.length % 4 != 0) return new float[0];
        ByteBuffer buffer = ByteBuffer.wrap(value).order(ByteOrder.LITTLE_ENDIAN);
        float[] result = new float[value.length / 4];
        for (int index = 0; index < result.length; index++) result[index] = buffer.getFloat();
        return result;
    }

    private static double clamp01(float value) {
        return Math.max(0, Math.min(1, value));
    }

    private static final class DecodedProfile {
        final AudioMemoryStore.SpeakerProfile profile;
        final float[] embedding;

        DecodedProfile(AudioMemoryStore.SpeakerProfile profile, float[] embedding) {
            this.profile = profile;
            this.embedding = embedding;
        }
    }
}
