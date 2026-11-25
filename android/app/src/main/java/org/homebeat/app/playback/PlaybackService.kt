package org.homebeat.app.playback

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
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
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
import org.homebeat.app.AppContainer
import org.homebeat.app.MainActivity
import org.homebeat.app.connect.ConnectMessage
import org.homebeat.app.connect.PlaybackState
import org.homebeat.app.container
import org.homebeat.app.data.api.Track
import org.homebeat.app.eq.BassShelfProcessor
import kotlin.math.abs
import org.homebeat.app.eq.EqAudioProcessor
import org.homebeat.app.eq.KaraokeProcessor
import org.homebeat.app.spoken.SpokenItem
import org.homebeat.app.spoken.SpokenMedia

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
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main + org.homebeat.app.diag.CrashLog.handler)
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
    private lateinit var djVoice: DjVoice
    /** The song that played before the current one (the DJ back-announces it). */
    private var djLastId: String? = null
    /** True while this service paused at the end of a track for the sleep timer. */
    private var sleepPausedAtEnd = false
    /** Wake-up alarm (started by [org.homebeat.app.alarm.AlarmReceiver] through [onStartCommand]). */
    private lateinit var alarm: org.homebeat.app.alarm.AlarmRun

    // ---- resume where you left off ----
    private lateinit var resume: ResumeStore
    /** savedAt of the local snapshot restored at start (null = nothing restored). */
    private var restoredSavedAt: Long? = null
    private var restoreJob: kotlinx.coroutines.Job? = null
    private var lastResumeSaveMs = 0L

    // ---- podcasts / audiobooks / stations in this player ----
    /** The current item is spoken: music-only behaviour (levelling, crossfade, DJ, queue mirror) is off. */
    private var spokenMode = false
    /** Music speed to go back to after a spoken item (which uses its own per-show/book speed). */
    private var musicSpeed = 1f
    private var spokenSpeedFor: String? = null
    /** Skip silence chosen while this item plays (overrides the item's saved setting). */
    private val skipSilenceOverride = mutableMapOf<String, Boolean>()
    private var lastSpokenSaveMs = 0L
    private var lastSpokenFlushMs = 0L
    /** When this phone claimed playback itself (play pressed here): the takeover that answers is not a load. */
    private var claimedAtMs = 0L

    override fun onCreate() {
        super.onCreate()
        c = container
        val http = OkHttpDataSource.Factory(c.callFactory).setUserAgent("homebeat-android")
        val dataSources = DefaultDataSource.Factory(this, http)
        // Each player gets its own EQ processor (they keep filter state); both read the same settings.
        // ... and its own low shelf for the DJ automix bass swap.
        fun renderers(bass: BassShelfProcessor) = object : DefaultRenderersFactory(this) {
            override fun buildAudioSink(context: Context, enableFloatOutput: Boolean, enableAudioTrackPlaybackParams: Boolean): AudioSink =
                DefaultAudioSink.Builder(context)
                    .setAudioProcessors(arrayOf(KaraokeProcessor { c.eq.karaoke.value }, EqAudioProcessor { c.eq.active.value }, bass))
                    .setEnableFloatOutput(enableFloatOutput)
                    .setEnableAudioTrackPlaybackParams(enableAudioTrackPlaybackParams)
                    .build()
        }
        val attributes = AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MUSIC).build()

        // Gapless: ExoPlayer plays consecutive items through one AudioTrack and trims encoder delay/padding
        // (Opus pre-skip; FLAC has none), as long as the sample rate/channels stay the same.
        player = ExoPlayer.Builder(this, renderers(mainBass))
            .setMediaSourceFactory(DefaultMediaSourceFactory(dataSources))
            .setAudioAttributes(attributes, /* handleAudioFocus = */ true)
            .setHandleAudioBecomingNoisy(true)
            .setWakeMode(C.WAKE_MODE_NETWORK)
            .build()
        mixer = VolumeMixer { player.volume = it }
        crossfader = Crossfader {
            faderBass = BassShelfProcessor()
            ExoPlayer.Builder(this, renderers(faderBass))
                .setMediaSourceFactory(DefaultMediaSourceFactory(dataSources))
                .setAudioAttributes(attributes, /* handleAudioFocus = */ false)
                .build()
        }
        val speech = AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_SPEECH).build()
        djVoice = DjVoice(
            scope, { c.api },
            buildPlayer = {
                ExoPlayer.Builder(this).setMediaSourceFactory(DefaultMediaSourceFactory(dataSources))
                    .setAudioAttributes(speech, /* handleAudioFocus = */ false).build()
            },
            duck = { mixer.duck = it },
            userVolume = { mixer.user },
        )
        scope.launch { DjVoiceState.refresh(c.api) }
        library = AutoLibrary(
            api = { c.api },
            hints = { AiRadio.context(this) },
            downloads = {
                c.db.downloads().all().filter { it.complete }.mapNotNull { c.library.decodeTrack(it.trackJson) }
                    .sortedWith(compareBy({ it.artist.lowercase() }, { it.album.lowercase() }, { it.discNo ?: 0 }, { it.trackNo ?: 0 }))
            },
            paired = { c.secureStore.config.value != null },
        )

        tracker = FeedbackTracker(player, c.events, c.contextCollector).also { it.attach() }
        player.addListener(object : Player.Listener {
            override fun onEvents(p: Player, events: Player.Events) = onPlayerEvents(events)
            override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
                djVoice.stop()
                val prevId = djLastId
                djLastId = mediaItem?.mediaId
                // Family mode: the server flags tracks already in a queue that this user may not hear.
                if (mediaItem?.mediaMetadata?.extras?.getBoolean(MediaItems.EXTRA_FAMILY_HIDDEN) == true) {
                    if (player.hasNextMediaItem()) player.seekToNextMediaItem() else player.pause()
                    return
                }
                applyItemMode()
                applyLevelling()
                updateNowFormat()
                refreshButtons()
                grantNowPlayingArt(mediaItem)
                loadArtworkData(player.currentMediaItemIndex)
                loadArtworkData(player.currentMediaItemIndex + 1)
                c.abRepeat.clear()
                // Radio DJ: talk over songs that start on their own, and over the first song of a radio.
                // (A crossfade / DJ automix moves on with a seek of its own: that counts as automatic too.)
                val auto = reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO ||
                    (reason == Player.MEDIA_ITEM_TRANSITION_REASON_SEEK && crossfadeSeeking)
                val started = reason == Player.MEDIA_ITEM_TRANSITION_REASON_PLAYLIST_CHANGED &&
                    player.currentMediaItemIndex == 0 && player.playWhenReady && player.currentPosition < 2_000
                if (auto || started) djSpot(player.currentMediaItemIndex, if (auto) prevId else null)?.let { djVoice.announce(it) }
            }
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                // Pressing play on this phone makes it the Connect active device (§5 rule 1).
                if (isPlaying && !applyingRemote && !c.connect.isActiveHere) {
                    claimedAtMs = System.currentTimeMillis()
                    c.connect.claimPlayback()
                }
                // A user pause stops a fade; buffering (e.g. the seek into the next song's mix-in) doesn't.
                if (!isPlaying && !player.playWhenReady) { cancelCrossfade(); djVoice.stop() }
                if (isPlaying) { handler.removeCallbacks(fastTick); handler.post(fastTick) }
                if (!isPlaying) { saveResume(); saveSpoken(flush = true) }
            }
            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_READY) errorRetries = 0
                if (state == Player.STATE_ENDED) currentSpoken()?.takeIf { !it.live }?.let { sp ->
                    c.spoken.progress.record(org.homebeat.app.data.api.ProgressWrite(sp.kind, sp.id, positionS = sp.durationS, durationS = sp.durationS, played = true), flush = true)
                }
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
                onSpokenLeave(old, new, reason)
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
            .setMediaButtonPreferences(buttons())
            .build()

        // Like button: follows favourites toggled anywhere (app, widget, notification).
        scope.launch { org.homebeat.app.widget.WidgetHub.starChanges.collect { refreshButtons() } }
        scope.launch { c.listening.settings.collect { applyLevelling() } }
        scope.launch {
            c.sleep.timer.collect { t ->
                refreshButtons()
                if (t == null) { mixer.sleep = 1f; if (sleepPausedAtEnd) { sleepPausedAtEnd = false; player.pauseAtEndOfMediaItems = false } }
            }
        }
        scope.launch { c.connect.events.collect { onConnect(it) } }
        // Streaming quality: a changed setting or network applies from the next song (queued stream URLs are
        // re-pointed; the playing song keeps its stream so there's no gap).
        scope.launch {
            var last: StreamSpec? = null
            kotlinx.coroutines.flow.combine(c.router.route, c.router.unmetered, c.settings.settings) { r, _, _ -> r }
                .collect { r ->
                    if (r == org.homebeat.app.net.Route.OFFLINE) return@collect
                    val spec = currentSpec()
                    if (last != null && spec != last) repointQueuedStreams(spec)
                    last = spec
                }
        }
        // Android Auto keeps showing a folder that failed to load; ask it to reload once we're connected again.
        scope.launch {
            kotlinx.coroutines.flow.combine(c.router.route, c.secureStore.config) { r, cfg -> r to (cfg != null) }
                .distinctUntilChanged().collect { reloadBrowseTree() }
        }
        alarm = org.homebeat.app.alarm.AlarmRun(this, c, player, mixer, scope, alarmHost)
        handler.post(tick)
        resume = ResumeStore(java.io.File(filesDir, "resume"))
        restoreLocal()
        scope.launch { adoptServerStateIfNewer() }
    }

    // ---- Resume exactly where you left off (app killed, swiped away, phone restarted) ----

    /** Put the last queue back at the same track and position, paused (not prepared: no network until play). */
    private fun restoreLocal() {
        if (player.mediaItemCount > 0) return
        restoreJob = scope.launch {
            val (q, pos) = withContext(Dispatchers.IO) { resume.load() } ?: return@launch
            val items = withContext(Dispatchers.IO) { snapshotItems(q) }
            val at = ResumeLogic.restorePoint(items.map { it.mediaId }, pos) ?: return@launch
            if (player.mediaItemCount > 0) return@launch // something started playing meanwhile
            applyingRemote = true
            try {
                player.setMediaItems(items, at.first, at.second)
                player.shuffleModeEnabled = pos?.shuffle ?: false
                player.repeatMode = pos?.repeat ?: Player.REPEAT_MODE_OFF
                player.playWhenReady = false
            } finally {
                applyingRemote = false
            }
            c.queueSync.setCurrent(if (q.spoken == null) q.queueId ?: pos?.queueId else null)
            restoredSavedAt = pos?.savedAt
            android.util.Log.i(org.homebeat.app.diag.CrashLog.TAG, "Restored ${items.size} items at ${at.first}/${at.second} ms")
        }
    }

    private suspend fun snapshotItems(q: ResumeQueue): List<MediaItem> {
        val sp = q.spoken
        return if (sp != null) SpokenMedia.mediaItems(sp, c.spoken.downloads::file)
        else q.entries.map { e ->
            resolve(MediaItems.fromTrack(e.track, PlaySource.entries.firstOrNull { it.wire == e.source } ?: PlaySource.LIBRARY, e.mixId))
        }
    }

    /**
     * Another device played something more recently than this phone's snapshot (e.g. the web player, then the
     * phone opens): show that here, paused at its position, so the phone picks up where you actually stopped.
     */
    private suspend fun adoptServerStateIfNewer() {
        restoreJob?.join()
        val s = kotlinx.coroutines.withTimeoutOrNull(20_000) { c.connect.state.first { it != null } } ?: return
        if (c.connect.isRemote || player.playWhenReady || ext != null) return
        if (!ResumeLogic.preferServer(restoredSavedAt ?: resume.savedAt(), s, c.connect.myDeviceId.value)) return
        applyingRemote = true
        try {
            val m = s.media
            if (m != null) {
                val (item, _) = SpokenMedia.load(c.spokenApi, m.kind, m.id) ?: return
                loadSpoken(item, s.positionMs / 1000.0, play = false)
            } else {
                val qid = s.queueId ?: return
                val q = c.queueSync.load(qid) ?: return
                if (q.tracks.isEmpty() || player.playWhenReady) return
                val items = withContext(Dispatchers.IO) { q.tracks.map { resolve(MediaItems.fromTrack(it, PlaySource.LIBRARY)) } }
                val idx = s.trackId?.let { id -> q.tracks.indexOfFirst { it.id == id }.takeIf { it >= 0 } } ?: s.index
                player.setMediaItems(items, idx.coerceIn(0, items.size - 1), s.positionMs.coerceAtLeast(0))
                player.shuffleModeEnabled = s.shuffle
                player.repeatMode = repeatMode(s.repeat)
                player.playWhenReady = false
                c.queueSync.setCurrent(qid)
            }
        } finally {
            applyingRemote = false
        }
        android.util.Log.i(org.homebeat.app.diag.CrashLog.TAG, "Picked up the newer state from another device")
    }

    /** Write where playback is (small, often). The queue itself is written when it changes ([saveResumeQueue]). */
    private fun saveResume() {
        if (player.mediaItemCount == 0) return
        lastResumeSaveMs = System.currentTimeMillis()
        val p = ResumePosition(
            index = player.currentMediaItemIndex,
            positionMs = player.currentPosition,
            itemId = player.currentMediaItem?.mediaId,
            shuffle = player.shuffleModeEnabled,
            repeat = player.repeatMode,
            savedAt = lastResumeSaveMs,
            queueId = c.queueSync.currentId.value,
        )
        resume.savePosition(p)
    }

    private val saveQueueSoon = Runnable { saveResumeQueue() }

    private fun saveResumeQueue() {
        if (restoreJob?.isActive == true) return // don't overwrite the snapshot with the empty start-up player
        val items = (0 until player.mediaItemCount).map { player.getMediaItemAt(it) }
        val sp = items.firstOrNull()?.let(SpokenMedia::item)
        val q = if (sp != null) ResumeQueue(spoken = sp)
        else ResumeQueue(
            entries = items.filterNot(SpokenMedia::isSpoken).map { mi ->
                val x = mi.mediaMetadata.extras
                ResumeEntry(MediaItems.toTrack(mi), x?.getString(MediaItems.EXTRA_SOURCE) ?: PlaySource.LIBRARY.wire, x?.getString(MediaItems.EXTRA_MIX_ID))
            },
            queueId = c.queueSync.currentId.value,
        )
        val pos = items.takeIf { it.isNotEmpty() }?.let {
            ResumePosition(player.currentMediaItemIndex, player.currentPosition, player.currentMediaItem?.mediaId,
                player.shuffleModeEnabled, player.repeatMode, System.currentTimeMillis(), c.queueSync.currentId.value)
        }
        scope.launch(Dispatchers.IO) {
            if (items.isEmpty()) resume.clear() else { resume.saveQueue(q); pos?.let(resume::savePosition) }
        }
    }

    /** A Continue listening entry as player items at its saved spot (queue: track + position; spoken: position). */
    private suspend fun resumeItem(type: String, id: String): MediaSession.MediaItemsWithStartPosition? {
        if (type == "queue") {
            val q = c.queueSync.load(id) ?: return null
            if (q.tracks.isEmpty()) return null
            val items = withContext(Dispatchers.IO) { q.tracks.map { resolve(MediaItems.fromTrack(it, PlaySource.LIBRARY)) } }
            c.queueSync.setCurrent(id)
            return MediaSession.MediaItemsWithStartPosition(items, q.currentIndex.coerceIn(0, items.size - 1), q.positionMs)
        }
        val (item, pos) = SpokenMedia.load(c.spokenApi, type, id) ?: return null
        val posS = c.spoken.progress.pending(type, id)?.let { w -> if (w.played == true) 0.0 else w.positionS } ?: pos
        val (idx, off) = SpokenMedia.startAt(item, posS)
        c.queueSync.setCurrent(null)
        return MediaSession.MediaItemsWithStartPosition(SpokenMedia.mediaItems(item, c.spoken.downloads::file), idx, off)
    }

    // ---- Podcasts / audiobooks / stations ----

    private fun currentSpoken(): SpokenItem? = SpokenMedia.item(player.currentMediaItem)

    /** Book-wide position of the current spoken item, in seconds. */
    private fun spokenPositionS(item: SpokenItem): Double =
        SpokenMedia.bookPositionS(item, SpokenMedia.part(player.currentMediaItem), player.currentPosition)

    /** Save the resume point of the spoken item playing (outbox; sent now when [flush]). */
    private fun saveSpoken(flush: Boolean) {
        val sp = currentSpoken() ?: return
        if (sp.live || player.playbackState == Player.STATE_IDLE) return
        lastSpokenSaveMs = System.currentTimeMillis()
        c.spoken.progress.position(sp.copy(speed = player.playbackParameters.speed), spokenPositionS(sp), flush)
        if (flush) lastSpokenFlushMs = lastSpokenSaveMs
    }

    /** Speed, skip silence and audio type follow the item: spoken items have their own, music gets its own back. */
    private fun applyItemMode() {
        val sp = currentSpoken()
        if (sp != null) {
            if (!spokenMode) { musicSpeed = userSpeed(); cancelCrossfade(); cancelDj() }
            if (spokenSpeedFor != sp.id) {
                spokenSpeedFor = sp.id
                player.playbackParameters = PlaybackParameters(sp.speed.coerceIn(0.25f, 4f), 1f)
            }
            player.skipSilenceEnabled = skipSilenceOverride[sp.id] ?: sp.skipSilence
            if (!spokenMode) player.setAudioAttributes(audioAttributes(speech = true), true)
            spokenMode = true
        } else if (spokenMode) {
            spokenMode = false
            spokenSpeedFor = null
            player.skipSilenceEnabled = false
            player.playbackParameters = PlaybackParameters(musicSpeed, 1f)
            player.setAudioAttributes(audioAttributes(speech = false), true)
        }
    }

    private fun audioAttributes(speech: Boolean) = AudioAttributes.Builder().setUsage(C.USAGE_MEDIA)
        .setContentType(if (speech) C.AUDIO_CONTENT_TYPE_SPEECH else C.AUDIO_CONTENT_TYPE_MUSIC).build()

    /** Load a spoken item at book-wide second [posS] (Connect playSpoken / takeover / picking up another device). */
    private fun loadSpoken(item: SpokenItem, posS: Double, play: Boolean) {
        val items = SpokenMedia.mediaItems(item, c.spoken.downloads::file)
        val (idx, off) = SpokenMedia.startAt(item, posS)
        c.queueSync.setCurrent(null)
        player.setMediaItems(items, idx.coerceIn(0, items.size - 1), off)
        player.prepare()
        if (play) player.play() else player.pause()
    }

    /** Seek a spoken item by book-wide milliseconds (a remote's seek). */
    private fun seekSpoken(item: SpokenItem, ms: Long) {
        val (idx, off) = SpokenMedia.startAt(item, ms.coerceAtLeast(0) / 1000.0)
        if (item.kind == "book" && idx in 0 until player.mediaItemCount) player.seekTo(idx, off) else player.seekTo(off)
    }

    /** Leaving a spoken item (or a part of a book): save where it was; played to its end = played. */
    private fun onSpokenLeave(old: Player.PositionInfo, new: Player.PositionInfo, reason: Int) {
        val oldItem = old.mediaItem ?: return
        val sp = SpokenMedia.item(oldItem) ?: return
        if (sp.live || oldItem.mediaId == new.mediaItem?.mediaId) return
        val sameBook = SpokenMedia.item(new.mediaItem)?.let { it.kind == sp.kind && it.id == sp.id } == true
        if (sameBook) return
        val ended = reason == Player.DISCONTINUITY_REASON_AUTO_TRANSITION
        val pos = SpokenMedia.bookPositionS(sp, SpokenMedia.part(oldItem), old.positionMs)
        c.spoken.progress.record(
            org.homebeat.app.data.api.ProgressWrite(sp.kind, sp.id, positionS = if (ended) sp.durationS ?: pos else pos,
                durationS = sp.durationS, played = if (ended) true else null),
            flush = true,
        )
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
    private val BASS_LOOKAHEAD_S = 0.2

    private fun cancelCrossfade() {
        if (crossfadeSeeking) return
        if (dj != null) cancelDj()
        crossfader.release()
        mixer.fade = 1f
    }

    // ---- DJ automix (Automix.kt has the maths) ----

    private val mainBass = BassShelfProcessor()
    private var faderBass = BassShelfProcessor()

    private class DjRun(val plan: DjPlan, val userSpeed: Float, val outVolume: Float) {
        var mixing = true
        var rampFromS = 0.0
        var lastFixMs = 0L
        var seekedToFix = false
        var setSpeed = 0f
    }

    private var dj: DjRun? = null
    /** The song whose DJ start was missed: it ends with the plain crossfade. */
    private var djMissedFor: String? = null
    private val djPlans = HashMap<String, DjPlan?>()

    /** The user's speed (DJ transitions change the player's speed for a while). */
    private fun userSpeed(): Float = dj?.userSpeed ?: player.playbackParameters.speed

    private fun setDjSpeed(run: DjRun, speed: Float) {
        run.setSpeed = speed
        player.playbackParameters = PlaybackParameters(speed, player.playbackParameters.pitch)
    }

    private fun djPlan(s: ListeningSettings, a: TrackFacts, b: TrackFacts): DjPlan? {
        val key = "${a.id}>${b.id}:${a.mix?.mixOutS}:${a.mix?.bpm}:${b.mix?.mixInS}:${b.mix?.bpm}:${s.djBeats}:${s.allowTempo}:${s.djFallbackS}"
        if (djPlans.size > 50) djPlans.clear()
        return djPlans.getOrPut(key) { ListeningRules.djPlan(s, a, b) }
    }

    /** At the outgoing mix-out point: the transient player plays the outgoing tail, the main player jumps to the
     *  incoming mix-in point at the matched tempo. */
    private fun startDj(plan: DjPlan, nextIdx: Int) {
        val run = DjRun(plan, player.playbackParameters.speed, mixer.total)
        crossfader.startManual(run.outVolume)
        faderBass.targetDb = 0.0
        mainBass.targetDb = Automix.bassIn(plan, 0.0)
        mixer.fade = 0f
        dj = run
        tracker?.markAutoTransition()
        crossfadeSeeking = true
        try {
            player.seekTo(nextIdx, Automix.ms(Automix.expectedIn(plan, crossfader.positionMs / 1000.0)))
        } finally { crossfadeSeeking = false }
        setDjSpeed(run, run.userSpeed * plan.rate.toFloat())
    }

    private fun djTick(run: DjRun) {
        // The user changed the speed meanwhile: they win.
        if (run.setSpeed != 0f && abs(player.playbackParameters.speed - run.setSpeed) > 0.001f) {
            dj = null
            crossfader.release(); mainBass.targetDb = 0.0; mixer.fade = 1f
            return
        }
        val p = run.plan
        val inPos = player.currentPosition / 1000.0
        val now = System.currentTimeMillis()
        if (run.mixing) {
            val outPos = crossfader.positionMs / 1000.0
            val x = Automix.progress(p, outPos)
            if (x >= 1.0 || !crossfader.playing && now - crossfader.startedAt > 1000) {
                crossfader.release()
                mainBass.targetDb = 0.0
                mixer.fade = 1f
                run.mixing = false
                run.rampFromS = inPos
                return
            }
            crossfader.setVolume(run.outVolume * Automix.volOut(p, x).toFloat())
            mixer.fade = Automix.volIn(p, x).toFloat()
            // the processors work ahead of what is heard by about the sink's buffer
            val xa = Automix.progress(p, outPos + BASS_LOOKAHEAD_S)
            faderBass.targetDb = Automix.bassOut(p, xa)
            mainBass.targetDb = Automix.bassIn(p, xa)
            if (p.beatmatch && player.isPlaying && now - run.lastFixMs > 400) {
                run.lastFixMs = now
                val want = Automix.expectedIn(p, outPos)
                val r = Automix.correctedRate(p.rate, inPos - want)
                if (r == null && !run.seekedToFix) {
                    run.seekedToFix = true // once: a seek re-buffers, which costs more phase than it fixes
                    crossfadeSeeking = true
                    try { player.seekTo(Automix.ms(want + 0.15)) } finally { crossfadeSeeking = false }
                } else {
                    setDjSpeed(run, run.userSpeed * (r ?: (p.rate * (if (inPos > want) 0.98 else 1.02))).toFloat())
                }
            }
            return
        }
        val after = inPos - run.rampFromS
        if (!p.beatmatch || p.rate == 1.0 || after >= Automix.RAMP_BEATS * p.inBeatS) {
            setDjSpeed(run, run.userSpeed)
            dj = null
        } else if (now - run.lastFixMs > 250) {
            run.lastFixMs = now
            setDjSpeed(run, run.userSpeed * Automix.rampRate(p, after).toFloat())
        }
    }

    /** Abort (pause, seek, skip): the incoming song plays on normally. */
    private fun cancelDj() {
        val run = dj ?: return
        dj = null
        crossfader.release()
        mainBass.targetDb = 0.0
        mixer.fade = 1f
        player.playbackParameters = PlaybackParameters(run.userSpeed, player.playbackParameters.pitch)
    }

    /** 20 Hz while playing: crossfade timing and the sleep timer's fades. */
    private val fastTick = object : Runnable {
        override fun run() {
            runCatching { listeningTick() }.onFailure { android.util.Log.w(org.homebeat.app.diag.CrashLog.TAG, "listening tick", it) }
            if (player.isPlaying || crossfader.active) handler.postDelayed(this, 50)
        }
    }

    private fun listeningTick() {
        dj?.let { run ->
            djTick(run)
            if (run.mixing && dj === run) return
        }
        crossfader.step()?.let { x -> mixer.fade = ListeningRules.fadeIn(x).toFloat() } ?: run { if (mixer.fade != 1f && !crossfader.active && dj == null) mixer.fade = 1f }
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

        // Crossfade (never for podcasts / audiobooks / stations)
        if (next == null || crossfader.active || player.mediaItemCount < 2) return
        if (SpokenMedia.isSpoken(cur) || SpokenMedia.isSpoken(next)) return
        if (c.abRepeat.loop.value != null) return
        val settings = c.listening.settings.value
        // DJ automix: from the outgoing mix-out point (sleep timer: plain transitions only)
        val fa = MediaItems.facts(cur)
        val fb = MediaItems.facts(next)
        if (dj == null && sleep == null && fa != null && fb != null && djMissedFor != cur.mediaId && ListeningRules.djFor(settings, fa, fb)) {
            val plan = djPlan(settings, fa, fb)
            if (plan != null) {
                val startAt = Automix.ms(plan.outStartS)
                if (pos >= startAt - 5_000 && pos < startAt) crossfader.prepare(cur, startAt, player.playbackParameters.speed)
                if (pos < startAt) return
                if (pos < startAt + 600) {
                    if (crossfader.isReadyFor(cur.mediaId)) startDj(plan, nextIdx)
                    return
                }
                // missed it (buffering, a seek past it): the plain crossfade below, from a fresh fader
                djMissedFor = cur.mediaId
                crossfader.release()
                return
            }
        }
        val fadeMs = ListeningRules.crossfadeMs(settings, fa, fb)
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

    /** Notification / lock screen buttons: Like (music only) next to the transport controls, sleep timer in the overflow. */
    private fun buttons(): List<CommandButton> {
        val sleep = sleepButton(c.sleep.timer.value != null)
        val id = if (spokenMode) null else player.currentMediaItem?.mediaId
        return if (id == null) listOf(sleep) else listOf(likeButton(isStarred(id)), sleep)
    }

    private fun refreshButtons() {
        session?.setMediaButtonPreferences(buttons())
    }

    private fun isStarred(id: String): Boolean =
        org.homebeat.app.widget.WidgetHub.starOverride(id)
            ?: player.currentMediaItem?.takeIf { it.mediaId == id }?.mediaMetadata?.extras?.getBoolean(MediaItems.EXTRA_STARRED)
            ?: false

    private fun likeButton(on: Boolean): CommandButton =
        CommandButton.Builder(if (on) CommandButton.ICON_HEART_FILLED else CommandButton.ICON_HEART_UNFILLED)
            .setCustomIconResId(if (on) org.homebeat.app.R.drawable.ic_w_heart else org.homebeat.app.R.drawable.ic_w_heart_outline)
            .setDisplayName(if (on) "Remove from favourites" else "Favourite")
            .setSessionCommand(SessionCommand(CMD_LIKE, android.os.Bundle.EMPTY))
            .setSlots(CommandButton.SLOT_FORWARD_SECONDARY, CommandButton.SLOT_OVERFLOW)
            .build()

    /** Like / unlike the playing song from the notification, lock screen or a watch. */
    private fun toggleLike() {
        if (spokenMode) return
        val id = player.currentMediaItem?.mediaId ?: return
        val was = isStarred(id)
        val star = !was
        org.homebeat.app.widget.WidgetHub.setStarred(id, star)
        c.events.record(if (star) org.homebeat.app.feedback.EventType.STAR else org.homebeat.app.feedback.EventType.UNSTAR, id, context = c.contextCollector.snapshot())
        scope.launch(Dispatchers.IO) {
            val ok = runCatching { if (star) c.api.star(id) else c.api.unstar(id) }.getOrNull()?.isSuccessful == true
            if (!ok) org.homebeat.app.widget.WidgetHub.setStarred(id, was)
        }
    }

    private fun sleepButton(on: Boolean): CommandButton =
        CommandButton.Builder(CommandButton.ICON_UNDEFINED)
            .setCustomIconResId(org.homebeat.app.R.drawable.ic_sleep_timer)
            .setDisplayName(if (on) "Sleep timer on (tap to turn off)" else "Sleep timer: 30 min")
            .setSessionCommand(SessionCommand(CMD_SLEEP, android.os.Bundle.EMPTY))
            .setSlots(CommandButton.SLOT_OVERFLOW)
            .build()

    // ---- MediaSession / library callback (app, notification, Bluetooth, watch, Android Auto) ----

    private inner class LibraryCallback : MediaLibrarySession.Callback {
        override fun onConnect(session: MediaSession, controller: MediaSession.ControllerInfo): MediaSession.ConnectionResult {
            val cmds = MediaSession.ConnectionResult.DEFAULT_SESSION_AND_LIBRARY_COMMANDS.buildUpon()
                .add(SessionCommand(CMD_SLEEP, android.os.Bundle.EMPTY))
                .add(SessionCommand(CMD_LIKE, android.os.Bundle.EMPTY))
                .add(SessionCommand(CMD_SKIP_SILENCE, android.os.Bundle.EMPTY)).build()
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
            if (customCommand.customAction == CMD_LIKE) {
                toggleLike()
                return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS))
            }
            if (customCommand.customAction == CMD_SKIP_SILENCE) {
                val on = args.getBoolean(ARG_ON, false)
                currentSpoken()?.let { skipSilenceOverride[it.id] = on }
                player.skipSilenceEnabled = on && spokenMode
                return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS))
            }
            return Futures.immediateFuture(SessionResult(SessionError.ERROR_NOT_SUPPORTED))
        }

        /**
         * System playback resumption (a Bluetooth/headset play press or the "resume" media card after a restart,
         * with nothing loaded): the saved queue at its track and position.
         */
        override fun onPlaybackResumption(
            mediaSession: MediaSession, controller: MediaSession.ControllerInfo,
        ): ListenableFuture<MediaSession.MediaItemsWithStartPosition> = scope.future {
            restoreJob?.join()
            val (q, pos) = withContext(Dispatchers.IO) { resume.load() } ?: throw UnsupportedOperationException("nothing to resume")
            val items = withContext(Dispatchers.IO) { snapshotItems(q) }
            val at = ResumeLogic.restorePoint(items.map { it.mediaId }, pos) ?: throw UnsupportedOperationException("nothing to resume")
            c.queueSync.setCurrent(if (q.spoken == null) q.queueId ?: pos?.queueId else null)
            MediaSession.MediaItemsWithStartPosition(items, at.first, at.second)
        }

        override fun onAddMediaItems(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
            mediaItems: MutableList<MediaItem>,
        ): ListenableFuture<MutableList<MediaItem>> = scope.future(Dispatchers.IO) {
            mediaItems.flatMap { item ->
                val e = runCatching { library.expand(item.mediaId) }.getOrNull()
                if (e != null) listOfNotNull(e.tracks.getOrNull(e.startIndex)).map { resolve(MediaItems.fromTrack(it, e.source, e.mixId)) }
                else listOf(resolve(item.buildUpon().setMediaId(AutoLibrary.trackIdOf(item.mediaId)).build()))
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
            // Android Auto "Continue listening": resume that queue / episode / book where it stopped.
            single?.mediaId?.let(AutoLibrary::parseContinueId)?.let { (type, id) -> resumeItem(type, id)?.let { return@future it } }
            val query = single?.requestMetadata?.searchQuery
            val e = when {
                single == null -> null
                SpokenMedia.isSpoken(single) -> null
                single.mediaId.isEmpty() && query != null -> library.voice(query)
                else -> runCatching { library.expand(single.mediaId) }.getOrNull()
            }
            if (e != null && e.tracks.isNotEmpty()) {
                val items = withContext(Dispatchers.IO) { e.tracks.map { resolve(MediaItems.fromTrack(it, e.source, e.mixId)) } }
                when {
                    e.radioQueueId != null -> { c.queueSync.markRadio(e.radioQueueId); c.queueSync.setCurrent(e.radioQueueId); c.queueSync.refresh() }
                    e.queueId != null -> c.queueSync.setCurrent(e.queueId)
                    else -> c.queueSync.created(e.queueName, e.tracks, e.startIndex)
                }
                MediaSession.MediaItemsWithStartPosition(items, e.startIndex, e.startPositionMs)
            } else {
                // A browse id whose context couldn't be loaded (offline): still play that one song.
                val items = withContext(Dispatchers.IO) {
                    mediaItems.map { resolve(it.buildUpon().setMediaId(AutoLibrary.trackIdOf(it.mediaId)).build()) }
                }
                MediaSession.MediaItemsWithStartPosition(items, startIndex, startPositionMs)
            }
        }

        override fun onGetLibraryRoot(
            session: MediaLibrarySession, browser: MediaSession.ControllerInfo, params: LibraryParams?,
        ): ListenableFuture<LibraryResult<MediaItem>> {
            val out = library.onRoot(browserKey(browser), params)
            val root = if (params?.isRecent == true) library.recentRoot() else library.root()
            return Futures.immediateFuture(LibraryResult.ofItem(root, out))
        }

        override fun onGetItem(
            session: MediaLibrarySession, browser: MediaSession.ControllerInfo, mediaId: String,
        ): ListenableFuture<LibraryResult<MediaItem>> = scope.future {
            runCatching { library.item(mediaId) }.getOrNull()
                ?.let { grantArt(browser, listOf(it)); LibraryResult.ofItem(it, null) }
                ?: LibraryResult.ofError(SessionError.ERROR_BAD_VALUE)
        }

        override fun onGetChildren(
            session: MediaLibrarySession, browser: MediaSession.ControllerInfo, parentId: String, page: Int, pageSize: Int, params: LibraryParams?,
        ): ListenableFuture<LibraryResult<ImmutableList<MediaItem>>> = scope.future {
            val all = runCatching {
                if (parentId == AutoLibrary.RECENT_ROOT) library.recentChildren() else library.children(parentId, browserKey(browser))
            }.getOrElse { e ->
                android.util.Log.w("PlaybackService", "browse $parentId failed", e)
                emptyList()
            }
            val items = paged(all, page, pageSize)
            grantArt(browser, items)
            LibraryResult.ofItemList(items, params)
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
            val items = paged(runCatching { library.search(query) }.getOrDefault(emptyList()), page, pageSize)
            grantArt(browser, items)
            LibraryResult.ofItemList(items, params)
        }
    }

    private fun browserKey(browser: MediaSession.ControllerInfo) = browser.packageName

    /** Media browsers (Android Auto, Automotive, watches) load art in their own process: let them read it. */
    private fun grantArt(browser: MediaSession.ControllerInfo, items: List<MediaItem>) =
        ArtProvider.grant(this, browser.packageName, items.map { it.mediaMetadata.artworkUri })

    /** Now-playing art for System UI / Android Auto and any connected controller (the URI is in the session metadata). */
    private fun grantNowPlayingArt(item: MediaItem?) {
        val uri = item?.mediaMetadata?.artworkUri ?: return
        val pkgs = ArtProvider.SYSTEM_READERS + (session?.connectedControllers?.map { it.packageName } ?: emptyList())
        pkgs.distinct().forEach { ArtProvider.grant(this, it, listOf(uri)) }
    }

    /** After the server became reachable (or was paired), refresh the root and every folder that failed. */
    private fun reloadBrowseTree() {
        val s = session ?: return
        val ids = library.failed.toList() + listOf(AutoLibrary.ROOT, AutoLibrary.FOR_YOU, AutoLibrary.CONTINUE)
        library.failed.clear()
        ids.distinct().forEach { runCatching { s.notifyChildrenChanged(it, 1, null) } }
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
            maybePrefetchDj()
            if (player.isPlaying && now - lastProgressMs > 15_000) {
                lastProgressMs = now
                if (!spokenMode) c.queueSync.progress(player.currentMediaItemIndex, player.currentPosition)
            }
            if (player.isPlaying && now - lastResumeSaveMs > 5_000) saveResume()
            if (player.isPlaying && spokenMode && now - lastSpokenSaveMs > 5_000) saveSpoken(flush = now - lastSpokenFlushMs > 30_000)
            handler.postDelayed(this, 250)
        }
    }

    /** Where the DJ might talk: item [index] of a radio/mix queue that this phone plays itself. */
    private fun djSpot(index: Int, prevId: String?): DjVoice.Spot? {
        if (spokenMode || ext != null || !DjVoiceState.on || index !in 0 until player.mediaItemCount) return null
        val item = player.getMediaItemAt(index)
        val x = item.mediaMetadata.extras
        if (x?.getBoolean(MediaItems.EXTRA_FAMILY_HIDDEN) == true) return null
        val qid = c.queueSync.currentId.value
        if (!DjLogic.eligible(c.queueSync.isRadio(qid), x?.getString(MediaItems.EXTRA_SOURCE))) return null
        return DjVoice.Spot(qid, index, item.mediaId, prevId)
    }

    /** ~25 s before the next song, ask the server for its intro so it's ready on time. */
    private fun maybePrefetchDj() {
        if (!player.isPlaying || !DjVoiceState.on) return
        val dur = player.duration
        if (dur <= 0 || dur - player.currentPosition > DjLogic.PREFETCH_MS) return
        val next = player.nextMediaItemIndex
        if (next == C.INDEX_UNSET || next == player.currentMediaItemIndex) return
        djSpot(next, player.currentMediaItem?.mediaId)?.let { djVoice.prefetch(it) }
    }

    private var errorRetries = 0

    /** One "offline" message per offline spell, not one per song that can't be streamed. */
    private var offlineNoticeShown = false

    private fun recover(error: androidx.media3.common.PlaybackException) {
        android.util.Log.w(org.homebeat.app.diag.CrashLog.TAG, "Playback error ${error.errorCodeName} (retry $errorRetries)", error)
        if (c.router.route.value == org.homebeat.app.net.Route.OFFLINE) {
            // No connection: don't retry streams, jump to the next downloaded song instead.
            val ids = (0 until player.mediaItemCount).map { player.getMediaItemAt(it).mediaId }
            val next = OfflinePlayback.nextPlayable(ids, player.currentMediaItemIndex, c.offline.downloadedIds.value)
            if (!offlineNoticeShown) {
                offlineNoticeShown = true
                c.ui.toast(if (next != null) "Offline: skipping songs that aren't downloaded" else "You're offline and the rest of this queue isn't downloaded")
            }
            if (next != null) { player.seekTo(next, 0); player.prepare(); player.play() }
            scope.launch {
                // Back online meanwhile? Retry the current song as normal.
                if (next == null && c.router.refresh() != org.homebeat.app.net.Route.OFFLINE) player.prepare()
            }
            return
        }
        offlineNoticeShown = false
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
                val resp = if (c.router.route.value == org.homebeat.app.net.Route.OFFLINE) null else runCatching { c.api.radioMore(qid, org.homebeat.app.data.api.RadioMoreRequest(automix = c.listening.settings.value.djOrdering.takeIf { it })) }.getOrNull()
                val fresh = if (resp != null) RadioLogic.newTracks(resp, localIds, org.homebeat.app.data.api.ApiFactory.json)
                else if (c.router.route.value == org.homebeat.app.net.Route.OFFLINE) {
                    // Offline: keep the radio going with downloaded songs it hasn't played yet.
                    val held = localIds.toSet()
                    OfflinePlayback.spreadArtists(c.offline.offlineTracks.value.filter { it.id !in held }.shuffled()).take(15)
                } else return@launch
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
                Player.EVENT_IS_PLAYING_CHANGED, Player.EVENT_MEDIA_ITEM_TRANSITION, Player.EVENT_TIMELINE_CHANGED,
                Player.EVENT_MEDIA_METADATA_CHANGED, Player.EVENT_AVAILABLE_COMMANDS_CHANGED,
            )
        ) {
            publishWidgetState()
        }
        if (events.containsAny(
                Player.EVENT_IS_PLAYING_CHANGED, Player.EVENT_MEDIA_ITEM_TRANSITION, Player.EVENT_POSITION_DISCONTINUITY,
                Player.EVENT_SHUFFLE_MODE_ENABLED_CHANGED, Player.EVENT_REPEAT_MODE_CHANGED, Player.EVENT_VOLUME_CHANGED,
                Player.EVENT_PLAYBACK_PARAMETERS_CHANGED,
            )
        ) {
            sendState()
        }
        if (events.containsAny(Player.EVENT_MEDIA_ITEM_TRANSITION, Player.EVENT_IS_PLAYING_CHANGED) && !player.isPlaying && !spokenMode) {
            c.queueSync.progress(player.currentMediaItemIndex, player.currentPosition)
        }
        if (events.contains(Player.EVENT_TIMELINE_CHANGED)) {
            handler.removeCallbacks(saveQueueSoon)
            handler.postDelayed(saveQueueSoon, 400)
        }
        if (events.containsAny(
                Player.EVENT_MEDIA_ITEM_TRANSITION, Player.EVENT_SHUFFLE_MODE_ENABLED_CHANGED, Player.EVENT_REPEAT_MODE_CHANGED,
                Player.EVENT_POSITION_DISCONTINUITY,
            )
        ) saveResume()
    }

    /** Home-screen widgets and the quick-settings tile follow this phone's player (Cast overlay applied). */
    private fun publishWidgetState() {
        val local = NowPlaying(
            item = player.currentMediaItem,
            isPlaying = player.isPlaying,
            hasNext = player.hasNextMediaItem(),
            hasPrevious = player.hasPreviousMediaItem(),
            queue = emptyList(),
            index = player.currentMediaItemIndex,
        )
        val e = ext
        val cast = e?.overlay(local.copy(queue = (0 until player.mediaItemCount).map { player.getMediaItemAt(it) }))
        org.homebeat.app.widget.WidgetHub.publishLocal(cast?.copy(queue = emptyList()) ?: local, if (cast != null) e.name ?: "a Cast speaker" else null)
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
        val sp = currentSpoken()
        if (sp != null) {
            val part = cast?.index ?: SpokenMedia.part(player.currentMediaItem)
            val posMs = if (sp.live) 0L else ((sp.partStarts.getOrNull(part) ?: 0.0) * 1000).toLong() + (cast?.positionMs ?: player.currentPosition)
            c.connect.reportState(
                PlaybackState(
                    queueId = null, index = 0, trackId = null, positionMs = posMs, positionAt = Instant.now().toString(),
                    playing = cast?.isPlaying ?: player.isPlaying, volume = cast?.volume ?: mixer.user,
                    shuffle = false, repeat = "off", speed = player.playbackParameters.speed,
                    castTo = if (cast != null) ext?.name ?: "a Cast speaker" else null,
                    media = org.homebeat.app.connect.ConnectMedia(sp.kind, sp.id),
                ),
            )
            return
        }
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
                speed = userSpeed(),
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
                // A takeover answering our own claim: we already play what the user picked (e.g. the restored queue);
                // loading the server's older state would jump to another song or restart the list.
                is ConnectMessage.Takeover -> if (ext == null && !ResumeLogic.ownClaim(claimedAtMs, System.currentTimeMillis(), player.isPlaying)) takeover(m.state)
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
        val m = s.media
        if (m != null) {
            val cur = currentSpoken()
            val posS = s.positionAt(System.currentTimeMillis()) / 1000.0
            if (cur != null && cur.kind == m.kind && cur.id == m.id) seekSpoken(cur, (posS * 1000).toLong())
            else {
                val (item, _) = SpokenMedia.load(c.spokenApi, m.kind, m.id) ?: return
                loadSpoken(item, posS, play = false)
            }
            player.playbackParameters = PlaybackParameters(s.speed.coerceIn(0.25f, 4f), 1f)
            if (s.playing) player.play() else player.pause()
            return
        }
        val qid = s.queueId
        if (qid != null && (qid != c.queueSync.currentId.value || spokenMode)) {
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
        val sp = currentSpoken()
        if (sp != null && !sp.live) when (action) {
            // On a podcast/book: next/prev skip forward 30 s / back 15 s; seek is book-wide.
            "next" -> { seekSpoken(sp, (spokenPositionS(sp) * 1000).toLong() + 30_000); return }
            "prev" -> { seekSpoken(sp, (spokenPositionS(sp) * 1000).toLong() - 15_000); return }
            "seek" -> { long("positionMs")?.let { seekSpoken(sp, it) }; return }
        }
        when (action) {
            "play" -> {
                if (sp?.live == true) player.seekToDefaultPosition() // live radio: back to "now"
                if (player.playbackState == Player.STATE_IDLE) player.prepare()
                player.play()
            }
            "pause" -> player.pause()
            "next" -> player.seekToNext()
            "prev" -> player.seekToPrevious()
            "seek" -> long("positionMs")?.let { player.seekTo(it) }
            "setVolume" -> float("volume")?.let { mixer.user = it; tracker?.userVolumeChanged(mixer.user) }
            "setShuffle" -> a["on"]?.jsonPrimitive?.booleanOrNull?.let { player.shuffleModeEnabled = it }
            "setRepeat" -> a["mode"]?.jsonPrimitive?.contentOrNull?.let { player.repeatMode = repeatMode(it) }
            "setSpeed" -> float("speed")?.let { cancelDj(); player.playbackParameters = PlaybackParameters(it.coerceIn(0.25f, 4f), player.playbackParameters.pitch) }
            "playQueue" -> {
                val id = a["queueId"]?.jsonPrimitive?.contentOrNull ?: return
                val q = c.queueSync.load(id) ?: return
                // No index = resume where the queue was left: its saved track AND position (the server also sends
                // positionMs). An explicit index starts that track from the top.
                val index = a["index"]?.jsonPrimitive?.intOrNull
                val pos = long("positionMs") ?: if (index == null) q.positionMs else 0L
                setTracks(q.tracks, index ?: q.currentIndex, pos)
                c.queueSync.setCurrent(id)
                ext?.reload() ?: player.play()
            }
            "playSpoken" -> {
                val kind = a["kind"]?.jsonPrimitive?.contentOrNull ?: return
                val id = a["id"]?.jsonPrimitive?.contentOrNull ?: return
                val (item, saved) = SpokenMedia.load(c.spokenApi, kind, id) ?: return
                val posS = long("positionMs")?.let { it / 1000.0 } ?: saved
                loadSpoken(item, posS, play = ext == null)
                ext?.reload()
            }
            "playTracks" -> {
                val ids = a["trackIds"]?.jsonArray?.mapNotNull { it.jsonPrimitive.contentOrNull }.orEmpty()
                if (ids.isEmpty()) return
                val index = a["index"]?.jsonPrimitive?.intOrNull ?: 0
                val name = a["queueName"]?.jsonPrimitive?.contentOrNull ?: "Remote"
                val q = runCatching { c.api.createQueue(org.homebeat.app.data.api.NewQueue(name, ids, index)) }.getOrNull()
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

    /** Local file if downloaded, else the stream URL in the quality for the current network. */
    private suspend fun resolve(item: MediaItem): MediaItem {
        if (SpokenMedia.isSpoken(item)) {
            if (item.localConfiguration != null) return item
            val sp = SpokenMedia.item(item) ?: return item
            return SpokenMedia.mediaItems(sp, c.spoken.downloads::file).getOrNull(SpokenMedia.part(item)) ?: item
        }
        // A song stored on the phone plays straight from MediaStore.
        org.homebeat.app.local.LocalLibrary.contentUri(item.mediaId)?.let { return item.buildUpon().setUri(it).build() }
        val local = c.library.localPath(item.mediaId)?.let(::File)?.takeIf { it.exists() }
        val uri = local?.let(Uri::fromFile)
            ?: Uri.parse(MediaItems.streamUrl(item.mediaId, currentSpec()))
        return item.buildUpon().setUri(uri).build()
    }

    /** Streaming quality for the current network (home Wi-Fi / mobile data / remote Wi-Fi) and settings. */
    private fun currentSpec(): StreamSpec {
        val net = NetworkKind.of(c.router.route.value, c.router.unmetered.value)
        return StreamQuality.resolve(c.settings.settings.value.streamQuality(net), net)
    }

    /** Point every queued stream (not the playing item, not downloaded files) at [spec]. */
    private fun repointQueuedStreams(spec: StreamSpec) {
        val cur = player.currentMediaItemIndex
        val n = player.mediaItemCount
        val items = (0 until n).map { player.getMediaItemAt(it) }
        val updated = items.mapIndexed { i, item ->
            val uri = item.localConfiguration?.uri?.toString()
            if (i == cur || uri == null || StreamSpec.ofUrl(uri) == null) item
            else MediaItems.streamUrl(item.mediaId, spec).takeIf { it != uri }?.let { item.buildUpon().setUri(it).build() } ?: item
        }
        if (updated == items) return
        if (cur + 1 < n) player.replaceMediaItems(cur + 1, n, updated.subList(cur + 1, n))
        if (cur > 0) player.replaceMediaItems(0, cur, updated.subList(0, cur))
        android.util.Log.i(org.homebeat.app.diag.CrashLog.TAG, "Stream quality now ${spec.label}; queue re-pointed")
    }

    private val originalLabels = java.util.concurrent.ConcurrentHashMap<String, String>()

    /** The original file's format ("FLAC 16/44.1", "MP3 320") from /library/tracks/{id}/info, cached. */
    private suspend fun originalLabel(id: String): String? = originalLabels[id]
        ?: runCatching { c.api.trackInfo(id) }.getOrNull()
            ?.let { FormatLabel.original(it.codec, it.bitsPerSample, it.sampleRate, it.bitrateKbps) }
            ?.also { originalLabels[id] = it }

    /** Publish what the current item actually is (stream quality or downloaded file) for Now Playing. */
    private fun updateNowFormat() {
        val item = player.currentMediaItem
        val uri = item?.localConfiguration?.uri
        if (item == null || uri == null) { c.nowFormat.value = null; return }
        val id = item.mediaId
        scope.launch {
            val nf = if (org.homebeat.app.local.LocalIds.isLocal(id)) {
                NowFormat(id, "On this phone")
            } else if (uri.scheme == "file") {
                val d = withContext(Dispatchers.IO) { c.db.downloads().completed(id) }
                if (d != null && d.format != "flac") NowFormat(id, "Opus ${if (d.kbps > 0) d.kbps else 160}", offline = true)
                else NowFormat(id, originalLabel(id) ?: "Original file", offline = true)
            } else {
                val spec = StreamSpec.ofUrl(uri.toString()) ?: run { c.nowFormat.value = null; return@launch }
                NowFormat(id, if (spec.lossless) originalLabel(id) ?: spec.label else spec.label)
            }
            if (player.currentMediaItem?.mediaId == id) c.nowFormat.value = nf
        }
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

    // ---- Wake-up alarm ----

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            org.homebeat.app.alarm.AlarmRun.ACTION_START -> { alarm.start(); return START_NOT_STICKY }
            org.homebeat.app.alarm.AlarmRun.ACTION_SNOOZE -> { alarm.snooze(); return START_NOT_STICKY }
            org.homebeat.app.alarm.AlarmRun.ACTION_STOP -> {
                alarm.stop()
                if (!player.playWhenReady) stopSelf(startId) // only started for the button; bound clients keep it alive
                return START_NOT_STICKY
            }
        }
        return super.onStartCommand(intent, flags, startId)
    }

    private val alarmHost = object : org.homebeat.app.alarm.AlarmRun.Host {
        override fun startForeground(n: android.app.Notification): Boolean = runCatching {
            androidx.core.app.ServiceCompat.startForeground(
                this@PlaybackService, org.homebeat.app.alarm.AlarmNotifications.ID, n,
                if (android.os.Build.VERSION.SDK_INT >= 29) android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK else 0,
            )
        }.onFailure { android.util.Log.w(org.homebeat.app.diag.CrashLog.TAG, "alarm foreground", it) }.isSuccess

        override fun stopForeground() =
            androidx.core.app.ServiceCompat.stopForeground(this@PlaybackService, androidx.core.app.ServiceCompat.STOP_FOREGROUND_REMOVE)

        override fun showSession() { session?.let { if (!isSessionAdded(it)) addSession(it) } }

        override suspend fun mediaItems(tracks: List<Track>, source: PlaySource, mixId: String?): List<MediaItem> =
            tracks.map { resolve(MediaItems.fromTrack(it, source, mixId)) }

        override fun playSpoken(item: SpokenItem) = loadSpoken(item, 0.0, play = true)
    }

    companion object {
        /** Custom session command: skip silence on/off for the spoken item playing (`ARG_ON`). */
        const val CMD_SKIP_SILENCE = "org.homebeat.skip_silence"
        const val ARG_ON = "on"
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        saveResume()
        saveSpoken(flush = true)
        val p = session?.player
        if (p == null || !p.playWhenReady || p.mediaItemCount == 0) stopSelf()
    }

    override fun onDestroy() {
        c.nowFormat.value = null
        handler.removeCallbacks(tick)
        handler.removeCallbacks(fastTick)
        crossfader.release()
        djVoice.release()
        if (::resume.isInitialized) { handler.removeCallbacks(saveQueueSoon); saveResume() }
        saveSpoken(flush = true)
        if (!spokenMode) c.queueSync.progress(player.currentMediaItemIndex, player.currentPosition)
        tracker?.release()
        org.homebeat.app.widget.WidgetHub.publishLocal(null)
        session?.run {
            player.release()
            release()
        }
        session = null
        scope.cancel()
        super.onDestroy()
    }
}

private const val CMD_SLEEP = "org.homebeat.sleep_timer"
private const val CMD_LIKE = "org.homebeat.like"

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
