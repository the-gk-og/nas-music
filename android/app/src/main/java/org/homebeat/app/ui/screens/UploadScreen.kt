package org.homebeat.app.ui.screens

import android.text.format.Formatter
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.work.WorkInfo
import androidx.work.WorkManager
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.homebeat.app.AppContainer
import org.homebeat.app.ui.LocalUi
import org.homebeat.app.ui.ScreenTitle
import org.homebeat.app.upload.UploadOverview
import org.homebeat.app.upload.UploadWorker
import org.homebeat.app.upload.UploadedFile
import org.homebeat.app.upload.VisibilityBody
import retrofit2.HttpException

/** Upload music (v7): pick files, upload them in the background (WorkManager), and manage your uploads. */
@Composable
fun UploadScreen(c: AppContainer) {
    val ui = LocalUi.current
    val ctx = c.context
    val scope = rememberCoroutineScope()
    var overview by remember { mutableStateOf<UploadOverview?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableIntStateOf(0) }
    var visibility by remember { mutableStateOf("everyone") }
    var wifiOnly by remember { mutableStateOf(UploadWorker.wifiOnly(ctx)) }
    var confirmDelete by remember { mutableStateOf<UploadedFile?>(null) }
    val wm = remember { WorkManager.getInstance(ctx) }
    val jobs by wm.getWorkInfosByTagFlow(UploadWorker.TAG).collectAsStateWithLifecycle(emptyList())
    fun size(b: Long) = Formatter.formatShortFileSize(ctx, b)

    val succeeded = jobs.count { it.state == WorkInfo.State.SUCCEEDED }
    LaunchedEffect(reload, succeeded) {
        error = null
        overview = try {
            c.uploads.overview()
        } catch (e: HttpException) {
            error = if (e.code() == 404) "This server doesn't support uploads yet (it needs an update)." else "HTTP ${e.code()}"
            null
        } catch (e: Exception) {
            error = "Can't reach the server"
            null
        }
    }
    // New uploads take a few seconds to be scanned into the library.
    LaunchedEffect(overview) {
        if (overview?.files?.any { !it.indexed } == true) { delay(3000); reload++ }
    }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        if (uris.isNotEmpty()) scope.launch {
            val n = UploadWorker.enqueue(ctx, uris, visibility, wifiOnly)
            ui.toast = if (n == 0) "Couldn't read those files" else "Uploading $n song${if (n == 1) "" else "s"}" +
                if (wifiOnly) " (on Wi-Fi)" else ""
        }
    }

    LazyColumn(Modifier.fillMaxSize()) {
        item {
            ScreenTitle("Upload music") {
                IconButton(onClick = { ui.nav.popBackStack() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
            }
        }
        val ov = overview
        item {
            Card(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp)) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    when {
                        error != null -> Text(error!!)
                        ov == null -> Text("Loading…")
                        !ov.enabled -> Text("Uploading isn't set up on this server. An admin needs to add an upload folder.")
                        !ov.canUpload -> Text("You can't upload to this server yet. Ask an admin to allow it for you.")
                        else -> {
                            Text("Songs you upload go into the shared library (FLAC, MP3, M4A/AAC, Ogg/Opus, WAV · up to " +
                                "${size(ov.maxFileBytes)} each). You can also share audio files to Homebeat from other apps.",
                                style = MaterialTheme.typography.bodySmall)
                            Text("${size(ov.usedBytes)} used" + (ov.quotaBytes?.let { " of ${size(it)}" } ?: ""))
                            ov.quotaBytes?.let { q ->
                                LinearProgressIndicator(progress = { (ov.usedBytes.toFloat() / q).coerceIn(0f, 1f) }, Modifier.fillMaxWidth())
                            }
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                FilterChip(visibility == "everyone", { visibility = "everyone" }, { Text("Everyone") })
                                FilterChip(visibility == "private", { visibility = "private" }, { Text("Only me") })
                            }
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text("Upload on Wi-Fi only", Modifier.weight(1f))
                                Switch(wifiOnly, { wifiOnly = it; UploadWorker.setWifiOnly(ctx, it) })
                            }
                            FilledTonalButton(onClick = { picker.launch(arrayOf("audio/*")) }) { Text("Choose files") }
                        }
                    }
                }
            }
        }
        if (jobs.isNotEmpty()) {
            item {
                Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 8.dp, top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("On this phone", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                    TextButton(onClick = { wm.pruneWork() }) { Text("Clear finished") }
                }
            }
            items(jobs.sortedBy { it.state.isFinished }, key = { it.id }) { w -> UploadJobRow(c, w) }
        }
        val files = ov?.files.orEmpty()
        if (files.isNotEmpty()) {
            item { Text("Your uploads", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(start = 16.dp, top = 16.dp)) }
            items(files, key = { it.id }) { f ->
                Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(f.title ?: f.originalName, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(listOfNotNull(f.artist, f.album, f.format.uppercase(), size(f.bytes)).joinToString(" · "),
                            style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(when {
                            !f.indexed -> "Adding to the library…"
                            f.visibility == "private" -> "Only you can see this"
                            else -> "In the library for everyone"
                        }, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    IconButton(onClick = {
                        scope.launch {
                            runCatching { c.uploads.setVisibility(f.id, VisibilityBody(if (f.visibility == "private") "everyone" else "private")) }
                                .onFailure { ui.toast = "Couldn't change it" }
                            reload++
                        }
                    }) {
                        if (f.visibility == "private") Icon(Icons.Default.VisibilityOff, "Show to everyone")
                        else Icon(Icons.Default.Visibility, "Only me")
                    }
                    IconButton(onClick = { confirmDelete = f }) { Icon(Icons.Default.Delete, "Delete upload") }
                }
            }
        }
    }

    confirmDelete?.let { f ->
        AlertDialog(
            onDismissRequest = { confirmDelete = null },
            title = { Text("Delete upload?") },
            text = { Text("\"${f.title ?: f.originalName}\" is removed from the server for everyone.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmDelete = null
                    scope.launch {
                        val r = runCatching { c.uploads.delete(f.id) }.getOrNull()
                        if (r?.isSuccessful != true) ui.toast = "Couldn't delete it"
                        reload++
                    }
                }) { Text("Delete") }
            },
            dismissButton = { TextButton(onClick = { confirmDelete = null }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun UploadJobRow(c: AppContainer, w: WorkInfo) {
    val ctx = c.context
    val scope = rememberCoroutineScope()
    val name = UploadWorker.nameOf(w.tags)
    val total = w.progress.getLong(UploadWorker.KEY_TOTAL, UploadWorker.sizeOf(w.tags))
    val sent = w.progress.getLong(UploadWorker.KEY_SENT, 0)
    val status = when (w.state) {
        WorkInfo.State.ENQUEUED -> if (w.runAttemptCount > 0) "Waiting to resume" else "Waiting for a connection"
        WorkInfo.State.RUNNING -> if (total > 0) "Uploading ${sent * 100 / total}%" else "Uploading"
        WorkInfo.State.SUCCEEDED -> "Uploaded" + (w.outputData.getString(UploadWorker.KEY_PATH)?.takeIf { it.isNotEmpty() }?.let { " as $it" } ?: "")
        WorkInfo.State.FAILED -> "Failed: " + (w.outputData.getString(UploadWorker.KEY_ERROR) ?: "unknown error")
        WorkInfo.State.CANCELLED -> "Cancelled"
        WorkInfo.State.BLOCKED -> "Waiting"
    }
    Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(name, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (w.state == WorkInfo.State.RUNNING) {
                if (total > 0) LinearProgressIndicator(progress = { sent.toFloat() / total }, Modifier.fillMaxWidth().padding(vertical = 4.dp))
                else LinearProgressIndicator(Modifier.fillMaxWidth().padding(vertical = 4.dp))
            }
            Text(status, style = MaterialTheme.typography.bodySmall,
                color = if (w.state == WorkInfo.State.FAILED) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (!w.state.isFinished) {
            IconButton(onClick = { scope.launch { UploadWorker.cancel(ctx, w.id) } }) { Icon(Icons.Default.Close, "Cancel") }
        } else if (w.state == WorkInfo.State.FAILED && UploadWorker.hasCachedCopy(ctx, w.id)) {
            IconButton(onClick = { UploadWorker.retry(ctx, w.id, name, UploadWorker.visibilityOf(w.tags)) }) { Icon(Icons.Default.Refresh, "Retry") }
        }
    }
}
