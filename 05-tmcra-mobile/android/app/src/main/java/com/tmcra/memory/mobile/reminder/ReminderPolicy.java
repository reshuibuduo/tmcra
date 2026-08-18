package com.tmcra.memory.mobile.reminder;

import android.content.Context;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;

import com.tmcra.memory.mobile.audio.AudioCaptureService;

import java.util.Calendar;

public final class ReminderPolicy {
    private ReminderPolicy() {}

    public static boolean shouldSpeak(Context context) {
        if (System.currentTimeMillis() - AudioCaptureService.lastVoiceAtMs() < 15_000) return false;
        int hour = Calendar.getInstance().get(Calendar.HOUR_OF_DAY);
        if (hour >= 22 || hour < 8) return false;
        AudioManager audio = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
        if (audio.getRingerMode() != AudioManager.RINGER_MODE_NORMAL) return false;
        try {
            for (AudioDeviceInfo device : audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS)) {
                int type = device.getType();
                if (type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP
                        || type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO
                        || type == AudioDeviceInfo.TYPE_BLE_HEADSET
                        || type == AudioDeviceInfo.TYPE_WIRED_HEADPHONES
                        || type == AudioDeviceInfo.TYPE_WIRED_HEADSET
                        || type == AudioDeviceInfo.TYPE_USB_HEADSET) {
                    return true;
                }
            }
        } catch (SecurityException ignored) {
            return false;
        }
        return false;
    }
}
