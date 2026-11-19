package org.nasmusic.app.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.AutoEqEntry
import org.nasmusic.app.playback.SleepTimer

/** Settings → Playback: crossfade and volume levelling (kept on this phone). */
@Composable
fun PlaybackSettings(c: AppContainer) {
    val s by c.listening.settings.collectAsStateWithLifecycle()
    Text("Crossfade: " + if (s.crossfadeS == 0) "off (gapless)" else "${s.crossfadeS} s")
    Slider(
        value = s.crossfadeS.toFloat(),
        onValueChange = { v -> c.listening.update { it.copy(crossfadeS = v.toInt()) } },
        valueRange = 0f..12f, steps = 11,
    )
    Text("Songs that follow each other on the same album always play gapless.", style = MaterialTheme.typography.bodySmall)
    Text("Volume levelling")
    val modes = listOf("off" to "Off", "track" to "Track", "album" to "Album", "auto" to "Auto")
    SingleChoiceSegmentedButtonRow {
        modes.forEachIndexed { i, (k, l) ->
            SegmentedButton(selected = s.levelling == k, onClick = { c.listening.update { it.copy(levelling = k) } },
                shape = SegmentedButtonDefaults.itemShape(i, modes.size)) { Text(l) }
        }
    }
    Text("ReplayGain from your server. Auto uses album gain while an album plays in order.", style = MaterialTheme.typography.bodySmall)
    if (s.levelling != "off") {
        Text("Pre-amp: ${if (s.preampDb > 0) "+" else ""}${s.preampDb} dB")
        Slider(
            value = s.preampDb,
            onValueChange = { v -> c.listening.update { it.copy(preampDb = Math.round(v * 2) / 2f) } },
            valueRange = -15f..15f,
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Prevent clipping")
                Text("Never raise a song so far that its loudest peak would distort. The phone only turns songs down, never up.",
                    style = MaterialTheme.typography.bodySmall)
            }
            Switch(s.preventClipping, { on -> c.listening.update { it.copy(preventClipping = on) } })
        }
    }
}

/** Sleep timer picker (Now Playing). */
@Composable
fun SleepTimerDialog(c: AppContainer, onDismiss: () -> Unit) {
    val timer by c.sleep.timer.collectAsStateWithLifecycle()
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) { while (true) { now = System.currentTimeMillis(); delay(1000) } }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Sleep timer") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                timer?.let { Text("Stops: ${it.label(now)}", style = MaterialTheme.typography.titleSmall) }
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf(15, 30, 45, 60).forEach { m -> FilterChip(false, { c.sleep.minutes(m); onDismiss() }, { Text("$m min") }) }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf(5, 10, 90).forEach { m -> FilterChip(false, { c.sleep.minutes(m); onDismiss() }, { Text("$m min") }) }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    FilterChip(timer?.mode == SleepTimer.Mode.TRACK, { c.sleep.set(SleepTimer(SleepTimer.Mode.TRACK)); onDismiss() }, { Text("End of song") })
                    FilterChip(timer?.mode == SleepTimer.Mode.ALBUM, {
                        c.sleep.set(SleepTimer(SleepTimer.Mode.ALBUM, albumId = c.player.state.value.item?.mediaMetadata?.extras?.getString(org.nasmusic.app.playback.MediaItems.EXTRA_ALBUM_ID))); onDismiss()
                    }, { Text("End of album") })
                }
                Text("The music fades out, then pauses. Also in the notification's menu.", style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Done") } },
        dismissButton = { TextButton(enabled = timer != null, onClick = { c.sleep.set(null); onDismiss() }) { Text("Turn off") } },
    )
}

/** Equalizer → headphone correction (AutoEq, fetched through the server). */
@Composable
fun HeadphoneCorrection(c: AppContainer) {
    val eq by c.eq.edited.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()
    var open by remember { mutableStateOf(false) }
    var query by remember { mutableStateOf("") }
    var results by remember { mutableStateOf<List<AutoEqEntry>>(emptyList()) }
    var status by remember { mutableStateOf<String?>(null) }
    val hp = eq.headphone

    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Headphone correction")
                Text(
                    hp?.let { "${it.name} · ${it.source} · ${it.filters.size} filters" } ?: "Off — pick your headphones (AutoEq)",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            if (hp != null) TextButton(onClick = { c.eq.update { it.copy(headphone = null) } }) { Text("Remove") }
            TextButton(onClick = { open = !open }) { Text(if (open) "Close" else if (hp != null) "Change" else "Choose") }
        }
        if (open) {
            LaunchedEffect(query) {
                if (query.isBlank()) { results = emptyList(); return@LaunchedEffect }
                delay(300)
                status = "Searching…"
                runCatching { c.api.autoEqSearch(query.trim()) }
                    .onSuccess { results = it.results; status = if (it.results.isEmpty()) "No matches" else null }
                    .onFailure { status = "Couldn't reach the AutoEq index (${it.message})" }
            }
            OutlinedTextField(query, { query = it }, Modifier.fillMaxWidth(), label = { Text("Search headphones, e.g. HD 650") }, singleLine = true)
            status?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
            LazyColumn(Modifier.heightIn(max = 280.dp)) {
                items(results, key = { it.id }) { r ->
                    Column(
                        Modifier.fillMaxWidth().clickable {
                            scope.launch {
                                runCatching { c.api.autoEqProfile(r.id) }
                                    .onSuccess { p -> c.eq.update { it.copy(headphone = p, enabled = true) }; open = false }
                                    .onFailure { status = "Couldn't load ${r.name} (${it.message})" }
                            }
                        }.padding(vertical = 8.dp),
                    ) {
                        Text(r.name)
                        Text("${r.source}${r.rig?.let { " on $it" } ?: ""} · ${r.form}", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
            Text("Profiles come from the AutoEq project and are fetched by your server. They apply to this output while the EQ is on.",
                style = MaterialTheme.typography.bodySmall)
        }
    }
}
