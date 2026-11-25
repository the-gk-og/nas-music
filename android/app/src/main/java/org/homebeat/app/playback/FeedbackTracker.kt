package org.homebeat.app.playback

import android.os.Handler
import android.os.Looper
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import java.util.UUID
import org.homebeat.app.context.ContextCollector
import org.homebeat.app.context.ListeningDetails
import org.homebeat.app.feedback.EventRecorder
import org.homebeat.app.feedback.EventType
import org.homebeat.app.feedback.LeaveReason
import org.homebeat.app.feedback.PlaybackClassifier

/**
 * Watches the player and turns transitions into play/skip events via [PlaybackClassifier], with the
 * opt-in listening context (seeks, volume changes, sessions…) attached. Only the device that
 * actually plays audio records these (docs/v2.md §5).
 */
class FeedbackTracker(
    private val player: Player,
    private val recorder: EventRecorder,
    private val contextCollector: ContextCollector? = null,
) : Player.Listener {
    private val handler = Handler(Looper.getMainLooper())
    private var current: MediaItem? = null
    private var counted = false
    private var lastPositionMs = 0L

    // Listening details for the current track
    private val seeks = mutableListOf<Pair<Long, Long>>()
    private val volumes = mutableListOf<Pair<Long, Float>>()
    private var previousTrackId: String? = null
    private var sessionId: String = UUID.randomUUID().toString()
    private var sessionPosition = 0
    private var lastPlayEndMs = 0L

    private val poll = object : Runnable {
        override fun run() {
            check()
            if (player.isPlaying) handler.postDelayed(this, 1000)
        }
    }

    fun attach() {
        player.addListener(this)
        current = player.currentMediaItem
    }

    fun release() {
        handler.removeCallbacks(poll)
        current?.let { leave(it, lastPositionMs, LeaveReason.OTHER) }
        player.removeListener(this)
    }

    private fun durationS(item: MediaItem): Double {
        val fromPlayer = player.duration.takeIf { it > 0 && player.currentMediaItem == item }?.div(1000.0)
        return fromPlayer ?: item.mediaMetadata.extras?.getDouble(MediaItems.EXTRA_DURATION_S) ?: 0.0
    }

    private fun check() {
        val item = current ?: return
        lastPositionMs = player.currentPosition
        if (PlaybackClassifier.onProgress(lastPositionMs / 1000.0, durationS(item), counted) == EventType.PLAY) {
            emit(EventType.PLAY, item, lastPositionMs)
            counted = true
        }
    }

    override fun onIsPlayingChanged(isPlaying: Boolean) {
        handler.removeCallbacks(poll)
        if (isPlaying) {
            // A new listening session starts after 30 minutes without music.
            if (lastPlayEndMs > 0 && System.currentTimeMillis() - lastPlayEndMs > 30 * 60_000L) {
                sessionId = UUID.randomUUID().toString(); sessionPosition = 0
            }
            handler.post(poll)
        } else {
            lastPlayEndMs = System.currentTimeMillis()
        }
    }

    /** The user's volume (player.volume also carries levelling and fades, so it isn't listened to). */
    fun userVolumeChanged(volume: Float) { volumes += player.currentPosition to volume }

    private var autoTransition = false

    /** The next item change is automatic even though it is a seek (crossfade hands over with seekToNext). */
    fun markAutoTransition() { autoTransition = true }
    override fun onDeviceVolumeChanged(volume: Int, muted: Boolean) {
        val max = player.deviceInfo.maxVolume.takeIf { it > 0 } ?: 15
        volumes += player.currentPosition to if (muted) 0f else volume.toFloat() / max
    }

    override fun onPositionDiscontinuity(old: Player.PositionInfo, new: Player.PositionInfo, reason: Int) {
        val leaving = old.mediaItem ?: return
        if (leaving.mediaId == new.mediaItem?.mediaId && old.mediaItemIndex == new.mediaItemIndex) {
            if (reason == Player.DISCONTINUITY_REASON_SEEK) seeks += old.positionMs to new.positionMs
            return
        }
        val auto = autoTransition
        autoTransition = false
        val why = when {
            auto -> LeaveReason.ENDED
            else -> when (reason) {
            Player.DISCONTINUITY_REASON_AUTO_TRANSITION -> LeaveReason.ENDED
            Player.DISCONTINUITY_REASON_SEEK, Player.DISCONTINUITY_REASON_SEEK_ADJUSTMENT -> LeaveReason.USER_SKIP
            else -> LeaveReason.OTHER
            }
        }
        leave(leaving, old.positionMs, why)
        startTracking(new.mediaItem)
    }

    override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
        if (reason == Player.MEDIA_ITEM_TRANSITION_REASON_PLAYLIST_CHANGED && mediaItem?.mediaId != current?.mediaId) {
            current?.let { leave(it, lastPositionMs, LeaveReason.OTHER) }
            startTracking(mediaItem)
        } else if (current == null) {
            startTracking(mediaItem)
        }
    }

    override fun onPlaybackStateChanged(state: Int) {
        if (state == Player.STATE_ENDED) {
            current?.let { leave(it, player.duration.coerceAtLeast(0), LeaveReason.ENDED) }
            current = null
        }
    }

    private fun startTracking(item: MediaItem?) {
        current = item
        counted = false
        lastPositionMs = 0
        seeks.clear(); volumes.clear()
    }

    private fun leave(item: MediaItem, positionMs: Long, reason: LeaveReason) {
        if (current?.mediaId != item.mediaId) return
        PlaybackClassifier.onLeave(positionMs / 1000.0, durationS(item), counted, reason)?.let {
            emit(it, item, positionMs)
        }
        counted = true
        previousTrackId = item.mediaId
    }

    private fun emit(type: EventType, item: MediaItem, positionMs: Long) {
        // Podcasts, audiobooks and radio stations share the player but are never listening events.
        if (org.homebeat.app.spoken.SpokenMedia.isSpoken(item)) return
        val x = item.mediaMetadata.extras
        val source = x?.getString(MediaItems.EXTRA_SOURCE)
        if (type == EventType.PLAY || type == EventType.SKIP) sessionPosition++
        val details = ListeningDetails(
            seeks = seeks.toList(),
            volumeChanges = volumes.toList(),
            repeatOfPrevious = previousTrackId == item.mediaId,
            addedFrom = source,
            sessionId = sessionId,
            sessionPosition = sessionPosition,
        )
        recorder.record(
            type = type,
            trackId = item.mediaId,
            positionS = positionMs / 1000.0,
            durationS = durationS(item).takeIf { it > 0 },
            source = source,
            mixId = x?.getString(MediaItems.EXTRA_MIX_ID),
            context = contextCollector?.snapshot(details, x?.getString(MediaItems.EXTRA_SEARCH_QUERY)),
        )
    }
}
