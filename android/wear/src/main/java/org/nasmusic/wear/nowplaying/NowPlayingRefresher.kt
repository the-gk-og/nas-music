package org.nasmusic.wear.nowplaying

import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Request
import org.nasmusic.app.net.Route
import org.nasmusic.wear.WearContainer
import org.nasmusic.wear.offline.OfflineStore
import org.nasmusic.wear.playback.WearMediaItems

/**
 * Fresh now-playing info for the tile, which may be shown while the app is not running: opens the
 * Connect socket briefly, reads the shared state, closes it again.
 */
class NowPlayingRefresher(private val c: WearContainer) {

    suspend fun refresh(timeoutMs: Long = 3_500): NowPlaying {
        val cur = c.nowPlaying.state.value
        // The watch itself is playing: the service keeps the store current.
        if (cur.isLocal && cur.playing) return cur
        if (c.secureStore.config.value?.isPaired != true || c.router.route.value == Route.OFFLINE) return cur
        c.connect.acquire(HOLDER)
        try {
            withTimeoutOrNull(timeoutMs) { c.connect.connected.first { it } } ?: return cur
            val s = c.connect.state.value ?: return cur
            val me = c.connect.myDeviceId.value
            val device = c.connect.devices.value.firstOrNull { it.id == s.activeDeviceId && it.online }
            if (device == null || device.id == me || s.trackId == null) {
                return cur.copy(playing = false).also { c.nowPlaying.update(it) }
            }
            val t = if (cur.trackId == s.trackId && cur.title.isNotBlank()) null
            else withTimeoutOrNull(timeoutMs) { runCatching { c.api.track(s.trackId!!) }.getOrNull() }
            val next = NowPlaying(
                trackId = s.trackId,
                title = t?.title ?: cur.title.takeIf { cur.trackId == s.trackId }.orEmpty(),
                artist = t?.artist ?: cur.artist.takeIf { cur.trackId == s.trackId }.orEmpty(),
                albumId = t?.albumId ?: cur.albumId.takeIf { cur.trackId == s.trackId },
                playing = s.playing,
                deviceName = device.name,
                queueId = s.queueId,
            )
            c.nowPlaying.update(next)
            return next
        } finally {
            c.connect.release(HOLDER)
        }
    }

    /** Small cover for the tile, cached per album. */
    suspend fun art(albumId: String?, size: Int = 128): ByteArray? = withContext(Dispatchers.IO) {
        if (albumId.isNullOrBlank()) return@withContext null
        val dir = File(c.context.cacheDir, "tile-art").apply { mkdirs() }
        val f = File(dir, OfflineStore.safe(albumId) + ".img")
        if (f.isFile && f.length() > 0) return@withContext f.readBytes()
        runCatching {
            val req = Request.Builder().url(WearMediaItems.artUrl(albumId, size).toHttpUrl()).build()
            c.callFactory.newCall(req).execute().use { r ->
                if (!r.isSuccessful) null else r.body?.bytes()?.also { bytes ->
                    // Keep only a handful of covers.
                    dir.listFiles()?.sortedBy { it.lastModified() }?.dropLast(10)?.forEach { it.delete() }
                    f.writeBytes(bytes)
                }
            }
        }.getOrNull()
    }

    private companion object { const val HOLDER = "surface" }
}
