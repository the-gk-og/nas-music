package org.nasmusic.wear.ui.screens

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Speaker
import androidx.compose.material.icons.filled.Watch
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.vector.ImageVector
import org.nasmusic.app.data.api.Track
import org.nasmusic.wear.WearContainer
import org.nasmusic.wear.ui.ListScreen
import org.nasmusic.wear.ui.NavChip
import org.nasmusic.wear.ui.header
import org.nasmusic.wear.ui.loading
import org.nasmusic.wear.ui.message

fun kindIcon(kind: String): ImageVector = when (kind) {
    "watch" -> Icons.Filled.Watch
    "android" -> Icons.Filled.PhoneAndroid
    "desktop" -> Icons.Filled.Computer
    "web" -> Icons.Filled.Language
    else -> Icons.Filled.Speaker
}

/** Connect devices: tap one to move playback there (including this watch). */
@Composable
fun DevicesScreen(c: WearContainer, done: () -> Unit) {
    val devices by c.connect.devices.collectAsState()
    val state by c.connect.state.collectAsState()
    val me by c.connect.myDeviceId.collectAsState()
    val connected by c.connect.connected.collectAsState()
    ListScreen {
        header("Play on")
        if (!connected) message("Connecting to the server…")
        val list = devices.filter { it.canPlay && (it.online || it.id == me) }.sortedBy { if (it.id == me) 0 else 1 }
        list.forEach { d ->
            item {
                val active = d.id == state?.activeDeviceId
                NavChip(
                    label = if (d.id == me) "This watch" else d.name,
                    secondary = when {
                        active && state?.playing == true -> "Playing"
                        active -> "Active"
                        d.id == me -> "Bluetooth headphones"
                        else -> d.kind
                    },
                    icon = kindIcon(d.kind),
                    primary = active,
                ) {
                    if (!active) c.player.transferTo(d.id)
                    done()
                }
            }
        }
        if (connected && list.isEmpty()) message("No other devices are online.")
    }
}

/** The queue of whatever is playing (the server queue when remote, the watch's own list when local). */
@Composable
fun QueueScreen(c: WearContainer, done: () -> Unit) {
    val ui by c.player.ui.collectAsState()
    var tracks by remember { mutableStateOf<List<Track>?>(null) }
    LaunchedEffect(ui.isRemote, ui.queueId) {
        tracks = if (ui.isRemote) {
            ui.queueId?.let { id -> runCatching { c.api.queue(id).tracks }.getOrNull() } ?: emptyList()
        } else c.player.localQueue()
    }
    ListScreen {
        header(if (ui.isRemote) "Queue on ${ui.remoteDevice?.name}" else "Queue")
        val t = tracks
        when {
            t == null -> loading()
            t.isEmpty() -> message("Nothing queued.")
            else -> t.forEachIndexed { i, track ->
                item {
                    NavChip(if (track.isExplicit) "🅴 ${track.title}" else track.title, track.artist, primary = i == ui.index) { c.player.jumpTo(i); done() }
                }
            }
        }
    }
}
