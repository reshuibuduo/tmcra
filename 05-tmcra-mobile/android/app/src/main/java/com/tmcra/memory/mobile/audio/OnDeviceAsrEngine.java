package com.tmcra.memory.mobile.audio;

import android.content.Context;

import com.k2fsa.sherpa.onnx.FeatureConfig;
import com.k2fsa.sherpa.onnx.OnlineModelConfig;
import com.k2fsa.sherpa.onnx.OnlineRecognizer;
import com.k2fsa.sherpa.onnx.OnlineRecognizerConfig;
import com.k2fsa.sherpa.onnx.OnlineRecognizerResult;
import com.k2fsa.sherpa.onnx.OnlineStream;
import com.k2fsa.sherpa.onnx.OnlineZipformer2CtcModelConfig;

import java.io.IOException;
import java.io.InputStream;
import java.util.concurrent.Executor;

/** App-owned streaming ASR. PCM stays on the device unless fallback is explicitly enabled. */
public final class OnDeviceAsrEngine implements AutoCloseable {
    public static final String MODEL_ID =
            "sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01";
    public static final String MODEL_NAME = "tmcra-asr-zipformer-small-zh-2025-04.int8.onnx";
    public static final String TOKENS_NAME = "tmcra-asr-zipformer-small-zh-2025-04.tokens.txt";

    public interface PartialCallback {
        void onPartial(String transcript);
    }

    public interface Callback {
        void onSuccess(String transcript, Double confidence);
        void onUnavailable(String reason);
    }

    private final Context context;
    private final Executor worker;
    private OnlineRecognizer recognizer;
    private OnlineStream stream;
    private String unavailableReason;
    private String lastPartial = "";
    private long lastPartialAtMs;
    private boolean closed;

    public OnDeviceAsrEngine(Context context, Executor worker) {
        this.context = context.getApplicationContext();
        this.worker = worker;
    }

    /** Initializes the model on the ASR worker before the first speech frame arrives. */
    public void preload() {
        if (!isAvailable()) return;
        worker.execute(this::getOrCreateRecognizer);
    }

    /** Fast asset probe; native model initialization remains off the main thread. */
    public boolean isAvailable() {
        return !closed && unavailableReason == null
                && assetExists(MODEL_NAME)
                && assetExists(TOKENS_NAME);
    }

    public String unavailableReason() {
        return unavailableReason == null ? "on_device_asr_unavailable" : unavailableReason;
    }

    /**
     * Queues one live PCM frame. Calls are decoded in order on a single worker so
     * the recognizer's recurrent state follows the microphone stream.
     */
    public void accept(short[] pcm, int sampleRate, PartialCallback callback) {
        if (pcm == null || pcm.length == 0 || sampleRate <= 0 || !isAvailable()) return;
        short[] copy = pcm.clone();
        worker.execute(() -> acceptFrame(copy, sampleRate, callback));
    }

    /** Finalizes the current streaming utterance and starts a fresh stream for the next one. */
    public void finish(Callback callback) {
        worker.execute(() -> finishStream(callback));
    }

    /**
     * Transcribes an already isolated speaker turn without disturbing the live microphone stream.
     * The shared worker preserves model access order while the temporary stream keeps the next
     * microphone utterance independent from this delayed diarization result.
     */
    public void transcribe(short[] pcm, int sampleRate, Callback callback) {
        if (callback == null) return;
        if (pcm == null || pcm.length == 0 || sampleRate <= 0) {
            callback.onUnavailable("on_device_asr_audio_empty");
            return;
        }
        short[] copy = pcm.clone();
        worker.execute(() -> transcribeOneShot(copy, sampleRate, callback));
    }

    private synchronized void acceptFrame(short[] pcm, int sampleRate, PartialCallback callback) {
        if (closed || unavailableReason != null) return;
        try {
            OnlineRecognizer active = getOrCreateRecognizer();
            if (active == null) return;
            if (stream == null) stream = active.createStream("");
            float[] samples = new float[pcm.length];
            for (int index = 0; index < pcm.length; index++) samples[index] = pcm[index] / 32768.0f;
            stream.acceptWaveform(samples, sampleRate);
            while (active.isReady(stream)) active.decode(stream);
            String partial = resultText(active.getResult(stream));
            long now = System.currentTimeMillis();
            if (!partial.isEmpty()
                    && !partial.equals(lastPartial)
                    && (now - lastPartialAtMs >= 120 || partial.length() >= lastPartial.length() + 3)) {
                lastPartial = partial;
                lastPartialAtMs = now;
                if (callback != null) callback.onPartial(partial);
            }
        } catch (Throwable error) {
            disable("on_device_asr_" + error.getClass().getSimpleName());
        }
    }

    private synchronized void finishStream(Callback callback) {
        if (closed || unavailableReason != null) {
            callback.onUnavailable(unavailableReason());
            resetStream();
            return;
        }
        try {
            OnlineRecognizer active = getOrCreateRecognizer();
            if (active == null || stream == null) {
                callback.onUnavailable(unavailableReason());
                resetStream();
                return;
            }
            stream.inputFinished();
            while (active.isReady(stream)) active.decode(stream);
            OnlineRecognizerResult result = active.getResult(stream);
            String text = resultText(result);
            if (text.isEmpty()) callback.onUnavailable("on_device_asr_empty");
            else callback.onSuccess(text, meanConfidence(result.getYsProbs()));
        } catch (Throwable error) {
            disable("on_device_asr_" + error.getClass().getSimpleName());
            callback.onUnavailable(unavailableReason());
        } finally {
            resetStream();
        }
    }

    private synchronized void transcribeOneShot(short[] pcm, int sampleRate, Callback callback) {
        if (closed || unavailableReason != null) {
            callback.onUnavailable(unavailableReason());
            return;
        }
        OnlineStream isolated = null;
        try {
            OnlineRecognizer active = getOrCreateRecognizer();
            if (active == null) {
                callback.onUnavailable(unavailableReason());
                return;
            }
            isolated = active.createStream("");
            float[] samples = new float[pcm.length];
            for (int index = 0; index < pcm.length; index++) samples[index] = pcm[index] / 32768.0f;
            isolated.acceptWaveform(samples, sampleRate);
            isolated.inputFinished();
            while (active.isReady(isolated)) active.decode(isolated);
            OnlineRecognizerResult result = active.getResult(isolated);
            String text = resultText(result);
            if (text.isEmpty()) callback.onUnavailable("on_device_asr_empty");
            else callback.onSuccess(text, meanConfidence(result.getYsProbs()));
        } catch (Throwable error) {
            disable("on_device_asr_" + error.getClass().getSimpleName());
            callback.onUnavailable(unavailableReason());
        } finally {
            if (isolated != null) isolated.release();
        }
    }

    private synchronized OnlineRecognizer getOrCreateRecognizer() {
        if (closed || unavailableReason != null) return null;
        if (recognizer != null) return recognizer;
        try {
            FeatureConfig features = new FeatureConfig();
            features.setSampleRate(16_000);
            features.setFeatureDim(80);
            features.setDither(0);

            OnlineModelConfig model = new OnlineModelConfig();
            model.setZipformer2Ctc(new OnlineZipformer2CtcModelConfig(MODEL_NAME));
            model.setTokens(TOKENS_NAME);
            model.setNumThreads(2);
            model.setDebug(false);
            model.setProvider("cpu");
            model.setModelingUnit("cjkchar");

            OnlineRecognizerConfig config = new OnlineRecognizerConfig();
            config.setFeatConfig(features);
            config.setModelConfig(model);
            config.setEnableEndpoint(false);
            config.setDecodingMethod("greedy_search");
            recognizer = new OnlineRecognizer(context.getAssets(), config);
            return recognizer;
        } catch (Throwable error) {
            disable("on_device_asr_initialization_" + error.getClass().getSimpleName());
            return null;
        }
    }

    private void disable(String reason) {
        unavailableReason = reason;
        resetStream();
        if (recognizer != null) recognizer.release();
        recognizer = null;
    }

    private void resetStream() {
        if (stream != null) stream.release();
        stream = null;
        lastPartial = "";
        lastPartialAtMs = 0;
    }

    private boolean assetExists(String name) {
        try (InputStream ignored = context.getAssets().open(name)) {
            return true;
        } catch (IOException error) {
            return false;
        }
    }

    private static String resultText(OnlineRecognizerResult result) {
        return result == null || result.getText() == null ? "" : result.getText().trim();
    }

    private static Double meanConfidence(float[] probabilities) {
        if (probabilities == null || probabilities.length == 0) return null;
        double sum = 0;
        int count = 0;
        for (float value : probabilities) {
            if (!Float.isFinite(value)) continue;
            sum += Math.max(0, Math.min(1, value));
            count++;
        }
        return count == 0 ? null : sum / count;
    }

    @Override
    public synchronized void close() {
        closed = true;
        resetStream();
        if (recognizer != null) recognizer.release();
        recognizer = null;
    }
}
