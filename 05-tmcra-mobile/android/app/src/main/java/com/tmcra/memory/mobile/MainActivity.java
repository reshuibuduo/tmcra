package com.tmcra.memory.mobile;

import android.Manifest;
import android.app.AlertDialog;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.Switch;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import androidx.core.splashscreen.SplashScreen;

import com.tmcra.memory.mobile.audio.AudioCaptureService;
import com.tmcra.memory.mobile.data.AudioMemoryStore;
import com.tmcra.memory.mobile.net.TmcraApiClient;

import java.io.File;
import java.util.ArrayList;
import java.util.List;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends AppCompatActivity {
    private static final int PERMISSION_REQUEST = 4001;

    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private LinearLayout loginPanel;
    private LinearLayout dashboardPanel;
    private LinearLayout recentSegments;
    private LinearLayout speakerProfiles;
    private EditText emailInput;
    private EditText passwordInput;
    private Button loginButton;
    private Button logoutButton;
    private Button captureButton;
    private TextView loginStatus;
    private TextView accountName;
    private TextView accountScope;
    private TextView captureState;
    private TextView captureDetail;
    private TextView pipelineStage;
    private TextView partialTranscript;
    private TextView stableTranscript;
    private TextView recallState;
    private TextView deliveryState;
    private ProgressBar audioLevel;
    private Switch remoteFallback;
    private TmcraApiClient api;
    private AudioMemoryStore store;
    private SharedPreferences preferences;
    private boolean captureRunning;

    private final BroadcastReceiver captureReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (!AudioCaptureService.ACTION_STATE.equals(intent.getAction())) return;
            captureRunning = intent.getBooleanExtra(AudioCaptureService.EXTRA_RUNNING, false);
            int level = intent.getIntExtra(AudioCaptureService.EXTRA_LEVEL, 0);
            String detail = intent.getStringExtra(AudioCaptureService.EXTRA_DETAIL);
            audioLevel.setProgress(level, true);
            captureState.setText(captureRunning ? "音频记忆正在运行" : "音频记忆已停止");
            if (intent.hasExtra(AudioCaptureService.EXTRA_DETAIL)) {
                captureDetail.setText(detail == null ? "" : detail);
            }
            captureButton.setText(captureRunning ? "停止音频记忆" : "开始音频记忆");
            String stage = intent.getStringExtra(AudioCaptureService.EXTRA_STAGE);
            if (stage != null && !"speech".equals(stage) && !"listening".equals(stage)) {
                pipelineStage.setText(stageLabel(stage));
            }
            if (intent.hasExtra(AudioCaptureService.EXTRA_PARTIAL_TEXT)) {
                String partial = intent.getStringExtra(AudioCaptureService.EXTRA_PARTIAL_TEXT);
                partialTranscript.setText(partial == null || partial.isEmpty()
                        ? "本地实时草稿会显示在这里" : "本地实时：" + partial);
            }
            if (intent.hasExtra(AudioCaptureService.EXTRA_STABLE_TEXT)) {
                String stable = intent.getStringExtra(AudioCaptureService.EXTRA_STABLE_TEXT);
                stableTranscript.setText(stable == null || stable.isEmpty()
                        ? "最终文本尚未确定" : stable);
            }
            if (intent.hasExtra(AudioCaptureService.EXTRA_RECALL_STATUS)) {
                recallState.setText("召回：" + recallStatusLabel(
                        intent.getStringExtra(AudioCaptureService.EXTRA_RECALL_STATUS)));
            }
            if (intent.hasExtra(AudioCaptureService.EXTRA_DELIVERY_STATUS)) {
                deliveryState.setText("提醒：" + deliveryStatusLabel(
                        intent.getStringExtra(AudioCaptureService.EXTRA_DELIVERY_STATUS)));
            }
            if (stage != null && !"speech".equals(stage) && !"listening".equals(stage)
                    && !"local_partial".equals(stage)) refreshRecent();
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        // Resolve Theme.SplashScreen to the AppCompat post-splash theme before
        // AppCompatActivity creates its delegate and inflates the first view.
        SplashScreen.installSplashScreen(this);
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        bindViews();
        api = new TmcraApiClient(this);
        store = new AudioMemoryStore(this);
        preferences = getSharedPreferences(AudioCaptureService.PREFERENCES, Context.MODE_PRIVATE);
        boolean remoteReviewEnabled = preferences.contains(AudioCaptureService.PREF_REMOTE_REVIEW)
                ? preferences.getBoolean(AudioCaptureService.PREF_REMOTE_REVIEW, false)
                : preferences.getBoolean(AudioCaptureService.PREF_REMOTE_FALLBACK, false);
        remoteFallback.setChecked(remoteReviewEnabled);
        remoteFallback.setOnCheckedChangeListener((button, checked) -> {
            if (!checked) {
                preferences.edit()
                        .putBoolean(AudioCaptureService.PREF_REMOTE_REVIEW, false)
                        .putBoolean(AudioCaptureService.PREF_REMOTE_FALLBACK, false)
                        .apply();
                return;
            }
            if (preferences.getBoolean(AudioCaptureService.PREF_REMOTE_REVIEW, false)) return;
            button.setChecked(false);
            new AlertDialog.Builder(this)
                    .setTitle("开启 Qwen 高精度复核？")
                    .setMessage("开启后，每个有效语音片段都会临时上传用于 Qwen3-ASR 复核并产生服务用量。本地草稿与远端结果会分别保留；数字、时间和已确认姓名冲突时暂停写入。原始音频不进入记忆索引，声纹模板不上传。")
                    .setNegativeButton("保持仅本地", null)
                    .setPositiveButton("开启复核", (dialog, which) -> {
                        preferences.edit()
                                .putBoolean(AudioCaptureService.PREF_REMOTE_REVIEW, true)
                                .putBoolean(AudioCaptureService.PREF_REMOTE_FALLBACK, false)
                                .apply();
                        remoteFallback.setChecked(true);
                    })
                    .show();
        });
        loginButton.setOnClickListener(view -> login());
        logoutButton.setOnClickListener(view -> logout());
        captureButton.setOnClickListener(view -> toggleCapture());
        ContextCompat.registerReceiver(
                this,
                captureReceiver,
                new IntentFilter(AudioCaptureService.ACTION_STATE),
                ContextCompat.RECEIVER_NOT_EXPORTED);
        restoreSession();
    }

    @Override
    protected void onDestroy() {
        unregisterReceiver(captureReceiver);
        io.shutdown();
        store.close();
        super.onDestroy();
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode,
            @NonNull String[] permissions,
            @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != PERMISSION_REQUEST) return;
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED) {
            startCapture();
        } else {
            captureDetail.setText("没有麦克风权限，音频记忆无法启动。");
        }
    }

    private void bindViews() {
        loginPanel = findViewById(R.id.login_panel);
        dashboardPanel = findViewById(R.id.dashboard_panel);
        recentSegments = findViewById(R.id.recent_segments);
        speakerProfiles = findViewById(R.id.speaker_profiles);
        emailInput = findViewById(R.id.email_input);
        passwordInput = findViewById(R.id.password_input);
        loginButton = findViewById(R.id.login_button);
        logoutButton = findViewById(R.id.logout_button);
        captureButton = findViewById(R.id.capture_button);
        loginStatus = findViewById(R.id.login_status);
        accountName = findViewById(R.id.account_name);
        accountScope = findViewById(R.id.account_scope);
        captureState = findViewById(R.id.capture_state);
        captureDetail = findViewById(R.id.capture_detail);
        pipelineStage = findViewById(R.id.pipeline_stage);
        partialTranscript = findViewById(R.id.partial_transcript);
        stableTranscript = findViewById(R.id.stable_transcript);
        recallState = findViewById(R.id.recall_state);
        deliveryState = findViewById(R.id.delivery_state);
        audioLevel = findViewById(R.id.audio_level);
        remoteFallback = findViewById(R.id.remote_fallback_switch);
    }

    private void restoreSession() {
        if (!api.sessionStore().hasSession()) {
            showLogin();
            return;
        }
        loginStatus.setText("正在恢复安全会话…");
        io.execute(() -> {
            try {
                TmcraApiClient.Account account = api.currentAccount();
                TmcraApiClient.PersonalSnapshot snapshot = api.personalSnapshot();
                preferences.edit()
                        .putString(AudioCaptureService.PREF_SCOPE_NAMESPACE, snapshot.scopeNamespace)
                        .apply();
                syncPendingSpeakerMappings();
                runOnUiThread(() -> showDashboard(account, snapshot));
            } catch (Exception error) {
                runOnUiThread(() -> {
                    showLogin();
                    loginStatus.setText("会话已失效，请重新登录。");
                });
            }
        });
    }

    private void login() {
        String email = emailInput.getText().toString().trim();
        String password = passwordInput.getText().toString();
        if (email.isEmpty() || password.isEmpty()) {
            loginStatus.setText("请输入邮箱和密码。");
            return;
        }
        loginButton.setEnabled(false);
        loginStatus.setText("正在登录并连接个人记忆空间…");
        io.execute(() -> {
            try {
                TmcraApiClient.Account account = api.login(email, password);
                TmcraApiClient.PersonalSnapshot snapshot = api.personalSnapshot();
                preferences.edit()
                        .putString(AudioCaptureService.PREF_SCOPE_NAMESPACE, snapshot.scopeNamespace)
                        .apply();
                syncPendingSpeakerMappings();
                runOnUiThread(() -> {
                    passwordInput.setText("");
                    loginButton.setEnabled(true);
                    showDashboard(account, snapshot);
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    loginButton.setEnabled(true);
                    loginStatus.setText(publicError(error));
                });
            }
        });
    }

    private void logout() {
        stopCapture();
        logoutButton.setEnabled(false);
        io.execute(() -> {
            try {
                api.logout();
            } catch (Exception ignored) {
                api.sessionStore().clear();
            }
            preferences.edit().remove(AudioCaptureService.PREF_SCOPE_NAMESPACE).apply();
            runOnUiThread(() -> {
                logoutButton.setEnabled(true);
                showLogin();
            });
        });
    }

    private void toggleCapture() {
        if (captureRunning) {
            stopCapture();
            return;
        }
        if (!hasCapturePermissions()) {
            requestCapturePermissions();
            return;
        }
        startCapture();
    }

    private boolean hasCapturePermissions() {
        return ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
    }

    private void requestCapturePermissions() {
        java.util.ArrayList<String> required = new java.util.ArrayList<>();
        required.add(Manifest.permission.RECORD_AUDIO);
        if (Build.VERSION.SDK_INT >= 33) required.add(Manifest.permission.POST_NOTIFICATIONS);
        if (Build.VERSION.SDK_INT >= 31) required.add(Manifest.permission.BLUETOOTH_CONNECT);
        ActivityCompat.requestPermissions(this, required.toArray(new String[0]), PERMISSION_REQUEST);
    }

    private void startCapture() {
        captureDetail.setText("正在启动本地录音与 VAD…");
        ContextCompat.startForegroundService(
                this,
                new Intent(this, AudioCaptureService.class).setAction(AudioCaptureService.ACTION_START));
    }

    private void stopCapture() {
        startService(new Intent(this, AudioCaptureService.class).setAction(AudioCaptureService.ACTION_STOP));
    }

    private void showLogin() {
        loginPanel.setVisibility(View.VISIBLE);
        dashboardPanel.setVisibility(View.GONE);
        captureRunning = false;
        loginStatus.setText("");
    }

    private void showDashboard(
            TmcraApiClient.Account account,
            TmcraApiClient.PersonalSnapshot snapshot) {
        loginPanel.setVisibility(View.GONE);
        dashboardPanel.setVisibility(View.VISIBLE);
        accountName.setText(account.displayName == null || account.displayName.isEmpty()
                ? account.email
                : account.displayName);
        accountScope.setText(snapshot.audioProjectScope());
        refreshRecent();
    }

    private void refreshRecent() {
        io.execute(() -> {
            List<AudioMemoryStore.Segment> segments = store.recent(12);
            List<AudioMemoryStore.SpeakerProfile> speakers = store.speakers();
            Map<String, Integer> speakerCounts = new HashMap<>();
            for (AudioMemoryStore.SpeakerProfile speaker : speakers) {
                speakerCounts.put(speaker.localId, store.segmentCountForSpeaker(speaker.localId));
            }
            runOnUiThread(() -> {
                renderRecent(segments);
                renderSpeakers(speakers, speakerCounts);
            });
        });
    }

    private void renderRecent(List<AudioMemoryStore.Segment> segments) {
        recentSegments.removeAllViews();
        if (segments.isEmpty()) {
            TextView empty = textView("还没有捕获到有效语音。", 14, getColor(R.color.tmcra_muted));
            empty.setPadding(0, dp(12), 0, dp(12));
            recentSegments.addView(empty);
            return;
        }
        for (AudioMemoryStore.Segment segment : segments) {
            LinearLayout card = new LinearLayout(this);
            card.setOrientation(LinearLayout.VERTICAL);
            card.setPadding(dp(16), dp(14), dp(16), dp(14));
            GradientDrawable background = new GradientDrawable();
            background.setColor(Color.rgb(251, 249, 245));
            background.setCornerRadius(dp(12));
            background.setStroke(dp(1), getColor(R.color.tmcra_line));
            card.setBackground(background);
            LinearLayout.LayoutParams cardParams = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT);
            cardParams.bottomMargin = dp(10);
            card.setLayoutParams(cardParams);

            String status = statusLabel(segment.state);
            String speaker = segment.speakerLabel != null && !segment.speakerLabel.isEmpty()
                    ? segment.speakerLabel
                    : "self".equals(segment.speakerRelation)
                    ? "我"
                    : "unknown".equals(segment.speakerRelation) ? "未知说话人" : segment.speakerRelation;
            TextView meta = textView(
                    status + "  ·  " + speaker + "  ·  " + durationLabel(segment.durationMs),
                    12,
                    getColor("uploaded".equals(segment.state) ? R.color.tmcra_signal : R.color.tmcra_muted));
            card.addView(meta);
            TextView transcript = textView(
                    segment.transcript == null
                            ? "review_required".equals(segment.state)
                            ? "最终文本已暂停，等待你的确认"
                            : "等待最终文本…"
                            : segment.transcript,
                    15,
                    getColor(R.color.tmcra_ink));
            transcript.setPadding(0, dp(8), 0, 0);
            transcript.setLineSpacing(0, 1.18f);
            card.addView(transcript);
            if (segment.localTranscript != null) {
                TextView local = candidateView("本地草稿", segment.localTranscript);
                local.setPadding(0, dp(10), 0, 0);
                card.addView(local);
            }
            if (segment.remoteTranscript != null) {
                TextView remote = candidateView("Qwen 复核", segment.remoteTranscript);
                remote.setPadding(0, dp(5), 0, 0);
                card.addView(remote);
            }
            if (segment.resolutionStatus != null) {
                int color = "review_required".equals(segment.resolutionStatus)
                        ? getColor(R.color.tmcra_warning) : getColor(R.color.tmcra_signal);
                TextView resolution = textView(resolutionDetail(segment), 12, color);
                resolution.setPadding(0, dp(8), 0, 0);
                card.addView(resolution);
            }
            if (segment.recallStatus != null) {
                TextView recall = textView(
                        "召回 · " + recallStatusLabel(segment.recallStatus)
                                + (segment.recallReason == null ? "" : " · " + segment.recallReason),
                        12,
                        getColor(R.color.tmcra_muted));
                recall.setPadding(0, dp(8), 0, 0);
                card.addView(recall);
                if (segment.recallSummary != null && !segment.recallSummary.isEmpty()) {
                    TextView evidence = textView(segment.recallSummary, 12, getColor(R.color.tmcra_ink));
                    evidence.setPadding(dp(10), dp(7), dp(10), dp(7));
                    GradientDrawable evidenceBackground = new GradientDrawable();
                    evidenceBackground.setColor(Color.rgb(239, 241, 235));
                    evidenceBackground.setCornerRadius(dp(8));
                    evidence.setBackground(evidenceBackground);
                    card.addView(evidence);
                }
            }
            if (segment.deliveryStatus != null) {
                TextView delivery = textView(
                        "提醒 · " + deliveryStatusLabel(segment.deliveryStatus),
                        12,
                        getColor(R.color.tmcra_muted));
                delivery.setPadding(0, dp(6), 0, 0);
                card.addView(delivery);
            }
            if (segment.lastError != null) {
                TextView error = textView(segment.lastError, 12, getColor(R.color.tmcra_warning));
                error.setPadding(0, dp(7), 0, 0);
                card.addView(error);
            }
            if ("review_required".equals(segment.state)) {
                card.addView(reviewActions(segment));
            } else if ("remote_review_failed".equals(segment.state)
                    || "asr_unavailable".equals(segment.state)
                    || "asr_failed".equals(segment.state)) {
                Button retry = smallButton("重新用 Qwen 复核");
                retry.setOnClickListener(view -> retrySegment(segment.eventId));
                LinearLayout.LayoutParams retryParams = new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        dp(44));
                retryParams.topMargin = dp(10);
                retry.setLayoutParams(retryParams);
                card.addView(retry);
            }
            card.setOnLongClickListener(view -> {
                showSegmentActions(segment);
                return true;
            });
            recentSegments.addView(card);
        }
    }

    private TextView candidateView(String label, String value) {
        TextView view = textView(label + "｜" + value, 12, getColor(R.color.tmcra_muted));
        view.setLineSpacing(0, 1.12f);
        return view;
    }

    private String resolutionDetail(AudioMemoryStore.Segment segment) {
        if ("review_required".equals(segment.resolutionStatus)) {
            String reason = segment.resolutionReasons == null ? "" : segment.resolutionReasons;
            if (reason.contains("critical_number_or_time_conflict")) {
                return "需要确认 · 数字或时间不一致，当前没有写入记忆";
            }
            if (reason.contains("large_transcript_divergence")) {
                return "需要确认 · 两份文字差异较大，当前没有写入记忆";
            }
            return "需要确认 · 当前没有写入记忆";
        }
        String source = "agreement".equals(segment.resolutionSource)
                ? "两份结果一致"
                : "remote".equals(segment.resolutionSource)
                ? "采用 Qwen"
                : "local".equals(segment.resolutionSource)
                ? "采用本地"
                : "manual".equals(segment.resolutionSource) ? "人工确认" : "已确定";
        String confidence = "high".equals(segment.resolutionConfidence)
                ? "高置信"
                : "medium".equals(segment.resolutionConfidence) ? "中等置信" : "低置信";
        String similarity = segment.resolutionSimilarity == null
                ? "" : String.format(Locale.getDefault(), " · 相似度 %.0f%%", segment.resolutionSimilarity * 100);
        return source + " · " + confidence + similarity;
    }

    private LinearLayout reviewActions(AudioMemoryStore.Segment segment) {
        LinearLayout container = new LinearLayout(this);
        container.setOrientation(LinearLayout.VERTICAL);
        container.setPadding(0, dp(10), 0, 0);
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        Button local = smallButton("采用本地");
        Button remote = smallButton("采用 Qwen");
        LinearLayout.LayoutParams half = new LinearLayout.LayoutParams(0, dp(44), 1);
        half.rightMargin = dp(6);
        row.addView(local, half);
        LinearLayout.LayoutParams otherHalf = new LinearLayout.LayoutParams(0, dp(44), 1);
        otherHalf.leftMargin = dp(6);
        row.addView(remote, otherHalf);
        local.setEnabled(segment.localTranscript != null && !segment.localTranscript.isEmpty());
        remote.setEnabled(segment.remoteTranscript != null && !segment.remoteTranscript.isEmpty());
        local.setOnClickListener(view -> confirmTranscriptChoice(segment, "local"));
        remote.setOnClickListener(view -> confirmTranscriptChoice(segment, "remote"));
        container.addView(row);
        Button edit = smallButton("手动修订后写入");
        LinearLayout.LayoutParams full = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(44));
        full.topMargin = dp(8);
        container.addView(edit, full);
        edit.setOnClickListener(view -> editTranscript(segment));
        return container;
    }

    private void confirmTranscriptChoice(AudioMemoryStore.Segment segment, String source) {
        String selected = "local".equals(source) ? segment.localTranscript : segment.remoteTranscript;
        new AlertDialog.Builder(this)
                .setTitle("确认最终文字？")
                .setMessage(("local".equals(source) ? "采用本地草稿：\n\n" : "采用 Qwen 复核：\n\n") + selected)
                .setNegativeButton("返回比较", null)
                .setPositiveButton("确认并写入", (dialog, which) -> resolveReview(segment.eventId, source, null))
                .show();
    }

    private void editTranscript(AudioMemoryStore.Segment segment) {
        EditText input = new EditText(this);
        input.setMinLines(4);
        input.setMaxLines(10);
        input.setGravity(android.view.Gravity.TOP | android.view.Gravity.START);
        String starting = segment.remoteTranscript != null ? segment.remoteTranscript : segment.localTranscript;
        input.setText(starting == null ? "" : starting);
        input.setSelection(input.getText().length());
        LinearLayout container = new LinearLayout(this);
        container.setPadding(dp(18), dp(8), dp(18), 0);
        container.addView(input, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("修订最终文字")
                .setMessage("保存后，这一版会作为人工确认结果写入；两份原始候选仍保留在本机审计记录中。")
                .setView(container)
                .setNegativeButton("取消", null)
                .setPositiveButton("保存并写入", null)
                .create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(view -> {
                    String clean = input.getText().toString().trim();
                    if (clean.isEmpty() || clean.length() > 50_000) {
                        input.setError("请输入有效文字");
                        return;
                    }
                    dialog.dismiss();
                    resolveReview(segment.eventId, "manual", clean);
                }));
        dialog.show();
    }

    private void resolveReview(String eventId, String source, String transcript) {
        Intent intent = new Intent(this, AudioCaptureService.class)
                .setAction(AudioCaptureService.ACTION_RESOLVE_REVIEW)
                .putExtra(AudioCaptureService.EXTRA_EVENT_ID, eventId)
                .putExtra(AudioCaptureService.EXTRA_RESOLUTION_SOURCE, source);
        if (transcript != null) intent.putExtra(AudioCaptureService.EXTRA_MANUAL_TRANSCRIPT, transcript);
        startService(intent);
        captureDetail.setText("正在保存你的确认并执行召回…");
    }

    private void retrySegment(String eventId) {
        preferences.edit().putBoolean(AudioCaptureService.PREF_REMOTE_REVIEW, true).apply();
        remoteFallback.setChecked(true);
        startService(new Intent(this, AudioCaptureService.class)
                .setAction(AudioCaptureService.ACTION_RETRY_SEGMENT)
                .putExtra(AudioCaptureService.EXTRA_EVENT_ID, eventId));
        captureDetail.setText("正在重新请求 Qwen 高精度复核…");
    }

    private Button smallButton(String label) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setTextSize(13);
        button.setTextColor(getColor(R.color.tmcra_ink));
        button.setBackgroundTintList(android.content.res.ColorStateList.valueOf(getColor(R.color.tmcra_line)));
        return button;
    }

    private void renderSpeakers(
            List<AudioMemoryStore.SpeakerProfile> speakers,
            Map<String, Integer> counts) {
        speakerProfiles.removeAllViews();
        if (speakers.isEmpty()) {
            TextView empty = textView("尚未形成可用声纹。捕获到清晰语音后会自动建立。", 13, getColor(R.color.tmcra_muted));
            empty.setPadding(0, dp(8), 0, dp(8));
            speakerProfiles.addView(empty);
            return;
        }
        for (AudioMemoryStore.SpeakerProfile speaker : speakers) {
            LinearLayout card = new LinearLayout(this);
            card.setOrientation(LinearLayout.VERTICAL);
            card.setPadding(dp(14), dp(12), dp(14), dp(12));
            GradientDrawable background = new GradientDrawable();
            background.setColor(Color.rgb(251, 249, 245));
            background.setCornerRadius(dp(10));
            background.setStroke(dp(1), getColor(R.color.tmcra_line));
            card.setBackground(background);
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT);
            params.bottomMargin = dp(8);
            card.setLayoutParams(params);
            String label = speaker.displayName != null && !speaker.displayName.isEmpty()
                    ? speaker.displayName
                    : "self".equals(speaker.relation) ? "我" : "待命名声纹";
            card.addView(textView(label, 15, getColor(R.color.tmcra_ink)));
            String shortId = speaker.localId.length() > 18
                    ? "…" + speaker.localId.substring(speaker.localId.length() - 14)
                    : speaker.localId;
            TextView meta = textView(
                    shortId + " · " + counts.getOrDefault(speaker.localId, 0) + " 段 · "
                            + speaker.sampleCount + " 个模板样本 · "
                            + String.format(Locale.getDefault(), "%.0f%%", speaker.confidence * 100),
                    12,
                    getColor(R.color.tmcra_muted));
            meta.setPadding(0, dp(5), 0, 0);
            card.addView(meta);
            TextView sync = textView(speakerSyncLabel(speaker), 12,
                    "error".equals(speaker.syncState) ? getColor(R.color.tmcra_warning) : getColor(R.color.tmcra_signal));
            sync.setPadding(0, dp(4), 0, 0);
            card.addView(sync);
            card.setOnClickListener(view -> showSpeakerActions(speaker));
            speakerProfiles.addView(card);
        }
    }

    private String speakerSyncLabel(AudioMemoryStore.SpeakerProfile speaker) {
        if ("unknown".equals(speaker.relation)) return "仅本机临时身份 · 声纹未上传";
        if ("synced".equals(speaker.syncState)) return "身份标签已同步 · 声纹仍只在本机";
        if ("error".equals(speaker.syncState)) return "身份标签同步失败，可再次保存重试";
        return "身份标签等待同步 · 声纹仍只在本机";
    }

    private void showSpeakerActions(AudioMemoryStore.SpeakerProfile speaker) {
        new AlertDialog.Builder(this)
                .setTitle(speaker.displayName == null ? "管理待命名声纹" : "管理「" + speaker.displayName + "」")
                .setItems(new String[]{"标记为我", "命名这个人", "删除本机声纹模板"}, (dialog, which) -> {
                    if (which == 0) confirmSelfSpeaker(speaker.localId);
                    else if (which == 1) promptSpeakerName(speaker.localId, speaker.displayName);
                    else confirmSpeakerDelete(speaker);
                })
                .show();
    }

    private void confirmSpeakerDelete(AudioMemoryStore.SpeakerProfile speaker) {
        new AlertDialog.Builder(this)
                .setTitle("删除这个声纹模板？")
                .setMessage("模板会从本机删除，已有文字片段会保留并改回未知说话人。远端从未保存这份声纹向量。")
                .setNegativeButton("取消", null)
                .setPositiveButton("删除模板", (dialog, which) -> io.execute(() -> {
                    store.deleteSpeakerProfile(speaker.localId);
                    runOnUiThread(this::refreshRecent);
                }))
                .show();
    }

    private void showSegmentActions(AudioMemoryStore.Segment segment) {
        boolean hasVoiceprint = segment.speakerId != null && segment.speakerId.startsWith("spk_local_");
        ArrayList<String> actions = new ArrayList<>();
        if (hasVoiceprint) {
            actions.add("这是我说的");
            actions.add("给说话人命名");
        }
        if (segment.remoteMessageId != null && !segment.remoteMessageId.isEmpty()) {
            actions.add("删除 TMCRA 远端记忆与本地音频");
        }
        actions.add("只删除手机本地记录");
        new AlertDialog.Builder(this)
                .setTitle(hasVoiceprint ? "管理说话人" : "这段语音没有可用声纹")
                .setItems(actions.toArray(new String[0]), (dialog, which) -> {
                    String action = actions.get(which);
                    if ("这是我说的".equals(action)) {
                        confirmSelfSpeaker(segment);
                    } else if ("给说话人命名".equals(action)) {
                        promptSpeakerName(segment);
                    } else if (action.startsWith("删除 TMCRA")) {
                        confirmRemoteDelete(segment);
                    } else {
                        confirmLocalDelete(segment);
                    }
                })
                .show();
    }

    private void confirmRemoteDelete(AudioMemoryStore.Segment segment) {
        new AlertDialog.Builder(this)
                .setTitle("删除远端记忆？")
                .setMessage("TMCRA 会按这段音频的稳定 Message ID 精确删除对应 Source 记忆、关联的快速记忆和受影响的慢图内容。删除任务不可撤销；本地音频也会立即移除。")
                .setNegativeButton("取消", null)
                .setPositiveButton("确认删除", (dialog, which) -> io.execute(() -> {
                    try {
                        TmcraApiClient.DeletionReceipt receipt = api.deleteAudioMemory(
                                segment.eventId,
                                segment.scopeName,
                                segment.remoteMessageId);
                        if (segment.wavPath != null) new File(segment.wavPath).delete();
                        store.markRemoteDeleteRequested(segment.eventId, receipt.deletionId);
                        runOnUiThread(() -> {
                            captureDetail.setText("远端精确删除任务已提交，可在记录中查看状态。");
                            refreshRecent();
                        });
                    } catch (Exception error) {
                        store.markRemoteDeleteError(segment.eventId, error.getClass().getSimpleName());
                        runOnUiThread(() -> {
                            captureDetail.setText(publicError(error));
                            refreshRecent();
                        });
                    }
                }))
                .show();
    }

    private void confirmSelfSpeaker(AudioMemoryStore.Segment segment) {
        confirmSelfSpeaker(segment.speakerId);
    }

    private void confirmSelfSpeaker(String speakerId) {
        new AlertDialog.Builder(this)
                .setTitle("确认这是你的声音？")
                .setMessage("确认后，同一声纹的后续片段会以用户本人记录。低于匹配阈值的片段仍会保持未知。")
                .setNegativeButton("取消", null)
                .setPositiveButton("确认是我", (dialog, which) -> io.execute(() -> {
                    store.labelSpeaker(speakerId, "我", "self");
                    syncPendingSpeakerMappings();
                    runOnUiThread(this::refreshRecent);
                }))
                .show();
    }

    private void promptSpeakerName(AudioMemoryStore.Segment segment) {
        promptSpeakerName(segment.speakerId, segment.speakerLabel);
    }

    private void promptSpeakerName(String speakerId, String currentLabel) {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setHint("例如：王老师");
        input.setText(currentLabel == null ? "" : currentLabel);
        int padding = dp(20);
        LinearLayout container = new LinearLayout(this);
        container.setPadding(padding, dp(8), padding, 0);
        container.addView(input, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        new AlertDialog.Builder(this)
                .setTitle("命名本地说话人")
                .setView(container)
                .setNegativeButton("取消", null)
                .setPositiveButton("保存", (dialog, which) -> {
                    String label = input.getText().toString().trim();
                    if (label.isEmpty() || label.length() > 80) return;
                    io.execute(() -> {
                        int revision = store.labelSpeaker(speakerId, label, "known");
                        syncSpeakerMapping(speakerId, label, "known", revision);
                        runOnUiThread(this::refreshRecent);
                    });
                })
                .show();
    }

    private void syncPendingSpeakerMappings() {
        for (AudioMemoryStore.SpeakerProfile speaker : store.pendingSpeakerMappings()) {
            syncSpeakerMapping(
                    speaker.localId,
                    speaker.displayName,
                    speaker.relation,
                    speaker.mappingRevision);
        }
    }

    private void syncSpeakerMapping(String localId, String label, String relation, int revision) {
        try {
            api.submitSpeakerIdentity(localId, label, relation, revision);
            store.markSpeakerMappingSynced(localId, revision);
        } catch (Exception error) {
            store.markSpeakerMappingError(localId, revision, error.getClass().getSimpleName());
        }
    }

    private void confirmLocalDelete(AudioMemoryStore.Segment segment) {
        new AlertDialog.Builder(this)
                .setTitle("删除手机本地记录？")
                .setMessage("这会删除本地音频缓存和本地状态。已提交到 TMCRA 的远端文字记忆需要在记忆管理页单独删除。")
                .setNegativeButton("取消", null)
                .setPositiveButton("删除本地", (dialog, which) -> io.execute(() -> {
                    if (segment.wavPath != null) new File(segment.wavPath).delete();
                    store.deleteSegment(segment.eventId);
                    runOnUiThread(this::refreshRecent);
                }))
                .show();
    }

    private TextView textView(String text, int sizeSp, int color) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(sizeSp);
        view.setTextColor(color);
        return view;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static String durationLabel(int durationMs) {
        return String.format(Locale.getDefault(), "%.1f s", durationMs / 1_000.0);
    }

    private static String statusLabel(String state) {
        if ("uploaded".equals(state)) return "已写入 TMCRA";
        if ("resolved".equals(state)) return "最终文字已确定";
        if ("local_draft".equals(state)) return "本地草稿完成";
        if ("remote_reviewing".equals(state)) return "Qwen 正在复核";
        if ("review_required".equals(state)) return "等待人工确认";
        if ("remote_review_failed".equals(state)) return "远端复核待重试";
        if ("remote_delete_pending".equals(state)) return "远端删除处理中";
        if ("transcribed".equals(state)) return "等待上传";
        if ("captured".equals(state)) return "等待转写";
        if ("upload_failed".equals(state)) return "上传待重试";
        if ("asr_failed".equals(state) || "asr_unavailable".equals(state)) return "转写待处理";
        return state == null ? "本地记录" : state;
    }

    private static String stageLabel(String stage) {
        if ("captured".equals(stage)) return "1 / 5 语音已分段并识别说话人";
        if ("local_draft".equals(stage)) return "2 / 5 本地草稿已生成";
        if ("remote_reviewing".equals(stage)) return "3 / 5 Qwen 正在高精度复核";
        if ("review_required".equals(stage)) return "暂停 · 等待你确认文字冲突";
        if ("resolved".equals(stage)) return "4 / 5 最终文字已确定";
        if ("recalling".equals(stage)) return "5 / 5 正在召回并写入";
        if ("recalled".equals(stage)) return "完成 · 已返回召回结果并写入";
        if ("delivered".equals(stage)) return "完成 · 已主动提醒";
        if (stage != null && stage.endsWith("failed")) return "处理暂停 · 可重试";
        return "等待有效语音";
    }

    private static String recallStatusLabel(String status) {
        if ("matched".equals(status)) return "找到相关旧记忆";
        if ("empty".equals(status)) return "没有找到相关旧记忆";
        if ("failed".equals(status)) return "服务暂时失败";
        if ("review_required".equals(status)) return "文字确认前不会召回";
        return status == null ? "尚未开始" : status;
    }

    private static String deliveryStatusLabel(String status) {
        if ("scheduled".equals(status)) return "已按识别时间登记";
        if ("delivered".equals(status)) return "已发送通知；条件允许时通过耳机播报";
        if ("not_triggered".equals(status)) return "本轮条件不足，未打扰用户";
        return status == null ? "尚未触发" : status;
    }

    private static String publicError(Exception error) {
        if (error instanceof TmcraApiClient.ApiException) {
            TmcraApiClient.ApiException apiError = (TmcraApiClient.ApiException) error;
            if ("invalid_credentials".equals(apiError.code)) return "邮箱或密码不正确。";
            if ("unverified_account".equals(apiError.code)) return "邮箱尚未验证。";
            if ("rate_limited".equals(apiError.code)) return "尝试次数过多，请稍后再试。";
            return apiError.getMessage();
        }
        return "无法连接 TMCRA，请检查网络后重试。";
    }
}
