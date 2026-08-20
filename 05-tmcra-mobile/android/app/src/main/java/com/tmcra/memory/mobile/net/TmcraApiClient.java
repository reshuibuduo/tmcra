package com.tmcra.memory.mobile.net;

import android.content.Context;

import com.tmcra.memory.mobile.security.SessionStore;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class TmcraApiClient {
    public static final String BASE_URL = "https://tmcra.com";
    public static final String CLIENT_VERSION = "0.3.0-rc2";
    private static final String CLIENT_HEADER = "com.tmcra.memory.mobile/" + CLIENT_VERSION;
    private static final int MAX_RESPONSE_BYTES = 4 * 1024 * 1024;
    private static final Pattern SESSION_COOKIE = Pattern.compile(
            "(?:^|,\\s*)(__Host-tmcra_session=[A-Za-z0-9_-]{20,180})(?:;|$)");

    private final SessionStore sessionStore;

    public TmcraApiClient(Context context) {
        sessionStore = new SessionStore(context);
    }

    public SessionStore sessionStore() {
        return sessionStore;
    }

    public Account login(String email, String password) throws IOException, JSONException {
        JSONObject body = new JSONObject().put("email", email).put("password", password);
        Response response = request("/api/auth/v1/sessions", "POST", body, false, null);
        captureSession(response.headers);
        return Account.from(response.json.getJSONObject("account"));
    }

    public Account currentAccount() throws IOException, JSONException {
        Response response = request("/api/auth/v1/sessions", "GET", null, true, null);
        return Account.from(response.json.getJSONObject("account"));
    }

    public void logout() throws IOException, JSONException {
        try {
            request("/api/auth/v1/sessions", "DELETE", null, true, null);
        } finally {
            sessionStore.clear();
        }
    }

    public PersonalSnapshot personalSnapshot() throws IOException, JSONException {
        Response response = request("/api/personal", "GET", null, true, null);
        JSONObject space = response.json.getJSONObject("space");
        return new PersonalSnapshot(
                space.getString("id"),
                space.getString("scopeName"),
                space.optString("displayName", "Personal memory"));
    }

    public Transcription transcribe(
            File wavFile,
            String scopeName,
            String sessionId,
            String language,
            String eventId,
            String localTranscript,
            String localModel,
            Double localConfidence,
            List<String> protectedTerms)
            throws IOException, JSONException {
        if (!wavFile.isFile() || wavFile.length() < 45) {
            throw new IOException("Recorded WAV file is missing");
        }
        String boundary = "tmcra-mobile-" + UUID.randomUUID();
        URL url = checkedUrl("/api/personal/audio-memory/transcribe");
        HttpURLConnection connection = open(url, "POST", true);
        connection.setConnectTimeout(20_000);
        connection.setReadTimeout(130_000);
        connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        connection.setDoOutput(true);
        try (DataOutputStream output = new DataOutputStream(connection.getOutputStream())) {
            writeFormField(output, boundary, "scopeName", scopeName);
            writeFormField(output, boundary, "sessionId", sessionId);
            if (language != null && !language.isEmpty()) writeFormField(output, boundary, "language", language);
            if (eventId != null && !eventId.isEmpty()) writeFormField(output, boundary, "eventId", eventId);
            if (localTranscript != null && !localTranscript.trim().isEmpty()) {
                writeFormField(output, boundary, "localTranscript", localTranscript.trim());
                if (localModel != null && !localModel.isEmpty()) {
                    writeFormField(output, boundary, "localModel", localModel);
                }
                if (localConfidence != null) {
                    writeFormField(output, boundary, "localConfidence", String.valueOf(localConfidence));
                }
            }
            if (protectedTerms != null && !protectedTerms.isEmpty()) {
                JSONArray terms = new JSONArray();
                for (String term : protectedTerms) {
                    if (term != null && !term.trim().isEmpty()) terms.put(term.trim());
                }
                if (terms.length() > 0) {
                    writeFormField(output, boundary, "protectedTerms", terms.toString());
                }
            }
            output.writeBytes("--" + boundary + "\r\n");
            output.writeBytes("Content-Disposition: form-data; name=\"audio\"; filename=\"segment.wav\"\r\n");
            output.writeBytes("Content-Type: audio/wav\r\n\r\n");
            try (InputStream input = new BufferedInputStream(new FileInputStream(wavFile))) {
                byte[] buffer = new byte[16 * 1024];
                int count;
                while ((count = input.read(buffer)) >= 0) output.write(buffer, 0, count);
            }
            output.writeBytes("\r\n--" + boundary + "--\r\n");
        }
        Response response = read(connection, true);
        Candidate localCandidate = Candidate.from(response.json.optJSONObject("localCandidate"));
        Candidate remoteCandidate = Candidate.from(response.json.optJSONObject("remoteCandidate"));
        Resolution resolution = Resolution.from(response.json.optJSONObject("resolution"));
        return new Transcription(
                response.json.getString("transcript"),
                response.json.optString("language", language == null ? "" : language),
                response.json.optString("provider", "tmcra-qwen3-asr"),
                response.json.optString("model", "Qwen3-ASR-0.6B-bf16"),
                localCandidate,
                remoteCandidate,
                resolution,
                JSONObject.NULL.equals(response.json.opt("usage")) ? null : response.json.optJSONObject("usage"));
    }

    public UploadReceipt submitEvent(AudioEvent event) throws IOException, JSONException {
        JSONObject body = new JSONObject()
                .put("eventId", event.eventId)
                .put("sessionId", event.sessionId)
                .put("scopeName", event.scopeName)
                .put("capturedAt", event.capturedAt)
                .put("transcript", event.transcript)
                .put("durationMs", event.durationMs)
                .put("language", event.language == null ? JSONObject.NULL : event.language)
                .put("speaker", new JSONObject()
                        .put("localId", event.speakerId)
                        .put("label", event.speakerLabel == null ? JSONObject.NULL : event.speakerLabel)
                        .put("relation", event.speakerRelation)
                        .put("confidence", event.speakerConfidence))
                .put("asr", asrPayload(event))
                .put("hints", new JSONObject()
                        .put("commitment", event.commitmentHint)
                        .put("temporal", event.temporalHint)
                        .put("person", event.personHint))
                .put("client", new JSONObject()
                        .put("platform", "android")
                        .put("version", CLIENT_VERSION));
        Response response = request("/api/personal/audio-memory/events", "POST", body, true, null);
        JSONObject write = response.json.getJSONObject("write");
        JSONArray recalls = response.json.optJSONArray("recalls");
        JSONArray contexts = response.json.optJSONArray("context");
        RecallDigest recall = recallDigest(recalls, contexts);
        return new UploadReceipt(
                write.optString("jobId", ""),
                write.optString("status", "submitted"),
                write.optString("messageId", response.json.optString("messageId", "")),
                recall.status,
                recall.count,
                recall.summary,
                recall.reason);
    }

    private static JSONObject asrPayload(AudioEvent event) throws JSONException {
        JSONObject value = new JSONObject()
                .put("mode", event.asrMode)
                .put("model", event.asrModel == null ? JSONObject.NULL : event.asrModel)
                .put("confidence", event.asrConfidence == null ? JSONObject.NULL : event.asrConfidence);
        if (event.localAsrSha256 != null) {
            value.put("local", new JSONObject()
                    .put("sha256", event.localAsrSha256)
                    .put("model", event.localAsrModel == null ? JSONObject.NULL : event.localAsrModel)
                    .put("confidence", event.localAsrConfidence == null
                            ? JSONObject.NULL : event.localAsrConfidence));
        }
        if (event.remoteAsrSha256 != null) {
            value.put("remote", new JSONObject()
                    .put("sha256", event.remoteAsrSha256)
                    .put("model", event.remoteAsrModel == null ? JSONObject.NULL : event.remoteAsrModel)
                    .put("provider", event.remoteAsrProvider == null
                            ? JSONObject.NULL : event.remoteAsrProvider)
                    .put("confidence", JSONObject.NULL));
        }
        if (event.resolutionStatus != null) {
            JSONArray reasons = parseArray(event.resolutionReasons);
            if (reasons.length() == 0) reasons.put("client_confirmed_transcript");
            value.put("resolution", new JSONObject()
                    .put("status", event.resolutionStatus)
                    .put("selectedSource", event.resolutionSource)
                    .put("confidenceBand", event.resolutionConfidence)
                    .put("similarity", event.resolutionSimilarity == null
                            ? JSONObject.NULL : event.resolutionSimilarity)
                    .put("reasons", reasons));
        }
        return value;
    }

    private static JSONArray parseArray(String value) {
        if (value == null || value.isEmpty()) return new JSONArray();
        try {
            return new JSONArray(value);
        } catch (JSONException ignored) {
            return new JSONArray();
        }
    }

    private static RecallDigest recallDigest(JSONArray recalls, JSONArray contexts) {
        int completed = 0;
        int failed = 0;
        int matched = 0;
        StringBuilder scopes = new StringBuilder();
        if (recalls != null) {
            for (int index = 0; index < recalls.length(); index++) {
                JSONObject receipt = recalls.optJSONObject(index);
                if (receipt == null) continue;
                String status = receipt.optString("status", "failed");
                if ("completed".equals(status)) completed += 1;
                else failed += 1;
                if (receipt.optBoolean("evidenceAvailable", false)) matched += 1;
                if (scopes.length() > 0) scopes.append("；");
                scopes.append(shortScope(receipt.optString("scopeName", "")))
                        .append(receipt.optBoolean("evidenceAvailable", false) ? "有相关证据" : "未命中");
            }
        }
        String summary = "";
        if (contexts != null) {
            for (int index = 0; index < contexts.length(); index++) {
                JSONObject context = contexts.optJSONObject(index);
                if (context == null) continue;
                String compact = compactEvidence(context.optString("content", ""));
                if (compact.isEmpty()) continue;
                if (!summary.isEmpty()) summary += "\n";
                summary += shortScope(context.optString("scopeName", "")) + "：" + compact;
                if (summary.length() >= 480) {
                    summary = summary.substring(0, 480);
                    break;
                }
            }
        }
        String status = matched > 0 ? "matched" : completed > 0 ? "empty" : "failed";
        String reason = scopes.length() > 0
                ? scopes.toString()
                : failed > 0 ? "召回服务暂时不可用" : "没有返回召回回执";
        return new RecallDigest(status, matched, summary, reason);
    }

    private static String compactEvidence(String value) {
        String clean = value == null ? "" : value
                .replaceAll("(?m)^#{1,6}\\s*", "")
                .replaceAll("(?m)^[-*]\\s+", "")
                .replaceAll("\\s+", " ")
                .trim();
        return clean.length() <= 220 ? clean : clean.substring(0, 220) + "…";
    }

    private static String shortScope(String value) {
        if (value == null || value.isEmpty()) return "记忆";
        if (value.endsWith("-global")) return "个人基础记忆";
        if (value.endsWith("-project-life-audio")) return "生活音频项目";
        return "当前项目";
    }

    public void submitSpeakerIdentity(
            String localId,
            String label,
            String relation,
            int revision) throws IOException, JSONException {
        JSONObject body = new JSONObject()
                .put("localId", localId)
                .put("label", label)
                .put("relation", relation)
                .put("revision", revision)
                .put("client", new JSONObject()
                        .put("platform", "android")
                        .put("version", CLIENT_VERSION));
        request("/api/personal/audio-memory/speakers", "POST", body, true, null);
    }

    public DeletionReceipt deleteAudioMemory(
            String eventId,
            String scopeName,
            String messageId) throws IOException, JSONException {
        JSONObject body = new JSONObject()
                .put("eventId", eventId)
                .put("scopeName", scopeName)
                .put("messageId", messageId);
        Response response = request(
                "/api/personal/audio-memory/delete",
                "POST",
                body,
                true,
                null);
        JSONObject deletion = response.json.getJSONObject("deletion");
        return new DeletionReceipt(
                deletion.optString("deletion_id", ""),
                deletion.optString("job_id", ""),
                deletion.optString("status", "submitted"));
    }

    private Response request(
            String path,
            String method,
            JSONObject body,
            boolean authenticated,
            String idempotencyKey) throws IOException, JSONException {
        HttpURLConnection connection = open(checkedUrl(path), method, authenticated);
        if (idempotencyKey != null) connection.setRequestProperty("Idempotency-Key", idempotencyKey);
        if (body != null) {
            byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            connection.setFixedLengthStreamingMode(bytes.length);
            connection.setDoOutput(true);
            try (OutputStream output = connection.getOutputStream()) {
                output.write(bytes);
            }
        }
        return read(connection, authenticated);
    }

    private HttpURLConnection open(URL url, String method, boolean authenticated) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setRequestMethod(method);
        connection.setConnectTimeout(15_000);
        connection.setReadTimeout(35_000);
        connection.setInstanceFollowRedirects(false);
        connection.setUseCaches(false);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("Origin", BASE_URL);
        connection.setRequestProperty("X-TMCRA-Mobile-Client", CLIENT_HEADER);
        if (authenticated) {
            String cookie = sessionStore.load();
            if (cookie == null) throw new ApiException(401, "authentication_required", "请先登录 TMCRA。", null);
            connection.setRequestProperty("Cookie", cookie);
        }
        return connection;
    }

    private Response read(HttpURLConnection connection, boolean authenticated) throws IOException, JSONException {
        int status = connection.getResponseCode();
        InputStream stream = status >= 200 && status < 400
                ? connection.getInputStream()
                : connection.getErrorStream();
        byte[] bytes = readBounded(stream);
        String text = new String(bytes, StandardCharsets.UTF_8);
        JSONObject json;
        try {
            json = text.isEmpty() ? new JSONObject() : new JSONObject(text);
        } catch (JSONException error) {
            String responseType = connection.getHeaderField("Content-Type");
            String code = responseType != null && responseType.toLowerCase(Locale.ROOT).contains("text/html")
                    ? "html_response" : "invalid_response";
            throw new ApiException(status, code, "TMCRA 返回了无法读取的响应。", null);
        }
        Map<String, List<String>> headers = connection.getHeaderFields();
        String requestId = json.optString("requestId", null);
        if (status < 200 || status >= 300) {
            if (status == 401 && authenticated) sessionStore.clear();
            JSONObject error = json.optJSONObject("error");
            String code = error == null ? "request_failed" : error.optString("code", "request_failed");
            String message = error == null ? "TMCRA 请求失败。" : error.optString("message", "TMCRA 请求失败。");
            if (error != null) requestId = error.optString("requestId", requestId);
            throw new ApiException(status, code, message, requestId);
        }
        return new Response(json, headers);
    }

    private void captureSession(Map<String, List<String>> headers) throws IOException {
        for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
            if (entry.getKey() == null || !"set-cookie".equalsIgnoreCase(entry.getKey())) continue;
            for (String value : entry.getValue()) {
                Matcher matcher = SESSION_COOKIE.matcher(value);
                if (!matcher.find()) continue;
                try {
                    sessionStore.save(matcher.group(1));
                    return;
                } catch (Exception error) {
                    throw new IOException("Unable to protect TMCRA session", error);
                }
            }
        }
        throw new ApiException(502, "missing_session_cookie", "登录成功但没有收到安全会话。", null);
    }

    private URL checkedUrl(String path) throws IOException {
        URL url = new URL(BASE_URL + path);
        if (!"https".equals(url.getProtocol()) || !"tmcra.com".equals(url.getHost()) || url.getUserInfo() != null) {
            throw new IOException("TMCRA endpoint escaped the trusted origin");
        }
        return url;
    }

    private static byte[] readBounded(InputStream input) throws IOException {
        if (input == null) return new byte[0];
        try (InputStream stream = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8 * 1024];
            int count;
            int total = 0;
            while ((count = stream.read(buffer)) >= 0) {
                total += count;
                if (total > MAX_RESPONSE_BYTES) throw new IOException("TMCRA response is too large");
                output.write(buffer, 0, count);
            }
            return output.toByteArray();
        }
    }

    private static void writeFormField(DataOutputStream output, String boundary, String name, String value)
            throws IOException {
        output.writeBytes("--" + boundary + "\r\n");
        output.writeBytes("Content-Disposition: form-data; name=\"" + name + "\"\r\n\r\n");
        output.write(value.getBytes(StandardCharsets.UTF_8));
        output.writeBytes("\r\n");
    }

    private static final class Response {
        final JSONObject json;
        final Map<String, List<String>> headers;

        Response(JSONObject json, Map<String, List<String>> headers) {
            this.json = json;
            this.headers = headers;
        }
    }

    public static final class Account {
        public final String email;
        public final String displayName;

        Account(String email, String displayName) {
            this.email = email;
            this.displayName = displayName;
        }

        static Account from(JSONObject value) {
            String email = value.optString("email", "");
            String displayName = value.optString("displayName", value.optString("fullName", email));
            return new Account(email, displayName.isEmpty() ? email : displayName);
        }
    }

    public static final class PersonalSnapshot {
        public final String spaceId;
        public final String scopeNamespace;
        public final String displayName;

        PersonalSnapshot(String spaceId, String scopeNamespace, String displayName) {
            this.spaceId = spaceId;
            this.scopeNamespace = scopeNamespace;
            this.displayName = displayName;
        }

        public String audioProjectScope() {
            return scopeNamespace + "-project-life-audio";
        }
    }

    public static final class Transcription {
        public final String text;
        public final String language;
        public final String provider;
        public final String model;
        public final Candidate localCandidate;
        public final Candidate remoteCandidate;
        public final Resolution resolution;
        public final JSONObject usage;

        Transcription(
                String text,
                String language,
                String provider,
                String model,
                Candidate localCandidate,
                Candidate remoteCandidate,
                Resolution resolution,
                JSONObject usage) {
            this.text = text;
            this.language = language;
            this.provider = provider;
            this.model = model;
            this.localCandidate = localCandidate;
            this.remoteCandidate = remoteCandidate;
            this.resolution = resolution;
            this.usage = usage;
        }
    }

    public static final class Candidate {
        public final String text;
        public final String sha256;
        public final String model;
        public final String provider;
        public final Double confidence;

        Candidate(String text, String sha256, String model, String provider, Double confidence) {
            this.text = text;
            this.sha256 = sha256;
            this.model = model;
            this.provider = provider;
            this.confidence = confidence;
        }

        static Candidate from(JSONObject value) {
            if (value == null) return null;
            Double confidence = value.isNull("confidence") ? null : value.optDouble("confidence");
            return new Candidate(
                    value.optString("text", ""),
                    value.optString("sha256", ""),
                    value.optString("model", ""),
                    value.optString("provider", ""),
                    confidence);
        }
    }

    public static final class Resolution {
        public final String status;
        public final String selectedSource;
        public final String confidenceBand;
        public final Double similarity;
        public final String reasonsJson;
        public final String conflictsJson;
        public final String finalTranscript;

        Resolution(
                String status,
                String selectedSource,
                String confidenceBand,
                Double similarity,
                String reasonsJson,
                String conflictsJson,
                String finalTranscript) {
            this.status = status;
            this.selectedSource = selectedSource;
            this.confidenceBand = confidenceBand;
            this.similarity = similarity;
            this.reasonsJson = reasonsJson;
            this.conflictsJson = conflictsJson;
            this.finalTranscript = finalTranscript;
        }

        static Resolution from(JSONObject value) {
            if (value == null) {
                return new Resolution(
                        "resolved", "remote", "medium", null,
                        new JSONArray().put("remote_only").toString(),
                        new JSONArray().toString(), null);
            }
            Double similarity = value.isNull("similarity") ? null : value.optDouble("similarity");
            return new Resolution(
                    value.optString("status", "review_required"),
                    value.optString("selectedSource", "none"),
                    value.optString("confidenceBand", "low"),
                    similarity,
                    value.optJSONArray("reasons") == null
                            ? new JSONArray().toString() : value.optJSONArray("reasons").toString(),
                    value.optJSONArray("criticalConflicts") == null
                            ? new JSONArray().toString() : value.optJSONArray("criticalConflicts").toString(),
                    value.isNull("finalTranscript") ? null : value.optString("finalTranscript", null));
        }
    }

    public static final class AudioEvent {
        public String eventId;
        public String sessionId;
        public String scopeName;
        public String capturedAt;
        public String transcript;
        public int durationMs;
        public String language;
        public String speakerId;
        public String speakerLabel;
        public String speakerRelation;
        public double speakerConfidence;
        public String asrMode;
        public String asrModel;
        public Double asrConfidence;
        public String localAsrSha256;
        public String localAsrModel;
        public Double localAsrConfidence;
        public String remoteAsrSha256;
        public String remoteAsrModel;
        public String remoteAsrProvider;
        public String resolutionStatus;
        public String resolutionSource;
        public String resolutionConfidence;
        public Double resolutionSimilarity;
        public String resolutionReasons;
        public boolean commitmentHint;
        public boolean temporalHint;
        public boolean personHint;
    }

    public static final class UploadReceipt {
        public final String jobId;
        public final String status;
        public final String messageId;
        public final String recallStatus;
        public final int recallCount;
        public final String recallSummary;
        public final String recallReason;

        UploadReceipt(
                String jobId,
                String status,
                String messageId,
                String recallStatus,
                int recallCount,
                String recallSummary,
                String recallReason) {
            this.jobId = jobId;
            this.status = status;
            this.messageId = messageId;
            this.recallStatus = recallStatus;
            this.recallCount = recallCount;
            this.recallSummary = recallSummary;
            this.recallReason = recallReason;
        }
    }

    private static final class RecallDigest {
        final String status;
        final int count;
        final String summary;
        final String reason;

        RecallDigest(String status, int count, String summary, String reason) {
            this.status = status;
            this.count = count;
            this.summary = summary;
            this.reason = reason;
        }
    }

    public static final class DeletionReceipt {
        public final String deletionId;
        public final String jobId;
        public final String status;

        DeletionReceipt(String deletionId, String jobId, String status) {
            this.deletionId = deletionId;
            this.jobId = jobId;
            this.status = status;
        }
    }

    public static final class ApiException extends IOException {
        public final int status;
        public final String code;
        public final String requestId;

        ApiException(int status, String code, String message, String requestId) {
            super(message);
            this.status = status;
            this.code = code;
            this.requestId = requestId;
        }
    }
}
