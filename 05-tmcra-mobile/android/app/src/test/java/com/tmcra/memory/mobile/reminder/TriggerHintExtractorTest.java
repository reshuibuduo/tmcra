package com.tmcra.memory.mobile.reminder;

import org.junit.Test;

import java.util.Calendar;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

public class TriggerHintExtractorTest {
    @Test
    public void chineseRelativeReminderProducesConcreteLocalDeadline() {
        long now = 1_800_000_000_000L;
        TriggerHintExtractor.Hints hints = TriggerHintExtractor.analyze(
                "记得十分钟后提醒我给王老师回电话", now);
        assertTrue(hints.commitment);
        assertTrue(hints.temporal);
        assertTrue(hints.person);
        // Chinese number words are intentionally not guessed by this deterministic parser.
        assertTrue(hints.dueAtMs == null);

        hints = TriggerHintExtractor.analyze("记得 10 分钟后提醒我给王老师回电话", now);
        assertNotNull(hints.dueAtMs);
        assertTrue(hints.dueAtMs >= now + 599_000L);
    }

    @Test
    public void ordinaryConversationDoesNotInventReminderIntent() {
        TriggerHintExtractor.Hints hints = TriggerHintExtractor.analyze(
                "今天天气不错，我们出去走走。", System.currentTimeMillis());
        assertFalse(hints.commitment);
        assertTrue(hints.temporal);
        assertFalse(hints.person);
        assertTrue(hints.dueAtMs == null);
    }

    @Test
    public void tomorrowClockTimeUsesTheLocalTimezone() {
        Calendar now = Calendar.getInstance();
        now.set(2026, Calendar.AUGUST, 16, 9, 0, 0);
        now.set(Calendar.MILLISECOND, 0);
        TriggerHintExtractor.Hints hints = TriggerHintExtractor.analyze(
                "明天下午 3 点提醒我开会", now.getTimeInMillis());
        assertNotNull(hints.dueAtMs);
        Calendar due = Calendar.getInstance();
        due.setTimeInMillis(hints.dueAtMs);
        assertTrue(due.get(Calendar.DAY_OF_YEAR) != now.get(Calendar.DAY_OF_YEAR));
        assertTrue(due.get(Calendar.HOUR_OF_DAY) == 15);
    }
}
