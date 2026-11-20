package org.nasmusic.app.playback

import android.content.ComponentName
import android.content.Context
import android.os.Handler
import android.os.Looper
import androidx.core.content.ContextCompat
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackParameters
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import org.nasmusic.app.connect.ConnectClient
import org.nasmusic.app.data.api.NasMusicApi
import org.nasmusic.app.data.api.Queue
import org.nasmusic.app.data.api.Track

/** Another device is playing; this phone is a remote control. */
data class RemoteInfo(
    val deviceId: String,
    val deviceName: String,
    val castTo: String? = null,
    /** A speaker the server plays on: offer "Stop casting" (or Cancel while [connecting]). */
    val isSpeaker: Boolean = false,
    val connecting: Boolean = false,
) {
    /** "Playing on Pixel", or "Casting from Pixel to Bedroom display" when that phone casts with Google Cast. */
    val label: String get() = when {
        connecting -> "Connecting to $deviceName…"
        castTo != null -> "Casting from $deviceName to $castTo"
        else -> "Playing on $deviceName"
    }
}

data class NowPlaying(
    val item: MediaItem? = null,
    val isPlaying: Boolean = false,
    val positionMs: Long = 0,
    val durationMs: Long = 0,
    val hasNext: Boolean = false,
    val hasPrevious: Boolean = false,
    val queue: List<MediaItem> = emptyList(),
    val index: Int = 0,
    val shuffle: Boolean = false,
    /** Player.REPEAT_MODE_* */
    val repeat: Int = Player.REPEAT_MODE_OFF,
    val volume: Float = 1f,
    val speed: Float = 1f,
    val pitch: Float = 1f,
    val remote: RemoteInfo? = null,
)

/**
 * UI-side handle on playback. Locally it drives the [PlaybackService] through a MediaController;
 * when another of the user's devices is active (Connect) every control becomes a remote command.
 */
class PlayerConnection(
    private val context: Context,
    private val connect: ConnectClient,
    private val queues: QueueSync,
    private val api: () -> NasMusicApi,
    private val scope: CoroutineScope,
    private val offline: OfflineSource = OfflineSource.NONE,
) {
    private var controller: MediaController? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    private val _local = MutableStateFlow(NowPlaying())
    private val _remote = MutableStateFlow<NowPlaying?>(null)
    private val _state = MutableStateFlow(NowPlaying())
    val state: StateFlow<NowPlaying> = _state.asStateFlow()
    private val pending = mutableListOf<(MediaController) -> Unit>()
    private var localQueueCache: List<MediaItem> = emptyList()

    // Touched from the main thread and from flow collectors on Dispatchers.Default.
    private val trackCache = java.util.concurrent.ConcurrentHashMap<String, Track>()
    @Volatile private var remoteQueue: Pair<String, List<MediaItem>>? = null

    init {
        scope.launch { combine(_local, _remote) { l, r -> r ?: l }.collect { _state.value = it } }
        scope.launch { connect.state.collect { refreshRemote() } }
        scope.launch { connect.devices.collect { refreshRemote() } }
    }

    val isRemote: Boolean get() = connect.isRemote

    /** Set by the full flavour's Google Cast integration; while active, controls go to the speaker. */
    @Volatile var external: ExternalTarget? = null
    private val ext: ExternalTarget? get() = external?.takeIf { it.active }

    fun connect() {
        if (controller != null) return
        val token = SessionToken(context, ComponentName(context, PlaybackService::class.java))
        val future = MediaController.Builder(context, token).buildAsync()
        future.addListener({
            val c = runCatching { future.get() }.getOrNull() ?: return@addListener
            controller = c
            c.addListener(object : Player.Listener {
                override fun onEvents(player: Player, events: Player.Events) {
                    if (events.contains(Player.EVENT_TIMELINE_CHANGED)) localQueueCache = items(player)
                    refresh()
                }
            })
            localQueueCache = items(c)
            refresh()
            pending.forEach { it(c) }
            pending.clear()
        }, ContextCompat.getMainExecutor(context))
    }

    fun release() {
        controller?.release()
        controller = null
    }

    private fun items(p: Player) = (0 until p.mediaItemCount).map { p.getMediaItemAt(it) }

    /** Called by the UI on a timer to update the position bar. */
    fun refresh() {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post { refresh() }
            return
        }
        val c = controller
        if (c != null) {
            val local = snapshot(c)
            _local.value = ext?.overlay(local) ?: local
        }
        if (_remote.value != null) refreshRemote()
    }

    private fun refreshRemote() {
        if (!connect.isRemote) { _remote.value = null; return }
        val s = connect.state.value ?: return
        val dev = connect.activeDevice() ?: return
        val now = System.currentTimeMillis()
        val track = s.trackId?.let { id -> trackCache[id] ?: run { fetchTrack(id); null } }
        val queue = remoteQueue?.takeIf { it.first == s.queueId }?.second ?: run { s.queueId?.let(::fetchQueue); emptyList() }
        _remote.value = NowPlaying(
            item = track?.let { MediaItems.fromTrack(it, PlaySource.LIBRARY) },
            isPlaying = s.playing,
            positionMs = s.positionAt(now),
            durationMs = ((track?.durationS ?: 0.0) * 1000).toLong(),
            hasNext = s.index + 1 < queue.size || queue.isEmpty(),
            hasPrevious = true,
            queue = queue,
            index = s.index,
            shuffle = s.shuffle,
            repeat = when (s.repeat) { "one" -> Player.REPEAT_MODE_ONE; "all" -> Player.REPEAT_MODE_ALL; else -> Player.REPEAT_MODE_OFF },
            volume = s.volume,
            speed = s.speed,
            remote = RemoteInfo(dev.id, dev.name, s.castTo, dev.isSpeaker, dev.isSpeaker && s.connecting),
        )
    }

    private fun fetchTrack(id: String) = scope.launch {
        runCatching { api().track(id) }.onSuccess { trackCache[id] = it; refreshRemote() }
    }

    private fun fetchQueue(id: String) = scope.launch {
        queues.load(id)?.let { q ->
            q.tracks.forEach { trackCache[it.id] = it }
            remoteQueue = id to q.tracks.map { MediaItems.fromTrack(it, PlaySource.LIBRARY) }
            refreshRemote()
        }
    }

    /**
     * Media3's MediaController must only be used on the application (main) thread; calling it from a
     * coroutine on Dispatchers.Default crashes with "MediaController method is called from a wrong thread".
     * Every controller call goes through here, so hop to the main thread first.
     */
    private fun withController(block: (MediaController) -> Unit) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post { withController(block) }
            return
        }
        controller?.let(block) ?: run { pending += block; connect() }
    }

    private fun snapshot(c: MediaController): NowPlaying =
        NowPlaying(
            item = c.currentMediaItem,
            isPlaying = c.isPlaying,
            positionMs = c.currentPosition,
            durationMs = c.duration.coerceAtLeast(0),
            hasNext = c.hasNextMediaItem(),
            hasPrevious = c.hasPreviousMediaItem(),
            queue = localQueueCache,
            index = c.currentMediaItemIndex,
            shuffle = c.shuffleModeEnabled,
            repeat = c.repeatMode,
            volume = c.volume,
            speed = c.playbackParameters.speed,
            pitch = c.playbackParameters.pitch,
        )

    /** This phone's own player state (never the Cast speaker's). Main thread only; null if not connected. */
    fun localSnapshot(): NowPlaying? =
        controller?.takeIf { Looper.myLooper() == Looper.getMainLooper() }?.let(::snapshot)

    private fun remoteOr(
        action: String,
        args: Map<String, kotlinx.serialization.json.JsonElement> = emptyMap(),
        cast: ((ExternalTarget) -> Unit)? = null,
        local: (MediaController) -> Unit,
    ) {
        val e = ext
        when {
            e != null && cast != null -> { cast(e); mainHandler.postDelayed({ refresh() }, 300) }
            connect.isRemote -> connect.command(action, args)
            else -> withController(local)
        }
    }

    /** Musicolet behaviour: playing from a list replaces the queue with that list (a new named queue). */
    fun play(
        tracks: List<Track>,
        startIndex: Int,
        source: PlaySource,
        queueName: String,
        mixId: String? = null,
        reasons: List<String?>? = null,
        searchQuery: String? = null,
    ) {
        if (tracks.isEmpty()) return
        // Offline: play just the downloaded songs of the list, without an error per missing one.
        if (!connect.isRemote && ext == null && offline.isOffline()) {
            val (kept, at) = OfflinePlayback.playable(tracks, startIndex, offline.downloadedIds())
                ?: run { offline.toast("You're offline and none of these songs are downloaded"); return }
            if (kept.size < tracks.size) offline.toast("Offline: playing the ${kept.size} downloaded songs")
            return playResolved(kept, at, source, queueName, mixId, reasons?.let { r -> kept.map { k -> r.getOrNull(tracks.indexOf(k)) } }, searchQuery)
        }
        playResolved(tracks, startIndex, source, queueName, mixId, reasons, searchQuery)
    }

    private fun playResolved(
        tracks: List<Track>,
        startIndex: Int,
        source: PlaySource,
        queueName: String,
        mixId: String?,
        reasons: List<String?>?,
        searchQuery: String?,
    ) {
        val start = startIndex.coerceIn(0, tracks.size - 1)
        if (connect.isRemote) {
            connect.command(
                "playTracks",
                mapOf(
                    "trackIds" to JsonArray(tracks.map { JsonPrimitive(it.id) }),
                    "index" to JsonPrimitive(start),
                    "queueName" to JsonPrimitive(queueName),
                ),
            )
            return
        }
        withController { c ->
            val items = tracks.mapIndexed { i, t -> MediaItems.fromTrack(t, source, mixId, reasons?.getOrNull(i), searchQuery) }
            c.setMediaItems(items, start, 0)
            c.prepare()
            c.play()
        }
        queues.created(queueName, tracks, start)
    }

    fun playQueue(q: Queue, index: Int? = null) {
        if (connect.isRemote) {
            connect.command("playQueue", mapOf("queueId" to JsonPrimitive(q.id), "index" to JsonPrimitive(index ?: q.currentIndex)))
            return
        }
        scope.launch {
            val full = if (q.tracks.isEmpty()) queues.load(q.id) ?: return@launch else q
            withController { c ->
                val items = full.tracks.map { MediaItems.fromTrack(it, PlaySource.LIBRARY) }
                if (items.isEmpty()) return@withController
                val i = (index ?: full.currentIndex).coerceIn(0, items.size - 1)
                c.setMediaItems(items, i, if (index == null) full.positionMs else 0)
                c.prepare(); c.play()
            }
            queues.setCurrent(q.id)
        }
    }

    /** Start a radio (docs/v3-ux.md §5) and play it here, or on the active device when remote. */
    suspend fun startRadio(seed: org.nasmusic.app.data.api.RadioSeed): Boolean {
        val q = (if (offline.isOffline() && !connect.isRemote) null else runCatching { api().startRadio(org.nasmusic.app.data.api.RadioRequest(seed)) }.getOrNull())
            ?: return startOfflineRadio(seed)
        queues.markRadio(q.id)
        queues.refresh()
        playQueue(q, 0)
        return true
    }

    /** No server (offline, or it failed while offline): a radio made from downloaded songs. */
    private fun startOfflineRadio(seed: org.nasmusic.app.data.api.RadioSeed): Boolean {
        if (!offline.isOffline() || connect.isRemote) return false
        val tracks = OfflinePlayback.radio(seed, offline.downloadedTracks())
        if (tracks.isEmpty()) { offline.toast("You're offline and nothing is downloaded yet"); return false }
        offline.toast("Offline radio from your downloads")
        playResolved(tracks, 0, PlaySource.MIX, "Offline radio", null, null, null)
        return true
    }

    fun playNext(tracks: List<Track>) = enqueue(tracks, next = true)
    fun addToQueue(tracks: List<Track>) = enqueue(tracks, next = false)

    private fun enqueue(tracks: List<Track>, next: Boolean) {
        queues.added(tracks.map { it.id }, next)
        if (connect.isRemote) return // the active device picks it up from the queue change
        withController { c ->
            val items = tracks.map { MediaItems.fromTrack(it, PlaySource.LIBRARY) }
            if (c.mediaItemCount == 0) { c.setMediaItems(items); c.prepare(); return@withController }
            if (next) c.addMediaItems(c.currentMediaItemIndex + 1, items) else c.addMediaItems(items)
        }
    }

    fun moveItem(from: Int, to: Int) {
        queues.moved(from, to)
        if (!connect.isRemote) withController { it.moveMediaItem(from, to) }
    }

    fun removeItem(index: Int) {
        queues.removed(index)
        if (!connect.isRemote) withController { it.removeMediaItem(index) }
    }

    fun skipTo(index: Int) {
        val q = queues.currentId.value
        val e = ext
        if (e != null) { e.skipTo(index); return }
        if (connect.isRemote && q != null) connect.command("playQueue", mapOf("queueId" to JsonPrimitive(q), "index" to JsonPrimitive(index)))
        else withController { it.seekTo(index, 0); it.play() }
    }

    fun togglePlay() {
        val playing = _state.value.isPlaying
        remoteOr(if (playing) "pause" else "play", cast = { if (playing) it.pause() else it.play() }) {
            if (it.isPlaying) it.pause() else it.play()
        }
    }
    fun pause() = remoteOr("pause", cast = { it.pause() }) { it.pause() }
    /** Pause this phone's own player only (casting is starting elsewhere). */
    fun pauseLocal() = withController { it.pause() }

    /** Put local playback at a queue position (after a native Cast session ends). No-op as a remote. */
    fun resumeAt(index: Int, positionMs: Long, play: Boolean) {
        if (connect.isRemote) return
        withController { c ->
            if (index in 0 until c.mediaItemCount) c.seekTo(index, positionMs.coerceAtLeast(0)) else c.seekTo(positionMs)
            if (play) c.play()
        }
    }

    fun next() = remoteOr("next", cast = { it.next() }) { it.seekToNext() }
    fun previous() = remoteOr("prev", cast = { it.previous() }) { it.seekToPrevious() }
    fun seekTo(ms: Long) = remoteOr("seek", mapOf("positionMs" to JsonPrimitive(ms)), cast = { it.seekTo(ms) }) { it.seekTo(ms) }
    fun setVolume(v: Float) = remoteOr("setVolume", mapOf("volume" to JsonPrimitive(v)), cast = { it.setVolume(v) }) { it.volume = v }
    fun setShuffle(on: Boolean) = remoteOr("setShuffle", mapOf("on" to JsonPrimitive(on))) { it.shuffleModeEnabled = on }

    fun cycleRepeat() {
        val next = when (_state.value.repeat) {
            Player.REPEAT_MODE_OFF -> Player.REPEAT_MODE_ALL
            Player.REPEAT_MODE_ALL -> Player.REPEAT_MODE_ONE
            else -> Player.REPEAT_MODE_OFF
        }
        val mode = when (next) { Player.REPEAT_MODE_ONE -> "one"; Player.REPEAT_MODE_ALL -> "all"; else -> "off" }
        remoteOr("setRepeat", mapOf("mode" to JsonPrimitive(mode))) { it.repeatMode = next }
    }

    /** Pitch is a local-only effect; speed is shared with Connect. */
    fun setSpeedPitch(speed: Float, pitch: Float) =
        remoteOr("setSpeed", mapOf("speed" to JsonPrimitive(speed))) { it.playbackParameters = PlaybackParameters(speed, pitch) }

    /** Bring playback from the remote device to this phone. */
    fun transferHere() {
        connect.myDeviceId.value?.let { connect.transfer(it, true) }
    }
}
