package org.nasmusic.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.IconButton
import androidx.compose.material3.Icon
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.Icons
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.clickable
import androidx.compose.foundation.background
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.AudioFormat
import org.nasmusic.app.net.ProbeResult
import org.nasmusic.app.net.Route
import org.nasmusic.app.sync.SyncScheduler

@Composable
fun SettingsScreen(c: AppContainer) {
    val ui = org.nasmusic.app.ui.LocalUi.current
    val scope = rememberCoroutineScope()
    val settings by c.settings.settings.collectAsStateWithLifecycle()
    val config by c.secureStore.config.collectAsStateWithLifecycle()
    val route by c.router.route.collectAsStateWithLifecycle()
    val probe by c.router.lanProbe.collectAsStateWithLifecycle()
    val downloads by c.library.downloads.collectAsStateWithLifecycle(emptyList())
    val usedMb = downloads.sumOf { it.bytes } / (1024 * 1024)
    var confirmUnpair by remember { mutableStateOf(false) }
    var confirmSmartOff by remember { mutableStateOf(false) }

    val cfg = config ?: return
    var lan by remember(cfg) { mutableStateOf(cfg.lanUrl) }
    var tunnel by remember(cfg) { mutableStateOf(cfg.tunnelUrl.orEmpty()) }
    var addrError by remember { mutableStateOf<String?>(null) }
    var cfId by remember(cfg) { mutableStateOf(cfg.cfAccessClientId.orEmpty()) }
    var cfSecret by remember(cfg) { mutableStateOf(cfg.cfAccessClientSecret.orEmpty()) }
    var limit by remember(settings.storageLimitMb) { mutableFloatStateOf(settings.storageLimitMb / 1024f) }
    var hour by remember(settings.syncHour) { mutableFloatStateOf(settings.syncHour.toFloat()) }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Section("Server: ${cfg.name}")
        Text(
            "Route: " + when (route) {
                Route.LAN -> "home network (LAN)"
                Route.TUNNEL -> "tunnel (away from home)"
                Route.OFFLINE -> "offline"
            } + " · LAN probe: " + when (probe) {
                ProbeResult.OK -> "reachable"
                ProbeResult.UNREACHABLE -> "unreachable"
                ProbeResult.WRONG_SERVER -> "a different server answered"
            },
        )
        Text(
            "Using: " + (cfg.baseFor(if (route == Route.OFFLINE) Route.TUNNEL else route)?.toString()?.trimEnd('/') ?: "—"),
            style = MaterialTheme.typography.bodySmall,
        )
        OutlinedTextField(lan, { lan = it }, label = { Text("Home (LAN) address") }, placeholder = { Text("192.168.68.2:8443") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(
            tunnel, { tunnel = it }, label = { Text("Remote address") }, placeholder = { Text("https://music.example.com:8443") },
            supportingText = { Text("Any port works; uses normal certificate checks (no pin).") },
            singleLine = true, modifier = Modifier.fillMaxWidth(),
        )
        addrError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        OutlinedTextField(cfId, { cfId = it }, label = { Text("CF Access client id") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(
            cfSecret, { cfSecret = it }, label = { Text("CF Access client secret") }, singleLine = true,
            visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = {
                addrError = c.pairing.saveAddresses(lan, tunnel, cfId, cfSecret)
                if (addrError == null) { ui.toast = "Addresses saved"; scope.launch { c.router.refresh() } }
            }) { Text("Save addresses") }
            OutlinedButton(onClick = { scope.launch { c.router.refresh() } }) { Text("Re-check route") }
        }
        Text("LAN certificate: ${cfg.certSha256.take(23)}…", style = MaterialTheme.typography.bodySmall)

        HorizontalDivider()
        Section("Streaming quality")
        org.nasmusic.app.ui.StreamQualitySettings(c)

        HorizontalDivider()
        Section("Offline")
        OutlinedButton(onClick = { ui.go("downloads") }) { Text("Downloads (using %d MB)".format(usedMb)) }
        OutlinedButton(onClick = { ui.go("uploads") }) { Text("Upload music to the server") }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Smart downloads")
                Text("Keeps favourites, this week's mixes, recent plays and songs you're likely to want on the phone, " +
                    "within a storage budget. Updates overnight on Wi-Fi while charging; least likely songs make room first.",
                    style = MaterialTheme.typography.bodySmall)
            }
            Switch(settings.smartDownloads, { on ->
                if (on) scope.launch { c.settings.setSmartDownloads(true) }
                else if (downloads.any { !it.isProtected }) confirmSmartOff = true
                else scope.launch { c.settings.setSmartDownloads(false) }
            })
        }
        if (settings.smartDownloads) {
            Text("Storage budget")
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                org.nasmusic.app.sync.StorageBudget.CHOICES.forEach { b ->
                    androidx.compose.material3.FilterChip(settings.smartBudget == b.key, { scope.launch { c.settings.setSmartBudget(b.key) } },
                        label = { Text(b.label) })
                }
            }
        }
        Text("Download quality")
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            org.nasmusic.app.sync.DownloadQuality.entries.forEach { q ->
                androidx.compose.material3.FilterChip(settings.downloadQuality == q.key,
                    { scope.launch { c.settings.setDownloadQuality(q.key) } }, label = { Text(q.label) })
            }
        }
        Text("For smart downloads and songs you download. Changing it re-downloads smart songs at the next sync.",
            style = MaterialTheme.typography.bodySmall)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Offline favourites (simple)")
                Text(if (settings.smartDownloads) "Not used while smart downloads is on (it already includes favourites and today's mixes)."
                    else "Favourites and today's mixes only, within the limit below.", style = MaterialTheme.typography.bodySmall)
            }
            Switch(settings.offlineFavourites, { on -> scope.launch { c.settings.setOfflineFavourites(on) } }, enabled = !settings.smartDownloads)
        }
        if (!settings.smartDownloads && settings.offlineFavourites) {
            Text("Favourites format")
            SingleChoiceSegmentedButtonRow {
                AudioFormat.entries.forEachIndexed { i, f ->
                    SegmentedButton(
                        selected = settings.favouritesFormat == f,
                        onClick = { scope.launch { c.settings.setFavouritesFormat(f) } },
                        shape = SegmentedButtonDefaults.itemShape(i, AudioFormat.entries.size),
                    ) { Text(if (f == AudioFormat.OPUS) "Opus 160" else "FLAC") }
                }
            }
            Text("Storage limit: %.1f GB".format(limit))
            Slider(
                value = limit, onValueChange = { limit = it }, valueRange = 1f..64f, steps = 62,
                onValueChangeFinished = { scope.launch { c.settings.setStorageLimitMb((limit * 1024).toInt()) } },
            )
        }
        Text("Night sync starts at %02d:00 (runs on Wi-Fi while charging, for 2 hours)".format(hour.toInt()))
        Slider(
            value = hour, onValueChange = { hour = it }, valueRange = 0f..23f, steps = 22,
            onValueChangeFinished = {
                scope.launch {
                    c.settings.setSyncHour(hour.toInt())
                    SyncScheduler.scheduleNight(c.context, hour.toInt(), replace = true)
                }
            },
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Allow sync over the tunnel")
                Text("Off: downloads only happen on the home network.", style = MaterialTheme.typography.bodySmall)
            }
            Switch(settings.allowTunnelSync, { on -> scope.launch { c.settings.setAllowTunnelSync(on) } })
        }
        OutlinedButton(enabled = settings.offlineFavourites || settings.smartDownloads, onClick = { SyncScheduler.syncNow(c.context) }) { Text("Sync now") }

        HorizontalDivider()
        Section("Family mode")
        org.nasmusic.app.ui.FamilySettings(c)

        HorizontalDivider()
        Section("Voice control")
        org.nasmusic.app.ui.VoiceSettings(c)

        HorizontalDivider()
        Section("Playback")
        PlaybackSettings(c)

        HorizontalDivider()
        Section("Wake-up alarm")
        val wake by c.alarm.alarm.collectAsStateWithLifecycle()
        OutlinedButton(onClick = { ui.go(org.nasmusic.app.alarm.AlarmScheduler.ROUTE) }) {
            Text(if (wake.enabled) "%02d:%02d · %s".format(wake.hour, wake.minute, wake.source.label) else "Set a wake-up alarm")
        }

        HorizontalDivider()
        Section("Appearance")
        AppearanceSettings(c, settings)

        HorizontalDivider()
        Section("Scrobbling")
        ScrobbleSettings(c)

        HorizontalDivider()
        Section("Data & recommendations")
        OutlinedButton(onClick = { ui.go("data") }) { Text("Choose what to collect") }

        HorizontalDivider()
        Section("Help")
        OutlinedButton(onClick = { ui.bugReport = false }) { Text("Send bug report") }
        Text("App ${org.nasmusic.app.BuildConfig.VERSION_NAME} (${org.nasmusic.app.BuildConfig.VERSION_CODE})", style = MaterialTheme.typography.bodySmall)

        HorizontalDivider()
        Section("Account")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { ui.go("account") }) { Text("Account") }
            OutlinedButton(onClick = { confirmUnpair = true }) { Text("Log out") }
        }
    }

    if (confirmSmartOff) {
        val auto = downloads.filter { !it.isProtected && it.complete }
        AlertDialog(
            onDismissRequest = { confirmSmartOff = false },
            title = { Text("Turn off smart downloads?") },
            text = { Text("Remove the ${auto.size} songs it downloaded (%d MB)? Songs you downloaded yourself stay.".format(auto.sumOf { it.bytes } / (1024 * 1024))) },
            confirmButton = { TextButton(onClick = {
                confirmSmartOff = false
                scope.launch { c.settings.setSmartDownloads(false); if (!c.settings.current().offlineFavourites) c.offline.removeAutomatic() }
            }) { Text("Turn off and remove") } },
            dismissButton = { TextButton(onClick = { confirmSmartOff = false; scope.launch { c.settings.setSmartDownloads(false) } }) { Text("Turn off, keep songs") } },
        )
    }

    if (confirmUnpair) {
        AlertDialog(
            onDismissRequest = { confirmUnpair = false },
            title = { Text("Log out?") },
            text = { Text("You'll sign in again with your username and password. Downloaded music stays until the next sync.") },
            confirmButton = { TextButton(onClick = { confirmUnpair = false; scope.launch { c.auth.logout(c.api) } }) { Text("Log out") } },
            dismissButton = { TextButton(onClick = { confirmUnpair = false }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun Section(title: String) = Text(title, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.primary)

@Composable
private fun AppearanceSettings(c: AppContainer, settings: org.nasmusic.app.data.prefs.AppSettings) {
    val scope = rememberCoroutineScope()
    @Composable
    fun Choice(label: String, options: List<Pair<String, String>>, value: String, set: suspend (String) -> Unit) {
        Text(label)
        SingleChoiceSegmentedButtonRow {
            options.forEachIndexed { i, (k, l) ->
                SegmentedButton(selected = value == k, onClick = { scope.launch { set(k) } }, shape = SegmentedButtonDefaults.itemShape(i, options.size)) { Text(l) }
            }
        }
    }
    Choice("Animations", org.nasmusic.app.ui.MotionLevel.entries.map { it.key to it.label }, settings.animations) { c.settings.setAnimations(it) }
    Text("Turning off animations in Android's accessibility settings also turns them off here.", style = MaterialTheme.typography.bodySmall)
    Choice("Default view", listOf("grid" to "Grid", "list" to "List"), settings.defaultView) { c.settings.setDefaultView(it) }
    Choice("Album & artist pages", listOf("big" to "Big header", "simple" to "Simple list"), settings.pageStyle) { c.settings.setPageStyle(it) }
    Text("Accent colour")
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        org.nasmusic.app.data.prefs.AppSettings.ACCENTS.forEach { argb ->
            val selected = settings.accent == argb
            androidx.compose.foundation.layout.Box(
                Modifier.size(if (selected) 40.dp else 32.dp)
                    .background(androidx.compose.ui.graphics.Color(argb), androidx.compose.foundation.shape.CircleShape)
                    .clickable { scope.launch { c.settings.setAccent(argb) } },
            )
        }
    }
    Text("Home shelves")
    val names = mapOf("start_radio" to "Start radio", "devices" to "Your devices", "recently_played" to "Recently played", "continue_listening" to "Continue listening", "pinned" to "Pinned", "on_this_day" to "On this day")
    val order = settings.homeShelves
    order.forEachIndexed { i, entry ->
        val id = entry.removePrefix("-")
        val visible = !entry.startsWith("-")
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(names[id] ?: id, Modifier.weight(1f))
            IconButton(enabled = i > 0, onClick = { scope.launch { c.settings.setHomeShelves(order.toMutableList().apply { add(i - 1, removeAt(i)) }) } }) {
                Icon(Icons.Default.KeyboardArrowUp, "Move up")
            }
            IconButton(enabled = i < order.lastIndex, onClick = { scope.launch { c.settings.setHomeShelves(order.toMutableList().apply { add(i + 1, removeAt(i)) }) } }) {
                Icon(Icons.Default.KeyboardArrowDown, "Move down")
            }
            Switch(visible, { on -> scope.launch { c.settings.setHomeShelves(order.toMutableList().apply { this[i] = if (on) id else "-$id" }) } })
        }
    }
}
