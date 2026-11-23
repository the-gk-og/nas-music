package org.nasmusic.app.playback

import org.nasmusic.app.net.Route

/** Which streaming-quality setting applies: home Wi-Fi (LAN route), mobile data, or remote via the public address. */
enum class NetworkKind(val label: String) {
    HOME("Home Wi-Fi"), MOBILE("Mobile data"), REMOTE("Remote Wi-Fi");

    companion object {
        /** LAN route → HOME; tunnel on a metered network → MOBILE; tunnel on Wi-Fi/ethernet elsewhere → REMOTE. */
        fun of(route: Route, unmetered: Boolean): NetworkKind = when {
            route == Route.LAN -> HOME
            !unmetered -> MOBILE
            else -> REMOTE
        }
    }
}

/** A streaming-quality choice. Opus bitrates are the server's tiers (/stream snaps to 64…256). */
enum class StreamQuality(val key: String, val kbps: Int, val label: String) {
    AUTO("auto", 0, "Auto"),
    LOSSLESS("lossless", 0, "Lossless"),
    OPUS_64("opus64", 64, "64"),
    OPUS_96("opus96", 96, "96"),
    OPUS_128("opus128", 128, "128"),
    OPUS_160("opus160", 160, "160"),
    OPUS_192("opus192", 192, "192"),
    OPUS_256("opus256", 256, "256");

    companion object {
        /** What "Auto" means away from home: small enough for a ~2 Mbit/s relay, transparent for most ears. */
        const val AUTO_AWAY_KBPS = 128

        fun from(key: String?): StreamQuality = entries.firstOrNull { it.key == key } ?: AUTO

        fun resolve(q: StreamQuality, net: NetworkKind): StreamSpec = when (q) {
            AUTO -> if (net == NetworkKind.HOME) StreamSpec.LOSSLESS else StreamSpec(false, AUTO_AWAY_KBPS)
            LOSSLESS -> StreamSpec.LOSSLESS
            else -> StreamSpec(false, q.kbps)
        }

        /** Shown under "Auto" in settings, e.g. "Lossless" or "Opus 128". */
        fun autoMeaning(net: NetworkKind): String = resolve(AUTO, net).label
    }
}

/** What to ask /stream for: the original file (lossless) or Opus at [kbps]. */
data class StreamSpec(val lossless: Boolean, val kbps: Int) {
    val query: String get() = if (lossless) "format=flac" else "format=opus&bitrate=$kbps"
    /** "Opus 128"; "Lossless" until the file's own format is known. */
    val label: String get() = if (lossless) "Lossless" else "Opus $kbps"

    companion object {
        val LOSSLESS = StreamSpec(true, 0)

        /** Parse a /stream URL back into its spec (null for anything else, e.g. a local file). */
        fun ofUrl(url: String): StreamSpec? {
            if (!url.contains("/stream/")) return null
            val q = url.substringAfter('?', "").split('&').mapNotNull { p ->
                p.split('=', limit = 2).takeIf { it.size == 2 }?.let { it[0] to it[1] }
            }.toMap()
            return when (q["format"]) {
                "flac", "original" -> LOSSLESS
                "opus", null -> StreamSpec(false, q["bitrate"]?.toIntOrNull() ?: 160)
                else -> null
            }
        }
    }
}

/** Short format labels for "now playing", matching the server's X-Stream-Format: "FLAC 16/44.1", "MP3 320". */
object FormatLabel {
    private val LOSSLESS = setOf("FLAC", "ALAC", "WAV", "PCM", "AIFF", "APE", "WAVPACK")

    private fun khz(sr: Int) = if (sr % 1000 == 0) "${sr / 1000}" else "%.1f".format(java.util.Locale.ROOT, sr / 1000.0)

    fun original(codec: String?, bits: Int?, sampleRate: Int?, kbps: Int?): String {
        var name = codec?.trim()?.uppercase().orEmpty().ifEmpty { "Original" }
        name = mapOf("OPUS" to "Opus", "VORBIS" to "Vorbis")[name] ?: name
        val lossless = name in LOSSLESS
        return when {
            lossless && bits != null && bits > 0 && sampleRate != null && sampleRate > 0 -> "$name $bits/${khz(sampleRate)}"
            lossless && sampleRate != null && sampleRate > 0 -> "$name ${khz(sampleRate)} kHz"
            !lossless && kbps != null && kbps > 0 -> "$name $kbps"
            else -> name
        }
    }
}

/** The format of the item the phone is playing, for the Now Playing screen. */
data class NowFormat(val mediaId: String, val label: String, val offline: Boolean = false)
