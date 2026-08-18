package com.tmcra.memory.mobile.audio;

import android.content.Context;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.Arrays;
import java.util.Comparator;

public final class RollingAudioStore {
    private static final long MAX_AGE_MS = 24L * 60L * 60L * 1_000L;
    private static final long MAX_BYTES = 256L * 1024L * 1024L;
    private final File directory;

    public RollingAudioStore(Context context) {
        directory = new File(context.getFilesDir(), "audio-cache");
        if (!directory.isDirectory() && !directory.mkdirs()) {
            throw new IllegalStateException("Unable to create audio cache");
        }
    }

    public File writeWav(String eventId, short[] pcm, int sampleRate) throws IOException {
        if (!eventId.matches("[A-Za-z0-9_.:-]{8,128}")) throw new IOException("Invalid event id");
        File destination = new File(directory, eventId.replace(':', '_') + ".wav");
        try (FileOutputStream output = new FileOutputStream(destination)) {
            int dataBytes = pcm.length * 2;
            writeAscii(output, "RIFF");
            writeInt(output, 36 + dataBytes);
            writeAscii(output, "WAVEfmt ");
            writeInt(output, 16);
            writeShort(output, 1);
            writeShort(output, 1);
            writeInt(output, sampleRate);
            writeInt(output, sampleRate * 2);
            writeShort(output, 2);
            writeShort(output, 16);
            writeAscii(output, "data");
            writeInt(output, dataBytes);
            for (short sample : pcm) writeShort(output, sample);
        }
        prune();
        return destination;
    }

    public void prune() {
        File[] files = directory.listFiles((dir, name) -> name.endsWith(".wav"));
        if (files == null) return;
        Arrays.sort(files, Comparator.comparingLong(File::lastModified));
        long now = System.currentTimeMillis();
        long total = 0;
        for (File file : files) total += Math.max(0, file.length());
        for (File file : files) {
            if (now - file.lastModified() <= MAX_AGE_MS && total <= MAX_BYTES) continue;
            long size = Math.max(0, file.length());
            if (file.delete()) total -= size;
        }
    }

    public void deleteAll() {
        File[] files = directory.listFiles();
        if (files == null) return;
        for (File file : files) if (file.isFile()) file.delete();
    }

    private static void writeAscii(FileOutputStream output, String value) throws IOException {
        output.write(value.getBytes(java.nio.charset.StandardCharsets.US_ASCII));
    }

    private static void writeShort(FileOutputStream output, int value) throws IOException {
        output.write(value & 0xff);
        output.write((value >>> 8) & 0xff);
    }

    private static void writeInt(FileOutputStream output, int value) throws IOException {
        output.write(value & 0xff);
        output.write((value >>> 8) & 0xff);
        output.write((value >>> 16) & 0xff);
        output.write((value >>> 24) & 0xff);
    }
}
