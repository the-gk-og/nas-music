package org.nasmusic.app.eq

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
    /** Preamp in dB, −12..+6 (Headroom = −6, Off = 0, Boost = +1). */
    val preampDb: Float = 0f,
    val mono: Boolean = false,
    /** −1 = left only … 0 centre … +1 = right only. */
    val balance: Float = 0f,
    /** AutoEq headphone correction for this output, applied before the bands while the EQ is on. */
    val headphone: HeadphoneProfile? = null,
) {
    fun withBand(i: Int, db: Float) = copy(bandsDb = bandsDb.toMutableList().also { it[i] = db.coerceIn(-12f, 12f) }, preset = "Custom")

    companion object {
        val BANDS_HZ = floatArrayOf(31.5f, 63f, 125f, 250f, 500f, 1000f, 2000f, 4000f, 8000f, 16000f)
        val BAND_LABELS = listOf("31.5", "63", "125", "250", "500", "1 k", "2 k", "4 k", "8 k", "16 k")

        val PRESETS: Map<String, List<Float>> = linkedMapOf(
            "Flat" to List(10) { 0f },
            "Bass" to listOf(6f, 5f, 4f, 2f, 0f, 0f, 0f, 0f, 0f, 0f),
            "Vocal" to listOf(-2f, -2f, -1f, 1f, 3f, 4f, 3f, 1f, 0f, -1f),
            "Treble" to listOf(0f, 0f, 0f, 0f, 0f, 1f, 2f, 4f, 5f, 6f),
            "Rock" to listOf(5f, 4f, 2f, -1f, -2f, -1f, 1f, 3f, 4f, 4f),
            "Pop" to listOf(-1f, 1f, 3f, 4f, 3f, 0f, -1f, -1f, 1f, 2f),
            "Classical" to listOf(4f, 3f, 2f, 1f, -1f, -1f, 0f, 2f, 3f, 4f),
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
