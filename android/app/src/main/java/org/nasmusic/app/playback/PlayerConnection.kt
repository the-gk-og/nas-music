package org.nasmusic.app.playback

import android.content.ComponentName
import android.content.Context
import androidx.core.content.ContextCompat
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.nasmusic.app.data.api.Track

data class NowPlaying(
    val item: MediaItem? = null,
    val isPlaying: Boolean = false,
    val positionMs: Long = 0,
    val durationMs: Long = 0,
    val hasNext: Boolean = false,
    val hasPrevious: Boolean = false,
)

/** UI-side handle on the [PlaybackService] through a MediaController. */
class PlayerConnection(private val context: Context) {
    private var controller: MediaController? = null
    private val _state = MutableStateFlow(NowPlaying())
    val state: StateFlow<NowPlaying> = _state.asStateFlow()
    private val pending = mutableListOf<(MediaController) -> Unit>()

    fun connect() {
        if (controller != null) return
        val token = SessionToken(context, ComponentName(context, PlaybackService::class.java))
        val future = MediaController.Builder(context, token).buildAsync()
        future.addListener({
            val c = runCatching { future.get() }.getOrNull() ?: return@addListener
            controller = c
            c.addListener(object : Player.Listener {
                override fun onEvents(player: Player, events: Player.Events) = refresh()
            })
            refresh()
            pending.forEach { it(c) }
            pending.clear()
        }, ContextCompat.getMainExecutor(context))
    }

    fun release() {
        controller?.release()
        controller = null
    }

    /** Called by the UI on a timer to update the position bar. */
    fun refresh() {
        val c = controller ?: return
        _state.value = NowPlaying(
            item = c.currentMediaItem,
            isPlaying = c.isPlaying,
            positionMs = c.currentPosition,
            durationMs = c.duration.coerceAtLeast(0),
            hasNext = c.hasNextMediaItem(),
            hasPrevious = c.hasPreviousMediaItem(),
        )
    }

    private fun withController(block: (MediaController) -> Unit) {
        controller?.let(block) ?: run { pending += block; connect() }
    }

    fun play(tracks: List<Track>, startIndex: Int, source: PlaySource, mixId: String? = null, reasons: List<String?>? = null) =
        withController { c ->
            val items = tracks.mapIndexed { i, t -> MediaItems.fromTrack(t, source, mixId, reasons?.getOrNull(i)) }
            c.setMediaItems(items, startIndex.coerceIn(0, (items.size - 1).coerceAtLeast(0)), 0)
            c.prepare()
            c.play()
        }

    fun togglePlay() = withController { if (it.isPlaying) it.pause() else it.play() }
    fun next() = withController { it.seekToNext() }
    fun previous() = withController { it.seekToPrevious() }
    fun seekTo(ms: Long) = withController { it.seekTo(ms) }
}
