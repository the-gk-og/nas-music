package org.nasmusic.app.data.prefs

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import org.nasmusic.app.data.api.AudioFormat

data class AppSettings(
    val favouritesFormat: AudioFormat = AudioFormat.OPUS,
    val storageLimitMb: Int = 8 * 1024,
    /** Hour (0–23) the night sync window opens; it stays open for [SYNC_WINDOW_HOURS]. */
    val syncHour: Int = 3,
    val allowTunnelSync: Boolean = false,
    val streamBitrate: Int = 160,
    /** v2: off by default — stream like Spotify; when on, favourites + mixes download overnight. */
    val offlineFavourites: Boolean = false,
    /** Lyrics text size multiplier (Tt button). */
    val lyricsScale: Float = 1f,
    // ---- v3 Appearance (docs/v3-ux.md §7) ----
    /** off | quick | expressive */
    val animations: String = "quick",
    /** grid | list — default for Library sections without their own choice */
    val defaultView: String = "grid",
    /** big | simple — album/artist/playlist page style */
    val pageStyle: String = "big",
    /** Accent colour as ARGB. */
    val accent: Long = 0xFF4ADE80,
    /** Home shelves in order; a leading '-' hides one. */
    val homeShelves: List<String> = DEFAULT_HOME_SHELVES,
    /** Per Library chip grid/list choice, e.g. "albums" -> "grid". */
    val libraryViews: Map<String, String> = emptyMap(),
) {
    fun viewFor(chip: String) = libraryViews[chip] ?: defaultView
    companion object {
        const val SYNC_WINDOW_HOURS = 2
        val DEFAULT_HOME_SHELVES = listOf("start_radio", "devices", "recent", "continue", "pinned")
        val ACCENTS = listOf(0xFF4ADE80, 0xFF60A5FA, 0xFFF472B6, 0xFFFBBF24, 0xFFA78BFA, 0xFFF87171)
    }
}

private val Context.dataStore by preferencesDataStore("settings")

class SettingsRepository(private val context: Context, scope: CoroutineScope) {
    private val favFormat = stringPreferencesKey("favourites_format")
    private val limit = intPreferencesKey("storage_limit_mb")
    private val hour = intPreferencesKey("sync_hour")
    private val tunnelSync = booleanPreferencesKey("allow_tunnel_sync")
    private val bitrate = intPreferencesKey("stream_bitrate")
    private val offline = booleanPreferencesKey("offline_favourites")
    private val lyricsScaleKey = androidx.datastore.preferences.core.floatPreferencesKey("lyrics_scale")
    private val animationsKey = stringPreferencesKey("ui_animations")
    private val defaultViewKey = stringPreferencesKey("ui_default_view")
    private val pageStyleKey = stringPreferencesKey("ui_page_style")
    private val accentKey = androidx.datastore.preferences.core.longPreferencesKey("ui_accent")
    private val homeShelvesKey = stringPreferencesKey("ui_home_shelves")
    private val libraryViewsKey = stringPreferencesKey("ui_library_views")

    private fun read(p: androidx.datastore.preferences.core.Preferences) = AppSettings(
            favouritesFormat = if (p[favFormat] == "flac") AudioFormat.FLAC else AudioFormat.OPUS,
            storageLimitMb = p[limit] ?: AppSettings().storageLimitMb,
            syncHour = p[hour] ?: 3,
            allowTunnelSync = p[tunnelSync] ?: false,
            streamBitrate = p[bitrate] ?: 160,
            offlineFavourites = p[offline] ?: false,
            lyricsScale = p[lyricsScaleKey] ?: 1f,
            animations = p[animationsKey] ?: "quick",
            defaultView = p[defaultViewKey] ?: "grid",
            pageStyle = p[pageStyleKey] ?: "big",
            accent = p[accentKey] ?: 0xFF4ADE80,
            homeShelves = p[homeShelvesKey]?.split(',')?.filter { it.isNotBlank() }?.let(::withNewShelves) ?: AppSettings.DEFAULT_HOME_SHELVES,
            libraryViews = p[libraryViewsKey]?.split(',')?.mapNotNull { e -> e.split('=').takeIf { it.size == 2 }?.let { it[0] to it[1] } }?.toMap().orEmpty(),
        )

    /** Shelves added in a later version are appended (visible) to a saved order. */
    private fun withNewShelves(saved: List<String>): List<String> {
        val known = saved.map { it.removePrefix("-") }.toSet()
        return saved + AppSettings.DEFAULT_HOME_SHELVES.filter { it !in known }
    }

    val settings: StateFlow<AppSettings> = context.dataStore.data.map(::read).stateIn(scope, SharingStarted.Eagerly, AppSettings())

    /** Reads from disk; use in workers, where [settings] may still hold defaults right after process start. */
    suspend fun current(): AppSettings = read(context.dataStore.data.first())

    suspend fun setFavouritesFormat(f: AudioFormat) = context.dataStore.edit { it[favFormat] = f.wire }
    suspend fun setStorageLimitMb(mb: Int) = context.dataStore.edit { it[limit] = mb.coerceAtLeast(256) }
    suspend fun setSyncHour(h: Int) = context.dataStore.edit { it[hour] = h.mod(24) }
    suspend fun setAllowTunnelSync(on: Boolean) = context.dataStore.edit { it[tunnelSync] = on }
    suspend fun setAnimations(v: String) = context.dataStore.edit { it[animationsKey] = v }
    suspend fun setDefaultView(v: String) = context.dataStore.edit { it[defaultViewKey] = v }
    suspend fun setPageStyle(v: String) = context.dataStore.edit { it[pageStyleKey] = v }
    suspend fun setAccent(v: Long) = context.dataStore.edit { it[accentKey] = v }
    suspend fun setHomeShelves(v: List<String>) = context.dataStore.edit { it[homeShelvesKey] = v.joinToString(",") }
    suspend fun setLibraryView(chip: String, view: String) = context.dataStore.edit { p ->
        val cur = p[libraryViewsKey]?.split(',')?.mapNotNull { e -> e.split('=').takeIf { it.size == 2 }?.let { it[0] to it[1] } }?.toMap().orEmpty()
        p[libraryViewsKey] = (cur + (chip to view)).entries.joinToString(",") { "${it.key}=${it.value}" }
    }
    suspend fun setOfflineFavourites(on: Boolean) = context.dataStore.edit { it[offline] = on }
    suspend fun setLyricsScale(s: Float) = context.dataStore.edit { it[lyricsScaleKey] = s.coerceIn(0.7f, 2f) }
    suspend fun setStreamBitrate(kbps: Int) = context.dataStore.edit { it[bitrate] = kbps }
}
