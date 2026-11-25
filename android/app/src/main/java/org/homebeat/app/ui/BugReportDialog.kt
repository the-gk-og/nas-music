package org.homebeat.app.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.homebeat.app.AppContainer
import org.homebeat.app.diag.BugReport
import org.homebeat.app.diag.BugReportQueue
import org.homebeat.app.diag.BugReportWorker
import org.homebeat.app.diag.CrashLog
import retrofit2.HttpException

/** Settings → Send bug report: log, crash logs, version, device and screen go to the owner's own server. */
@Composable
fun BugReportDialog(c: AppContainer, afterCrash: Boolean, onDismiss: () -> Unit) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var text by remember { mutableStateOf("") }
    var sending by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val lastCrash = remember { if (afterCrash) CrashLog.newestCrash()?.let { f -> runCatching { f.readLines() }.getOrNull() } else null }
    // Closing the after-crash prompt in any way means "asked": it won't come back until there's a new crash.
    val close = { if (afterCrash) CrashLog.markCrashesSeen(); onDismiss() }
    AlertDialog(
        onDismissRequest = { if (!sending) close() },
        title = { Text(if (afterCrash) "Homebeat crashed last time" else "Send bug report") },
        text = {
            Column {
                Text(
                    "Sends the app's recent log, crash logs, app version, phone model, Android version and current screen to your server. " +
                        "Nothing goes anywhere else.",
                    style = MaterialTheme.typography.bodySmall,
                )
                // "crash at <time>" then the exception line, e.g. "java.lang.IllegalStateException: …"
                lastCrash?.getOrNull(3)?.let { Text(it.take(200), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
                OutlinedTextField(
                    text, { text = it }, label = { Text("What happened? (optional)") },
                    modifier = Modifier.fillMaxWidth().height(140.dp),
                )
                if (sending) CircularProgressIndicator()
                error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            TextButton(enabled = !sending, onClick = {
                sending = true
                scope.launch {
                    val body = withContext(Dispatchers.IO) {
                        val s = c.player.state.value
                        BugReport.build(
                            description = text,
                            screen = CrashLog.currentScreen,
                            state = mapOf(
                                "route" to c.router.route.value.name,
                                "connectConnected" to c.connect.connected.value.toString(),
                                "remote" to (s.remote?.deviceName ?: "no"),
                                "trackId" to (s.item?.mediaId ?: "-"),
                                "queueId" to (c.queueSync.currentId.value ?: "-"),
                                "playing" to s.isPlaying.toString(),
                            ),
                            log = BugReport.readLog(),
                            crashFiles = CrashLog.files(),
                        )
                    }
                    val result = runCatching { c.api.bugReport(body) }
                    sending = false
                    result.onSuccess {
                        CrashLog.markSent()
                        ui.toast = "Bug report sent${it.id?.let { id -> " (#${id.take(8)})" } ?: ""}. Thanks!"
                        onDismiss()
                    }.onFailure { e ->
                        val http = e as? HttpException
                        if (http == null || BugReport.retryable(http.code())) {
                            // Offline or server trouble: keep it and send it automatically later.
                            val queued = withContext(Dispatchers.IO) {
                                runCatching { BugReportQueue(c.context).add(body) }.isSuccess
                            }
                            if (queued) {
                                BugReportWorker.enqueue(c.context)
                                CrashLog.markSent()
                                ui.toast = "Couldn't reach the server. The report will be sent automatically when you're back online."
                                onDismiss()
                            } else {
                                error = "Couldn't reach the server (${e.message ?: e.javaClass.simpleName}). Try again when connected."
                            }
                        } else {
                            val errBody = withContext(Dispatchers.IO) { runCatching { http.response()?.errorBody()?.string() }.getOrNull() }
                            error = BugReport.failureMessage(http.code(), errBody)
                        }
                    }
                }
            }) { Text("Send") }
        },
        dismissButton = { TextButton(enabled = !sending, onClick = close) { Text(if (afterCrash) "Not now" else "Cancel") } },
    )
}
