package org.nasmusic.app.ui

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
import org.nasmusic.app.AppContainer
import org.nasmusic.app.diag.BugReport
import org.nasmusic.app.diag.CrashLog
import retrofit2.HttpException

/** Settings → Send bug report: log, crash logs, version, device and screen go to the owner's own server. */
@Composable
fun BugReportDialog(c: AppContainer, afterCrash: Boolean, onDismiss: () -> Unit) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var text by remember { mutableStateOf("") }
    var sending by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = { if (!sending) onDismiss() },
        title = { Text(if (afterCrash) "nas-music crashed last time" else "Send bug report") },
        text = {
            Column {
                Text(
                    "Sends the app's recent log, crash logs, app version, phone model, Android version and current screen to your server. " +
                        "Nothing goes anywhere else.",
                    style = MaterialTheme.typography.bodySmall,
                )
                OutlinedTextField(
                    text, { text = it }, label = { Text("What happened? (optional)") },
                    modifier = Modifier.fillMaxWidth().height(140.dp),
                )
                if (sending) CircularProgressIndicator()
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
                        ui.toast = "Bug report sent${it.id?.let { id -> " (#$id)" } ?: ""}. Thanks!"
                        onDismiss()
                    }.onFailure { e ->
                        ui.toast = when ((e as? HttpException)?.code()) {
                            404, 405 -> "Your server doesn't accept bug reports yet. Update the server."
                            null -> "Couldn't reach the server. Try again when connected."
                            else -> "Sending failed (HTTP ${(e as HttpException).code()})."
                        }
                    }
                }
            }) { Text("Send") }
        },
        dismissButton = { TextButton(enabled = !sending, onClick = onDismiss) { Text(if (afterCrash) "Not now" else "Cancel") } },
    )
}
