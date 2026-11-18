package org.nasmusic.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
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
    val scope = rememberCoroutineScope()
    val settings by c.settings.settings.collectAsStateWithLifecycle()
    val config by c.secureStore.config.collectAsStateWithLifecycle()
    val route by c.router.route.collectAsStateWithLifecycle()
    val probe by c.router.lanProbe.collectAsStateWithLifecycle()
    val downloads by c.library.downloads.collectAsStateWithLifecycle(emptyList())
    val usedMb = downloads.sumOf { it.bytes } / (1024 * 1024)
    var confirmUnpair by remember { mutableStateOf(false) }

    val cfg = config ?: return
    var lan by remember(cfg) { mutableStateOf(cfg.lanUrl) }
    var tunnel by remember(cfg) { mutableStateOf(cfg.tunnelUrl.orEmpty()) }
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
        OutlinedTextField(lan, { lan = it }, label = { Text("LAN address") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(tunnel, { tunnel = it }, label = { Text("Tunnel address") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(cfId, { cfId = it }, label = { Text("CF Access client id") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(
            cfSecret, { cfSecret = it }, label = { Text("CF Access client secret") }, singleLine = true,
            visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { c.pairing.updateAddresses(lan, tunnel, cfId, cfSecret) }) { Text("Save addresses") }
            OutlinedButton(onClick = { scope.launch { c.router.refresh() } }) { Text("Re-check route") }
        }
        Text("LAN certificate: ${cfg.certSha256.take(23)}…", style = MaterialTheme.typography.bodySmall)

        HorizontalDivider()
        Section("Offline")
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
        Text("Storage limit: %.1f GB (using %d MB)".format(limit, usedMb))
        Slider(
            value = limit, onValueChange = { limit = it }, valueRange = 1f..64f, steps = 62,
            onValueChangeFinished = { scope.launch { c.settings.setStorageLimitMb((limit * 1024).toInt()) } },
        )
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
        OutlinedButton(onClick = { SyncScheduler.syncNow(c.context) }) { Text("Sync now") }

        HorizontalDivider()
        Section("Device")
        OutlinedButton(onClick = { confirmUnpair = true }) { Text("Re-pair / forget this server") }
    }

    if (confirmUnpair) {
        AlertDialog(
            onDismissRequest = { confirmUnpair = false },
            title = { Text("Forget this server?") },
            text = { Text("You will need to scan a new pairing QR. Downloaded music stays until the next sync.") },
            confirmButton = { TextButton(onClick = { confirmUnpair = false; c.pairing.unpair() }) { Text("Forget") } },
            dismissButton = { TextButton(onClick = { confirmUnpair = false }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun Section(title: String) = Text(title, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.primary)
