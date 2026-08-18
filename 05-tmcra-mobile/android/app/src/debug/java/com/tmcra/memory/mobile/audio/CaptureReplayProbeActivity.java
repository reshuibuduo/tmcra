package com.tmcra.memory.mobile.audio;

import android.app.Activity;
import android.content.Intent;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.util.Log;
import android.view.WindowManager;

import com.tmcra.memory.mobile.data.AudioMemoryStore;
import com.tmcra.memory.mobile.net.TmcraApiClient;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/** ADB-only acoustic replay through the production microphone capture service. */
public final class CaptureReplayProbeActivity extends Activity {
    private static final String TAG = "TMCRA_CAPTURE_REPLAY";
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private PowerManager.WakeLock wakeLock;
    private MediaPlayer player;
    private int originalMusicVolume = -1;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true);
            setTurnScreenOn(true);
        }
        PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "tmcra:capture-replay-probe");
        wakeLock.acquire(6 * 60_000L);
        String requested = getIntent().getStringExtra("fixture");
        String fixture = requested == null || requested.trim().isEmpty()
                ? "1-two-speakers-en.wav"
                : new File(requested).getName();
        worker.execute(() -> runProbe(fixture));
    }

    private void runProbe(String fixtureName) {
        File fixtureDirectory = new File(getFilesDir(), "fixtures");
        File resultFile = new File(fixtureDirectory, "last-capture-replay-result.json");
        JSONObject report = new JSONObject();
        AudioMemoryStore store = new AudioMemoryStore(this);
        Set<String> initialEvents = eventIds(store.recent(500));
        Set<String> initialSpeakers = speakerIds(store.speakers());
        List<AudioMemoryStore.Segment> captured = new ArrayList<>();
        try {
            File fixture = new File(fixtureDirectory, fixtureName);
            if (!fixture.isFile()) throw new IllegalStateException("fixture_missing");
            startService(new Intent(this, AudioCaptureService.class)
                    .setAction(AudioCaptureService.ACTION_START));
            Thread.sleep(5_000);
            playAndWait(fixture);
            captured = waitForTerminalSegments(store, initialEvents, 210_000L);
            stopService(new Intent(this, AudioCaptureService.class));
            if (captured.isEmpty()) throw new IllegalStateException("no_segments_captured");

            Set<String> observedSpeakers = new HashSet<>();
            int overlaps = 0;
            int uploaded = 0;
            JSONArray segments = new JSONArray();
            for (AudioMemoryStore.Segment segment : captured) {
                if (segment.speakerId != null && segment.speakerId.startsWith("spk_overlap_")) {
                    overlaps++;
                } else if (segment.speakerId != null) {
                    observedSpeakers.add(segment.speakerId);
                }
                if ("uploaded".equals(segment.state)) uploaded++;
                segments.put(new JSONObject()
                        .put("duration_ms", segment.durationMs)
                        .put("speaker_id", segment.speakerId)
                        .put("speaker_relation", segment.speakerRelation)
                        .put("state", segment.state)
                        .put("pipeline_stage", segment.pipelineStage)
                        .put("transcript_sha256", segment.transcript == null
                                ? JSONObject.NULL : sha256(segment.transcript))
                        .put("last_error", segment.lastError == null
                                ? JSONObject.NULL : segment.lastError));
            }
            JSONObject cleanup = cleanup(store, captured, initialSpeakers);
            report.put("ok", observedSpeakers.size() >= 2 && uploaded > 0)
                    .put("fixture", fixtureName)
                    .put("segment_count", captured.size())
                    .put("distinct_non_overlap_speakers", observedSpeakers.size())
                    .put("overlap_segments", overlaps)
                    .put("uploaded_segments", uploaded)
                    .put("segments", segments)
                    .put("cleanup", cleanup);
        } catch (Throwable error) {
            stopService(new Intent(this, AudioCaptureService.class));
            try {
                captured = newSegments(store, initialEvents);
                JSONObject cleanup = cleanup(store, captured, initialSpeakers);
                report.put("ok", false)
                        .put("fixture", fixtureName)
                        .put("error", error.getClass().getSimpleName() + ":" + safeMessage(error))
                        .put("captured_before_failure", captured.size())
                        .put("cleanup", cleanup);
            } catch (Exception ignored) {
                // The primary error remains the useful diagnostic.
            }
        } finally {
            restoreVolume();
            store.close();
        }
        try {
            if (!fixtureDirectory.exists() && !fixtureDirectory.mkdirs()) {
                throw new IllegalStateException("fixture_directory_unavailable");
            }
            try (FileOutputStream output = new FileOutputStream(resultFile, false)) {
                output.write(report.toString(2).getBytes(StandardCharsets.UTF_8));
            }
            Log.i(TAG, report.toString());
        } catch (Exception error) {
            Log.e(TAG, "Unable to persist capture replay result", error);
        } finally {
            releaseWakeLock();
            worker.shutdown();
            runOnUiThread(this::finish);
        }
    }

    private void playAndWait(File fixture) throws Exception {
        CountDownLatch completed = new CountDownLatch(1);
        runOnUiThread(() -> {
            try {
                AudioManager audio = (AudioManager) getSystemService(AUDIO_SERVICE);
                originalMusicVolume = audio.getStreamVolume(AudioManager.STREAM_MUSIC);
                int testVolume = Math.max(1, Math.round(audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC) * 0.65f));
                audio.setStreamVolume(AudioManager.STREAM_MUSIC, testVolume, 0);
                player = new MediaPlayer();
                player.setAudioStreamType(AudioManager.STREAM_MUSIC);
                player.setDataSource(fixture.getAbsolutePath());
                player.setVolume(1f, 1f);
                player.setOnCompletionListener(ignored -> completed.countDown());
                player.setOnErrorListener((ignored, what, extra) -> {
                    completed.countDown();
                    return true;
                });
                player.prepare();
                player.start();
            } catch (Exception error) {
                Log.e(TAG, "Unable to start acoustic fixture", error);
                completed.countDown();
            }
        });
        if (!completed.await(90, TimeUnit.SECONDS)) throw new IllegalStateException("fixture_playback_timeout");
        Thread.sleep(4_000);
        releasePlayer();
    }

    private static List<AudioMemoryStore.Segment> waitForTerminalSegments(
            AudioMemoryStore store,
            Set<String> initialEvents,
            long timeoutMs) throws InterruptedException {
        long deadline = System.currentTimeMillis() + timeoutMs;
        int stableCount = -1;
        long stableSince = 0;
        while (System.currentTimeMillis() < deadline) {
            List<AudioMemoryStore.Segment> current = newSegments(store, initialEvents);
            boolean terminal = !current.isEmpty();
            for (AudioMemoryStore.Segment segment : current) {
                if (!isTerminal(segment.state)) {
                    terminal = false;
                    break;
                }
            }
            if (current.size() != stableCount) {
                stableCount = current.size();
                stableSince = System.currentTimeMillis();
            }
            if (terminal && System.currentTimeMillis() - stableSince >= 8_000L) return current;
            Thread.sleep(2_000);
        }
        return newSegments(store, initialEvents);
    }

    private JSONObject cleanup(
            AudioMemoryStore store,
            List<AudioMemoryStore.Segment> segments,
            Set<String> initialSpeakers) throws Exception {
        TmcraApiClient api = new TmcraApiClient(this);
        int remoteRequested = 0;
        int remoteFailed = 0;
        for (AudioMemoryStore.Segment segment : segments) {
            if (segment.remoteMessageId != null && !segment.remoteMessageId.isEmpty()) {
                try {
                    api.deleteAudioMemory(segment.eventId, segment.scopeName, segment.remoteMessageId);
                    remoteRequested++;
                } catch (Exception error) {
                    remoteFailed++;
                }
            }
            if (segment.wavPath != null) new File(segment.wavPath).delete();
            store.deleteSegment(segment.eventId);
        }
        int speakerProfilesDeleted = 0;
        for (AudioMemoryStore.SpeakerProfile speaker : store.speakers()) {
            if (!initialSpeakers.contains(speaker.localId)) {
                store.deleteSpeakerProfile(speaker.localId);
                speakerProfilesDeleted++;
            }
        }
        return new JSONObject()
                .put("remote_delete_requested", remoteRequested)
                .put("remote_delete_failed", remoteFailed)
                .put("local_segments_deleted", segments.size())
                .put("test_speaker_profiles_deleted", speakerProfilesDeleted);
    }

    private static boolean isTerminal(String state) {
        return "uploaded".equals(state)
                || "review_required".equals(state)
                || "remote_review_failed".equals(state)
                || "upload_failed".equals(state)
                || "asr_unavailable".equals(state);
    }

    private static List<AudioMemoryStore.Segment> newSegments(
            AudioMemoryStore store,
            Set<String> initialEvents) {
        ArrayList<AudioMemoryStore.Segment> result = new ArrayList<>();
        for (AudioMemoryStore.Segment segment : store.recent(500)) {
            if (!initialEvents.contains(segment.eventId)) result.add(segment);
        }
        return result;
    }

    private static Set<String> eventIds(List<AudioMemoryStore.Segment> segments) {
        HashSet<String> values = new HashSet<>();
        for (AudioMemoryStore.Segment segment : segments) values.add(segment.eventId);
        return values;
    }

    private static Set<String> speakerIds(List<AudioMemoryStore.SpeakerProfile> speakers) {
        HashSet<String> values = new HashSet<>();
        for (AudioMemoryStore.SpeakerProfile speaker : speakers) values.add(speaker.localId);
        return values;
    }

    private static String sha256(String value) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256")
                .digest(value.getBytes(StandardCharsets.UTF_8));
        StringBuilder result = new StringBuilder(64);
        for (byte item : digest) result.append(String.format(Locale.US, "%02x", item & 0xff));
        return result.toString();
    }

    private static String safeMessage(Throwable error) {
        String message = error.getMessage();
        return message == null ? "no_message" : message.replace('\n', ' ').replace('\r', ' ');
    }

    private void restoreVolume() {
        if (originalMusicVolume < 0) return;
        AudioManager audio = (AudioManager) getSystemService(AUDIO_SERVICE);
        audio.setStreamVolume(AudioManager.STREAM_MUSIC, originalMusicVolume, 0);
        originalMusicVolume = -1;
    }

    private void releasePlayer() {
        if (player != null) player.release();
        player = null;
    }

    private void releaseWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        wakeLock = null;
    }

    @Override
    protected void onDestroy() {
        releasePlayer();
        restoreVolume();
        releaseWakeLock();
        worker.shutdownNow();
        super.onDestroy();
    }
}
