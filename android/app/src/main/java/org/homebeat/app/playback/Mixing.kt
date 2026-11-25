package org.homebeat.app.playback

import androidx.media3.common.ForwardingPlayer
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer

/**
 * ExoPlayer's volume = user volume × levelling × crossfade fade-in × sleep fade × wake-up alarm fade-in ×
 * DJ-voice duck. ExoPlayer can only
 * attenuate, so a levelling gain above 0 dB is applied as 0 dB (tracks louder than the reference are
 * the common case; quiet tracks simply aren't raised on the phone).
 */
class VolumeMixer(private val apply: (Float) -> Unit) {
    var user = 1f
        set(v) { field = v.coerceIn(0f, 1f); push() }
    var levelling = 1f
        set(v) { field = v.coerceIn(0f, 1f); push() }
    var fade = 1f
        set(v) { field = v.coerceIn(0f, 1f); push() }
    var sleep = 1f
        set(v) { field = v.coerceIn(0f, 1f); push() }
    var alarm = 1f
        set(v) { field = v.coerceIn(0f, 1f); push() }
    /** Lowered while the radio DJ talks (DjVoice). */
    var duck = 1f
        set(v) { field = v.coerceIn(0f, 1f); push() }

    val total: Float get() = (user * levelling * fade * sleep * alarm * duck).coerceIn(0f, 1f)

    fun push() = apply(total)
}

/**
 * The player the MediaSession (and so the app UI, notification, watch and car) sees: its volume is
 * the user's volume, not the levelled/faded value ExoPlayer actually plays at.
 */
@UnstableApi
class UserVolumePlayer(player: Player, private val mixer: VolumeMixer, private val onUserVolume: (Float) -> Unit) : ForwardingPlayer(player) {
    override fun setVolume(volume: Float) {
        mixer.user = volume
        onUserVolume(mixer.user)
    }

    override fun getVolume(): Float = mixer.user
}

/**
 * Crossfade with a second, short-lived ExoPlayer: a few seconds before the fade the outgoing track is
 * prepared on it at the fade start position; at the fade start it plays there (fading out) while the
 * main player moves on to the next track (fading in). The MediaSession only ever shows the main player.
 */
@UnstableApi
class Crossfader(private val build: () -> ExoPlayer) {
    private var fader: ExoPlayer? = null
    private var preparedId: String? = null
    private var startedAtMs = 0L
    private var lengthMs = 0L
    private var startVolume = 1f
    /** DJ automix drives volume and stops it itself ([startManual]); [step] leaves it alone. */
    private var manual = false

    val active: Boolean get() = startedAtMs > 0
    val startedAt: Long get() = startedAtMs
    val positionMs: Long get() = fader?.currentPosition ?: 0L
    val playing: Boolean get() = fader?.isPlaying == true

    fun startManual(volume: Float) {
        val f = fader ?: return
        manual = true
        f.volume = volume
        f.play()
        startedAtMs = System.currentTimeMillis()
    }

    fun setVolume(v: Float) { fader?.volume = v.coerceIn(0f, 1f) }

    fun prepare(item: MediaItem, startMs: Long, speed: Float) {
        if (preparedId == item.mediaId && fader != null) return
        release()
        fader = build().apply {
            setMediaItem(item, startMs)
            setPlaybackSpeed(speed)
            prepare()
        }
        preparedId = item.mediaId
    }

    fun isReadyFor(id: String?): Boolean = id != null && preparedId == id && fader?.playbackState == Player.STATE_READY

    fun start(lengthMs: Long, volume: Float) {
        val f = fader ?: return
        this.lengthMs = lengthMs.coerceAtLeast(1)
        startVolume = volume
        f.volume = volume
        f.play()
        startedAtMs = System.currentTimeMillis()
    }

    /** Advance the fade. Returns progress 0..1 (for the main player's fade-in), or null when no fade runs. */
    fun step(): Double? {
        if (!active || manual) return null
        val x = (System.currentTimeMillis() - startedAtMs).toDouble() / lengthMs
        if (x >= 1.0) {
            release()
            return null
        }
        fader?.volume = startVolume * ListeningRules.fadeOut(x).toFloat()
        return x
    }

    fun release() {
        fader?.release()
        fader = null
        preparedId = null
        startedAtMs = 0
        manual = false
    }
}
