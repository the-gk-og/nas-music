package org.homebeat.app.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import org.homebeat.app.AppContainer
import org.homebeat.app.playback.DjVoiceState

/** How often the DJ may talk (`dj.every`). */
private val DJ_EVERY = listOf(1 to "Every song", 3 to "Every 3rd", 5 to "Every 5th")

/** Settings → Radio DJ (api 0.8.2). Shows nothing when the server can't talk (older server, or no Piper). */
@Composable
fun DjVoiceSettings(c: AppContainer, header: @Composable () -> Unit) {
    val scope = rememberCoroutineScope()
    val st by DjVoiceState.status.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { DjVoiceState.refresh(c.api) }
    val s = st ?: return
    if (!s.available) return
    header()
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("DJ voice")
            Text(
                "In radio and mixes, a voice introduces some songs over their opening, with facts about the artist " +
                    "from your server. The music dips while it talks. Never after you skip, and not when casting.",
                style = MaterialTheme.typography.bodySmall,
            )
        }
        Switch(checked = s.enabled, onCheckedChange = { on -> scope.launch { DjVoiceState.set(c.api, enabled = on) } })
    }
    if (s.enabled) {
        Text("How often (always before the first song of a radio)")
        SingleChoiceSegmentedButtonRow {
            DJ_EVERY.forEachIndexed { i, (k, l) ->
                SegmentedButton(
                    selected = s.every == k,
                    onClick = { scope.launch { DjVoiceState.set(c.api, every = k) } },
                    shape = SegmentedButtonDefaults.itemShape(i, DJ_EVERY.size),
                ) { Text(l) }
            }
        }
        when (s.state) {
            "downloading" -> Text("Your server is downloading the voice. The DJ starts talking once it's ready.",
                style = MaterialTheme.typography.bodySmall)
            "error" -> Text("The DJ can't talk right now: ${s.detail ?: "unknown error"}", style = MaterialTheme.typography.bodySmall)
        }
    }
}
