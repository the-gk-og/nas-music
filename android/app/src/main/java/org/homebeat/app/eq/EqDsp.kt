package org.homebeat.app.eq

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt

/** RBJ-cookbook biquad, transposed direct form II, per channel state. */
class Biquad(b0: Double, b1: Double, b2: Double, a1: Double, a2: Double, channels: Int) {
    private var b0 = b0; private var b1 = b1; private var b2 = b2; private var a1 = a1; private var a2 = a2
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

    /** Take another filter's coefficients but keep this one's state, so a gain change doesn't click. */
    fun retune(o: Biquad) { b0 = o.b0; b1 = o.b1; b2 = o.b2; a1 = o.a1; a2 = o.a2 }

    fun clear() { z1.fill(0.0); z2.fill(0.0) }

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

        /** RBJ low / high pass (12 dB/oct; q = 0.707 is Butterworth). */
        fun pass(fs: Double, f0: Double, q: Double, high: Boolean, channels: Int): Biquad {
            val w0 = 2 * PI * f0.coerceIn(5.0, fs * 0.49) / fs
            val cw = cos(w0)
            val alpha = sin(w0) / (2 * q.coerceAtLeast(0.05))
            val a0 = 1 + alpha
            val k = if (high) (1 + cw) / 2 else (1 - cw) / 2
            val b1 = if (high) -(1 + cw) else 1 - cw
            return Biquad(k / a0, b1 / a0, k / a0, -2 * cw / a0, (1 - alpha) / a0, channels)
        }

        /** Butterworth section Qs for 24 and 48 dB/oct. */
        private val BUTTERWORTH = mapOf(24 to doubleArrayOf(0.5412, 1.3066), 48 to doubleArrayOf(0.5098, 0.6013, 0.9000, 2.5629))

        /** The biquads for one parametric band, with the peak/shelf gain overridden by [gainDb] (for gliding). */
        fun band(b: EqBand, fs: Double, channels: Int, gainDb: Double = b.gainDb): List<Biquad> = when (b.type) {
            "LP", "HP" -> {
                val qs = BUTTERWORTH[b.slope] ?: doubleArrayOf(b.q)
                qs.map { pass(fs, b.fc, it, b.type == "HP", channels) }
            }
            else -> listOfNotNull(parametric(ParametricFilter(b.type, b.fc, gainDb, b.q), fs, channels))
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
 * auto headroom + preamp → AutoEq headphone filters → 10 peaking bands (graphic mode) or the user's parametric bands
 * (parametric mode) → bass shelf → stereo width / mono → balance → limiter.
 *
 * What keeps it clean:
 * - **Auto headroom.** The peak boost of the whole curve (bands + bass shelf + headphone correction) is taken off the preamp,
 *   so a +6 dB bass boost no longer drives the signal into clipping. The user's preamp is a trim on top.
 * - **No clicks.** Settings changes go through [update]; filters keep their state and gains glide at [GLIDE_DB_PER_S]
 *   in [BLOCK]-frame steps, gains/mix ramp per sample.
 * - **A real limiter** (stereo-linked, instant attack, smooth release) instead of a clipper catches what's left
 *   (widening, a positive preamp).
 */
class EqEngine(initial: EqSettings, val sampleRate: Int, val channels: Int) {
    var settings: EqSettings = initial
        private set
    private val fs = sampleRate.toDouble()

    // Bands above ~Nyquist are skipped (e.g. 16 kHz at a 22.05 kHz sample rate).
    private val bandIdx = EqSettings.BANDS_HZ.indices.filter { EqSettings.BANDS_HZ[it] < fs * 0.45 }
    private val bands = bandIdx.map { Biquad.peaking(fs, EqSettings.BANDS_HZ[it].toDouble(), 0.0, Q, channels) }
    private val bass = Biquad.lowShelf(fs, BASS_HZ, 0.0, channels)
    private var hpProfile: HeadphoneProfile? = null
    private var hpFilters: List<Biquad> = emptyList()

    /** One parametric band in the chain. Peaks/shelves glide [cur] dB; pass filters switch. Dying slots fade out, then go. */
    private class Slot(var band: EqBand, val filters: List<Biquad>, var cur: Double, var dying: Boolean = false)
    private var slots: List<Slot> = emptyList()
    private var paramDirty = false

    // Current (gliding) gains in dB; hpMix scales the headphone filters 0..1.
    private val bandDb = DoubleArray(bandIdx.size)
    private var bassDb = 0.0
    private var hpMix = 0.0
    private var curPreamp = 1.0
    private var curSide = 1.0
    private var curL = 1.0
    private var curR = 1.0
    private var limGain = 1.0
    private val release = kotlin.math.exp(-1.0 / (RELEASE_S * fs))
    private var stateClear = true

    /** Gain (dB) the auto headroom takes off right now; ≤ 0. */
    var autoHeadroomDb = 0.0
        private set

    /** Frequencies the auto headroom checks: a log sweep plus the band centres. */
    private val probeHz: DoubleArray = run {
        val top = minOf(20_000.0, fs * 0.45)
        val log = DoubleArray(PROBES) { i -> 20.0 * (top / 20.0).pow(i / (PROBES - 1.0)) }
        (log + bandIdx.map { EqSettings.BANDS_HZ[it].toDouble() } + BASS_HZ / 2).sorted().toDoubleArray()
    }

    init {
        update(initial)
        snap()
    }

    /** New settings: filters retune and gains glide there over the next few ms. */
    fun update(s: EqSettings) {
        if (s.headphone != hpProfile) {
            val p = s.headphone
            // A different profile replaces the filters; removing one glides it out first.
            if (p != null) {
                hpProfile = p
                hpFilters = p.filters.mapNotNull { Biquad.parametric(it.copy(gainDb = 0.0), fs, channels) }
                hpMix = 0.0
            }
        }
        settings = s
        updateSlots()
    }

    /** Keep a slot (and its filter state) where the band at that index still has the same shape; else replace it. */
    private fun updateSlots() {
        val want = if (settings.isParametric) settings.parametric.map { it.clamped() }.take(EqSettings.MAX_PARAM_BANDS) else emptyList()
        val live = slots.filter { !it.dying }
        val next = ArrayList<Slot>()
        want.forEachIndexed { i, b ->
            val old = live.getOrNull(i)
            if (old != null && old.band.type == b.type && old.band.slope == b.slope && (!b.isPass || old.band.on == b.on)) {
                old.band = b; next += old
            } else {
                old?.let { if (!it.band.isPass) { it.dying = true; next += it } }
                next += Slot(b, Biquad.band(b, fs, channels, 0.0), 0.0)
            }
        }
        // Peaks/shelves that went away fade out; pass filters just stop.
        live.drop(want.size).filter { !it.band.isPass }.forEach { it.dying = true; next += it }
        slots.filter { it.dying && it !in next }.forEach { next += it }
        slots = next
        paramDirty = true
    }

    private fun tSlot(sl: Slot) = if (settings.enabled && !sl.dying && sl.band.on && !sl.band.isPass) sl.band.gainDb else 0.0
    private fun slotRuns(sl: Slot) = if (sl.band.isPass) settings.enabled && !sl.dying && sl.band.on else sl.cur != 0.0 || tSlot(sl) != 0.0

    private fun tBand(j: Int) = if (settings.enabled && !settings.isParametric) settings.bandsDb.getOrElse(bandIdx[j]) { 0f }.toDouble().coerceIn(-12.0, 12.0) else 0.0
    private fun tBass() = if (settings.enabled) settings.bassBoost.coerceIn(0, 100) / 100.0 * BASS_MAX_DB else 0.0
    private fun tHp() = if (settings.enabled && settings.headphone != null) 1.0 else 0.0
    private fun tUserPreampDb() = if (settings.enabled) settings.preampDb.toDouble() else 0.0
    private fun tSide() = when {
        !settings.enabled -> 1.0
        settings.mono -> 0.0
        else -> 1.0 + settings.surround.coerceIn(0, 100) / 100.0
    }
    private fun tL() = if (settings.enabled) (1.0 - settings.balance).coerceIn(0.0, 1.0) else 1.0
    private fun tR() = if (settings.enabled) (1.0 + settings.balance).coerceIn(0.0, 1.0) else 1.0

    /** Jump straight to the targets (new stream: nothing to glide from). */
    private fun snap() {
        for (j in bandDb.indices) bandDb[j] = tBand(j)
        bassDb = tBass(); hpMix = tHp()
        slots = slots.filter { !it.dying }.onEach { it.cur = tSlot(it) }
        if (hpMix == 0.0 && settings.headphone == null) { hpProfile = null; hpFilters = emptyList() }
        retune()
        curPreamp = targetPreamp(); curSide = tSide(); curL = tL(); curR = tR()
    }

    private fun retune() {
        for (j in bands.indices) bands[j].retune(Biquad.peaking(fs, EqSettings.BANDS_HZ[bandIdx[j]].toDouble(), bandDb[j], Q, channels))
        bass.retune(Biquad.lowShelf(fs, BASS_HZ, bassDb, channels))
        hpProfile?.filters?.mapNotNull { Biquad.parametric(it.copy(gainDb = it.gainDb * hpMix), fs, channels) }
            ?.forEachIndexed { i, b -> hpFilters.getOrNull(i)?.retune(b) }
        for (sl in slots) Biquad.band(sl.band, fs, channels, sl.cur).forEachIndexed { i, b -> sl.filters.getOrNull(i)?.retune(b) }
        paramDirty = false
        autoHeadroomDb = -peakResponseDb().coerceAtLeast(0.0)
    }

    /** Highest boost of the current curve, 20 Hz … 20 kHz. */
    private fun peakResponseDb(): Double {
        val all = active()
        if (all.isEmpty()) return 0.0
        var peak = Double.NEGATIVE_INFINITY
        for (f in probeHz) {
            var db = 0.0
            for (b in all) db += b.responseDb(fs, f)
            if (db > peak) peak = db
        }
        return peak
    }

    /** Every filter in the chain; all run while the EQ is active, so none has stale state when its gain moves off 0. */
    private fun chain(): List<Biquad> = buildList {
        addAll(hpFilters); addAll(bands)
        for (sl in slots) if (slotRuns(sl)) addAll(sl.filters)
        add(bass)
    }

    /** Filters that shape the curve right now (for the headroom maths). */
    private fun active(): List<Biquad> = buildList {
        if (hpMix > 0) addAll(hpFilters)
        for (j in bands.indices) if (bandDb[j] != 0.0) add(bands[j])
        if (bassDb != 0.0) add(bass)
        for (sl in slots) if (slotRuns(sl) && (sl.band.isPass || sl.cur != 0.0)) addAll(sl.filters)
    }

    private fun targetPreamp() = 10.0.pow((autoHeadroomDb + tUserPreampDb()) / 20.0)

    private fun neutralNow() = bandDb.all { it == 0.0 } && bassDb == 0.0 && hpMix == 0.0 && slots.none { slotRuns(it) } &&
        curPreamp == 1.0 && curSide == 1.0 && curL == 1.0 && curR == 1.0

    private fun atTargets() = bandDb.indices.all { bandDb[it] == tBand(it) } && bassDb == tBass() && hpMix == tHp() &&
        slots.all { it.cur == tSlot(it) && !it.dying }

    /** True when processing would change nothing (lets the processor pass audio straight through). */
    val isIdentity: Boolean
        get() = neutralNow() && atTargets() && targetPreamp() == 1.0 && tSide() == 1.0 && tL() == 1.0 && tR() == 1.0

    /** Move gliding gains one block towards their targets; true when filters were retuned. */
    private fun glide(frames: Int): Boolean {
        val step = GLIDE_DB_PER_S * frames / fs
        var moved = false
        fun g(cur: Double, t: Double): Double {
            if (cur == t) return cur
            moved = true
            return if (kotlin.math.abs(t - cur) <= step) t else cur + kotlin.math.sign(t - cur) * step
        }
        for (j in bandDb.indices) bandDb[j] = g(bandDb[j], tBand(j))
        bassDb = g(bassDb, tBass())
        hpMix = g(hpMix * 12.0, tHp() * 12.0) / 12.0 // glide the correction like a 12 dB move
        for (sl in slots) sl.cur = g(sl.cur, tSlot(sl))
        if (slots.any { it.dying && it.cur == 0.0 }) slots = slots.filter { !(it.dying && it.cur == 0.0) }
        if (paramDirty) moved = true
        if (hpMix == 0.0 && settings.headphone == null && hpProfile != null) { hpProfile = null; hpFilters = emptyList() }
        if (moved) retune()
        return moved
    }

    fun process(buf: FloatArray, frames: Int) {
        if (isIdentity) {
            if (!stateClear) {
                bands.forEach { it.clear() }; bass.clear(); hpFilters.forEach { it.clear() }; slots.forEach { s -> s.filters.forEach { it.clear() } }
                limGain = 1.0; stateClear = true
            }
            return
        }
        stateClear = false
        var f0 = 0
        while (f0 < frames) {
            val n = minOf(BLOCK, frames - f0)
            glide(n)
            processBlock(buf, f0, n)
            f0 += n
        }
    }

    private fun processBlock(buf: FloatArray, start: Int, n: Int) {
        val filters = chain()
        // Per-sample linear ramps for the plain gains (no zipper noise).
        val p0 = curPreamp; val p1 = targetPreamp()
        val s0 = curSide; val s1 = tSide()
        val l0 = curL; val l1 = tL()
        val r0 = curR; val r1 = tR()
        for (k in 0 until n) {
            val t = (k + 1).toDouble() / n
            val pre = p0 + (p1 - p0) * t
            val base = (start + k) * channels
            for (ch in 0 until channels) {
                var x = buf[base + ch] * pre
                for (flt in filters) x = flt.process(x, ch)
                buf[base + ch] = x.toFloat()
            }
            if (channels == 2) {
                var l = buf[base].toDouble()
                var r = buf[base + 1].toDouble()
                val side = s0 + (s1 - s0) * t
                if (side != 1.0) {
                    val m = (l + r) / 2
                    val s = (l - r) / 2 * side
                    l = m + s; r = m - s
                }
                l *= l0 + (l1 - l0) * t
                r *= r0 + (r1 - r0) * t
                val g = limiterGain(maxOf(kotlin.math.abs(l), kotlin.math.abs(r)))
                buf[base] = (l * g).coerceIn(-1.0, 1.0).toFloat()
                buf[base + 1] = (r * g).coerceIn(-1.0, 1.0).toFloat()
            } else {
                for (ch in 0 until channels) {
                    val x = buf[base + ch].toDouble()
                    buf[base + ch] = (x * limiterGain(kotlin.math.abs(x))).coerceIn(-1.0, 1.0).toFloat()
                }
            }
        }
        curPreamp = p1; curSide = s1; curL = l1; curR = r1
    }

    /** Peak limiter: clamps instantly to [LIMIT] and recovers over [RELEASE_S]. */
    private fun limiterGain(peak: Double): Double {
        val released = 1.0 - (1.0 - limGain) * release
        limGain = if (peak * released > LIMIT) LIMIT / peak else released
        return limGain
    }

    companion object {
        /** ~1 octave per band: neighbouring bands blend smoothly. */
        const val Q = 1.41
        const val BASS_HZ = 100.0
        const val BASS_MAX_DB = 12.0
        const val GLIDE_DB_PER_S = 80.0
        const val BLOCK = 64
        const val LIMIT = 0.98
        const val RELEASE_S = 0.15
        private const val PROBES = 96

        /** Auto headroom (dB, ≤ 0) these settings get; for the UI. */
        fun headroomDb(s: EqSettings, sampleRate: Int = 48_000): Double = EqEngine(s, sampleRate, 2).autoHeadroomDb

        /** The settings' EQ curve in dB at each of [freqs] (filters only, before headroom); for the UI graph. */
        fun curveDb(s: EqSettings, freqs: DoubleArray, sampleRate: Int = 48_000): DoubleArray {
            val e = EqEngine(s, sampleRate, 2)
            val all = e.active()
            return DoubleArray(freqs.size) { i -> all.sumOf { it.responseDb(e.fs, freqs[i].coerceAtMost(e.fs * 0.49)) } }
        }
    }
}
