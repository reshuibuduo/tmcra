package com.tmcra.memory.mobile.audio;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.util.Log;
import android.view.WindowManager;

import com.tmcra.memory.mobile.net.TmcraApiClient;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.Collections;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * ADB-only authenticated smoke test for the phone -> website BFF -> GPU ASR -> memory chain.
 * It immediately requests exact deletion of the temporary remote memory it creates.
 */
public final class AudioApiProbeActivity extends Activity {
    private static final String TAG = "TMCRA_AUDIO_API_PROBE";
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private PowerManager.WakeLock wakeLock;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true);
            setTurnScreenOn(true);
        }
        PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "tmcra:audio-api-probe");
        wakeLock.acquire(5 * 60_000L);
        String requested = getIntent().getStringExtra("fixture");
        String fixture = requested == null || requested.trim().isEmpty()
                ? "1-two-speakers-en.wav"
                : new File(requested).getName();
        worker.execute(() -> runProbe(fixture));
    }

    private void runProbe(String fixtureName) {
        File directory = new File(getFilesDir(), "fixtures");
        File resultFile = new File(directory, "last-audio-api-result.json");
        JSONObject report = new JSONObject();
        try {
            File fixture = new File(directory, fixtureName);
            if (!fixture.isFile()) throw new IllegalStateException("fixture_missing");
            SharedPreferences settings = getSharedPreferences(
                    AudioCaptureService.PREFERENCES,
                    Context.MODE_PRIVATE);
            String namespace = settings.getString(AudioCaptureService.PREF_SCOPE_NAMESPACE, "");
            if (namespace == null || namespace.trim().isEmpty()) {
                throw new IllegalStateException("scope_namespace_missing");
            }
            String suffix = UUID.randomUUID().toString().replace("-", "");
            String eventId = "evt_e2e_" + suffix;
            String sessionId = "audio-e2e-" + suffix.substring(0, 12);
            String scopeName = namespace + "-project-life-audio";
            TmcraApiClient api = new TmcraApiClient(this);
            long asrStarted = System.nanoTime();
            TmcraApiClient.Transcription transcription = api.transcribe(
                    fixture,
                    scopeName,
                    sessionId,
                    "en-US",
                    eventId,
                    null,
                    null,
                    null,
                    Collections.emptyList());
            long asrMs = elapsedMs(asrStarted);
            if (transcription.text == null || transcription.text.trim().isEmpty()) {
                throw new IllegalStateException("remote_transcript_empty");
            }

            TmcraApiClient.AudioEvent event = new TmcraApiClient.AudioEvent();
            event.eventId = eventId;
            event.sessionId = sessionId;
            event.scopeName = scopeName;
            event.capturedAt = isoNow();
            event.transcript = transcription.text.trim();
            event.durationMs = Math.max(100, (int) (((fixture.length() - 44L) / 2L) * 1_000L / 16_000L));
            event.language = transcription.language;
            event.speakerId = "spk_e2e_probe";
            event.speakerLabel = null;
            event.speakerRelation = "unknown";
            event.speakerConfidence = 0;
            event.asrMode = "remote_review";
            event.asrModel = transcription.model;
            event.asrConfidence = null;
            TmcraApiClient.Candidate remote = transcription.remoteCandidate;
            event.remoteAsrSha256 = remote == null || remote.sha256.isEmpty()
                    ? sha256(event.transcript)
                    : remote.sha256;
            event.remoteAsrModel = transcription.model;
            event.remoteAsrProvider = transcription.provider;
            TmcraApiClient.Resolution resolution = transcription.resolution;
            event.resolutionStatus = resolution == null ? "resolved" : resolution.status;
            event.resolutionSource = resolution == null ? "remote" : resolution.selectedSource;
            event.resolutionConfidence = resolution == null ? "medium" : resolution.confidenceBand;
            event.resolutionSimilarity = resolution == null ? null : resolution.similarity;
            event.resolutionReasons = resolution == null ? "[\"remote_only\"]" : resolution.reasonsJson;

            long writeStarted = System.nanoTime();
            TmcraApiClient.UploadReceipt write = api.submitEvent(event);
            long writeMs = elapsedMs(writeStarted);
            if (write.messageId == null || write.messageId.trim().isEmpty()) {
                throw new IllegalStateException("remote_message_id_missing");
            }
            long deleteStarted = System.nanoTime();
            TmcraApiClient.DeletionReceipt deletion = api.deleteAudioMemory(
                    eventId,
                    scopeName,
                    write.messageId);
            long deleteMs = elapsedMs(deleteStarted);

            report.put("ok", true)
                    .put("fixture", fixtureName)
                    .put("asr_provider", transcription.provider)
                    .put("asr_model", transcription.model)
                    .put("transcript_sha256", sha256(event.transcript))
                    .put("transcript_characters", event.transcript.length())
                    .put("asr_ms", asrMs)
                    .put("write_ms", writeMs)
                    .put("write_status", write.status)
                    .put("recall_status", write.recallStatus)
                    .put("recall_count", write.recallCount)
                    .put("delete_ms", deleteMs)
                    .put("delete_status", deletion.status)
                    .put("cleanup_requested", true);
        } catch (Throwable error) {
            try {
                report.put("ok", false)
                        .put("fixture", fixtureName)
                        .put("error", error.getClass().getSimpleName() + ":" + safeMessage(error));
            } catch (Exception ignored) {
                // Primitive diagnostic fields are supported on every target Android version.
            }
        }
        try {
            if (!directory.exists() && !directory.mkdirs()) {
                throw new IllegalStateException("fixture_directory_unavailable");
            }
            try (FileOutputStream output = new FileOutputStream(resultFile, false)) {
                output.write(report.toString(2).getBytes(StandardCharsets.UTF_8));
            }
            Log.i(TAG, report.toString());
        } catch (Exception error) {
            Log.e(TAG, "Unable to persist API probe result", error);
        } finally {
            releaseWakeLock();
            worker.shutdown();
            runOnUiThread(this::finish);
        }
    }

    private static long elapsedMs(long startedNs) {
        return Math.round((System.nanoTime() - startedNs) / 1_000_000.0);
    }

    private static String sha256(String value) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256")
                .digest(value.getBytes(StandardCharsets.UTF_8));
        StringBuilder result = new StringBuilder(64);
        for (byte item : digest) result.append(String.format(Locale.US, "%02x", item & 0xff));
        return result.toString();
    }

    private static String isoNow() {
        SimpleDateFormat format = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US);
        format.setTimeZone(TimeZone.getTimeZone("UTC"));
        return format.format(new Date());
    }

    private static String safeMessage(Throwable error) {
        String message = error.getMessage();
        return message == null ? "no_message" : message.replace('\n', ' ').replace('\r', ' ');
    }

    private void releaseWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        wakeLock = null;
    }

    @Override
    protected void onDestroy() {
        releaseWakeLock();
        worker.shutdownNow();
        super.onDestroy();
    }
}
