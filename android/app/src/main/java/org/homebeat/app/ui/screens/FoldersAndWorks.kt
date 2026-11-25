package org.homebeat.app.ui.screens

import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CreateNewFolder
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.homebeat.app.AppContainer
import org.homebeat.app.data.Classical
import org.homebeat.app.data.ClassicalMode
import org.homebeat.app.data.PlaylistFolders
import org.homebeat.app.data.api.Composer
import org.homebeat.app.data.api.NewPlaylistFolder
import org.homebeat.app.data.api.NewPlaylistIn
import org.homebeat.app.data.api.Playlist
import org.homebeat.app.data.api.PlaylistFolder
import org.homebeat.app.data.api.Track
import org.homebeat.app.data.api.Work
import org.homebeat.app.playback.PlaySource
import org.homebeat.app.ui.AlbumArt
import org.homebeat.app.ui.Loading
import org.homebeat.app.ui.LocalUi
import org.homebeat.app.ui.Message
import org.homebeat.app.ui.ScreenTitle
import org.homebeat.app.ui.SongRow
import org.homebeat.app.ui.songItems

// ---------- api 0.10: playlist folders ----------

private fun fmtMin(s: Double): String = "${(s / 60).toInt()} min"

/** Folder rows (with a ⋯ menu) for the folders directly inside [parentId]. */
fun LazyListScope.playlistFolderRows(folders: List<PlaylistFolder>, parentId: String?, onOpen: (PlaylistFolder) -> Unit, onMenu: (PlaylistFolder) -> Unit) {
    items(PlaylistFolders.children(folders, parentId), key = { "folder:" + it.id }) { f ->
        Row(
            Modifier.fillMaxWidth().clickable { onOpen(f) }.padding(start = 20.dp, end = 4.dp, top = 6.dp, bottom = 6.dp).heightIn(min = 44.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(Icons.Default.Folder, null, tint = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(24.dp))
            Text(f.name, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (f.playlistCount > 0) Text("${f.playlistCount}", color = MaterialTheme.colorScheme.onSurfaceVariant)
            IconButton(onClick = { onMenu(f) }) { Icon(Icons.Default.MoreVert, "Folder options") }
        }
    }
}

/** Dialogs for folder actions: new folder, rename, move, delete, and filing a playlist ("Move to folder"). */
class FolderActions {
    var newIn by mutableStateOf<String?>(null)      // parent id ("" = top level) of a folder being created
    var newPlaylistIn by mutableStateOf<String?>(null)
    var menuFor by mutableStateOf<PlaylistFolder?>(null)
    var renaming by mutableStateOf<PlaylistFolder?>(null)
    var moving by mutableStateOf<PlaylistFolder?>(null)
    var deleting by mutableStateOf<PlaylistFolder?>(null)
    var filing by mutableStateOf<Playlist?>(null)
}

@Composable
fun FolderDialogs(c: AppContainer, a: FolderActions, folders: List<PlaylistFolder>, onChanged: suspend () -> Unit) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    fun change(block: suspend () -> Unit) = scope.launch {
        runCatching { block() }.onFailure { ui.toast = "Couldn't change folders: ${it.message ?: "error"}" }
        onChanged()
    }
    a.newIn?.let { parent ->
        NameDialog("New folder", "", "Create", { a.newIn = null }) { name ->
            a.newIn = null
            change { c.api.createPlaylistFolder(NewPlaylistFolder(name, parent.ifEmpty { null })) }
        }
    }
    a.newPlaylistIn?.let { folder ->
        NameDialog("New playlist", "", "Create", { a.newPlaylistIn = null }) { name ->
            a.newPlaylistIn = null
            scope.launch {
                val p = runCatching { c.api.createPlaylistIn(NewPlaylistIn(name, folder.ifEmpty { null })) }.getOrNull()
                onChanged()
                if (p != null) ui.go("playlist/${p.id}") else ui.toast = "Couldn't create the playlist"
            }
        }
    }
    a.menuFor?.let { f ->
        AlertDialog(
            onDismissRequest = { a.menuFor = null },
            title = { Text(f.name) },
            text = {
                Column {
                    listOf(
                        "New playlist here" to { a.newPlaylistIn = f.id },
                        "New folder inside" to { a.newIn = f.id },
                        "Rename" to { a.renaming = f },
                        "Move to…" to { a.moving = f },
                        "Delete folder" to { a.deleting = f },
                    ).forEach { (label, act) ->
                        Text(label, Modifier.fillMaxWidth().clickable { a.menuFor = null; act() }.padding(vertical = 12.dp))
                    }
                }
            },
            confirmButton = { TextButton(onClick = { a.menuFor = null }) { Text("Close") } },
        )
    }
    a.renaming?.let { f ->
        NameDialog("Rename folder", f.name, "Save", { a.renaming = null }) { name ->
            a.renaming = null
            change { c.api.updatePlaylistFolder(f.id, JsonObject(mapOf("name" to JsonPrimitive(name)))) }
        }
    }
    a.moving?.let { f ->
        FolderPicker("Move \"${f.name}\" to", folders, f.parentId, excluding = f.id, onDismiss = { a.moving = null }) { target ->
            a.moving = null
            change { c.api.updatePlaylistFolder(f.id, JsonObject(mapOf("parentId" to (target?.let { JsonPrimitive(it) } ?: JsonNull)))) }
        }
    }
    a.deleting?.let { f ->
        AlertDialog(
            onDismissRequest = { a.deleting = null },
            title = { Text("Delete folder") },
            text = { Text("Delete \"${f.name}\"? Its playlists and folders move up a level; no playlist is deleted.") },
            confirmButton = { TextButton(onClick = { a.deleting = null; change { c.api.deletePlaylistFolder(f.id) } }) { Text("Delete") } },
            dismissButton = { TextButton(onClick = { a.deleting = null }) { Text("Cancel") } },
        )
    }
    a.filing?.let { p ->
        FolderPicker("Move \"${p.name}\" to", folders, p.folderId, onDismiss = { a.filing = null }, onNewFolder = { a.filing = null; a.newIn = "" }) { target ->
            a.filing = null
            change {
                c.api.setPlaylistFolder(p.id, JsonObject(mapOf("folderId" to (target?.let { JsonPrimitive(it) } ?: JsonNull))))
                ui.toast = folders.firstOrNull { it.id == target }?.let { "Moved to \"${it.name}\"" } ?: "Moved to the top level"
            }
        }
    }
}

@Composable
private fun NameDialog(title: String, initial: String, ok: String, onDismiss: () -> Unit, onOk: (String) -> Unit) {
    var name by remember { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { OutlinedTextField(name, { name = it }, singleLine = true, label = { Text("Name") }) },
        confirmButton = { TextButton(enabled = name.isNotBlank(), onClick = { onOk(name.trim()) }) { Text(ok) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

/** Pick a folder (null = top level). */
@Composable
private fun FolderPicker(
    title: String, folders: List<PlaylistFolder>, current: String?, excluding: String? = null,
    onDismiss: () -> Unit, onNewFolder: (() -> Unit)? = null, onPick: (String?) -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            LazyColumn {
                items(PlaylistFolders.choices(folders, excluding), key = { it.first?.id ?: "" }) { (f, depth) ->
                    Row(
                        Modifier.fillMaxWidth().clickable { if (f?.id != current) onPick(f?.id) else onDismiss() }
                            .padding(start = (depth * 16).dp, top = 10.dp, bottom = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(if (f == null) Icons.AutoMirrored.Filled.List else Icons.Default.Folder, null)
                        Spacer(Modifier.width(12.dp))
                        Text(f?.name ?: "Top level", color = if (f?.id == current) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
                    }
                }
                if (onNewFolder != null) item {
                    Row(Modifier.fillMaxWidth().clickable { onNewFolder() }.padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.CreateNewFolder, null, tint = MaterialTheme.colorScheme.primary)
                        Spacer(Modifier.width(12.dp))
                        Text("New folder…")
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

/** One playlist folder: its subfolders and playlists. */
@Composable
fun PlaylistFolderScreen(c: AppContainer, id: String) {
    val ui = LocalUi.current
    var folders by remember { mutableStateOf<List<PlaylistFolder>?>(null) }
    var lists by remember { mutableStateOf<List<Playlist>>(emptyList()) }
    val actions = remember { FolderActions() }
    suspend fun reload() {
        folders = runCatching { c.api.playlistFolders() }.getOrDefault(emptyList())
        lists = runCatching { c.api.playlists() }.getOrDefault(emptyList())
    }
    LaunchedEffect(id) { reload() }
    val all = folders ?: return Loading()
    val me = all.firstOrNull { it.id == id } ?: return Message("This folder no longer exists.")
    val here = lists.filter { it.folderId == id }.sortedBy { it.name.lowercase() }
    Column(Modifier.fillMaxSize()) {
        ScreenTitle(me.name) {
            IconButton(onClick = { ui.nav.popBackStack() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
            IconButton(onClick = { actions.menuFor = me }) { Icon(Icons.Default.MoreVert, "Folder options") }
        }
        val crumbs = PlaylistFolders.path(all, me.parentId)
        if (crumbs.isNotEmpty()) Text("in " + crumbs.joinToString(" › ") { it.name }, style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 16.dp))
        LazyColumn(Modifier.fillMaxSize()) {
            playlistFolderRows(all, id, onOpen = { ui.go("playlist-folder/${it.id}") }, onMenu = { actions.menuFor = it })
            items(here, key = { it.id }) { p ->
                Row(Modifier.fillMaxWidth().clickable { ui.go("playlist/${p.id}") }.padding(horizontal = 20.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.AutoMirrored.Filled.List, null)
                    Spacer(Modifier.width(24.dp))
                    Text(p.name, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    if (p.trackCount > 0) Text("${p.trackCount}", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (here.isEmpty() && PlaylistFolders.children(all, id).isEmpty()) item {
                Text("Empty. Use \"Move to folder\" on a playlist to file it here.", Modifier.padding(20.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            item {
                Row(Modifier.padding(12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = { actions.newPlaylistIn = id }) { Icon(Icons.Default.Add, null); Text("  New playlist") }
                    TextButton(onClick = { actions.newIn = id }) { Icon(Icons.Default.CreateNewFolder, null); Text("  New folder") }
                }
            }
        }
    }
    FolderDialogs(c, actions, all) { reload() }
}

// ---------- api 0.10: classical mode ----------

@Composable
fun ClassicalModeSwitch(c: AppContainer) {
    val scope = rememberCoroutineScope()
    val on by ClassicalMode.on.collectAsState()
    LaunchedEffect(Unit) { ClassicalMode.ensure(c.context, c.api) }
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("Classical mode")
            Text("Group albums by work with a play-whole-work button, show movement names, put the composer before the performer, and add Composers to the library.",
                style = MaterialTheme.typography.bodySmall)
        }
        Switch(on, { v -> scope.launch { ClassicalMode.set(c.context, c.api, v) } })
    }
}

/** Album tracks: grouped under their works in classical mode, else the plain list. */
fun LazyListScope.albumTrackItems(c: AppContainer, tracks: List<Track>, queueName: String, classical: Boolean) {
    if (!classical || !Classical.hasWorks(tracks)) { songItems(c, tracks, queueName, PlaySource.LIBRARY); return }
    for (g in Classical.groupByWork(tracks)) {
        if (g.workId != null) item(key = "work:${g.start}") {
            WorkHeader(g.work.orEmpty(), g.composer, g.tracks) { c.player.play(g.tracks, 0, PlaySource.LIBRARY, "Work: ${g.work}") }
        }
        itemsIndexed(g.tracks, key = { j, t -> "${g.start + j}:${t.id}" }) { j, t ->
            // Shown with the movement name; played as the whole album from here.
            SongRow(c, if (g.workId != null) t.copy(title = Classical.movementTitle(t)) else t,
                onClick = { c.player.play(tracks, g.start + j, PlaySource.LIBRARY, queueName) })
        }
    }
}

@Composable
private fun WorkHeader(title: String, composer: String?, tracks: List<Track>, onPlay: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 14.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
            Text(listOfNotNull(composer, "${tracks.size} movement${if (tracks.size == 1) "" else "s"}", fmtMin(tracks.sumOf { it.durationS }))
                .joinToString(" · "), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        IconButton(onClick = onPlay) { Icon(Icons.Default.PlayArrow, "Play whole work") }
    }
}

/** Library chip (classical mode): composers of works. */
@Composable
fun ComposersSection(c: AppContainer, sort: String) {
    val ui = LocalUi.current
    var list by remember { mutableStateOf<List<Composer>?>(null) }
    LaunchedEffect(Unit) { list = runCatching { c.api.composers() }.getOrDefault(emptyList()) }
    val l = list?.let { if (sort == "works") it.sortedByDescending { x -> x.workCount } else it } ?: return Loading()
    if (l.isEmpty()) return Message("No works found. Tag tracks with WORK and MOVEMENT, or title them \"Work: I. Movement\" with a composer.")
    LazyColumn(Modifier.fillMaxSize()) {
        items(l, key = { it.name }) { comp ->
            Row(Modifier.fillMaxWidth().clickable { ui.go("composer/${Uri.encode(comp.name)}") }.padding(horizontal = 20.dp, vertical = 14.dp)) {
                Text(comp.name, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text("${comp.workCount} work${if (comp.workCount == 1) "" else "s"}", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
fun ComposerScreen(c: AppContainer, name: String) {
    val ui = LocalUi.current
    var works by remember(name) { mutableStateOf<List<Work>?>(null) }
    LaunchedEffect(name) { works = runCatching { c.api.works(composer = name) }.getOrDefault(emptyList()) }
    Column(Modifier.fillMaxSize()) {
        ScreenTitle(name) { IconButton(onClick = { ui.nav.popBackStack() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } }
        val w = works ?: return@Column Loading()
        LazyColumn(Modifier.fillMaxSize()) {
            items(w, key = { it.id }) { work ->
                Row(Modifier.fillMaxWidth().clickable { ui.go("work/${Uri.encode(work.id)}") }.padding(horizontal = 16.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    AlbumArt(if (work.hasArt) work.albumId else null, 52.dp)
                    Spacer(Modifier.width(14.dp))
                    Column(Modifier.weight(1f)) {
                        Text(work.title, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        Text(listOfNotNull(work.artist, work.year?.toString(), "${work.trackCount} mvts", fmtMin(work.durationS)).joinToString(" · "),
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                    }
                }
            }
        }
    }
}

@Composable
fun WorkScreen(c: AppContainer, id: String) {
    val ui = LocalUi.current
    var work by remember(id) { mutableStateOf<Work?>(null) }
    var failed by remember(id) { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    LaunchedEffect(id) { work = runCatching { c.api.work(id) }.onFailure { failed = true }.getOrNull() }
    if (failed) return Message("Couldn't load this work.")
    val w = work ?: return Loading()
    val name = "Work: ${w.title}"
    Column(Modifier.fillMaxSize()) {
        ScreenTitle(w.title) {
            IconButton(onClick = { c.player.play(w.tracks, 0, PlaySource.LIBRARY, name) }) { Icon(Icons.Default.PlayArrow, "Play whole work") }
            Column {
                IconButton(onClick = { menu = true }) { Icon(Icons.Default.MoreVert, "More") }
                DropdownMenu(menu, { menu = false }) {
                    DropdownMenuItem({ Text("Go to album") }, { menu = false; ui.go("album/${w.albumId}") })
                    DropdownMenuItem({ Text("Add to queue") }, { menu = false; c.player.addToQueue(w.tracks) })
                }
            }
        }
        Text(listOfNotNull(w.composer, w.artist, w.year?.toString()).joinToString(" · "), style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 16.dp))
        LazyColumn(Modifier.fillMaxSize()) {
            itemsIndexed(w.tracks, key = { i, t -> "$i:${t.id}" }) { i, t ->
                SongRow(c, t.copy(title = Classical.movementTitle(t)), onClick = { c.player.play(w.tracks, i, PlaySource.LIBRARY, name) })
            }
        }
    }
}
