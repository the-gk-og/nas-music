package org.nasmusic.wear

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Watch-local settings (not mirrored to /me/settings). */
data class WearPrefs(
    /** Offline downloads only on Wi-Fi (default). Off = also over the phone's Bluetooth proxy / LTE. */
    val downloadWifiOnly: Boolean = true,
    /** Most the offline music may take, in MB. Also capped by free space minus [RESERVE_MB]. */
    val storageCapMb: Int = 2048,
    /** Opus bitrate for streaming and downloads. The watch is short on bandwidth and storage. */
    val opusKbps: Int = 96,
) {
    companion object {
        const val RESERVE_MB = 500L
        val CAP_CHOICES = listOf(512, 1024, 2048, 4096, 8192)
        val KBPS_CHOICES = listOf(64, 96, 128)
    }
}

class WearSettings(context: Context) {
    private val prefs = context.getSharedPreferences("wear_settings", Context.MODE_PRIVATE)
    private val _state = MutableStateFlow(load())
    val state: StateFlow<WearPrefs> = _state.asStateFlow()
    val current: WearPrefs get() = _state.value

    private fun load() = WearPrefs(
        downloadWifiOnly = prefs.getBoolean("wifiOnly", true),
        storageCapMb = prefs.getInt("capMb", 2048),
        opusKbps = prefs.getInt("kbps", 96),
    )

    fun update(f: (WearPrefs) -> WearPrefs) {
        val n = f(_state.value)
        prefs.edit()
            .putBoolean("wifiOnly", n.downloadWifiOnly)
            .putInt("capMb", n.storageCapMb)
            .putInt("kbps", n.opusKbps)
            .apply()
        _state.value = n
    }
}
