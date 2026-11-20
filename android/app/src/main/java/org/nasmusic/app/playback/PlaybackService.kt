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
import androidx.media3.session.CommandButton
import androidx.media3.session.LibraryResult
import androidx.media3.session.MediaLibraryService
import androidx.media3.session.MediaSession
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionError
import androidx.media3.session.SessionResult
import com.google.common.collect.ImmutableList
import com.google.common.util.concurrent.Futures
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
 * Media3 playback in a foreground MediaLibraryService (a MediaSessionService that also serves a
 * browse tree, for Android Auto — see [AutoLibrary]): notification, lock screen, Bluetooth,
 * headphone buttons and Galaxy Watch controls all come from the MediaSession. Tracks play from the
 * downloaded file when present, otherwise stream `/stream/{id}` through the routed client.
 *
 * v2: audio goes through the EQ processor chain; this phone takes part in Connect (reports its
 * state when active, executes remote commands, takes over / releases playback); queue position is
 * mirrored to the server; A-B repeat; artwork bitmap data for watches.
 */
@UnstableApi
class PlaybackService : MediaLibraryService() {
    private var session: MediaLibrarySession? = null
    private var tracker: FeedbackTracker? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main + org.nasmusic.app.diag.CrashLog.handler)
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var c: AppContainer
    private lateinit var player: ExoPlayer
    /** Set while applying a Connect takeover/command, so it isn't mistaken for a local "play". */
    private var applyingRemote = false
    private var lastStateSentMs = 0L
    private var lastProgressMs = 0L
    private lateinit var mixer: VolumeMixer
    private lateinit var crossfader: Crossfader
    private lateinit var library: AutoLibrary
    /** True while this service paused at the end of a track for the sleep timer. */
    private var sleepPausedAtEnd = false

    override fun onCreate() {
        super.onCreate()
        c = container
        val http = OkHttpDataSource.Factory(c.callFactory).setUserAgent("nas-music-android")
        val dataSources = DefaultDataSource.Factory(this, http)
        // Each player gets its own EQ processor (they keep filter state); both read the same settings.
        fun renderers() = object : DefaultRenderersFactory(this) {
            override fun buildAudioSink(context: Context, enableFloatOutput: Boolean, enableAudioTrackPlaybackParams: Boolean): AudioSink =
                DefaultAudioSink.Builder(context)
                    .setAudioProcessors(arrayOf(EqAudioProcessor { c.eq.active.value }))
                    .setEnableFloatOutput(enableFloatOutput)
                    .setEnableAudioTrackPlaybackParams(enableAudioTrackPlaybackParams)
                    .build()
        }
        val attributes = AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MUSIC).build()

        // Gapless: ExoPlayer plays consecutive items through one AudioTrack and trims encoder delay/padding
        // (Opus pre-skip; FLAC has none), as long as the sample rate/channels stay the same.
        player = ExoPlayer.Builder(this, renderers())
            .setMediaSourceFactory(DefaultMediaSourceFactory(dataSources))
            .setAudioAttributes(attributes, /* handleAudioFocus = */ true)
            .setHandleAudioBecomingNoisy(true)
            .setWakeMode(C.WAKE_MODE_NETWORK)
            .build()
        mixer = VolumeMixer { player.volume = it }
        crossfader = Crossfader {
            ExoPlayer.Builder(this, renderers())
                .setMediaSourceFactory(DefaultMediaSourceFactory(dataSources))
                .setAudioAttributes(attributes, /* handleAudioFocus = */ false)
                .build()
        }
        library = AutoLibrary { c.api }

        tracker = FeedbackTracker(player, c.events, c.contextCollector).also { it.attach() }
        player.addListener(object : Player.Listener {
            override fun onEvents(p: Player, events: Player.Events) = onPlayerEvents(events)
            override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
                // Family mode: the server flags tracks already in a queue that this user may not hear.
                if (mediaItem?.mediaMetadata?.extras?.getBoolean(MediaItems.EXTRA_FAMILY_HIDDEN) == true) {
                    if (player.hasNextMediaItem()) player.seekToNextMediaItem() else player.pause()
                    return
                }
                applyLevelling()
                loadArtworkData(player.currentMediaItemIndex)
                loadArtworkData(player.currentMediaItemIndex + 1)
                c.abRepeat.clear()
            }
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                // Pressing play on this phone makes it the Connect active device (§5 rule 1).
                if (isPlaying && !applyingRemote) c.connect.claimPlayback()
                if (!isPlaying) cancelCrossfade()
                if (isPlaying) { handler.removeCallbacks(fastTick); handler.post(fastTick) }
            }
            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_READY) errorRetries = 0
                if (state == Player.STATE_ENDED && c.sleep.timer.value != null) finishSleep(pauseNow = false)
            }
            override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
                if (!playWhenReady && reason == Player.PLAY_WHEN_READY_CHANGE_REASON_END_OF_MEDIA_ITEM && sleepPausedAtEnd) {
                    finishSleep(pauseNow = false)
                    if (player.hasNextMediaItem()) player.seekToNextMediaItem()
                }
            }
            override fun onPositionDiscontinuity(old: Player.PositionInfo, new: Player.PositionInfo, reason: Int) {
                if (reason == Player.DISCONTINUITY_REASON_SEEK && !crossfadeSeeking) cancelCrossfade()
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
        val sessionPlayer = UserVolumePlayer(player, mixer) { v -> tracker?.userVolumeChanged(v) }
        session = MediaLibrarySession.Builder(this, sessionPlayer, LibraryCallback())
            .setSessionActivity(openApp)
            .setBitmapLoader(bitmaps)
            .setMediaButtonPreferences(listOf(sleepButton(false)))
            .build()

        scope.launch { c.listening.settings.collect { applyLevelling() } }
        scope.launch {
            c.sleep.timer.collect { t ->
                session?.setMediaButtonPreferences(listOf(sleepButton(t != null)))
                if (t == null) { mixer.sleep = 1f; if (sleepPausedAtEnd) { sleepPausedAtEnd = false; player.pauseAtEndOfMediaItems = false } }
            }
        }
        scope.launch { c.connect.events.collect { onConnect(it) } }
        handler.post(tick)
    }

    // ---- Listening: levelling, crossfade, sleep timer ----

    private fun facts(i: Int) = if (i in 0 until player.mediaItemCount) MediaItems.facts(player.getMediaItemAt(i)) else null

    /** ReplayGain for the current item (track or album gain per the setting). */
    private fun applyLevelling() {
        val s = c.listening.settings.value
        val i = player.currentMediaItemIndex
        val prev = player.previousMediaItemIndex.takeIf { it != C.INDEX_UNSET }?.let(::facts)
        val next = player.nextMediaItemIndex.takeIf { it != C.INDEX_UNSET }?.let(::facts)
        val album = ListeningRules.useAlbumGain(s, prev, facts(i), next, player.shuffleModeEnabled)
        mixer.levelling = ListeningRules.gain(facts(i), s, album).toFloat()
    }

    private var crossfadeSeeking = false

    private fun cancelCrossfade() {
        if (crossfadeSeeking) return
        crossfader.release()
        mixer.fade = 1f
    }

    /** 20 Hz while playing: crossfade timing and the sleep timer's fades. */
    private val fastTick = object : Runnable {
        override fun run() {
            runCatching { listeningTick() }.onFailure { android.util.Log.w(org.nasmusic.app.diag.CrashLog.TAG, "listening tick", it) }
            if (player.isPlaying || crossfader.active) handler.postDelayed(this, 50)
        }
    }

    private fun listeningTick() {
        crossfader.step()?.let { x -> mixer.fade = ListeningRules.fadeIn(x).toFloat() } ?: run { if (mixer.fade != 1f && !crossfader.active) mixer.fade = 1f }
        val sleep = c.sleep.timer.value
        val cur = player.currentMediaItem ?: return
        val nextIdx = player.nextMediaItemIndex
        val next = if (nextIdx != C.INDEX_UNSET && player.repeatMode != Player.REPEAT_MODE_ONE) player.getMediaItemAt(nextIdx) else null
        val dur = player.duration.takeIf { it != C.TIME_UNSET && it > 0 } ?: return
        val pos = player.currentPosition
        val remaining = ((dur - pos) / player.playbackParameters.speed.coerceAtLeast(0.25f)).toLong()

        // Sleep timer
        if (sleep != null) {
            if (sleep.mode == SleepTimer.Mode.MINUTES) {
                val left = sleep.endsAtMs - System.currentTimeMillis()
                if (left <= 0) { finishSleep(pauseNow = true); return }
                mixer.sleep = SleepTimer.fadeLevel(left)
            } else if (sleep.stopsAt(MediaItems.facts(cur), MediaItems.facts(next))) {
                if (!sleepPausedAtEnd) { sleepPausedAtEnd = true; player.pauseAtEndOfMediaItems = true }
                mixer.sleep = SleepTimer.fadeLevel(remaining, SleepTimer.TRACK_FADE_MS)
                return // no crossfade into a track that won't play
            }
        }

        // Crossfade
        if (next == null || crossfader.active || player.mediaItemCount < 2) return
        if (c.abRepeat.loop.value != null) return
        val fadeMs = ListeningRules.crossfadeMs(c.listening.settings.value, MediaItems.facts(cur), MediaItems.facts(next))
        if (fadeMs <= 0 || dur < fadeMs * 2) return
        val startAt = dur - fadeMs
        if (pos >= startAt - 5_000 && pos < startAt) crossfader.prepare(cur, startAt, player.playbackParameters.speed)
        if (pos >= startAt && crossfader.isReadyFor(cur.mediaId)) {
            crossfader.start(fadeMs, mixer.total)
            mixer.fade = 0f
            tracker?.markAutoTransition()
            crossfadeSeeking = true
            try { player.seekToNextMediaItem() } finally { crossfadeSeeking = false }
        }
    }

    private fun finishSleep(pauseNow: Boolean) {
        val wasPlaying = player.isPlaying
        if (pauseNow) player.pause()
        cancelCrossfade()
        sleepPausedAtEnd = false
        player.pauseAtEndOfMediaItems = false
        c.sleep.set(null)
        mixer.sleep = 0f
        // Back to full level once the pause has taken effect, so the next play isn't silent.
        handler.postDelayed({ mixer.sleep = 1f }, 500)
        if (wasPlaying || !pauseNow) c.ui.toast("Sleep timer: playback stopped")
    }

    private fun sleepButton(on: Boolean): CommandButton =
        CommandButton.Builder(CommandButton.ICON_UNDEFINED)
            .setCustomIconResId(org.nasmusic.app.R.drawable.ic_sleep_timer)
            .setDisplayName(if (on) "Sleep timer on (tap to turn off)" else "Sleep timer: 30 min")
            .setSessionCommand(SessionCommand(CMD_SLEEP, android.os.Bundle.EMPTY))
            .setSlots(CommandButton.SLOT_OVERFLOW)
            .build()

    // ---- MediaSession / library callback (app, notification, Bluetooth, watch, Android Auto) ----

    private inner class LibraryCallback : MediaLibrarySession.Callback {
        override fun onConnect(session: MediaSession, controller: MediaSession.ControllerInfo): MediaSession.ConnectionResult {
            val cmds = MediaSession.ConnectionResult.DEFAULT_SESSION_AND_LIBRARY_COMMANDS.buildUpon()
                .add(SessionCommand(CMD_SLEEP, android.os.Bundle.EMPTY)).build()
            return MediaSession.ConnectionResult.AcceptedResultBuilder(session).setAvailableSessionCommands(cmds).build()
        }

        override fun onCustomCommand(
            session: MediaSession, controller: MediaSession.ControllerInfo, customCommand: SessionCommand, args: android.os.Bundle,
        ): ListenableFuture<SessionResult> {
            if (customCommand.customAction == CMD_SLEEP) {
                c.sleep.toggleFromNotification()
                c.ui.toast(if (c.sleep.timer.value != null) "Sleep timer: 30 min" else "Sleep timer off")
                return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS))
            }
            return Futures.immediateFuture(SessionResult(SessionError.ERROR_NOT_SUPPORTED))
        }

        override fun onAddMediaItems(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
            mediaItems: MutableList<MediaItem>,
        ): ListenableFuture<MutableList<MediaItem>> = scope.future(Dispatchers.IO) {
            mediaItems.flatMap { item ->
                val e = runCatching { library.expand(item.mediaId) }.getOrNull()
                if (e != null) listOfNotNull(e.tracks.getOrNull(e.startIndex)).map { resolve(MediaItems.fromTrack(it, e.source, e.mixId)) }
                else listOf(resolve(item))
            }.toMutableList()
        }

        override fun onSetMediaItems(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
            mediaItems: MutableList<MediaItem>,
            startIndex: Int,
            startPositionMs: Long,
        ): ListenableFuture<MediaSession.MediaItemsWithStartPosition> = scope.future {
            val single = mediaItems.singleOrNull()
            val query = single?.requestMetadata?.searchQuery
            val e = when {
                single == null -> null
                single.mediaId.isEmpty() && query != null -> library.voice(query)
                else -> runCatching { library.expand(single.mediaId) }.getOrNull()
            }
            if (e != null && e.tracks.isNotEmpty()) {
                val items = withContext(Dispatchers.IO) { e.tracks.map { resolve(MediaItems.fromTrack(it, e.source, e.mixId)) } }
                if (e.radioQueueId != null) { c.queueSync.markRadio(e.radioQueueId); c.queueSync.setCurrent(e.radioQueueId); c.queueSync.refresh() }
                else c.queueSync.created(e.queueName, e.tracks, e.startIndex)
                MediaSession.MediaItemsWithStartPosition(items, e.startIndex, 0)
            } else {
                val items = withContext(Dispatchers.IO) { mediaItems.map { resolve(it) } }
                MediaSession.MediaItemsWithStartPosition(items, startIndex, startPositionMs)
            }
        }

        override fun onGetLibraryRoot(
            session: MediaLibrarySession, browser: MediaSession.ControllerInfo, params: LibraryParams?,
        ): ListenableFuture<LibraryResult<MediaItem>> = Futures.immediateFuture(LibraryResult.ofItem(library.root(), params))

        override fun onGetItem(
            session: MediaLibrarySession, browser: MediaSession.ControllerInfo, mediaId: String,
        ): ListenableFuture<LibraryResult<MediaItem>> = scope.future {
            if (mediaId == AutoLibrary.ROOT) LibraryResult.ofItem(library.root(), null)
            else runCatching { c.api.track(AutoLibrary.parsePlayId(mediaId)?.second ?: mediaId) }.getOrNull()
                ?.let { LibraryResult.ofItem(MediaItems.fromTrack(it, PlaySource.LIBRARY), null) }
                ?: LibraryResult.ofError(SessionError.ERROR_BAD_VALUE)
        }

        override fun onGetChildren(
            session: MediaLibrarySession, browser: MediaSession.ControllerInfo, parentId: String, page: Int, pageSize: Int, params: LibraryParams?,
        ): ListenableFuture<LibraryResult<ImmutableList<MediaItem>>> = scope.future {
            runCatching { library.children(parentId) }.fold(
                { LibraryResult.ofItemList(paged(it, page, pageSize), params) },
                { LibraryResult.ofError(SessionError.ERROR_IO) },
            )
        }

        override fun onSearch(
            session: MediaLibrarySession, browser: MediaSession.ControllerInfo, query: String, params: LibraryParams?,
        ): ListenableFuture<LibraryResult<Void>> = scope.future {
            val n = runCatching { library.search(query).size }.getOrDefault(0)
            session.notifySearchResultChanged(browser, query, n, params)
            LibraryResult.ofVoid()
        }

        override fun onGetSearchResult(
            session: MediaLibrarySession, browser: MediaSession.ControllerInfo, query: String, page: Int, pageSize: Int, params: LibraryParams?,
        ): ListenableFuture<LibraryResult<ImmutableList<MediaItem>>> = scope.future {
            LibraryResult.ofItemList(paged(runCatching { library.search(query) }.getOrDefault(emptyList()), page, pageSize), params)
        }
    }

    private fun paged(all: List<MediaItem>, page: Int, size: Int): List<MediaItem> {
        if (size <= 0 || size == Int.MAX_VALUE) return all
        val from = (page.toLong() * size).coerceAtMost(all.size.toLong()).toInt()
        return all.subList(from, (from + size).coerceAtMost(all.size))
    }

    /** 4 Hz: A-B repeat, periodic Connect state while playing, queue position mirror. */
    private val tick = object : Runnable {
        override fun run() {
            val now = System.currentTimeMillis()
            AbRepeat.check(c.abRepeat.loop.value, player.currentMediaItem?.mediaId, player.currentPosition)?.let { player.seekTo(it) }
            if (player.isPlaying && now - lastStateSentMs > 5_000) sendState()
            // Casting: the local player is paused, so poll the speaker (track changes, pause from its screen).
            if (ext != null && now - lastStateSentMs > 2_000) sendState()
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

    /** A Google Cast session (full flavour) playing this phone's queue; Connect sees it as this phone. */
    private val ext: ExternalTarget? get() = c.player.external?.takeIf { it.active }

    private fun sendState() {
        if (!c.connect.isActiveHere || player.mediaItemCount == 0) return
        lastStateSentMs = System.currentTimeMillis()
        // While casting, report what the speaker is doing so the web app, desktop and watch follow it.
        val cast = ext?.overlay(
            NowPlaying(
                item = player.currentMediaItem,
                queue = (0 until player.mediaItemCount).map { player.getMediaItemAt(it) },
                index = player.currentMediaItemIndex,
                volume = mixer.user,
            ),
        )
        c.connect.reportState(
            PlaybackState(
                queueId = c.queueSync.currentId.value,
                index = cast?.index ?: player.currentMediaItemIndex,
                trackId = cast?.item?.mediaId ?: player.currentMediaItem?.mediaId,
                positionMs = cast?.positionMs ?: player.currentPosition,
                positionAt = Instant.now().toString(),
                playing = cast?.isPlaying ?: player.isPlaying,
                volume = cast?.volume ?: mixer.user,
                shuffle = player.shuffleModeEnabled,
                repeat = when (player.repeatMode) { Player.REPEAT_MODE_ONE -> "one"; Player.REPEAT_MODE_ALL -> "all"; else -> "off" },
                speed = player.playbackParameters.speed,
                castTo = if (cast != null) ext?.name ?: "a Cast speaker" else null,
            ),
        )
    }

    // ---- Connect ----

    private suspend fun onConnect(m: ConnectMessage) {
        applyingRemote = true
        try {
            when (m) {
                // While casting this phone stays the active device; the speaker plays, not the phone.
                is ConnectMessage.Takeover -> if (ext == null) takeover(m.state)
                ConnectMessage.Release -> ext?.pause() ?: player.pause()
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
        ext?.let { e ->
            when (action) {
                "play" -> { e.play(); return }
                "pause" -> { e.pause(); return }
                "next" -> { e.next(); return }
                "prev" -> { e.previous(); return }
                "seek" -> { long("positionMs")?.let(e::seekTo); return }
                "setVolume" -> { float("volume")?.let(e::setVolume); return }
                else -> Unit
            }
        }
        when (action) {
            "play" -> player.play()
            "pause" -> player.pause()
            "next" -> player.seekToNext()
            "prev" -> player.seekToPrevious()
            "seek" -> long("positionMs")?.let { player.seekTo(it) }
            "setVolume" -> float("volume")?.let { mixer.user = it; tracker?.userVolumeChanged(mixer.user) }
            "setShuffle" -> a["on"]?.jsonPrimitive?.booleanOrNull?.let { player.shuffleModeEnabled = it }
            "setRepeat" -> a["mode"]?.jsonPrimitive?.contentOrNull?.let { player.repeatMode = repeatMode(it) }
            "setSpeed" -> float("speed")?.let { player.playbackParameters = PlaybackParameters(it.coerceIn(0.25f, 4f), player.playbackParameters.pitch) }
            "playQueue" -> {
                val id = a["queueId"]?.jsonPrimitive?.contentOrNull ?: return
                val q = c.queueSync.load(id) ?: return
                setTracks(q.tracks, a["index"]?.jsonPrimitive?.intOrNull ?: q.currentIndex, 0)
                c.queueSync.setCurrent(id)
                ext?.reload() ?: player.play()
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
                ext?.reload() ?: player.play()
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

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaLibrarySession? = session

    override fun onTaskRemoved(rootIntent: Intent?) {
        val p = session?.player
        if (p == null || !p.playWhenReady || p.mediaItemCount == 0) stopSelf()
    }

    override fun onDestroy() {
        handler.removeCallbacks(tick)
        handler.removeCallbacks(fastTick)
        crossfader.release()
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

private const val CMD_SLEEP = "org.nasmusic.sleep_timer"

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
