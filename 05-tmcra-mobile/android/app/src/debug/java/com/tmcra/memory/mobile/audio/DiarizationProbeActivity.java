package com.tmcra.memory.mobile.audio;

import android.app.Activity;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.util.Log;
import android.view.WindowManager;

import org.json.JSONArray;
import org.json.JSONObject;

import com.tmcra.memory.mobile.data.AudioMemoryStore;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.UUID;

/** ADB-only debug probe. It never opens the account store, memory DB, or network client. */
public final class DiarizationProbeActivity extends Activity {
    private static final String TAG = "TMCRA_DIARIZATION_PROBE";
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private PowerManager.WakeLock wakeLock;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true);
            setTurnScreenOn(true);
        } else {
            getWindow().addFlags(
                    WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                            | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON);
        }
        PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "tmcra:diarization-probe");
        wakeLock.acquire(10 * 60_000L);
        String requested = getIntent().getStringExtra("fixture");
        String fixtureName = requested == null || requested.trim().isEmpty()
                ? "0-four-speakers-zh.wav"
                : new File(requested).getName();
        float threshold = getIntent().getFloatExtra(
                "threshold",
                SpeakerDiarizationEngine.UNKNOWN_SPEAKER_THRESHOLD);
        int numClusters = getIntent().getIntExtra("num_clusters", -1);
        boolean identityProbe = getIntent().getBooleanExtra("identity_probe", false);
        String identityDatabase = sanitizedDatabaseName(
                getIntent().getStringExtra("identity_database"));
        boolean identityReset = getIntent().getBooleanExtra(
                "identity_reset",
                identityDatabase == null);
        boolean identityPreserve = getIntent().getBooleanExtra("identity_preserve", false);
        worker.execute(() -> runProbe(
                fixtureName,
                threshold,
                numClusters,
                identityProbe,
                identityDatabase,
                identityReset,
                identityPreserve));
    }

    private void runProbe(
            String fixtureName,
            float threshold,
            int numClusters,
            boolean identityProbe,
            String identityDatabase,
            boolean identityReset,
            boolean identityPreserve) {
        File fixtureDirectory = new File(getFilesDir(), "fixtures");
        File resultFile = new File(fixtureDirectory, "last-diarization-result.json");
        File progressFile = new File(fixtureDirectory, "last-diarization-progress.json");
        JSONObject report = new JSONObject();
        try {
            File fixture = new File(fixtureDirectory, fixtureName);
            writeProbeProgress(progressFile, "fixture_loading", new JSONObject()
                    .put("fixture", fixtureName));
            WavData wav = readPcm16Mono(fixture);
            writeProbeProgress(progressFile, "diarizer_initializing", new JSONObject()
                    .put("fixture", fixtureName)
                    .put("sample_rate", wav.sampleRate)
                    .put("sample_count", wav.pcm.length));
            long initializeStarted = System.nanoTime();
            SpeakerDiarizationEngine engine = new SpeakerDiarizationEngine(
                    this,
                    threshold,
                    numClusters);
            long initializeMs = elapsedMs(initializeStarted);
            try {
                if (!engine.isAvailable()) throw new IOException(engine.unavailableReason());
                writeProbeProgress(progressFile, "diarization_running", new JSONObject()
                        .put("fixture", fixtureName)
                        .put("initialize_ms", initializeMs));
                long processStarted = System.nanoTime();
                SpeakerDiarizationEngine.Result result = engine.diarize(wav.pcm, wav.sampleRate);
                long processMs = elapsedMs(processStarted);
                if (!result.available) throw new IOException(result.reason);
                writeProbeProgress(progressFile, "diarization_complete", new JSONObject()
                        .put("fixture", fixtureName)
                        .put("process_ms", processMs)
                        .put("speaker_count", result.speakerCount)
                        .put("turn_count", result.turns.size()));
                double audioSeconds = wav.pcm.length / (double) wav.sampleRate;
                JSONArray turns = new JSONArray();
                for (SpeakerDiarizationEngine.Turn turn : result.turns) {
                    turns.put(new JSONObject()
                            .put("speaker", turn.localSpeaker)
                            .put("start_seconds", turn.startSeconds)
                            .put("end_seconds", turn.endSeconds)
                            .put("duration_seconds", turn.durationSeconds())
                            .put("overlap", turn.overlap));
                }
                JSONArray exclusiveTurns = new JSONArray();
                List<SpeakerDiarizationEngine.Turn> exclusive =
                        SpeakerDiarizationEngine.exclusiveTimeline(result.turns);
                for (SpeakerDiarizationEngine.Turn turn : exclusive) {
                    exclusiveTurns.put(new JSONObject()
                            .put("speaker", turn.localSpeaker)
                            .put("start_seconds", turn.startSeconds)
                            .put("end_seconds", turn.endSeconds)
                            .put("duration_seconds", turn.durationSeconds())
                            .put("overlap", turn.overlap));
                }
                report.put("ok", true)
                        .put("fixture", fixtureName)
                        .put("model", SpeakerDiarizationEngine.MODEL_ID)
                        .put("clustering_threshold", threshold)
                        .put("requested_num_clusters", numClusters)
                        .put("sample_rate", wav.sampleRate)
                        .put("audio_seconds", audioSeconds)
                        .put("speaker_count", result.speakerCount)
                        .put("turn_count", result.turns.size())
                        .put("initialize_ms", initializeMs)
                        .put("process_ms", processMs)
                        .put("rtf", processMs / (audioSeconds * 1_000.0))
                        .put("turns", turns)
                        .put("exclusive_turn_count", exclusiveTurns.length())
                        .put("exclusive_turns", exclusiveTurns);
                if (identityProbe) {
                    addIdentityProbe(
                            report,
                            exclusive,
                            wav,
                            identityDatabase,
                            identityReset,
                            identityPreserve,
                            progressFile);
                }
            } finally {
                engine.close();
            }
        } catch (Throwable error) {
            try {
                report.put("ok", false)
                        .put("fixture", fixtureName)
                        .put("error", error.getClass().getSimpleName() + ":" + safeMessage(error));
                writeProbeProgress(progressFile, "failed", new JSONObject()
                        .put("fixture", fixtureName)
                        .put("error", error.getClass().getSimpleName() + ":" + safeMessage(error)));
            } catch (Exception ignored) {
                // JSONObject accepts these primitive fields on supported Android versions.
            }
        }
        try {
            if (!fixtureDirectory.exists() && !fixtureDirectory.mkdirs()) {
                throw new IOException("fixture_directory_unavailable");
            }
            try (FileOutputStream output = new FileOutputStream(resultFile, false)) {
                output.write(report.toString(2).getBytes(StandardCharsets.UTF_8));
            }
            writeProbeProgress(progressFile, "complete", new JSONObject()
                    .put("fixture", fixtureName)
                    .put("ok", report.optBoolean("ok", false)));
            Log.i(TAG, report.toString());
        } catch (Exception error) {
            Log.e(TAG, "Unable to persist probe result", error);
        } finally {
            releaseWakeLock();
            worker.shutdown();
            runOnUiThread(this::finish);
        }
    }

    private void addIdentityProbe(
            JSONObject report,
            List<SpeakerDiarizationEngine.Turn> turns,
            WavData wav,
            String requestedDatabase,
            boolean resetDatabase,
            boolean preserveDatabase,
            File progressFile) throws Exception {
        String databaseName = requestedDatabase == null
                ? "tmcra_voice_probe_" + UUID.randomUUID().toString().replace("-", "") + ".db"
                : requestedDatabase;
        if (resetDatabase) deleteDatabase(databaseName);
        AudioMemoryStore store = null;
        SpeakerIdentityEngine identity = null;
        try {
            Map<Integer, List<short[]>> chunks = new TreeMap<>();
            int excludedOverlapTurns = 0;
            for (SpeakerDiarizationEngine.Turn turn : turns) {
                if (turn.overlap || turn.localSpeaker < 0) {
                    excludedOverlapTurns++;
                    continue;
                }
                chunks.computeIfAbsent(turn.localSpeaker, ignored -> new ArrayList<>())
                        .add(crop(wav.pcm, wav.sampleRate, turn.startSeconds, turn.endSeconds));
            }

            store = new AudioMemoryStore(this, databaseName);
            int profileCountBefore = store.speakers().size();
            identity = new SpeakerIdentityEngine(this, store);
            if (!identity.isAvailable()) throw new IOException(identity.unavailableReason());
            writeProbeProgress(progressFile, "identity_first_pass_started", new JSONObject()
                    .put("eligible_speakers", chunks.size())
                    .put("profile_count_before", profileCountBefore));

            Map<Integer, String> firstPassIds = new TreeMap<>();
            JSONArray enrollment = new JSONArray();
            boolean everyFirstPassMatched = true;
            int firstPassIndex = 0;
            for (Map.Entry<Integer, List<short[]>> entry : chunks.entrySet()) {
                writeProbeProgress(progressFile, "identity_first_pass_running", new JSONObject()
                        .put("speaker_index", firstPassIndex)
                        .put("speaker_total", chunks.size())
                        .put("diarizer_speaker", entry.getKey()));
                SpeakerIdentityEngine.Result value = identity.identify(concat(entry.getValue()), wav.sampleRate);
                firstPassIds.put(entry.getKey(), value.localId);
                everyFirstPassMatched &= "matched".equals(value.reason);
                enrollment.put(new JSONObject()
                        .put("diarizer_speaker", entry.getKey())
                        .put("persistent_speaker_id", value.localId)
                        .put("reason", value.reason)
                        .put("confidence", value.confidence));
                firstPassIndex++;
            }
            int enrolledProfileCount = store.speakers().size();
            int newProfilesCreated = Math.max(0, enrolledProfileCount - profileCountBefore);
            writeProbeProgress(progressFile, "identity_first_pass_complete", new JSONObject()
                    .put("persistent_voiceprint_count", enrolledProfileCount)
                    .put("new_profiles_created", newProfilesCreated));

            identity.close();
            identity = null;
            store.close();
            store = null;

            store = new AudioMemoryStore(this, databaseName);
            identity = new SpeakerIdentityEngine(this, store);
            if (!identity.isAvailable()) throw new IOException(identity.unavailableReason());
            writeProbeProgress(progressFile, "identity_reopen_started", new JSONObject()
                    .put("expected_speakers", chunks.size())
                    .put("stored_voiceprints", store.speakers().size()));
            JSONArray reopenMappings = new JSONArray();
            boolean stableIds = true;
            boolean everyReopenMatched = true;
            int reopenIndex = 0;
            for (Map.Entry<Integer, List<short[]>> entry : chunks.entrySet()) {
                writeProbeProgress(progressFile, "identity_reopen_running", new JSONObject()
                        .put("speaker_index", reopenIndex)
                        .put("speaker_total", chunks.size())
                        .put("diarizer_speaker", entry.getKey()));
                SpeakerIdentityEngine.Result value = identity.identify(concat(entry.getValue()), wav.sampleRate);
                String expected = firstPassIds.get(entry.getKey());
                boolean stable = expected != null && expected.equals(value.localId);
                stableIds &= stable;
                everyReopenMatched &= "matched".equals(value.reason);
                reopenMappings.put(new JSONObject()
                        .put("diarizer_speaker", entry.getKey())
                        .put("persistent_speaker_id", value.localId)
                        .put("reason", value.reason)
                        .put("confidence", value.confidence)
                        .put("stable", stable));
                reopenIndex++;
            }
            int reopenedProfileCount = store.speakers().size();
            int expectedNewProfiles = profileCountBefore == 0 ? chunks.size() : 0;
            boolean lifecycleOk = !chunks.isEmpty()
                    && enrolledProfileCount == chunks.size()
                    && reopenedProfileCount == enrolledProfileCount
                    && newProfilesCreated == expectedNewProfiles
                    && (profileCountBefore == 0 || everyFirstPassMatched)
                    && stableIds
                    && everyReopenMatched;
            report.put("identity_probe", true)
                    .put("identity_lifecycle_ok", lifecycleOk)
                    .put("identity_database", databaseName)
                    .put("identity_database_preserved", preserveDatabase)
                    .put("eligible_diarizer_speaker_count", chunks.size())
                    .put("excluded_overlap_turn_count", excludedOverlapTurns)
                    .put("profile_count_before", profileCountBefore)
                    .put("new_profiles_created", newProfilesCreated)
                    .put("persistent_voiceprint_count", enrolledProfileCount)
                    .put("reopened_voiceprint_count", reopenedProfileCount)
                    .put("every_first_pass_matched", everyFirstPassMatched)
                    .put("stable_after_reopen", stableIds)
                    .put("every_reopen_matched", everyReopenMatched)
                    .put("identity_mappings", enrollment)
                    .put("reopen_identity_mappings", reopenMappings);
            if (!lifecycleOk) {
                report.put("ok", false)
                        .put("identity_error", "voiceprint_lifecycle_gate_failed");
            }
            writeProbeProgress(progressFile, "identity_complete", new JSONObject()
                    .put("lifecycle_ok", lifecycleOk)
                    .put("persistent_voiceprint_count", enrolledProfileCount)
                    .put("reopened_voiceprint_count", reopenedProfileCount));
        } finally {
            if (identity != null) identity.close();
            if (store != null) store.close();
            if (!preserveDatabase) deleteDatabase(databaseName);
        }
    }

    private static void writeProbeProgress(
            File progressFile,
            String phase,
            JSONObject details) throws IOException {
        File parent = progressFile.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs()) {
            throw new IOException("probe_progress_directory_unavailable");
        }
        try {
            JSONObject payload = details == null ? new JSONObject() : details;
            payload.put("phase", phase)
                    .put("updated_at_epoch_ms", System.currentTimeMillis());
            try (FileOutputStream output = new FileOutputStream(progressFile, false)) {
                output.write(payload.toString(2).getBytes(StandardCharsets.UTF_8));
            }
        } catch (org.json.JSONException error) {
            throw new IOException("probe_progress_json_failed", error);
        }
    }

    private static String sanitizedDatabaseName(String requested) {
        if (requested == null || requested.trim().isEmpty()) return null;
        String name = new File(requested.trim()).getName();
        name = name.replaceAll("[^A-Za-z0-9._-]", "_");
        if (!name.endsWith(".db")) name += ".db";
        return name.length() > 120 ? name.substring(name.length() - 120) : name;
    }

    private static short[] crop(
            short[] pcm,
            int sampleRate,
            float startSeconds,
            float endSeconds) {
        int start = Math.max(0, Math.min(pcm.length, Math.round(startSeconds * sampleRate)));
        int end = Math.max(start, Math.min(pcm.length, Math.round(endSeconds * sampleRate)));
        short[] result = new short[end - start];
        System.arraycopy(pcm, start, result, 0, result.length);
        return result;
    }

    private static short[] concat(List<short[]> chunks) {
        int length = 0;
        for (short[] chunk : chunks) length += chunk.length;
        short[] result = new short[length];
        int offset = 0;
        for (short[] chunk : chunks) {
            System.arraycopy(chunk, 0, result, offset, chunk.length);
            offset += chunk.length;
        }
        return result;
    }

    @Override
    protected void onDestroy() {
        releaseWakeLock();
        worker.shutdownNow();
        super.onDestroy();
    }

    private void releaseWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        wakeLock = null;
    }

    private static long elapsedMs(long startedNs) {
        return Math.round((System.nanoTime() - startedNs) / 1_000_000.0);
    }

    private static String safeMessage(Throwable error) {
        String message = error.getMessage();
        return message == null ? "no_message" : message.replace('\n', ' ').replace('\r', ' ');
    }

    private static WavData readPcm16Mono(File file) throws IOException {
        if (!file.isFile()) throw new IOException("fixture_missing");
        byte[] bytes;
        try (FileInputStream input = new FileInputStream(file);
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[64 * 1024];
            int count;
            while ((count = input.read(buffer)) >= 0) output.write(buffer, 0, count);
            bytes = output.toByteArray();
        }
        ByteBuffer reader = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
        requireChunk(reader, "RIFF");
        reader.getInt();
        requireChunk(reader, "WAVE");
        int sampleRate = 0;
        int channels = 0;
        int bits = 0;
        byte[] pcmBytes = null;
        while (reader.remaining() >= 8) {
            String chunk = readAscii(reader, 4);
            int size = reader.getInt();
            if (size < 0 || size > reader.remaining()) throw new IOException("invalid_wav_chunk:" + chunk);
            int next = reader.position() + size + (size & 1);
            if ("fmt ".equals(chunk)) {
                int format = reader.getShort() & 0xffff;
                channels = reader.getShort() & 0xffff;
                sampleRate = reader.getInt();
                reader.getInt();
                reader.getShort();
                bits = reader.getShort() & 0xffff;
                if (format != 1) throw new IOException("fixture_not_pcm");
            } else if ("data".equals(chunk)) {
                pcmBytes = new byte[size];
                reader.get(pcmBytes);
            }
            reader.position(Math.min(next, reader.limit()));
        }
        if (pcmBytes == null || channels != 1 || bits != 16 || sampleRate <= 0) {
            throw new IOException("fixture_must_be_pcm16_mono");
        }
        ByteBuffer pcmReader = ByteBuffer.wrap(pcmBytes).order(ByteOrder.LITTLE_ENDIAN);
        short[] pcm = new short[pcmBytes.length / 2];
        for (int index = 0; index < pcm.length; index++) pcm[index] = pcmReader.getShort();
        return new WavData(sampleRate, pcm);
    }

    private static void requireChunk(ByteBuffer reader, String expected) throws IOException {
        String actual = readAscii(reader, expected.length());
        if (!expected.equals(actual)) throw new IOException("expected_" + expected + "_found_" + actual);
    }

    private static String readAscii(ByteBuffer reader, int count) {
        byte[] bytes = new byte[count];
        reader.get(bytes);
        return new String(bytes, StandardCharsets.US_ASCII);
    }

    private static final class WavData {
        final int sampleRate;
        final short[] pcm;

        WavData(int sampleRate, short[] pcm) {
            this.sampleRate = sampleRate;
            this.pcm = pcm;
        }
    }
}
