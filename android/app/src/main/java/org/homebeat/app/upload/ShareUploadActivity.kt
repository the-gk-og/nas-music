package org.homebeat.app.upload

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import org.homebeat.app.container
import org.homebeat.app.ui.theme.HomebeatTheme

/** "Share to Homebeat": audio files shared from another app are queued as uploads (WorkManager). */
class ShareUploadActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val uris = sharedUris(intent)
        val paired = container.secureStore.config.value?.isPaired == true
        setContent {
            HomebeatTheme {
                val scope = rememberCoroutineScope()
                var visibility by remember { mutableStateOf("everyone") }
                var wifiOnly by remember { mutableStateOf(UploadWorker.wifiOnly(this)) }
                var busy by remember { mutableStateOf(false) }
                AlertDialog(
                    onDismissRequest = { finish() },
                    title = { Text("Upload to Homebeat") },
                    text = {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            when {
                                !paired -> Text("Sign in to your server in the Homebeat app first.")
                                uris.isEmpty() -> Text("Nothing to upload.")
                                else -> {
                                    Text("${uris.size} file${if (uris.size == 1) "" else "s"} will be added to the library. " +
                                        "Uploads keep going in the background.")
                                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                        FilterChip(visibility == "everyone", { visibility = "everyone" }, { Text("Everyone") })
                                        FilterChip(visibility == "private", { visibility = "private" }, { Text("Only me") })
                                    }
                                    Row(Modifier.fillMaxWidth().padding(top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                                        Text("Wi-Fi only", Modifier.weight(1f))
                                        Switch(wifiOnly, { wifiOnly = it; UploadWorker.setWifiOnly(this@ShareUploadActivity, it) })
                                    }
                                }
                            }
                        }
                    },
                    confirmButton = {
                        if (paired && uris.isNotEmpty()) TextButton(enabled = !busy, onClick = {
                            busy = true
                            scope.launch {
                                val n = UploadWorker.enqueue(applicationContext, uris, visibility, wifiOnly)
                                Toast.makeText(applicationContext,
                                    if (n == 0) "Couldn't read the shared files" else "Uploading $n song${if (n == 1) "" else "s"} to Homebeat",
                                    Toast.LENGTH_SHORT).show()
                                finish()
                            }
                        }) { Text(if (busy) "Preparing…" else "Upload") }
                    },
                    dismissButton = { TextButton(onClick = { finish() }) { Text("Cancel") } },
                )
            }
        }
    }

    @Suppress("DEPRECATION")
    private fun sharedUris(i: Intent): List<Uri> = when (i.action) {
        Intent.ACTION_SEND -> listOfNotNull(
            if (Build.VERSION.SDK_INT >= 33) i.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
            else i.getParcelableExtra(Intent.EXTRA_STREAM) as? Uri,
        )
        Intent.ACTION_SEND_MULTIPLE ->
            (if (Build.VERSION.SDK_INT >= 33) i.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
            else i.getParcelableArrayListExtra(Intent.EXTRA_STREAM)).orEmpty()
        else -> emptyList()
    }
}
