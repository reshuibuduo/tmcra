package com.tmcra.memory.mobile.reminder;

import java.util.Calendar;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class TriggerHintExtractor {
    private static final Pattern COMMITMENT = Pattern.compile(
            "提醒|记得|别忘|要去|需要|必须|答应|承诺|remind|remember|need to|must|promise",
            Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);
    private static final Pattern PERSON = Pattern.compile(
            "(?:给|跟|找|问|告诉|联系)[\\p{IsHan}A-Za-z][\\p{IsHan}A-Za-z0-9_-]{1,15}|" +
                    "(?:call|ask|tell|meet|email)\\s+[A-Z][A-Za-z0-9_-]{1,30}",
            Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);
    private static final Pattern TEMPORAL = Pattern.compile(
            "今天|明天|后天|上午|下午|晚上|几点|分钟后|小时后|today|tomorrow|at \\d|in \\d",
            Pattern.CASE_INSENSITIVE);
    private static final Pattern RELATIVE_ZH = Pattern.compile("(\\d{1,3})\\s*(分钟|小时)后");
    private static final Pattern RELATIVE_EN = Pattern.compile(
            "\\bin\\s+(\\d{1,3})\\s+(minute|minutes|hour|hours)\\b",
            Pattern.CASE_INSENSITIVE);
    private static final Pattern TOMORROW_ZH = Pattern.compile(
            "明天\\s*(上午|中午|下午|晚上)?\\s*(\\d{1,2})\\s*(?:点|时)(?:\\s*(\\d{1,2})\\s*分)?");
    private static final Pattern TOMORROW_EN = Pattern.compile(
            "\\btomorrow(?:\\s+at)?\\s+(\\d{1,2})(?::(\\d{2}))?\\s*(am|pm)?\\b",
            Pattern.CASE_INSENSITIVE);

    private TriggerHintExtractor() {}

    public static Hints analyze(String transcript, long nowMs) {
        String text = transcript == null ? "" : transcript.trim();
        boolean commitment = COMMITMENT.matcher(text).find();
        boolean person = PERSON.matcher(text).find();
        Long dueAt = parseDueAt(text, nowMs);
        boolean temporal = dueAt != null || TEMPORAL.matcher(text).find();
        return new Hints(commitment, temporal, person, dueAt);
    }

    private static Long parseDueAt(String text, long nowMs) {
        Matcher relativeZh = RELATIVE_ZH.matcher(text);
        if (relativeZh.find()) {
            long amount = Long.parseLong(relativeZh.group(1));
            long unit = "小时".equals(relativeZh.group(2)) ? 60L * 60L * 1_000L : 60L * 1_000L;
            return nowMs + amount * unit;
        }
        Matcher relativeEn = RELATIVE_EN.matcher(text);
        if (relativeEn.find()) {
            long amount = Long.parseLong(relativeEn.group(1));
            long unit = relativeEn.group(2).toLowerCase(Locale.ROOT).startsWith("hour")
                    ? 60L * 60L * 1_000L
                    : 60L * 1_000L;
            return nowMs + amount * unit;
        }
        Matcher tomorrowZh = TOMORROW_ZH.matcher(text);
        if (tomorrowZh.find()) {
            int hour = Integer.parseInt(tomorrowZh.group(2));
            int minute = tomorrowZh.group(3) == null ? 0 : Integer.parseInt(tomorrowZh.group(3));
            String period = tomorrowZh.group(1);
            if (("下午".equals(period) || "晚上".equals(period)) && hour < 12) hour += 12;
            if ("中午".equals(period) && hour < 11) hour += 12;
            return tomorrow(nowMs, hour, minute);
        }
        Matcher tomorrowEn = TOMORROW_EN.matcher(text);
        if (tomorrowEn.find()) {
            int hour = Integer.parseInt(tomorrowEn.group(1));
            int minute = tomorrowEn.group(2) == null ? 0 : Integer.parseInt(tomorrowEn.group(2));
            String meridiem = tomorrowEn.group(3);
            if (meridiem != null) {
                if ("pm".equalsIgnoreCase(meridiem) && hour < 12) hour += 12;
                if ("am".equalsIgnoreCase(meridiem) && hour == 12) hour = 0;
            }
            return tomorrow(nowMs, hour, minute);
        }
        return null;
    }

    private static Long tomorrow(long nowMs, int hour, int minute) {
        if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
        Calendar calendar = Calendar.getInstance();
        calendar.setTimeInMillis(nowMs);
        calendar.add(Calendar.DAY_OF_YEAR, 1);
        calendar.set(Calendar.HOUR_OF_DAY, hour);
        calendar.set(Calendar.MINUTE, minute);
        calendar.set(Calendar.SECOND, 0);
        calendar.set(Calendar.MILLISECOND, 0);
        return calendar.getTimeInMillis();
    }

    public static final class Hints {
        public final boolean commitment;
        public final boolean temporal;
        public final boolean person;
        public final Long dueAtMs;

        Hints(boolean commitment, boolean temporal, boolean person, Long dueAtMs) {
            this.commitment = commitment;
            this.temporal = temporal;
            this.person = person;
            this.dueAtMs = dueAtMs;
        }
    }
}
