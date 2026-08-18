package com.tmcra.memory.mobile.reminder;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;

import androidx.core.app.NotificationCompat;

import com.tmcra.memory.mobile.MainActivity;
import com.tmcra.memory.mobile.R;
import com.tmcra.memory.mobile.data.AudioMemoryStore;

import java.util.Locale;

public final class ReminderReceiver extends BroadcastReceiver {
    public static final String ACTION_DELIVER = "com.tmcra.memory.mobile.action.DELIVER_REMINDER";
    public static final String EXTRA_REMINDER_ID = "reminder_id";
    private static final String CHANNEL_ID = "tmcra-memory-reminders";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (!ACTION_DELIVER.equals(intent.getAction())) return;
        String id = intent.getStringExtra(EXTRA_REMINDER_ID);
        if (id == null) return;
        AudioMemoryStore store = new AudioMemoryStore(context);
        AudioMemoryStore.Reminder reminder = store.reminder(id);
        if (reminder == null || !"scheduled".equals(reminder.status)) {
            store.close();
            return;
        }
        showNotification(context, reminder);
        store.markReminderDelivered(id);
        store.close();
        if (ReminderPolicy.shouldSpeak(context)) speak(context, reminder.text, id);
    }

    private static void showNotification(Context context, AudioMemoryStore.Reminder reminder) {
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26) {
            manager.createNotificationChannel(new NotificationChannel(
                    CHANNEL_ID,
                    context.getString(R.string.reminder_channel_name),
                    NotificationManager.IMPORTANCE_HIGH));
        }
        PendingIntent open = PendingIntent.getActivity(
                context,
                0,
                new Intent(context, MainActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        manager.notify(reminder.id.hashCode(), new NotificationCompat.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentTitle("TMCRA 记忆提醒")
                .setContentText(reminder.text)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(reminder.text))
                .setContentIntent(open)
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .build());
    }

    private static void speak(Context context, String text, String id) {
        final TextToSpeech[] holder = new TextToSpeech[1];
        holder[0] = new TextToSpeech(context.getApplicationContext(), status -> {
            if (status != TextToSpeech.SUCCESS) {
                holder[0].shutdown();
                return;
            }
            holder[0].setLanguage(Locale.getDefault());
            holder[0].setOnUtteranceProgressListener(new UtteranceProgressListener() {
                @Override public void onStart(String utteranceId) {}
                @Override public void onDone(String utteranceId) { holder[0].shutdown(); }
                @Override public void onError(String utteranceId) { holder[0].shutdown(); }
            });
            holder[0].speak(text, TextToSpeech.QUEUE_FLUSH, null, "tmcra-" + id);
        });
    }
}
