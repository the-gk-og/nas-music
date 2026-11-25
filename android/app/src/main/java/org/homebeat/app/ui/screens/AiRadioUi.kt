package org.homebeat.app.ui.screens

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Bedtime
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.LocalFireDepartment
import androidx.compose.material.icons.filled.Radio
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material.icons.filled.ThumbDown
import androidx.compose.material.icons.filled.ThumbUp
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.InputChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import org.homebeat.app.AppContainer
import org.homebeat.app.data.api.RadioSeed
import org.homebeat.app.data.api.RadioStation
import org.homebeat.app.data.api.VibeConstraint
import org.homebeat.app.data.api.VibeParseRequest
import org.homebeat.app.playback.AiRadio
import org.homebeat.app.ui.LocalUi

/** Home: "Play something for now" (smart shuffle), "Describe a vibe", and the random radio. */
@Composable
fun StartRadioButtons(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Button(
            onClick = { scope.launch { if (c.player.startRadio(RadioSeed("moment"))) ui.go("now") else ui.toast = "Couldn't start the smart shuffle" } },
            modifier = Modifier.fillMaxWidth().height(64.dp),
            shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.primary),
        ) { Icon(Icons.Default.AutoAwesome, null); Spacer(Modifier.width(12.dp)); Text("Play something for now", style = MaterialTheme.typography.titleMedium) }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedButton(onClick = { ui.vibeOpen = true }, modifier = Modifier.weight(1f).height(52.dp), shape = RoundedCornerShape(14.dp)) {
                Icon(Icons.Default.Edit, null); Spacer(Modifier.width(8.dp)); Text("Describe a vibe", maxLines = 1)
            }
            OutlinedButton(
                onClick = { scope.launch { if (c.player.startRadio(RadioSeed("random"))) ui.go("now") else ui.toast = "Couldn't start the radio" } },
                modifier = Modifier.weight(1f).height(52.dp), shape = RoundedCornerShape(14.dp),
            ) { Icon(Icons.Default.Radio, null); Spacer(Modifier.width(8.dp)); Text("Random radio", maxLines = 1) }
        }
    }
}

/**
 * Describe-a-vibe: type, or dictate with the keyboard's microphone; the server interprets it as chips the user
 * can drop before starting an endless station.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun VibeDialog(c: AppContainer, onDismiss: () -> Unit) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var text by remember { mutableStateOf("") }
    var parsedFor by remember { mutableStateOf<String?>(null) }
    var chips by remember { mutableStateOf<List<VibeConstraint>>(emptyList()) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    suspend fun interpret(t: String) {
        val q = t.trim()
        if (q.isEmpty()) return
        busy = true; error = null
        val r = runCatching { c.api.parseVibe(VibeParseRequest(q)) }
        busy = false
        r.onSuccess {
            chips = it.constraints; parsedFor = q
            if (chips.isEmpty()) error = "Nothing recognised yet; try words like chill, upbeat, 90s, jazz, no vocals."
        }.onFailure { error = "Vibe radio isn't available on this server yet" }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Describe a vibe") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = text, onValueChange = { text = it.take(200) },
                    placeholder = { Text("chill rainy evening…") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                    keyboardActions = KeyboardActions(onSearch = { scope.launch { interpret(text) } }),
                    modifier = Modifier.fillMaxWidth(),
                )
                if (chips.isEmpty() && parsedFor == null) {
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        AiRadio.VIBE_EXAMPLES.forEach { ex ->
                            SuggestionChip(onClick = { text = ex; scope.launch { interpret(ex) } }, label = { Text(ex) })
                        }
                    }
                }
                if (chips.isNotEmpty()) {
                    Text("Understood as (tap to drop):", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        chips.forEach { chip ->
                            InputChip(
                                selected = true, onClick = { chips = chips.filterNot { it.id == chip.id } },
                                label = { Text(chip.label) },
                                trailingIcon = { Icon(Icons.Default.Close, "Remove ${chip.label}", Modifier.size(16.dp)) },
                            )
                        }
                    }
                }
                error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
        },
        confirmButton = {
            TextButton(enabled = !busy && (chips.isNotEmpty() || text.isNotBlank()), onClick = {
                scope.launch {
                    val q = text.trim()
                    if (q.isNotEmpty() && q != parsedFor) interpret(q)
                    if (chips.isEmpty() && q.isEmpty()) return@launch
                    onDismiss()
                    if (c.player.startRadio(RadioSeed("vibe", text = q, constraints = chips))) ui.go("now")
                    else ui.toast = "Couldn't start that vibe"
                }
            }) { Text("Start radio") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

/**
 * Now Playing, while a radio plays: why these picks, and steering that changes the next tracks right away.
 * More/less/never ask "this song or this artist".
 */
@Composable
fun RadioSteerBar(c: AppContainer, queueKey: String?) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val qid = queueKey ?: return
    var station by remember(qid) { mutableStateOf<RadioStation?>(null) }
    var busy by remember { mutableStateOf(false) }
    var showWhy by remember { mutableStateOf(false) }
    LaunchedEffect(qid) { station = c.player.radioStation(qid) }

    fun steer(action: String, scope0: String = "track", refId: String? = null) {
        if (busy) return
        busy = true
        scope.launch {
            val st = c.player.steerRadio(action, scope0, refId)
            busy = false
            if (st == null) { ui.toast = "Steering isn't available right now"; return@launch }
            station = st
            ui.toast = when (action) {
                AiRadio.RESET -> "Steering reset"
                AiRadio.REMOVE -> "Undone"
                else -> "${steerLabel(action)}${if (scope0 == "artist") " (artist)" else ""} — next tracks updated"
            }
        }
    }

    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        station?.explanation?.let { why ->
            Text(
                why, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp),
            )
            TextButton(onClick = { showWhy = true }, contentPadding = ButtonDefaults.TextButtonContentPadding) {
                Text("Why these songs? · steer", style = MaterialTheme.typography.labelSmall)
            }
        }
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            ScopedChip("More like", Icons.Default.ThumbUp, busy) { steer(AiRadio.MORE_LIKE, it) }
            ScopedChip("Less like", Icons.Default.ThumbDown, busy) { steer(AiRadio.LESS_LIKE, it) }
            SteerChip("Energy", Icons.Default.LocalFireDepartment, busy) { steer(AiRadio.MORE_ENERGY) }
            SteerChip("Calmer", Icons.Default.Bedtime, busy) { steer(AiRadio.CALMER) }
            SteerChip("Variety", Icons.Default.Shuffle, busy) { steer(AiRadio.MORE_VARIETY) }
            SteerChip("Familiar", Icons.Default.History, busy) { steer(AiRadio.MORE_FAMILIAR) }
            ScopedChip("Never here", Icons.Default.Block, busy) { steer(AiRadio.NEVER_HERE, it) }
            if (station?.steeringSummary != null) SteerChip("Reset", Icons.Default.Refresh, busy) { steer(AiRadio.RESET) }
        }
    }

    if (showWhy) {
        val st = station
        AlertDialog(
            onDismissRequest = { showWhy = false },
            title = { Text(st?.title ?: "Radio") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    st?.explanation?.let { Text(it) }
                    st?.steeringSummary?.let { Text("Steered: $it", style = MaterialTheme.typography.bodySmall) }
                    val refs = st?.steering?.let { s -> s.moreLike.map { "+" to it } + s.lessLike.map { "−" to it } + s.never.map { "⊘" to it } }.orEmpty()
                    refs.forEach { (k, r) ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("$k ${r.name ?: r.type}", Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                            TextButton(onClick = { steer(AiRadio.REMOVE, r.type, r.id) }) { Text("Undo") }
                        }
                    }
                }
            },
            confirmButton = { TextButton(onClick = { showWhy = false }) { Text("Done") } },
        )
    }
}

private fun steerLabel(action: String) = when (action) {
    AiRadio.MORE_LIKE -> "More like this"
    AiRadio.LESS_LIKE -> "Less like this"
    AiRadio.MORE_ENERGY -> "More energy"
    AiRadio.CALMER -> "Calmer"
    AiRadio.MORE_VARIETY -> "More variety"
    AiRadio.MORE_FAMILIAR -> "More familiar"
    AiRadio.NEVER_HERE -> "Never on this station"
    else -> action
}

@Composable
private fun SteerChip(label: String, icon: ImageVector, busy: Boolean, onClick: () -> Unit) {
    AssistChip(onClick = onClick, enabled = !busy, label = { Text(label) }, leadingIcon = { Icon(icon, null, Modifier.size(18.dp)) })
}

/** A chip that asks "this song or this artist?" and calls back with "track" or "artist". */
@Composable
private fun ScopedChip(label: String, icon: ImageVector, busy: Boolean, onPick: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        SteerChip(label, icon, busy) { open = true }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            DropdownMenuItem(text = { Text("This song") }, onClick = { open = false; onPick("track") })
            DropdownMenuItem(text = { Text("This artist") }, onClick = { open = false; onPick("artist") })
        }
    }
}
