package org.homebeat.app.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudOff
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.Card
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.work.WorkInfo
import androidx.work.WorkManager
import org.homebeat.app.AppContainer
import org.homebeat.app.data.api.Mix
import org.homebeat.app.playback.PlaySource
import org.homebeat.app.sync.SyncScheduler
import org.homebeat.app.sync.SyncWorker
import org.homebeat.app.ui.Loading
import org.homebeat.app.ui.Message
import org.homebeat.app.ui.TrackRow
import org.homebeat.app.ui.formatDuration

@Composable
fun TodayScreen(c: AppContainer) {
    val ui = org.homebeat.app.ui.LocalUi.current
    var mixes by remember { mutableStateOf<List<Mix>?>(null) }
    var online by remember { mutableStateOf(true) }
    var expanded by remember { mutableStateOf<String?>(null) }
    val route by c.router.route.collectAsStateWithLifecycle()
    LaunchedEffect(route) {
        val (m, ok) = c.library.todayMixes()
        mixes = m; online = ok
    }
    val downloads by c.library.downloads.collectAsStateWithLifecycle(emptyList())
    val offlineIds = remember(downloads) { downloads.filter { it.complete }.map { it.trackId }.toSet() }

    Column(Modifier.fillMaxSize()) {
        SyncBar(c)
        if (!online) {
            Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Default.CloudOff, null)
                Text("  Offline: showing last synced mixes", style = MaterialTheme.typography.bodySmall)
            }
        }
        val list = mixes
        when {
            list == null -> Loading()
            list.isEmpty() -> Message("No mixes yet. The server builds them overnight.")
            else -> LazyColumn(Modifier.fillMaxSize()) {
                items(list, key = { it.id }) { mix ->
                    val have = mix.tracks.count { it.track.id in offlineIds }
                    Card(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp)) {
                        Row(
                            Modifier.fillMaxWidth().clickable { expanded = if (expanded == mix.id) null else mix.id }.padding(16.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(mix.title, style = MaterialTheme.typography.titleMedium)
                                Text(
                                    "${mix.tracks.size} tracks · ${formatDuration(mix.durationS ?: mix.tracks.sumOf { it.track.durationS })}" +
                                        " · $have/${mix.tracks.size} offline",
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                            IconButton(onClick = {
                                c.player.play(mix.tracks.map { it.track }, 0, PlaySource.MIX, mix.title, mix.id, mix.tracks.map { it.reason })
                                ui.go("now")
                            }) { Icon(Icons.Default.PlayArrow, "Play ${mix.title}") }
                        }
                        if (expanded == mix.id) {
                            mix.tracks.forEachIndexed { i, mt ->
                                TrackRow(mt.track, subtitle = mt.reason, offline = mt.track.id in offlineIds) {
                                    c.player.play(mix.tracks.map { it.track }, i, PlaySource.MIX, mix.title, mix.id, mix.tracks.map { it.reason })
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun SyncBar(c: AppContainer) {
    val wm = remember { WorkManager.getInstance(c.context) }
    val now by wm.getWorkInfosForUniqueWorkFlow(SyncScheduler.NOW).collectAsStateWithLifecycle(emptyList())
    val night by wm.getWorkInfosForUniqueWorkFlow(SyncScheduler.NIGHT).collectAsStateWithLifecycle(emptyList())
    val running = (now + night).firstOrNull { it.state == WorkInfo.State.RUNNING }
    val last = now.firstOrNull { it.state.isFinished }
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Column(Modifier.weight(1f)) {
            if (running != null) {
                val done = running.progress.getInt(SyncWorker.KEY_DONE, 0)
                val total = running.progress.getInt(SyncWorker.KEY_TOTAL, 0)
                Text(
                    (running.progress.getString(SyncWorker.KEY_STATUS) ?: "Syncing") + if (total > 0) " $done/$total" else "",
                    style = MaterialTheme.typography.bodySmall,
                )
                if (total > 0) LinearProgressIndicator(progress = { done.toFloat() / total }, Modifier.fillMaxWidth())
                else LinearProgressIndicator(Modifier.fillMaxWidth())
            } else {
                Text(
                    last?.outputData?.getString(SyncWorker.KEY_STATUS) ?: "Downloads sync overnight on Wi-Fi while charging",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
        FilledTonalButton(onClick = { SyncScheduler.syncNow(c.context) }, enabled = running == null) {
            Icon(Icons.Default.Sync, null)
            Text(" Sync now")
        }
    }
}
