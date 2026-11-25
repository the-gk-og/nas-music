package org.homebeat.app.ui.screens

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import java.time.DayOfWeek
import java.time.LocalTime
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle
import java.util.Locale
import kotlinx.coroutines.delay
import org.homebeat.app.AppContainer
import org.homebeat.app.alarm.AlarmScheduler
import org.homebeat.app.alarm.AlarmSource
import org.homebeat.app.alarm.AlarmTimes
import org.homebeat.app.alarm.WakeAlarm

/** Wake-up alarm: time, days, what to play, fade-in, volume floor, snooze length. */
@OptIn(ExperimentalMaterial3Api::class, androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
@Composable
fun AlarmScreen(c: AppContainer) {
    val ctx = LocalContext.current
    val alarm by c.alarm.alarm.collectAsStateWithLifecycle()
    val snooze by c.alarm.snoozeUntil.collectAsStateWithLifecycle()
    var picking by remember { mutableStateOf(false) }
    var pickingTime by remember { mutableStateOf(false) }
    var exact by remember { mutableStateOf(AlarmScheduler.canExact(ctx)) }
    var now by remember { mutableStateOf(ZonedDateTime.now()) }
    LaunchedEffect(Unit) { while (true) { now = ZonedDateTime.now(); exact = AlarmScheduler.canExact(ctx); delay(20_000) } }

    fun save(a: WakeAlarm) {
        c.alarm.set(a)
        AlarmScheduler.apply(ctx)
    }

    val is24 = android.text.format.DateFormat.is24HourFormat(ctx)
    val fmt = DateTimeFormatter.ofPattern(if (is24) "HH:mm" else "h:mm a")

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Wake-up alarm", style = MaterialTheme.typography.headlineSmall)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f).clickable { pickingTime = true }) {
                Text(LocalTime.of(alarm.hour, alarm.minute).format(fmt), style = MaterialTheme.typography.displayMedium)
                Text(
                    AlarmTimes.next(alarm, now)?.let { "${AlarmTimes.daysLabel(alarm.days)} · ${AlarmTimes.inLabel(it, now)}" }
                        ?: "Off",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Switch(alarm.enabled, { save(alarm.copy(enabled = it)) })
        }
        if (!exact && alarm.enabled) {
            Text(
                "Android isn't letting Homebeat set exact alarms, so it may ring late and can't always start the music. " +
                    "Allow \"Alarms & reminders\" for the app.",
                color = MaterialTheme.colorScheme.error,
            )
            if (Build.VERSION.SDK_INT >= 31) OutlinedButton(onClick = {
                runCatching {
                    ctx.startActivity(Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:${ctx.packageName}")))
                }
            }) { Text("Allow alarms") }
        }
        snooze?.let { at ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "Snoozed until " + java.time.Instant.ofEpochMilli(at).atZone(now.zone).toLocalTime().format(fmt),
                    Modifier.weight(1f),
                )
                TextButton(onClick = { AlarmScheduler.cancelSnooze(ctx) }) { Text("Cancel snooze") }
            }
        }

        Text("Repeat", style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.primary)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            for (d in 1..7) {
                val on = d in alarm.days
                FilterChip(
                    selected = on,
                    onClick = { save(alarm.copy(days = if (on) alarm.days - d else alarm.days + d, enabled = true)) },
                    label = { Text(DayOfWeek.of(d).getDisplayName(TextStyle.SHORT, Locale.getDefault())) },
                )
            }
        }
        Text(
            if (alarm.days.isEmpty()) "No days picked: rings once, then turns itself off." else AlarmTimes.daysLabel(alarm.days),
            style = MaterialTheme.typography.bodySmall,
        )

        HorizontalDivider()
        Text("Wake up to", style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.primary)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(alarm.source.label, Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
            OutlinedButton(onClick = { picking = true }) { Text("Change") }
        }
        Text(
            "If the server can't be reached it plays your downloads, and if nothing plays it rings the phone's alarm tone.",
            style = MaterialTheme.typography.bodySmall,
        )

        HorizontalDivider()
        Text("Fade in: " + fadeLabel(alarm.fadeInS), style = MaterialTheme.typography.titleSmall)
        val fadeSteps = listOf(0, 15, 30, 60, 120, 180, 300, 600)
        Slider(
            value = fadeSteps.indexOfFirst { it >= alarm.fadeInS }.coerceAtLeast(0).toFloat(),
            onValueChange = { save(alarm.copy(fadeInS = fadeSteps[it.toInt().coerceIn(0, fadeSteps.lastIndex)])) },
            valueRange = 0f..fadeSteps.lastIndex.toFloat(),
            steps = fadeSteps.size - 2,
        )
        Text(
            "Raise media volume to at least: " + if (alarm.minVolumePct == 0) "leave as it is" else "${alarm.minVolumePct}%",
            style = MaterialTheme.typography.titleSmall,
        )
        Slider(
            value = alarm.minVolumePct.toFloat(),
            onValueChange = { save(alarm.copy(minVolumePct = (it / 10).toInt() * 10)) },
            valueRange = 0f..100f,
            steps = 9,
        )
        Text("Snooze: ${alarm.snoozeMin} min", style = MaterialTheme.typography.titleSmall)
        Slider(
            value = alarm.snoozeMin.toFloat(),
            onValueChange = { save(alarm.copy(snoozeMin = it.toInt().coerceIn(1, 30))) },
            valueRange = 1f..30f,
            steps = 28,
        )
        Text(
            "Pausing the music, or Stop in the notification, turns the alarm off for today.",
            style = MaterialTheme.typography.bodySmall,
        )
    }

    if (pickingTime) {
        val state = rememberTimePickerState(alarm.hour, alarm.minute, is24)
        AlertDialog(
            onDismissRequest = { pickingTime = false },
            confirmButton = {
                TextButton(onClick = {
                    pickingTime = false
                    save(alarm.copy(hour = state.hour, minute = state.minute, enabled = true))
                }) { Text("Set") }
            },
            dismissButton = { TextButton(onClick = { pickingTime = false }) { Text("Cancel") } },
            text = { TimePicker(state) },
        )
    }

    if (picking) SourcePicker(c, onDismiss = { picking = false }) { s -> picking = false; save(alarm.copy(source = s)) }
}

private fun fadeLabel(s: Int) = when {
    s == 0 -> "off"
    s < 60 -> "$s s"
    else -> "${s / 60} min"
}

/** Mixes (by kind, so tomorrow's mix of the same kind plays), radios, playlists and internet radio stations. */
@Composable
private fun SourcePicker(c: AppContainer, onDismiss: () -> Unit, onPick: (AlarmSource) -> Unit) {
    var options by remember {
        mutableStateOf(
            listOf(
                "Mixes" to listOf(AlarmSource.MORNING_MIX, AlarmSource("mix", "daily", "Daily Mix")),
                "Radio" to listOf(AlarmSource.MOMENT, AlarmSource.RANDOM),
            ),
        )
    }
    LaunchedEffect(Unit) {
        val mixes = runCatching { c.api.mixesToday() }.getOrNull()
            ?.distinctBy { it.kind }?.map { AlarmSource("mix", it.kind, it.title) }
        val playlists = runCatching { c.api.playlists() }.getOrNull()?.map { AlarmSource("playlist", it.id, it.name) }.orEmpty()
        val stations = runCatching { c.spokenApi.stations().stations }.getOrNull()
            ?.map { AlarmSource("station", it.id, "${it.name} (radio station)") }.orEmpty()
        options = listOfNotNull(
            "Mixes" to (if (mixes.isNullOrEmpty()) options.first().second else mixes),
            "Radio" to listOf(AlarmSource.MOMENT, AlarmSource.RANDOM),
            ("Playlists" to playlists).takeIf { playlists.isNotEmpty() },
            ("Internet radio" to stations).takeIf { stations.isNotEmpty() },
        )
    }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Wake up to") },
        confirmButton = {},
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
        text = {
            LazyColumn(Modifier.heightIn(max = 420.dp)) {
                options.forEach { (group, list) ->
                    item(key = "h:$group") {
                        Text(group, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.padding(top = 8.dp, bottom = 4.dp))
                    }
                    items(list, key = { "${it.type}:${it.id}:$group" }) { s ->
                        Text(s.label, Modifier.fillMaxWidth().clickable { onPick(s) }.padding(vertical = 10.dp))
                    }
                }
            }
        },
    )
}
