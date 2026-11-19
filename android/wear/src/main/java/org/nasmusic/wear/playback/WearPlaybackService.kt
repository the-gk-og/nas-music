package org.nasmusic.wear.playback

import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.provider.Settings
import android.util.Log
import androidx.core.net.toUri
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.datasource.DataSourceBitmapLoader
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.session.CacheBitmapLoader
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import com.google.common.util.concurrent.MoreExecutors
import java.time.Instant
import java.util.concurrent.Executors
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.floatOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import org.nasmusic.app.connect.ConnectMessage
import org.nasmusic.app.connect.PlaybackState
import org.nasmusic.app.data.api.NewQueue
import org.nasmusic.app.data.api.Queue
import org.nasmusic.app.data.api.QueuePatch
import org.nasmusic.wear.WearApp
import org.nasmusic.wear.nowplaying.NowPlaying
import org.nasmusic.wear.ui.MainActivity

/**
 * Plays on the watch, to Bluetooth headphones only: ExoPlayer refuses the watch speaker
 * ([ExoPlayer.Builder.setSuppressPlaybackOnUnsuitableOutput]) and we open the system Bluetooth
 * picker instead, as Wear OS media apps are expected to.
 *
 * Offline files are played when present, otherwise Opus is streamed through the routed call factory
 * (LAN with the pinned cert, or the tunnel). While playing, the watch is the Connect active device.
 */
class WearPlaybackService : MediaSessionService() {
    private lateinit var player: ExoPlayer
    private lateinit var session: MediaSession
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val c by lazy { (application as WearApp).container }

    override fun onCreate() {
        super.onCreate()
        val http = OkHttpDataSource.Factory(c.callFactory).setUserAgent("nas-music-wear")
        val dataSources = DefaultDataSource.Factory(this, http)
        player = ExoPlayer.Builder(this)
            .setMediaSourceFactory(DefaultMediaSourceFactory(dataSources))
            .setAudioAttributes(
                AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MUSIC).build(),
                /* handleAudioFocus = */ true,
            )
            .setHandleAudioBecomingNoisy(true)
            .setWakeMode(C.WAKE_MODE_NETWORK)
            .setSuppressPlaybackOnUnsuitableOutput(true)
            .build()
        player.addListener(object : Player.Listener {
            override fun onPlaybackSuppressionReasonChanged(reason: Int) {
                if (reason == Player.PLAYBACK_SUPPRESSION_REASON_UNSUITABLE_AUDIO_OUTPUT) openBluetoothPicker()
            }
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                if (isPlaying && !c.connect.isActiveHere) c.connect.claimPlayback()
                if (!isPlaying) saveQueueProgress()
            }
            override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) = saveQueueProgress()
            override fun onEvents(p: Player, events: Player.Events) {
                if (events.containsAny(
                        Player.EVENT_IS_PLAYING_CHANGED, Player.EVENT_MEDIA_ITEM_TRANSITION, Player.EVENT_POSITION_DISCONTINUITY,
                        Player.EVENT_SHUFFLE_MODE_ENABLED_CHANGED, Player.EVENT_REPEAT_MODE_CHANGED, Player.EVENT_VOLUME_CHANGED,
                    )
                ) publish()
            }
        })

        val openApp = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java).putExtra(MainActivity.EXTRA_OPEN, "now_playing"),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val bitmaps = CacheBitmapLoader(
            DataSourceBitmapLoader(MoreExecutors.listeningDecorator(Executors.newSingleThreadExecutor()), dataSources),
        )
        session = MediaSession.Builder(this, player)
            .setSessionActivity(openApp)
            .setBitmapLoader(bitmaps)
            .setCallback(object : MediaSession.Callback {
                override fun onAddMediaItems(
                    mediaSession: MediaSession,
                    controller: MediaSession.ControllerInfo,
                    mediaItems: MutableList<MediaItem>,
                ): ListenableFuture<MutableList<MediaItem>> = Futures.immediateFuture(mediaItems.map(::resolve).toMutableList())
            })
            .build()

        c.connect.acquire(HOLDER)
        scope.launch { c.connect.events.collect(::onConnect) }
        // Started playing before the socket was up (offline, or still connecting): claim once it is.
        scope.launch {
            c.connect.connected.collect { up -> if (up && player.isPlaying && !c.connect.isActiveHere) { c.connect.claimPlayback(); publish() } }
        }
        // Keep the shared position fresh for other devices' progress bars.
        scope.launch {
            while (true) {
                delay(15_000)
                if (player.isPlaying) { publish(); saveQueueProgress() }
            }
        }
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession = session

    override fun onTaskRemoved(rootIntent: Intent?) {
        if (!player.playWhenReady || player.mediaItemCount == 0) stopSelf()
    }

    override fun onDestroy() {
        saveQueueProgress()
        scope.cancel()
        c.connect.release(HOLDER)
        session.release()
        player.release()
        super.onDestroy()
    }

    private fun resolve(item: MediaItem): MediaItem {
        val local = c.offline.localFile(item.mediaId)
        val uri = local?.toUri() ?: WearMediaItems.streamUrl(item.mediaId, c.settings.current.opusKbps).toUri()
        return item.buildUpon().setUri(uri).build()
    }

    private val queueId: String?
        get() = player.playlistMetadata.extras?.getString(WearMediaItems.EXTRA_QUEUE_ID)

    /** Tell Connect (when active here) and the tile/complication what is playing. */
    private fun publish() {
        val item = player.currentMediaItem ?: return
        val t = WearMediaItems.toTrack(item)
        c.nowPlaying.update(NowPlaying(t.id, t.title, t.artist, t.albumId, player.isPlaying, deviceName = null, queueId = queueId))
        if (c.connect.isActiveHere) {
            c.connect.reportState(
                PlaybackState(
                    queueId = queueId,
                    index = player.currentMediaItemIndex,
                    trackId = t.id,
                    positionMs = player.currentPosition,
                    positionAt = Instant.now().toString(),
                    playing = player.isPlaying,
                    volume = player.volume,
                    shuffle = player.shuffleModeEnabled,
                    repeat = when (player.repeatMode) { Player.REPEAT_MODE_ONE -> "one"; Player.REPEAT_MODE_ALL -> "all"; else -> "off" },
                    speed = player.playbackParameters.speed,
                ),
            )
        }
    }

    private fun saveQueueProgress() {
        val q = queueId ?: return
        val idx = player.currentMediaItemIndex
        val pos = player.currentPosition
        c.scope.launch { runCatching { c.api.patchQueue(q, QueuePatch(currentIndex = idx, positionMs = pos)) } }
    }

    private suspend fun onConnect(m: ConnectMessage) {
        when (m) {
            is ConnectMessage.Takeover -> {
                val s = m.state
                val q = s.queueId?.let { runCatching { c.api.queue(it) }.getOrNull() }
                if (q == null || q.tracks.isEmpty()) return
                load(q, s.index, s.positionAt(System.currentTimeMillis()), s.playing)
            }
            ConnectMessage.Release -> player.pause()
            is ConnectMessage.Command -> if (c.connect.isActiveHere) command(m.action, m.args)
            else -> Unit
        }
    }

    private fun load(q: Queue, index: Int, positionMs: Long, play: Boolean) {
        player.setMediaItems(q.tracks.map { resolve(WearMediaItems.fromTrack(it)) }, index.coerceIn(0, q.tracks.size - 1), positionMs)
        player.playlistMetadata = WearMediaItems.queueMetadata(q.id, q.name)
        player.prepare()
        player.playWhenReady = play
    }

    private suspend fun command(action: String, args: JsonObject) {
        fun str(k: String) = (args[k] as? JsonPrimitive)?.contentOrNull
        fun int(k: String) = (args[k] as? JsonPrimitive)?.intOrNull
        when (action) {
            "play" -> player.play()
            "pause" -> player.pause()
            "next" -> player.seekToNext()
            "prev" -> player.seekToPrevious()
            "seek" -> (args["positionMs"] as? JsonPrimitive)?.longOrNull?.let { player.seekTo(it) }
            "setVolume" -> (args["volume"] as? JsonPrimitive)?.floatOrNull?.let { player.volume = it.coerceIn(0f, 1f) }
            "setShuffle" -> (args["on"] as? JsonPrimitive)?.booleanOrNull?.let { player.shuffleModeEnabled = it }
            "setRepeat" -> player.repeatMode = when (str("mode")) { "one" -> Player.REPEAT_MODE_ONE; "all" -> Player.REPEAT_MODE_ALL; else -> Player.REPEAT_MODE_OFF }
            "setSpeed" -> (args["speed"] as? JsonPrimitive)?.floatOrNull?.let { player.setPlaybackSpeed(it) }
            "playQueue" -> {
                val q = str("queueId")?.let { runCatching { c.api.queue(it) }.getOrNull() } ?: return
                if (q.tracks.isNotEmpty()) load(q, int("index") ?: q.currentIndex, 0, true)
            }
            "playTracks" -> {
                val ids = runCatching { args["trackIds"]!!.jsonArray.mapNotNull { it.jsonPrimitive.contentOrNull } }.getOrNull() ?: return
                val created = runCatching { c.api.createQueue(NewQueue(str("queueName") ?: "Queue", ids, int("index") ?: 0)) }.getOrNull() ?: return
                val q = if (created.tracks.isNotEmpty()) created else runCatching { c.api.queue(created.id) }.getOrNull() ?: return
                load(q, int("index") ?: 0, 0, true)
            }
            else -> Log.d(TAG, "ignored command $action")
        }
    }

    /** The Wear OS output switcher, filtered to headphones; falls back to plain Bluetooth settings. */
    private fun openBluetoothPicker() {
        val picker = Intent("com.google.android.clockwork.settings.connectivity.bluetooth.ACTION_BLUETOOTH_SETTINGS")
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            .putExtra("EXTRA_CONNECTION_ONLY", true)
            .putExtra("EXTRA_CLOSE_ON_CONNECT", true)
            .putExtra("android.bluetooth.devicepicker.extra.FILTER_TYPE", 1)
        val intent = if (packageManager.queryIntentActivities(picker, PackageManager.MATCH_SYSTEM_ONLY).isNotEmpty()) picker
        else Intent(Settings.ACTION_BLUETOOTH_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { startActivity(intent) }.onFailure { Log.w(TAG, "no Bluetooth settings", it) }
        c.ui.toast("Connect Bluetooth headphones to play on the watch")
    }

    private companion object {
        const val TAG = "WearPlayback"
        const val HOLDER = "playback"
    }
}
