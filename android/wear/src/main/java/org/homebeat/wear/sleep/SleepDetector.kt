package org.homebeat.wear.sleep

import kotlin.math.acos
import kotlin.math.sqrt

/**
 * Sleep onset from the watch's own sensors, in the spirit of wrist actigraphy: the night is cut into
 * 30-second epochs, each marked still or moving from the accelerometer, with the median heart rate.
 *
 * You are taken to be asleep when you have been still for a while and your heart rate has dropped
 * below what it was while you were settling down. Without heart rate (no sensor, or permission
 * refused) a longer stillness alone is enough. Pure Kotlin so it can be unit tested.
 */
data class Epoch(
    /** Wall clock (ms) at the end of the epoch. */
    val endMs: Long,
    val still: Boolean,
    /** Median bpm in this epoch, or null when the sensor gave nothing usable. */
    val heartRate: Float?,
)

/** What [SleepDetector] thinks after each epoch. */
data class SleepVerdict(
    val asleep: Boolean,
    /** When you last moved (start of the current still stretch), or null while moving. */
    val stillSinceMs: Long?,
    /** Median bpm while settling down, if known. */
    val baselineBpm: Float? = null,
    /** Median bpm over the last few minutes, if known. */
    val recentBpm: Float? = null,
    /** "heart rate", "stillness" or null: what decided [asleep]. */
    val reason: String? = null,
)

class SleepDetector(private val cfg: Config = Config()) {
    data class Config(
        val epochMs: Long = 30_000,
        /** Still this long (and heart rate down) = asleep. */
        val stillMsWithHeartRate: Long = 10 * 60_000L,
        /** Heart rate this much below the settling-down median counts as "dropped". */
        val heartRateDrop: Float = 0.07f,
        /** "Recent" heart rate = median over this long. */
        val recentMs: Long = 5 * 60_000L,
        /** Baseline = median heart rate from this long before the still stretch began... */
        val baselineBeforeMs: Long = 10 * 60_000L,
        /** ...to this long into it (the first minutes of lying still are usually still awake). */
        val baselineIntoStillMs: Long = 5 * 60_000L,
        /** Fewest epochs with a heart rate for the baseline / recent medians to count. */
        val minHeartRateEpochs: Int = 4,
        /** Without heart rate: still this long = asleep. */
        val stillMsWithoutHeartRate: Long = 20 * 60_000L,
        /** With heart rate that never drops: still this long is taken as asleep anyway. */
        val stillMsAlone: Long = 45 * 60_000L,
        /** Epochs kept (2 h). */
        val keepMs: Long = 2 * 60 * 60_000L,
    )

    private val epochs = ArrayDeque<Epoch>()

    fun reset() = epochs.clear()

    fun add(e: Epoch): SleepVerdict {
        epochs.addLast(e)
        while (epochs.isNotEmpty() && e.endMs - epochs.first().endMs > cfg.keepMs) epochs.removeFirst()
        return verdict()
    }

    fun verdict(): SleepVerdict {
        val last = epochs.lastOrNull() ?: return SleepVerdict(false, null)
        if (!last.still) return SleepVerdict(false, null)
        // The still stretch: trailing still epochs. It "began" at the start of its first epoch.
        val firstStill = epochs.indexOfLast { !it.still } + 1
        val stillSince = epochs[firstStill].endMs - cfg.epochMs
        val stillFor = last.endMs - stillSince

        val baseline = median(
            epochs.filter { it.endMs > stillSince - cfg.baselineBeforeMs && it.endMs <= stillSince + cfg.baselineIntoStillMs }
                .mapNotNull { it.heartRate },
        )
        val recent = median(epochs.filter { it.endMs > last.endMs - cfg.recentMs }.mapNotNull { it.heartRate })
        val anyHeartRate = epochs.drop(firstStill).any { it.heartRate != null }

        val v = SleepVerdict(false, stillSince, baseline, recent)
        return when {
            // Recent must not overlap the baseline window, or it is just comparing a value with itself.
            stillFor >= cfg.stillMsWithHeartRate && baseline != null && recent != null &&
                last.endMs - cfg.recentMs >= stillSince + cfg.baselineIntoStillMs &&
                recent <= baseline * (1f - cfg.heartRateDrop) -> v.copy(asleep = true, reason = "heart rate")
            !anyHeartRate && stillFor >= cfg.stillMsWithoutHeartRate -> v.copy(asleep = true, reason = "stillness")
            stillFor >= cfg.stillMsAlone -> v.copy(asleep = true, reason = "stillness")
            else -> v
        }
    }

    private fun median(xs: List<Float>): Float? {
        if (xs.size < cfg.minHeartRateEpochs) return null
        val s = xs.sorted()
        return if (s.size % 2 == 1) s[s.size / 2] else (s[s.size / 2 - 1] + s[s.size / 2]) / 2f
    }
}

/**
 * Turns raw accelerometer and heart-rate samples into [Epoch]s.
 *
 * An epoch is "moving" when the wrist jolts (the acceleration magnitude strays from the epoch's median by
 * more than [joltMs2] in more than [maxJoltFraction] of samples) or turns (the average gravity direction
 * moved more than [turnDegrees] since the previous epoch, e.g. rolling over). Breathing and a resting
 * pulse stay well under both.
 */
class EpochBuilder(
    private val joltMs2: Float = 0.35f,
    private val maxJoltFraction: Float = 0.02f,
    private val turnDegrees: Float = 12f,
) {
    private val mags = ArrayList<Float>(512)
    private var sx = 0.0
    private var sy = 0.0
    private var sz = 0.0
    private val hrs = ArrayList<Float>(64)
    private var lastGravity: Triple<Double, Double, Double>? = null

    fun addMotion(x: Float, y: Float, z: Float) {
        mags += sqrt(x * x + y * y + z * z)
        sx += x; sy += y; sz += z
    }

    /** [accurate] = the sensor reported at least low accuracy (not "unreliable" / "no contact"). */
    fun addHeartRate(bpm: Float, accurate: Boolean) {
        if (accurate && bpm in 30f..220f) hrs += bpm
    }

    /** Closes the epoch ending at [endMs]. An epoch with no motion samples counts as still. */
    fun close(endMs: Long): Epoch {
        var still = true
        if (mags.isNotEmpty()) {
            val med = mags.sorted()[mags.size / 2]
            val jolts = mags.count { kotlin.math.abs(it - med) > joltMs2 }
            if (jolts > mags.size * maxJoltFraction) still = false
            val n = mags.size.toDouble()
            val g = Triple(sx / n, sy / n, sz / n)
            lastGravity?.let { if (angleDegrees(it, g) > turnDegrees) still = false }
            lastGravity = g
        }
        val hr = if (hrs.isEmpty()) null else hrs.sorted()[hrs.size / 2]
        mags.clear(); hrs.clear(); sx = 0.0; sy = 0.0; sz = 0.0
        return Epoch(endMs, still, hr)
    }

    companion object {
        fun angleDegrees(a: Triple<Double, Double, Double>, b: Triple<Double, Double, Double>): Double {
            val la = sqrt(a.first * a.first + a.second * a.second + a.third * a.third)
            val lb = sqrt(b.first * b.first + b.second * b.second + b.third * b.third)
            if (la < 1e-6 || lb < 1e-6) return 0.0
            val cos = ((a.first * b.first + a.second * b.second + a.third * b.third) / (la * lb)).coerceIn(-1.0, 1.0)
            return Math.toDegrees(acos(cos))
        }
    }
}
