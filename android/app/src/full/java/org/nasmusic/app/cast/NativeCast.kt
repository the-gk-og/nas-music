package org.nasmusic.app.cast

import android.util.Log
import android.view.ContextThemeWrapper
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.mediarouter.app.MediaRouteButton
import com.google.android.gms.cast.MediaInfo
import com.google.android.gms.cast.MediaLoadRequestData
import com.google.android.gms.cast.MediaMetadata
import com.google.android.gms.cast.MediaQueueData
import com.google.android.gms.cast.MediaQueueItem
import com.google.android.gms.cast.MediaStatus
import com.google.android.gms.cast.framework.CastButtonFactory
import com.google.android.gms.cast.framework.CastContext
import com.google.android.gms.cast.framework.CastSession
import com.google.android.gms.cast.framework.SessionManagerListener
import com.google.android.gms.common.images.WebImage
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.CastSignRequest
import org.nasmusic.app.playback.ExternalTarget
import org.nasmusic.app.playback.NowPlaying
import com.google.android.gms.cast.MediaSeekOptions
import com.google.android.gms.cast.framework.media.RemoteMediaClient

/**
 * `full` flavour: a Google Cast button that casts the phone's current queue straight to a Chromecast,
 * using signed media URLs from the server (POST /cast/sign) and Google's Default Media Receiver.
 *
 * This is outside Connect: while casting, the phone's own player is paused, and when the Cast session ends
 * the phone resumes (paused) at the track and position the Chromecast reached. The FOSS way to reach a
 * Chromecast is the server-side speaker in the Devices list, which also works from the web player.
 */
object NativeCast {
    const val AVAILABLE = true
    private const val TAG = "NativeCast"
    /** Cast queues are limited; send a window around the current track. */
    private const val BEFORE = 20
    private const val MAX_ITEMS = 200

    private var cast: CastContext? = null
    private val ready = mutableStateOf(false)
    private var endedAt: Pair<Int, Long>? = null

    fun init(c: AppContainer) {
        if (cast != null) return
        try {
            CastContext.getSharedInstance(c.context, ContextCompat.getMainExecutor(c.context))
                .addOnSuccessListener { cc ->
                    cast = cc
                    cc.sessionManager.addSessionManagerListener(Listener(c), CastSession::class.java)
                    c.player.external = Target
                    ready.value = true
                }
                .addOnFailureListener { Log.w(TAG, "Google Cast unavailable: ${it.message}") }
        } catch (e: Exception) {
            // No (or old) Google Play Services on this device: no Cast button, everything else works.
            Log.w(TAG, "Google Cast unavailable: ${e.message}")
        }
    }

    @Composable
    fun DevicesRow(c: AppContainer) {
        val ok by ready
        if (!ok) return
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            AndroidView(
                factory = { ctx ->
                    MediaRouteButton(ContextThemeWrapper(ctx, androidx.appcompat.R.style.Theme_AppCompat_DayNight)).also {
                        CastButtonFactory.setUpMediaRouteButton(ctx.applicationContext, it)
                    }
                },
                modifier = Modifier.size(48.dp),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                "Google Cast from this phone",
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }

    /** Transport controls and progress while a Cast session plays the queue (see PlayerConnection.external). */
    private object Target : ExternalTarget {
        private val session: CastSession? get() = cast?.sessionManager?.currentCastSession?.takeIf { it.isConnected }
        private val rmc: RemoteMediaClient? get() = session?.remoteMediaClient?.takeIf { it.hasMediaSession() }
        override val active: Boolean get() = rmc != null
        override fun play() { rmc?.play() }
        override fun pause() { rmc?.pause() }
        override fun next() { rmc?.queueNext(null) }
        override fun previous() {
            val r = rmc ?: return
            // Like a normal player: restart the track unless we're near its start.
            if (r.approximateStreamPosition > 3000) r.seek(MediaSeekOptions.Builder().setPosition(0).build()) else r.queuePrev(null)
        }
        override fun seekTo(ms: Long) { rmc?.seek(MediaSeekOptions.Builder().setPosition(ms).build()) }
        override fun skipTo(index: Int) {
            val r = rmc ?: return
            val item = r.mediaStatus?.queueItems?.firstOrNull { it.media?.customData?.optInt("index", -1) == index }
            if (item != null) r.queueJumpToItem(item.itemId, null)
        }
        override fun setVolume(v: Float) { runCatching { session?.volume = v.toDouble() } }
        override fun overlay(local: NowPlaying): NowPlaying? {
            val r = rmc ?: return null
            val idx = r.mediaInfo?.customData?.optInt("index", -1)?.takeIf { it >= 0 } ?: local.index
            return local.copy(
                item = local.queue.getOrNull(idx) ?: local.item,
                index = idx,
                isPlaying = r.isPlaying || r.isBuffering,
                positionMs = r.approximateStreamPosition,
                durationMs = r.streamDuration.coerceAtLeast(0),
                hasNext = idx < local.queue.size - 1,
                hasPrevious = true,
                volume = (session?.volume ?: local.volume.toDouble()).toFloat(),
            )
        }
    }

    private class Listener(private val c: AppContainer) : SessionManagerListener<CastSession> {
        override fun onSessionStarted(session: CastSession, sessionId: String) = handOff(c, session)
        override fun onSessionResumed(session: CastSession, wasSuspended: Boolean) = Unit
        override fun onSessionEnding(session: CastSession) {
            val rmc = session.remoteMediaClient ?: return
            val idx = rmc.mediaInfo?.customData?.optInt("index", -1) ?: -1
            endedAt = if (idx >= 0) idx to rmc.approximateStreamPosition else null
        }
        override fun onSessionEnded(session: CastSession, error: Int) {
            val (idx, pos) = endedAt ?: return
            endedAt = null
            c.player.resumeAt(idx, pos, play = false)
        }
        override fun onSessionStarting(session: CastSession) = Unit
        override fun onSessionStartFailed(session: CastSession, error: Int) = Unit
        override fun onSessionResuming(session: CastSession, sessionId: String) = Unit
        override fun onSessionResumeFailed(session: CastSession, error: Int) = Unit
        override fun onSessionSuspended(session: CastSession, reason: Int) = Unit
    }

    private fun handOff(c: AppContainer, session: CastSession) {
        val now = c.player.state.value
        val ids = now.queue.map { it.mediaId }.filter { it.isNotBlank() }
        if (ids.isEmpty()) return
        val index = now.index.coerceIn(0, ids.size - 1)
        val start = (index - BEFORE).coerceAtLeast(0)
        val window = ids.subList(start, minOf(ids.size, start + MAX_ITEMS))
        val position = now.positionMs
        c.player.pauseLocal()
        c.scope.launch {
            val signed = runCatching { c.api.castSign(CastSignRequest(window)).items }.getOrElse {
                c.ui.toast("Couldn't start casting: ${it.message}")
                return@launch
            }
            val byId = now.queue.associateBy { it.mediaId }
            // Queue position of each signed item (the server skips unknown ids; a track may repeat).
            var w = 0
            val positions = signed.map { s ->
                while (w < window.size && window[w] != s.trackId) w++
                (start + w).also { w++ }
            }
            val items = signed.mapIndexed { i, s ->
                val md = byId[s.trackId]?.mediaMetadata
                val meta = MediaMetadata(MediaMetadata.MEDIA_TYPE_MUSIC_TRACK).apply {
                    md?.title?.let { putString(MediaMetadata.KEY_TITLE, it.toString()) }
                    md?.artist?.let { putString(MediaMetadata.KEY_ARTIST, it.toString()) }
                    md?.albumTitle?.let { putString(MediaMetadata.KEY_ALBUM_TITLE, it.toString()) }
                    s.artUrl?.let { addImage(WebImage(Uri.parse(it))) }
                }
                val info = MediaInfo.Builder(s.url)
                    .setContentType(s.contentType)
                    .setStreamType(MediaInfo.STREAM_TYPE_BUFFERED)
                    .setMetadata(meta)
                    .setCustomData(JSONObject().put("trackId", s.trackId).put("index", positions[i]))
                    .build()
                MediaQueueItem.Builder(info).setAutoplay(true).setPreloadTime(10.0).build()
            }
            if (items.isEmpty()) return@launch
            val startAt = positions.indexOfFirst { it >= index }.let { if (it < 0) items.size - 1 else it }
            withContext(Dispatchers.Main) {
                val req = MediaLoadRequestData.Builder()
                    .setQueueData(MediaQueueData.Builder().setItems(items).setStartIndex(startAt)
                        .setRepeatMode(MediaStatus.REPEAT_MODE_REPEAT_OFF).build())
                    .setCurrentTime(position)
                    .setAutoplay(true)   // casting starts playing, even from a paused phone
                    .build()
                session.remoteMediaClient?.load(req)
            }
        }
    }
}
