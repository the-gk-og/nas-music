package org.nasmusic.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import coil3.compose.AsyncImage
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.Suggestion
import org.nasmusic.app.data.api.SuggestionStatus
import org.nasmusic.app.ui.Loading
import org.nasmusic.app.ui.Message

/** Weekly album suggestions with a short preview and Want / Not interested / Already have. */
@Composable
fun DiscoverScreen(c: AppContainer) {
    var list by remember { mutableStateOf<List<Suggestion>?>(null) }
    var error by remember { mutableStateOf(false) }
    var previewing by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) {
        list = runCatching { c.api.suggestions() }.onFailure { error = true }.getOrNull()
    }
    // A separate, throwaway player for previews so the main queue is left alone.
    val preview = remember { ExoPlayer.Builder(c.context).build() }
    DisposableEffect(Unit) { onDispose { preview.release() } }

    fun togglePreview(s: Suggestion) {
        if (previewing == s.id) { preview.stop(); previewing = null; return }
        val url = s.previewUrl ?: return
        preview.setMediaItem(MediaItem.fromUri(url))
        preview.prepare(); preview.play()
        previewing = s.id
    }

    fun setStatus(s: Suggestion, status: String) = scope.launch {
        runCatching { c.api.setSuggestionStatus(s.id, SuggestionStatus(status)) }.onSuccess {
            list = list?.map { if (it.id == s.id) it.copy(status = status) else it }
        }
    }

    val l = list
    when {
        error -> Message("Discover needs a connection to the server.")
        l == null -> Loading()
        l.isEmpty() -> Message("No suggestions this week.")
        else -> LazyColumn(Modifier.fillMaxSize()) {
            items(l, key = { it.id }) { s ->
                Card(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp)) {
                    Row(Modifier.padding(12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        AsyncImage(
                            model = s.coverUrl, contentDescription = null, contentScale = ContentScale.Crop,
                            modifier = Modifier.size(88.dp).clip(RoundedCornerShape(6.dp)),
                        )
                        Column(Modifier.weight(1f)) {
                            Text(s.album, style = MaterialTheme.typography.titleMedium)
                            Text(listOfNotNull(s.artist, s.year?.toString()).joinToString(" · "))
                            Text(s.reason, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        if (s.previewUrl != null) {
                            IconButton(onClick = { togglePreview(s) }) {
                                Icon(if (previewing == s.id) Icons.Default.Stop else Icons.Default.PlayArrow, "Preview")
                            }
                        }
                    }
                    Row(
                        Modifier.padding(start = 12.dp, end = 12.dp, bottom = 8.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        StatusChip("Want", s.status == "want") { setStatus(s, "want") }
                        StatusChip("Not interested", s.status == "not_interested") { setStatus(s, "not_interested") }
                        StatusChip("Already have", s.status == "have") { setStatus(s, "have") }
                    }
                }
            }
        }
    }
}

@Composable
private fun StatusChip(label: String, selected: Boolean, onClick: () -> Unit) {
    AssistChip(
        onClick = onClick,
        label = { Text(if (selected) "✓ $label" else label) },
    )
}
