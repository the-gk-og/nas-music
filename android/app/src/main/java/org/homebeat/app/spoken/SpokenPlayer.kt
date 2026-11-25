package org.homebeat.app.spoken

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.homebeat.app.data.api.BookmarkBody
import org.homebeat.app.data.api.PodcastPatch
import org.homebeat.app.data.api.SpokenApi
import org.homebeat.app.data.api.Station
import org.homebeat.app.playback.NowPlaying
import org.homebeat.app.playback.PlayerConnection

data class SpokenState(
    val item: SpokenItem? = null,
    val playing: Boolean = false,
    val buffering: Boolean = false,
    val positionS: Double = 0.0,
    val speed: Float = 1f,
    val skipSilence: Boolean = false,
    val nowPlaying: String? = null,
    val sleepEndsAt: Long? = null,
    val sleepEndOfChapter: Boolean = false,
    val error: String? = null,
    /** Playing on another device (Connect); controls go there. */
    val remoteLabel: String? = null,
) {
    val live get() = item?.kind == "station"
    val chapterIndex get() = SpokenLogic.chapterAt(item?.chapters.orEmpty().map { it.start }, positionS)
}

private data class Extra(
    val nowPlaying: Pair<String, String?>? = null,
    val sleepEndsAt: Long? = null,
    val sleepEndOfChapter: Boolean = false,
    val skipSilence: Map<String, Boolean> = emptyMap(),
    val error: String? = null,
)

/**
 * Podcasts, audiobooks and internet radio on top of the MAIN player ([PlayerConnection] → PlaybackService), so
 * they get the same notification, lock screen, watch, Bluetooth, Connect (remotes, transfer), casting and resume
 * as music. This class only adds what spoken items need: book-wide positions across parts, chapters, per-show
 * speed and skip silence, station now-playing, a sleep timer with "end of chapter", bookmarks and mark played.
 * Resume points are saved by the PlaybackService (see [SpokenProgressStore]).
 */
class SpokenPlayer(
    private val player: PlayerConnection,
    private val api: () -> SpokenApi,
    val downloads: SpokenDownloads,
    val progress: SpokenProgressStore,
) {
    private val main = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val extra = MutableStateFlow(Extra())
    private var npJob: Job? = null
    private var npFor: String? = null
    private var sleepJob: Job? = null

    val state: StateFlow<SpokenState> = combine(player.state, extra) { np, x -> derive(np, x) }
        .stateIn(main, SharingStarted.Eagerly, SpokenState())

    init {
        main.launch { state.collect { s -> watchStation(s.item) } }
    }

    private fun derive(np: NowPlaying, x: Extra): SpokenState {
        val mi = np.item
        val item = SpokenMedia.item(mi) ?: return SpokenState()
        val pos = if (item.live) 0.0 else SpokenMedia.bookPositionS(item, SpokenMedia.part(mi), np.positionMs)
        return SpokenState(
            item = item, playing = np.isPlaying, positionS = pos, speed = np.speed,
            skipSilence = x.skipSilence[item.id] ?: item.skipSilence,
            nowPlaying = x.nowPlaying?.takeIf { it.first == item.id }?.second,
            sleepEndsAt = x.sleepEndsAt, sleepEndOfChapter = x.sleepEndOfChapter && !item.live,
            error = x.error, remoteLabel = np.remote?.label,
        )
    }

    private fun watchStation(item: SpokenItem?) {
        val id = item?.takeIf { it.live }?.id
        if (id == npFor) return
        npFor = id
        npJob?.cancel()
        if (id == null) return
        npJob = main.launch {
            while (isActive) {
                runCatching { api().nowPlaying(id).title }.onSuccess { t -> extra.value = extra.value.copy(nowPlaying = id to t) }
                delay(20_000)
            }
        }
    }

    private fun fail(t: Throwable) { extra.value = extra.value.copy(error = t.message ?: "Could not play this") }

    /** Start [item] at book-wide second [posS] (here, or on the active device as a remote). */
    fun play(item: SpokenItem, posS: Double) {
        extra.value = extra.value.copy(error = null)
        player.playSpoken(item, posS, downloads::file)
    }

    /** A saved position not yet sent beats the server's (it is newer). */
    private fun resumeFrom(kind: String, id: String, server: Double): Double =
        progress.pending(kind, id)?.let { w -> if (w.played == true) 0.0 else w.positionS } ?: server

    fun playEpisode(id: String) = main.launch {
        val (item, pos) = SpokenMedia.load(api(), "episode", id) ?: return@launch fail(IllegalStateException("Could not load this episode"))
        play(item, resumeFrom("episode", id, pos))
    }

    fun playBook(id: String, at: Double? = null) = main.launch {
        val (item, pos) = SpokenMedia.load(api(), "book", id) ?: return@launch fail(IllegalStateException("Could not load this book"))
        play(item, at ?: resumeFrom("book", id, pos))
    }

    fun playStation(s: Station) = play(SpokenMedia.fromStation(s), 0.0)

    fun toggle() = player.togglePlay()

    fun seekTo(pos: Double) {
        val it = state.value.item ?: return
        if (it.live) return
        player.seekSpoken(it, pos.coerceIn(0.0, (it.durationS ?: Double.MAX_VALUE) - 0.5))
    }

    fun skip(d: Double) = seekTo(state.value.positionS + d)

    fun chapter(next: Boolean) {
        val starts = state.value.item?.chapters.orEmpty().map { it.start }
        if (starts.isEmpty()) return
        val pos = state.value.positionS
        val t = if (next) starts.firstOrNull { it > pos + 0.5 } else SpokenLogic.previousChapter(starts, pos)
        t?.let { seekTo(it) }
    }

    fun setSpeed(speed: Float) = main.launch {
        player.setSpeedPitch(speed, 1f)
        val it = state.value.item ?: return@launch
        if (it.kind == "episode" && it.showId != null) runCatching { api().patchPodcast(it.showId, PodcastPatch(speed = speed.toDouble())) }
        if (it.kind == "book") progress.position(it.copy(speed = speed), state.value.positionS, flush = true)
    }

    fun setSkipSilence(on: Boolean) = main.launch {
        val it = state.value.item ?: return@launch
        player.setSkipSilence(on)
        extra.value = extra.value.copy(skipSilence = extra.value.skipSilence + (it.id to on))
        if (it.kind == "episode" && it.showId != null) runCatching { api().patchPodcast(it.showId, PodcastPatch(skipSilence = on)) }
    }

    /** minutes > 0: stop after that long; 0: off; -1: at the end of this chapter. */
    fun setSleep(minutes: Int) {
        extra.value = when {
            minutes > 0 -> extra.value.copy(sleepEndsAt = System.currentTimeMillis() + minutes * 60_000L, sleepEndOfChapter = false)
            minutes < 0 -> extra.value.copy(sleepEndsAt = null, sleepEndOfChapter = true)
            else -> extra.value.copy(sleepEndsAt = null, sleepEndOfChapter = false)
        }
        sleepJob?.cancel()
        if (minutes == 0) return
        sleepJob = main.launch {
            while (isActive) {
                player.refresh()
                val s = state.value
                val x = extra.value
                if (s.item == null || (x.sleepEndsAt == null && !x.sleepEndOfChapter)) break
                val stop = x.sleepEndsAt?.let { System.currentTimeMillis() >= it } == true || (x.sleepEndOfChapter && run {
                    val ch = s.item.chapters
                    val i = SpokenLogic.chapterAt(ch.map { it.start }, s.positionS)
                    val end = ch.getOrNull(i + 1)?.start ?: s.item.durationS ?: Double.MAX_VALUE
                    s.positionS >= end - 0.5
                })
                if (stop && s.playing) {
                    extra.value = extra.value.copy(sleepEndsAt = null, sleepEndOfChapter = false)
                    player.pause()
                    break
                }
                delay(1000)
            }
        }
    }

    /** Stop: the position is kept (Continue listening / the episode list resume it). */
    fun close() = player.stopAndClear()

    suspend fun addBookmark(note: String?): Boolean {
        val it = state.value.item ?: return false
        if (it.kind != "book") return false
        return runCatching { api().addBookmark(it.id, BookmarkBody(state.value.positionS, note?.ifBlank { null })) }.isSuccess
    }

    /** Mark an episode/book (un)played from a list. */
    fun markPlayed(kind: String, id: String, played: Boolean) = progress.markPlayed(kind, id, played)

    suspend fun flush() = progress.flush()
}
