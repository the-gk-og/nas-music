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
) {
    companion object { const val SYNC_WINDOW_HOURS = 2 }
}

private val Context.dataStore by preferencesDataStore("settings")

class SettingsRepository(private val context: Context, scope: CoroutineScope) {
    private val favFormat = stringPreferencesKey("favourites_format")
    private val limit = intPreferencesKey("storage_limit_mb")
    private val hour = intPreferencesKey("sync_hour")
    private val tunnelSync = booleanPreferencesKey("allow_tunnel_sync")
    private val bitrate = intPreferencesKey("stream_bitrate")

    val settings: StateFlow<AppSettings> = context.dataStore.data.map { p ->
        AppSettings(
            favouritesFormat = if (p[favFormat] == "flac") AudioFormat.FLAC else AudioFormat.OPUS,
            storageLimitMb = p[limit] ?: AppSettings().storageLimitMb,
            syncHour = p[hour] ?: 3,
            allowTunnelSync = p[tunnelSync] ?: false,
            streamBitrate = p[bitrate] ?: 160,
        )
    }.stateIn(scope, SharingStarted.Eagerly, AppSettings())

    suspend fun setFavouritesFormat(f: AudioFormat) = context.dataStore.edit { it[favFormat] = f.wire }
    suspend fun setStorageLimitMb(mb: Int) = context.dataStore.edit { it[limit] = mb.coerceAtLeast(256) }
    suspend fun setSyncHour(h: Int) = context.dataStore.edit { it[hour] = h.mod(24) }
    suspend fun setAllowTunnelSync(on: Boolean) = context.dataStore.edit { it[tunnelSync] = on }
    suspend fun setStreamBitrate(kbps: Int) = context.dataStore.edit { it[bitrate] = kbps }
}
