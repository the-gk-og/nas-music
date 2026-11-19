package org.nasmusic.app.playback

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.PlaybackParameters
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSourceBitmapLoader
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.session.CacheBitmapLoader
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import com.google.common.util.concurrent.ListenableFuture
import com.google.common.util.concurrent.MoreExecutors
import java.io.ByteArrayOutputStream
import java.io.File
import java.time.Instant
import java.util.concurrent.Executors
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.guava.future
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.floatOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import okhttp3.Request
import org.nasmusic.app.AppContainer
import org.nasmusic.app.MainActivity
import org.nasmusic.app.connect.ConnectMessage
import org.nasmusic.app.connect.PlaybackState
import org.nasmusic.app.container
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.eq.EqAudioProcessor

/**
 * Media3 playback in a foreground MediaSessionService: notification, lock screen, Bluetooth,
 * headphone buttons and Galaxy Watch controls all come from the MediaSession. Tracks play from the
 * downloaded file when present, otherwise stream `/stream/{id}` through the routed client.
 *
 * v2: audio goes through the EQ processor chain; this phone takes part in Connect (reports its
 * state when active, executes remote commands, takes over / releases playback); queue position is
 * mirrored to the server; A-B repeat; artwork bitmap data for watches.
 */
@UnstableApi
class PlaybackService : MediaSessionService() {
    private var session: MediaSession? = null
    private var tracker: FeedbackTracker? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main + org.nasmusic.app.diag.CrashLog.handler)
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var c: AppContainer
    private lateinit var player: ExoPlayer
    /** Set while applying a Connect takeover/command, so it isn't mistaken for a local "play". */
    private var applyingRemote = false
    private var lastStateSentMs = 0L
    private var lastProgressMs = 0L

    override fun onCreate() {
        super.onCreate()
        c = container
        val http = OkHttpDataSource.Factory(c.callFactory).setUserAgent("nas-music-android")
        val dataSources = DefaultDataSource.Factory(this, http)
        val eq = EqAudioProcessor { c.eq.active.value }

        val renderers = object : DefaultRenderersFactory(this) {
            override fun buildAudioSink(context: Context, enableFloatOutput: Boolean, enableAudioTrackPlaybackParams: Boolean): AudioSink =
                DefaultAudioSink.Builder(context)
                    .setAudioProcessors(arrayOf(eq))
                    .setEnableFloatOutput(enableFloatOutput)
                    .setEnableAudioTrackPlaybackParams(enableAudioTrackPlaybackParams)
                    .build()
        }

        player = ExoPlayer.Builder(this, renderers)
            .setMediaSourceFactory(DefaultMediaSourceFactory(dataSources))
            .setAudioAttributes(
                AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MUSIC).build(),
                /* handleAudioFocus = */ true,
            )
            .setHandleAudioBecomingNoisy(true)
            .setWakeMode(C.WAKE_MODE_NETWORK)
            .build()

        tracker = FeedbackTracker(player, c.events, c.contextCollector).also { it.attach() }
        player.addListener(object : Player.Listener {
            override fun onEvents(p: Player, events: Player.Events) = onPlayerEvents(events)
            override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
                loadArtworkData(player.currentMediaItemIndex)
                loadArtworkData(player.currentMediaItemIndex + 1)
                c.abRepeat.clear()
            }
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                // Pressing play on this phone makes it the Connect active device (§5 rule 1).
                if (isPlaying && !applyingRemote) c.connect.claimPlayback()
            }
            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_READY) errorRetries = 0
            }
            override fun onPlayerError(error: androidx.media3.common.PlaybackException) = recover(error)
        })

        val openApp = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
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
                ): ListenableFuture<MutableList<MediaItem>> = scope.future(Dispatchers.IO) {
                    mediaItems.map { resolve(it) }.toMutableList()
                }
            })
            .build()

        scope.launch { c.connect.events.collect { onConnect(it) } }
        handler.post(tick)
    }

    /** 4 Hz: A-B repeat, periodic Connect state while playing, queue position mirror. */
    private val tick = object : Runnable {
        override fun run() {
            val now = System.currentTimeMillis()
            AbRepeat.check(c.abRepeat.loop.value, player.currentMediaItem?.mediaId, player.currentPosition)?.let { player.seekTo(it) }
            if (player.isPlaying && now - lastStateSentMs > 5_000) sendState()
            maybeExtendRadio()
            if (player.isPlaying && now - lastProgressMs > 15_000) {
                lastProgressMs = now
                c.queueSync.progress(player.currentMediaItemIndex, player.currentPosition)
            }
            handler.postDelayed(this, 250)
        }
    }

    private var errorRetries = 0

    private fun recover(error: androidx.media3.common.PlaybackException) {
        android.util.Log.w(org.nasmusic.app.diag.CrashLog.TAG, "Playback error ${error.errorCodeName} (retry $errorRetries)", error)
        when (val a = PlaybackRecovery.decide(error.errorCode, errorRetries, player.hasNextMediaItem())) {
            is PlaybackRecovery.Action.Retry -> {
                errorRetries++
                handler.postDelayed({
                    // Re-check LAN vs tunnel first; the stream URL is re-resolved on prepare.
                    scope.launch { c.router.refresh(); player.prepare() }
                }, a.delayMs)
            }
            PlaybackRecovery.Action.Skip -> {
                c.ui.toast("Skipped a song that couldn't be played")
                player.seekToNextMediaItem(); player.prepare()
            }
            PlaybackRecovery.Action.Stop -> c.ui.toast("Playback stopped: ${error.errorCodeName}")
        }
    }

    private var radioFetching = false

    /** Radio queues are endless: when 5 tracks remain, fetch more and append them. */
    private fun maybeExtendRadio() {
        val qid = c.queueSync.currentId.value ?: return
        if (radioFetching || !c.queueSync.isRadio(qid)) return
        if (!RadioLogic.needsMore(player.mediaItemCount, player.currentMediaItemIndex)) return
        radioFetching = true
        scope.launch {
            try {
                val localIds = (0 until player.mediaItemCount).map { player.getMediaItemAt(it).mediaId }
                val resp = runCatching { c.api.radioMore(qid) }.getOrNull() ?: return@launch
                val fresh = RadioLogic.newTracks(resp, localIds, org.nasmusic.app.data.api.ApiFactory.json)
                if (fresh.isEmpty() || c.queueSync.currentId.value != qid) return@launch
                val items = withContext(Dispatchers.IO) { fresh.map { resolve(MediaItems.fromTrack(it, PlaySource.MIX)) } }
                player.addMediaItems(items)
            } finally {
                // Back off a little so a server that returns nothing isn't hammered every tick.
                handler.postDelayed({ radioFetching = false }, 30_000)
            }
        }
    }

    private fun onPlayerEvents(events: Player.Events) {
        if (events.containsAny(
                Player.EVENT_IS_PLAYING_CHANGED, Player.EVENT_MEDIA_ITEM_TRANSITION, Player.EVENT_POSITION_DISCONTINUITY,
                Player.EVENT_SHUFFLE_MODE_ENABLED_CHANGED, Player.EVENT_REPEAT_MODE_CHANGED, Player.EVENT_VOLUME_CHANGED,
                Player.EVENT_PLAYBACK_PARAMETERS_CHANGED,
            )
        ) {
            sendState()
        }
        if (events.containsAny(Player.EVENT_MEDIA_ITEM_TRANSITION, Player.EVENT_IS_PLAYING_CHANGED) && !player.isPlaying) {
            c.queueSync.progress(player.currentMediaItemIndex, player.currentPosition)
        }
    }

    private fun sendState() {
        if (!c.connect.isActiveHere || player.mediaItemCount == 0) return
        lastStateSentMs = System.currentTimeMillis()
        c.connect.reportState(
            PlaybackState(
                queueId = c.queueSync.currentId.value,
                index = player.currentMediaItemIndex,
                trackId = player.currentMediaItem?.mediaId,
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

    // ---- Connect ----

    private suspend fun onConnect(m: ConnectMessage) {
        applyingRemote = true
        try {
            when (m) {
                is ConnectMessage.Takeover -> takeover(m.state)
                ConnectMessage.Release -> player.pause()
                is ConnectMessage.Command -> command(m.action, m.args)
                is ConnectMessage.QueuesChanged -> {
                    c.queueSync.refresh()
                    if (m.queueId != null && m.queueId == c.queueSync.currentId.value) reconcileQueue(m.queueId)
                }
                else -> Unit
            }
        } finally {
            applyingRemote = false
        }
        sendState()
    }

    private suspend fun takeover(s: PlaybackState) {
        val qid = s.queueId
        if (qid != null && qid != c.queueSync.currentId.value) {
            val q = c.queueSync.load(qid) ?: return
            setTracks(q.tracks, s.index, s.positionAt(System.currentTimeMillis()))
            c.queueSync.setCurrent(qid)
        } else if (player.mediaItemCount > 0) {
            if (s.index != player.currentMediaItemIndex) player.seekTo(s.index, s.positionAt(System.currentTimeMillis()))
            else player.seekTo(s.positionAt(System.currentTimeMillis()))
        }
        player.shuffleModeEnabled = s.shuffle
        player.repeatMode = repeatMode(s.repeat)
        player.playbackParameters = PlaybackParameters(s.speed.coerceIn(0.25f, 4f), player.playbackParameters.pitch)
        if (s.playing) player.play() else player.pause()
    }

    private suspend fun command(action: String, a: JsonObject) {
        fun long(k: String) = a[k]?.jsonPrimitive?.longOrNull
        fun float(k: String) = a[k]?.jsonPrimitive?.floatOrNull
        when (action) {
            "play" -> player.play()
            "pause" -> player.pause()
            "next" -> player.seekToNext()
            "prev" -> player.seekToPrevious()
            "seek" -> long("positionMs")?.let { player.seekTo(it) }
            "setVolume" -> float("volume")?.let { player.volume = it.coerceIn(0f, 1f) }
            "setShuffle" -> a["on"]?.jsonPrimitive?.booleanOrNull?.let { player.shuffleModeEnabled = it }
            "setRepeat" -> a["mode"]?.jsonPrimitive?.contentOrNull?.let { player.repeatMode = repeatMode(it) }
            "setSpeed" -> float("speed")?.let { player.playbackParameters = PlaybackParameters(it.coerceIn(0.25f, 4f), player.playbackParameters.pitch) }
            "playQueue" -> {
                val id = a["queueId"]?.jsonPrimitive?.contentOrNull ?: return
                val q = c.queueSync.load(id) ?: return
                setTracks(q.tracks, a["index"]?.jsonPrimitive?.intOrNull ?: q.currentIndex, 0)
                c.queueSync.setCurrent(id)
                player.play()
            }
            "playTracks" -> {
                val ids = a["trackIds"]?.jsonArray?.mapNotNull { it.jsonPrimitive.contentOrNull }.orEmpty()
                if (ids.isEmpty()) return
                val index = a["index"]?.jsonPrimitive?.intOrNull ?: 0
                val name = a["queueName"]?.jsonPrimitive?.contentOrNull ?: "Remote"
                val q = runCatching { c.api.createQueue(org.nasmusic.app.data.api.NewQueue(name, ids, index)) }.getOrNull()
                val tracks = q?.let { c.queueSync.load(it.id)?.tracks } ?: ids.mapNotNull { runCatching { c.api.track(it) }.getOrNull() }
                setTracks(tracks, index, 0)
                c.queueSync.setCurrent(q?.id)
                c.queueSync.refresh()
                player.play()
            }
        }
    }

    /** The current queue changed on another device: adopt its order, keeping the playing track. */
    private suspend fun reconcileQueue(id: String) {
        val q = c.queueSync.load(id) ?: return
        val localIds = (0 until player.mediaItemCount).map { player.getMediaItemAt(it).mediaId }
        val remoteIds = q.tracks.map { it.id }
        if (localIds == remoteIds) return
        val curId = player.currentMediaItem?.mediaId
        val newIndex = remoteIds.indexOf(curId)
        if (newIndex < 0 || player.mediaItemCount == 0) return
        val items = q.tracks.map { resolve(MediaItems.fromTrack(it, PlaySource.LIBRARY)) }
        val cur = player.currentMediaItemIndex
        if (cur + 1 < player.mediaItemCount) player.removeMediaItems(cur + 1, player.mediaItemCount)
        if (cur > 0) player.removeMediaItems(0, cur)
        player.addMediaItems(0, items.subList(0, newIndex))
        player.addMediaItems(items.subList(newIndex + 1, items.size))
    }

    private suspend fun setTracks(tracks: List<Track>, index: Int, positionMs: Long) {
        if (tracks.isEmpty()) return
        val items = withContext(Dispatchers.IO) { tracks.map { resolve(MediaItems.fromTrack(it, PlaySource.LIBRARY)) } }
        player.setMediaItems(items, index.coerceIn(0, items.size - 1), positionMs.coerceAtLeast(0))
        player.prepare()
    }

    private fun repeatMode(s: String) = when (s) { "one" -> Player.REPEAT_MODE_ONE; "all" -> Player.REPEAT_MODE_ALL; else -> Player.REPEAT_MODE_OFF }

    // ---- Media ----

    /** Local file if downloaded, else the stream URL. */
    private suspend fun resolve(item: MediaItem): MediaItem {
        val local = c.library.localPath(item.mediaId)?.let(::File)?.takeIf { it.exists() }
        val uri = local?.let(Uri::fromFile)
            ?: Uri.parse(MediaItems.streamUrl(item.mediaId, c.settings.settings.value.streamBitrate))
        return item.buildUpon().setUri(uri).build()
    }

    /**
     * Galaxy Watch (and some car units) only show cover art when the session metadata carries the
     * bitmap itself, so put a ≤ 512 px JPEG into artworkData for the current and next item.
     */
    private fun loadArtworkData(index: Int) {
        if (index !in 0 until player.mediaItemCount) return
        val item = player.getMediaItemAt(index)
        if (item.mediaMetadata.artworkData != null) return
        val albumId = item.mediaMetadata.extras?.getString(MediaItems.EXTRA_ALBUM_ID) ?: return
        scope.launch {
            val bytes = withContext(Dispatchers.IO) { fetchArt(albumId) } ?: return@launch
            // The queue may have changed meanwhile: find the item again by id.
            val i = (0 until player.mediaItemCount).firstOrNull { player.getMediaItemAt(it).mediaId == item.mediaId } ?: return@launch
            val cur = player.getMediaItemAt(i)
            val md = cur.mediaMetadata.buildUpon().setArtworkData(bytes, MediaMetadata.PICTURE_TYPE_FRONT_COVER).build()
            player.replaceMediaItem(i, cur.buildUpon().setMediaMetadata(md).build())
        }
    }

    private fun fetchArt(albumId: String): ByteArray? = runCatching {
        c.callFactory.newCall(Request.Builder().url(MediaItems.artUrl(albumId, 512)).build()).execute().use { r ->
            if (!r.isSuccessful) return null
            ArtworkScaler.toMax512Jpeg(r.body?.bytes() ?: return null)
        }
    }.getOrNull()

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = session

    override fun onTaskRemoved(rootIntent: Intent?) {
        val p = session?.player
        if (p == null || !p.playWhenReady || p.mediaItemCount == 0) stopSelf()
    }

    override fun onDestroy() {
        handler.removeCallbacks(tick)
        c.queueSync.progress(player.currentMediaItemIndex, player.currentPosition)
        tracker?.release()
        session?.run {
            player.release()
            release()
        }
        session = null
        scope.cancel()
        super.onDestroy()
    }
}

object ArtworkScaler {
    const val MAX_PX = 512

    /** Returns JPEG bytes no larger than 512 px on the long side (re-encodes only when needed). */
    fun toMax512Jpeg(bytes: ByteArray): ByteArray? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        if (bounds.outWidth <= 0) return null
        if (maxOf(bounds.outWidth, bounds.outHeight) <= MAX_PX) return bytes
        val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return null
        val (w, h) = scaledSize(bmp.width, bmp.height)
        val scaled = Bitmap.createScaledBitmap(bmp, w, h, true)
        return ByteArrayOutputStream().use { scaled.compress(Bitmap.CompressFormat.JPEG, 88, it); it.toByteArray() }
    }

    fun scaledSize(w: Int, h: Int): Pair<Int, Int> {
        val long = maxOf(w, h)
        if (long <= MAX_PX) return w to h
        val f = MAX_PX.toDouble() / long
        return (w * f).toInt().coerceAtLeast(1) to (h * f).toInt().coerceAtLeast(1)
    }
}
