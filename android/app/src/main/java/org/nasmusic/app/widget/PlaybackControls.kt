package org.nasmusic.app.widget

import android.content.ComponentName
import android.content.Context
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.ProcessLifecycleOwner
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.guava.await
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.nasmusic.app.container
import org.nasmusic.app.data.api.RadioSeed
import org.nasmusic.app.feedback.EventType
import org.nasmusic.app.playback.PlaybackService

/**
 * Transport controls for widgets and tiles. They go where the music is: a Google Cast session (full
 * flavour), the Connect active device or speaker ("Playing on Kitchen"), or this phone's player.
 *
 * This phone's player is driven through a short-lived MediaController, so a widget tap never leaves the
 * playback service bound after the app's UI is gone (PlayerConnection's controller lives with MainActivity).
 */
object PlaybackControls {
    enum class Command { PLAY, PAUSE, NEXT, PREVIOUS }

    /** The big button: play/pause, resume the last queue, or "Play something for now". */
    suspend fun primary(ctx: Context) {
        val info = WidgetHub.state.value
        when (WidgetStates.primaryAction(info)) {
            PrimaryAction.TOGGLE -> send(ctx, if (info.isPlaying) Command.PAUSE else Command.PLAY)
            PrimaryAction.RESUME -> resume(ctx)
            PrimaryAction.PLAY_SOMETHING -> playSomethingForNow(ctx)
        }
    }

    suspend fun send(ctx: Context, cmd: Command) {
        val c = ctx.container
        val ext = c.player.external?.takeIf { it.active }
        when {
            ext != null -> withContext(Dispatchers.Main) {
                when (cmd) {
                    Command.PLAY -> ext.play(); Command.PAUSE -> ext.pause()
                    Command.NEXT -> ext.next(); Command.PREVIOUS -> ext.previous()
                }
            }
            c.connect.isRemote -> c.connect.command(
                when (cmd) { Command.PLAY -> "play"; Command.PAUSE -> "pause"; Command.NEXT -> "next"; Command.PREVIOUS -> "prev" },
            )
            else -> withLocalController(ctx) { p ->
                when (cmd) {
                    Command.PLAY -> { if (p.playbackState == androidx.media3.common.Player.STATE_IDLE) p.prepare(); p.play() }
                    Command.PAUSE -> p.pause()
                    Command.NEXT -> p.seekToNext()
                    Command.PREVIOUS -> p.seekToPrevious()
                }
            }
        }
    }

    /** AI radio for this moment (smart shuffle), here or on the active device. */
    suspend fun playSomethingForNow(ctx: Context): Boolean {
        val c = ctx.container
        val ok = c.player.startRadio(RadioSeed("moment"))
        if (ok) settleAndReleaseIfBackground(ctx)
        return ok
    }

    /** Nothing loaded in the player but a remembered song: pick the last queue back up where it stopped. */
    private suspend fun resume(ctx: Context) {
        val c = ctx.container
        val q = (c.queueSync.currentId.value ?: c.queueSync.queues.value.firstOrNull()?.id)?.let { c.queueSync.load(it) }
        if (q == null || q.tracks.isEmpty()) { playSomethingForNow(ctx); return }
        c.queueSync.setCurrent(q.id)
        c.player.playQueue(q)
        settleAndReleaseIfBackground(ctx)
    }

    suspend fun toggleFavourite(ctx: Context) {
        val c = ctx.container
        val info = WidgetHub.state.value
        val id = info.trackId ?: return
        val star = !info.starred
        WidgetHub.setStarred(id, star)
        c.events.record(if (star) EventType.STAR else EventType.UNSTAR, id, context = c.contextCollector.snapshot())
        val ok = runCatching { if (star) c.api.star(id) else c.api.unstar(id) }.getOrNull()?.isSuccessful == true
        if (!ok) WidgetHub.setStarred(id, info.starred)
    }

    private suspend fun withLocalController(ctx: Context, block: (MediaController) -> Unit) = withContext(Dispatchers.Main) {
        val token = SessionToken(ctx, ComponentName(ctx, PlaybackService::class.java))
        val controller = runCatching { MediaController.Builder(ctx, token).buildAsync().await() }.getOrNull() ?: return@withContext
        try {
            block(controller)
            delay(300) // let the commands reach the session before unbinding
        } finally {
            controller.release()
        }
    }

    /**
     * startRadio/playQueue go through PlayerConnection, which binds its own controller. Once the player has
     * the new queue, unbind again unless the app is on screen (MainActivity owns that controller then).
     */
    private suspend fun settleAndReleaseIfBackground(ctx: Context) {
        val c = ctx.container
        if (c.connect.isRemote) return
        withTimeoutOrNull(10_000) { c.player.state.first { it.item != null && it.remote == null } }
        delay(500)
        withContext(Dispatchers.Main) {
            if (!ProcessLifecycleOwner.get().lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) c.player.release()
        }
    }
}
