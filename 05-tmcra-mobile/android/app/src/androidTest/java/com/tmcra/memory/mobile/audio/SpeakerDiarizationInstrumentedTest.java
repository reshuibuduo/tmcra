package com.tmcra.memory.mobile.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.util.Log;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;

import org.junit.Test;
import org.junit.runner.RunWith;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Locale;

@RunWith(AndroidJUnit4.class)
public final class SpeakerDiarizationInstrumentedTest {
    private static final String TAG = "TMCRA_DIARIZATION_TEST";

    @Test
    public void officialFourSpeakerChineseFixtureRunsLocally() throws Exception {
        Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
        File fixture = new File(context.getFilesDir(), "fixtures/0-four-speakers-zh.wav");
        assertTrue("Push the licensed fixture into app files before the test", fixture.isFile());
        WavData wav = readPcm16Mono(fixture);
        assertEquals(16_000, wav.sampleRate);

        long initializeStarted = System.nanoTime();
        SpeakerDiarizationEngine engine = new SpeakerDiarizationEngine(context, 0.90f, 4);
        long initializeMs = elapsedMs(initializeStarted);
        try {
            assertTrue("Diarizer unavailable: " + engine.unavailableReason(), engine.isAvailable());
            long processStarted = System.nanoTime();
            SpeakerDiarizationEngine.Result result = engine.diarize(wav.pcm, wav.sampleRate);
            long processMs = elapsedMs(processStarted);
            assertTrue("Diarization failed: " + result.reason, result.available);
            for (SpeakerDiarizationEngine.Turn turn : result.turns) {
                Log.i(TAG, String.format(
                        Locale.US,
                        "turn speaker=%d start=%.3f end=%.3f duration=%.3f overlap=%s",
                        turn.localSpeaker,
                        turn.startSeconds,
                        turn.endSeconds,
                        turn.durationSeconds(),
                        turn.overlap));
            }
            double audioSeconds = wav.pcm.length / (double) wav.sampleRate;
            double realTimeFactor = processMs / (audioSeconds * 1_000.0);
            Log.i(TAG, String.format(
                    Locale.US,
                    "summary speakers=%d turns=%d audio_sec=%.3f init_ms=%d process_ms=%d rtf=%.4f",
                    result.speakerCount,
                    result.turns.size(),
                    audioSeconds,
                    initializeMs,
                    processMs,
                    realTimeFactor));
            assertEquals("Official fixture contains four speakers", 4, result.speakerCount);
            assertTrue("Expected multiple diarized turns", result.turns.size() >= 4);
        } finally {
            engine.close();
        }
    }

    private static long elapsedMs(long startedNs) {
        return Math.round((System.nanoTime() - startedNs) / 1_000_000.0);
    }

    private static WavData readPcm16Mono(File file) throws IOException {
        byte[] bytes;
        try (FileInputStream input = new FileInputStream(file);
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[64 * 1024];
            int count;
            while ((count = input.read(buffer)) >= 0) output.write(buffer, 0, count);
            bytes = output.toByteArray();
        }
        ByteBuffer reader = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
        assertChunk(reader, "RIFF");
        reader.getInt();
        assertChunk(reader, "WAVE");
        int sampleRate = 0;
        int channels = 0;
        int bits = 0;
        byte[] pcmBytes = null;
        while (reader.remaining() >= 8) {
            String chunk = readAscii(reader, 4);
            int size = reader.getInt();
            if (size < 0 || size > reader.remaining()) throw new IOException("Invalid WAV chunk " + chunk);
            int next = reader.position() + size + (size & 1);
            if ("fmt ".equals(chunk)) {
                int format = reader.getShort() & 0xffff;
                channels = reader.getShort() & 0xffff;
                sampleRate = reader.getInt();
                reader.getInt();
                reader.getShort();
                bits = reader.getShort() & 0xffff;
                if (format != 1) throw new IOException("Fixture is not PCM");
            } else if ("data".equals(chunk)) {
                pcmBytes = new byte[size];
                reader.get(pcmBytes);
            }
            reader.position(Math.min(next, reader.limit()));
        }
        if (pcmBytes == null || channels != 1 || bits != 16 || sampleRate <= 0) {
            throw new IOException("Fixture must be 16-bit mono PCM WAV");
        }
        ByteBuffer pcmReader = ByteBuffer.wrap(pcmBytes).order(ByteOrder.LITTLE_ENDIAN);
        short[] pcm = new short[pcmBytes.length / 2];
        for (int index = 0; index < pcm.length; index++) pcm[index] = pcmReader.getShort();
        return new WavData(sampleRate, pcm);
    }

    private static void assertChunk(ByteBuffer reader, String expected) throws IOException {
        String actual = readAscii(reader, expected.length());
        if (!expected.equals(actual)) throw new IOException("Expected " + expected + ", found " + actual);
    }

    private static String readAscii(ByteBuffer reader, int count) {
        byte[] bytes = new byte[count];
        reader.get(bytes);
        return new String(bytes, java.nio.charset.StandardCharsets.US_ASCII);
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
