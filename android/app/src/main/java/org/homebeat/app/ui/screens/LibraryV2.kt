package org.homebeat.app.ui.screens

import android.net.Uri
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.automirrored.filled.Sort
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.LibraryMusic
import androidx.compose.material.icons.filled.LocalFireDepartment
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.NewReleases
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Radio
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Sell
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil3.compose.AsyncImage
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.homebeat.app.AppContainer
import org.homebeat.app.data.api.Album
import org.homebeat.app.data.api.AlbumDetail
import org.homebeat.app.data.api.Artist
import org.homebeat.app.data.api.ArtistDetail
import org.homebeat.app.data.api.FolderListing
import org.homebeat.app.data.api.Genre
import org.homebeat.app.data.api.NameBody
import org.homebeat.app.data.api.Playlist
import org.homebeat.app.data.api.SearchResult
import org.homebeat.app.data.api.Track
import org.homebeat.app.playback.MediaItems
import org.homebeat.app.playback.PlaySource
import org.homebeat.app.ui.LocalUi
import org.homebeat.app.ui.Loading
import org.homebeat.app.ui.Message
import org.homebeat.app.ui.ScreenTitle
import org.homebeat.app.ui.SongList
import org.homebeat.app.ui.SongRow
import org.homebeat.app.ui.songItems

// ---------- Folders ----------

@Composable
fun FoldersScreen(c: AppContainer, path: String) {
    val ui = LocalUi.current
    var listing by remember(path) { mutableStateOf<FolderListing?>(null) }
    var error by remember(path) { mutableStateOf(false) }
    LaunchedEffect(path) { listing = runCatching { c.api.folders(path) }.onFailure { error = true }.getOrNull() }
    val l = listing
    Column(Modifier.fillMaxSize()) {
        ScreenTitle(if (path.isBlank()) "Folders" else path.substringAfterLast('/')) {
            if (path.isNotBlank()) IconButton(onClick = { ui.nav.popBackStack() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Up") }
        }
        when {
            error -> Message("Folders need a connection to the server.")
            l == null -> Loading()
            else -> LazyColumn(Modifier.fillMaxSize()) {
                items(l.folders, key = { "f" + it.path }) { f ->
                    Row(
                        Modifier.fillMaxWidth().clickable { ui.go("folder/${Uri.encode(f.path)}") }.padding(horizontal = 16.dp, vertical = 12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(Icons.Default.Folder, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.width(16.dp))
                        Column(Modifier.weight(1f)) {
                            Text(f.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text("${f.trackCount} songs", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
                songItems(c, l.tracks, "Folder: ${path.substringAfterLast('/').ifBlank { "Library" }}", PlaySource.LIBRARY)
            }
        }
    }
}

// ---------- Albums ----------

internal suspend fun loadAllAlbums(c: AppContainer, sort: String, artistId: String? = null): List<Album> {
    val out = mutableListOf<Album>()
    while (true) {
        val page = c.api.albums(artistId = artistId, sort = sort, offset = out.size, limit = 1000)
        out += page
        if (page.size < 1000) return out
    }
}

@Composable
fun AlbumGrid(albums: List<Album>, header: (@Composable () -> Unit)? = null, onClick: (Album) -> Unit) {
    LazyVerticalGrid(
        columns = GridCells.Fixed(3),
        modifier = Modifier.fillMaxSize(),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        if (header != null) item(span = { GridItemSpan(3) }) { header() }
        itemsIndexed(albums, key = { i, a -> "$i:${a.id}" }) { _, a ->
            Card(
                onClick = { onClick(a) },
                shape = RoundedCornerShape(8.dp),
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.background),
            ) {
                AsyncImage(
                    model = MediaItems.artUrl(a.id, 256), contentDescription = null, contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxWidth().aspectRatio(1f),
                )
                Text(a.title, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(horizontal = 8.dp, vertical = 6.dp))
            }
        }
    }
}

// Album/artist pages live in Pages.kt (v3 Big header / Simple list).

// ---------- Genres ----------

@Composable
fun GenreScreen(c: AppContainer, name: String) {
    var tracks by remember { mutableStateOf<List<Track>?>(null) }
    LaunchedEffect(name) { tracks = runCatching { c.api.genreTracks(name) }.getOrDefault(emptyList()) }
    Column(Modifier.fillMaxSize()) {
        ScreenTitle(name)
        SongList(c, tracks, "Genre: $name", PlaySource.LIBRARY)
    }
}

// ---------- Playlists ----------

internal data class SmartList(val kind: String, val title: String, val icon: ImageVector)

internal val smartLists = listOf(
    SmartList("all", "All songs", Icons.Default.LibraryMusic),
    SmartList("favourites", "Favorites", Icons.Default.Favorite),
    SmartList("recently_added", "Recently added", Icons.Default.NewReleases),
    SmartList("recently_played", "Recently played", Icons.Default.History),
    SmartList("most_played", "Most played", Icons.Default.LocalFireDepartment),
    SmartList("not_played", "Not played", Icons.Default.Block),
    SmartList("new", "New this week", Icons.Default.NewReleases),   // api 0.7: its own screen (NewMusicScreen)
)

@Composable
fun PlaylistsScreen(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var lists by remember { mutableStateOf<List<Playlist>?>(null) }
    var creating by remember { mutableStateOf(false) }
    var name by remember { mutableStateOf("") }
    // api 0.10: folders first; playlists filed in a folder show inside it
    var folders by remember { mutableStateOf<List<org.homebeat.app.data.api.PlaylistFolder>>(emptyList()) }
    val folderActions = remember { FolderActions() }
    suspend fun reload() {
        lists = runCatching { c.api.playlists() }.getOrDefault(emptyList())
        folders = runCatching { c.api.playlistFolders() }.getOrDefault(emptyList())
    }
    LaunchedEffect(Unit) { reload() }
    val folderIds = folders.map { it.id }.toSet()
    LazyColumn(Modifier.fillMaxSize().padding(top = 8.dp)) {
        items(smartLists, key = { it.kind }) { s ->
            Row(Modifier.fillMaxWidth().clickable { ui.go("list/${s.kind}") }.padding(horizontal = 20.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(s.icon, null)
                Spacer(Modifier.width(24.dp))
                Text(s.title, style = MaterialTheme.typography.titleMedium)
            }
        }
        item { Text("Your playlists", color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(start = 20.dp, top = 16.dp, bottom = 4.dp)) }
        playlistFolderRows(folders, null, onOpen = { ui.go("playlist-folder/${it.id}") }, onMenu = { folderActions.menuFor = it })
        items(lists.orEmpty().filter { it.folderId == null || it.folderId !in folderIds }, key = { it.id }) { p ->
            Row(Modifier.fillMaxWidth().clickable { ui.go("playlist/${p.id}") }.padding(horizontal = 20.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.AutoMirrored.Filled.List, null)
                Spacer(Modifier.width(24.dp))
                Text(p.name, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                if (p.trackCount > 0) Text("${p.trackCount}", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        item {
            Row(Modifier.padding(20.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedButton(onClick = { creating = true }) {
                    Icon(Icons.Default.Add, null, tint = MaterialTheme.colorScheme.primary); Text("  New Playlist")
                }
                OutlinedButton(onClick = { folderActions.newIn = "" }) {
                    Icon(Icons.Default.Folder, null, tint = MaterialTheme.colorScheme.primary); Text("  New folder")
                }
            }
        }
    }
    FolderDialogs(c, folderActions, folders) { reload() }
    if (creating) {
        AlertDialog(
            onDismissRequest = { creating = false },
            title = { Text("New playlist") },
            text = { OutlinedTextField(name, { name = it }, singleLine = true, label = { Text("Name") }) },
            confirmButton = {
                TextButton(enabled = name.isNotBlank(), onClick = {
                    scope.launch { runCatching { c.api.createPlaylist(NameBody(name.trim())) }; name = ""; creating = false; reload() }
                }) { Text("Create") }
            },
            dismissButton = { TextButton(onClick = { creating = false }) { Text("Cancel") } },
        )
    }
}

@Composable
fun SmartListScreen(c: AppContainer, kind: String) {
    if (kind == "new") { NewMusicScreen(c); return }
    val title = smartLists.firstOrNull { it.kind == kind }?.title ?: kind
    var tracks by remember { mutableStateOf<List<Track>?>(null) }
    var offline by remember { mutableStateOf(false) }
    LaunchedEffect(kind) {
        tracks = runCatching {
            if (kind == "all") c.api.allTracks() else c.api.smartList(kind)
        }.getOrElse {
            // Offline: favourites fall back to the last synced copy.
            offline = true
            if (kind == "favourites") c.library.favourites().first else emptyList()
        }
    }
    Column(Modifier.fillMaxSize()) {
        ScreenTitle(title) {
            IconButton(onClick = { tracks?.takeIf { it.isNotEmpty() }?.let { c.player.play(it.shuffled(), 0, PlaySource.LIBRARY, title) } }) { Icon(Icons.Default.Shuffle, "Shuffle") }
        }
        if (offline) Text("Offline: showing what's on this phone", style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(horizontal = 16.dp))
        SongList(c, tracks, title, if (kind == "favourites") PlaySource.FAVOURITES else PlaySource.LIBRARY)
    }
}

@Composable
fun PlaylistScreen(c: AppContainer, id: String) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var p by remember { mutableStateOf<Playlist?>(null) }
    var menu by remember { mutableStateOf(false) }
    var renaming by remember { mutableStateOf(false) }
    var membersOpen by remember { mutableStateOf(false) }
    var newName by remember { mutableStateOf("") }
    // api 0.10: "Move to folder" (each user files playlists, shared ones too, in their own folders)
    val folderActions = remember { FolderActions() }
    var folders by remember { mutableStateOf<List<org.homebeat.app.data.api.PlaylistFolder>>(emptyList()) }
    suspend fun reload() { p = runCatching { c.api.playlist(id) }.getOrNull() }
    LaunchedEffect(id, ui.tagsRev) { reload() }
    // v5: live changes by other members (Connect `playlist`)
    LaunchedEffect(id) {
        c.connect.playlistChanges.collect { m ->
            if (m.playlistId != id) return@collect
            if (m.deleted) { ui.toast = "This playlist was deleted or is no longer shared with you"; ui.nav.popBackStack() }
            else if (m.rev == null || m.rev != p?.rev) reload()
        }
    }
    val pl = p ?: return Loading()
    val isOwner = pl.role == "owner"
    val canEdit = pl.role != "viewer"
    /** Entry ops keep their meaning when others edited meanwhile; a 409 just means "reload". */
    fun edit(block: suspend () -> Playlist?) = scope.launch {
        runCatching { block() }.onSuccess { r -> if (r != null) p = r else reload() }.onFailure { reload() }
    }
    Column(Modifier.fillMaxSize()) {
        ScreenTitle(pl.name) {
            IconButton(onClick = { c.player.play(pl.tracks, 0, PlaySource.LIBRARY, "Playlist: ${pl.name}") }) { Icon(Icons.Default.PlayArrow, "Play") }
            IconButton(onClick = {
                scope.launch { if (c.player.startRadio(org.homebeat.app.data.api.RadioSeed("playlist", id))) ui.go("now") else ui.toast = "Couldn't start the radio" }
            }) { Icon(Icons.Default.Radio, "Playlist radio") }
            Column {
                IconButton(onClick = { menu = true }) { Icon(Icons.Default.MoreVert, "More") }
                DropdownMenu(menu, { menu = false }) {
                    DropdownMenuItem({ Text(if (isOwner) "Share / members" else "Members & activity") }, { menu = false; membersOpen = true })
                    if (isOwner) DropdownMenuItem({ Text("Rename") }, { menu = false; newName = pl.name; renaming = true })
                    DropdownMenuItem({ Text("Move to folder") }, {
                        menu = false
                        scope.launch {
                            folders = runCatching { c.api.playlistFolders() }.getOrDefault(emptyList())
                            folderActions.filing = pl
                        }
                    })
                    DropdownMenuItem({ Text("Pin to Home") }, { menu = false; scope.launch { runCatching { c.api.pin(org.homebeat.app.data.api.NewPin("playlist", id)) }; ui.toast = "Pinned to Home" } })
                    KeepOfflineItem(c, "playlist", id, pl.name, pl.tracks) { menu = false }
                    if (isOwner) DropdownMenuItem({ Text("Delete playlist") }, {
                        menu = false
                        scope.launch { runCatching { c.api.deletePlaylist(id) }; ui.nav.popBackStack() }
                    })
                }
            }
        }
        if (!isOwner) {
            Text("By ${pl.owner ?: "?"} · you're ${if (canEdit) "an editor" else "a viewer"}",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 16.dp))
        } else if (pl.collaborative) {
            Text("Collaborative · ${pl.memberCount} member${if (pl.memberCount == 1) "" else "s"}",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 16.dp))
        }
        if (pl.tracks.isEmpty()) Message("Empty. Add songs from any song's … menu.")
        LazyColumn(Modifier.fillMaxSize()) {
            items(pl.tracks.withIndex().toList(), key = { pl.entries.getOrNull(it.index)?.id ?: "${it.index}:${it.value.id}" }) { (i, t) ->
                val entry = pl.entries.getOrNull(i)
                Column {
                    SongRow(c, t, onClick = { c.player.play(pl.tracks, i, PlaySource.LIBRARY, "Playlist: ${pl.name}") }, leading = if (canEdit) ({
                        IconButton(onClick = {
                            val after = if (i > 0) pl.entries.getOrNull(i - 1)?.id else ""
                            edit { if (entry != null) c.api.removePlaylistEntry(id, entry.id) else { c.api.removeFromPlaylist(id, i); null } }
                            ui.undo = org.homebeat.app.ui.UndoRequest("Removed \"${t.title}\"") {
                                if (entry != null && after != null) { p = c.api.addToPlaylistAfter(id, org.homebeat.app.data.api.AddEntriesBody(listOf(t.id), after)) }
                                else { c.api.addToPlaylist(id, org.homebeat.app.data.api.TrackIdsBody(listOf(t.id), kotlinx.serialization.json.JsonPrimitive(i))); reload() }
                            }
                        }) {
                            Icon(Icons.Default.Close, "Remove from playlist", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }) else null)
                    if (pl.collaborative && entry?.addedBy != null) {
                        Text("added by ${entry.addedBy}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(start = 72.dp, bottom = 4.dp))
                    }
                }
            }
        }
    }
    FolderDialogs(c, folderActions, folders) {
        folders = runCatching { c.api.playlistFolders() }.getOrDefault(emptyList())
        reload()
    }
    if (membersOpen) {
        org.homebeat.app.ui.PlaylistMembersDialog(c, id, isOwner, onLeft = { membersOpen = false; ui.nav.popBackStack() }) {
            membersOpen = false
            scope.launch { reload() }
        }
    }
    if (renaming) {
        AlertDialog(
            onDismissRequest = { renaming = false },
            title = { Text("Rename playlist") },
            text = { OutlinedTextField(newName, { newName = it }, singleLine = true) },
            confirmButton = { TextButton(onClick = { scope.launch { runCatching { c.api.renamePlaylist(id, NameBody(newName.trim())) }; renaming = false; reload() } }) { Text("Save") } },
            dismissButton = { TextButton(onClick = { renaming = false }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun Header(text: String) =
    Text(text, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(16.dp, 12.dp, 16.dp, 4.dp))
