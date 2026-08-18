package com.tmcra.memory.mobile.data;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;

import java.util.ArrayList;
import java.util.List;

/** Device-local operational store. Semantic retrieval only receives transcript records. */
public final class AudioMemoryStore extends SQLiteOpenHelper {
    private static final String DATABASE_NAME = "tmcra_audio_memory.db";
    private static final int DATABASE_VERSION = 5;

    public AudioMemoryStore(Context context) {
        this(context, DATABASE_NAME);
    }

    /** Isolated database constructor for instrumentation and debug probes. */
    public AudioMemoryStore(Context context, String databaseName) {
        super(context.getApplicationContext(), safeDatabaseName(databaseName), null, DATABASE_VERSION);
    }

    private static String safeDatabaseName(String value) {
        if (value == null || !value.matches("[A-Za-z0-9._-]{1,120}")) {
            throw new IllegalArgumentException("Invalid audio memory database name");
        }
        return value;
    }

    @Override
    public void onCreate(SQLiteDatabase database) {
        database.execSQL("CREATE TABLE segments (" +
                "id INTEGER PRIMARY KEY AUTOINCREMENT," +
                "event_id TEXT NOT NULL UNIQUE," +
                "session_id TEXT NOT NULL," +
                "scope_name TEXT NOT NULL," +
                "captured_at TEXT NOT NULL," +
                "duration_ms INTEGER NOT NULL," +
                "speaker_id TEXT NOT NULL," +
                "speaker_label TEXT," +
                "speaker_relation TEXT NOT NULL," +
                "speaker_confidence REAL NOT NULL," +
                "transcript TEXT," +
                "language TEXT," +
                "asr_mode TEXT," +
                "asr_model TEXT," +
                "asr_confidence REAL," +
                "local_transcript TEXT," +
                "local_asr_model TEXT," +
                "local_asr_confidence REAL," +
                "local_asr_sha256 TEXT," +
                "remote_transcript TEXT," +
                "remote_asr_model TEXT," +
                "remote_asr_provider TEXT," +
                "remote_asr_sha256 TEXT," +
                "resolution_status TEXT," +
                "resolution_source TEXT," +
                "resolution_confidence TEXT," +
                "resolution_similarity REAL," +
                "resolution_reasons TEXT," +
                "resolution_conflicts TEXT," +
                "pipeline_stage TEXT NOT NULL DEFAULT 'captured'," +
                "recall_status TEXT," +
                "recall_summary TEXT," +
                "recall_reason TEXT," +
                "recall_count INTEGER NOT NULL DEFAULT 0," +
                "delivery_status TEXT," +
                "remote_message_id TEXT," +
                "remote_delete_status TEXT," +
                "wav_path TEXT," +
                "state TEXT NOT NULL," +
                "commitment_hint INTEGER NOT NULL DEFAULT 0," +
                "temporal_hint INTEGER NOT NULL DEFAULT 0," +
                "person_hint INTEGER NOT NULL DEFAULT 0," +
                "remote_job_id TEXT," +
                "last_error TEXT," +
                "created_at_ms INTEGER NOT NULL," +
                "updated_at_ms INTEGER NOT NULL" +
                ")");
        database.execSQL("CREATE INDEX segments_state_idx ON segments(state, id)");
        database.execSQL("CREATE INDEX segments_capture_idx ON segments(captured_at DESC)");
        createSpeakerTable(database);
        database.execSQL("CREATE TABLE reminders (" +
                "id TEXT PRIMARY KEY," +
                "segment_event_id TEXT NOT NULL," +
                "text TEXT NOT NULL," +
                "due_at_ms INTEGER NOT NULL," +
                "status TEXT NOT NULL," +
                "created_at_ms INTEGER NOT NULL" +
                ")");
    }

    @Override
    public void onUpgrade(SQLiteDatabase database, int oldVersion, int newVersion) {
        if (oldVersion < 2) createSpeakerTable(database);
        if (oldVersion < 3) database.execSQL("ALTER TABLE segments ADD COLUMN asr_model TEXT");
        if (oldVersion >= 2 && oldVersion < 4) {
            database.execSQL("ALTER TABLE speakers ADD COLUMN mapping_revision INTEGER NOT NULL DEFAULT 0");
            database.execSQL("ALTER TABLE speakers ADD COLUMN sync_state TEXT NOT NULL DEFAULT 'local_only'");
            database.execSQL("ALTER TABLE speakers ADD COLUMN sync_error TEXT");
        }
        if (oldVersion < 5) {
            database.execSQL("ALTER TABLE segments ADD COLUMN local_transcript TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN local_asr_model TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN local_asr_confidence REAL");
            database.execSQL("ALTER TABLE segments ADD COLUMN local_asr_sha256 TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN remote_transcript TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN remote_asr_model TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN remote_asr_provider TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN remote_asr_sha256 TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN resolution_status TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN resolution_source TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN resolution_confidence TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN resolution_similarity REAL");
            database.execSQL("ALTER TABLE segments ADD COLUMN resolution_reasons TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN resolution_conflicts TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN pipeline_stage TEXT NOT NULL DEFAULT 'captured'");
            database.execSQL("ALTER TABLE segments ADD COLUMN recall_status TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN recall_summary TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN recall_reason TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN recall_count INTEGER NOT NULL DEFAULT 0");
            database.execSQL("ALTER TABLE segments ADD COLUMN delivery_status TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN remote_message_id TEXT");
            database.execSQL("ALTER TABLE segments ADD COLUMN remote_delete_status TEXT");
            database.execSQL("UPDATE segments SET pipeline_stage = CASE " +
                    "WHEN state='uploaded' THEN 'written' " +
                    "WHEN state IN ('transcribed','upload_failed') THEN 'resolved' " +
                    "ELSE state END WHERE pipeline_stage='captured'");
        }
    }

    public void markLocalDraft(
            String eventId,
            String transcript,
            String language,
            String model,
            Double confidence,
            String sha256) {
        ContentValues values = new ContentValues();
        values.put("local_transcript", transcript);
        values.put("local_asr_model", model);
        if (confidence == null) values.putNull("local_asr_confidence");
        else values.put("local_asr_confidence", confidence);
        values.put("local_asr_sha256", sha256);
        values.put("language", language);
        values.put("state", "local_draft");
        values.put("pipeline_stage", "local_draft");
        values.putNull("last_error");
        values.put("updated_at_ms", System.currentTimeMillis());
        getWritableDatabase().update("segments", values, "event_id = ?", new String[]{eventId});
    }

    public void markRemoteReviewing(String eventId) {
        ContentValues values = new ContentValues();
        values.put("state", "remote_reviewing");
        values.put("pipeline_stage", "remote_reviewing");
        values.putNull("last_error");
        values.put("updated_at_ms", System.currentTimeMillis());
        getWritableDatabase().update("segments", values, "event_id = ?", new String[]{eventId});
    }

    public void markPipelineStage(String eventId, String stage) {
        ContentValues values = new ContentValues();
        values.put("pipeline_stage", stage);
        values.put("updated_at_ms", System.currentTimeMillis());
        getWritableDatabase().update("segments", values, "event_id = ?", new String[]{eventId});
    }

    public void applyTranscriptResolution(
            String eventId,
            String finalTranscript,
            String language,
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
            String resolutionStatus,
            String resolutionSource,
            String resolutionConfidence,
            Double resolutionSimilarity,
            String resolutionReasons,
            String resolutionConflicts,
            boolean commitment,
            boolean temporal,
            boolean person) {
        ContentValues values = transcriptResolutionValues(
                finalTranscript,
                language,
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
                resolutionStatus,
                resolutionSource,
                resolutionConfidence,
                resolutionSimilarity,
                resolutionReasons,
                resolutionConflicts);
        values.put("commitment_hint", commitment ? 1 : 0);
        values.put("temporal_hint", temporal ? 1 : 0);
        values.put("person_hint", person ? 1 : 0);
        values.put("state", "resolved");
        values.put("pipeline_stage", "resolved");
        values.putNull("last_error");
        values.put("updated_at_ms", System.currentTimeMillis());
        getWritableDatabase().update("segments", values, "event_id = ?", new String[]{eventId});
    }

    public void markReviewRequired(
            String eventId,
            String language,
            String localTranscript,
            String localModel,
            Double localConfidence,
            String localSha256,
            String remoteTranscript,
            String remoteModel,
            String remoteProvider,
            String remoteSha256,
            Double similarity,
            String reasons,
            String conflicts) {
        ContentValues values = transcriptResolutionValues(
                null,
                language,
                "dual_review",
                remoteModel,
                null,
                localTranscript,
                localModel,
                localConfidence,
                localSha256,
                remoteTranscript,
                remoteModel,
                remoteProvider,
                remoteSha256,
                "review_required",
                "none",
                "low",
                similarity,
                reasons,
                conflicts);
        values.put("state", "review_required");
        values.put("pipeline_stage", "review_required");
        values.putNull("last_error");
        values.put("updated_at_ms", System.currentTimeMillis());
        getWritableDatabase().update("segments", values, "event_id = ?", new String[]{eventId});
    }

    private static ContentValues transcriptResolutionValues(
            String finalTranscript,
            String language,
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
            String resolutionStatus,
            String resolutionSource,
            String resolutionConfidence,
            Double resolutionSimilarity,
            String resolutionReasons,
            String resolutionConflicts) {
        ContentValues values = new ContentValues();
        putNullable(values, "transcript", finalTranscript);
        putNullable(values, "language", language);
        putNullable(values, "asr_mode", asrMode);
        putNullable(values, "asr_model", asrModel);
        putNullable(values, "asr_confidence", asrConfidence);
        putNullable(values, "local_transcript", localTranscript);
        putNullable(values, "local_asr_model", localModel);
        putNullable(values, "local_asr_confidence", localConfidence);
        putNullable(values, "local_asr_sha256", localSha256);
        putNullable(values, "remote_transcript", remoteTranscript);
        putNullable(values, "remote_asr_model", remoteModel);
        putNullable(values, "remote_asr_provider", remoteProvider);
        putNullable(values, "remote_asr_sha256", remoteSha256);
        putNullable(values, "resolution_status", resolutionStatus);
        putNullable(values, "resolution_source", resolutionSource);
        putNullable(values, "resolution_confidence", resolutionConfidence);
        putNullable(values, "resolution_similarity", resolutionSimilarity);
        putNullable(values, "resolution_reasons", resolutionReasons);
        putNullable(values, "resolution_conflicts", resolutionConflicts);
        return values;
    }

    private static void putNullable(ContentValues values, String key, Object value) {
        if (value == null) {
            values.putNull(key);
        } else if (value instanceof String) {
            values.put(key, (String) value);
        } else if (value instanceof Double) {
            values.put(key, (Double) value);
        } else {
            throw new IllegalArgumentException("Unsupported database value");
        }
    }

    public long insertCaptured(Segment segment) {
        long now = System.currentTimeMillis();
        ContentValues values = baseValues(segment);
        values.put("state", "captured");
        values.put("created_at_ms", now);
        values.put("updated_at_ms", now);
        return getWritableDatabase().insertOrThrow("segments", null, values);
    }

    public void markTranscribed(
            String eventId,
            String transcript,
            String language,
            String asrMode,
            String asrModel,
            Double confidence,
            boolean commitment,
            boolean temporal,
            boolean person) {
        ContentValues values = new ContentValues();
        values.put("transcript", transcript);
        values.put("language", language);
        values.put("asr_mode", asrMode);
        values.put("asr_model", asrModel);
        if (confidence == null) values.putNull("asr_confidence");
        else values.put("asr_confidence", confidence);
        values.put("commitment_hint", commitment ? 1 : 0);
        values.put("temporal_hint", temporal ? 1 : 0);
        values.put("person_hint", person ? 1 : 0);
        values.put("state", "transcribed");
        values.put("pipeline_stage", "resolved");
        values.put("last_error", (String) null);
        values.put("updated_at_ms", System.currentTimeMillis());
        getWritableDatabase().update("segments", values, "event_id = ?", new String[]{eventId});
    }

    public void markUploaded(String eventId, String jobId, String messageId) {
        ContentValues values = new ContentValues();
        values.put("state", "uploaded");
        values.put("remote_job_id", jobId);
        values.put("remote_message_id", messageId);
        values.put("pipeline_stage", "written");
        values.put("last_error", (String) null);
        values.put("updated_at_ms", System.currentTimeMillis());
        getWritableDatabase().update("segments", values, "event_id = ?", new String[]{eventId});
    }

    public void markRecallReceipt(
            String eventId,
            String status,
            int count,
            String summary,
            String reason) {
        ContentValues values = new ContentValues();
        values.put("recall_status", status);
        values.put("recall_count", Math.max(0, count));
        putNullable(values, "recall_summary", summary);
        putNullable(values, "recall_reason", reason);
        values.put("pipeline_stage", "recalled");
        values.put("updated_at_ms", System.currentTimeMillis());
        getWritableDatabase().update("segments", values, "event_id = ?", new String[]{eventId});
    }

    public void markDelivery(String eventId, String status) {
        ContentValues values = new ContentValues();
        values.put("delivery_status", status);
        values.put("pipeline_stage", "delivered".equals(status) ? "delivered" : "recalled");
        values.put("updated_at_ms", System.currentTimeMillis());
        getWritableDatabase().update("segments", values, "event_id = ?", new String[]{eventId});
    }

    public void markRemoteDeleteRequested(String eventId, String deletionId) {
        ContentValues values = new ContentValues();
        values.put("remote_delete_status", "requested:" + deletionId);
        values.put("state", "remote_delete_pending");
        values.put("pipeline_stage", "remote_delete_pending");
        values.putNull("wav_path");
        values.put("updated_at_ms", System.currentTimeMillis());
        getWritableDatabase().update("segments", values, "event_id = ?", new String[]{eventId});
    }

    public void markRemoteDeleteError(String eventId, String error) {
        ContentValues values = new ContentValues();
        values.put("remote_delete_status", "error");
        values.put("last_error", error == null ? "remote_delete_failed" : error);
        values.put("updated_at_ms", System.currentTimeMillis());
        getWritableDatabase().update("segments", values, "event_id = ?", new String[]{eventId});
    }

    public void markError(String eventId, String state, String error) {
        ContentValues values = new ContentValues();
        values.put("state", state);
        values.put("pipeline_stage", state);
        values.put("last_error", error == null ? "unknown_error" : error.substring(0, Math.min(300, error.length())));
        values.put("updated_at_ms", System.currentTimeMillis());
        getWritableDatabase().update("segments", values, "event_id = ?", new String[]{eventId});
    }

    public List<Segment> pendingUploads(int limit) {
        return querySegments(
                "state IN ('resolved','transcribed','upload_failed') AND transcript IS NOT NULL " +
                        "AND (resolution_status IS NULL OR resolution_status='resolved')",
                null,
                Math.max(1, Math.min(100, limit)));
    }

    public List<Segment> recent(int limit) {
        return querySegments(null, null, Math.max(1, Math.min(50, limit)));
    }

    public Segment segment(String eventId) {
        List<Segment> values = querySegments("event_id = ?", new String[]{eventId}, 1);
        return values.isEmpty() ? null : values.get(0);
    }

    public void deleteSegment(String eventId) {
        getWritableDatabase().delete("segments", "event_id = ?", new String[]{eventId});
    }

    public void deleteSpeakerProfile(String localId) {
        SQLiteDatabase database = getWritableDatabase();
        database.beginTransaction();
        try {
            ContentValues segments = new ContentValues();
            segments.putNull("speaker_label");
            segments.put("speaker_relation", "unknown");
            segments.put("updated_at_ms", System.currentTimeMillis());
            database.update("segments", segments, "speaker_id = ?", new String[]{localId});
            database.delete("speakers", "local_id = ?", new String[]{localId});
            database.setTransactionSuccessful();
        } finally {
            database.endTransaction();
        }
    }

    public int segmentCountForSpeaker(String localId) {
        try (Cursor cursor = getReadableDatabase().rawQuery(
                "SELECT COUNT(*) FROM segments WHERE speaker_id = ?",
                new String[]{localId})) {
            return cursor.moveToFirst() ? cursor.getInt(0) : 0;
        }
    }

    public void deleteAllLocalMemory() {
        SQLiteDatabase database = getWritableDatabase();
        database.beginTransaction();
        try {
            database.delete("segments", null, null);
            database.delete("speakers", null, null);
            database.delete("reminders", null, null);
            database.setTransactionSuccessful();
        } finally {
            database.endTransaction();
        }
    }

    public List<SpeakerProfile> speakers() {
        ArrayList<SpeakerProfile> result = new ArrayList<>();
        try (Cursor cursor = getReadableDatabase().query(
                "speakers",
                null,
                null,
                null,
                null,
                null,
                "created_at_ms ASC")) {
            while (cursor.moveToNext()) {
                result.add(new SpeakerProfile(
                        text(cursor, "local_id"),
                        nullableText(cursor, "display_name"),
                        text(cursor, "relation"),
                        cursor.getBlob(cursor.getColumnIndexOrThrow("encrypted_template")),
                        integer(cursor, "sample_count"),
                        decimal(cursor, "confidence"),
                        integer(cursor, "mapping_revision"),
                        text(cursor, "sync_state"),
                        nullableText(cursor, "sync_error")));
            }
        }
        return result;
    }

    public void insertSpeaker(SpeakerProfile speaker) {
        long now = System.currentTimeMillis();
        ContentValues values = speakerValues(speaker);
        values.put("created_at_ms", now);
        values.put("updated_at_ms", now);
        getWritableDatabase().insertOrThrow("speakers", null, values);
    }

    public void updateSpeakerTemplate(
            String localId,
            byte[] encryptedTemplate,
            int sampleCount,
            double confidence) {
        ContentValues values = new ContentValues();
        values.put("encrypted_template", encryptedTemplate);
        values.put("sample_count", sampleCount);
        values.put("confidence", confidence);
        values.put("updated_at_ms", System.currentTimeMillis());
        getWritableDatabase().update("speakers", values, "local_id = ?", new String[]{localId});
    }

    public int labelSpeaker(String localId, String displayName, String relation) {
        if (!"self".equals(relation) && !"known".equals(relation) && !"unknown".equals(relation)) {
            throw new IllegalArgumentException("Invalid speaker relation");
        }
        SQLiteDatabase database = getWritableDatabase();
        int nextRevision;
        database.beginTransaction();
        try {
            if ("self".equals(relation)) {
                database.execSQL(
                        "UPDATE speakers SET relation = 'known', " +
                                "mapping_revision = mapping_revision + 1, sync_state = 'pending', " +
                                "sync_error = NULL, updated_at_ms = ? " +
                                "WHERE relation = 'self' AND local_id != ?",
                        new Object[]{System.currentTimeMillis(), localId});
                ContentValues demotedSegments = new ContentValues();
                demotedSegments.put("speaker_relation", "known");
                demotedSegments.put("updated_at_ms", System.currentTimeMillis());
                database.update(
                        "segments",
                        demotedSegments,
                        "speaker_relation = 'self' AND speaker_id != ?",
                        new String[]{localId});
            }
            try (Cursor cursor = database.query(
                    "speakers",
                    new String[]{"mapping_revision"},
                    "local_id = ?",
                    new String[]{localId},
                    null,
                    null,
                    null,
                    "1")) {
                if (!cursor.moveToFirst()) throw new IllegalArgumentException("Unknown local speaker");
                nextRevision = cursor.getInt(0) + 1;
            }
            ContentValues speaker = new ContentValues();
            speaker.put("display_name", displayName);
            speaker.put("relation", relation);
            speaker.put("mapping_revision", nextRevision);
            speaker.put("sync_state", "unknown".equals(relation) ? "local_only" : "pending");
            speaker.putNull("sync_error");
            speaker.put("updated_at_ms", System.currentTimeMillis());
            database.update("speakers", speaker, "local_id = ?", new String[]{localId});

            ContentValues segments = new ContentValues();
            segments.put("speaker_label", displayName);
            segments.put("speaker_relation", relation);
            segments.put("updated_at_ms", System.currentTimeMillis());
            database.update("segments", segments, "speaker_id = ?", new String[]{localId});
            database.setTransactionSuccessful();
        } finally {
            database.endTransaction();
        }
        return nextRevision;
    }

    public List<SpeakerProfile> pendingSpeakerMappings() {
        ArrayList<SpeakerProfile> result = new ArrayList<>();
        try (Cursor cursor = getReadableDatabase().query(
                "speakers",
                null,
                "sync_state IN ('pending','error') AND relation IN ('self','known') " +
                        "AND display_name IS NOT NULL",
                null,
                null,
                null,
                "updated_at_ms ASC")) {
            while (cursor.moveToNext()) {
                result.add(new SpeakerProfile(
                        text(cursor, "local_id"),
                        nullableText(cursor, "display_name"),
                        text(cursor, "relation"),
                        cursor.getBlob(cursor.getColumnIndexOrThrow("encrypted_template")),
                        integer(cursor, "sample_count"),
                        decimal(cursor, "confidence"),
                        integer(cursor, "mapping_revision"),
                        text(cursor, "sync_state"),
                        nullableText(cursor, "sync_error")));
            }
        }
        return result;
    }

    public void markSpeakerMappingSynced(String localId, int revision) {
        ContentValues values = new ContentValues();
        values.put("sync_state", "synced");
        values.putNull("sync_error");
        values.put("updated_at_ms", System.currentTimeMillis());
        getWritableDatabase().update(
                "speakers",
                values,
                "local_id = ? AND mapping_revision = ?",
                new String[]{localId, String.valueOf(revision)});
    }

    public void markSpeakerMappingError(String localId, int revision, String error) {
        ContentValues values = new ContentValues();
        values.put("sync_state", "error");
        String safe = error == null ? "speaker_mapping_failed" : error;
        values.put("sync_error", safe.substring(0, Math.min(300, safe.length())));
        values.put("updated_at_ms", System.currentTimeMillis());
        getWritableDatabase().update(
                "speakers",
                values,
                "local_id = ? AND mapping_revision = ?",
                new String[]{localId, String.valueOf(revision)});
    }

    public void insertReminder(String id, String eventId, String text, long dueAtMs) {
        ContentValues values = new ContentValues();
        values.put("id", id);
        values.put("segment_event_id", eventId);
        values.put("text", text);
        values.put("due_at_ms", dueAtMs);
        values.put("status", "scheduled");
        values.put("created_at_ms", System.currentTimeMillis());
        getWritableDatabase().insertWithOnConflict(
                "reminders", null, values, SQLiteDatabase.CONFLICT_IGNORE);
    }

    public Reminder reminder(String id) {
        try (Cursor cursor = getReadableDatabase().query(
                "reminders",
                new String[]{"id", "text", "due_at_ms", "status"},
                "id = ?",
                new String[]{id},
                null,
                null,
                null,
                "1")) {
            if (!cursor.moveToFirst()) return null;
            return new Reminder(
                    cursor.getString(0),
                    cursor.getString(1),
                    cursor.getLong(2),
                    cursor.getString(3));
        }
    }

    public void markReminderDelivered(String id) {
        ContentValues values = new ContentValues();
        values.put("status", "delivered");
        getWritableDatabase().update("reminders", values, "id = ?", new String[]{id});
    }

    private List<Segment> querySegments(String selection, String[] arguments, int limit) {
        ArrayList<Segment> result = new ArrayList<>();
        try (Cursor cursor = getReadableDatabase().query(
                "segments",
                null,
                selection,
                arguments,
                null,
                null,
                "id DESC",
                String.valueOf(limit))) {
            while (cursor.moveToNext()) result.add(readSegment(cursor));
        }
        return result;
    }

    private static ContentValues baseValues(Segment segment) {
        ContentValues values = new ContentValues();
        values.put("event_id", segment.eventId);
        values.put("session_id", segment.sessionId);
        values.put("scope_name", segment.scopeName);
        values.put("captured_at", segment.capturedAt);
        values.put("duration_ms", segment.durationMs);
        values.put("speaker_id", segment.speakerId);
        values.put("speaker_label", segment.speakerLabel);
        values.put("speaker_relation", segment.speakerRelation);
        values.put("speaker_confidence", segment.speakerConfidence);
        values.put("wav_path", segment.wavPath);
        return values;
    }

    private static void createSpeakerTable(SQLiteDatabase database) {
        database.execSQL("CREATE TABLE IF NOT EXISTS speakers (" +
                "local_id TEXT PRIMARY KEY," +
                "display_name TEXT," +
                "relation TEXT NOT NULL," +
                "encrypted_template BLOB NOT NULL," +
                "sample_count INTEGER NOT NULL DEFAULT 1," +
                "confidence REAL NOT NULL DEFAULT 0," +
                "mapping_revision INTEGER NOT NULL DEFAULT 0," +
                "sync_state TEXT NOT NULL DEFAULT 'local_only'," +
                "sync_error TEXT," +
                "created_at_ms INTEGER NOT NULL," +
                "updated_at_ms INTEGER NOT NULL" +
                ")");
    }

    private static ContentValues speakerValues(SpeakerProfile speaker) {
        ContentValues values = new ContentValues();
        values.put("local_id", speaker.localId);
        values.put("display_name", speaker.displayName);
        values.put("relation", speaker.relation);
        values.put("encrypted_template", speaker.encryptedTemplate);
        values.put("sample_count", speaker.sampleCount);
        values.put("confidence", speaker.confidence);
        values.put("mapping_revision", speaker.mappingRevision);
        values.put("sync_state", speaker.syncState);
        values.put("sync_error", speaker.syncError);
        return values;
    }

    private static Segment readSegment(Cursor cursor) {
        Segment segment = new Segment();
        segment.eventId = text(cursor, "event_id");
        segment.sessionId = text(cursor, "session_id");
        segment.scopeName = text(cursor, "scope_name");
        segment.capturedAt = text(cursor, "captured_at");
        segment.durationMs = integer(cursor, "duration_ms");
        segment.speakerId = text(cursor, "speaker_id");
        segment.speakerLabel = nullableText(cursor, "speaker_label");
        segment.speakerRelation = text(cursor, "speaker_relation");
        segment.speakerConfidence = decimal(cursor, "speaker_confidence");
        segment.transcript = nullableText(cursor, "transcript");
        segment.language = nullableText(cursor, "language");
        segment.asrMode = nullableText(cursor, "asr_mode");
        segment.asrModel = nullableText(cursor, "asr_model");
        int confidenceIndex = cursor.getColumnIndexOrThrow("asr_confidence");
        segment.asrConfidence = cursor.isNull(confidenceIndex) ? null : cursor.getDouble(confidenceIndex);
        segment.localTranscript = nullableText(cursor, "local_transcript");
        segment.localAsrModel = nullableText(cursor, "local_asr_model");
        int localConfidenceIndex = cursor.getColumnIndexOrThrow("local_asr_confidence");
        segment.localAsrConfidence = cursor.isNull(localConfidenceIndex)
                ? null : cursor.getDouble(localConfidenceIndex);
        segment.localAsrSha256 = nullableText(cursor, "local_asr_sha256");
        segment.remoteTranscript = nullableText(cursor, "remote_transcript");
        segment.remoteAsrModel = nullableText(cursor, "remote_asr_model");
        segment.remoteAsrProvider = nullableText(cursor, "remote_asr_provider");
        segment.remoteAsrSha256 = nullableText(cursor, "remote_asr_sha256");
        segment.resolutionStatus = nullableText(cursor, "resolution_status");
        segment.resolutionSource = nullableText(cursor, "resolution_source");
        segment.resolutionConfidence = nullableText(cursor, "resolution_confidence");
        int similarityIndex = cursor.getColumnIndexOrThrow("resolution_similarity");
        segment.resolutionSimilarity = cursor.isNull(similarityIndex)
                ? null : cursor.getDouble(similarityIndex);
        segment.resolutionReasons = nullableText(cursor, "resolution_reasons");
        segment.resolutionConflicts = nullableText(cursor, "resolution_conflicts");
        segment.pipelineStage = text(cursor, "pipeline_stage");
        segment.recallStatus = nullableText(cursor, "recall_status");
        segment.recallSummary = nullableText(cursor, "recall_summary");
        segment.recallReason = nullableText(cursor, "recall_reason");
        segment.recallCount = integer(cursor, "recall_count");
        segment.deliveryStatus = nullableText(cursor, "delivery_status");
        segment.remoteMessageId = nullableText(cursor, "remote_message_id");
        segment.remoteDeleteStatus = nullableText(cursor, "remote_delete_status");
        segment.wavPath = nullableText(cursor, "wav_path");
        segment.state = text(cursor, "state");
        segment.commitmentHint = integer(cursor, "commitment_hint") == 1;
        segment.temporalHint = integer(cursor, "temporal_hint") == 1;
        segment.personHint = integer(cursor, "person_hint") == 1;
        segment.remoteJobId = nullableText(cursor, "remote_job_id");
        segment.lastError = nullableText(cursor, "last_error");
        return segment;
    }

    private static String text(Cursor cursor, String name) {
        return cursor.getString(cursor.getColumnIndexOrThrow(name));
    }

    private static String nullableText(Cursor cursor, String name) {
        int index = cursor.getColumnIndexOrThrow(name);
        return cursor.isNull(index) ? null : cursor.getString(index);
    }

    private static int integer(Cursor cursor, String name) {
        return cursor.getInt(cursor.getColumnIndexOrThrow(name));
    }

    private static double decimal(Cursor cursor, String name) {
        return cursor.getDouble(cursor.getColumnIndexOrThrow(name));
    }

    public static final class Segment {
        public String eventId;
        public String sessionId;
        public String scopeName;
        public String capturedAt;
        public int durationMs;
        public String speakerId = "spk_unknown_default";
        public String speakerLabel;
        public String speakerRelation = "unknown";
        public double speakerConfidence;
        public String transcript;
        public String language;
        public String asrMode;
        public String asrModel;
        public Double asrConfidence;
        public String localTranscript;
        public String localAsrModel;
        public Double localAsrConfidence;
        public String localAsrSha256;
        public String remoteTranscript;
        public String remoteAsrModel;
        public String remoteAsrProvider;
        public String remoteAsrSha256;
        public String resolutionStatus;
        public String resolutionSource;
        public String resolutionConfidence;
        public Double resolutionSimilarity;
        public String resolutionReasons;
        public String resolutionConflicts;
        public String pipelineStage = "captured";
        public String recallStatus;
        public String recallSummary;
        public String recallReason;
        public int recallCount;
        public String deliveryStatus;
        public String remoteMessageId;
        public String remoteDeleteStatus;
        public String wavPath;
        public String state;
        public boolean commitmentHint;
        public boolean temporalHint;
        public boolean personHint;
        public String remoteJobId;
        public String lastError;
    }

    public static final class SpeakerProfile {
        public final String localId;
        public final String displayName;
        public final String relation;
        public final byte[] encryptedTemplate;
        public final int sampleCount;
        public final double confidence;
        public final int mappingRevision;
        public final String syncState;
        public final String syncError;

        public SpeakerProfile(
                String localId,
                String displayName,
                String relation,
                byte[] encryptedTemplate,
                int sampleCount,
                double confidence,
                int mappingRevision,
                String syncState,
                String syncError) {
            this.localId = localId;
            this.displayName = displayName;
            this.relation = relation;
            this.encryptedTemplate = encryptedTemplate;
            this.sampleCount = sampleCount;
            this.confidence = confidence;
            this.mappingRevision = mappingRevision;
            this.syncState = syncState;
            this.syncError = syncError;
        }
    }

    public static final class Reminder {
        public final String id;
        public final String text;
        public final long dueAtMs;
        public final String status;

        Reminder(String id, String text, long dueAtMs, String status) {
            this.id = id;
            this.text = text;
            this.dueAtMs = dueAtMs;
            this.status = status;
        }
    }
}
