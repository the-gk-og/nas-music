package org.homebeat.app.ui.screens

import android.text.format.DateUtils
import android.text.format.Formatter
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.work.WorkInfo
import androidx.work.WorkManager
import kotlinx.coroutines.launch
import org.homebeat.app.AppContainer
import org.homebeat.app.data.LibraryRepository
import org.homebeat.app.data.db.DownloadEntity
import org.homebeat.app.playback.PlaySource
import org.homebeat.app.sync.StorageBudget
import org.homebeat.app.sync.SyncScheduler
import org.homebeat.app.sync.SyncWorker
import org.homebeat.app.ui.LocalUi
import org.homebeat.app.ui.Message
import org.homebeat.app.ui.ScreenTitle
import org.homebeat.app.ui.SongRow

private val REASON_LABELS = mapOf(
    "favourite" to "Favourite", "mix" to "Today's mix", "week_mix" to "This week's mix", "recent" to "Recently played",
    "release_radar" to "Release Radar", "foryou" to "For You", "predicted" to "Picked for you", "pinned" to "Kept playlist/album",
    "manual" to "Downloaded by you",
)

/** Downloads: storage used, smart vs manual, last sync result, sync now. */
@Composable
fun DownloadsScreen(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val settings by c.settings.settings.collectAsStateWithLifecycle()
    val rows by c.offline.downloads.collectAsStateWithLifecycle(emptyList())
    val pins by c.offline.pins.collectAsStateWithLifecycle(emptyList())
    val status by c.offline.status.collectAsStateWithLifecycle(null)
    val wm = remember { WorkManager.getInstance(c.context) }
    val now by wm.getWorkInfosForUniqueWorkFlow(SyncScheduler.NOW).collectAsStateWithLifecycle(emptyList())
    val night by wm.getWorkInfosForUniqueWorkFlow(SyncScheduler.NIGHT).collectAsStateWithLifecycle(emptyList())
    val prot by wm.getWorkInfosForUniqueWorkFlow(SyncScheduler.PROTECTED).collectAsStateWithLifecycle(emptyList())
    val running = (now + night + prot).firstOrNull { it.state == WorkInfo.State.RUNNING }
    var tab by rememberSaveable { mutableStateOf("smart") }
    var confirmClear by remember { mutableStateOf(false) }

    val complete = rows.filter { it.complete }
    val protectedRows = rows.filter { it.isProtected }
    val autoRows = complete.filter { !it.isProtected }
    val smartBytes = autoRows.sumOf { it.bytes }
    val manualBytes = protectedRows.filter { it.complete }.sumOf { it.bytes }
    val free = remember(complete.size) { c.downloader.freeBytes() }
    val budget = StorageBudget.parse(settings.smartBudget)
    val budgetBytes = budget.bytes(free, smartBytes)
    fun size(b: Long) = Formatter.formatShortFileSize(c.context, b)

    LazyColumn(Modifier.fillMaxSize()) {
        item {
            ScreenTitle("Downloads") {
                IconButton(onClick = { ui.nav.popBackStack() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
            }
        }
        item {
            Card(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp)) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("${size(smartBytes + manualBytes)} used · ${size(free)} free on this phone", style = MaterialTheme.typography.titleMedium)
                    if (settings.smartDownloads) {
                        Text("Smart: ${size(smartBytes)} of ${size(budgetBytes)} (${budget.label}) · ${autoRows.size} songs")
                        LinearProgressIndicator(
                            progress = { if (budgetBytes > 0) (smartBytes.toFloat() / budgetBytes).coerceIn(0f, 1f) else 0f },
                            modifier = Modifier.fillMaxWidth(),
                        )
                    } else if (autoRows.isNotEmpty()) {
                        Text("Automatic: ${size(smartBytes)} · ${autoRows.size} songs" + if (settings.offlineFavourites) " (offline favourites)" else "")
                    }
                    Text("Downloaded by you: ${size(manualBytes)} · ${protectedRows.count { it.complete }} songs" +
                        protectedRows.count { !it.complete }.let { if (it > 0) " ($it waiting)" else "" })
                    Text("Your downloads are never removed automatically and don't count towards the smart budget.",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        item {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Column(Modifier.weight(1f)) {
                    if (running != null) {
                        val done = running.progress.getInt(SyncWorker.KEY_DONE, 0)
                        val total = running.progress.getInt(SyncWorker.KEY_TOTAL, 0)
                        Text((running.progress.getString(SyncWorker.KEY_STATUS) ?: "Syncing") + if (total > 0) " $done/$total" else "",
                            style = MaterialTheme.typography.bodySmall)
                        if (total > 0) LinearProgressIndicator(progress = { done.toFloat() / total }, Modifier.fillMaxWidth())
                        else LinearProgressIndicator(Modifier.fillMaxWidth())
                    } else {
                        val s = status
                        Text(
                            if (s == null) "No sync yet. Downloads run overnight on Wi-Fi while charging."
                            else "Last sync " + DateUtils.getRelativeTimeSpanString(s.atMs) + ": " + s.summary,
                            style = MaterialTheme.typography.bodySmall,
                            color = if (s != null && !s.ok) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
                        )
                    }
                }
                FilledTonalButton(onClick = { SyncScheduler.syncNow(c.context); ui.toast = org.homebeat.app.ui.downloadToast(c).replace("Downloading", "Syncing") },
                    enabled = running == null) {
                    Icon(Icons.Default.Sync, null); Text(" Sync now")
                }
            }
        }
        item {
            Row(Modifier.padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(tab == "smart", { tab = "smart" }, label = { Text("Smart (${autoRows.size})") })
                FilterChip(tab == "manual", { tab = "manual" }, label = { Text("Yours (${protectedRows.size})") })
            }
        }
        if (tab == "smart") {
            if (!settings.smartDownloads && autoRows.isEmpty()) item {
                Message("Smart downloads is off. Turn it on in Settings › Offline to keep favourites, this week's mixes, " +
                    "recent plays and songs you're likely to want on the phone.")
            }
            val sorted = autoRows.sortedByDescending { it.priority }
            val tracks = sorted.mapNotNull { LibraryRepository.decodeTrackJson(it.trackJson) }
            items(sorted, key = { "s:" + it.trackId }) { row ->
                DownloadRow(c, row, size(row.bytes), tracks, onRemove = null)
            }
            if (autoRows.isNotEmpty()) item {
                OutlinedButton(onClick = { confirmClear = true }, modifier = Modifier.padding(16.dp)) { Text("Remove all automatic downloads") }
            }
        } else {
            if (pins.isEmpty() && protectedRows.isEmpty()) item {
                Message("Nothing yet. Use “Download” in a song's menu, or on a playlist or album page.")
            }
            items(pins, key = { "p:" + it.key }) { p ->
                val held = rows.count { p.key in it.pinKeys && it.complete }
                Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(p.title, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text("${p.type.replaceFirstChar { it.uppercase() }} · $held of ${p.ids.size} downloaded",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    IconButton(onClick = { scope.launch { c.offline.unpin(p.key) } }) { Icon(Icons.Default.Close, "Remove download") }
                }
            }
            val manual = protectedRows.filter { it.manual }
            val tracks = manual.filter { it.complete }.mapNotNull { LibraryRepository.decodeTrackJson(it.trackJson) }
            items(manual, key = { "m:" + it.trackId }) { row ->
                DownloadRow(c, row, if (row.complete) size(row.bytes) else "Waiting", tracks,
                    onRemove = { scope.launch { c.offline.removeManual(row.trackId) } })
            }
        }
        item { Spacer(Modifier.height(24.dp)) }
    }

    if (confirmClear) AlertDialog(
        onDismissRequest = { confirmClear = false },
        title = { Text("Remove automatic downloads?") },
        text = { Text("Deletes ${autoRows.size} songs (${size(smartBytes)}). Your own downloads stay. " +
            if (settings.smartDownloads) "Smart downloads will fill up again at the next sync." else "") },
        confirmButton = { TextButton(onClick = { confirmClear = false; scope.launch { c.offline.removeAutomatic() } }) { Text("Remove") } },
        dismissButton = { TextButton(onClick = { confirmClear = false }) { Text("Cancel") } },
    )
}

@Composable
private fun DownloadRow(c: AppContainer, row: DownloadEntity, sizeText: String, queue: List<org.homebeat.app.data.api.Track>, onRemove: (() -> Unit)?) {
    val track = LibraryRepository.decodeTrackJson(row.trackJson)
    val reasons = row.reasons.split(',').filter { it.isNotBlank() }.ifEmpty { listOf(row.why) }
    val label = reasons.mapNotNull { REASON_LABELS[it] }.distinct().take(2).joinToString(" · ") + " · $sizeText"
    Column {
        if (track != null) {
            SongRow(c, track, onClick = {
                val i = queue.indexOfFirst { it.id == track.id }
                if (i >= 0) c.player.play(queue, i, PlaySource.LIBRARY, "Downloads")
            })
        } else {
            Text(row.trackId, Modifier.padding(horizontal = 16.dp, vertical = 8.dp))
        }
        Row(Modifier.fillMaxWidth().padding(start = 74.dp, end = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
            if (onRemove != null) TextButton(onClick = onRemove) { Text("Remove") }
        }
    }
}
