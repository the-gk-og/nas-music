package org.homebeat.app.lyrics

import org.homebeat.app.data.api.LyricLine
import org.homebeat.app.data.api.LyricWord

object LyricsTimeline {
    /**
     * Index of the line playing at [positionMs]: the last line whose timeMs <= position.
     * -1 before the first line. Lines must be sorted by timeMs (the API guarantees it for synced lyrics);
     * lines without a timeMs are treated as starting at 0.
     */
    fun currentIndex(lines: List<LyricLine>, positionMs: Long): Int {
        var lo = 0
        var hi = lines.size - 1
        var ans = -1
        while (lo <= hi) {
            val mid = (lo + hi) ushr 1
            if ((lines[mid].timeMs ?: 0L) <= positionMs) { ans = mid; lo = mid + 1 } else hi = mid - 1
        }
        return ans
    }

    /** Index of the word being sung at [positionMs] (karaoke), -1 before the first one. */
    fun currentWordIndex(words: List<LyricWord>, positionMs: Long): Int {
        var ans = -1
        for (i in words.indices) { if (words[i].timeMs <= positionMs) ans = i else break }
        return ans
    }

    /** "+0.5 s" / "−1.25 s" / "In sync" for the timing nudge. */
    fun formatOffset(ms: Long): String {
        if (ms == 0L) return "In sync"
        val s = java.math.BigDecimal(kotlin.math.abs(ms)).movePointLeft(3).stripTrailingZeros().toPlainString()
        return "${if (ms > 0) "+" else "−"}$s s"
    }

    /** Empty synced lines are instrumental gaps. */
    fun isInstrumental(line: LyricLine): Boolean = line.text.isBlank()
}
