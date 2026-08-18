package com.tmcra.memory.mobile.audio;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import androidx.annotation.Nullable;
import androidx.core.app.ActivityCompat;
import androidx.core.app.NotificationCompat;

import com.tmcra.memory.mobile.MainActivity;
import com.tmcra.memory.mobile.R;
import com.tmcra.memory.mobile.data.AudioMemoryStore;
import com.tmcra.memory.mobile.net.TmcraApiClient;
import com.tmcra.memory.mobile.reminder.LocalRecallDecisionPolicy;
import com.tmcra.memory.mobile.reminder.ReminderScheduler;
import com.tmcra.memory.mobile.reminder.TriggerHintExtractor;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TimeZone;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

public final class AudioCaptureService extends Service {
    public static final String ACTION_START = "com.tmcra.memory.mobile.action.START_CAPTURE";
    public static final String ACTION_STOP = "com.tmcra.memory.mobile.action.STOP_CAPTURE";
    public static final String ACTION_RESOLVE_REVIEW = "com.tmcra.memory.mobile.action.RESOLVE_REVIEW";
    public static final String ACTION_RETRY_SEGMENT = "com.tmcra.memory.mobile.action.RETRY_SEGMENT";
    public static final String ACTION_STATE = "com.tmcra.memory.mobile.action.CAPTURE_STATE";
    public static final String EXTRA_RUNNING = "running";
    public static final String EXTRA_LEVEL = "level";
    public static final String EXTRA_DETAIL = "detail";
    public static final String EXTRA_EVENT_ID = "event_id";
    public static final String EXTRA_STAGE = "stage";
    public static final String EXTRA_PARTIAL_TEXT = "partial_text";
    public static final String EXTRA_STABLE_TEXT = "stable_text";
    public static final String EXTRA_RECALL_STATUS = "recall_status";
    public static final String EXTRA_DELIVERY_STATUS = "delivery_status";
    public static final String EXTRA_RESOLUTION_SOURCE = "resolution_source";
    public static final String EXTRA_MANUAL_TRANSCRIPT = "manual_transcript";
    public static final String PREFERENCES = "tmcra_audio_settings";
    public static final String PREF_REMOTE_FALLBACK = "remote_fallback";
    public static final String PREF_REMOTE_REVIEW = "remote_review";
    public static final String PREF_SCOPE_NAMESPACE = "scope_namespace";

    private static final String CHANNEL_ID = "tmcra-audio-capture";
    private static final int NOTIFICATION_ID = 4101;
    private static final int SAMPLE_RATE = 16_000;
    private static final AtomicLong LAST_VOICE_AT = new AtomicLong(0);

    private final ExecutorService processing = Executors.newFixedThreadPool(2);
    private final ExecutorService audioWriter = Executors.newSingleThreadExecutor();
    private volatile boolean running;
    private Thread captureThread;
    private AudioRecord recorder;
    private PowerManager.WakeLock wakeLock;
    private AudioMemoryStore store;
    private RollingAudioStore audioStore;
    private TmcraApiClient api;
    private OnDeviceAsrEngine localAsr;
    private Future<SpeakerIdentityEngine> speakerEngineFuture;
    private Future<SpeakerDiarizationEngine> diarizationEngineFuture;
    private SharedPreferences preferences;

    public static long lastVoiceAtMs() {
        return LAST_VOICE_AT.get();
    }

    @Override
    public void onCreate() {
        super.onCreate();
        store = new AudioMemoryStore(this);
        audioStore = new RollingAudioStore(this);
        api = new TmcraApiClient(this);
        localAsr = new OnDeviceAsrEngine(this, audioWriter);
        localAsr.preload();
        speakerEngineFuture = processing.submit(() -> new SpeakerIdentityEngine(this, store));
        diarizationEngineFuture = processing.submit(() -> new SpeakerDiarizationEngine(this));
        preferences = getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
        createChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? null : intent.getAction();
        if (ACTION_RESOLVE_REVIEW.equals(action)) {
            String eventId = intent.getStringExtra(EXTRA_EVENT_ID);
            String source = intent.getStringExtra(EXTRA_RESOLUTION_SOURCE);
            String manualTranscript = intent.getStringExtra(EXTRA_MANUAL_TRANSCRIPT);
            processing.execute(() -> {
                resolveReview(eventId, source, manualTranscript);
                if (!running) stopSelf(startId);
            });
            return START_NOT_STICKY;
        }
        if (ACTION_RETRY_SEGMENT.equals(action)) {
            String eventId = intent.getStringExtra(EXTRA_EVENT_ID);
            processing.execute(() -> {
                retrySegment(eventId);
                if (!running) stopSelf(startId);
            });
            return START_NOT_STICKY;
        }
        if (ACTION_STOP.equals(action)) {
            stopCapture();
            stopSelf();
            return START_NOT_STICKY;
        }
        if (ACTION_START.equals(action) && !running) startCapture();
        return START_NOT_STICKY;
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        stopCapture();
        closeSpeakerEngine();
        closeDiarizationEngine();
        if (localAsr != null) localAsr.close();
        localAsr = null;
        processing.shutdown();
        audioWriter.shutdown();
        store.close();
        super.onDestroy();
    }

    private void startCapture() {
        if (ActivityCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            broadcast(false, 0, "缺少麦克风权限");
            stopSelf();
            return;
        }
        String namespace = preferences.getString(PREF_SCOPE_NAMESPACE, "");
        if (namespace == null || namespace.trim().isEmpty() || !api.sessionStore().hasSession()) {
            broadcast(false, 0, "请先登录并完成个人空间初始化");
            stopSelf();
            return;
        }
        int minimum = AudioRecord.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT);
        if (minimum <= 0) {
            broadcast(false, 0, "设备不支持 16 kHz 单声道录音");
            stopSelf();
            return;
        }
        recorder = new AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                Math.max(minimum * 2, SAMPLE_RATE * 2));
        if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
            recorder.release();
            recorder = null;
            broadcast(false, 0, "录音设备初始化失败");
            stopSelf();
            return;
        }
        PendingIntent stopIntent = PendingIntent.getService(
                this,
                0,
                new Intent(this, AudioCaptureService.class).setAction(ACTION_STOP),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent openIntent = PendingIntent.getActivity(
                this,
                0,
                new Intent(this, MainActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        startForeground(NOTIFICATION_ID, new NotificationCompat.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentTitle("TMCRA 音频记忆正在运行")
                .setContentText("静音不会写入；原始音频保留在手机滚动缓存中")
                .setOngoing(true)
                .setContentIntent(openIntent)
                .addAction(0, "停止", stopIntent)
                .setCategory(NotificationCompat.CATEGORY_SERVICE)
                .build());
        PowerManager power = (PowerManager) getSystemService(Context.POWER_SERVICE);
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "tmcra:audio-capture");
        wakeLock.acquire();
        running = true;
        recorder.startRecording();
        captureThread = new Thread(this::recordLoop, "tmcra-audio-record");
        captureThread.start();
        broadcastStage(null, "listening", localAsr.isAvailable()
                ? "本地 ASR 已就绪，正在监听语音"
                : "本地 ASR 暂不可用；远端复核开启时可继续处理", null, null, null);
        processing.execute(this::flushPendingUploads);
    }

    private void stopCapture() {
        running = false;
        AudioRecord activeRecorder = recorder;
        recorder = null;
        if (activeRecorder != null) {
            try {
                activeRecorder.stop();
            } catch (IllegalStateException ignored) {
                // Recorder may already be stopped after an input-device error.
            }
            activeRecorder.release();
        }
        if (captureThread != null) {
            captureThread.interrupt();
            captureThread = null;
        }
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        wakeLock = null;
        stopForeground(STOP_FOREGROUND_REMOVE);
        broadcast(false, 0, "采集已停止；已转写的事件仍会保留待上传状态");
    }

    private void recordLoop() {
        VadSegmenter vad = new VadSegmenter(new VadSegmenter.Listener() {
            private long lastLevelBroadcast;

            @Override
            public void onLevel(float level, boolean speechActive) {
                if (speechActive) LAST_VOICE_AT.set(System.currentTimeMillis());
                long now = System.currentTimeMillis();
                if (now - lastLevelBroadcast >= 120) {
                    lastLevelBroadcast = now;
                    broadcastLevel(Math.round(level * 100), speechActive);
                }
            }

            @Override
            public void onSegment(short[] pcm, int sampleRate) {
                LAST_VOICE_AT.set(System.currentTimeMillis());
                Future<SegmentBatch> pending = processing.submit(() -> persistSegmentBatch(pcm, sampleRate));
                localAsr.finish(new OnDeviceAsrEngine.Callback() {
                    @Override
                    public void onSuccess(String transcript, Double confidence) {
                        processing.execute(() -> resolveAsrBatch(
                                pending,
                                transcript,
                                confidence,
                                null));
                    }

                    @Override
                    public void onUnavailable(String reason) {
                        processing.execute(() -> resolveAsrBatch(pending, null, null, reason));
                    }
                });
            }
        });
        short[] buffer = new short[640];
        while (running && recorder != null) {
            int count;
            try {
                count = recorder.read(buffer, 0, buffer.length, AudioRecord.READ_BLOCKING);
            } catch (RuntimeException error) {
                broadcast(false, 0, "录音输入中断");
                break;
            }
            if (count > 0) {
                short[] liveFrame = Arrays.copyOf(buffer, count);
                localAsr.accept(liveFrame, SAMPLE_RATE, partial -> broadcast(
                        true,
                        0,
                        null,
                        null,
                        "local_partial",
                        compactPartial(partial),
                        null,
                        null,
                        null));
                vad.accept(buffer, count);
            }
            else if (count < 0) {
                broadcast(false, 0, "录音输入返回错误 " + count);
                break;
            }
        }
        vad.finish();
        if (running) {
            running = false;
            stopSelf();
        }
    }

    private SegmentBatch persistSegmentBatch(short[] pcm, int sampleRate) {
        String namespace = preferences.getString(PREF_SCOPE_NAMESPACE, "");
        if (namespace == null || namespace.isEmpty()) return SegmentBatch.empty();
        SpeakerDiarizationEngine.Result diarization = diarizeSpeakers(pcm, sampleRate);
        if (diarization.available && diarization.speakerCount > 1) {
            List<SpeakerDiarizationEngine.Turn> turns = SpeakerDiarizationEngine.exclusiveTimeline(
                    diarization.turns);
            SegmentBatch split = persistSpeakerTurns(namespace, pcm, sampleRate, turns);
            if (!split.items.isEmpty()) return split;
        }
        SpeakerIdentityEngine.Result speaker = identifySpeaker(pcm, sampleRate);
        PendingAsr pending = persistSingleSegment(namespace, pcm, sampleRate, speaker);
        return pending == null ? SegmentBatch.empty() : SegmentBatch.single(pending);
    }

    private SegmentBatch persistSpeakerTurns(
            String namespace,
            short[] pcm,
            int sampleRate,
            List<SpeakerDiarizationEngine.Turn> turns) {
        String batchId = UUID.randomUUID().toString().replace("-", "");
        Map<Integer, List<short[]>> identityAudio = new HashMap<>();
        ArrayList<TurnAudio> materialized = new ArrayList<>();
        for (SpeakerDiarizationEngine.Turn turn : turns) {
            short[] turnPcm = crop(pcm, sampleRate, turn.startSeconds, turn.endSeconds);
            if (turnPcm.length * 1_000L / Math.max(1, sampleRate) < 120) continue;
            materialized.add(new TurnAudio(turn, turnPcm));
            if (!turn.overlap && turn.localSpeaker >= 0) {
                identityAudio.computeIfAbsent(turn.localSpeaker, ignored -> new ArrayList<>())
                        .add(turnPcm);
            }
        }
        if (materialized.isEmpty()) return SegmentBatch.empty();

        Map<Integer, SpeakerIdentityEngine.Result> identities = new HashMap<>();
        for (Map.Entry<Integer, List<short[]>> entry : identityAudio.entrySet()) {
            SpeakerIdentityEngine.Result identity = identifySpeaker(concat(entry.getValue()), sampleRate);
            if ("spk_unknown_default".equals(identity.localId)) {
                identity = SpeakerIdentityEngine.Result.unresolved(
                        "spk_window_" + batchId + "_s" + entry.getKey(),
                        identity.reason);
            }
            identities.put(entry.getKey(), identity);
        }

        ArrayList<PendingAsr> pending = new ArrayList<>();
        for (TurnAudio item : materialized) {
            SpeakerIdentityEngine.Result speaker;
            if (item.turn.overlap || item.turn.localSpeaker < 0) {
                speaker = SpeakerIdentityEngine.Result.unresolved(
                        "spk_overlap_" + batchId,
                        "overlapping_speakers");
            } else {
                speaker = identities.get(item.turn.localSpeaker);
                if (speaker == null) {
                    speaker = SpeakerIdentityEngine.Result.unresolved(
                            "spk_window_" + batchId + "_s" + item.turn.localSpeaker,
                            "diarized_turn_too_short");
                }
            }
            PendingAsr saved = persistSingleSegment(namespace, item.pcm, sampleRate, speaker);
            if (saved != null) pending.add(saved);
        }
        if (!pending.isEmpty()) {
            broadcastStage(
                    pending.get(0).segment.eventId,
                    "speaker_turns_ready",
                    "检测到多人对话，已按说话人切分为 " + pending.size() + " 段",
                    null,
                    null,
                    null);
        }
        return new SegmentBatch(pending, true);
    }

    private PendingAsr persistSingleSegment(
            String namespace,
            short[] pcm,
            int sampleRate,
            SpeakerIdentityEngine.Result speaker) {
        String eventId = "evt_" + UUID.randomUUID().toString().replace("-", "");
        String capturedAt = isoNow();
        String sessionId = "audio-day-" + localDay();
        try {
            File wav = audioStore.writeWav(eventId, pcm, sampleRate);
            AudioMemoryStore.Segment segment = new AudioMemoryStore.Segment();
            segment.eventId = eventId;
            segment.sessionId = sessionId;
            segment.scopeName = namespace + "-project-life-audio";
            segment.capturedAt = capturedAt;
            segment.durationMs = Math.max(100, pcm.length * 1_000 / sampleRate);
            segment.speakerId = speaker.localId;
            segment.speakerLabel = speaker.label;
            segment.speakerRelation = speaker.relation;
            segment.speakerConfidence = speaker.confidence;
            segment.wavPath = wav.getAbsolutePath();
            store.insertCaptured(segment);
            String speakerText = "overlapping_speakers".equals(speaker.reason)
                    ? "多人同时说话"
                    : speaker.label != null
                    ? speaker.label
                    : "unknown".equals(speaker.relation) ? "未知说话人" : speaker.relation;
            broadcastStage(
                    eventId,
                    "captured",
                    "已分出语音并归到「" + speakerText + "」，正在本地转写",
                    null,
                    null,
                    null);
            return new PendingAsr(segment, wav, pcm, sampleRate);
        } catch (Exception error) {
            broadcast(true, 0, "语音片段保存失败");
            return null;
        }
    }

    private void resolveAsrBatch(
            Future<SegmentBatch> future,
            String streamingTranscript,
            Double streamingConfidence,
            String streamingUnavailableReason) {
        SegmentBatch batch = awaitBatch(future);
        if (batch == null || batch.items.isEmpty()) return;
        if (!batch.split) {
            PendingAsr pending = batch.items.get(0);
            if (streamingUnavailableReason == null) {
                resolveLocalAsr(pending, streamingTranscript, streamingConfidence);
            } else {
                resolveUnavailableAsr(pending, streamingUnavailableReason);
            }
            return;
        }
        for (PendingAsr pending : batch.items) {
            localAsr.transcribe(pending.pcm, pending.sampleRate, new OnDeviceAsrEngine.Callback() {
                @Override
                public void onSuccess(String transcript, Double confidence) {
                    processing.execute(() -> resolveLocalAsr(pending, transcript, confidence));
                }

                @Override
                public void onUnavailable(String reason) {
                    processing.execute(() -> resolveUnavailableAsr(pending, reason));
                }
            });
        }
    }

    private SegmentBatch awaitBatch(Future<SegmentBatch> future) {
        try {
            return future.get(120, TimeUnit.SECONDS);
        } catch (Exception error) {
            broadcast(running, 0, "语音片段处理超时");
            return null;
        }
    }

    private void resolveLocalAsr(PendingAsr pending, String transcript, Double confidence) {
        String clean = transcript == null ? "" : transcript.trim();
        if (clean.isEmpty()) {
            resolveUnavailableAsr(pending, "local_transcript_empty");
            return;
        }
        String localSha256 = sha256(clean);
        store.markLocalDraft(
                pending.segment.eventId,
                clean,
                languageTag(),
                OnDeviceAsrEngine.MODEL_ID,
                confidence,
                localSha256);
        pending.segment.localTranscript = clean;
        pending.segment.localAsrModel = OnDeviceAsrEngine.MODEL_ID;
        pending.segment.localAsrConfidence = confidence;
        pending.segment.localAsrSha256 = localSha256;
        broadcastStage(
                pending.segment.eventId,
                "local_draft",
                remoteReviewEnabled() ? "本地草稿完成，正在请求 Qwen 高精度复核" : "本地转写完成",
                clean,
                null,
                null);
        if (remoteReviewEnabled()) {
            remoteReview(pending, clean, confidence, null);
            return;
        }
        finalizeResolvedTranscript(
                pending,
                clean,
                "on_device",
                OnDeviceAsrEngine.MODEL_ID,
                confidence,
                clean,
                OnDeviceAsrEngine.MODEL_ID,
                confidence,
                localSha256,
                null,
                null,
                null,
                null,
                "local",
                "medium",
                1.0,
                "[\"local_only_mode\"]",
                "[]");
    }

    private void resolveUnavailableAsr(PendingAsr pending, String reason) {
        if (remoteReviewEnabled()) {
            remoteReview(pending, null, null, reason);
        } else {
            store.markError(pending.segment.eventId, "asr_unavailable", reason);
            broadcastStage(
                    pending.segment.eventId,
                    "asr_unavailable",
                    "本地转写不可用，远端高精度复核已关闭",
                    null,
                    null,
                    null);
        }
    }

    private void remoteReview(
            PendingAsr pending,
            String localTranscript,
            Double localConfidence,
            String localReason) {
        store.markRemoteReviewing(pending.segment.eventId);
        broadcastStage(
                pending.segment.eventId,
                "remote_reviewing",
                "Qwen3-ASR 正在复核这段音频",
                localTranscript,
                null,
                null);
        try {
            TmcraApiClient.Transcription result = api.transcribe(
                    pending.wav,
                    pending.segment.scopeName,
                    pending.segment.sessionId,
                    languageTag(),
                    pending.segment.eventId,
                    localTranscript,
                    localTranscript == null ? null : OnDeviceAsrEngine.MODEL_ID,
                    localConfidence,
                    protectedTerms());
            TmcraApiClient.Candidate local = result.localCandidate;
            TmcraApiClient.Candidate remote = result.remoteCandidate;
            TmcraApiClient.Resolution resolution = result.resolution;
            if (remote == null || remote.text == null || remote.text.trim().isEmpty()) {
                throw new IllegalStateException("remote_transcript_missing");
            }
            if (resolution != null && "review_required".equals(resolution.status)) {
                store.markReviewRequired(
                        pending.segment.eventId,
                        result.language,
                        local == null ? localTranscript : local.text,
                        local == null ? OnDeviceAsrEngine.MODEL_ID : local.model,
                        local == null ? localConfidence : local.confidence,
                        local == null ? nullableSha(localTranscript) : local.sha256,
                        remote.text,
                        remote.model,
                        remote.provider,
                        remote.sha256,
                        resolution.similarity,
                        resolution.reasonsJson,
                        resolution.conflictsJson);
                broadcastStage(
                        pending.segment.eventId,
                        "review_required",
                        "两份转写存在关键冲突，请确认后再写入记忆",
                        null,
                        "review_required",
                        null);
                return;
            }
            String finalTranscript = resolution != null && resolution.finalTranscript != null
                    ? resolution.finalTranscript : result.text;
            finalizeResolvedTranscript(
                    pending,
                    finalTranscript,
                    local == null ? "remote_review" : "dual_review",
                    result.model,
                    null,
                    local == null ? null : local.text,
                    local == null ? null : local.model,
                    local == null ? null : local.confidence,
                    local == null ? null : local.sha256,
                    remote.text,
                    remote.model,
                    remote.provider,
                    remote.sha256,
                    resolution == null ? "remote" : resolution.selectedSource,
                    resolution == null ? "medium" : resolution.confidenceBand,
                    resolution == null ? null : resolution.similarity,
                    resolution == null ? "[\"remote_only\"]" : resolution.reasonsJson,
                    resolution == null ? "[]" : resolution.conflictsJson);
        } catch (Exception error) {
            store.markError(
                    pending.segment.eventId,
                    "remote_review_failed",
                    (localReason == null ? "remote_review" : localReason)
                            + ":" + transportFailure(error));
            broadcastStage(
                    pending.segment.eventId,
                    "remote_review_failed",
                    "远端复核失败，音频和本地草稿已保留，可稍后重试",
                    localTranscript,
                    null,
                    null);
        }
    }

    private void finalizeResolvedTranscript(
            PendingAsr pending,
            String finalTranscript,
            String asrMode,
            String asrModel,
            Double asrConfidence,
            String localTranscript,
            String localModel,
            Double localConfidence,
            String localSha256,
            String remoteTranscript,
            String remoteModel,
            String remoteProvider,
            String remoteSha256,
            String resolutionSource,
            String resolutionConfidence,
            Double resolutionSimilarity,
            String resolutionReasons,
            String resolutionConflicts) {
        String clean = finalTranscript == null ? "" : finalTranscript.trim();
        if (clean.isEmpty()) {
            store.markError(pending.segment.eventId, "asr_failed", "final_transcript_empty");
            return;
        }
        TriggerHintExtractor.Hints hints = TriggerHintExtractor.analyze(clean, System.currentTimeMillis());
        store.applyTranscriptResolution(
                pending.segment.eventId,
                clean,
                languageTag(),
                asrMode,
                asrModel,
                asrConfidence,
                localTranscript,
                localModel,
                localConfidence,
                localSha256,
                remoteTranscript,
                remoteModel,
                remoteProvider,
                remoteSha256,
                "resolved",
                resolutionSource,
                resolutionConfidence,
                resolutionSimilarity,
                resolutionReasons,
                resolutionConflicts,
                hints.commitment,
                hints.temporal,
                hints.person);
        pending.segment.transcript = clean;
        pending.segment.language = languageTag();
        pending.segment.asrMode = asrMode;
        pending.segment.asrModel = asrModel;
        pending.segment.asrConfidence = asrConfidence;
        pending.segment.localTranscript = localTranscript;
        pending.segment.localAsrModel = localModel;
        pending.segment.localAsrConfidence = localConfidence;
        pending.segment.localAsrSha256 = localSha256;
        pending.segment.remoteTranscript = remoteTranscript;
        pending.segment.remoteAsrModel = remoteModel;
        pending.segment.remoteAsrProvider = remoteProvider;
        pending.segment.remoteAsrSha256 = remoteSha256;
        pending.segment.resolutionStatus = "resolved";
        pending.segment.resolutionSource = resolutionSource;
        pending.segment.resolutionConfidence = resolutionConfidence;
        pending.segment.resolutionSimilarity = resolutionSimilarity;
        pending.segment.resolutionReasons = resolutionReasons;
        pending.segment.resolutionConflicts = resolutionConflicts;
        pending.segment.commitmentHint = hints.commitment;
        pending.segment.temporalHint = hints.temporal;
        pending.segment.personHint = hints.person;
        if (hints.commitment && hints.dueAtMs != null) {
            ReminderScheduler.schedule(
                    this,
                    "rem_" + pending.segment.eventId.substring(4),
                    pending.segment.eventId,
                    clean,
                    hints.dueAtMs);
            pending.segment.deliveryStatus = "scheduled";
        }
        broadcastStage(
                pending.segment.eventId,
                "resolved",
                resolutionLabel(resolutionSource, resolutionConfidence),
                clean,
                null,
                null);
        upload(pending.segment);
    }

    private void upload(AudioMemoryStore.Segment segment) {
        if (segment == null || segment.transcript == null
                || "review_required".equals(segment.resolutionStatus)) return;
        try {
            store.markPipelineStage(segment.eventId, "recalling");
            broadcastStage(segment.eventId, "recalling", "正在检索相关记忆并准备写入", segment.transcript, null, null);
            TmcraApiClient.AudioEvent event = toEvent(segment);
            TmcraApiClient.UploadReceipt receipt = api.submitEvent(event);
            store.markUploaded(segment.eventId, receipt.jobId, receipt.messageId);
            store.markRecallReceipt(
                    segment.eventId,
                    receipt.recallStatus,
                    receipt.recallCount,
                    receipt.recallSummary,
                    receipt.recallReason);
            broadcastStage(
                    segment.eventId,
                    "recalled",
                    recallLabel(receipt),
                    segment.transcript,
                    receipt.recallStatus,
                    null);
            maybeDeliverRecall(segment, receipt);
        } catch (Exception error) {
            store.markError(segment.eventId, "upload_failed", transportFailure(error));
            broadcastStage(
                    segment.eventId,
                    "upload_failed",
                    "文字记忆提交失败，已留在手机等待重试",
                    segment.transcript,
                    "failed",
                    null);
        }
    }

    private void flushPendingUploads() {
        List<AudioMemoryStore.Segment> pending = store.pendingUploads(30);
        for (AudioMemoryStore.Segment segment : pending) upload(segment);
        if (!pending.isEmpty()) {
            broadcastStage(null, "retry_complete", "待上传文字记忆已完成重试", null, null, null);
        }
    }

    private static TmcraApiClient.AudioEvent toEvent(AudioMemoryStore.Segment segment) {
        TmcraApiClient.AudioEvent event = new TmcraApiClient.AudioEvent();
        event.eventId = segment.eventId;
        event.sessionId = segment.sessionId;
        event.scopeName = segment.scopeName;
        event.capturedAt = segment.capturedAt;
        event.transcript = segment.transcript;
        event.durationMs = segment.durationMs;
        event.language = segment.language;
        event.speakerId = segment.speakerId;
        event.speakerLabel = segment.speakerLabel;
        event.speakerRelation = segment.speakerRelation;
        event.speakerConfidence = segment.speakerConfidence;
        event.asrMode = segment.asrMode;
        event.asrModel = segment.asrModel;
        event.asrConfidence = segment.asrConfidence;
        event.localAsrSha256 = segment.localAsrSha256;
        event.localAsrModel = segment.localAsrModel;
        event.localAsrConfidence = segment.localAsrConfidence;
        event.remoteAsrSha256 = segment.remoteAsrSha256;
        event.remoteAsrModel = segment.remoteAsrModel;
        event.remoteAsrProvider = segment.remoteAsrProvider;
        event.resolutionStatus = segment.resolutionStatus;
        event.resolutionSource = segment.resolutionSource;
        event.resolutionConfidence = segment.resolutionConfidence;
        event.resolutionSimilarity = segment.resolutionSimilarity;
        event.resolutionReasons = segment.resolutionReasons;
        event.commitmentHint = segment.commitmentHint;
        event.temporalHint = segment.temporalHint;
        event.personHint = segment.personHint;
        return event;
    }

    private void retrySegment(String eventId) {
        AudioMemoryStore.Segment segment = eventId == null ? null : store.segment(eventId);
        if (segment == null || segment.wavPath == null) {
            broadcastStage(eventId, "retry_failed", "找不到可重试的本地音频", null, null, null);
            return;
        }
        File wav = new File(segment.wavPath);
        if (!wav.isFile()) {
            store.markError(eventId, "retry_failed", "local_audio_missing");
            broadcastStage(eventId, "retry_failed", "本地音频已不在滚动缓存中", null, null, null);
            return;
        }
        remoteReview(
                new PendingAsr(segment, wav),
                segment.localTranscript,
                segment.localAsrConfidence,
                "manual_retry");
    }

    private void resolveReview(String eventId, String source, String manualTranscript) {
        AudioMemoryStore.Segment segment = eventId == null ? null : store.segment(eventId);
        if (segment == null || !"review_required".equals(segment.state)) {
            broadcastStage(eventId, "review_failed", "这段记录已不需要人工确认", null, null, null);
            return;
        }
        String selectedSource;
        String selected;
        String reason;
        if ("local".equals(source)) {
            selectedSource = "local";
            selected = segment.localTranscript;
            reason = "user_selected_local_candidate";
        } else if ("remote".equals(source)) {
            selectedSource = "remote";
            selected = segment.remoteTranscript;
            reason = "user_selected_remote_candidate";
        } else if ("manual".equals(source)) {
            selectedSource = "manual";
            selected = manualTranscript;
            reason = "user_edited_transcript";
        } else {
            broadcastStage(eventId, "review_failed", "确认来源无效", null, null, null);
            return;
        }
        String clean = selected == null ? "" : selected.trim();
        if (clean.isEmpty() || clean.length() > 50_000 || clean.indexOf('\0') >= 0) {
            broadcastStage(eventId, "review_failed", "确认后的文字无效", null, null, null);
            return;
        }
        File wav = segment.wavPath == null ? null : new File(segment.wavPath);
        finalizeResolvedTranscript(
                new PendingAsr(segment, wav),
                clean,
                "manual",
                "user-confirmed-transcript",
                "local".equals(selectedSource) ? segment.localAsrConfidence : null,
                segment.localTranscript,
                segment.localAsrModel,
                segment.localAsrConfidence,
                segment.localAsrSha256,
                segment.remoteTranscript,
                segment.remoteAsrModel,
                segment.remoteAsrProvider,
                segment.remoteAsrSha256,
                selectedSource,
                "high",
                segment.resolutionSimilarity,
                "[\"" + reason + "\"]",
                segment.resolutionConflicts == null ? "[]" : segment.resolutionConflicts);
    }

    private List<String> protectedTerms() {
        ArrayList<String> terms = new ArrayList<>();
        for (AudioMemoryStore.SpeakerProfile speaker : store.speakers()) {
            if (speaker.displayName == null) continue;
            String value = speaker.displayName.trim();
            if (!value.isEmpty() && value.length() <= 80 && !terms.contains(value)) terms.add(value);
            if (terms.size() >= 50) break;
        }
        return terms;
    }

    private boolean remoteReviewEnabled() {
        if (preferences.contains(PREF_REMOTE_REVIEW)) {
            return preferences.getBoolean(PREF_REMOTE_REVIEW, false);
        }
        return preferences.getBoolean(PREF_REMOTE_FALLBACK, false);
    }

    private void maybeDeliverRecall(
            AudioMemoryStore.Segment segment,
            TmcraApiClient.UploadReceipt receipt) {
        if ("scheduled".equals(segment.deliveryStatus)) {
            store.markDelivery(segment.eventId, "scheduled");
            broadcastStage(
                    segment.eventId,
                    "recalled",
                    recallLabel(receipt) + "；提醒已按识别出的时间登记",
                    segment.transcript,
                    receipt.recallStatus,
                    "scheduled");
            return;
        }
        LocalRecallDecisionPolicy.Decision decision = LocalRecallDecisionPolicy.evaluate(
                segment.transcript,
                receipt.recallStatus,
                receipt.recallSummary);
        if (!decision.deliver) {
            store.markDelivery(segment.eventId, "not_triggered");
            broadcastStage(
                    segment.eventId,
                    "recalled",
                    recallLabel(receipt) + "；当前无需主动提醒",
                    segment.transcript,
                    receipt.recallStatus,
                    "not_triggered");
            return;
        }
        String reminderId = "recall_" + segment.eventId.substring(4);
        ReminderScheduler.deliverImmediate(
                this,
                reminderId,
                segment.eventId,
                decision.message);
        store.markDelivery(segment.eventId, "delivered");
        broadcastStage(
                segment.eventId,
                "delivered",
                "相关记忆已通过通知提醒" + (decision.speakEligible ? "，耳机条件允许时会播报" : ""),
                segment.transcript,
                receipt.recallStatus,
                "delivered");
    }

    private static String resolutionLabel(String source, String confidence) {
        String sourceLabel = "agreement".equals(source)
                ? "本地与远端结果一致"
                : "remote".equals(source)
                ? "采用 Qwen 高精度结果"
                : "local".equals(source)
                ? "采用本地结果"
                : "已由用户确认";
        String confidenceLabel = "high".equals(confidence)
                ? "高置信"
                : "medium".equals(confidence) ? "中等置信" : "低置信";
        return sourceLabel + " · " + confidenceLabel + "，准备召回";
    }

    private static String recallLabel(TmcraApiClient.UploadReceipt receipt) {
        if ("matched".equals(receipt.recallStatus)) {
            return "已从 " + Math.max(1, receipt.recallCount) + " 个记忆范围找到相关证据，文字已写入";
        }
        if ("empty".equals(receipt.recallStatus)) return "本轮没有找到相关旧记忆，文字已写入";
        return "召回暂时失败，文字写入任务已提交";
    }

    private static String nullableSha(String value) {
        return value == null || value.trim().isEmpty() ? null : sha256(value.trim());
    }

    private static String transportFailure(Exception error) {
        if (error instanceof TmcraApiClient.ApiException) {
            TmcraApiClient.ApiException apiError = (TmcraApiClient.ApiException) error;
            String code = apiError.code == null || apiError.code.isEmpty()
                    ? "request_failed" : apiError.code;
            return "http_" + apiError.status + ":" + code;
        }
        return error == null ? "unknown_failure" : error.getClass().getSimpleName();
    }

    private SpeakerIdentityEngine.Result identifySpeaker(short[] pcm, int sampleRate) {
        Future<SpeakerIdentityEngine> future = speakerEngineFuture;
        if (future == null) return SpeakerIdentityEngine.Result.unresolved("model_not_started");
        try {
            SpeakerIdentityEngine engine = future.get(15, TimeUnit.SECONDS);
            return engine.identify(pcm, sampleRate);
        } catch (Exception error) {
            return SpeakerIdentityEngine.Result.unresolved("model_not_ready");
        }
    }

    private SpeakerDiarizationEngine.Result diarizeSpeakers(short[] pcm, int sampleRate) {
        Future<SpeakerDiarizationEngine> future = diarizationEngineFuture;
        if (future == null) return SpeakerDiarizationEngine.Result.unavailable("model_not_started");
        try {
            SpeakerDiarizationEngine engine = future.get(15, TimeUnit.SECONDS);
            return engine.diarize(pcm, sampleRate);
        } catch (Exception error) {
            return SpeakerDiarizationEngine.Result.unavailable("model_not_ready");
        }
    }

    private void closeSpeakerEngine() {
        Future<SpeakerIdentityEngine> future = speakerEngineFuture;
        speakerEngineFuture = null;
        if (future == null) return;
        if (!future.isDone()) {
            future.cancel(true);
            return;
        }
        try {
            future.get().close();
        } catch (Exception ignored) {
            // Model initialization may have failed before service shutdown.
        }
    }

    private void closeDiarizationEngine() {
        Future<SpeakerDiarizationEngine> future = diarizationEngineFuture;
        diarizationEngineFuture = null;
        if (future == null) return;
        if (!future.isDone()) {
            future.cancel(true);
            return;
        }
        try {
            future.get().close();
        } catch (Exception ignored) {
            // Model initialization may have failed before service shutdown.
        }
    }

    private static short[] crop(
            short[] pcm,
            int sampleRate,
            float startSeconds,
            float endSeconds) {
        int start = Math.max(0, Math.min(pcm.length, Math.round(startSeconds * sampleRate)));
        int end = Math.max(start, Math.min(pcm.length, Math.round(endSeconds * sampleRate)));
        return Arrays.copyOfRange(pcm, start, end);
    }

    private static short[] concat(List<short[]> chunks) {
        long total = 0;
        for (short[] chunk : chunks) total += chunk == null ? 0 : chunk.length;
        int length = (int) Math.min(Integer.MAX_VALUE, total);
        short[] result = new short[length];
        int offset = 0;
        for (short[] chunk : chunks) {
            if (chunk == null || chunk.length == 0 || offset >= result.length) continue;
            int count = Math.min(chunk.length, result.length - offset);
            System.arraycopy(chunk, 0, result, offset, count);
            offset += count;
        }
        return result;
    }

    private void broadcast(boolean isRunning, int level, String detail) {
        broadcast(isRunning, level, detail, null, null, null, null, null, null);
    }

    private void broadcast(
            boolean isRunning,
            int level,
            String detail,
            String eventId,
            String stage,
            String partialText,
            String stableText,
            String recallStatus,
            String deliveryStatus) {
        Intent intent = new Intent(ACTION_STATE)
                .setPackage(getPackageName())
                .putExtra(EXTRA_RUNNING, isRunning)
                .putExtra(EXTRA_LEVEL, Math.max(0, Math.min(100, level)));
        if (detail != null) intent.putExtra(EXTRA_DETAIL, detail);
        if (eventId != null) intent.putExtra(EXTRA_EVENT_ID, eventId);
        if (stage != null) intent.putExtra(EXTRA_STAGE, stage);
        if (partialText != null) intent.putExtra(EXTRA_PARTIAL_TEXT, partialText);
        if (stableText != null) intent.putExtra(EXTRA_STABLE_TEXT, stableText);
        if (recallStatus != null) intent.putExtra(EXTRA_RECALL_STATUS, recallStatus);
        if (deliveryStatus != null) intent.putExtra(EXTRA_DELIVERY_STATUS, deliveryStatus);
        sendBroadcast(intent);
    }

    private void broadcastLevel(int level, boolean speechActive) {
        broadcast(running, level, null, null, speechActive ? "speech" : "listening", null, null, null, null);
    }

    private void broadcastStage(
            String eventId,
            String stage,
            String detail,
            String stableText,
            String recallStatus,
            String deliveryStatus) {
        broadcast(running, 0, detail, eventId, stage, null, stableText, recallStatus, deliveryStatus);
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager manager = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                getString(R.string.capture_channel_name),
                NotificationManager.IMPORTANCE_LOW);
        channel.setDescription(getString(R.string.capture_channel_description));
        channel.setSound(null, null);
        manager.createNotificationChannel(channel);
    }

    private static String isoNow() {
        SimpleDateFormat format = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US);
        format.setTimeZone(TimeZone.getTimeZone("UTC"));
        return format.format(new Date());
    }

    private static String localDay() {
        return new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date());
    }

    private static String languageTag() {
        String tag = Locale.getDefault().toLanguageTag();
        return tag == null || tag.isEmpty() ? "zh-CN" : tag;
    }

    private static String compactPartial(String value) {
        String clean = value == null ? "" : value.trim();
        return clean.length() <= 42 ? clean : clean.substring(clean.length() - 42);
    }

    private static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder(64);
            for (byte item : digest) result.append(String.format(Locale.US, "%02x", item & 0xff));
            return result.toString();
        } catch (Exception error) {
            throw new IllegalStateException("SHA-256 unavailable", error);
        }
    }

    private static final class PendingAsr {
        final AudioMemoryStore.Segment segment;
        final File wav;
        final short[] pcm;
        final int sampleRate;

        PendingAsr(AudioMemoryStore.Segment segment, File wav) {
            this(segment, wav, null, SAMPLE_RATE);
        }

        PendingAsr(AudioMemoryStore.Segment segment, File wav, short[] pcm, int sampleRate) {
            this.segment = segment;
            this.wav = wav;
            this.pcm = pcm;
            this.sampleRate = sampleRate;
        }
    }

    private static final class SegmentBatch {
        final List<PendingAsr> items;
        final boolean split;

        SegmentBatch(List<PendingAsr> items, boolean split) {
            this.items = items;
            this.split = split;
        }

        static SegmentBatch empty() {
            return new SegmentBatch(new ArrayList<>(), false);
        }

        static SegmentBatch single(PendingAsr item) {
            ArrayList<PendingAsr> items = new ArrayList<>();
            items.add(item);
            return new SegmentBatch(items, false);
        }
    }

    private static final class TurnAudio {
        final SpeakerDiarizationEngine.Turn turn;
        final short[] pcm;

        TurnAudio(SpeakerDiarizationEngine.Turn turn, short[] pcm) {
            this.turn = turn;
            this.pcm = pcm;
        }
    }
}
