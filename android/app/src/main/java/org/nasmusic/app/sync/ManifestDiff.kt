package org.nasmusic.app.sync

import org.nasmusic.app.data.api.AudioFormat
import org.nasmusic.app.data.api.ManifestItem

/** What the phone currently holds for one track. */
data class LocalTrack(val trackId: String, val format: AudioFormat, val bytes: Long, val complete: Boolean)

data class SyncPlan(
    /** Missing, partial, or held in the wrong format. Mix tracks first, then favourites. */
    val toDownload: List<ManifestItem>,
    /** Track ids held locally (complete or partial) that the manifest no longer lists. */
    val toDelete: List<String>,
    /** Already complete in the right format. */
    val keep: List<ManifestItem>,
)

object ManifestDiff {
    fun plan(manifest: List<ManifestItem>, local: List<LocalTrack>): SyncPlan {
        val localById = local.associateBy { it.trackId }
        // A track may appear twice (e.g. as mix + favourite); keep the first, prefer FLAC if any entry wants it.
        val wanted = LinkedHashMap<String, ManifestItem>()
        for (item in manifest) {
            val prev = wanted[item.trackId]
            wanted[item.trackId] = when {
                prev == null -> item
                item.format == AudioFormat.FLAC && prev.format != AudioFormat.FLAC -> prev.copy(format = AudioFormat.FLAC)
                else -> prev
            }
        }
        val keep = mutableListOf<ManifestItem>()
        val download = mutableListOf<ManifestItem>()
        for (item in wanted.values) {
            val l = localById[item.trackId]
            if (l != null && l.complete && l.format == item.format) keep += item else download += item
        }
        val toDelete = local.map { it.trackId }.filter { it !in wanted }
        val ordered = download.sortedBy { if (it.why == "mix") 0 else 1 } // stable sort keeps manifest order
        return SyncPlan(ordered, toDelete, keep)
    }

    /**
     * Greedily picks downloads (in order) that fit under [limitBytes], given [usedBytes] already
     * held by kept tracks. Items that do not fit are skipped, smaller later ones may still fit.
     */
    fun fitToLimit(
        candidates: List<Pair<ManifestItem, Long>>,
        usedBytes: Long,
        limitBytes: Long,
    ): List<ManifestItem> {
        var used = usedBytes
        val out = mutableListOf<ManifestItem>()
        for ((item, size) in candidates) {
            if (used + size <= limitBytes) {
                out += item
                used += size
            }
        }
        return out
    }

    /** Bytes still to fetch for [item]: the server's size when given, else a local estimate, minus any partial file. */
    fun plannedBytes(item: ManifestItem, partialBytes: Long, opusKbps: Int): Long {
        val total = item.bytes ?: estimateBytes(item.format, item.durationS, null, opusKbps)
        return (total - partialBytes).coerceAtLeast(0)
    }

    /** A finished download is checked against the manifest size only when the server says it is exact. */
    fun sizeMismatch(item: ManifestItem, actualBytes: Long): Boolean =
        item.bytesExact && item.bytes != null && item.bytes != actualBytes

    /** Size estimate when the manifest has no size: FLAC uses the original size; Opus ≈ bitrate × duration + 3 % container overhead. */
    fun estimateBytes(format: AudioFormat, durationS: Double?, flacBytes: Long?, opusKbps: Int): Long = when (format) {
        AudioFormat.FLAC -> flacBytes ?: ((durationS ?: 300.0) * 1411 * 1000 / 8).toLong()
        AudioFormat.OPUS -> ((durationS ?: 300.0) * opusKbps * 1000 / 8 * 1.03).toLong()
    }
}
