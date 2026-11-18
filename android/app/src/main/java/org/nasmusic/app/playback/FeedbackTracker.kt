package org.nasmusic.app.playback

import android.os.Handler
import android.os.Looper
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import org.nasmusic.app.feedback.EventRecorder
import org.nasmusic.app.feedback.EventType
import org.nasmusic.app.feedback.LeaveReason
import org.nasmusic.app.feedback.PlaybackClassifier

/**
 * Watches the player and turns transitions into play/skip events via [PlaybackClassifier].
 * Polls position once a second while playing so the 80 % mark is caught even if the user then
 * pauses forever.
 */
class FeedbackTracker(
    private val player: Player,
    private val recorder: EventRecorder,
) : Player.Listener {
    private val handler = Handler(Looper.getMainLooper())
    private var current: MediaItem? = null
    private var counted = false
    private var lastPositionMs = 0L

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
        if (isPlaying) handler.post(poll)
    }

    override fun onPositionDiscontinuity(old: Player.PositionInfo, new: Player.PositionInfo, reason: Int) {
        val leaving = old.mediaItem ?: return
        if (leaving.mediaId == new.mediaItem?.mediaId && old.mediaItemIndex == new.mediaItemIndex) return
        val why = when (reason) {
            Player.DISCONTINUITY_REASON_AUTO_TRANSITION -> LeaveReason.ENDED
            Player.DISCONTINUITY_REASON_SEEK, Player.DISCONTINUITY_REASON_SEEK_ADJUSTMENT -> LeaveReason.USER_SKIP
            else -> LeaveReason.OTHER
        }
        leave(leaving, old.positionMs, why)
        startTracking(new.mediaItem)
    }

    override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
        // Queue replaced (setMediaItems) does not always report a discontinuity.
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
    }

    private fun leave(item: MediaItem, positionMs: Long, reason: LeaveReason) {
        if (current?.mediaId != item.mediaId) return
        PlaybackClassifier.onLeave(positionMs / 1000.0, durationS(item), counted, reason)?.let {
            emit(it, item, positionMs)
        }
        counted = true
    }

    private fun emit(type: EventType, item: MediaItem, positionMs: Long) {
        val x = item.mediaMetadata.extras
        recorder.record(
            type = type,
            trackId = item.mediaId,
            positionS = positionMs / 1000.0,
            durationS = durationS(item).takeIf { it > 0 },
            source = x?.getString(MediaItems.EXTRA_SOURCE),
            mixId = x?.getString(MediaItems.EXTRA_MIX_ID),
        )
    }
}
