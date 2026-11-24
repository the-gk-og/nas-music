package org.nasmusic.wear.playback

import android.content.ComponentName
import android.content.Context
import android.media.AudioManager
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.guava.await
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import org.nasmusic.app.connect.ConnectDevice
import org.nasmusic.app.connect.PlaybackState
import org.nasmusic.app.data.api.NasMusicApi
import org.nasmusic.app.data.api.NewQueue
import org.nasmusic.app.data.api.Track
import org.nasmusic.wear.connect.WearConnect
import org.nasmusic.wear.nowplaying.NowPlaying
import org.nasmusic.wear.nowplaying.NowPlayingStore

/** What the now-playing screen shows, wherever the music is. */
data class NowPlayingUi(
    /** null = nothing anywhere. */
    val track: Track? = null,
    val playing: Boolean = false,
    val positionMs: Long = 0,
    val durationMs: Long = 0,
    /** 0..1 */
    val volume: Float = 1f,
    /** Name of the device playing, or null when it's this watch. */
    val remoteDevice: ConnectDevice? = null,
    val queueId: String? = null,
    val index: Int = 0,
) {
    val isRemote: Boolean get() = remoteDevice != null
}

/**
 * The one place UI, tile and complication send play/pause/skip/volume/queue actions to. Like the
 * phone's PlayerConnection: when another online device is the Connect active device the watch is
 * its remote; otherwise it drives its own [WearPlaybackService] through a [MediaController].
 */
class PlayerHub(
    private val context: Context,
    private val connect: WearConnect,
    private val api: () -> NasMusicApi,
    private val nowPlaying: NowPlayingStore,
    private val scope: CoroutineScope,
) {
    private var controller: MediaController? = null
    private var connecting = false
    private val pending = mutableListOf<(MediaController) -> Unit>()
    private val audio = context.getSystemService(AudioManager::class.java)

    private val _local = MutableStateFlow(NowPlayingUi())
    private val remoteTracks = MutableStateFlow<Map<String, Track>>(emptyMap())
    private val _ui = MutableStateFlow(NowPlayingUi())
    val ui: StateFlow<NowPlayingUi> = _ui.asStateFlow()

    init {
        scope.launch(Dispatchers.Main) {
            combine(_local, connect.state, connect.devices, remoteTracks) { local, s, devices, tracks ->
                val active = s?.activeDeviceId
                val remote = devices.firstOrNull { it.id == active && it.online && active != connect.myDeviceId.value }
                if (remote != null && s != null) remoteUi(s, remote, tracks) else local
            }.collect { u ->
                _ui.value = u
                if (u.isRemote) {
                    u.track?.let { t ->
                        nowPlaying.update(NowPlaying(t.id, t.title, t.artist, t.albumId, u.playing, u.remoteDevice?.name, u.queueId))
                    }
                }
            }
        }
    }

    private fun remoteUi(s: PlaybackState, d: ConnectDevice, tracks: Map<String, Track>): NowPlayingUi {
        val id = s.trackId
        val t = id?.let { tracks[it] }
        if (id != null && t == null) fetchTrack(id)
        return NowPlayingUi(
            track = t ?: id?.let { Track(id = it, title = "…") },
            playing = s.playing,
            positionMs = s.positionAt(System.currentTimeMillis()),
            durationMs = ((t?.durationS ?: 0.0) * 1000).toLong(),
            volume = s.volume,
            remoteDevice = d,
            queueId = s.queueId,
            index = s.index,
        )
    }

    private val inFlight = mutableSetOf<String>()
    private fun fetchTrack(id: String) {
        if (!inFlight.add(id)) return
        scope.launch {
            runCatching { api().track(id) }.onSuccess { t -> remoteTracks.value = remoteTracks.value + (id to t) }
            inFlight.remove(id)
        }
    }

    // ---- local controller ----

    /** Binds to the playback service (which starts it). Safe to call repeatedly. */
    fun connectLocal() = withController { }

    private fun withController(block: (MediaController) -> Unit) {
        scope.launch(Dispatchers.Main) {
            controller?.let { block(it); return@launch }
            pending += block
            if (connecting) return@launch
            connecting = true
            val token = SessionToken(context, ComponentName(context, WearPlaybackService::class.java))
            val mc = runCatching { MediaController.Builder(context, token).buildAsync().await() }.getOrNull()
            connecting = false
            if (mc == null) { pending.clear(); return@launch }
            controller = mc
            mc.addListener(object : Player.Listener {
                override fun onEvents(player: Player, events: Player.Events) = refreshLocal()
            })
            refreshLocal()
            pending.toList().also { pending.clear() }.forEach { it(mc) }
        }
    }

    /** Called every second by the now-playing screen so the progress ring moves. */
    fun refreshLocal() {
        val mc = controller ?: return
        val item = mc.currentMediaItem
        _local.value = NowPlayingUi(
            track = item?.let(WearMediaItems::toTrack),
            playing = mc.isPlaying,
            positionMs = mc.currentPosition,
            durationMs = mc.duration.takeIf { it > 0 } ?: 0,
            volume = localVolume(),
            remoteDevice = null,
            queueId = mc.playlistMetadata.extras?.getString(WearMediaItems.EXTRA_QUEUE_ID),
            index = mc.currentMediaItemIndex,
        )
    }

    fun localQueue(): List<Track> {
        val mc = controller ?: return emptyList()
        return (0 until mc.mediaItemCount).map { WearMediaItems.toTrack(mc.getMediaItemAt(it)) }
    }

    private fun localVolume(): Float {
        val max = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC).coerceAtLeast(1)
        return audio.getStreamVolume(AudioManager.STREAM_MUSIC).toFloat() / max
    }

    // ---- actions ----

    private val remote: Boolean get() = connect.isRemote

    fun playPause() {
        if (remote) connect.command(if (connect.state.value?.playing == true) "pause" else "play")
        else withController { if (it.isPlaying) it.pause() else { if (it.playbackState == Player.STATE_IDLE) it.prepare(); it.play() } }
    }

    fun next() = if (remote) { connect.command("next"); Unit } else withController { it.seekToNext() }
    fun prev() = if (remote) { connect.command("prev"); Unit } else withController { it.seekToPrevious() }

    /**
     * Tile ▶: toggles whatever is loaded (here or remote). With nothing loaded anywhere, resumes
     * [queueId] (or the most recently used server queue) on the watch, where it left off.
     */
    fun playPauseOrResume(queueId: String?) {
        if (remote) { playPause(); return }
        withController { mc ->
            if (mc.mediaItemCount > 0) { playPause(); return@withController }
            scope.launch {
                val q = runCatching {
                    queueId?.let { api().queue(it) } ?: api().queues().maxByOrNull { it.updatedAt.orEmpty() }?.let { api().queue(it.id) }
                }.getOrNull() ?: return@launch
                if (q.tracks.isEmpty()) return@launch
                withContext(Dispatchers.Main) {
                    mc.setMediaItems(q.tracks.map(WearMediaItems::fromTrack), q.currentIndex.coerceIn(0, q.tracks.size - 1), q.positionMs)
                    mc.playlistMetadata = WearMediaItems.queueMetadata(q.id, q.name)
                    mc.prepare()
                    mc.play()
                }
            }
        }
    }

    /** [steps] from the crown/bezel or the +/− buttons; one step = 5 % remotely, one system step locally. */
    fun volumeBy(steps: Int) {
        if (steps == 0) return
        if (remote) {
            val v = ((connect.state.value?.volume ?: 1f) + steps * 0.05f).coerceIn(0f, 1f)
            connect.command("setVolume", mapOf("volume" to JsonPrimitive(v)))
        } else {
            val dir = if (steps > 0) AudioManager.ADJUST_RAISE else AudioManager.ADJUST_LOWER
            repeat(kotlin.math.abs(steps)) { audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, dir, 0) }
            refreshLocal()
        }
    }

    /** Plays a list: on the active remote device if there is one, else here (and makes a server queue for Connect). */
    fun playTracks(tracks: List<Track>, index: Int, name: String) {
        if (tracks.isEmpty()) return
        if (remote) {
            connect.command(
                "playTracks",
                mapOf(
                    "trackIds" to JsonArray(tracks.map { JsonPrimitive(it.id) }),
                    "index" to JsonPrimitive(index),
                    "queueName" to JsonPrimitive(name),
                ),
            )
            return
        }
        withController { mc ->
            mc.setMediaItems(tracks.map(WearMediaItems::fromTrack), index, 0)
            mc.playlistMetadata = WearMediaItems.queueMetadata(null, name)
            mc.prepare()
            mc.play()
            // A server queue lets the phone/web take over where the watch is ("Continue listening").
            scope.launch {
                val q = runCatching { api().createQueue(NewQueue(name, tracks.map { it.id }, index)) }.getOrNull() ?: return@launch
                withContext(Dispatchers.Main) {
                    if (mc.mediaItemCount == tracks.size) mc.playlistMetadata = WearMediaItems.queueMetadata(q.id, name)
                }
            }
        }
    }

    fun jumpTo(index: Int) {
        val q = connect.state.value?.queueId
        if (remote && q != null) connect.command("playQueue", mapOf("queueId" to JsonPrimitive(q), "index" to JsonPrimitive(index)))
        else withController { it.seekTo(index, 0); it.play() }
    }

    /**
     * Sleep detection: fades out over [fadeMs], pauses, then puts the volume back for next time. Works on the
     * watch's own player or, as a remote, on the active device. Stops and restores the volume if [cancelled]
     * turns true during the fade (you moved). Returns true when it paused.
     */
    suspend fun fadeOutAndPause(fadeMs: Long, cancelled: () -> Boolean): Boolean = withContext(Dispatchers.Main) {
        val steps = 15
        val stepMs = (fadeMs / steps).coerceAtLeast(50)
        fun vol(v: Float) = mapOf("volume" to JsonPrimitive(v.coerceIn(0f, 1f)))
        if (remote) {
            val start = connect.state.value?.volume ?: 1f
            for (i in 1..steps) {
                if (cancelled()) { connect.command("setVolume", vol(start)); return@withContext false }
                connect.command("setVolume", vol(start * (1f - i.toFloat() / steps)))
                delay(stepMs)
            }
            connect.command("pause")
            delay(1_500) // let the pause land before the volume comes back
            connect.command("setVolume", vol(start))
            true
        } else {
            val mc = controller ?: return@withContext false
            val start = mc.volume
            for (i in 1..steps) {
                if (cancelled()) { mc.volume = start; return@withContext false }
                mc.volume = start * (1f - i.toFloat() / steps)
                delay(stepMs)
            }
            mc.pause() // the playback service saves the queue position on pause
            mc.volume = start
            refreshLocal()
            true
        }
    }

    /** Seek within the current item, here or on the active remote device. */
    fun seekTo(positionMs: Long) {
        if (remote) connect.command("seek", mapOf("positionMs" to JsonPrimitive(positionMs)))
        else withController { it.seekTo(positionMs) }
    }

    /** Move playback to [deviceId] (may be this watch: the server then sends us "takeover"). */
    fun transferTo(deviceId: String) {
        if (deviceId == connect.myDeviceId.value) connectLocal()
        connect.transfer(deviceId, true)
    }

}
