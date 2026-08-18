package com.tmcra.memory.mobile.reminder;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;

import com.tmcra.memory.mobile.data.AudioMemoryStore;

public final class ReminderScheduler {
    private ReminderScheduler() {}

    public static void schedule(Context context, String id, String eventId, String text, long dueAtMs) {
        if (dueAtMs <= System.currentTimeMillis() + 10_000) return;
        AudioMemoryStore store = new AudioMemoryStore(context);
        store.insertReminder(id, eventId, text, dueAtMs);
        store.close();
        Intent intent = new Intent(context, ReminderReceiver.class)
                .setAction(ReminderReceiver.ACTION_DELIVER)
                .putExtra(ReminderReceiver.EXTRA_REMINDER_ID, id);
        PendingIntent pendingIntent = PendingIntent.getBroadcast(
                context,
                id.hashCode(),
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        AlarmManager alarms = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, dueAtMs, pendingIntent);
    }

    public static void deliverImmediate(
            Context context,
            String id,
            String eventId,
            String text) {
        AudioMemoryStore store = new AudioMemoryStore(context);
        store.insertReminder(id, eventId, text, System.currentTimeMillis());
        store.close();
        context.sendBroadcast(new Intent(context, ReminderReceiver.class)
                .setAction(ReminderReceiver.ACTION_DELIVER)
                .putExtra(ReminderReceiver.EXTRA_REMINDER_ID, id));
    }
}
