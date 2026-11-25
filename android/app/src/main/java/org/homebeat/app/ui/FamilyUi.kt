package org.homebeat.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import org.homebeat.app.AppContainer
import org.homebeat.app.data.FamilyRepository
import org.homebeat.app.data.api.FamilyUpdate

/** The "E" badge (api 0.6) next to explicit tracks/albums; "probable" (lyrics word list) is an outlined E. */
@Composable
fun ExplicitBadge(status: String?, modifier: Modifier = Modifier) {
    if (status != "explicit" && status != "probable") return
    val c = MaterialTheme.colorScheme.onSurfaceVariant
    val shape = RoundedCornerShape(3.dp)
    val label = if (status == "explicit") "Explicit" else "Probably explicit"
    Box(
        modifier.padding(end = 6.dp).size(16.dp).semantics { contentDescription = label }
            .then(if (status == "explicit") Modifier.background(c, shape) else Modifier.border(1.dp, c, shape)),
        contentAlignment = Alignment.Center,
    ) {
        Text("E", fontSize = 10.sp, lineHeight = 10.sp, fontWeight = FontWeight.Bold,
            color = if (status == "explicit") MaterialTheme.colorScheme.surface else c)
    }
}

/**
 * Changes family mode, asking for the admin's PIN when a locked policy would be loosened. Call [update];
 * put [Dialog] somewhere in the composition.
 */
class FamilyActions(private val c: AppContainer, private val launch: (suspend () -> Unit) -> Unit, private val toast: (String) -> Unit) {
    var pending by mutableStateOf<FamilyUpdate?>(null)

    fun update(u: FamilyUpdate) = launch {
        when (val r = c.family.update(u)) {
            FamilyRepository.Result.Ok -> {}
            FamilyRepository.Result.NeedsPin -> pending = u
            is FamilyRepository.Result.Failed -> toast(r.message)
        }
    }

    fun toggle() = update(FamilyUpdate(enabled = !c.family.mode.value.enabled))

    @Composable
    fun Dialog() {
        val u = pending ?: return
        var pin by remember { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { pending = null },
            title = { Text("Family mode is locked") },
            text = {
                Column {
                    Text("Enter the unlock PIN your admin set.")
                    OutlinedTextField(
                        pin, { v -> pin = v.filter { it.isDigit() }.take(12) }, label = { Text("PIN") }, singleLine = true,
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                        modifier = Modifier.padding(top = 8.dp),
                    )
                }
            },
            confirmButton = { TextButton(onClick = { pending = null; update(u.copy(pin = pin)) }, enabled = pin.length >= 4) { Text("Unlock") } },
            dismissButton = { TextButton(onClick = { pending = null }) { Text("Cancel") } },
        )
    }
}

@Composable
fun rememberFamilyActions(c: AppContainer): FamilyActions {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    return remember(c) { FamilyActions(c, { block -> scope.launch { block() } }, { ui.toast = it }) }
}

/** Settings section: family mode switch + what it hides. */
@Composable
fun FamilySettings(c: AppContainer) {
    val m by c.family.mode.collectAsStateWithLifecycle()
    val actions = rememberFamilyActions(c)
    actions.Dialog()
    Text(
        "Hides explicit songs everywhere — library, search, mixes, radio — and skips them in queues. Your server does " +
            "the filtering, so it applies on all your devices." +
            if (m.locked) (if (m.hasPin) " Locked by an admin: loosening it needs the unlock PIN." else " Locked by an admin.") else "",
        style = MaterialTheme.typography.bodySmall,
    )
    SwitchLine(if (m.locked) "Family mode 🔒" else "Family mode", "Hide songs marked explicit", m.enabled) { actions.update(FamilyUpdate(enabled = it)) }
    if (m.enabled) {
        SwitchLine("Also hide “probably explicit”", "Lyrics contain swearing (found automatically, not certain)", m.hideProbable) {
            actions.update(FamilyUpdate(hideProbable = it))
        }
        SwitchLine("Also hide unchecked songs", "Strictest: hides songs nobody has classified yet", m.hideUnknown) {
            actions.update(FamilyUpdate(hideUnknown = it))
        }
    }
}

@Composable
private fun SwitchLine(title: String, sub: String, on: Boolean, set: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title)
            Text(sub, style = MaterialTheme.typography.bodySmall)
        }
        Switch(on, set)
    }
}
