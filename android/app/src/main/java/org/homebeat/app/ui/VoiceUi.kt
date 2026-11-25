package org.homebeat.app.ui

import android.content.ClipData
import android.content.ClipboardManager
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
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
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import org.homebeat.app.AppContainer
import org.homebeat.app.data.api.NewVoiceToken
import org.homebeat.app.data.api.VoiceDevice
import org.homebeat.app.data.api.VoiceDevicePrefs
import org.homebeat.app.data.api.VoiceToken

/** Splits "bedroom, upstairs ,  " into ["bedroom", "upstairs"]. */
fun parseAliases(raw: String): List<String> = raw.split(',').map { it.trim().replace(Regex("\\s+"), " ") }.filter { it.isNotEmpty() }

/**
 * Settings section (api 0.7): voice tokens for Home Assistant Assist and the names each device/speaker answers
 * to by voice. A voice token can only send voice commands (POST /voice/command); it's shown once.
 */
@Composable
fun VoiceSettings(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var tokens by remember { mutableStateOf<List<VoiceToken>>(emptyList()) }
    var devices by remember { mutableStateOf<List<VoiceDevice>>(emptyList()) }
    var name by remember { mutableStateOf("Home Assistant") }
    var created by remember { mutableStateOf<String?>(null) }
    var revoking by remember { mutableStateOf<VoiceToken?>(null) }

    suspend fun reload() {
        tokens = runCatching { c.api.voiceTokens().tokens }.getOrDefault(tokens)
        devices = runCatching { c.api.voiceDevices().devices }.getOrDefault(devices)
    }
    LaunchedEffect(Unit) { reload() }

    Text(
        "Control your music by voice through Home Assistant Assist (\"play my Pop radio in the bedroom\", \"skip\", " +
            "\"what's playing?\"). Create a voice token and paste it into Home Assistant — see deploy/homeassistant/README.md. " +
            "A voice token can only send voice commands: it can't sign in, browse or stream.",
        style = MaterialTheme.typography.bodySmall,
    )
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(name, { name = it.take(100) }, label = { Text("Token name") }, singleLine = true, modifier = Modifier.weight(1f))
        OutlinedButton(onClick = {
            scope.launch {
                runCatching { c.api.createVoiceToken(NewVoiceToken(name.trim().ifEmpty { "Home Assistant" })) }
                    .onSuccess { created = it.token; reload() }
                    .onFailure { ui.toast = "Couldn't create a voice token" }
            }
        }) { Text("Create") }
    }
    tokens.forEach { t ->
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(t.name)
                Text("Last used: ${t.lastUsed?.take(16)?.replace('T', ' ') ?: "never"}", style = MaterialTheme.typography.bodySmall)
            }
            TextButton(onClick = { revoking = t }) { Text("Revoke") }
        }
    }

    Text(
        "Names for voice: the rooms or nicknames you'll say, separated by commas. The default plays when you don't " +
            "name a room and nothing is playing.",
        style = MaterialTheme.typography.bodySmall,
        modifier = Modifier.padding(top = 8.dp),
    )
    devices.forEach { d ->
        var text by remember(d.id, d.aliases) { mutableStateOf(d.aliases.joinToString(", ")) }
        fun save() {
            val want = parseAliases(text)
            if (want == d.aliases) return
            scope.launch {
                runCatching { c.api.putVoiceDevice(d.id, VoiceDevicePrefs(aliases = want)) }
                    .onSuccess { r -> devices = devices.map { if (it.id == d.id) it.copy(aliases = r.aliases ?: want) else it } }
                    .onFailure { ui.toast = "Couldn't save" }
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(d.name)
                OutlinedTextField(
                    text, { text = it.take(300) }, singleLine = true,
                    label = { Text((d.castType ?: d.kind) + if (d.online) "" else " · offline") },
                    placeholder = { Text("e.g. bedroom, upstairs") },
                    modifier = Modifier.fillMaxWidth().onFocusChanged { if (!it.isFocused) save() },
                )
            }
            RadioButton(selected = d.isDefault, onClick = {
                scope.launch {
                    runCatching { c.api.putVoiceDevice(d.id, VoiceDevicePrefs(isDefault = true)) }
                        .onSuccess { devices = devices.map { it.copy(isDefault = it.id == d.id) } }
                }
            })
        }
    }

    created?.let { tok ->
        AlertDialog(
            onDismissRequest = { created = null },
            title = { Text("Voice token") },
            text = {
                Column {
                    Text("Copy it now — it won't be shown again. Paste it into Home Assistant's secrets.yaml.")
                    SelectionContainer { Text(tok, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 8.dp)) }
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    c.context.getSystemService(ClipboardManager::class.java)?.setPrimaryClip(ClipData.newPlainText("Homebeat voice token", tok))
                    ui.toast = "Copied"
                }) { Text("Copy") }
            },
            dismissButton = { TextButton(onClick = { created = null }) { Text("Done") } },
        )
    }
    revoking?.let { t ->
        AlertDialog(
            onDismissRequest = { revoking = null },
            title = { Text("Revoke ${t.name}?") },
            text = { Text("It stops working at once. Home Assistant will need a new token.") },
            confirmButton = {
                TextButton(onClick = {
                    revoking = null
                    scope.launch { runCatching { c.api.revokeVoiceToken(t.id) }; reload() }
                }) { Text("Revoke") }
            },
            dismissButton = { TextButton(onClick = { revoking = null }) { Text("Cancel") } },
        )
    }
}
