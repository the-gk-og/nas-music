package org.nasmusic.app.ui.screens

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.ListenBrainzConnect
import org.nasmusic.app.data.api.NewMusic
import org.nasmusic.app.data.api.ScrobbleService
import org.nasmusic.app.data.api.ScrobbleSettingsBody
import org.nasmusic.app.data.api.ScrobbleStatus
import org.nasmusic.app.data.api.UserSettings
import org.nasmusic.app.playback.PlaySource
import org.nasmusic.app.ui.Loading
import org.nasmusic.app.ui.LocalUi
import org.nasmusic.app.ui.Message
import org.nasmusic.app.ui.ScreenTitle
import org.nasmusic.app.ui.SongList
import retrofit2.HttpException

/** Display names for the api 0.7 scrobbling services. */
internal val SCROBBLE_NAMES = mapOf("listenbrainz" to "ListenBrainz", "lastfm" to "Last.fm")

/** One-line status (unit-tested in ScrobbleTextTest). */
internal fun scrobbleLine(s: ScrobbleService, whenText: (String) -> String = { it.take(16).replace('T', ' ') }): String {
    if (!s.connected) return if (s.available) "Not connected" else "Not set up on this server"
    if (s.needsReauth) return "${SCROBBLE_NAMES[s.service]} rejected the connection — reconnect (${s.pending} waiting)"
    if (!s.enabled) return "Connected as ${s.username ?: "?"} · paused"
    val parts = mutableListOf("Connected as ${s.username ?: "?"}")
    parts += s.lastScrobbleAt?.let { "last scrobble ${whenText(it)}" } ?: "no scrobbles yet"
    if (s.pending > 0) parts += "${s.pending} waiting"
    if (s.failed > 0) parts += "${s.failed} failed"
    return parts.joinToString(" · ")
}

/** Settings → Scrobbling + Notifications. */
@Composable
fun ScrobbleSettings(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var st by remember { mutableStateOf<ScrobbleStatus?>(null) }
    var unsupported by remember { mutableStateOf(false) }
    var settings by remember { mutableStateOf<Map<String, kotlinx.serialization.json.JsonElement>>(emptyMap()) }
    var lbToken by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var lfmWaiting by remember { mutableStateOf(false) }
    var confirmDisconnect by remember { mutableStateOf<String?>(null) }
    var refreshTick by remember { mutableIntStateOf(0) }
    val deviceKey = c.secureStore.config.collectAsState().value?.deviceId?.let { "device.$it.scrobble" }

    LaunchedEffect(refreshTick) {
        runCatching { c.api.scrobble() }
            .onSuccess { st = it }
            .onFailure { if (it is HttpException && it.code() == 404) unsupported = true }
        runCatching { c.api.mySettings().settings }.onSuccess { settings = it }
        if (st?.import?.state == "running") { delay(3000); refreshTick++ }
    }

    fun act(block: suspend () -> ScrobbleStatus, ok: String? = null) = scope.launch {
        busy = true
        try {
            st = block()
            ok?.let { ui.toast = it }
        } catch (e: HttpException) {
            ui.toast = when (e.code()) {
                400 -> "That token was rejected"
                409 -> "Not connected"
                503 -> "Not set up on this server"
                else -> "Server error (${e.code()})"
            }
        } catch (e: Exception) {
            ui.toast = "Couldn't reach the server"
        } finally {
            busy = false
        }
    }

    fun putSetting(key: String, value: kotlinx.serialization.json.JsonElement) = scope.launch {
        runCatching { c.api.putMySettings(UserSettings(mapOf(key to value))) }
            .onSuccess { settings = settings.toMutableMap().apply { if (value is JsonNull) remove(key) else put(key, value) } }
            .onFailure { ui.toast = "Couldn't save" }
    }

    fun connectLastfm() = scope.launch {
        busy = true
        try {
            val auth = c.api.lastfmConnect()
            runCatching {
                c.context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(auth.authUrl)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }.onFailure { ui.toast = "No browser to open Last.fm" }
            lfmWaiting = true
            val deadline = System.currentTimeMillis() + 5 * 60_000
            while (lfmWaiting && System.currentTimeMillis() < deadline) {
                delay(3000)
                try {
                    st = c.api.lastfmComplete()
                    lfmWaiting = false
                    ui.toast = "Last.fm connected"
                } catch (e: HttpException) {
                    if (e.code() != 409) { lfmWaiting = false; ui.toast = "Last.fm: ${if (e.code() == 410) "the request expired, try again" else "error ${e.code()}"}" }
                } catch (_: Exception) { /* offline for a moment: keep polling */ }
            }
            if (lfmWaiting) { lfmWaiting = false; ui.toast = "Last.fm was not approved in time" }
        } catch (e: HttpException) {
            ui.toast = if (e.code() == 503) "Last.fm is not set up on this server" else "Last.fm error (${e.code()})"
        } catch (e: Exception) {
            ui.toast = "Couldn't reach the server"
        } finally {
            busy = false
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        val status = st
        when {
            unsupported -> Text("This server does not support scrobbling yet.", style = MaterialTheme.typography.bodySmall)
            status == null -> Text("Loading…", style = MaterialTheme.typography.bodySmall)
            else -> {
                Text(
                    "Your server sends what you play — on any device, including speakers — to ListenBrainz or Last.fm. " +
                        "Off until you connect an account. A song counts after half of it (or 4 minutes).",
                    style = MaterialTheme.typography.bodySmall,
                )
                status.services.forEach { s ->
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Column(Modifier.weight(1f)) {
                                    Text(SCROBBLE_NAMES[s.service] ?: s.service, style = MaterialTheme.typography.titleSmall)
                                    Text(scrobbleLine(s), style = MaterialTheme.typography.bodySmall,
                                        color = if (s.needsReauth) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
                                    if (s.connected && s.lastError != null) {
                                        Text("Last error: ${s.lastError}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                                    }
                                }
                                if (s.connected) Switch(s.enabled, { on -> act({ c.api.putScrobble(s.service, ScrobbleSettingsBody(enabled = on)) }) }, enabled = !busy)
                            }
                            if (s.connected) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text("Show \"now playing\"", Modifier.weight(1f))
                                    Switch(s.nowPlaying, { on -> act({ c.api.putScrobble(s.service, ScrobbleSettingsBody(nowPlaying = on)) }) }, enabled = !busy)
                                }
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    if (s.failed > 0) OutlinedButton(enabled = !busy, onClick = { act({ c.api.retryScrobbles() }, "Retrying") }) { Text("Retry failed") }
                                    if (s.needsReauth && s.service == "lastfm") OutlinedButton(enabled = !busy, onClick = { connectLastfm() }) { Text("Reconnect") }
                                    TextButton(enabled = !busy, onClick = { confirmDisconnect = s.service }) { Text("Disconnect") }
                                }
                            }
                            if (s.service == "listenbrainz" && (!s.connected || s.needsReauth)) {
                                OutlinedTextField(
                                    lbToken, { lbToken = it }, Modifier.fillMaxWidth(), singleLine = true,
                                    label = { Text("ListenBrainz user token") },
                                    visualTransformation = PasswordVisualTransformation(),
                                )
                                Text("Copy it from listenbrainz.org/settings.", style = MaterialTheme.typography.bodySmall)
                                Button(enabled = !busy && lbToken.isNotBlank(), onClick = {
                                    val t = lbToken.trim()
                                    act({ c.api.connectListenBrainz(ListenBrainzConnect(t)).also { lbToken = "" } }, "ListenBrainz connected")
                                }) { Text("Connect") }
                            }
                            if (s.service == "lastfm" && !s.connected) {
                                if (s.available) {
                                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                        Button(enabled = !busy && !lfmWaiting, onClick = { connectLastfm() }) {
                                            Text(if (lfmWaiting) "Waiting for approval…" else "Connect Last.fm")
                                        }
                                        if (lfmWaiting) TextButton(onClick = { lfmWaiting = false }) { Text("Cancel") }
                                    }
                                } else {
                                    Text("Your server admin needs to add a Last.fm API key and secret first.", style = MaterialTheme.typography.bodySmall)
                                }
                            }
                        }
                    }
                }
                if (deviceKey != null && status.services.any { it.connected }) {
                    val on = (settings[deviceKey] as? JsonPrimitive)?.content != "false"
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("Scrobble plays from this phone")
                            Text("Turn off for a shared or kids' phone.", style = MaterialTheme.typography.bodySmall)
                        }
                        Switch(on, { v -> putSetting(deviceKey, if (v) JsonNull else JsonPrimitive(false)) })
                    }
                }
                if (status.services.any { it.service == "listenbrainz" && it.connected }) {
                    Text("Import ListenBrainz history", style = MaterialTheme.typography.titleSmall)
                    Text(
                        "Copies your past listens of songs in this library into your history here, so your mixes learn from them. " +
                            "Imported listens are marked, can be removed again, and are never scrobbled back.",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    status.import?.let { imp ->
                        Text(
                            when (imp.state) {
                                "running" -> "Importing… ${imp.scanned ?: 0} listens read"
                                "error" -> "Import failed: ${imp.error.orEmpty()}"
                                else -> "Last import: ${imp.scanned ?: 0} read, ${imp.imported ?: 0} imported, ${imp.alreadyKnown ?: 0} already here, ${imp.unmatched ?: 0} not in the library"
                            },
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                    val running = status.import?.state == "running"
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(enabled = !busy && !running, onClick = {
                            act({ c.api.importListenBrainz().also { refreshTick++ } })
                        }) { Text("Import") }
                        if (status.importedEvents > 0) {
                            TextButton(enabled = !busy && !running, onClick = {
                                act({ c.api.removeImported().also { ui.toast = "Removed ${it.removed ?: 0} imported listens" } })
                            }) { Text("Remove ${status.importedEvents} imported") }
                        }
                    }
                }
            }
        }

        Text("Notifications", style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.padding(top = 8.dp))
        val notifyOn = (settings["notify.newMusic"] as? JsonPrimitive)?.content == "true"
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("New music added")
                Text("One notification when a library scan adds songs or albums. Checked about once an hour, no Google services needed.",
                    style = MaterialTheme.typography.bodySmall)
            }
            Switch(notifyOn, { v -> putSetting("notify.newMusic", JsonPrimitive(v)) })
        }
        OutlinedButton(onClick = { ui.go("list/new") }) { Text("New this week") }
    }

    confirmDisconnect?.let { svc ->
        AlertDialog(
            onDismissRequest = { confirmDisconnect = null },
            title = { Text("Disconnect ${SCROBBLE_NAMES[svc]}?") },
            text = { Text("Plays will no longer be scrobbled there. Unsent scrobbles are dropped.") },
            confirmButton = { TextButton(onClick = { confirmDisconnect = null; act({ c.api.disconnectScrobble(svc) }) }) { Text("Disconnect") } },
            dismissButton = { TextButton(onClick = { confirmDisconnect = null }) { Text("Cancel") } },
        )
    }
}

/** "New this week" (GET /library/new) plus the latest notifications; opened from Library or a notification. */
@Composable
fun NewMusicScreen(c: AppContainer) {
    val ui = LocalUi.current
    var data by remember { mutableStateOf<NewMusic?>(null) }
    var failed by remember { mutableStateOf(false) }
    var tab by remember { mutableIntStateOf(0) }
    val notes by c.notifications.items.collectAsState()
    LaunchedEffect(Unit) {
        data = runCatching { c.api.newMusic(7) }.onFailure { failed = true }.getOrNull()
        c.notifications.load()
        c.notifications.markAllRead()
    }
    Column(Modifier.fillMaxSize()) {
        ScreenTitle("New this week")
        notes.firstOrNull()?.let { n ->
            Card(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp)) {
                Column(Modifier.padding(12.dp)) {
                    Text(n.title, style = MaterialTheme.typography.titleSmall)
                    if (n.body.isNotBlank()) Text(n.body, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        val d = data
        when {
            failed -> Message("New music needs a connection to the server.")
            d == null -> Loading()
            d.albums.isEmpty() && d.tracks.isEmpty() -> Message("Nothing new in the last ${d.days} days.")
            else -> {
                TabRow(selectedTabIndex = tab) {
                    Tab(tab == 0, { tab = 0 }, text = { Text("Albums (${d.albums.size})") })
                    Tab(tab == 1, { tab = 1 }, text = { Text("Songs (${d.tracks.size})") })
                }
                if (tab == 0) {
                    if (d.albums.isEmpty()) Message("No new albums — see Songs.")
                    else AlbumGrid(d.albums) { ui.go("album/${it.id}") }
                } else SongList(c, d.tracks, "New this week", PlaySource.LIBRARY, empty = "No songs added to existing albums.")
            }
        }
    }
}
