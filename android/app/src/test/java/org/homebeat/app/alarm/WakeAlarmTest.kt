package org.homebeat.app.alarm

import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WakeAlarmTest {
    private val zone = ZoneId.of("Europe/London")
    private fun at(y: Int, mo: Int, d: Int, h: Int, mi: Int) = ZonedDateTime.of(y, mo, d, h, mi, 0, 0, zone)

    @Test fun offNeverRings() {
        assertNull(AlarmTimes.next(WakeAlarm(enabled = false), at(2026, 11, 24, 6, 0)))
    }

    @Test fun onceRingsTodayOrTomorrow() {
        val a = WakeAlarm(enabled = true, hour = 7, minute = 30)
        assertEquals(at(2026, 11, 24, 7, 30), AlarmTimes.next(a, at(2026, 11, 24, 6, 0)))
        assertEquals(at(2026, 11, 25, 7, 30), AlarmTimes.next(a, at(2026, 11, 24, 7, 30))) // exactly now: tomorrow
        assertEquals(at(2026, 11, 25, 7, 30), AlarmTimes.next(a, at(2026, 11, 24, 22, 0)))
    }

    @Test fun weekdaysSkipTheWeekend() {
        val a = WakeAlarm(enabled = true, hour = 7, minute = 0, days = setOf(1, 2, 3, 4, 5))
        val friday = at(2026, 11, 27, 8, 0)
        assertEquals(java.time.DayOfWeek.FRIDAY, friday.dayOfWeek)
        assertEquals(at(2026, 11, 30, 7, 0), AlarmTimes.next(a, friday)) // Monday
    }

    @Test fun singleDayAWeekAway() {
        val tue = at(2026, 11, 24, 9, 0)
        assertEquals(java.time.DayOfWeek.TUESDAY, tue.dayOfWeek)
        val a = WakeAlarm(enabled = true, hour = 8, minute = 0, days = setOf(2))
        assertEquals(at(2026, 12, 1, 8, 0), AlarmTimes.next(a, tue))
    }

    @Test fun dstGapMovesForward() {
        // UK clocks go forward at 01:00 on 29 Mar 2026; 01:30 doesn't exist that day.
        val a = WakeAlarm(enabled = true, hour = 1, minute = 30)
        val next = AlarmTimes.next(a, at(2026, 3, 29, 0, 10))!!
        assertEquals(29, next.dayOfMonth)
        assertEquals(2, next.hour)
    }

    @Test fun labels() {
        assertEquals("Once", AlarmTimes.daysLabel(emptySet(), Locale.UK))
        assertEquals("Weekdays", AlarmTimes.daysLabel(setOf(1, 2, 3, 4, 5), Locale.UK))
        assertEquals("Every day", AlarmTimes.daysLabel((1..7).toSet(), Locale.UK))
        assertEquals("Mon, Wed", AlarmTimes.daysLabel(setOf(3, 1), Locale.UK))
        val now = at(2026, 11, 24, 6, 0)
        assertEquals("Rings in 1 h 30 min", AlarmTimes.inLabel(at(2026, 11, 24, 7, 30), now))
        assertEquals("Rings in 1 min", AlarmTimes.inLabel(now.plusSeconds(10), now))
        assertEquals("Rings in 1 d 2 h", AlarmTimes.inLabel(at(2026, 11, 25, 8, 0), now))
    }

    @Test fun fadeStartsQuietAndEndsFull() {
        assertTrue(AlarmTimes.fadeLevel(0, 60_000) < 0.05f)
        assertTrue(AlarmTimes.fadeLevel(30_000, 60_000) < 0.5f)
        assertEquals(1f, AlarmTimes.fadeLevel(60_000, 60_000))
        assertEquals(1f, AlarmTimes.fadeLevel(5, 0))
    }

    @Test fun volumeFloorOnlyRaises() {
        assertEquals(6, AlarmTimes.volumeFloor(2, 15, 40))
        assertNull(AlarmTimes.volumeFloor(10, 15, 40))
        assertNull(AlarmTimes.volumeFloor(0, 15, 0))
    }

    @Test fun settingsRoundTrip() {
        val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
        val a = WakeAlarm(enabled = true, hour = 6, minute = 45, days = setOf(1, 5), source = AlarmSource("station", "s1", "Radio X"))
        assertEquals(a, json.decodeFromString(WakeAlarm.serializer(), json.encodeToString(WakeAlarm.serializer(), a)))
        assertEquals(WakeAlarm(), json.decodeFromString(WakeAlarm.serializer(), "{}"))
    }
}
