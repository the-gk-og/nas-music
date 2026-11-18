package org.nasmusic.app.ui.screens

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.pairing.QrPayload

@Composable
fun PairingScreen(c: AppContainer) {
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var lan by rememberSaveable { mutableStateOf("https://") }
    var cert by rememberSaveable { mutableStateOf("") }
    var code by rememberSaveable { mutableStateOf("") }
    var tunnel by rememberSaveable { mutableStateOf("") }
    var cfId by rememberSaveable { mutableStateOf("") }
    var cfSecret by rememberSaveable { mutableStateOf("") }

    fun pair(p: QrPayload) {
        busy = true; error = null
        scope.launch {
            try { c.pairing.pair(p) } catch (e: Exception) { error = e.message }
            busy = false
        }
    }

    val scanner = rememberLauncherForActivityResult(ScanContract()) { result ->
        val text = result.contents ?: return@rememberLauncherForActivityResult
        QrPayload.parse(text).fold(::pair) { error = "Not a nas-music pairing code: ${it.message}" }
    }

    Column(
        Modifier.fillMaxSize().systemBarsPadding().verticalScroll(rememberScrollState()).padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Pair with your NAS", style = MaterialTheme.typography.headlineMedium)
        Text("Open the server's web UI → Pair device, then scan the QR code.")
        Button(
            onClick = {
                scanner.launch(
                    ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE).setPrompt("Scan the pairing QR")
                        .setBeepEnabled(false).setOrientationLocked(false),
                )
            },
            enabled = !busy,
            modifier = Modifier.fillMaxWidth(),
            contentPadding = ButtonDefaults.ButtonWithIconContentPadding,
        ) {
            Icon(Icons.Default.QrCodeScanner, null)
            Text("  Scan QR code")
        }
        if (busy) CircularProgressIndicator()
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }

        HorizontalDivider(Modifier.padding(vertical = 8.dp))
        Text("Or enter the details by hand", style = MaterialTheme.typography.titleMedium)
        OutlinedTextField(lan, { lan = it }, label = { Text("LAN address (https://192.168.1.50:8443)") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(cert, { cert = it }, label = { Text("Certificate SHA-256 (AB:CD:…)") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(code, { code = it }, label = { Text("Pair code") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(tunnel, { tunnel = it }, label = { Text("Tunnel address (optional)") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(cfId, { cfId = it }, label = { Text("CF Access client id (optional)") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(
            cfSecret, { cfSecret = it }, label = { Text("CF Access client secret (optional)") },
            modifier = Modifier.fillMaxWidth(), singleLine = true, visualTransformation = PasswordVisualTransformation(),
        )
        OutlinedButton(
            onClick = {
                val p = QrPayload(
                    v = 1, lanUrl = lan.trim(), certSha256 = cert.trim(), pairCode = code.trim(),
                    tunnelUrl = tunnel.trim().ifBlank { null },
                    cfAccessClientId = cfId.trim().ifBlank { null }, cfAccessClientSecret = cfSecret.trim().ifBlank { null },
                )
                val json = org.nasmusic.app.data.api.ApiFactory.json.encodeToString(QrPayload.serializer(), p)
                QrPayload.parse(json).fold(::pair) { error = it.message }
            },
            enabled = !busy,
            modifier = Modifier.fillMaxWidth(),
        ) { Text("Pair") }
    }
}
