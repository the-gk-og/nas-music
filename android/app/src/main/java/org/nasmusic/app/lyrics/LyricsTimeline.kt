package org.nasmusic.app.lyrics

import org.nasmusic.app.data.api.LyricLine

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

    /** Empty synced lines are instrumental gaps. */
    fun isInstrumental(line: LyricLine): Boolean = line.text.isBlank()
}
