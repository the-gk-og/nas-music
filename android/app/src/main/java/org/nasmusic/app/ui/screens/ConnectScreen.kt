package org.nasmusic.app.ui.screens

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.net.CertProbe
import org.nasmusic.app.net.ProbeOutcome
import org.nasmusic.app.net.ServerAddress
import org.nasmusic.app.net.TrustDecision
import org.nasmusic.app.pairing.QrPayload

/** v2 Connect: server address → trust the LAN cert if needed → sign in / join with invite / scan QR. */
@Composable
fun ConnectScreen(c: AppContainer) {
    val scope = rememberCoroutineScope()
    val prev = c.secureStore.config.value
    var address by rememberSaveable { mutableStateOf(prev?.lanUrl?.ifBlank { null } ?: prev?.tunnelUrl ?: "") }
    var probe by remember { mutableStateOf<ProbeOutcome?>(null) }
    var askTrust by remember { mutableStateOf<Pair<ProbeOutcome, TrustDecision>?>(null) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var tab by rememberSaveable { mutableIntStateOf(0) }
    var username by rememberSaveable { mutableStateOf(prev?.username.orEmpty()) }
    var password by rememberSaveable { mutableStateOf("") }
    var invite by rememberSaveable { mutableStateOf("") }

    fun run(block: suspend () -> Unit) {
        busy = true; error = null
        scope.launch {
            try { block() } catch (e: Exception) { error = e.message ?: e.toString() }
            busy = false
        }
    }

    val scanner = rememberLauncherForActivityResult(ScanContract()) { result ->
        val text = result.contents ?: return@rememberLauncherForActivityResult
        QrPayload.parse(text).fold({ p -> run { c.pairing.pair(p) } }) { error = "Not a nas-music QR code: ${it.message}" }
    }

    Column(
        Modifier.fillMaxSize().systemBarsPadding().verticalScroll(rememberScrollState()).padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Connect to your server", style = MaterialTheme.typography.headlineMedium)
        val p = probe
        if (p == null) {
            OutlinedTextField(
                address, { address = it },
                label = { Text("Server address") },
                placeholder = { Text("192.168.68.2:8443 or https://music.example.com") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth(),
            )
            Button(
                enabled = !busy,
                modifier = Modifier.fillMaxWidth(),
                onClick = {
                    run {
                        val url = ServerAddress.normalize(address).getOrThrow()
                        val out = c.certProbe.probe(url)
                        when (val d = CertProbe.decide(out, c.auth.knownPinFor(out))) {
                            TrustDecision.CA_TRUSTED, TrustDecision.KNOWN_PIN -> probe = out
                            else -> askTrust = out to d
                        }
                    }
                },
            ) { Text("Continue") }
            OutlinedButton(
                enabled = !busy,
                modifier = Modifier.fillMaxWidth(),
                onClick = {
                    scanner.launch(
                        ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                            .setPrompt("Scan the QR from Account → Add a phone").setBeepEnabled(false).setOrientationLocked(false),
                    )
                },
            ) { Icon(Icons.Default.QrCodeScanner, null); Text("  Scan QR") }
        } else {
            Text("${p.info.name} · ${ServerAddress.origin(p.url)}", color = MaterialTheme.colorScheme.onSurfaceVariant)
            TabRow(selectedTabIndex = tab) {
                Tab(tab == 0, { tab = 0 }, text = { Text("Sign in") })
                Tab(tab == 1, { tab = 1 }, text = { Text("Join with invite code") })
            }
            if (tab == 1) {
                OutlinedTextField(invite, { invite = it }, label = { Text("Invite code") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            }
            OutlinedTextField(username, { username = it }, label = { Text("Username") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(
                password, { password = it }, label = { Text("Password") }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                modifier = Modifier.fillMaxWidth(),
            )
            Button(
                enabled = !busy && username.isNotBlank() && password.isNotBlank() && (tab == 0 || invite.isNotBlank()),
                modifier = Modifier.fillMaxWidth(),
                onClick = {
                    run {
                        if (tab == 0) c.auth.login(p, username, password) else c.auth.register(p, invite, username, password)
                    }
                },
            ) { Text(if (tab == 0) "Sign in" else "Create account") }
            TextButton(onClick = { probe = null; error = null }) { Text("Use a different server") }
        }
        if (busy) CircularProgressIndicator()
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    }

    askTrust?.let { (out, decision) ->
        AlertDialog(
            onDismissRequest = { askTrust = null },
            title = { Text(if (decision == TrustDecision.PIN_CHANGED) "Certificate changed!" else "Trust this server?") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (decision == TrustDecision.PIN_CHANGED) {
                        Text(
                            "This server's certificate is different from the one you trusted before. Only continue if you " +
                                "regenerated it on the NAS; otherwise someone may be intercepting the connection.",
                            color = MaterialTheme.colorScheme.error,
                        )
                    } else {
                        Text("${ServerAddress.origin(out.url)} uses a self-signed certificate. Check that the fingerprint matches the one shown in the server's web admin.")
                    }
                    Text("SHA-256", style = MaterialTheme.typography.labelMedium)
                    Text(out.fingerprint, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall)
                    if (out.info.certSha256 != null && !CertProbe.same(out.info.certSha256, out.fingerprint)) {
                        Text("Warning: the server reports a different fingerprint for itself.", color = MaterialTheme.colorScheme.error)
                    }
                }
            },
            confirmButton = { TextButton(onClick = { probe = out; askTrust = null }) { Text("Trust") } },
            dismissButton = { TextButton(onClick = { askTrust = null }) { Text("Cancel") } },
        )
    }
}
