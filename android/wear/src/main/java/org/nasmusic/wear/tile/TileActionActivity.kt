package org.nasmusic.wear.tile

import android.app.Activity
import android.os.Bundle
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import org.nasmusic.wear.WearApp

/**
 * Invisible trampoline for tile buttons (a tile can only launch an activity). Makes sure the
 * Connect socket is up, so it knows whether to drive the watch's player or a remote device, runs the
 * action, then finishes. Quick resume: ▶ with nothing loaded picks up the last queue on the watch.
 */
class TileActionActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val action = intent.getStringExtra(EXTRA_ACTION)
        val c = (application as WearApp).container
        // App-wide scope: this activity finishes immediately, the action must not be cancelled with it.
        CoroutineScope(SupervisorJob() + Dispatchers.Main).launch {
            val np = c.nowPlaying.state.value
            val needConnect = !(np.isLocal && np.playing)
            if (needConnect) {
                c.connect.acquire(HOLDER)
                withTimeoutOrNull(3_000) { c.connect.connected.first { it } }
            }
            when (action) {
                PLAY_PAUSE -> c.player.playPauseOrResume(np.queueId)
                NEXT -> c.player.next()
                PREV -> c.player.prev()
            }
            delay(1_500)
            c.nowPlaying.requestSurfaces()
            if (needConnect) c.connect.release(HOLDER)
        }
        finish()
    }

    companion object {
        const val EXTRA_ACTION = "tile_action"
        const val OPEN = "open"
        const val PLAY_PAUSE = "play_pause"
        const val NEXT = "next"
        const val PREV = "prev"
        private const val HOLDER = "tile-action"
    }
}
