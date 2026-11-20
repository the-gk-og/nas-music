package org.nasmusic.app.spoken

import android.content.ComponentName
import android.content.Context
import android.net.Uri
import android.os.Bundle
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionToken
import com.google.common.util.concurrent.MoreExecutors
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.builtins.ListSerializer
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.Audiobook
import org.nasmusic.app.data.api.BookmarkBody
import org.nasmusic.app.data.api.Episode
import org.nasmusic.app.data.api.PodcastPatch
import org.nasmusic.app.data.api.ProgressBatch
import org.nasmusic.app.data.api.ProgressWrite
import org.nasmusic.app.data.api.SpokenApi
import org.nasmusic.app.data.api.SpokenChapter
import org.nasmusic.app.data.api.Station
import org.nasmusic.app.net.RoutedCallFactory

data class SpokenItem(
    val kind: String, // episode | book | station
    val id: String,
    val title: String,
    val subtitle: String,
    val artUrl: String,
    val durationS: Double?,
    val chapters: List<SpokenChapter> = emptyList(),
    val partStarts: List<Double> = listOf(0.0),
    val partDurations: List<Double> = emptyList(),
    val showId: String? = null,
)

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
) {
    val live get() = item?.kind == "station"
    val chapterIndex get() = SpokenLogic.chapterAt(item?.chapters.orEmpty().map { it.start }, positionS)
}

/**
 * UI-side handle on [SpokenService] (MediaController, main thread only). Keeps book-wide positions,
 * saves resume points to /spoken/progress every 15 s and on pause/seek (an on-disk outbox covers
 * offline spells), polls now-playing for stations, and runs its own sleep timer. Never records events.
 */
class SpokenPlayer(
    private val context: Context,
    private val api: () -> SpokenApi,
    val downloads: SpokenDownloads,
) {
    private val main = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val _state = MutableStateFlow(SpokenState())
    val state: StateFlow<SpokenState> = _state.asStateFlow()
    private var controller: MediaController? = null
    private var ticker: Job? = null
    private var npJob: Job? = null
    private var lastSave = 0L
    private val prefs = context.getSharedPreferences("spoken", Context.MODE_PRIVATE)
    private val outboxSer = ListSerializer(ProgressWrite.serializer())
    private val outbox = ProgressOutbox(
        runCatching { ApiFactory.json.decodeFromString(outboxSer, prefs.getString("outbox", "[]")!!) }.getOrDefault(emptyList()),
    )

    private suspend fun ctrl(): MediaController = controller ?: withContext(Dispatchers.Main) {
        val token = SessionToken(context, ComponentName(context, SpokenService::class.java))
        val f = MediaController.Builder(context, token).buildAsync()
        val c = kotlinx.coroutines.suspendCancellableCoroutine { cont ->
            f.addListener({ cont.resumeWith(runCatching { f.get() }) }, MoreExecutors.directExecutor())
        }
        c.addListener(object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                update { it.copy(playing = isPlaying) }
                if (isPlaying) startTicker() else save()
            }
            override fun onPlaybackStateChanged(s: Int) {
                update { it.copy(buffering = s == Player.STATE_BUFFERING) }
                if (s == Player.STATE_ENDED) onEnded()
            }
            override fun onPlayerError(error: androidx.media3.common.PlaybackException) {
                update { it.copy(error = if (it.live) "The station is not responding" else "Could not play this (${error.errorCodeName})") }
            }
        })
        controller = c
        c
    }

    private fun update(f: (SpokenState) -> SpokenState) { _state.value = f(_state.value) }

    private fun bookPos(c: MediaController): Double {
        val starts = _state.value.item?.partStarts.orEmpty()
        return (starts.getOrNull(c.currentMediaItemIndex) ?: 0.0) + c.currentPosition / 1000.0
    }

    private fun startTicker() {
        ticker?.cancel()
        ticker = main.launch {
            while (isActive) {
                val c = controller ?: break
                val s = _state.value
                if (!s.live) {
                    val pos = bookPos(c)
                    update { it.copy(positionS = pos) }
                    if (s.sleepEndOfChapter) {
                        val ch = s.item?.chapters.orEmpty()
                        val i = SpokenLogic.chapterAt(ch.map { it.start }, pos)
                        val end = ch.getOrNull(i + 1)?.start ?: s.item?.durationS ?: Double.MAX_VALUE
                        if (pos >= end - 0.5) { update { it.copy(sleepEndOfChapter = false) }; c.pause() }
                    }
                    if (System.currentTimeMillis() - lastSave > 15_000) save()
                }
                s.sleepEndsAt?.let { if (System.currentTimeMillis() >= it) { update { st -> st.copy(sleepEndsAt = null) }; c.pause() } }
                if (!c.isPlaying) break
                delay(1000)
            }
        }
    }

    private fun onEnded() {
        val it = _state.value.item ?: return
        if (it.kind == "station") return
        queue(ProgressWrite(it.kind, it.id, positionS = it.durationS ?: _state.value.positionS, durationS = it.durationS, played = true))
        flushSoon()
    }

    private fun meta(title: String, subtitle: String, art: String, extras: Bundle? = null) = MediaMetadata.Builder()
        .setTitle(title).setArtist(subtitle).setArtworkUri(Uri.parse(art))
        .setIsPlayable(true).setIsBrowsable(false).setMediaType(MediaMetadata.MEDIA_TYPE_PODCAST_EPISODE)
        .apply { extras?.let { setExtras(it) } }.build()

    private fun start(item: SpokenItem, items: List<MediaItem>, index: Int, offsetMs: Long, speed: Float, skipSilence: Boolean) = main.launch {
        save()
        npJob?.cancel()
        val c = runCatching { ctrl() }.getOrElse { update { s -> s.copy(error = it.message) }; return@launch }
        update { SpokenState(item = item, speed = speed, skipSilence = skipSilence, positionS = item.partStarts.getOrElse(index) { 0.0 } + offsetMs / 1000.0,
            sleepEndsAt = it.sleepEndsAt, sleepEndOfChapter = it.sleepEndOfChapter && item.kind != "station") }
        c.setMediaItems(items, index, offsetMs)
        c.setPlaybackSpeed(speed)
        c.sendCustomCommand(SessionCommand(SpokenService.CMD_SKIP_SILENCE, Bundle.EMPTY), Bundle().apply { putBoolean(SpokenService.ARG_ON, skipSilence) })
        c.prepare()
        c.play()
        if (item.kind == "station") npJob = main.launch {
            while (isActive && _state.value.item?.id == item.id) {
                runCatching { api().nowPlaying(item.id).title }.onSuccess { t -> update { it.copy(nowPlaying = t) } }
                delay(20_000)
            }
        }
    }

    fun playEpisode(id: String) = main.launch {
        val ep: Episode = runCatching { api().episode(id) }.getOrElse { update { s -> s.copy(error = it.message) }; return@launch }
        val local: File? = downloads.file(ep.id)
        val uri = local?.let { Uri.fromFile(it) } ?: Uri.parse(RoutedCallFactory.virtual("stream/episode/${Uri.encode(ep.id)}"))
        val art = RoutedCallFactory.virtual("art/podcast/${Uri.encode(ep.podcastId)}?size=512")
        val item = SpokenItem("episode", ep.id, ep.title, ep.podcastTitle.orEmpty(), art, ep.durationS, ep.chapters, showId = ep.podcastId)
        val resume = ep.progress?.takeIf { !it.played }?.positionS ?: 0.0
        val mi = MediaItem.Builder().setMediaId("ep:${ep.id}").setUri(uri).setMediaMetadata(meta(ep.title, ep.podcastTitle.orEmpty(), art)).build()
        start(item, listOf(mi), 0, (resume * 1000).toLong(), ep.speed.toFloat(), ep.skipSilence)
    }

    fun playBook(id: String, at: Double? = null) = main.launch {
        val b: Audiobook = runCatching { api().book(id) }.getOrElse { update { s -> s.copy(error = it.message) }; return@launch }
        val art = RoutedCallFactory.virtual("art/book/${Uri.encode(b.id)}?size=512")
        val sub = listOfNotNull(b.author, b.narrator?.let { "read by $it" }).joinToString(" · ")
        val item = SpokenItem(
            "book", b.id, b.title, sub, art, b.durationS,
            chapters = b.chapters.map { SpokenChapter(it.startS, it.title) },
            partStarts = b.parts.map { it.startS }, partDurations = b.parts.map { it.durationS },
        )
        val items = b.parts.map { p ->
            MediaItem.Builder().setMediaId("book:${b.id}:${p.index}")
                .setUri(RoutedCallFactory.virtual("stream/book/${Uri.encode(b.id)}/${p.index}"))
                .setMediaMetadata(meta(b.title, sub, art)).build()
        }
        val pos = at ?: b.progress?.takeIf { !it.played }?.positionS ?: 0.0
        val (idx, off) = SpokenLogic.partAt(item.partStarts, item.partDurations, pos)
        start(item, items, idx, (off * 1000).toLong(), (b.progress?.speed ?: 1.0).toFloat(), false)
    }

    fun playStation(s: Station) {
        val art = if (s.hasImage) RoutedCallFactory.virtual("art/station/${Uri.encode(s.id)}?size=256") else ""
        val sub = listOfNotNull(s.country, *s.tags.take(2).toTypedArray()).joinToString(" · ")
        val item = SpokenItem("station", s.id, s.name, sub.ifBlank { "Internet radio" }, art, null)
        val mi = MediaItem.Builder().setMediaId("st:${s.id}").setUri(RoutedCallFactory.virtual("stream/station/${Uri.encode(s.id)}"))
            .setMediaMetadata(meta(s.name, item.subtitle, art)).build()
        start(item, listOf(mi), 0, 0, 1f, false)
    }

    fun toggle() = main.launch {
        val c = controller ?: return@launch
        if (c.isPlaying) c.pause()
        else {
            if (_state.value.live) { c.seekToDefaultPosition(); c.prepare() } // live: resume at "now"
            c.play()
        }
    }

    fun seekTo(pos: Double) = main.launch {
        val c = controller ?: return@launch
        val it = _state.value.item ?: return@launch
        if (it.kind == "station") return@launch
        val p = pos.coerceIn(0.0, (it.durationS ?: Double.MAX_VALUE) - 0.5)
        val (idx, off) = SpokenLogic.partAt(it.partStarts, it.partDurations, p)
        c.seekTo(idx, (off * 1000).toLong())
        update { s -> s.copy(positionS = p) }
        save()
    }

    fun skip(d: Double) = seekTo(_state.value.positionS + d)

    fun chapter(next: Boolean) {
        val starts = _state.value.item?.chapters.orEmpty().map { it.start }
        if (starts.isEmpty()) return
        val pos = _state.value.positionS
        val t = if (next) starts.firstOrNull { it > pos + 0.5 } else SpokenLogic.previousChapter(starts, pos)
        t?.let { seekTo(it) }
    }

    fun setSpeed(speed: Float) = main.launch {
        controller?.setPlaybackSpeed(speed)
        update { it.copy(speed = speed) }
        val it = _state.value.item ?: return@launch
        if (it.kind == "episode" && it.showId != null) runCatching { api().patchPodcast(it.showId, PodcastPatch(speed = speed.toDouble())) }
        if (it.kind == "book") save()
    }

    fun setSkipSilence(on: Boolean) = main.launch {
        controller?.sendCustomCommand(SessionCommand(SpokenService.CMD_SKIP_SILENCE, Bundle.EMPTY), Bundle().apply { putBoolean(SpokenService.ARG_ON, on) })
        update { it.copy(skipSilence = on) }
        val it = _state.value.item ?: return@launch
        if (it.kind == "episode" && it.showId != null) runCatching { api().patchPodcast(it.showId, PodcastPatch(skipSilence = on)) }
    }

    /** minutes > 0: stop after that long; 0: off; -1: at the end of this chapter. */
    fun setSleep(minutes: Int) {
        update {
            when {
                minutes > 0 -> it.copy(sleepEndsAt = System.currentTimeMillis() + minutes * 60_000L, sleepEndOfChapter = false)
                minutes < 0 -> it.copy(sleepEndsAt = null, sleepEndOfChapter = true)
                else -> it.copy(sleepEndsAt = null, sleepEndOfChapter = false)
            }
        }
        if (_state.value.playing) startTicker()
    }

    fun close() = main.launch {
        save()
        npJob?.cancel()
        controller?.run { stop(); clearMediaItems() }
        _state.value = SpokenState()
    }

    suspend fun addBookmark(note: String?): Boolean {
        val it = _state.value.item ?: return false
        if (it.kind != "book") return false
        return runCatching { api().addBookmark(it.id, BookmarkBody(_state.value.positionS, note?.ifBlank { null })) }.isSuccess
    }

    // ---------- progress ----------

    private fun queue(w: ProgressWrite) {
        outbox.add(w)
        prefs.edit().putString("outbox", ApiFactory.json.encodeToString(outboxSer, outbox.snapshot())).apply()
    }

    fun save() {
        val s = _state.value
        val it = s.item ?: return
        if (it.kind == "station") return
        controller?.let { c -> if (c.mediaItemCount > 0) update { st -> st.copy(positionS = bookPos(c)) } }
        lastSave = System.currentTimeMillis()
        queue(ProgressWrite(it.kind, it.id, positionS = Math.round(_state.value.positionS * 10) / 10.0, durationS = it.durationS,
            speed = if (it.kind == "book") s.speed.toDouble() else null))
        flushSoon()
    }

    /** Mark an episode/book (un)played from a list. */
    fun markPlayed(kind: String, id: String, played: Boolean) {
        queue(ProgressWrite(kind, id, played = played))
        flushSoon()
    }

    private var flushing: Job? = null
    private fun flushSoon() {
        if (flushing?.isActive == true) return
        flushing = main.launch { flush() }
    }

    suspend fun flush() {
        val batch = outbox.take()
        if (batch.isEmpty()) return
        val ok = withContext(Dispatchers.IO) { runCatching { api().putProgress(ProgressBatch(batch)) }.isSuccess }
        if (!ok) outbox.restore(batch)
        prefs.edit().putString("outbox", ApiFactory.json.encodeToString(outboxSer, outbox.snapshot())).apply()
    }
}
