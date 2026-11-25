package org.homebeat.app.playback

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import org.homebeat.app.data.api.Track
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sin

/**
 * Listening settings, kept on this phone only (never synced): crossfade (0 = gapless), volume
 * levelling from the server's ReplayGain values, and its pre-amp / clipping prevention. Same rules
 * as the web player (web/src/lib/listening.ts).
 */
@Serializable
data class ListeningSettings(
    /** off (gapless) | crossfade | dj (beat-matched automix; falls back to a crossfade). Null = saved before
     *  automix existed: crossfadeS > 0 means crossfade. */
    val transition: String? = null,
    /** Crossfade length (also DJ's fallback); 0 = gapless in old settings. */
    val crossfadeS: Int = 0,
    /** DJ automix: 8, 16 or 32 beats. */
    val djBeats: Int = 16,
    /** DJ automix: time-stretch the incoming track ±8 % (pitch kept) so beats line up. */
    val allowTempo: Boolean = true,
    /** DJ automix: reorder radio, mixes and playlists for flow when they start. */
    val automixOrder: Boolean = true,
    /** off | track | album | auto */
    val levelling: String = "auto",
    val preampDb: Float = 0f,
    val preventClipping: Boolean = true,
) {
    fun normalised() = copy(
        transition = transition?.takeIf { it in TRANSITIONS } ?: if (crossfadeS > 0) "crossfade" else "off",
        djBeats = Automix.djBeats(djBeats),
        crossfadeS = crossfadeS.coerceIn(0, CROSSFADE_MAX_S),
        levelling = levelling.takeIf { it in LEVELLING_MODES } ?: "auto",
        preampDb = preampDb.coerceIn(-15f, 15f),
    )

    companion object {
        const val CROSSFADE_MAX_S = 12
        val LEVELLING_MODES = listOf("off", "track", "album", "auto")
        val TRANSITIONS = listOf("off", "crossfade", "dj")
    }

    val dj: Boolean get() = transition == "dj"
    /** Crossfade used by DJ mode when two tracks can't be beat-matched. */
    val djFallbackS: Int get() = if (crossfadeS > 0) crossfadeS else 6
    val djOrdering: Boolean get() = dj && automixOrder
}

class ListeningRepository(context: Context) {
    private val prefs = context.getSharedPreferences("listening", Context.MODE_PRIVATE)
    private val json = Json { ignoreUnknownKeys = true }
    private val _settings = MutableStateFlow(
        runCatching { json.decodeFromString(ListeningSettings.serializer(), prefs.getString("settings", null) ?: "{}") }
            .getOrDefault(ListeningSettings()).normalised(),
    )
    /** Read from the playback thread. */
    val settings: StateFlow<ListeningSettings> = _settings.asStateFlow()

    fun update(transform: (ListeningSettings) -> ListeningSettings) {
        val next = transform(_settings.value).normalised()
        _settings.value = next
        prefs.edit().putString("settings", json.encodeToString(ListeningSettings.serializer(), next)).apply()
    }
}

/** The facts about a queue entry the listening rules need (from a MediaItem or a Track). */
data class TrackFacts(
    val id: String,
    val albumId: String,
    val trackNo: Int?,
    val discNo: Int?,
    val durationS: Double,
    val trackGain: Double? = null,
    val trackPeak: Double? = null,
    val albumGain: Double? = null,
    val albumPeak: Double? = null,
    val mix: org.homebeat.app.data.api.TrackMix? = null,
) {
    companion object {
        fun of(t: Track) = TrackFacts(
            t.id, t.albumId, t.trackNo, t.discNo, t.durationS,
            t.replayGain?.trackGain, t.replayGain?.trackPeak, t.replayGain?.albumGain, t.replayGain?.albumPeak, t.mix,
        )
    }
}

object ListeningRules {
    /** `b` directly follows `a` on the same album (n → n+1, or last of a disc → first of the next). */
    fun followsOnAlbum(a: TrackFacts?, b: TrackFacts?): Boolean {
        if (a == null || b == null || a.albumId.isEmpty() || a.albumId != b.albumId || a.id == b.id) return false
        val ta = a.trackNo ?: return true
        val tb = b.trackNo ?: return true
        val da = a.discNo ?: 1
        val db = b.discNo ?: 1
        return (da == db && tb == ta + 1) || (db == da + 1 && tb == 1)
    }

    /** Crossfade length in ms for an automatic a → b transition (0 = gapless). */
    fun crossfadeMs(s: ListeningSettings, a: TrackFacts?, b: TrackFacts?): Long {
        val len = when (s.transition) { "off" -> 0; "dj" -> s.djFallbackS; else -> s.crossfadeS }
        if (a == null || b == null || len <= 0 || a.id == b.id || followsOnAlbum(a, b)) return 0
        val shortest = min(a.durationS.takeIf { it > 0 } ?: Double.MAX_VALUE, b.durationS.takeIf { it > 0 } ?: Double.MAX_VALUE)
        return (min(len.toDouble(), shortest / 3) * 1000).toLong().coerceAtLeast(0)
    }

    /** DJ automix for a → b? Never between consecutive album tracks (they stay gapless). */
    fun djFor(s: ListeningSettings, a: TrackFacts?, b: TrackFacts?): Boolean =
        s.dj && a != null && b != null && a.id != b.id && !followsOnAlbum(a, b)

    fun djPlan(s: ListeningSettings, a: TrackFacts, b: TrackFacts): DjPlan? =
        Automix.plan(a.id, a.durationS, a.mix, b.id, b.durationS, b.mix, DjOptions(s.djBeats, s.allowTempo, s.djFallbackS.toDouble()))

    /** Album gain when the setting says so, or ("auto") when neighbours in play order are the same album in order. */
    fun useAlbumGain(s: ListeningSettings, prev: TrackFacts?, cur: TrackFacts?, next: TrackFacts?, shuffled: Boolean): Boolean =
        s.levelling == "album" || (s.levelling == "auto" && !shuffled && (followsOnAlbum(prev, cur) || followsOnAlbum(cur, next)))

    /** Linear gain for a track (may exceed 1; the caller limits it to what it can apply). */
    fun gain(t: TrackFacts?, s: ListeningSettings, albumMode: Boolean): Double {
        if (t == null || s.levelling == "off") return 1.0
        var db = s.preampDb.toDouble()
        var peak: Double? = null
        if (t.trackGain != null) {
            val useAlbum = albumMode && t.albumGain != null
            db += if (useAlbum) t.albumGain!! else t.trackGain
            peak = if (useAlbum) t.albumPeak ?: t.trackPeak else t.trackPeak
        }
        var g = 10.0.pow(db.coerceIn(-40.0, 24.0) / 20)
        if (s.preventClipping && peak != null && peak > 0) g = min(g, 1 / peak)
        return g
    }

    fun fadeIn(x: Double): Double = sin(x.coerceIn(0.0, 1.0) * PI / 2)
    fun fadeOut(x: Double): Double = cos(x.coerceIn(0.0, 1.0) * PI / 2)
}

/** Sleep timer: a number of minutes, or the end of the current track / album. */
data class SleepTimer(val mode: Mode, val endsAtMs: Long = 0, val albumId: String? = null) {
    enum class Mode { MINUTES, TRACK, ALBUM }

    /** Stop instead of moving from `cur` to `next`? */
    fun stopsAt(cur: TrackFacts?, next: TrackFacts?): Boolean = when (mode) {
        Mode.TRACK -> true
        Mode.ALBUM -> next == null || next.albumId != (albumId ?: cur?.albumId)
        Mode.MINUTES -> false
    }

    fun label(nowMs: Long): String = when (mode) {
        Mode.TRACK -> "End of song"
        Mode.ALBUM -> "End of album"
        Mode.MINUTES -> "${((endsAtMs - nowMs).coerceAtLeast(0) + 59_999) / 60_000} min"
    }

    companion object {
        const val FADE_MS = 10_000L
        const val TRACK_FADE_MS = 6_000L

        /** 1 → 0 over the last `fadeMs` before the stop (eased, so the end is barely audible). */
        fun fadeLevel(remainingMs: Long, fadeMs: Long = FADE_MS): Float {
            if (remainingMs >= fadeMs) return 1f
            if (remainingMs <= 0) return 0f
            val x = remainingMs.toFloat() / fadeMs
            return x * x
        }
    }
}

/** Sleep timer state shared by the UI, the notification button and the playback service. */
class SleepTimerState {
    private val _timer = MutableStateFlow<SleepTimer?>(null)
    val timer: StateFlow<SleepTimer?> = _timer.asStateFlow()

    fun set(t: SleepTimer?) { _timer.value = t }

    fun minutes(m: Int) = set(SleepTimer(SleepTimer.Mode.MINUTES, System.currentTimeMillis() + m * 60_000L))

    /** The notification button: off → 30 min → off. */
    fun toggleFromNotification() = if (_timer.value == null) minutes(30) else set(null)
}
