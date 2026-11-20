package org.nasmusic.app.spoken

import java.time.Instant
import org.nasmusic.app.data.api.ProgressWrite

/** Pure helpers for podcasts / audiobooks (unit-tested, no Android). */
object SpokenLogic {
    /** Which part of a multi-file book holds book-wide second [pos], and the offset inside it. */
    fun partAt(starts: List<Double>, durations: List<Double>, pos: Double): Pair<Int, Double> {
        if (starts.isEmpty()) return 0 to 0.0
        var i = 0
        for (k in starts.indices) if (starts[k] <= pos) i = k
        val d = durations.getOrNull(i)?.takeIf { it > 0 } ?: Double.MAX_VALUE
        return i to (pos - starts[i]).coerceIn(0.0, d)
    }

    /** Index of the chapter playing at [pos] (-1 before the first). */
    fun chapterAt(starts: List<Double>, pos: Double): Int {
        var i = -1
        for (k in starts.indices) if (starts[k] <= pos + 0.25) i = k
        return i
    }

    /** Where "previous chapter" goes: this chapter's start, or the one before when within 3 s of it. */
    fun previousChapter(starts: List<Double>, pos: Double): Double? {
        val i = chapterAt(starts, pos)
        if (i < 0) return starts.firstOrNull()
        return if (pos - starts[i] > 3 || i == 0) starts[i] else starts[i - 1]
    }

    fun clock(s: Double?): String {
        if (s == null || s.isNaN() || s.isInfinite()) return "–:––"
        val t = s.toLong().coerceAtLeast(0)
        val h = t / 3600
        val m = (t % 3600) / 60
        val sec = t % 60
        return if (h > 0) "%d:%02d:%02d".format(h, m, sec) else "%d:%02d".format(m, sec)
    }

    fun remaining(positionS: Double?, durationS: Double?, played: Boolean): String {
        if (played) return "Played"
        val d = durationS ?: return ""
        val left = (d - (positionS ?: 0.0)).coerceAtLeast(0.0)
        val m = Math.round(left / 60).toInt()
        val txt = if (m >= 60) "${m / 60} h ${m % 60} min" else "${m.coerceAtLeast(1)} min"
        return if ((positionS ?: 0.0) > 5) "$txt left" else txt
    }
}

/**
 * Progress writes waiting to be sent, one per episode/book (newest wins). Kept on disk by [SpokenPlayer]
 * so positions recorded offline reach the server later without rewinding newer ones (the server compares
 * `updatedAt`).
 */
class ProgressOutbox(initial: List<ProgressWrite> = emptyList()) {
    private val items = LinkedHashMap<String, ProgressWrite>()

    init {
        initial.forEach { items[key(it)] = it }
    }

    private fun key(w: ProgressWrite) = "${w.kind}:${w.id}"

    @Synchronized
    fun add(w: ProgressWrite, now: Instant = Instant.now()) {
        val prev = items[key(w)]
        items[key(w)] = ProgressWrite(
            kind = w.kind, id = w.id,
            positionS = w.positionS ?: prev?.positionS,
            durationS = w.durationS ?: prev?.durationS,
            played = w.played ?: prev?.played,
            speed = w.speed ?: prev?.speed,
            updatedAt = w.updatedAt ?: now.toString(),
        )
    }

    @Synchronized
    fun take(): List<ProgressWrite> = items.values.toList().also { items.clear() }

    /** Put back writes that failed to send, unless a newer write for the same item arrived meanwhile. */
    @Synchronized
    fun restore(ws: List<ProgressWrite>) {
        for (w in ws) {
            val cur = items[key(w)]
            if (cur == null || (cur.updatedAt ?: "") < (w.updatedAt ?: "")) items[key(w)] = w
        }
    }

    @Synchronized
    fun snapshot(): List<ProgressWrite> = items.values.toList()

    @Synchronized
    fun get(kind: String, id: String): ProgressWrite? = items["$kind:$id"]

    val size: Int @Synchronized get() = items.size
}
