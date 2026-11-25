package org.homebeat.app.ui.screens

import android.content.Intent
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.PersonRemove
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.google.zxing.BarcodeFormat
import com.journeyapps.barcodescanner.BarcodeEncoder
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.homebeat.app.AppContainer
import org.homebeat.app.data.api.JamDecision
import org.homebeat.app.data.api.JamLinkBody
import org.homebeat.app.data.api.JamSettingsBody
import org.homebeat.app.data.api.JamSnapshot
import org.homebeat.app.ui.LocalUi

private val CAPS = listOf<Int?>(1, 2, 3, 5, 10, 20, null)

/**
 * Jam (listen together), host side: start one, show the join QR / share the link, approve requests, remove
 * songs, kick guests, end. Guests use the server's web page (no app). Refreshes every 3 s while open; the
 * queue itself reaches the player through the normal Connect `queues` messages.
 */
@Composable
fun JamSheet(c: AppContainer, onDismiss: () -> Unit) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var snap by remember { mutableStateOf<JamSnapshot?>(null) }
    var loaded by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var lan by remember { mutableStateOf(false) }
    var confirmEnd by remember { mutableStateOf(false) }
    var approval by remember { mutableStateOf(false) }
    var playNext by remember { mutableStateOf(false) }
    var cap by remember { mutableStateOf<Int?>(5) }
    var capMenu by remember { mutableStateOf(false) }

    fun apply(s: JamSnapshot?) {
        snap = s
        s?.jam?.settings?.let { approval = it.approval; playNext = it.allowPlayNext; cap = it.maxPerGuest }
    }

    fun act(block: suspend () -> JamSnapshot?) {
        if (busy) return
        busy = true
        scope.launch {
            runCatching { block() }.onSuccess { apply(it) }.onFailure { ui.toast = it.message ?: "Jam request failed" }
            busy = false
        }
    }

    LaunchedEffect(Unit) {
        while (true) {
            val id = snap?.jam?.id
            runCatching { if (id == null) c.jam.current().body() else c.jam.get(id) }
                .onSuccess { apply(it) }
                .onFailure { e -> if (id != null && (e as? retrofit2.HttpException)?.code() in setOf(404, 410)) apply(null) } // ended elsewhere
            loaded = true
            delay(3000)
        }
    }

    fun saveSettings() {
        val id = snap?.jam?.id ?: return
        act { c.jam.settings(id, JamSettingsBody(approval, playNext, cap).json()) }
    }

    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(Modifier.fillMaxSize().padding(16.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Jam", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f))
                    IconButton(onClick = onDismiss) { Icon(Icons.Default.Close, "Close") }
                }
                val s = snap
                LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    if (!loaded) {
                        item { Text("Loading…") }
                    } else if (s == null) {
                        item {
                            Text("Friends open a link on their phone (no account or app) and add songs to the queue you're " +
                                "playing. The music keeps playing wherever you play it.")
                        }
                    } else {
                        item {
                            val link = if (lan) s.jam.lanJoinUrl else s.jam.joinUrl
                            val qr = remember(link) {
                                link?.let { runCatching { BarcodeEncoder().encodeBitmap(it, BarcodeFormat.QR_CODE, 640, 640) }.getOrNull() }
                            }
                            Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                                if (s.jam.joinCode == null) {
                                    Text("The join link is closed: nobody new can join.")
                                } else {
                                    qr?.let { Image(it.asImageBitmap(), "Join QR code", Modifier.size(240.dp).background(Color.White).padding(8.dp)) }
                                    Text(link ?: "No link for this network", fontFamily = FontFamily.Monospace,
                                        style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 6.dp))
                                    if (lan) Text("Home Wi-Fi only; guests will see a certificate warning.", style = MaterialTheme.typography.bodySmall)
                                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                        OutlinedButton(enabled = link != null, onClick = {
                                            val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, link)
                                            context.startActivity(Intent.createChooser(send, "Share Jam link"))
                                        }) { Text("Share") }
                                        OutlinedButton(onClick = { lan = !lan }) { Text(if (lan) "Public link" else "LAN link") }
                                    }
                                }
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    TextButton(enabled = !busy, onClick = { act { c.jam.link(s.jam.id, JamLinkBody()) } }) { Text("New link") }
                                    if (s.jam.joinCode != null) {
                                        TextButton(enabled = !busy, onClick = { act { c.jam.link(s.jam.id, JamLinkBody(close = true)) } }) { Text("Close link") }
                                    }
                                }
                            }
                        }
                    }
                    if (loaded) {
                        item { Section("Settings") }
                        item { SwitchRow("Approve songs before they're added", approval) { approval = it; saveSettings() } }
                        item { SwitchRow("Guests may use \"play next\"", playNext) { playNext = it; saveSettings() } }
                        item {
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                Text("Max songs waiting per guest", Modifier.weight(1f))
                                Column {
                                    TextButton(onClick = { capMenu = true }) { Text(cap?.toString() ?: "No limit") }
                                    DropdownMenu(capMenu, { capMenu = false }) {
                                        CAPS.forEach { n ->
                                            DropdownMenuItem({ Text(n?.toString() ?: "No limit") }, { capMenu = false; cap = n; saveSettings() })
                                        }
                                    }
                                }
                            }
                        }
                    }
                    if (s != null) {
                        if (s.pending.isNotEmpty()) {
                            item { Section("Waiting for approval") }
                            items(s.pending, key = { "p" + it.id }) { p ->
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    TrackText(p.track?.title ?: p.trackId, "${p.track?.artist.orEmpty()} · ${p.by?.name.orEmpty()}" +
                                        if (p.playNext) " · play next" else "", Modifier.weight(1f))
                                    IconButton(enabled = !busy, onClick = { act { c.jam.decide(s.jam.id, p.id, JamDecision(true)) } }) { Icon(Icons.Default.Check, "Approve") }
                                    IconButton(enabled = !busy, onClick = { act { c.jam.decide(s.jam.id, p.id, JamDecision(false)) } }) { Icon(Icons.Default.Close, "Reject") }
                                }
                            }
                        }
                        item { Section("Up next") }
                        s.nowPlaying?.let { np ->
                            item { TrackText("▶ ${np.track.title}", "${np.track.artist} · ${np.addedBy.name}", Modifier.fillMaxWidth()) }
                        }
                        if (s.upcoming.isEmpty()) item { Text("Nothing queued yet.", style = MaterialTheme.typography.bodySmall) }
                        items(s.upcoming, key = { "e${it.position}:${it.track.id}" }) { e ->
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                val votes = if (e.votes.up + e.votes.down > 0) " · 👍 ${e.votes.up} 👎 ${e.votes.down}" else ""
                                TrackText(e.track.title, "${e.track.artist} · ${if (e.addedBy.isHost) "you" else e.addedBy.name}$votes", Modifier.weight(1f))
                                IconButton(enabled = !busy, onClick = { act { c.jam.remove(s.jam.id, e.position, e.track.id) } }) { Icon(Icons.Default.Delete, "Remove from queue") }
                            }
                        }
                        item { Section("Guests (${s.guests.size})") }
                        if (s.guests.isEmpty()) item { Text("No one has joined yet.", style = MaterialTheme.typography.bodySmall) }
                        items(s.guests, key = { "g" + it.id }) { g ->
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                TrackText(g.name, if (g.isMember) "signed-in user" else "guest", Modifier.weight(1f))
                                IconButton(enabled = !busy, onClick = { act { c.jam.kick(s.jam.id, g.id) } }) { Icon(Icons.Default.PersonRemove, "Remove ${g.name}") }
                            }
                        }
                    }
                }
                HorizontalDivider()
                Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    if (snap == null) {
                        Button(enabled = loaded && !busy, onClick = { act { c.jam.start(JamSettingsBody(approval, playNext, cap).json()) } }) { Text("Start a Jam") }
                    } else {
                        OutlinedButton(enabled = !busy, onClick = { confirmEnd = true }) { Text("End Jam", color = MaterialTheme.colorScheme.error) }
                    }
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = onDismiss) { Text("Close") }
                }
            }
        }
    }

    if (confirmEnd) {
        AlertDialog(
            onDismissRequest = { confirmEnd = false },
            title = { Text("End the Jam?") },
            text = { Text("Everyone gets disconnected. Your queue stays as it is.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmEnd = false
                    val id = snap?.jam?.id ?: return@TextButton
                    act { c.jam.end(id); ui.toast = "Jam ended"; null }
                }) { Text("End Jam") }
            },
            dismissButton = { TextButton(onClick = { confirmEnd = false }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun Section(title: String) {
    Text(title.uppercase(), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 16.dp, bottom = 4.dp))
}

@Composable
private fun SwitchRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f))
        Spacer(Modifier.width(8.dp))
        Switch(checked, onChange)
    }
}

@Composable
private fun TrackText(title: String, sub: String, modifier: Modifier) {
    Column(modifier.padding(vertical = 4.dp)) {
        Text(title, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text(sub, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}
