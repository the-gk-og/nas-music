package org.homebeat.wear.sleep

import java.time.LocalTime
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Where sleep detection is at, for the UI. Held by the container; written by [SleepWatchService]. */
sealed interface SleepStatus {
    data object Off : SleepStatus
    data class Watching(val sinceMs: Long, val stillSinceMs: Long? = null, val heartRate: Boolean = false) : SleepStatus
    data object Fading : SleepStatus
    /** Paused because you fell asleep. [rewoundToMs] = where the episode/book was moved back to, if it was. */
    data class Paused(val atMs: Long, val rewoundToMs: Long? = null) : SleepStatus
}

class SleepWatch {
    private val _status = MutableStateFlow<SleepStatus>(SleepStatus.Off)
    val status: StateFlow<SleepStatus> = _status.asStateFlow()
    fun set(s: SleepStatus) { _status.value = s }
    val watching: Boolean get() = _status.value is SleepStatus.Watching || _status.value is SleepStatus.Fading

    /** You stopped it by hand: don't start it again by itself until then (wall clock ms). */
    @Volatile var noAutoUntilMs: Long = 0

    companion object {
        /** Kinds of PlaybackState.media that count as spoken word (live stations have nothing to resume). */
        val SPOKEN = setOf("episode", "book")

        /** Should falling asleep pause what is playing now? */
        fun eligible(playing: Boolean, mediaKind: String?, spokenOnly: Boolean): Boolean =
            playing && (!spokenOnly || mediaKind in SPOKEN)

        /** "At night" for starting by itself: 21:00 to 05:00 local time. */
        fun isNight(t: LocalTime): Boolean = t.hour >= 21 || t.hour < 5

        /**
         * Where to move a podcast/audiobook back to: where it was when you last moved, from [positions]
         * (wall clock ms -> position ms, sampled while playing). At most [maxBackMs] back; null = leave it.
         */
        fun rewindTarget(positions: List<Pair<Long, Long>>, stillSinceMs: Long, nowPositionMs: Long, maxBackMs: Long = 30 * 60_000L): Long? {
            val at = positions.lastOrNull { it.first <= stillSinceMs } ?: positions.firstOrNull() ?: return null
            val target = at.second.coerceAtLeast(nowPositionMs - maxBackMs).coerceAtLeast(0)
            return target.takeIf { it < nowPositionMs - 5_000 }
        }
    }
}
