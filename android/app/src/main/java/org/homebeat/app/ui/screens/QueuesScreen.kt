package org.homebeat.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Sort
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.DragHandle
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.MoreHoriz
import androidx.compose.material.icons.filled.Groups
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Save
import androidx.compose.material.icons.filled.Timer
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import org.homebeat.app.AppContainer
import org.homebeat.app.data.api.Queue
import org.homebeat.app.data.api.Track
import org.homebeat.app.playback.MediaItems
import org.homebeat.app.playback.PlaySource
import org.homebeat.app.playback.QueueSync
import org.homebeat.app.ui.LocalUi
import org.homebeat.app.ui.Message
import org.homebeat.app.ui.SongRow
import org.homebeat.app.ui.formatDuration

/** Musicolet-style queues: the playing queue (reorderable) plus up to 20 named queues from the server. */
@Composable
fun QueuesScreen(c: AppContainer) = QueuePanel(c)

/**
 * The queue UI, shared by the Queue tab and the queue popup on Now Playing (same rows, reorder,
 * removal and selector, so the two can't drift apart). [onOpenFull] adds "Open full queue".
 */
@Composable
fun QueuePanel(c: AppContainer, onOpenFull: (() -> Unit)? = null) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val now by c.player.state.collectAsStateWithLifecycle()
    val queues by c.queueSync.queues.collectAsStateWithLifecycle()
    val currentId by c.queueSync.currentId.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { c.queueSync.refresh() }

    // Which queue is shown: null = the one playing now.
    var viewing by remember { mutableStateOf<String?>(null) }
    var viewed by remember { mutableStateOf<Queue?>(null) }
    LaunchedEffect(viewing) { viewed = viewing?.let { c.queueSync.load(it) } }
    val showingCurrent = viewing == null || viewing == currentId

    val tracks: List<Track> = if (showingCurrent) now.queue.map(MediaItems::toTrack) else viewed?.tracks.orEmpty()
    val playingIndex = if (showingCurrent) now.index else -1
    val title = queues.firstOrNull { it.id == (viewing ?: currentId) }?.let(c.queueSync::label)
        ?: if (tracks.isEmpty()) "No queue" else "Current queue"

    var selector by remember { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    var sortMenu by remember { mutableStateOf(false) }
    var dialog by remember { mutableStateOf<String?>(null) } // "save" | "rename"
    var text by remember { mutableStateOf("") }
    var jamOpen by remember { mutableStateOf(false) }

    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Row(
                    Modifier.fillMaxWidth().border(BorderStroke(1.dp, MaterialTheme.colorScheme.outline), RoundedCornerShape(8.dp))
                        .clickable { selector = true }.padding(horizontal = 16.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(title, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                    Icon(Icons.Default.KeyboardArrowDown, "Choose queue")
                }
                DropdownMenu(selector, { selector = false }) {
                    if (queues.isEmpty()) DropdownMenuItem({ Text("No saved queues yet") }, { selector = false })
                    queues.forEach { q ->
                        DropdownMenuItem(
                            { Text(c.queueSync.label(q) + if (q.id == currentId) "  ▶" else "", maxLines = 1) },
                            { selector = false; viewing = q.id },
                        )
                    }
                }
            }
            // In the popup an ✕ would read as "close the popup"; deleting stays in the ⋯ menu there.
            if (onOpenFull == null) IconButton(onClick = {
                val id = viewing ?: currentId ?: return@IconButton
                scope.launch { c.queueSync.delete(id); viewing = null }
            }) { Icon(Icons.Default.Close, "Close queue") }
        }
        if (onOpenFull != null) {
            TextButton(onClick = onOpenFull, modifier = Modifier.padding(horizontal = 8.dp)) { Text("Open full queue") }
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = {
                if (showingCurrent) c.player.skipTo(0) else viewed?.let { c.player.playQueue(it) }
            }) { Icon(Icons.Default.PlayArrow, "Play queue") }
            Column {
                IconButton(onClick = { sortMenu = true }) { Icon(Icons.AutoMirrored.Filled.Sort, "Sort queue") }
                DropdownMenu(sortMenu, { sortMenu = false }) {
                    listOf<Pair<String, (Track) -> String>>(
                        "Title" to { it.title.lowercase() }, "Artist" to { it.artist.lowercase() }, "Album" to { it.album.lowercase() },
                    ).forEach { (label, key) ->
                        DropdownMenuItem({ Text("Sort by $label") }, {
                            sortMenu = false
                            val sorted = tracks.sortedBy(key)
                            val cur = tracks.getOrNull(playingIndex)?.id
                            c.player.play(sorted, sorted.indexOfFirst { it.id == cur }.coerceAtLeast(0), PlaySource.LIBRARY, title.substringAfter(". "))
                        })
                    }
                }
            }
            Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
                Text("${if (playingIndex >= 0) playingIndex + 1 else "–"} / ${tracks.size}", fontWeight = FontWeight.Bold)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Default.Timer, null, modifier = Modifier.padding(end = 4.dp).graphicsLayer(scaleX = 0.7f, scaleY = 0.7f))
                    Text(formatDuration(tracks.sumOf { it.durationS }))
                }
            }
            IconButton(onClick = { jamOpen = true }) { Icon(Icons.Default.Groups, "Jam: listen together") }
            IconButton(onClick = { text = title.substringAfter(". "); dialog = "save" }, enabled = tracks.isNotEmpty()) { Icon(Icons.Default.Save, "Save as playlist") }
            Column {
                IconButton(onClick = { menu = true }) { Icon(Icons.Default.MoreHoriz, "Queue menu") }
                DropdownMenu(menu, { menu = false }) {
                    DropdownMenuItem({ Text("Clear queue") }, {
                        menu = false
                        if (showingCurrent) (tracks.indices).reversed().filter { it != playingIndex }.forEach { c.player.removeItem(it) }
                    })
                    DropdownMenuItem({ Text("Rename queue") }, { menu = false; text = title.substringAfter(". "); dialog = "rename" })
                    DropdownMenuItem({ Text("Delete queue") }, {
                        menu = false
                        val id = viewing ?: currentId
                        if (id != null) scope.launch { c.queueSync.delete(id); viewing = null }
                    })
                }
            }
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)

        if (tracks.isEmpty()) Message("The queue is empty. Tap a song anywhere to start one.")
        else ReorderableQueue(c, tracks, playingIndex, enabled = showingCurrent, modifier = Modifier.weight(1f)) { i ->
            if (showingCurrent) c.player.skipTo(i) else viewed?.let { c.player.playQueue(it, i) }
        }
    }

    if (jamOpen) JamSheet(c) { jamOpen = false }

    dialog?.let { kind ->
        AlertDialog(
            onDismissRequest = { dialog = null },
            title = { Text(if (kind == "save") "Save as playlist" else "Rename queue") },
            text = { OutlinedTextField(text, { text = it }, singleLine = true, label = { Text("Name") }) },
            confirmButton = {
                TextButton(enabled = text.isNotBlank(), onClick = {
                    val id = viewing ?: currentId
                    dialog = null
                    scope.launch {
                        if (id == null) { ui.toast = "This queue isn't synced yet"; return@launch }
                        if (kind == "save") ui.toast = if (c.queueSync.saveAsPlaylist(id, text.trim()) != null) "Saved as playlist" else "Couldn't save"
                        else c.queueSync.rename(id, text.trim())
                    }
                }) { Text("OK") }
            },
            dismissButton = { TextButton(onClick = { dialog = null }) { Text("Cancel") } },
        )
    }
}

/** Queue list with a drag handle (☰) per row. Dropping commits one move to the player + server. */
@Composable
private fun ReorderableQueue(c: AppContainer, tracks: List<Track>, playing: Int, enabled: Boolean, modifier: Modifier = Modifier, onPlay: (Int) -> Unit) {
    val ui = org.homebeat.app.ui.LocalUi.current
    // Local copy so rows move under the finger; keys are positions at load time.
    var order by remember(tracks) { mutableStateOf(tracks.indices.toList()) }
    var dragKey by remember { mutableStateOf<Int?>(null) }
    var dragStart by remember { mutableStateOf(-1) }
    var offset by remember { mutableFloatStateOf(0f) }
    val state = rememberLazyListState()
    // Open with the current track in view (a couple of rows of context above it).
    LaunchedEffect(Unit) { if (playing in tracks.indices) state.scrollToItem((playing - 2).coerceAtLeast(0)) }

    LazyColumn(state = state, modifier = modifier.fillMaxWidth()) {
        itemsIndexed(order, key = { _, k -> k }) { i, k ->
            val t = tracks.getOrNull(k) ?: return@itemsIndexed
            val isPlaying = k == playing
            val dragging = dragKey == k
            SwipeToRemove(enabled = enabled, onRemove = { org.homebeat.app.ui.removeFromQueueWithUndo(c, ui, order.indexOf(k), t) }) {
            SongRow(
                c, t, onClick = { onPlay(i) }, highlighted = isPlaying,
                menuTarget = if (enabled) org.homebeat.app.ui.SongMenuTarget(t, queueIndex = order.indexOf(k)) else null,
                modifier = Modifier
                    .padding(horizontal = 6.dp, vertical = 2.dp)
                    .then(if (isPlaying) Modifier.border(1.dp, MaterialTheme.colorScheme.primary, RoundedCornerShape(10.dp)) else Modifier)
                    .graphicsLayer { if (dragging) { translationY = offset; shadowElevation = 12f } },
                leading = {
                    Icon(
                        Icons.Default.DragHandle, "Drag to reorder",
                        tint = if (isPlaying) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(start = 8.dp).then(
                            if (!enabled) Modifier else Modifier.pointerInput(k) {
                                detectDragGestures(
                                    onDragStart = { dragKey = k; dragStart = order.indexOf(k); offset = 0f },
                                    onDragEnd = {
                                        val from = dragStart
                                        val to = order.indexOf(k)
                                        dragKey = null; offset = 0f
                                        if (from >= 0 && from != to) c.player.moveItem(from, to)
                                    },
                                    onDragCancel = { dragKey = null; offset = 0f; order = tracks.indices.toList() },
                                ) { change, amount ->
                                    change.consume()
                                    offset += amount.y
                                    val pos = order.indexOf(k)
                                    val h = state.layoutInfo.visibleItemsInfo.firstOrNull { it.key == k }?.size?.toFloat() ?: return@detectDragGestures
                                    if (offset > h / 2 && pos < order.lastIndex) {
                                        order = QueueSync.move(order, pos, pos + 1); offset -= h
                                    } else if (offset < -h / 2 && pos > 0) {
                                        order = QueueSync.move(order, pos, pos - 1); offset += h
                                    }
                                }
                            },
                        ),
                    )
                },
            )
            }
        }
    }
}

/** Swipe a queue row sideways to remove it (only for the queue that is playing here). */
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
private fun SwipeToRemove(enabled: Boolean, onRemove: () -> Unit, content: @Composable () -> Unit) {
    if (!enabled) { content(); return }
    val state = androidx.compose.material3.rememberSwipeToDismissBoxState(
        confirmValueChange = { v -> if (v != androidx.compose.material3.SwipeToDismissBoxValue.Settled) { onRemove(); true } else false },
    )
    androidx.compose.material3.SwipeToDismissBox(
        state = state,
        backgroundContent = {
            // Only show the remove hint while a swipe is actually in progress; the row on top is transparent.
            if (state.dismissDirection == androidx.compose.material3.SwipeToDismissBoxValue.Settled) return@SwipeToDismissBox
            Row(
                Modifier.fillMaxSize().padding(horizontal = 24.dp),
                horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(Icons.Default.Close, "Remove", tint = MaterialTheme.colorScheme.error)
                Icon(Icons.Default.Close, "Remove", tint = MaterialTheme.colorScheme.error)
            }
        },
    ) {
        // Opaque background so the hint underneath never bleeds through the row.
        androidx.compose.foundation.layout.Box(Modifier.background(MaterialTheme.colorScheme.background)) { content() }
    }
}
