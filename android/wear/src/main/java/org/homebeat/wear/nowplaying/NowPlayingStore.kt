package org.homebeat.wear.nowplaying

import android.content.ComponentName
import android.content.Context
import androidx.wear.tiles.TileService
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceUpdateRequester
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import org.homebeat.wear.complication.NowPlayingComplicationService
import org.homebeat.wear.tile.NowPlayingTileService

/** What the tile and complication show: the last thing known to play, here or on another device. */
@Serializable
data class NowPlaying(
    val trackId: String? = null,
    val title: String = "",
    val artist: String = "",
    val albumId: String? = null,
    val playing: Boolean = false,
    /** null = on this watch. */
    val deviceName: String? = null,
    val queueId: String? = null,
    val updatedAtMs: Long = 0,
) {
    val isEmpty: Boolean get() = trackId == null
    val isLocal: Boolean get() = deviceName == null
}

/**
 * Persists [NowPlaying] (so tiles survive process death) and pokes the tile and complication when
 * what they would show changes.
 */
class NowPlayingStore(private val context: Context) {
    private val prefs = context.getSharedPreferences("now_playing", Context.MODE_PRIVATE)
    private val json = Json { ignoreUnknownKeys = true }
    private val _state = MutableStateFlow(load())
    val state: StateFlow<NowPlaying> = _state.asStateFlow()

    private fun load(): NowPlaying =
        prefs.getString("np", null)?.let { runCatching { json.decodeFromString(NowPlaying.serializer(), it) }.getOrNull() }
            ?: NowPlaying()

    fun update(next: NowPlaying) {
        val prev = _state.value
        val stamped = next.copy(updatedAtMs = System.currentTimeMillis())
        _state.value = stamped
        prefs.edit().putString("np", json.encodeToString(NowPlaying.serializer(), stamped)).apply()
        if (prev.copy(updatedAtMs = 0) != next.copy(updatedAtMs = 0)) requestSurfaces()
    }

    fun requestSurfaces() {
        runCatching { TileService.getUpdater(context).requestUpdate(NowPlayingTileService::class.java) }
        runCatching {
            ComplicationDataSourceUpdateRequester.create(
                context, ComponentName(context, NowPlayingComplicationService::class.java),
            ).requestUpdateAll()
        }
    }
}
