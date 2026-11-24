package org.nasmusic.app.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreHoriz
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.playback.PlaySource

/**
 * A Musicolet song row: cover thumb, title, artist, album, duration and a "…" menu. Tapping plays
 * the whole list as a new queue; long-press offers Play next / Add to queue.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun SongRow(
    c: AppContainer,
    track: Track,
    onClick: () -> Unit,
    highlighted: Boolean = false,
    leading: (@Composable () -> Unit)? = null,
    modifier: Modifier = Modifier,
    /** What the "…" menu opens for (e.g. with a queue position, which adds "Remove from queue"). */
    menuTarget: SongMenuTarget? = null,
) {
    val ui = LocalUi.current
    var quick by remember { mutableStateOf(false) }
    val base = if (highlighted) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface
    // Family mode: tracks the server flags in a queue/playlist are dimmed (the player skips them).
    val color = if (track.familyHidden) base.copy(alpha = 0.4f) else base
    Box(modifier) {
        Row(
            Modifier.fillMaxWidth().combinedClickable(onClick = onClick, onLongClick = { quick = true })
                .padding(start = if (leading == null) 12.dp else 0.dp, end = 4.dp, top = 6.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            leading?.invoke()
            AlbumArt(track.albumId, 52.dp)
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    ExplicitBadge(track.explicit)
                    Text(track.title, color = color, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyLarge)
                }
                // api 0.9 classical mode: composer before performer
                val classical by org.nasmusic.app.data.ClassicalMode.on.collectAsState()
                Text(org.nasmusic.app.data.Classical.artistLine(track, classical), color = color.copy(alpha = 0.85f), maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall)
                Row {
                    Text(track.album, color = color.copy(alpha = 0.7f), maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                    Text(formatDuration(track.durationS), color = color.copy(alpha = 0.7f), style = MaterialTheme.typography.bodySmall)
                }
            }
            IconButton(onClick = { ui.songMenu = menuTarget ?: SongMenuTarget(track) }) {
                Icon(Icons.Default.MoreHoriz, "Song menu", tint = color)
            }
        }
        DropdownMenu(quick, { quick = false }) {
            DropdownMenuItem({ Text("Play next") }, { quick = false; c.player.playNext(listOf(track)); ui.toast = "Playing next" })
            DropdownMenuItem({ Text("Add to queue") }, { quick = false; c.player.addToQueue(listOf(track)); ui.toast = "Added to queue" })
        }
    }
}

/** Song rows for a list that plays as one queue named [queueName]. */
fun LazyListScope.songItems(
    c: AppContainer,
    tracks: List<Track>,
    queueName: String,
    source: PlaySource,
    searchQuery: String? = null,
) {
    itemsIndexed(tracks, key = { i, t -> "$i:${t.id}" }) { i, t ->
        SongRow(c, t, onClick = { c.player.play(tracks, i, source, queueName, searchQuery = searchQuery) })
    }
}

@Composable
fun SongList(c: AppContainer, tracks: List<Track>?, queueName: String, source: PlaySource, empty: String = "Nothing here yet.") {
    when {
        tracks == null -> Loading()
        tracks.isEmpty() -> Message(empty)
        else -> LazyColumn(Modifier.fillMaxSize()) { songItems(c, tracks, queueName, source) }
    }
}

@Composable
fun ScreenTitle(text: String, actions: @Composable () -> Unit = {}) {
    Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 12.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(text, style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
        actions()
    }
}

@Composable
fun IconSize(content: @Composable () -> Unit) = Box(Modifier.size(24.dp)) { content() }
