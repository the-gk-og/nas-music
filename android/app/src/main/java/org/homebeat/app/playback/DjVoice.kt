package org.homebeat.app.playback

import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import org.homebeat.app.data.api.DjIntro
import org.homebeat.app.data.api.DjIntroRequest
import org.homebeat.app.data.api.DjStatus
import org.homebeat.app.data.api.HomebeatApi
import org.homebeat.app.data.api.UserSettings
import org.homebeat.app.net.RoutedCallFactory

/** Radio DJ voice (api 0.8.2): the server's settings for it, shared by Settings and the playback service. */
object DjVoiceState {
    private val _status = MutableStateFlow<DjStatus?>(null)
    val status: StateFlow<DjStatus?> = _status.asStateFlow()

    /** Switched on by the user and the server can talk. */
    val on: Boolean get() = _status.value?.let { it.enabled && it.available } == true

    suspend fun refresh(api: HomebeatApi) {
        _status.value = runCatching { api.djStatus() }.getOrNull() // null: an older server without it
    }

    suspend fun set(api: HomebeatApi, enabled: Boolean? = null, every: Int? = null) {
        val m = buildMap<String, JsonElement> {
            enabled?.let { put("dj.voice", JsonPrimitive(it)) }
            every?.let { put("dj.every", JsonPrimitive(it)) }
        }
        _status.value = _status.value?.let { it.copy(enabled = enabled ?: it.enabled, every = every ?: it.every) }
        runCatching { api.putMySettings(UserSettings(m)) }
        refresh(api)
    }
}

/** Pure rules, unit-tested in DjVoiceTest. */
object DjLogic {
    /** Music level while the DJ talks. */
    const val DUCK = 0.3f
    const val DUCK_DOWN_MS = 350L
    const val DUCK_UP_MS = 900L
    /** Ask for the next song's intro this long before the current one ends. */
    const val PREFETCH_MS = 25_000L
    /** How long a song may wait for its intro before it plays without one. */
    const val WAIT_MS = 1_500L

    /** Radio queues and mixes get a DJ; albums, playlists and search results don't. */
    fun eligible(isRadio: Boolean, source: String?): Boolean = isRadio || source == "mix" || source == "more_like"

    /** Linear ramp from [from] to [to], [t] in 0..1. */
    fun ramp(from: Float, to: Float, t: Float): Float = from + (to - from) * t.coerceIn(0f, 1f)

    fun key(s: DjVoice.Spot) = "${s.queueId}|${s.position}|${s.trackId}|${s.previousTrackId ?: ""}"
}

/**
 * Plays the DJ's spoken intro over the opening of a song, on its own small ExoPlayer, while [duck] lowers the
 * music (VolumeMixer.duck). Only the playback service drives it: on automatic moves to the next song in a
 * radio or mix, never after a skip, while casting or for podcasts/audiobooks.
 */
@UnstableApi
class DjVoice(
    private val scope: CoroutineScope,
    private val api: () -> HomebeatApi,
    private val buildPlayer: () -> ExoPlayer,
    private val duck: (Float) -> Unit,
    private val userVolume: () -> Float,
) {
    data class Spot(val queueId: String?, val position: Int, val trackId: String, val previousTrackId: String?)

    private val intros = object : LinkedHashMap<String, Deferred<DjIntro?>>() {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Deferred<DjIntro?>>?) = size > 30
    }
    private var voice: ExoPlayer? = null
    private var talk: Job? = null
    private var rampJob: Job? = null
    private var level = 1f

    /** Words being spoken now (null = quiet). */
    private val _speaking = MutableStateFlow<String?>(null)
    val speaking: StateFlow<String?> = _speaking.asStateFlow()

    fun prefetch(spot: Spot): Deferred<DjIntro?> = intros.getOrPut(DjLogic.key(spot)) {
        scope.async {
            runCatching {
                api().djIntro(DjIntroRequest(spot.trackId, spot.position, spot.previousTrackId, spot.queueId)).body()
            }.getOrNull()
        }
    }

    /** A song started on its own: speak its intro if one is ready soon enough. */
    fun announce(spot: Spot) {
        stop()
        talk = scope.launch {
            val intro = withTimeoutOrNull(DjLogic.WAIT_MS) { prefetch(spot).await() } ?: return@launch
            val p = voice ?: buildPlayer().also { voice = it; it.addListener(listener) }
            p.volume = userVolume()
            p.setMediaItem(MediaItem.fromUri(RoutedCallFactory.virtual(intro.url)))
            p.prepare()
            p.play()
            _speaking.value = intro.text
            rampTo(DjLogic.DUCK, DjLogic.DUCK_DOWN_MS)
            delay(intro.durationMs + 4_000) // never leave the music ducked if the clip stalls
            finish()
        }
    }

    private val listener = object : Player.Listener {
        override fun onPlaybackStateChanged(state: Int) { if (state == Player.STATE_ENDED) finish() }
        override fun onPlayerError(error: PlaybackException) = finish()
    }

    private fun finish() {
        talk?.takeIf { it.isActive }?.cancel()
        talk = null
        voice?.let { if (it.isPlaying || it.playbackState != Player.STATE_IDLE) it.stop() }
        if (_speaking.value != null || level < 1f) {
            _speaking.value = null
            rampTo(1f, DjLogic.DUCK_UP_MS)
        }
    }

    /** Silence the DJ now (skip, pause, another song, casting). */
    fun stop() = finish()

    /** The queue changed under us (steer, edit): intros prefetched for it may name the wrong songs. */
    fun forget() = intros.clear()

    private fun rampTo(target: Float, ms: Long) {
        rampJob?.cancel()
        val from = level
        rampJob = scope.launch {
            val steps = (ms / 30).coerceAtLeast(1)
            for (i in 1..steps) {
                level = DjLogic.ramp(from, target, i.toFloat() / steps)
                duck(level)
                delay(30)
            }
        }
    }

    fun release() {
        intros.clear()
        talk?.cancel()
        rampJob?.cancel()
        level = 1f
        duck(1f)
        voice?.release()
        voice = null
    }
}
