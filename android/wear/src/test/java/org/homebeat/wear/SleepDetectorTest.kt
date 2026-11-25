package org.homebeat.wear

import java.time.LocalTime
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.homebeat.wear.sleep.Epoch
import org.homebeat.wear.sleep.EpochBuilder
import org.homebeat.wear.sleep.SleepDetector
import org.homebeat.wear.sleep.SleepVerdict
import org.homebeat.wear.sleep.SleepWatch

class SleepDetectorTest {
    private val epoch = 30_000L
    private val min = 60_000L

    /** Feeds [n] epochs (2 per minute) after [t0], returning the last verdict. */
    private fun SleepDetector.feed(from: Long, n: Int, still: Boolean, hr: (Int) -> Float?): Pair<Long, SleepVerdict> {
        var v = SleepVerdict(false, null)
        var t = from
        repeat(n) { i -> t += epoch; v = add(Epoch(t, still, hr(i))) }
        return t to v
    }

    @Test fun stillWithDroppingHeartRateIsAsleep() {
        val d = SleepDetector()
        // 10 min awake and fidgeting at ~70 bpm, then lying still while the pulse settles to ~62.
        val (t1, _) = d.feed(0, 20, still = false) { 70f }
        val (t2, early) = d.feed(t1, 10, still = true) { 69f }   // 5 min still: not yet
        assertFalse(early.asleep)
        assertEquals(t1, early.stillSinceMs)
        val (_, v) = d.feed(t2, 12, still = true) { 62f }        // 11 min still, recent 62 vs baseline ~70
        assertTrue(v.asleep)
        assertEquals("heart rate", v.reason)
        assertEquals(t1, v.stillSinceMs)
    }

    @Test fun stillButHeartRateUnchangedIsAwakeUntilTheLongLimit() {
        val d = SleepDetector()
        val (t1, _) = d.feed(0, 10, still = false) { 66f }
        val (t2, v) = d.feed(t1, 60, still = true) { 66f }        // 30 min still, same pulse: lying awake
        assertFalse(v.asleep)
        val (_, late) = d.feed(t2, 30, still = true) { 66f }     // 45 min still: asleep by stillness
        assertTrue(late.asleep)
        assertEquals("stillness", late.reason)
    }

    @Test fun withoutHeartRateTwentyMinutesStillIsAsleep() {
        val d = SleepDetector()
        val (t1, _) = d.feed(0, 4, still = false) { null }
        val (t2, v) = d.feed(t1, 39, still = true) { null }
        assertFalse(v.asleep)
        val (_, v2) = d.feed(t2, 1, still = true) { null }
        assertTrue(v2.asleep)
        assertEquals("stillness", v2.reason)
    }

    @Test fun movingResetsTheStillStretch() {
        val d = SleepDetector()
        val (t1, _) = d.feed(0, 30, still = true) { null }
        val (t2, moved) = d.feed(t1, 1, still = false) { null }
        assertFalse(moved.asleep)
        assertNull(moved.stillSinceMs)
        val (_, v) = d.feed(t2, 10, still = true) { null }
        assertFalse(v.asleep)
        assertEquals(t2, v.stillSinceMs)
    }

    @Test fun heartRateDropNeedsEnoughStillTime() {
        val d = SleepDetector()
        val (t1, _) = d.feed(0, 20, still = false) { 75f }
        // Pulse drops right away but you've only been still 8 minutes.
        val (_, v) = d.feed(t1, 16, still = true) { 60f }
        assertFalse(v.asleep)
    }

    @Test fun epochBuilderTellsStillFromMoving() {
        val b = EpochBuilder()
        // Resting wrist: gravity plus tiny noise.
        repeat(150) { i -> b.addMotion(0.01f * (i % 3), 0f, 9.81f) }
        b.addHeartRate(64f, accurate = true)
        b.addHeartRate(0f, accurate = true)       // no skin contact
        b.addHeartRate(90f, accurate = false)     // unreliable
        val still = b.close(30_000)
        assertTrue(still.still)
        assertEquals(64f, still.heartRate!!, 0f)

        // Shaking the wrist.
        repeat(150) { i -> b.addMotion(0f, 0f, if (i % 2 == 0) 9.81f else 12.5f) }
        assertFalse(b.close(60_000).still)

        // Rolling over: calm samples, but gravity now points along x.
        repeat(150) { b.addMotion(9.81f, 0f, 0f) }
        b.close(90_000).let { assertFalse(it.still); assertNull(it.heartRate) }
        repeat(150) { b.addMotion(9.81f, 0f, 0f) }
        assertTrue(b.close(120_000).still)
    }

    @Test fun eligibility() {
        assertTrue(SleepWatch.eligible(true, "episode", spokenOnly = true))
        assertTrue(SleepWatch.eligible(true, "book", spokenOnly = true))
        assertFalse(SleepWatch.eligible(true, null, spokenOnly = true))        // music
        assertFalse(SleepWatch.eligible(true, "station", spokenOnly = true))
        assertTrue(SleepWatch.eligible(true, null, spokenOnly = false))
        assertFalse(SleepWatch.eligible(false, "book", spokenOnly = false))
    }

    @Test fun night() {
        assertTrue(SleepWatch.isNight(LocalTime.of(23, 30)))
        assertTrue(SleepWatch.isNight(LocalTime.of(2, 0)))
        assertFalse(SleepWatch.isNight(LocalTime.of(5, 0)))
        assertFalse(SleepWatch.isNight(LocalTime.of(20, 59)))
    }

    @Test fun rewindGoesBackToWhenYouLastMoved() {
        val positions = (0..40).map { i -> i * epoch to i * epoch }   // playing at 1x from 0
        // Last moved at 10 min; paused at 20.5 min.
        assertEquals(10 * min, SleepWatch.rewindTarget(positions, 10 * min, 20 * min + 30_000))
        // Never more than 30 min back.
        assertEquals(60 * min - 30 * min, SleepWatch.rewindTarget(listOf(0L to 0L), 0, 60 * min))
        // Nothing worth moving.
        assertNull(SleepWatch.rewindTarget(positions, 20 * min, 20 * min + 2_000))
        assertNull(SleepWatch.rewindTarget(emptyList(), 0, 10 * min))
    }
}
