package org.homebeat.app.playback

import org.homebeat.app.data.api.TrackMix
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToLong
import kotlin.math.sin

/**
 * DJ automix transitions: the pure planning maths, same rules as the web player (web/src/lib/automix.ts).
 *
 * A plan: when the outgoing track reaches [DjPlan.outStartS] (its media time), start the incoming one at
 * [DjPlan.inStartS] at [DjPlan.rate] × the user's speed (Media3 PlaybackParameters keeps the pitch), so its
 * downbeats land on the outgoing downbeats. Over [DjPlan.durationS] volumes and bass follow the curves below;
 * then the incoming speed eases back to 1 over [RAMP_BEATS]. Without a beat-matchable pair but with mix points:
 * an equal-power crossfade between the mix points. Without analysis: null → the normal crossfade.
 */
data class DjPlan(
    val beatmatch: Boolean,
    val outStartS: Double,
    val inStartS: Double,
    val durationS: Double,
    val beats: Int,
    val rate: Double,
    val multiplier: Double,
    val inBeatS: Double,
)

data class DjOptions(val beats: Int, val allowTempo: Boolean, val fallbackS: Double)

object Automix {
    const val MAX_STRETCH = 0.08
    const val NO_STRETCH_TOLERANCE = 0.003
    val DJ_BEATS = listOf(8, 16, 32)
    const val RAMP_BEATS = 32
    const val BASS_KILL_DB = -26.0
    const val SEEK_ERROR_S = 0.12
    private const val MIN_BEATS = 8

    fun djBeats(v: Int): Int = DJ_BEATS.minByOrNull { abs(it - v) } ?: 16

    /** (multiplier, rate) making b's beats match a's: the smallest stretch among m ∈ {1, 2, ½}. */
    fun tempoMatch(aBpm: Double, bBpm: Double): Pair<Double, Double> {
        var best = 1.0 to aBpm / bBpm
        for (m in listOf(2.0, 0.5)) {
            val r = aBpm / (bBpm * m)
            if (abs(ln(r)) < abs(ln(best.second)) - 1e-9) best = m to r
        }
        return best
    }

    enum class Snap { FLOOR, CEIL, NEAREST }

    fun snapBar(t: Double, downbeat: Double, bar: Double, mode: Snap = Snap.NEAREST): Double {
        val x = (t - downbeat) / bar
        val n = when (mode) {
            Snap.FLOOR -> floor(x + 1e-6)
            Snap.CEIL -> ceil(x - 1e-6)
            Snap.NEAREST -> Math.round(x).toDouble()
        }
        return downbeat + max(0.0, n) * bar
    }

    private fun gridOk(m: TrackMix) = m.gridStable && (m.bpm ?: 0.0) > 0 && m.downbeatS != null

    /** `a`/`b`: id, duration and analysis of the outgoing / incoming track. */
    fun plan(aId: String, aDur: Double, am: TrackMix?, bId: String, bDur: Double, bm: TrackMix?, o: DjOptions): DjPlan? {
        if (aId == bId || am == null || bm == null) return null
        val aMixIn = am.mixInS ?: return null
        val aMixOut = am.mixOutS ?: return null
        val bMixIn = bm.mixInS ?: return null
        val aLen = if (aDur > 0) aDur else Double.MAX_VALUE
        val bLen = if (bDur > 0) bDur else Double.MAX_VALUE
        val aEnd = min(am.lastBeatS ?: aLen, aLen)
        if (gridOk(am) && gridOk(bm)) {
            val (mult, rate) = tempoMatch(am.bpm!!, bm.bpm!!)
            val stretch = abs(rate - 1)
            if (if (o.allowTempo) stretch <= MAX_STRETCH else stretch <= NO_STRETCH_TOLERANCE) {
                val beat = 60.0 / am.bpm
                val bpb = am.beatsPerBar ?: 4
                val bar = beat * bpb
                var beats = djBeats(o.beats)
                var outStart = snapBar(aMixOut, am.downbeatS!!, bar)
                if (outStart + beats * beat > aEnd + 1e-6) outStart = snapBar(aEnd - beats * beat, am.downbeatS, bar, Snap.FLOOR)
                val earliest = max(aMixIn, (if (aDur > 0) aDur else aEnd) * 0.5)
                if (outStart < earliest) {
                    outStart = snapBar(max(aMixOut, earliest), am.downbeatS, bar, Snap.CEIL)
                    beats = (floor((aEnd - outStart) / beat / bpb) * bpb).toInt()
                }
                val inBeat = 60.0 / bm.bpm
                val inStart = snapBar(bMixIn, bm.downbeatS!!, inBeat * (bm.beatsPerBar ?: 4))
                val inRoom = (bLen - inStart) / rate
                if (beats * beat > inRoom) beats = (floor(inRoom / beat / bpb) * bpb).toInt()
                if (beats >= MIN_BEATS && outStart >= 0) {
                    return DjPlan(true, outStart, inStart, beats * beat, beats, rate, mult, inBeat)
                }
            }
        }
        val whole = if (aDur > 0) aDur else aEnd
        val len = max(1.0, minOf(o.fallbackS, whole / 3, bLen / 3))
        var outStart = aMixOut
        if (outStart + len > aEnd) outStart = aEnd - len
        outStart = max(outStart, whole * 0.5)
        if (outStart + len > whole + 1e-6) return null
        return DjPlan(false, outStart, max(0.0, bMixIn), len, 0, 1.0, 1.0, 0.0)
    }

    fun expectedIn(p: DjPlan, outPos: Double): Double = p.inStartS + (outPos - p.outStartS) * p.rate

    fun progress(p: DjPlan, outPos: Double): Double = ((outPos - p.outStartS) / p.durationS).coerceIn(0.0, 1.0)

    private fun s(x: Double) = sin(x.coerceIn(0.0, 1.0) * PI / 2)
    private fun c(x: Double) = cos(x.coerceIn(0.0, 1.0) * PI / 2)

    fun volIn(p: DjPlan, x: Double): Double = if (p.beatmatch) s(x * 2) else s(x)
    fun volOut(p: DjPlan, x: Double): Double = if (p.beatmatch) c((x - 0.5) * 2) else c(x)

    fun bassIn(p: DjPlan, x: Double): Double {
        if (!p.beatmatch) return 0.0
        val half = 0.5 / max(p.beats, 1)
        val y = ((x - (0.5 - half)) / (2 * half)).coerceIn(0.0, 1.0)
        return BASS_KILL_DB * (1 - y) + 0.0
    }

    fun bassOut(p: DjPlan, x: Double): Double = if (p.beatmatch) BASS_KILL_DB - bassIn(p, x) else 0.0

    fun rampRate(p: DjPlan, secondsAfter: Double): Double {
        if (!p.beatmatch || p.rate == 1.0) return 1.0
        val x = (secondsAfter / (RAMP_BEATS * p.inBeatS)).coerceIn(0.0, 1.0)
        return p.rate + (1 - p.rate) * (0.5 - 0.5 * cos(PI * x))
    }

    /** Phase lock: rate for the incoming track given its phase error (actual − expected, s); null = seek. */
    fun correctedRate(rate: Double, errorS: Double): Double? {
        if (abs(errorS) > SEEK_ERROR_S) return null
        if (abs(errorS) < 0.004) return rate
        return rate * (1 - (errorS * 0.4).coerceIn(-0.02, 0.02))
    }

    fun ms(seconds: Double): Long = (seconds * 1000).roundToLong()

    /**
     * Apply the server's order (POST /automix/order on tracks[start..]) to the list and its per-track reasons.
     * Null when the answer doesn't hold exactly the same ids (then keep the list as it was).
     */
    fun <T> reorder(items: List<T>, id: (T) -> String, start: Int, reasons: List<String?>?, orderedIds: List<String>): Pair<List<T>, List<String?>?>? {
        val rest = items.indices.drop(start)
        if (orderedIds.size != rest.size || orderedIds.sorted() != rest.map { id(items[it]) }.sorted()) return null
        val pool = rest.groupBy { id(items[it]) }.mapValues { it.value.toMutableList() }
        val order = items.indices.take(start) + orderedIds.map { pool.getValue(it).removeAt(0) }
        return order.map { items[it] } to reasons?.let { r -> order.map { r.getOrNull(it) } }
    }
}
