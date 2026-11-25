package org.homebeat.app.spoken

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Test
import org.homebeat.app.data.api.ProgressWrite

class SpokenLogicTest {
    private val starts = listOf(0.0, 100.0, 150.0)
    private val durs = listOf(100.0, 50.0, 30.0)

    @Test fun partAtMapsBookPositions() {
        assertEquals(0 to 0.0, SpokenLogic.partAt(starts, durs, 0.0))
        assertEquals(1 to 20.0, SpokenLogic.partAt(starts, durs, 120.0))
        assertEquals(2 to 30.0, SpokenLogic.partAt(starts, durs, 999.0))
        assertEquals(0 to 0.0, SpokenLogic.partAt(emptyList(), emptyList(), 5.0))
    }

    @Test fun chapters() {
        assertEquals(0, SpokenLogic.chapterAt(listOf(0.0, 60.0, 120.0), 59.0))
        assertEquals(1, SpokenLogic.chapterAt(listOf(0.0, 60.0, 120.0), 60.0))
        assertEquals(-1, SpokenLogic.chapterAt(listOf(10.0), 5.0))
        assertEquals(60.0, SpokenLogic.previousChapter(listOf(0.0, 60.0), 70.0)!!, 0.0)
        assertEquals(0.0, SpokenLogic.previousChapter(listOf(0.0, 60.0), 61.0)!!, 0.0)
    }

    @Test fun formatting() {
        assertEquals("1:02:03", SpokenLogic.clock(3723.0))
        assertEquals("1:05", SpokenLogic.clock(65.0))
        assertEquals("1 h 0 min left", SpokenLogic.remaining(600.0, 4200.0, false))
        assertEquals("5 min", SpokenLogic.remaining(null, 300.0, false))
        assertEquals("Played", SpokenLogic.remaining(1.0, 300.0, true))
    }

    @Test fun outboxCoalescesAndKeepsNewer() {
        val o = ProgressOutbox()
        o.add(ProgressWrite("episode", "a", positionS = 10.0), Instant.parse("2026-01-01T00:00:01Z"))
        o.add(ProgressWrite("episode", "a", positionS = 20.0, durationS = 300.0), Instant.parse("2026-01-01T00:00:02Z"))
        o.add(ProgressWrite("book", "a", positionS = 5.0, speed = 1.5), Instant.parse("2026-01-01T00:00:02Z"))
        val batch = o.take()
        assertEquals(2, batch.size)
        assertEquals(20.0, batch.first { it.kind == "episode" }.positionS!!, 0.0)
        o.add(ProgressWrite("episode", "a", positionS = 30.0), Instant.parse("2026-01-01T00:00:03Z"))
        o.restore(batch) // failed send must not overwrite the newer position
        assertEquals(30.0, o.get("episode", "a")!!.positionS!!, 0.0)
        assertEquals(1.5, o.get("book", "a")!!.speed!!, 0.0)
        // merged fields survive a later partial write
        o.add(ProgressWrite("book", "a", positionS = 9.0), Instant.parse("2026-01-01T00:00:04Z"))
        assertEquals(1.5, o.get("book", "a")!!.speed!!, 0.0)
    }
}
