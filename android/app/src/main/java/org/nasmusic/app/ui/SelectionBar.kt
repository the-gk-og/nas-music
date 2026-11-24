package org.nasmusic.app.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.PlaylistAdd
import androidx.compose.material.icons.automirrored.filled.PlaylistPlay
import androidx.compose.material.icons.automirrored.filled.QueueMusic
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupProperties
import androidx.navigation.compose.currentBackStackEntryAsState
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.playback.PlaySource

/**
 * Floating bar while songs are multi-selected (long-press a song, then tap more):
 * Play, Play next, Add to queue, Add to playlist, Download. Back or leaving the page clears it.
 */
@Composable
fun SelectionBar(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val entry by ui.nav.currentBackStackEntryAsState()
    LaunchedEffect(entry?.id) { ui.selection = emptyList() }
    val sel = ui.selection
    if (sel.isEmpty()) return
    BackHandler { ui.selection = emptyList() }

    /** Hand the picked songs to [f] and end the selection. */
    fun act(f: (List<org.nasmusic.app.data.api.Track>) -> Unit) {
        val t = ui.selection
        ui.selection = emptyList()
        f(t)
    }

    Popup(alignment = Alignment.TopCenter, properties = PopupProperties(focusable = false)) {
        Surface(
            shape = RoundedCornerShape(28.dp), tonalElevation = 6.dp, shadowElevation = 8.dp,
            color = MaterialTheme.colorScheme.surfaceContainerHigh,
            modifier = Modifier.statusBarsPadding().padding(8.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(0.dp), modifier = Modifier.padding(horizontal = 4.dp)) {
                IconButton(onClick = { ui.selection = emptyList() }) { Icon(Icons.Default.Close, "Clear selection") }
                Text("${sel.size}", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(end = 4.dp))
                IconButton(onClick = { act { c.player.play(it, 0, PlaySource.LIBRARY, "${it.size} songs") } }) { Icon(Icons.Default.PlayArrow, "Play") }
                IconButton(onClick = { act { c.player.playNext(it); ui.toast = "${it.size} songs will play next" } }) { Icon(Icons.AutoMirrored.Filled.PlaylistPlay, "Play next") }
                IconButton(onClick = { act { ui.addToQueue = it } }) { Icon(Icons.AutoMirrored.Filled.QueueMusic, "Add to a queue") }
                IconButton(onClick = { act { ui.addToPlaylist = it } }) { Icon(Icons.AutoMirrored.Filled.PlaylistAdd, "Add to playlist") }
                IconButton(onClick = {
                    act { t -> scope.launch { c.offline.downloadTracks(t); ui.toast = downloadToast(c) } }
                }) { Icon(Icons.Default.Download, "Download") }
            }
        }
    }
}
