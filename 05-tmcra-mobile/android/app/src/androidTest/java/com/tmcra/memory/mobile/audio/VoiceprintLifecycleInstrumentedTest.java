package com.tmcra.memory.mobile.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import android.content.Context;

import androidx.test.core.app.ApplicationProvider;
import androidx.test.ext.junit.runners.AndroidJUnit4;

import com.tmcra.memory.mobile.data.AudioMemoryStore;
import com.tmcra.memory.mobile.security.CryptoBox;

import org.junit.After;
import org.junit.Test;
import org.junit.runner.RunWith;

import java.util.UUID;

/**
 * Fast deterministic identity-lifecycle gate. Covers the four scenarios the
 * server emulator gate could not reach because it front-loaded the slow
 * diarization:
 *
 * <ol>
 *   <li>first enrollment creates a persistent voiceprint;</li>
 *   <li>the same speaker reusing the engine matches the same local ID;</li>
 *   <li>a new speaker creates a new cluster instead of merging;</li>
 *   <li>closing and reopening the store keeps IDs stable (process-restart proxy).</li>
 * </ol>
 *
 * No neural model runs here: {@link VoiceprintMatcher} is driven with
 * deterministic synthetic embeddings, so the whole suite finishes in seconds.
 */
@RunWith(AndroidJUnit4.class)
public final class VoiceprintLifecycleInstrumentedTest {
    private static final int DIM = 192;
    private static final String KEY_ALIAS = "tmcra_test_vp_key";

    private String databaseName;

    @After
    public void tearDown() {
        if (databaseName != null) {
            ApplicationProvider.getApplicationContext().deleteDatabase(databaseName);
            databaseName = null;
        }
    }

    @Test
    public void firstEnrollmentSameSpeakerReuseStrangerAndReopenStability() {
        Context context = ApplicationProvider.getApplicationContext();
        databaseName = "fast_lifecycle_" + UUID.randomUUID().toString().replace("-", "") + ".db";
        context.deleteDatabase(databaseName);

        float[] speakerA = normalized(alternating(1.0f, 0.3f));
        float[] speakerB = normalized(alternating(0.3f, 1.0f));

        AudioMemoryStore store = new AudioMemoryStore(context, databaseName);
        try {
            VoiceprintMatcher matcher = new VoiceprintMatcher(
                    store, new CryptoBox(KEY_ALIAS));

            // 1) First enrollment: an empty store creates a persistent profile.
            SpeakerIdentityEngine.Result first = matcher.identify(speakerA);
            assertEquals("new_cluster", first.reason);
            assertNotEquals("spk_unknown_default", first.localId);
            assertEquals(1, store.speakers().size());

            // 2) Same speaker again: matched to the same local ID (template adaptation allowed).
            SpeakerIdentityEngine.Result reuse = matcher.identify(variation(speakerA, 7));
            assertEquals("matched", reuse.reason);
            assertEquals(first.localId, reuse.localId);
            assertEquals(1, store.speakers().size());

            // 3) Stranger: a clearly different embedding must not merge into speaker A.
            SpeakerIdentityEngine.Result stranger = matcher.identify(speakerB);
            assertEquals("new_cluster", stranger.reason);
            assertNotEquals(first.localId, stranger.localId);
            assertEquals(2, store.speakers().size());

            // 4) Store reopen (process-restart proxy): IDs stay stable for both speakers.
            store.close();
            AudioMemoryStore reopened = new AudioMemoryStore(context, databaseName);
            try {
                VoiceprintMatcher reopenedMatcher = new VoiceprintMatcher(
                        reopened, new CryptoBox(KEY_ALIAS));
                SpeakerIdentityEngine.Result aAfterReopen =
                        reopenedMatcher.identify(variation(speakerA, 13));
                assertEquals("matched", aAfterReopen.reason);
                assertEquals(first.localId, aAfterReopen.localId);

                SpeakerIdentityEngine.Result bAfterReopen =
                        reopenedMatcher.identify(variation(speakerB, 17));
                assertEquals("matched", bAfterReopen.reason);
                assertEquals(stranger.localId, bAfterReopen.localId);
                assertEquals(2, reopened.speakers().size());
            } finally {
                reopened.close();
            }
        } finally {
            store.close();
        }
    }

    @Test
    public void corruptedTemplateIsIgnoredAndDoesNotBreakIdentification() {
        Context context = ApplicationProvider.getApplicationContext();
        databaseName = "fast_lifecycle_corrupt_" + UUID.randomUUID().toString().replace("-", "") + ".db";
        context.deleteDatabase(databaseName);

        float[] speakerA = normalized(alternating(1.0f, 0.2f));
        AudioMemoryStore store = new AudioMemoryStore(context, databaseName);
        try {
            VoiceprintMatcher matcher = new VoiceprintMatcher(
                    store, new CryptoBox(KEY_ALIAS));
            SpeakerIdentityEngine.Result first = matcher.identify(speakerA);
            assertEquals("new_cluster", first.reason);
            assertNotNull(first.localId);

            // Corrupt the stored template bytes directly.
            AudioMemoryStore.SpeakerProfile profile = store.speakers().get(0);
            byte[] corrupted = new byte[]{9, 9, 9, 9};
            store.updateSpeakerTemplate(profile.localId, corrupted, 2, 0.9);

            // Identification must still work and create a fresh cluster rather than crash.
            SpeakerIdentityEngine.Result afterCorruption = matcher.identify(variation(speakerA, 5));
            assertTrue("new_cluster".equals(afterCorruption.reason)
                    || "matched".equals(afterCorruption.reason));
            assertNotEquals("spk_unknown_default", afterCorruption.localId);
        } finally {
            store.close();
        }
    }

    private static float[] alternating(float even, float odd) {
        float[] values = new float[DIM];
        for (int index = 0; index < DIM; index++) {
            values[index] = index % 2 == 0 ? even : odd;
        }
        return values;
    }

    /** Deterministic small perturbation; cosine with the source stays above the match threshold. */
    private static float[] variation(float[] source, int seed) {
        float[] result = new float[source.length];
        long state = seed * 2654435761L + 40503L;
        for (int index = 0; index < source.length; index++) {
            state = state * 6364136223846793005L + 1442695040888963407L;
            double uniform = ((state >>> 33) & 0xffffffffL) / (double) 0x100000000L;
            result[index] = source[index] + (float) (0.06 * (uniform - 0.5));
        }
        return normalized(result);
    }

    private static float[] normalized(float[] values) {
        double norm = 0;
        for (float value : values) norm += value * value;
        if (norm <= 0) return values;
        float divisor = (float) Math.sqrt(norm);
        for (int index = 0; index < values.length; index++) values[index] /= divisor;
        return values;
    }
}
