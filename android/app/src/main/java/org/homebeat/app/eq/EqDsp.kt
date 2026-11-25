package org.homebeat.app.eq

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt

/** RBJ-cookbook biquad, transposed direct form II, per channel state. */
class Biquad(private val b0: Double, private val b1: Double, private val b2: Double, private val a1: Double, private val a2: Double, channels: Int) {
    private val z1 = DoubleArray(channels)
    private val z2 = DoubleArray(channels)

    /** Magnitude response in dB at `f` (tests). */
    fun responseDb(fs: Double, f: Double): Double {
        val w = 2 * PI * f / fs
        val nr = b0 + b1 * cos(w) + b2 * cos(2 * w)
        val ni = -(b1 * sin(w) + b2 * sin(2 * w))
        val dr = 1 + a1 * cos(w) + a2 * cos(2 * w)
        val di = -(a1 * sin(w) + a2 * sin(2 * w))
        return 10 * kotlin.math.log10((nr * nr + ni * ni) / (dr * dr + di * di))
    }

    fun process(x: Double, ch: Int): Double {
        val y = b0 * x + z1[ch]
        z1[ch] = b1 * x - a1 * y + z2[ch]
        z2[ch] = b2 * x - a2 * y
        return y
    }

    companion object {
        fun peaking(fs: Double, f0: Double, gainDb: Double, q: Double, channels: Int): Biquad {
            val a = 10.0.pow(gainDb / 40)
            val w0 = 2 * PI * f0 / fs
            val alpha = sin(w0) / (2 * q)
            val a0 = 1 + alpha / a
            return Biquad(
                (1 + alpha * a) / a0, (-2 * cos(w0)) / a0, (1 - alpha * a) / a0,
                (-2 * cos(w0)) / a0, (1 - alpha / a) / a0, channels,
            )
        }

        /** Shelf with an explicit Q (AutoEq's LSC/HSC). */
        fun shelfQ(fs: Double, f0: Double, gainDb: Double, q: Double, high: Boolean, channels: Int): Biquad {
            val a = 10.0.pow(gainDb / 40)
            val w0 = 2 * PI * f0.coerceAtMost(fs * 0.49) / fs
            val cw = cos(w0)
            val alpha = sin(w0) / (2 * q.coerceAtLeast(0.05))
            val sq = 2 * sqrt(a) * alpha
            val sg = if (high) -1.0 else 1.0
            val a0 = a + 1 + sg * (a - 1) * cw + sq
            return Biquad(
                a * (a + 1 - sg * (a - 1) * cw + sq) / a0,
                sg * 2 * a * (a - 1 - sg * (a + 1) * cw) / a0,
                a * (a + 1 - sg * (a - 1) * cw - sq) / a0,
                -sg * 2 * (a - 1 + sg * (a + 1) * cw) / a0,
                (a + 1 + sg * (a - 1) * cw - sq) / a0,
                channels,
            )
        }

        fun parametric(f: ParametricFilter, fs: Double, channels: Int): Biquad? = when (f.type.uppercase()) {
            "PK" -> peaking(fs, f.fc.coerceAtMost(fs * 0.49), f.gainDb, f.q.coerceAtLeast(0.05), channels)
            "LSC" -> shelfQ(fs, f.fc, f.gainDb, f.q, high = false, channels)
            "HSC" -> shelfQ(fs, f.fc, f.gainDb, f.q, high = true, channels)
            else -> null
        }

        fun lowShelf(fs: Double, f0: Double, gainDb: Double, channels: Int, s: Double = 1.0): Biquad {
            val a = 10.0.pow(gainDb / 40)
            val w0 = 2 * PI * f0 / fs
            val cw = cos(w0)
            val alpha = sin(w0) / 2 * sqrt((a + 1 / a) * (1 / s - 1) + 2)
            val sa = 2 * sqrt(a) * alpha
            val a0 = (a + 1) + (a - 1) * cw + sa
            return Biquad(
                a * ((a + 1) - (a - 1) * cw + sa) / a0,
                2 * a * ((a - 1) - (a + 1) * cw) / a0,
                a * ((a + 1) - (a - 1) * cw - sa) / a0,
                -2 * ((a - 1) + (a + 1) * cw) / a0,
                ((a + 1) + (a - 1) * cw - sa) / a0,
                channels,
            )
        }
    }
}

/**
 * The whole effect chain for one stream, on interleaved float samples in −1..1:
 * preamp → 10 peaking bands → bass shelf → widening (stereo) → mono → balance → soft clip.
 * Built for a given sample rate / channel count; rebuild when settings change.
 */
class EqEngine(val settings: EqSettings, val sampleRate: Int, val channels: Int) {
    private val filters: List<Biquad> = buildList {
        if (!settings.enabled) return@buildList
        val fs = sampleRate.toDouble()
        settings.headphone?.filters?.forEach { f -> Biquad.parametric(f, fs, channels)?.let(::add) }
        EqSettings.BANDS_HZ.forEachIndexed { i, f ->
            val g = settings.bandsDb.getOrElse(i) { 0f }.toDouble()
            // Bands above Nyquist are skipped (e.g. 16 kHz at 22.05 kHz sample rate).
            if (g != 0.0 && f < fs * 0.45) add(Biquad.peaking(fs, f.toDouble(), g, Q, channels))
        }
        if (settings.bassBoost > 0) add(Biquad.lowShelf(fs, 100.0, settings.bassBoost / 100.0 * 12.0, channels))
    }
    private val preamp = if (settings.enabled) {
        10.0.pow((settings.preampDb + (settings.headphone?.preampDb?.coerceAtMost(0.0) ?: 0.0)) / 20.0)
    } else 1.0
    private val width = if (settings.enabled) settings.surround / 100.0 else 0.0
    private val leftGain = if (settings.enabled) (1.0 - settings.balance).coerceIn(0.0, 1.0) else 1.0
    private val rightGain = if (settings.enabled) (1.0 + settings.balance).coerceIn(0.0, 1.0) else 1.0
    private val mono = settings.enabled && settings.mono

    /** True when processing would change nothing (lets the processor stay inactive). */
    val isIdentity: Boolean = filters.isEmpty() && preamp == 1.0 && width == 0.0 && !mono && leftGain == 1.0 && rightGain == 1.0

    fun process(buf: FloatArray, frames: Int) {
        if (isIdentity) return
        for (f in 0 until frames) {
            val base = f * channels
            for (ch in 0 until channels) {
                var x = buf[base + ch] * preamp
                for (flt in filters) x = flt.process(x, ch)
                buf[base + ch] = x.toFloat()
            }
            if (channels == 2) {
                var l = buf[base].toDouble()
                var r = buf[base + 1].toDouble()
                if (width > 0) {
                    val m = (l + r) / 2
                    val s = (l - r) / 2 * (1 + width * 1.5)
                    l = m + s; r = m - s
                }
                if (mono) { val m = (l + r) / 2; l = m; r = m }
                buf[base] = softClip(l * leftGain)
                buf[base + 1] = softClip(r * rightGain)
            } else {
                for (ch in 0 until channels) buf[base + ch] = softClip(buf[base + ch].toDouble())
            }
        }
    }

    private fun softClip(x: Double): Float = when {
        x > 1.0 -> 1f
        x < -1.0 -> -1f
        x > 0.9 || x < -0.9 -> {
            // gentle knee in the last 10 % to avoid hard edges when boosting
            val s = if (x > 0) 1 else -1
            val e = (kotlin.math.abs(x) - 0.9) / 0.1
            (s * (0.9 + 0.1 * (e - e * e / 4))).toFloat()
        }
        else -> x.toFloat()
    }

    companion object { const val Q = 1.41 }
}
