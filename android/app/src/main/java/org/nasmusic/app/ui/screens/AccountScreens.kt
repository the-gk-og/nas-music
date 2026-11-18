package org.nasmusic.app.ui.screens

import android.content.ClipData
import android.content.ClipboardManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.google.zxing.BarcodeFormat
import com.journeyapps.barcodescanner.BarcodeEncoder
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.nasmusic.app.AppContainer
import org.nasmusic.app.context.ContextSignal
import org.nasmusic.app.data.api.AdminUser
import org.nasmusic.app.data.api.AdminUserPatch
import org.nasmusic.app.data.api.Invite
import org.nasmusic.app.data.api.MyDevice
import org.nasmusic.app.data.api.NewInvite
import org.nasmusic.app.data.api.NewPassword
import org.nasmusic.app.data.api.PairCode
import org.nasmusic.app.data.api.PasswordChange
import org.nasmusic.app.ui.LocalUi
import org.nasmusic.app.ui.ScreenTitle

@Composable
private fun Section(title: String) =
    Text(title, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(top = 12.dp))

private fun copy(c: AppContainer, text: String) =
    c.context.getSystemService(ClipboardManager::class.java)?.setPrimaryClip(ClipData.newPlainText("nas-music", text))

// ---------- Account ----------

@Composable
fun AccountScreen(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val cfg by c.secureStore.config.collectAsStateWithLifecycle()
    var devices by remember { mutableStateOf<List<MyDevice>>(emptyList()) }
    var pair by remember { mutableStateOf<PairCode?>(null) }
    var pwOpen by remember { mutableStateOf(false) }
    var current by remember { mutableStateOf("") }
    var next by remember { mutableStateOf("") }
    var logoutConfirm by remember { mutableStateOf(false) }
    suspend fun reload() { devices = runCatching { c.api.myDevices() }.getOrDefault(emptyList()) }
    LaunchedEffect(Unit) { reload() }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        ScreenTitle("Account")
        Text("Signed in as ${cfg?.username ?: "?"}" + if (cfg?.isAdmin == true) " (admin)" else "")
        Text("Server: ${cfg?.name} · ${cfg?.tunnelUrl ?: cfg?.lanUrl}", color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedButton(onClick = { pwOpen = true }) { Text("Change password") }

        Section("Your devices")
        devices.forEach { d ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(d.name + if (d.id == cfg?.deviceId) " (this phone)" else "")
                    Text("${d.kind} · ${if (d.online) "online" else "last seen ${d.lastSeen ?: "never"}"}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (d.id != cfg?.deviceId) IconButton(onClick = { scope.launch { runCatching { c.api.deleteDevice(d.id) }; reload() } }) {
                    Icon(Icons.Default.Delete, "Sign out ${d.name}")
                }
            }
        }
        OutlinedButton(onClick = {
            scope.launch { pair = runCatching { c.api.pairCode() }.onFailure { ui.toast = "Couldn't create a pairing code" }.getOrNull() }
        }) { Text("Add a phone (QR)") }

        Section("Data & recommendations")
        OutlinedButton(onClick = { ui.go("data") }) { Text("Choose what to collect") }

        HorizontalDivider(Modifier.padding(vertical = 8.dp))
        Button(onClick = { logoutConfirm = true }) { Text("Log out") }
    }

    pair?.let { p ->
        val bmp = remember(p) { runCatching { BarcodeEncoder().encodeBitmap(p.qrText, BarcodeFormat.QR_CODE, 720, 720) }.getOrNull() }
        AlertDialog(
            onDismissRequest = { pair = null },
            title = { Text("Scan on the other phone") },
            text = {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    bmp?.let { Image(it.asImageBitmap(), "Pairing QR", Modifier.size(260.dp).background(Color.White).padding(8.dp)) }
                    Text("Code: ${p.pairCode}", fontFamily = FontFamily.Monospace)
                    Text("Valid for 10 minutes, once.", style = MaterialTheme.typography.bodySmall)
                }
            },
            confirmButton = { TextButton(onClick = { pair = null }) { Text("Done") } },
        )
    }
    if (pwOpen) {
        AlertDialog(
            onDismissRequest = { pwOpen = false },
            title = { Text("Change password") },
            text = {
                Column {
                    OutlinedTextField(current, { current = it }, label = { Text("Current password") }, visualTransformation = PasswordVisualTransformation(), singleLine = true)
                    OutlinedTextField(next, { next = it }, label = { Text("New password (8+)") }, visualTransformation = PasswordVisualTransformation(), singleLine = true)
                }
            },
            confirmButton = {
                TextButton(enabled = next.length >= 8, onClick = {
                    scope.launch {
                        val ok = runCatching { c.api.changePassword(PasswordChange(current, next)).isSuccessful }.getOrDefault(false)
                        ui.toast = if (ok) "Password changed" else "Couldn't change the password"
                        pwOpen = false; current = ""; next = ""
                    }
                }) { Text("Save") }
            },
            dismissButton = { TextButton(onClick = { pwOpen = false }) { Text("Cancel") } },
        )
    }
    if (logoutConfirm) {
        AlertDialog(
            onDismissRequest = { logoutConfirm = false },
            title = { Text("Log out?") },
            text = { Text("This phone's sign-in is revoked. Downloaded music stays until the next sync.") },
            confirmButton = { TextButton(onClick = { logoutConfirm = false; c.connect.stop(); scope.launch { c.auth.logout(c.api); c.connect.start() } }) { Text("Log out") } },
            dismissButton = { TextButton(onClick = { logoutConfirm = false }) { Text("Cancel") } },
        )
    }
}

// ---------- Admin ----------

@Composable
fun AdminScreen(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var invites by remember { mutableStateOf<List<Invite>>(emptyList()) }
    var users by remember { mutableStateOf<List<AdminUser>>(emptyList()) }
    var note by remember { mutableStateOf("") }
    var created by remember { mutableStateOf<Invite?>(null) }
    var resetFor by remember { mutableStateOf<AdminUser?>(null) }
    var newPw by remember { mutableStateOf("") }
    suspend fun reload() {
        invites = runCatching { c.api.invites() }.getOrDefault(emptyList())
        users = runCatching { c.api.users() }.getOrDefault(emptyList())
    }
    LaunchedEffect(Unit) { reload() }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        ScreenTitle("Admin")
        Section("Invites")
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(note, { note = it }, label = { Text("Note (who is it for?)") }, singleLine = true, modifier = Modifier.weight(1f))
            TextButton(onClick = {
                scope.launch {
                    created = runCatching { c.api.createInvite(NewInvite(note.ifBlank { null })) }.onFailure { ui.toast = "Couldn't create invite" }.getOrNull()
                    note = ""; reload()
                }
            }) { Text("Create") }
        }
        invites.forEach { i ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(i.code, fontFamily = FontFamily.Monospace)
                    Text(listOfNotNull(i.note, i.usedBy?.let { "used" } ?: i.expiresAt?.let { "expires $it" }).joinToString(" · "), style = MaterialTheme.typography.bodySmall)
                }
                IconButton(onClick = { scope.launch { runCatching { c.api.deleteInvite(i.code) }; reload() } }) { Icon(Icons.Default.Delete, "Delete invite") }
            }
        }
        Section("Users")
        users.forEach { u ->
            Column {
                Text(u.username, style = MaterialTheme.typography.titleSmall)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Admin", Modifier.padding(end = 8.dp))
                    Switch(u.isAdmin, { on -> scope.launch { runCatching { c.api.patchUser(u.id, AdminUserPatch(isAdmin = on)) }.onFailure { ui.toast = "Not allowed" }; reload() } })
                    Text("Disabled", Modifier.padding(start = 16.dp, end = 8.dp))
                    Switch(u.disabled, { on -> scope.launch { runCatching { c.api.patchUser(u.id, AdminUserPatch(disabled = on)) }.onFailure { ui.toast = "Not allowed" }; reload() } })
                }
                TextButton(onClick = { resetFor = u }) { Text("Reset password") }
                HorizontalDivider()
            }
        }
    }

    created?.let { i ->
        AlertDialog(
            onDismissRequest = { created = null },
            title = { Text("Invite created") },
            text = { Column { Text(i.code, fontFamily = FontFamily.Monospace); i.url?.let { Text(it) }; Text("Send this link or code to the person you're inviting.") } },
            confirmButton = { TextButton(onClick = { copy(c, i.url ?: i.code); ui.toast = "Copied"; created = null }) { Text("Copy") } },
        )
    }
    resetFor?.let { u ->
        AlertDialog(
            onDismissRequest = { resetFor = null },
            title = { Text("New password for ${u.username}") },
            text = { OutlinedTextField(newPw, { newPw = it }, visualTransformation = PasswordVisualTransformation(), singleLine = true) },
            confirmButton = {
                TextButton(enabled = newPw.length >= 8, onClick = {
                    scope.launch { runCatching { c.api.resetPassword(u.id, NewPassword(newPw)) }; newPw = ""; resetFor = null; ui.toast = "Password reset" }
                }) { Text("Set") }
            },
            dismissButton = { TextButton(onClick = { resetFor = null }) { Text("Cancel") } },
        )
    }
}

// ---------- Data & recommendations (docs/v2.md §10) ----------

@Composable
fun DataSettingsScreen(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val enabled by c.contextCollector.enabled.collectAsStateWithLifecycle()
    val places by c.contextCollector.places.collectAsStateWithLifecycle()
    var pendingOn by remember { mutableStateOf<List<ContextSignal>>(emptyList()) }
    var data by remember { mutableStateOf<JsonObject?>(null) }
    var deleteConfirm by remember { mutableStateOf<String?>(null) }
    var renaming by remember { mutableStateOf<Pair<Int, String>?>(null) }

    // Permissions are asked only when a switch that needs them is turned on.
    val permissions = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
        val ok = pendingOn.filter { s -> s.permissions().isEmpty() || s.permissions().any { granted[it] == true } || c.contextCollector.hasPermissions(s) }
        c.contextCollector.setAll(pendingOn.associateWith { it in ok }, c.api)
        if (ok.size < pendingOn.size) ui.toast = "Some switches stay off without the permission"
        pendingOn = emptyList()
    }
    fun turnOn(signals: List<ContextSignal>) {
        val needPerms = signals.filter { it.permissions().isNotEmpty() && !c.contextCollector.hasPermissions(it) }
        val noPerms = signals - needPerms.toSet()
        if (noPerms.isNotEmpty()) c.contextCollector.setAll(noPerms.associateWith { true }, c.api)
        if (needPerms.isNotEmpty()) {
            pendingOn = needPerms
            permissions.launch(needPerms.flatMap { it.permissions() }.distinct().toTypedArray())
        }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        ScreenTitle("Data & recommendations")
        Text(
            "Plays, skips, favourites, ratings and thumbs are always used. Everything below is off until you switch it on, " +
                "and only ever goes to your own server.",
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        OutlinedButton(onClick = { turnOn(ContextSignal.entries.filter { it !in enabled }) }) { Text("Turn everything on") }
        ContextSignal.entries.forEach { s ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f).padding(end = 8.dp)) {
                    Text(s.title, style = MaterialTheme.typography.titleSmall)
                    Text(s.explanation, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Switch(s in enabled, { on -> if (on) turnOn(listOf(s)) else c.contextCollector.set(s, false, c.api) })
            }
        }
        if (ContextSignal.LOCATION in enabled) {
            Section("Your places")
            if (places.isEmpty()) Text("No places yet. They appear after you've listened somewhere.", style = MaterialTheme.typography.bodySmall)
            places.sortedByDescending { it.count }.forEach { p ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("${p.label}  (${p.count})", Modifier.weight(1f))
                    TextButton(onClick = { renaming = p.id to p.label }) { Text("Name") }
                    IconButton(onClick = { c.contextCollector.forgetPlace(p.id) }) { Icon(Icons.Default.Delete, "Forget place") }
                }
            }
        }
        HorizontalDivider(Modifier.padding(vertical = 8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { scope.launch { data = runCatching { c.api.myData() }.onFailure { ui.toast = "Couldn't load your data" }.getOrNull() } }) { Text("View my data") }
            OutlinedButton(onClick = { deleteConfirm = "context" }) { Text("Delete") }
        }
    }

    data?.let { d ->
        AlertDialog(
            onDismissRequest = { data = null },
            title = { Text("Your data on the server") },
            text = { Text(Json { prettyPrint = true }.encodeToString(JsonObject.serializer(), d), fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall, modifier = Modifier.verticalScroll(rememberScrollState())) },
            confirmButton = { TextButton(onClick = { data = null }) { Text("Close") } },
        )
    }
    deleteConfirm?.let { kind ->
        AlertDialog(
            onDismissRequest = { deleteConfirm = null },
            title = { Text("Delete collected data?") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("Choose what to erase on the server. This can't be undone.")
                    ContextSignal.entries.forEach { s ->
                        TextButton(onClick = { deleteConfirm = null; scope.launch { del(c, s.key) { ui.toast = it } } }) { Text("Only ${s.title}") }
                    }
                    TextButton(onClick = { deleteConfirm = "all" }) { Text("All context + listening history (favourites, playlists and queues are kept)", color = MaterialTheme.colorScheme.error) }
                }
            },
            confirmButton = {
                if (kind == "all") TextButton(onClick = { deleteConfirm = null; scope.launch { del(c, "all") { ui.toast = it } } }) {
                    Text("Yes, erase everything", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = { TextButton(onClick = { deleteConfirm = null }) { Text("Cancel") } },
        )
    }
    renaming?.let { (id, label) ->
        var text by remember(id) { mutableStateOf(label) }
        AlertDialog(
            onDismissRequest = { renaming = null },
            title = { Text("Name this place") },
            text = { Column { Text("e.g. home, work, gym"); OutlinedTextField(text, { text = it }, singleLine = true) } },
            confirmButton = { TextButton(onClick = { c.contextCollector.renamePlace(id, text); renaming = null }) { Text("Save") } },
            dismissButton = { TextButton(onClick = { renaming = null }) { Text("Cancel") } },
        )
    }
}

private suspend fun del(c: AppContainer, signal: String, toast: (String) -> Unit) {
    val ok = runCatching { c.api.deleteMyData(signal, confirm = if (signal == "all") true else null).isSuccessful }.getOrDefault(false)
    toast(if (ok) "Deleted" else "Couldn't delete (needs the server)")
}

