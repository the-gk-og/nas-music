package org.homebeat.app.sync

import kotlin.math.exp
import kotlin.math.roundToLong
import org.homebeat.app.data.api.AudioFormat

/** Smart-download quality. Opus bitrates are what /stream transcodes to; FLAC is the original file. */
enum class DownloadQuality(val key: String, val format: AudioFormat, val kbps: Int, val label: String) {
    OPUS_96("opus96", AudioFormat.OPUS, 96, "Opus 96"),
    OPUS_128("opus128", AudioFormat.OPUS, 128, "Opus 128"),
    OPUS_160("opus160", AudioFormat.OPUS, 160, "Opus 160"),
    OPUS_192("opus192", AudioFormat.OPUS, 192, "Opus 192"),
    OPUS_256("opus256", AudioFormat.OPUS, 256, "Opus 256"),
    FLAC("flac", AudioFormat.FLAC, 0, "Lossless");

    companion object {
        fun from(key: String?): DownloadQuality = entries.firstOrNull { it.key == key } ?: OPUS_128

        /** The quality a held file is in, from its stored format and bitrate. */
        fun of(format: String, kbps: Int): String = if (format == "flac") FLAC.key else "opus$kbps"
    }
}

/** How much space smart downloads may use: a fixed size, or a share of what is free on the phone. */
sealed interface StorageBudget {
    val key: String

    data class Fixed(val gb: Int) : StorageBudget {
        override val key get() = "gb:$gb"
    }

    data class PercentOfFree(val percent: Int) : StorageBudget {
        override val key get() = "pct:$percent"
    }

    /**
     * Bytes smart downloads may hold. For a share of free space, what smart downloads already hold
     * counts as "free" (it can be given back), so the budget doesn't shrink each night as it fills.
     */
    fun bytes(freeBytes: Long, smartHeldBytes: Long): Long = when (this) {
        is Fixed -> gb.toLong() * GB
        is PercentOfFree -> ((freeBytes + smartHeldBytes).coerceAtLeast(0) * (percent.coerceIn(1, 90) / 100.0)).roundToLong()
    }

    val label: String
        get() = when (this) {
            is Fixed -> "$gb GB"
            is PercentOfFree -> "$percent% of free space"
        }

    companion object {
        const val GB = 1024L * 1024 * 1024
        val CHOICES: List<StorageBudget> = listOf(Fixed(2), Fixed(5), Fixed(10), Fixed(20), PercentOfFree(25), PercentOfFree(50))
        val DEFAULT: StorageBudget = Fixed(5)

        fun parse(key: String?): StorageBudget {
            val (kind, n) = key?.split(':')?.takeIf { it.size == 2 }?.let { it[0] to it[1].toIntOrNull() } ?: return DEFAULT
            return when {
                n == null || n <= 0 -> DEFAULT
                kind == "gb" -> Fixed(n)
                kind == "pct" -> PercentOfFree(n.coerceAtMost(90))
                else -> DEFAULT
            }
        }
    }
}

/** One track the server suggests keeping offline (`GET /sync/smart`), with its size in the chosen quality. */
data class SmartCandidate(
    val trackId: String,
    /** favourite | mix | week_mix | recent | release_radar | foryou | predicted */
    val reasons: Set<String>,
    /** AI likelihood 0..1 (0 when unknown). */
    val score: Double,
    val bytes: Long,
    /** Days since last played, null if never / unknown. */
    val lastPlayedDaysAgo: Double? = null,
)

/** What the phone holds for one track, as the planner sees it. */
data class HeldTrack(
    val trackId: String,
    val bytes: Long,
    /** [DownloadQuality.key]-style: "opus128", "opus192", "flac", "opus160" for older downloads. */
    val quality: String,
    val complete: Boolean,
    /** Manual download or part of a collection kept offline: never evicted, not counted in the budget. */
    val protected: Boolean,
)

data class SmartPlan(
    /** Already held in the right quality and still wanted, best first. */
    val keep: List<SmartCandidate>,
    /** To fetch, most likely to be played first. */
    val download: List<SmartCandidate>,
    /** Held smart tracks no longer wanted, least likely to be played first. */
    val evict: List<String>,
    val budgetBytes: Long,
    /** Bytes the kept + downloaded set will take. */
    val plannedBytes: Long,
    /** Wanted candidates that didn't fit the budget. */
    val overBudget: Int,
)

/**
 * Chooses the rolling offline set. Every candidate gets a priority (how likely it is to be played,
 * from why it's suggested plus the AI's score); the set is filled best-first within the budget, and
 * everything else the phone holds is evicted, least likely first. Manual downloads and tracks of
 * collections kept offline are never touched and don't count against the budget.
 */
object SmartPlanner {
    private val WEIGHTS = mapOf(
        "favourite" to 1.0,
        "mix" to 0.9,
        "recent" to 0.7,
        "week_mix" to 0.6,
        "release_radar" to 0.55,
        "foryou" to 0.5,
        "predicted" to 0.3,
    )

    /** Small preference for what's already on the phone, so the set doesn't churn night to night. */
    const val HOLD_BONUS = 0.05

    /** Recently played tracks fade from "recent" over about two weeks (but keep half their weight). */
    private const val RECENT_HALF_LIFE_DAYS = 14.0

    fun priority(c: SmartCandidate): Double {
        val base = c.reasons.maxOfOrNull { r ->
            val w = WEIGHTS[r] ?: 0.0
            if (r == "recent" && c.lastPlayedDaysAgo != null) {
                w * (0.5 + 0.5 * exp(-c.lastPlayedDaysAgo.coerceAtLeast(0.0) * Math.log(2.0) / RECENT_HALF_LIFE_DAYS))
            } else w
        } ?: 0.0
        // Several reasons together (e.g. favourite that's also in today's mix) nudge it up a little.
        val extra = 0.02 * (c.reasons.count { it in WEIGHTS } - 1).coerceIn(0, 3)
        return base + 0.5 * c.score.coerceIn(0.0, 1.0) + extra
    }

    fun plan(candidates: List<SmartCandidate>, held: List<HeldTrack>, quality: DownloadQuality, budgetBytes: Long): SmartPlan {
        val heldById = held.associateBy { it.trackId }
        val protectedIds = held.filter { it.protected }.map { it.trackId }.toSet()
        fun heldOk(id: String) = heldById[id]?.let { it.complete && it.quality == quality.key } == true

        val unique = LinkedHashMap<String, SmartCandidate>()
        for (c in candidates) {
            if (c.trackId in protectedIds) continue
            val prev = unique[c.trackId]
            unique[c.trackId] = if (prev == null) c else prev.copy(reasons = prev.reasons + c.reasons, score = maxOf(prev.score, c.score))
        }
        val prio = unique.values.associate { it.trackId to priority(it) + if (heldOk(it.trackId)) HOLD_BONUS else 0.0 }
        val ranked = unique.values.sortedWith(compareByDescending<SmartCandidate> { prio.getValue(it.trackId) }.thenBy { it.trackId })

        var used = 0L
        val keep = mutableListOf<SmartCandidate>()
        val download = mutableListOf<SmartCandidate>()
        var over = 0
        for (c in ranked) {
            val size = if (heldOk(c.trackId)) heldById.getValue(c.trackId).bytes else c.bytes
            if (used + size > budgetBytes) { over++; continue }
            used += size
            if (heldOk(c.trackId)) keep += c else download += c
        }
        val wanted = (keep + download).map { it.trackId }.toSet()
        val evict = held.filter { !it.protected && it.trackId !in wanted }
            .sortedWith(compareBy<HeldTrack> { prio[it.trackId] ?: -1.0 }.thenBy { it.trackId })
            .map { it.trackId }
        return SmartPlan(keep, download, evict, budgetBytes, used, over)
    }

    /** Estimated download size of a candidate in [quality]: FLAC uses the original size; Opus ≈ bitrate × duration. */
    fun estimateBytes(quality: DownloadQuality, durationS: Double?, flacBytes: Long?): Long =
        ManifestDiff.estimateBytes(quality.format, durationS, flacBytes, quality.kbps.takeIf { it > 0 } ?: 160)
}
