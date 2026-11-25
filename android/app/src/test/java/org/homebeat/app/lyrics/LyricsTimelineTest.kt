package org.homebeat.app.lyrics

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.homebeat.app.data.api.LyricLine

class LyricsTimelineTest {
    private val lines = listOf(
        LyricLine(1_000, "one"), LyricLine(5_000, "two"), LyricLine(9_000, ""),
        LyricLine(12_000, "three"), LyricLine(12_000, "three again"), LyricLine(20_000, "last"),
    )

    @Test fun `before the first line nothing is current`() {
        assertEquals(-1, LyricsTimeline.currentIndex(lines, 0))
        assertEquals(-1, LyricsTimeline.currentIndex(lines, 999))
    }

    @Test fun `a line is current from its timestamp until the next one`() {
        assertEquals(0, LyricsTimeline.currentIndex(lines, 1_000))
        assertEquals(0, LyricsTimeline.currentIndex(lines, 4_999))
        assertEquals(1, LyricsTimeline.currentIndex(lines, 5_000))
        assertEquals(2, LyricsTimeline.currentIndex(lines, 10_000))
    }

    @Test fun `equal timestamps pick the last of them`() {
        assertEquals(4, LyricsTimeline.currentIndex(lines, 12_000))
    }

    @Test fun `after the last line it stays on the last`() {
        assertEquals(5, LyricsTimeline.currentIndex(lines, 20_000))
        assertEquals(5, LyricsTimeline.currentIndex(lines, 10_000_000))
    }

    @Test fun `empty list and single line`() {
        assertEquals(-1, LyricsTimeline.currentIndex(emptyList(), 5_000))
        assertEquals(0, LyricsTimeline.currentIndex(listOf(LyricLine(0, "x")), 0))
    }

    @Test fun `matches a linear scan for every position`() {
        for (pos in 0L..25_000L step 250) {
            val expected = lines.indexOfLast { (it.timeMs ?: 0) <= pos }
            assertEquals("pos=$pos", expected, LyricsTimeline.currentIndex(lines, pos))
        }
    }

    @Test fun `empty lines are instrumental gaps`() {
        assertTrue(LyricsTimeline.isInstrumental(LyricLine(9_000, "")))
        assertTrue(LyricsTimeline.isInstrumental(LyricLine(9_000, "  ")))
        assertFalse(LyricsTimeline.isInstrumental(LyricLine(9_000, "la")))
    }

    @Test fun `karaoke word index follows the position`() {
        val words = listOf(org.homebeat.app.data.api.LyricWord(1_000, "a "), org.homebeat.app.data.api.LyricWord(1_500, "b"))
        assertEquals(-1, LyricsTimeline.currentWordIndex(words, 999))
        assertEquals(0, LyricsTimeline.currentWordIndex(words, 1_499))
        assertEquals(1, LyricsTimeline.currentWordIndex(words, 9_000))
    }

    @Test fun `timing nudge label`() {
        assertEquals("In sync", LyricsTimeline.formatOffset(0))
        assertEquals("+0.5 s", LyricsTimeline.formatOffset(500))
        assertEquals("−1.25 s", LyricsTimeline.formatOffset(-1_250))
    }

    @Test fun `word timings decode and survive the editor`() {
        val json = kotlinx.serialization.json.Json { ignoreUnknownKeys = true }
        val l = json.decodeFromString(
            org.homebeat.app.data.api.Lyrics.serializer(),
            """{"trackId":"t","synced":true,"source":"embedded","text":"Hi there",
               "lines":[{"timeMs":1000,"text":"Hi there","words":[{"timeMs":1000,"text":"Hi "},{"timeMs":1500,"text":"there"}]}]}""",
        )
        assertEquals(2, l.lines[0].words?.size)
        assertEquals("[00:01.00]<00:01.00>Hi <00:01.50>there", LrcFormat.toEditable(l))
    }
}
