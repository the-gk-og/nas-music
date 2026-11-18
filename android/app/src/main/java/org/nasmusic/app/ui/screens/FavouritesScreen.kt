package org.nasmusic.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.playback.PlaySource
import org.nasmusic.app.ui.Loading
import org.nasmusic.app.ui.Message
import org.nasmusic.app.ui.TrackRow

/** Starred tracks; the ones synced to the phone are playable offline. */
@Composable
fun FavouritesScreen(c: AppContainer) {
    var server by remember { mutableStateOf<List<Track>?>(null) }
    var offlineOnly by remember { mutableStateOf(false) }
    val route by c.router.route.collectAsStateWithLifecycle()
    LaunchedEffect(route) {
        val (list, ok) = c.library.favourites()
        server = list
        if (!ok) offlineOnly = true
    }
    val downloads by c.library.downloads.collectAsStateWithLifecycle(emptyList())
    val offline = remember(downloads) {
        downloads.filter { it.complete && it.why == "favourite" }.mapNotNull { c.library.decodeTrack(it.trackJson) }
    }
    val offlineIds = remember(downloads) { downloads.filter { it.complete }.map { it.trackId }.toSet() }

    val all = server
    val shown: List<Track>? = when {
        offlineOnly -> (all?.filter { it.id in offlineIds }.orEmpty() + offline).distinctBy { it.id }
        else -> all
    }

    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            FilterChip(offlineOnly, { offlineOnly = !offlineOnly }, label = { Text("On this phone (${offline.size})") })
            Text("", Modifier.weight(1f))
            FilledTonalButton(
                onClick = { shown?.shuffled()?.let { c.player.play(it, 0, PlaySource.FAVOURITES) } },
                enabled = !shown.isNullOrEmpty(),
            ) { Icon(Icons.Default.Shuffle, null); Text(" Shuffle") }
        }
        when {
            shown == null -> Loading()
            shown.isEmpty() -> Message(if (offlineOnly) "No favourites synced yet." else "Star tracks to add them here.")
            else -> LazyColumn(Modifier.fillMaxSize()) {
                itemsIndexed(shown, key = { _, t -> t.id }) { i, t ->
                    TrackRow(t, offline = t.id in offlineIds) { c.player.play(shown, i, PlaySource.FAVOURITES) }
                }
            }
        }
        Text(
            "${offline.size} favourites stored offline",
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(16.dp),
        )
    }
}
