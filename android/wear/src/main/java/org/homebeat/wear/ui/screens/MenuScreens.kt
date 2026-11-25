package org.homebeat.wear.ui.screens

import android.text.format.Formatter
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Logout
import androidx.compose.material.icons.automirrored.filled.PlaylistPlay
import androidx.compose.material.icons.filled.Album
import androidx.compose.material.icons.filled.DownloadForOffline
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Bedtime
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Speaker
import androidx.compose.material.icons.filled.Sync
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.wear.compose.material.Switch
import androidx.wear.compose.material.Text
import androidx.wear.compose.material.ToggleChip
import org.homebeat.app.net.Route
import org.homebeat.wear.WearContainer
import org.homebeat.wear.WearPrefs
import org.homebeat.wear.offline.OfflineSyncWorker
import org.homebeat.wear.sleep.SleepWatchService
import org.homebeat.wear.sleep.label
import org.homebeat.wear.sleep.rememberSleepControls
import org.homebeat.wear.ui.ListScreen
import org.homebeat.wear.ui.NavChip
import org.homebeat.wear.ui.header
import org.homebeat.wear.ui.message

@Composable
fun HomeMenu(c: WearContainer, go: (String) -> Unit) {
    val ui by c.player.ui.collectAsState()
    val route by c.router.route.collectAsState()
    val prefs by c.settings.state.collectAsState()
    val sleep by c.sleep.status.collectAsState()
    val sleepControls = rememberSleepControls(c)
    ListScreen {
        header("Homebeat")
        item {
            val t = ui.track
            NavChip(
                label = t?.title ?: "Now playing",
                secondary = when {
                    t == null -> "Nothing playing"
                    ui.remoteDevice != null -> "on ${ui.remoteDevice?.name}"
                    else -> t.artist
                },
                artAlbumId = t?.albumId?.takeIf { it.isNotBlank() },
                icon = Icons.Filled.MusicNote,
                primary = true,
            ) { go("now") }
        }
        if (route == Route.OFFLINE) message("Offline: downloaded music only")
        if (prefs.sleepEnabled) {
            item {
                val on = c.sleep.watching
                NavChip(
                    if (on) "Stop sleep detection" else "Pause when I fall asleep",
                    sleep.label() ?: if (prefs.sleepSpokenOnly) "Podcasts and audiobooks" else "Anything playing",
                    icon = Icons.Filled.Bedtime,
                ) { sleepControls.toggle() }
            }
        }
        item { NavChip("Home", icon = Icons.Filled.Home) { go("feed") } }
        item { NavChip("Playlists", icon = Icons.AutoMirrored.Filled.PlaylistPlay) { go("playlists") } }
        item { NavChip("Albums", icon = Icons.Filled.Album) { go("albums") } }
        item { NavChip("Downloads", icon = Icons.Filled.DownloadForOffline) { go("downloads") } }
        item { NavChip("Play on…", icon = Icons.Filled.Speaker) { go("devices") } }
        item { NavChip("Settings", icon = Icons.Filled.Settings) { go("settings") } }
    }
}

@Composable
fun DownloadsScreen(c: WearContainer, open: (kind: String, id: String) -> Unit) {
    val lists by c.offline.lists.collectAsState()
    val status by c.offline.status.collectAsState()
    val prefs by c.settings.state.collectAsState()
    var used by remember { mutableStateOf(0L) }
    LaunchedEffect(status) { used = c.offline.usedBytes() }
    ListScreen {
        header("On this watch")
        message(
            "${Formatter.formatShortFileSize(c.context, used)} of ${prefs.storageCapMb / 1024.0}".removeSuffix(".0") + " GB" +
                if (prefs.downloadWifiOnly) " · Wi-Fi only" else "",
        )
        when {
            status.running -> message(status.message ?: "Downloading ${status.done}/${status.total}…")
            status.message != null -> message(status.message!!)
        }
        if (lists.isEmpty()) message("Nothing downloaded. Open a playlist or album and tap \"Download to watch\".")
        lists.forEach { l ->
            item {
                NavChip(l.name, "${c.offline.downloadedCount(l)}/${l.tracks.size} songs", icon = Icons.AutoMirrored.Filled.PlaylistPlay) {
                    open(l.kind, l.id)
                }
            }
        }
        if (lists.isNotEmpty()) item { NavChip("Sync now", icon = Icons.Filled.Sync, enabled = !status.running) { OfflineSyncWorker.runNow(c.context) } }
    }
}

@Composable
fun SettingsScreen(c: WearContainer) {
    val cfg by c.secureStore.config.collectAsState()
    val prefs by c.settings.state.collectAsState()
    val route by c.router.route.collectAsState()
    val sleepControls = rememberSleepControls(c)
    ListScreen {
        header("Settings")
        message(
            "${cfg?.username ?: "?"} @ ${cfg?.name ?: "server"} · " +
                when (route) { Route.LAN -> "home network"; Route.TUNNEL -> "remote"; Route.OFFLINE -> "offline" },
        )
        item {
            ToggleChip(
                checked = prefs.downloadWifiOnly,
                onCheckedChange = { v -> c.settings.update { it.copy(downloadWifiOnly = v) } },
                label = { Text("Download on Wi-Fi only") },
                toggleControl = { Switch(checked = prefs.downloadWifiOnly) },
                modifier = Modifier.fillMaxWidth(),
            )
        }
        item {
            NavChip("Storage limit", "${prefs.storageCapMb} MB · tap to change") {
                c.settings.update { it.copy(storageCapMb = next(WearPrefs.CAP_CHOICES, it.storageCapMb)) }
            }
        }
        item {
            NavChip("Quality", "Opus ${prefs.opusKbps} kbps · tap to change") {
                c.settings.update { it.copy(opusKbps = next(WearPrefs.KBPS_CHOICES, it.opusKbps)) }
            }
        }
        header("Sleep detection")
        item {
            ToggleChip(
                checked = prefs.sleepEnabled,
                onCheckedChange = { v ->
                    c.settings.update { it.copy(sleepEnabled = v) }
                    // Turning it on asks for heart rate now, while you're looking.
                    if (v) sleepControls.requestHeartRate() else SleepWatchService.stop(c.context)
                },
                label = { Text("Pause when I fall asleep") },
                secondaryLabel = { Text("Stillness + lower heart rate") },
                toggleControl = { Switch(checked = prefs.sleepEnabled) },
                modifier = Modifier.fillMaxWidth(),
            )
        }
        if (prefs.sleepEnabled) {
            item {
                ToggleChip(
                    checked = prefs.sleepSpokenOnly,
                    onCheckedChange = { v -> c.settings.update { it.copy(sleepSpokenOnly = v) } },
                    label = { Text("Podcasts and audiobooks only") },
                    secondaryLabel = { Text(if (prefs.sleepSpokenOnly) "Music keeps playing" else "Music pauses too") },
                    toggleControl = { Switch(checked = prefs.sleepSpokenOnly) },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            item {
                ToggleChip(
                    checked = prefs.sleepAutoAtNight,
                    onCheckedChange = { v -> c.settings.update { it.copy(sleepAutoAtNight = v) } },
                    label = { Text("Start by itself at night") },
                    secondaryLabel = { Text("9 pm to 5 am, when you open the app") },
                    toggleControl = { Switch(checked = prefs.sleepAutoAtNight) },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            item {
                ToggleChip(
                    checked = prefs.sleepRewind,
                    onCheckedChange = { v -> c.settings.update { it.copy(sleepRewind = v) } },
                    label = { Text("Go back to where I dozed off") },
                    secondaryLabel = { Text("Podcasts and audiobooks") },
                    toggleControl = { Switch(checked = prefs.sleepRewind) },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
        item { NavChip("Sign out", icon = Icons.AutoMirrored.Filled.Logout) { c.auth.signOut() } }
    }
}

private fun next(choices: List<Int>, cur: Int): Int = choices[(choices.indexOf(cur) + 1) % choices.size]
