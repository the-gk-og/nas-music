package org.nasmusic.app.widget

import android.content.Context
import android.graphics.Bitmap
import androidx.glance.appwidget.GlanceAppWidgetManager
import androidx.glance.appwidget.updateAll
import coil3.SingletonImageLoader
import coil3.request.ImageRequest
import coil3.request.SuccessResult
import coil3.request.allowHardware
import coil3.toBitmap
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.FeedRepository
import org.nasmusic.app.data.api.ShelfItem
import org.nasmusic.app.data.api.pins
import org.nasmusic.app.net.RoutedCallFactory
import org.nasmusic.app.playback.MediaItems
import org.nasmusic.app.playback.NowPlaying

/** A recent or pinned item on the large widget, with its (small) cover. */
data class Shortcut(val item: ShelfItem, val art: Bitmap?)

/**
 * The one source of "what's playing" for widgets and tiles. Fed by events, never polled:
 * - [publishLocal] from PlaybackService's player listener (this phone's player, Cast overlay applied);
 * - PlayerConnection.state, which carries Connect's remote state while another device/speaker is active.
 * Widgets are re-rendered only when the shown state (not the position) changes.
 */
object WidgetHub {
    private val local = MutableStateFlow<PlayingInfo?>(null)
    private val remembered = MutableStateFlow<PlayingInfo?>(null)
    private val stars = MutableStateFlow<Map<String, Boolean>>(emptyMap())
    private val _state = MutableStateFlow(PlayingInfo())
    val state: StateFlow<PlayingInfo> = _state.asStateFlow()
    private val _cover = MutableStateFlow<Pair<String, Bitmap>?>(null)
    /** Cover of the album playing (albumId to bitmap). */
    val cover: StateFlow<Pair<String, Bitmap>?> = _cover.asStateFlow()
    private val _shortcuts = MutableStateFlow<List<Shortcut>>(emptyList())
    val shortcuts: StateFlow<List<Shortcut>> = _shortcuts.asStateFlow()
    @Volatile private var shortcutsAt = 0L
    @Volatile private var started = false

    fun publishLocal(np: NowPlaying?, castName: String? = null) {
        local.value = np?.let { WidgetStates.fromNowPlaying(it, castName) }
    }

    /** Favourite changes made this session (track id to starred), for the notification's Like button. */
    val starChanges: StateFlow<Map<String, Boolean>> get() = stars

    /** A favourite toggled since the song's media item was built, or null. */
    fun starOverride(trackId: String): Boolean? = stars.value[trackId]

    fun setStarred(trackId: String, starred: Boolean) {
        stars.value = stars.value + (trackId to starred)
    }

    fun start(ctx: Context, c: AppContainer) {
        if (started) return
        started = true
        val app = ctx.applicationContext
        val prefs = app.getSharedPreferences("widget_last", Context.MODE_PRIVATE)
        remembered.value = prefs.getString("trackId", null)?.let {
            PlayingInfo(it, prefs.getString("title", "").orEmpty(), prefs.getString("artist", "").orEmpty(),
                prefs.getString("albumId", null), prefs.getBoolean("starred", false), live = false)
        }
        val remote = c.player.state.map { np: NowPlaying -> np.remote?.let { _ -> WidgetStates.fromNowPlaying(np) } }
        c.scope.launch {
            combine(remote, local, remembered, stars) { r, l, m, s -> WidgetStates.withStar(WidgetStates.choose(r, l, m), s) }
                .distinctUntilChanged()
                .collect { _state.value = it }
        }
        // Remember the last song so a widget shows it after a restart.
        c.scope.launch {
            local.collect { l ->
                if (l?.hasTrack == true && l.trackId != remembered.value?.trackId) {
                    prefs.edit().putString("trackId", l.trackId).putString("title", l.title).putString("artist", l.artist)
                        .putString("albumId", l.albumId).putBoolean("starred", l.starred).apply()
                }
            }
        }
        c.scope.launch {
            state.map { it.albumId }.distinctUntilChanged().collect { id ->
                if (id == null) { _cover.value = null; return@collect }
                if (_cover.value?.first != id) _cover.value = loadArt(app, MediaItems.artUrl(id, 256), 256)?.let { id to it }
            }
        }
        @OptIn(kotlinx.coroutines.FlowPreview::class)
        c.scope.launch {
            combine(state, cover, shortcuts) { s, cv, sc -> Triple(s, cv?.first, sc.map { it.item.id }) }
                .distinctUntilChanged()
                .debounce(150) // a track change sends a few events in a row
                .collect { if (hasWidgets(app)) runCatching { NowPlayingWidget().updateAll(app) } }
        }
    }

    suspend fun hasWidgets(ctx: Context): Boolean = runCatching {
        GlanceAppWidgetManager(ctx).getGlanceIds(NowPlayingWidget::class.java).isNotEmpty()
    }.getOrDefault(false)

    /** Pinned items first, then recently played albums; refreshed at most every 15 minutes. */
    fun refreshShortcuts(ctx: Context, c: AppContainer, force: Boolean = false) {
        val now = System.currentTimeMillis()
        if (!force && now - shortcutsAt < 15 * 60_000L) return
        shortcutsAt = now
        c.scope.launch {
            val pins = runCatching { c.api.pins() }.getOrNull().orEmpty()
            val recent = if (pins.size >= 3) emptyList() else
                runCatching { FeedRepository.recentAlbums(c.api.smartList("recently_played", limit = 40)) }.getOrNull().orEmpty()
            val items = pickShortcuts(pins, recent)
            if (items.isEmpty()) { shortcutsAt = 0; return@launch }
            _shortcuts.value = items.map { Shortcut(it, it.artUrl?.let { u -> loadArt(ctx, RoutedCallFactory.virtual(withSize(u, 128)), 128) }) }
        }
    }

    fun pickShortcuts(pins: List<ShelfItem>, recent: List<ShelfItem>, max: Int = 3): List<ShelfItem> =
        (pins + recent).distinctBy { it.type to it.id }.filter { it.type != "device" }.take(max)

    private fun withSize(url: String, size: Int) = if ('?' in url) url else "$url?size=$size"

    private suspend fun loadArt(ctx: Context, url: String, px: Int): Bitmap? = runCatching {
        val req = ImageRequest.Builder(ctx).data(url).size(px).allowHardware(false).build()
        (SingletonImageLoader.get(ctx).execute(req) as? SuccessResult)?.image?.toBitmap(px, px)
    }.getOrNull()
}
