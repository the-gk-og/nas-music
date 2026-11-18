package org.nasmusic.app.ui.screens

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
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.Album
import org.nasmusic.app.data.api.AlbumDetail
import org.nasmusic.app.data.api.Artist
import org.nasmusic.app.data.api.ArtistDetail
import org.nasmusic.app.data.api.FolderListing
import org.nasmusic.app.data.api.Genre
import org.nasmusic.app.data.api.NameBody
import org.nasmusic.app.data.api.Playlist
import org.nasmusic.app.data.api.SearchResult
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.playback.MediaItems
import org.nasmusic.app.playback.PlaySource
import org.nasmusic.app.ui.LocalUi
import org.nasmusic.app.ui.Loading
import org.nasmusic.app.ui.Message
import org.nasmusic.app.ui.ScreenTitle
import org.nasmusic.app.ui.SongList
import org.nasmusic.app.ui.SongRow
import org.nasmusic.app.ui.songItems

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

private suspend fun loadAllAlbums(c: AppContainer, sort: String, artistId: String? = null): List<Album> {
    val out = mutableListOf<Album>()
    while (true) {
        val page = c.api.albums(artistId = artistId, sort = sort, offset = out.size, limit = 1000)
        out += page
        if (page.size < 1000) return out
    }
}

@Composable
fun AlbumsScreen(c: AppContainer) {
    val ui = LocalUi.current
    var sort by rememberSaveable { mutableStateOf("name") }
    var query by rememberSaveable { mutableStateOf("") }
    var albums by remember { mutableStateOf<List<Album>?>(null) }
    var error by remember { mutableStateOf(false) }
    var sortMenu by remember { mutableStateOf(false) }
    LaunchedEffect(sort) { albums = runCatching { loadAllAlbums(c, sort) }.onFailure { error = true }.getOrNull() }
    val shown = albums?.let { all -> if (query.isBlank()) all else all.filter { it.title.contains(query, true) || it.artist.contains(query, true) } }

    Column(Modifier.fillMaxSize()) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(end = 4.dp)) {
            TextField(
                query, { query = it },
                placeholder = { Text("Search an album…") },
                leadingIcon = { Icon(Icons.Default.Search, null) },
                singleLine = true,
                colors = TextFieldDefaults.colors(
                    focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent,
                    unfocusedIndicatorColor = Color.Transparent,
                ),
                modifier = Modifier.weight(1f),
            )
            Column {
                IconButton(onClick = { sortMenu = true }) { Icon(Icons.AutoMirrored.Filled.Sort, "Sort") }
                DropdownMenu(sortMenu, { sortMenu = false }) {
                    listOf("name" to "Title", "year" to "Year", "added" to "Recently added").forEach { (k, label) ->
                        DropdownMenuItem({ Text(if (sort == k) "✓ $label" else label) }, { sort = k; sortMenu = false })
                    }
                }
            }
            IconButton(onClick = { ui.go("settings") }) { Icon(Icons.Default.Settings, "Settings") }
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        when {
            error -> Message("Albums need a connection to the server.")
            shown == null -> Loading()
            shown.isEmpty() -> Message("No albums.")
            else -> AlbumGrid(shown) { ui.go("album/${it.id}") }
        }
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
        items(albums, key = { it.id }) { a ->
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

@Composable
fun AlbumScreen(c: AppContainer, albumId: String) {
    val ui = LocalUi.current
    var album by remember { mutableStateOf<AlbumDetail?>(null) }
    LaunchedEffect(albumId) { album = runCatching { c.api.album(albumId) }.getOrNull() }
    val a = album ?: return Loading()
    LazyColumn(Modifier.fillMaxSize()) {
        item {
            Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                org.nasmusic.app.ui.AlbumArt(a.id, 120.dp)
                Column(Modifier.weight(1f)) {
                    Text(a.title, style = MaterialTheme.typography.titleLarge)
                    Text(a.artist, color = MaterialTheme.colorScheme.primary, modifier = Modifier.clickable { ui.go("artist/${a.artistId}") })
                    Text(listOfNotNull(a.year?.toString(), "${a.tracks.size} songs").joinToString(" · "), style = MaterialTheme.typography.bodySmall)
                    Row {
                        IconButton(onClick = { c.player.play(a.tracks, 0, PlaySource.LIBRARY, "Album: ${a.title}") }) { Icon(Icons.Default.PlayArrow, "Play") }
                        IconButton(onClick = { c.player.play(a.tracks.shuffled(), 0, PlaySource.LIBRARY, "Album: ${a.title}") }) { Icon(Icons.Default.Shuffle, "Shuffle") }
                    }
                }
            }
        }
        songItems(c, a.tracks, "Album: ${a.title}", PlaySource.LIBRARY)
    }
}

// ---------- Artists ----------

@Composable
fun ArtistsScreen(c: AppContainer) {
    val ui = LocalUi.current
    var artists by remember { mutableStateOf<List<Artist>?>(null) }
    var error by remember { mutableStateOf(false) }
    var query by rememberSaveable { mutableStateOf("") }
    LaunchedEffect(Unit) { artists = runCatching { c.api.artists() }.onFailure { error = true }.getOrNull() }
    Column(Modifier.fillMaxSize()) {
        TextField(
            query, { query = it }, placeholder = { Text("Search an artist…") }, leadingIcon = { Icon(Icons.Default.Search, null) },
            singleLine = true, modifier = Modifier.fillMaxWidth(),
            colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent),
        )
        val list = artists?.filter { query.isBlank() || it.name.contains(query, true) }
        when {
            error -> Message("Artists need a connection to the server.")
            list == null -> Loading()
            else -> LazyColumn(Modifier.fillMaxSize()) {
                items(list, key = { it.id }) { a ->
                    Row(Modifier.fillMaxWidth().clickable { ui.go("artist/${a.id}") }.padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.Person, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.width(16.dp))
                        Column {
                            Text(a.name)
                            Text("${a.albumCount} albums", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun ArtistScreen(c: AppContainer, artistId: String) {
    val ui = LocalUi.current
    var artist by remember { mutableStateOf<ArtistDetail?>(null) }
    LaunchedEffect(artistId) { artist = runCatching { c.api.artist(artistId) }.getOrNull() }
    val a = artist ?: return Loading()
    AlbumGrid(a.albums, header = { Text(a.name, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.padding(8.dp)) }) { ui.go("album/${it.id}") }
}

// ---------- Genres ----------

@Composable
fun GenresScreen(c: AppContainer) {
    val ui = LocalUi.current
    var genres by remember { mutableStateOf<List<Genre>?>(null) }
    var error by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { genres = runCatching { c.api.genres() }.onFailure { error = true }.getOrNull() }
    Column(Modifier.fillMaxSize()) {
        ScreenTitle("Genres")
        val g = genres
        when {
            error -> Message("Genres need a connection to the server.")
            g == null -> Loading()
            else -> LazyColumn(Modifier.fillMaxSize()) {
                items(g, key = { it.name }) { genre ->
                    Row(Modifier.fillMaxWidth().clickable { ui.go("genre/${Uri.encode(genre.name)}") }.padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.Sell, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.width(16.dp))
                        Text(genre.name, Modifier.weight(1f))
                        Text("${genre.trackCount}", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
    }
}

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

private data class SmartList(val kind: String, val title: String, val icon: ImageVector)

private val smartLists = listOf(
    SmartList("all", "All songs", Icons.Default.LibraryMusic),
    SmartList("favourites", "Favorites", Icons.Default.Favorite),
    SmartList("recently_added", "Recently added", Icons.Default.NewReleases),
    SmartList("recently_played", "Recently played", Icons.Default.History),
    SmartList("most_played", "Most played", Icons.Default.LocalFireDepartment),
    SmartList("not_played", "Not played", Icons.Default.Block),
)

@Composable
fun PlaylistsScreen(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var lists by remember { mutableStateOf<List<Playlist>?>(null) }
    var creating by remember { mutableStateOf(false) }
    var name by remember { mutableStateOf("") }
    suspend fun reload() { lists = runCatching { c.api.playlists() }.getOrDefault(emptyList()) }
    LaunchedEffect(Unit) { reload() }
    LazyColumn(Modifier.fillMaxSize().padding(top = 8.dp)) {
        items(smartLists, key = { it.kind }) { s ->
            Row(Modifier.fillMaxWidth().clickable { ui.go("list/${s.kind}") }.padding(horizontal = 20.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(s.icon, null)
                Spacer(Modifier.width(24.dp))
                Text(s.title, style = MaterialTheme.typography.titleMedium)
            }
        }
        item { Text("Your playlists", color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(start = 20.dp, top = 16.dp, bottom = 4.dp)) }
        items(lists.orEmpty(), key = { it.id }) { p ->
            Row(Modifier.fillMaxWidth().clickable { ui.go("playlist/${p.id}") }.padding(horizontal = 20.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.AutoMirrored.Filled.List, null)
                Spacer(Modifier.width(24.dp))
                Text(p.name, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                if (p.trackCount > 0) Text("${p.trackCount}", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        item {
            OutlinedButton(onClick = { creating = true }, modifier = Modifier.padding(20.dp)) {
                Icon(Icons.Default.Add, null, tint = MaterialTheme.colorScheme.primary); Text("  New Playlist")
            }
        }
    }
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
    var newName by remember { mutableStateOf("") }
    suspend fun reload() { p = runCatching { c.api.playlist(id) }.getOrNull() }
    LaunchedEffect(id) { reload() }
    val pl = p ?: return Loading()
    Column(Modifier.fillMaxSize()) {
        ScreenTitle(pl.name) {
            IconButton(onClick = { c.player.play(pl.tracks, 0, PlaySource.LIBRARY, "Playlist: ${pl.name}") }) { Icon(Icons.Default.PlayArrow, "Play") }
            Column {
                IconButton(onClick = { menu = true }) { Icon(Icons.Default.MoreVert, "More") }
                DropdownMenu(menu, { menu = false }) {
                    DropdownMenuItem({ Text("Rename") }, { menu = false; newName = pl.name; renaming = true })
                    DropdownMenuItem({ Text("Delete playlist") }, {
                        menu = false
                        scope.launch { runCatching { c.api.deletePlaylist(id) }; ui.nav.popBackStack() }
                    })
                }
            }
        }
        if (pl.tracks.isEmpty()) Message("Empty. Add songs from any song's … menu.")
        LazyColumn(Modifier.fillMaxSize()) {
            items(pl.tracks.withIndex().toList(), key = { "${it.index}:${it.value.id}" }) { (i, t) ->
                SongRow(c, t, onClick = { c.player.play(pl.tracks, i, PlaySource.LIBRARY, "Playlist: ${pl.name}") }, leading = {
                    IconButton(onClick = { scope.launch { runCatching { c.api.removeFromPlaylist(id, i) }; reload() } }) {
                        Icon(Icons.Default.Close, "Remove from playlist", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                })
            }
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

// ---------- Search ----------

@Composable
fun SearchScreen(c: AppContainer) {
    val ui = LocalUi.current
    var query by rememberSaveable { mutableStateOf("") }
    var results by remember { mutableStateOf<SearchResult?>(null) }
    LaunchedEffect(query) {
        if (query.isBlank()) { results = null; return@LaunchedEffect }
        delay(300)
        results = runCatching { c.api.search(query) }.getOrNull()
    }
    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            query, { query = it }, leadingIcon = { Icon(Icons.Default.Search, null) },
            placeholder = { Text("Search songs, albums, artists") }, singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(12.dp),
        )
        val r = results ?: return@Column Message("Type to search your library.")
        val q = query.trim()
        LazyColumn(Modifier.fillMaxSize()) {
            if (r.tracks.isNotEmpty()) item { Header("Songs") }
            songItems(c, r.tracks, "Search: ${q.take(12)}", PlaySource.SEARCH, searchQuery = q)
            if (r.albums.isNotEmpty()) item { Header("Albums") }
            items(r.albums, key = { "al" + it.id }) { a ->
                Row(Modifier.fillMaxWidth().clickable { ui.go("album/${a.id}") }.padding(horizontal = 12.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    org.nasmusic.app.ui.AlbumArt(a.id, 52.dp)
                    Column(Modifier.padding(start = 12.dp)) { Text(a.title); Text(a.artist, style = MaterialTheme.typography.bodySmall) }
                }
            }
            if (r.artists.isNotEmpty()) item { Header("Artists") }
            items(r.artists, key = { "ar" + it.id }) { a ->
                Text(a.name, Modifier.fillMaxWidth().clickable { ui.go("artist/${a.id}") }.padding(horizontal = 16.dp, vertical = 14.dp))
            }
        }
    }
}

@Composable
private fun Header(text: String) =
    Text(text, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(16.dp, 12.dp, 16.dp, 4.dp))
