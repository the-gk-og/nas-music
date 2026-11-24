package org.nasmusic.app.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
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
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.AppVoiceCommand
import org.nasmusic.app.data.api.Queue
import org.nasmusic.app.data.api.VoiceResult
import org.nasmusic.app.voice.Heard
import org.nasmusic.app.voice.SpeechModel
import org.nasmusic.app.voice.VoiceAction
import org.nasmusic.app.voice.VoiceCommands
import org.nasmusic.app.voice.VoiceListener

private sealed interface VoiceStep {
    data object Offer : VoiceStep                          // first use: download the speech model?
    data class Downloading(val progress: Float) : VoiceStep
    data class Listening(val partial: String) : VoiceStep
    data class Sending(val text: String) : VoiceStep
    data class Done(val text: String, val result: VoiceResult) : VoiceStep
    data class Failed(val message: String, val heard: String? = null) : VoiceStep
}

/**
 * The mic button (search bar): say "play something chill", "add this to Gym", "skip", "play my Pop radio in
 * the bedroom". Speech is recognised on the phone by Vosk (no Google, works without internet once the model
 * is downloaded); the text goes to your server, which understands it the same way as Home Assistant's
 * voice commands. [onSearch] puts words the server didn't understand into the search box instead.
 */
@Composable
fun VoiceCommandButton(c: AppContainer, onSearch: (String) -> Unit) {
    val scope = rememberCoroutineScope()
    val model = remember { SpeechModel(c.context.filesDir, c.baseClient) }
    var step by remember { mutableStateOf<VoiceStep?>(null) }
    var job by remember { mutableStateOf<Job?>(null) }

    fun close() { job?.cancel(); job = null; step = null }

    fun send(text: String) {
        step = VoiceStep.Sending(text)
        job = scope.launch {
            val now = c.player.state.value
            val r = runCatching { c.api.voiceCommand(AppVoiceCommand(text, trackId = now.item?.mediaId?.takeIf { it.isNotEmpty() })) }
                .getOrElse { step = VoiceStep.Failed("Couldn't reach your server.", text); return@launch }
            val shown = if (r.local && r.intent == "now_playing") {
                val md = now.item?.mediaMetadata
                VoiceCommands.nowPlayingSpeech(md?.title?.toString(), md?.artist?.toString(), now.isPlaying)?.let { r.copy(speech = it) } ?: r
            } else r
            when (val a = VoiceCommands.action(r, now.volume)) {
                is VoiceAction.PlayQueue -> c.player.playQueue(Queue(a.queueId, r.item?.title ?: "Voice"), 0)
                VoiceAction.Pause -> c.player.pause()
                VoiceAction.Resume -> { if (!now.isPlaying) c.player.togglePlay() }
                VoiceAction.Next -> c.player.next()
                VoiceAction.Previous -> c.player.previous()
                is VoiceAction.SetVolume -> c.player.setVolume(a.volume)
                VoiceAction.None -> Unit
            }
            step = VoiceStep.Done(text, shown)
            if (shown.ok) { delay(2500); if (step is VoiceStep.Done) step = null }
        }
    }

    fun listen() {
        step = VoiceStep.Listening("")
        job = scope.launch {
            VoiceListener.listen(model.dir).collect { h ->
                when (h) {
                    is Heard.Partial -> step = VoiceStep.Listening(h.text)
                    is Heard.Final -> if (h.text.isBlank()) step = VoiceStep.Failed("I didn't hear anything.") else send(h.text)
                    is Heard.Failed -> step = VoiceStep.Failed("The microphone isn't working: ${h.error}")
                }
            }
        }
    }

    fun download() {
        step = VoiceStep.Downloading(-1f)
        job = scope.launch {
            runCatching { model.download { p -> step = VoiceStep.Downloading(p) } }
                .onSuccess { listen() }
                .onFailure { step = VoiceStep.Failed("Couldn't download the speech model: ${it.message}") }
        }
    }

    fun start() {
        if (model.isInstalled()) listen() else step = VoiceStep.Offer
    }

    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) start() else step = VoiceStep.Failed("Voice commands need the microphone. You can allow it in Android's app settings.")
    }

    IconButton(onClick = {
        if (ContextCompat.checkSelfPermission(c.context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) start()
        else permission.launch(Manifest.permission.RECORD_AUDIO)
    }) { Icon(Icons.Default.Mic, "Voice command") }

    val s = step ?: return
    AlertDialog(
        onDismissRequest = ::close,
        title = {
            Text(
                when (s) {
                    VoiceStep.Offer, is VoiceStep.Downloading -> "Voice commands"
                    is VoiceStep.Listening -> "Listening…"
                    is VoiceStep.Sending -> "One moment…"
                    is VoiceStep.Done -> if (s.result.ok) "Done" else "Hmm"
                    is VoiceStep.Failed -> "Voice commands"
                },
            )
        },
        text = {
            Column(Modifier.fillMaxWidth()) {
                when (s) {
                    VoiceStep.Offer -> Text(
                        "Voice commands understand your speech on this phone, with no Google services. That needs a " +
                            "${SpeechModel.SIZE_MB} MB English speech model, downloaded once from alphacephei.com (Vosk). " +
                            "Only the words you say are sent, and only to your own server.",
                    )
                    is VoiceStep.Downloading -> {
                        Text("Downloading the speech model…")
                        if (s.progress in 0f..1f) LinearProgressIndicator(progress = { s.progress }, modifier = Modifier.fillMaxWidth().padding(top = 12.dp))
                        else LinearProgressIndicator(Modifier.fillMaxWidth().padding(top = 12.dp))
                    }
                    is VoiceStep.Listening -> {
                        Text(s.partial.ifEmpty { "Try \"play something chill\", \"add this to Gym\" or \"skip\"." },
                            style = if (s.partial.isEmpty()) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.titleMedium)
                    }
                    is VoiceStep.Sending -> Text("“${s.text}”", style = MaterialTheme.typography.titleMedium)
                    is VoiceStep.Done -> {
                        Text("“${s.text}”", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(s.result.speech, style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 8.dp))
                    }
                    is VoiceStep.Failed -> {
                        s.heard?.let { Text("“$it”", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                        Text(s.message, modifier = Modifier.padding(top = 4.dp))
                    }
                }
            }
        },
        confirmButton = {
            when (s) {
                VoiceStep.Offer -> TextButton(onClick = ::download) { Text("Download") }
                is VoiceStep.Listening -> TextButton(onClick = ::close) { Text("Cancel") }
                is VoiceStep.Done -> {
                    if (s.result.ok) TextButton(onClick = ::close) { Text("OK") }
                    else TextButton(onClick = { job?.cancel(); listen() }) { Text("Try again") }
                }
                is VoiceStep.Failed -> TextButton(onClick = { job?.cancel(); start() }) { Text("Try again") }
                else -> TextButton(onClick = ::close) { Text("Cancel") }
            }
        },
        dismissButton = {
            when (s) {
                VoiceStep.Offer -> TextButton(onClick = ::close) { Text("Not now") }
                is VoiceStep.Done -> {
                    if (!s.result.ok && s.result.intent == "unknown") TextButton(onClick = { close(); onSearch(s.text) }) { Text("Search instead") }
                    else if (!s.result.ok) TextButton(onClick = ::close) { Text("Close") }
                }
                is VoiceStep.Failed -> TextButton(onClick = ::close) { Text("Close") }
                else -> Unit
            }
        },
    )
}
