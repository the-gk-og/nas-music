package org.homebeat.app.alarm

import android.app.Notification
import android.app.NotificationManager
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.Ringtone
import android.media.RingtoneManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.homebeat.app.AppContainer
import org.homebeat.app.data.api.RadioRequest
import org.homebeat.app.data.api.RadioSeed
import org.homebeat.app.data.api.Track
import org.homebeat.app.net.Route
import org.homebeat.app.playback.AiRadio
import org.homebeat.app.playback.PlaySource
import org.homebeat.app.playback.VolumeMixer
import org.homebeat.app.spoken.SpokenItem
import org.homebeat.app.spoken.SpokenMedia

/**
 * One wake-up alarm going off, inside the playback service: start the chosen source (or downloads, or the
 * system alarm tone), raise the media volume to the floor, fade in through [VolumeMixer.alarm], and offer
 * Stop / Snooze in a notification. Pausing the music (anywhere: app, notification, headphones, watch) stops it.
 */
class AlarmRun(
    private val ctx: Context,
    private val c: AppContainer,
    private val player: Player,
    private val mixer: VolumeMixer,
    private val scope: CoroutineScope,
    private val host: Host,
) {
    /** What the playback service lends the alarm. */
    interface Host {
        fun startForeground(n: Notification): Boolean
        fun stopForeground()
        /** Make the session the system's media session, so the media notification shows. */
        fun showSession()
        suspend fun mediaItems(tracks: List<Track>, source: PlaySource, mixId: String?): List<MediaItem>
        fun playSpoken(item: SpokenItem)
    }

    private val handler = Handler(Looper.getMainLooper())
    private var active = false
    private var job: Job? = null
    private var playingSinceMs = 0L
    private var everPlayed = false
    private var fadeMs = 0L
    private var ownsForeground = false
    private var ringtone: Ringtone? = null
    /** True while the alarm itself changes the queue (its pause isn't a "stop"). */
    private var switching = false

    private val listener = object : Player.Listener {
        override fun onIsPlayingChanged(isPlaying: Boolean) {
            if (!isPlaying || !active) return
            if (!everPlayed) { everPlayed = true; ownsForeground = false } // Media3's notification is the foreground one now
            if (playingSinceMs == 0L) playingSinceMs = System.currentTimeMillis()
            stopRingtone()
        }
        override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
            if (!playWhenReady && active && everPlayed && !switching && reason == Player.PLAY_WHEN_READY_CHANGE_REASON_USER_REQUEST) stop()
        }
    }

    private val fadeTick = object : Runnable {
        override fun run() {
            if (!active) return
            if (playingSinceMs > 0) {
                val level = AlarmTimes.fadeLevel(System.currentTimeMillis() - playingSinceMs, fadeMs)
                mixer.alarm = level
                if (level >= 1f) return
            }
            handler.postDelayed(this, 100)
        }
    }

    val isActive: Boolean get() = active

    fun start() {
        if (active) finish(cancelNotification = false)
        val a = c.alarm.alarm.value
        active = true
        everPlayed = false
        playingSinceMs = 0L
        fadeMs = a.fadeInS.coerceIn(0, 30 * 60) * 1000L
        ownsForeground = host.startForeground(AlarmNotifications.ringing(ctx, a.source.label, a.snoozeMin))
        mixer.alarm = if (fadeMs > 0) 0f else 1f
        raiseVolume(a.minVolumePct)
        player.addListener(listener)
        handler.post(fadeTick)
        job = scope.launch {
            val ok = runCatching { withTimeoutOrNull(15_000) { playSource(a.source) } == true }.getOrDefault(false) || playDownloads()
            if (!ok) { ring(); return@launch }
            // Started but nothing audible a while later (server gone mid-load, stream errors): ring.
            delay(45_000)
            if (active && !everPlayed) ring()
        }
    }

    fun snooze() {
        val a = c.alarm.alarm.value
        val was = active
        finish(cancelNotification = false)
        if (was || player.isPlaying) player.pause()
        val at = AlarmScheduler.snooze(ctx, a.snoozeMin)
        post(AlarmNotifications.snoozed(ctx, at))
    }

    /** Stop button, a pause, or Dismiss on a snoozed alarm. */
    fun stop() {
        val wasPlayingAlarm = active
        finish(cancelNotification = true)
        AlarmScheduler.cancelSnooze(ctx)
        if (wasPlayingAlarm && player.playWhenReady) player.pause()
    }

    private fun finish(cancelNotification: Boolean) {
        active = false
        job?.cancel()
        job = null
        handler.removeCallbacks(fadeTick)
        handler.removeCallbacks(ringLimit)
        player.removeListener(listener)
        stopRingtone()
        mixer.alarm = 1f
        if (ownsForeground) { host.stopForeground(); ownsForeground = false }
        if (cancelNotification) runCatching { ctx.getSystemService(NotificationManager::class.java)?.cancel(AlarmNotifications.ID) }
    }

    private fun post(n: Notification) {
        runCatching { ctx.getSystemService(NotificationManager::class.java)?.notify(AlarmNotifications.ID, n) }
    }

    // ---- what to play ----

    private suspend fun playSource(s: AlarmSource): Boolean {
        if (c.router.route.value == Route.OFFLINE) return false
        return when (s.type) {
            "station" -> {
                val (item, _) = SpokenMedia.load(c.spokenApi, "station", s.id ?: return false) ?: return false
                host.showSession()
                switching = true
                try { host.playSpoken(item) } finally { switching = false }
                true
            }
            "mix" -> {
                val mixes = c.api.mixesToday()
                val m = mixes.firstOrNull { it.kind == s.id } ?: mixes.firstOrNull() ?: return false
                play(m.tracks.map { it.track }, PlaySource.MIX, m.id)
                c.queueSync.created(m.title, m.tracks.map { it.track }, 0)
                true
            }
            "playlist" -> {
                val p = c.api.playlist(s.id ?: return false)
                if (p.tracks.isEmpty()) return false
                play(p.tracks, PlaySource.LIBRARY, null)
                c.queueSync.created("Playlist: ${p.name}", p.tracks, 0)
                true
            }
            "moment", "random" -> {
                val hints = if (s.type == "moment") AiRadio.context(ctx) else null
                val q = c.api.startRadio(RadioRequest(RadioSeed(s.type), hints))
                if (q.tracks.isEmpty()) return false
                play(q.tracks, PlaySource.MIX, null)
                c.queueSync.markRadio(q.id)
                c.queueSync.setCurrent(q.id)
                c.queueSync.refresh()
                true
            }
            else -> false
        }
    }

    /** No server (or the source failed): shuffle what's downloaded. */
    private suspend fun playDownloads(): Boolean {
        if (!active) return false
        val tracks = c.offline.offlineTracks.value.shuffled().take(60)
        if (tracks.isEmpty()) return false
        play(tracks, PlaySource.LIBRARY, null)
        c.queueSync.setCurrent(null)
        return true
    }

    private suspend fun play(tracks: List<Track>, source: PlaySource, mixId: String?) {
        val items = withContext(Dispatchers.IO) { host.mediaItems(tracks, source, mixId) }
        if (!active) return
        host.showSession()
        switching = true
        try {
            player.shuffleModeEnabled = false
            player.setMediaItems(items, 0, 0)
            player.prepare()
            player.play()
        } finally {
            switching = false
        }
    }

    // ---- volume and the last-resort tone ----

    private fun raiseVolume(minPct: Int) {
        val am = ctx.getSystemService(AudioManager::class.java) ?: return
        val cur = am.getStreamVolume(AudioManager.STREAM_MUSIC)
        val max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        AlarmTimes.volumeFloor(cur, max, minPct)?.let { runCatching { am.setStreamVolume(AudioManager.STREAM_MUSIC, it, 0) } }
    }

    private fun ring() {
        if (!active || ringtone != null) return
        android.util.Log.w("WakeAlarm", "music didn't start; ringing the alarm tone")
        if (!ownsForeground) ownsForeground = host.startForeground(AlarmNotifications.ringing(ctx, "Alarm tone (music couldn't start)", c.alarm.alarm.value.snoozeMin))
        mixer.alarm = 1f
        val uri = RingtoneManager.getActualDefaultRingtoneUri(ctx, RingtoneManager.TYPE_ALARM)
            ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
            ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
        ringtone = RingtoneManager.getRingtone(ctx, uri)?.apply {
            audioAttributes = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
            if (Build.VERSION.SDK_INT >= 28) isLooping = true
            play()
        }
        handler.postDelayed(ringLimit, 10 * 60_000L)
    }

    /** Ring for at most 10 minutes. */
    private val ringLimit = Runnable { if (ringtone != null) stop() }

    private fun stopRingtone() {
        ringtone?.let { runCatching { it.stop() } }
        ringtone = null
    }

    companion object {
        const val ACTION_START = "org.homebeat.alarm.START"
        const val ACTION_STOP = "org.homebeat.alarm.STOP"
        const val ACTION_SNOOZE = "org.homebeat.alarm.SNOOZE"
    }
}
