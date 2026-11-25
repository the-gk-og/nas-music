package org.homebeat.app.eq

import kotlinx.serialization.Serializable

/** One equalizer profile (docs/v2.md §6). Each audio output (speaker, wired, each Bluetooth device) has its own. */
@Serializable
data class EqSettings(
    val enabled: Boolean = false,
    /** Gains in dB for [EqSettings.BANDS_HZ], each −12..+12. */
    val bandsDb: List<Float> = List(10) { 0f },
    val preset: String = "Flat",
    /** 0..100 % → low shelf at 100 Hz, up to +12 dB. */
    val bassBoost: Int = 0,
    /** 0..100 % stereo widening (mid/side). */
    val surround: Int = 0,
    /** Preamp trim in dB, −12..+6, on top of the engine's automatic headroom (Quieter = −3, Auto = 0, Louder = +3). */
    val preampDb: Float = 0f,
    val mono: Boolean = false,
    /** −1 = left only … 0 centre … +1 = right only. */
    val balance: Float = 0f,
    /** AutoEq headphone correction for this output, applied before the bands while the EQ is on. */
    val headphone: HeadphoneProfile? = null,
    /** "graphic" (the 10 sliders + presets) or "parametric" ([parametric] bands instead of the sliders). */
    val mode: String = GRAPHIC,
    /** Parametric bands, used while [mode] is "parametric". */
    val parametric: List<EqBand> = emptyList(),
) {
    val isParametric get() = mode == PARAMETRIC

    fun withParam(i: Int, b: EqBand) = copy(parametric = parametric.toMutableList().also { it[i] = b.clamped() })

    fun withBand(i: Int, db: Float) = copy(bandsDb = bandsDb.toMutableList().also { it[i] = db.coerceIn(-12f, 12f) }, preset = "Custom")

    companion object {
        const val GRAPHIC = "graphic"
        const val PARAMETRIC = "parametric"
        const val MAX_PARAM_BANDS = 16

        val BANDS_HZ = floatArrayOf(31.5f, 63f, 125f, 250f, 500f, 1000f, 2000f, 4000f, 8000f, 16000f)
        val BAND_LABELS = listOf("31.5", "63", "125", "250", "500", "1 k", "2 k", "4 k", "8 k", "16 k")

        /**
         * Gentle curves: a few dB goes a long way, and every boost costs headroom (the engine turns the whole
         * signal down by the biggest boost so nothing clips). Same values as web/src/lib/eq.ts.
         */
        val PRESETS: Map<String, List<Float>> = linkedMapOf(
            "Flat" to List(10) { 0f },
            "Bass" to listOf(3f, 4.5f, 4f, 2f, 0.5f, 0f, 0f, 0f, 0f, 0f),
            "Vocal" to listOf(-2f, -1.5f, -1f, 0f, 1.5f, 2.5f, 3f, 2f, 0.5f, 0f),
            "Treble" to listOf(0f, 0f, 0f, 0f, 0f, 0.5f, 1.5f, 3f, 4f, 4f),
            "Rock" to listOf(2.5f, 3f, 1.5f, -0.5f, -1.5f, -1f, 0.5f, 2f, 3f, 2.5f),
            "Pop" to listOf(-1f, 0.5f, 2f, 2.5f, 1.5f, 0f, -0.5f, 0f, 1f, 1.5f),
            "Classical" to listOf(2f, 2f, 1f, 0f, 0f, 0f, 0f, 0.5f, 1.5f, 2f),
            "Loudness" to listOf(3.5f, 3f, 1f, 0f, -0.5f, -0.5f, 0f, 1f, 2.5f, 2.5f),
            "Late night" to listOf(-3f, -2f, -1f, 0f, 0.5f, 1f, 1f, 0f, -1f, -2f),
        )
    }
}

/** An AutoEq parametric profile (from the server's /eq/autoeq/profile). */
@Serializable
data class HeadphoneProfile(
    val id: String,
    val name: String,
    val source: String = "",
    val preampDb: Double = 0.0,
    val filters: List<ParametricFilter> = emptyList(),
)

/** type: PK (peaking), LSC (low shelf), HSC (high shelf); RBJ cookbook with Q as given. */
@Serializable
data class ParametricFilter(val type: String, val fc: Double, val gainDb: Double, val q: Double)

/**
 * One parametric band. type: PK (peak), LSC / HSC (low / high shelf, Q as in AutoEq), LP / HP (low / high pass).
 * Pass filters ignore gain; [slope] 12 uses [q] (0.707 = Butterworth), 24 and 48 dB/oct are Butterworth cascades.
 */
@Serializable
data class EqBand(
    val type: String = "PK",
    val fc: Double = 1000.0,
    val gainDb: Double = 0.0,
    val q: Double = 1.0,
    val on: Boolean = true,
    val slope: Int = 12,
) {
    val isPass get() = type == "LP" || type == "HP"

    fun clamped() = copy(
        type = type.uppercase().takeIf { it in TYPES } ?: "PK",
        fc = fc.coerceIn(10.0, 22_000.0),
        gainDb = gainDb.coerceIn(-MAX_GAIN, MAX_GAIN),
        q = q.coerceIn(0.1, 20.0),
        slope = SLOPES.minBy { kotlin.math.abs(it - slope) },
    )

    companion object {
        val TYPES = listOf("PK", "LSC", "HSC", "LP", "HP")
        val TYPE_LABELS = mapOf("PK" to "Peak", "LSC" to "Low shelf", "HSC" to "High shelf", "LP" to "Low pass", "HP" to "High pass")
        val SLOPES = listOf(12, 24, 48)
        const val MAX_GAIN = 15.0

        /** Bands from an AutoEq profile (its filters are PK / LSC / HSC). */
        fun fromAutoEq(p: HeadphoneProfile) = p.filters.map { EqBand(it.type.uppercase(), it.fc, it.gainDb, it.q).clamped() }

        /** Starting point when switching to parametric: the graphic sliders that aren't flat, as peaks. */
        fun fromGraphic(bandsDb: List<Float>) = EqSettings.BANDS_HZ.indices
            .filter { bandsDb.getOrElse(it) { 0f } != 0f }
            .map { EqBand("PK", EqSettings.BANDS_HZ[it].toDouble(), bandsDb[it].toDouble(), EqEngine.Q) }
    }
}
