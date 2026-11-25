package org.homebeat.wear.offline

import org.homebeat.app.data.api.Track

/** Pure planning for an offline sync, so the storage rules can be unit-tested. */
object OfflinePlan {
    data class Plan(
        /** Tracks to fetch now, in list order. */
        val download: List<Track>,
        /** Track ids whose files stay (already downloaded and still wanted). */
        val keep: Set<String>,
        /** Wanted tracks that don't fit in the budget. */
        val skipped: Int,
    )

    /** Expected Opus size: duration × bitrate, plus 5 % for container overhead. */
    fun estimateBytes(t: Track, kbps: Int): Long =
        ((t.durationS.coerceAtLeast(30.0)) * kbps * 1000 / 8 * 1.05).toLong()

    /**
     * Walks the wanted tracks in order (lists in the order the user added them, tracks in list
     * order) and keeps taking them while they fit in [budgetBytes]. Files already on the watch count
     * at their real size. Once one track doesn't fit, later ones are skipped too, so the set on the
     * watch is always a prefix of each list rather than a random sprinkle.
     */
    fun plan(wanted: List<Track>, existing: Map<String, Long>, budgetBytes: Long, kbps: Int): Plan {
        val seen = HashSet<String>()
        val download = mutableListOf<Track>()
        val keep = HashSet<String>()
        var used = 0L
        var skipped = 0
        var full = false
        for (t in wanted) {
            if (!seen.add(t.id)) continue
            if (full) { skipped++; continue }
            val size = existing[t.id] ?: estimateBytes(t, kbps)
            if (used + size > budgetBytes) { full = true; skipped++; continue }
            used += size
            if (existing.containsKey(t.id)) keep += t.id else download += t
        }
        return Plan(download, keep, skipped)
    }

    /** Budget = min(user cap, what we already use + free space − reserve). Never negative. */
    fun budget(capBytes: Long, usedBytes: Long, freeBytes: Long, reserveBytes: Long): Long =
        minOf(capBytes, usedBytes + freeBytes - reserveBytes).coerceAtLeast(0)
}
