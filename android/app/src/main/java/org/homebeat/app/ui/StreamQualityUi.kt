package org.homebeat.app.ui

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import org.homebeat.app.AppContainer
import org.homebeat.app.playback.NetworkKind
import org.homebeat.app.playback.StreamQuality

/** Streaming quality, one choice per network kind; changes apply from the next song. */
@Composable
fun StreamQualitySettings(c: AppContainer) {
    val scope = rememberCoroutineScope()
    val settings by c.settings.settings.collectAsStateWithLifecycle()
    val route by c.router.route.collectAsStateWithLifecycle()
    val unmetered by c.router.unmetered.collectAsStateWithLifecycle()
    val now = if (route == org.homebeat.app.net.Route.OFFLINE) null else NetworkKind.of(route, unmetered)
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        NetworkKind.entries.forEach { net ->
            val chosen = settings.streamQuality(net)
            Text(net.label + if (net == now) " (now)" else "", style = MaterialTheme.typography.titleSmall)
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                StreamQuality.entries.forEach { q ->
                    FilterChip(chosen == q, { scope.launch { c.settings.setStreamQuality(net, q.key) } },
                        label = { Text(if (q.kbps > 0) "Opus ${q.label}" else q.label) })
                }
            }
            if (chosen == StreamQuality.AUTO) {
                Text("Auto: ${StreamQuality.autoMeaning(net)}", style = MaterialTheme.typography.bodySmall)
            }
        }
        Text("Lossless sends the original file (FLAC as stored). Opus is transcoded on the server and cached. " +
            "Changes apply from the next song; downloaded songs play in their downloaded quality.",
            style = MaterialTheme.typography.bodySmall)
    }
}
