package org.nasmusic.app.ui.screens

import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.automirrored.filled.Sort
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.CreateNewFolder
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.GridView
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Sell
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.Album
import org.nasmusic.app.data.api.Artist
import org.nasmusic.app.data.api.Genre
import org.nasmusic.app.data.api.Playlist
import org.nasmusic.app.data.api.ShelfItem
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.playback.PlaySource
import org.nasmusic.app.ui.AlbumArt
import org.nasmusic.app.ui.LocalUi
import org.nasmusic.app.ui.Loading
import org.nasmusic.app.ui.Message
import org.nasmusic.app.ui.ShelfCard
import org.nasmusic.app.ui.SongList

private val CHIPS = listOf("downloaded" to "Downloaded", "playlists" to "Playlists", "albums" to "Albums", "artists" to "Artists", "composers" to "Composers", "genres" to "Genres", "folders" to "Folders", "songs" to "Songs")

private val SORTS = mapOf(
    "albums" to listOf("name" to "Title", "year" to "Year", "added" to "Recently added"),
    "songs" to listOf("title" to "Title", "added" to "Recently added", "year" to "Year"),
    "artists" to listOf("name" to "Name", "albums" to "Most albums"),
    "playlists" to listOf("name" to "Name", "updated" to "Recently updated"),
    "genres" to listOf("name" to "Name", "count" to "Most songs"),
    "composers" to listOf("name" to "Name", "works" to "Most works"),
)

/** Library (docs/v3-ux.md §3.3): filter chips, sort menu, grid/list toggle remembered per chip. */
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
fun LibraryScreen(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val settings by c.settings.settings.collectAsStateWithLifecycle()
    // Offline, the server sections can't load: open on what's downloaded.
    var chip by rememberSaveable { mutableStateOf(if (c.router.route.value == org.nasmusic.app.net.Route.OFFLINE) "downloaded" else "playlists") }
    var sort by rememberSaveable(chip) { mutableStateOf(SORTS[chip]?.first()?.first ?: "") }
    var sortMenu by remember { mutableStateOf(false) }
    val view = settings.viewFor(chip)
    // api 0.10: the Composers chip only in classical mode
    val classical by org.nasmusic.app.data.ClassicalMode.on.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { org.nasmusic.app.data.ClassicalMode.ensure(c.context, c.api) }

    Column(Modifier.fillMaxSize()) {
        TopBar(c, "Library")
        Row(Modifier.fillMaxWidth().padding(start = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Row(Modifier.weight(1f).horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                CHIPS.filter { it.first != "composers" || classical }.forEach { (k, label) -> FilterChip(chip == k, { chip = k }, label = { Text(label) }) }
                // Spoken word + live radio live apart from music (their own player, no recommendations).
                FilterChip(false, { ui.go("spoken") }, label = { Text("Podcasts & more") })
            }
            SORTS[chip]?.let { sorts -> Box {
                IconButton(onClick = { sortMenu = true }) { Icon(Icons.AutoMirrored.Filled.Sort, "Sort") }
                DropdownMenu(sortMenu, { sortMenu = false }) {
                    sorts.forEach { (k, label) -> DropdownMenuItem({ Text(if (k == sort) "✓ $label" else label) }, { sort = k; sortMenu = false }) }
                }
            } }
            if (chip != "folders" && chip != "songs" && chip != "downloaded" && chip != "composers") IconButton(onClick = {
                scope.launch { c.settings.setLibraryView(chip, if (view == "grid") "list" else "grid") }
            }) { Icon(if (view == "grid") Icons.AutoMirrored.Filled.List else Icons.Default.GridView, if (view == "grid") "List view" else "Grid view") }
        }
        // Pull down to reload the section: a new key throws away its loaded state, so it fetches again.
        var refreshKey by remember { mutableStateOf(0) }
        var refreshing by remember { mutableStateOf(false) }
        LaunchedEffect(refreshKey) { if (refreshing) { kotlinx.coroutines.delay(700); refreshing = false } }
        androidx.compose.material3.pulltorefresh.PullToRefreshBox(refreshing, { refreshing = true; refreshKey++ }, Modifier.weight(1f)) {
            androidx.compose.runtime.key(refreshKey) {
                when (chip) {
                    "downloaded" -> DownloadedSection(c)
                    "playlists" -> PlaylistsSection(c, view, sort)
                    "albums" -> AlbumsSection(c, view, sort)
                    "artists" -> ArtistsSection(c, view, sort)
                    "genres" -> GenresSection(c, view, sort)
                    "composers" -> ComposersSection(c, sort)
                    "folders" -> FoldersScreen(c, "")
                    "songs" -> SongsSection(c, sort)
                }
            }
        }
    }
}

@Composable
private fun <T> GridOrList(items: List<T>, grid: Boolean, key: (T) -> String, cell: @Composable (T) -> Unit, row: @Composable (T) -> Unit) {
    if (grid) LazyVerticalGrid(
        GridCells.Adaptive(140.dp), Modifier.fillMaxSize(),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(12.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp), verticalArrangement = Arrangement.spacedBy(16.dp),
    ) { itemsIndexed(items, key = { i, it -> "$i:${key(it)}" }) { _, it -> cell(it) } }
    else LazyColumn(Modifier.fillMaxSize()) { itemsIndexed(items, key = { i, it -> "$i:${key(it)}" }) { _, it -> row(it) } }
}

@Composable
private fun ListRow(title: String, subtitle: String?, leading: @Composable () -> Unit, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        leading()
        Spacer(Modifier.width(14.dp))
        Column {
            Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis)
            subtitle?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1) }
        }
    }
}

@Composable
private fun PlaylistsSection(c: AppContainer, view: String, sort: String) {
    val ui = LocalUi.current
    var lists by remember { mutableStateOf<List<Playlist>?>(null) }
    // api 0.10: playlists filed in folders show inside their folder, not here
    var folders by remember { mutableStateOf<List<org.nasmusic.app.data.api.PlaylistFolder>>(emptyList()) }
    val folderActions = remember { FolderActions() }
    suspend fun reload() {
        lists = runCatching { c.api.playlists() }.getOrDefault(emptyList())
        folders = runCatching { c.api.playlistFolders() }.getOrDefault(emptyList())
    }
    LaunchedEffect(Unit) { reload() }
    if (view == "list") { PlaylistsScreen(c); return }
    val folderIds = folders.map { it.id }.toSet()
    val sorted = lists.orEmpty().filter { it.folderId == null || it.folderId !in folderIds }.let { if (sort == "updated") it.sortedByDescending { p -> p.updatedAt } else it.sortedBy { p -> p.name.lowercase() } }
    var smart by remember { mutableStateOf<List<org.nasmusic.app.data.api.SmartPlaylist>>(emptyList()) }
    LaunchedEffect(Unit) { smart = runCatching { c.api.smartPlaylists() }.getOrDefault(emptyList()) }
    val smartSorted = if (sort == "updated") smart else smart.sortedBy { it.name.lowercase() }
    val items = smartLists.map { ShelfItem("smart_list", it.kind, it.title) } +
        smartSorted.map { ShelfItem("smart_playlist", it.id, it.name, "Smart · ${it.trackCount} songs", artUrl = org.nasmusic.app.data.FeedRepository.art(it.tracks.firstOrNull()?.albumId), smartPlaylist = it) } +
        sorted.map { ShelfItem("playlist", it.id, it.name, "${it.trackCount} songs") }
    Column {
        Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            androidx.compose.material3.TextButton(onClick = { ui.go("smart-edit/new") }) {
                Icon(Icons.Default.AutoAwesome, null); Text("  New smart playlist")
            }
            androidx.compose.material3.TextButton(onClick = { folderActions.newIn = "" }) {
                Icon(Icons.Default.CreateNewFolder, null); Text("  New folder")
            }
        }
        val top = org.nasmusic.app.data.PlaylistFolders.children(folders, null)
        if (top.isNotEmpty()) Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            top.forEach { f ->
                androidx.compose.material3.AssistChip(onClick = { ui.go("playlist-folder/${f.id}") }, label = { Text(f.name) },
                    leadingIcon = { Icon(Icons.Default.Folder, null) })
            }
        }
        GridOrList(items, true, { it.type + it.id }, cell = { ShelfCard(c, it, width = 150.dp) }, row = {})
    }
    FolderDialogs(c, folderActions, folders) { reload() }
}

@Composable
private fun AlbumsSection(c: AppContainer, view: String, sort: String) {
    val ui = LocalUi.current
    var albums by remember(sort) { mutableStateOf<List<Album>?>(null) }
    var failed by remember { mutableStateOf(false) }
    LaunchedEffect(sort) { albums = runCatching { loadAllAlbums(c, sort) }.onFailure { failed = true }.getOrNull() }
    val a = albums
    when {
        failed -> Message("Albums need a connection to the server.")
        a == null -> Loading()
        else -> GridOrList(a, view == "grid", { it.id },
            cell = { ShelfCard(c, ShelfItem("album", it.id, it.title, it.artist, artUrl = org.nasmusic.app.data.FeedRepository.art(it.id), album = it), width = 150.dp) },
            row = { al -> ListRow(al.title, listOfNotNull(al.artist, al.year?.toString()).joinToString(" · "), { AlbumArt(al.id, 52.dp) }) { ui.go("album/${al.id}") } })
    }
}

@Composable
private fun ArtistsSection(c: AppContainer, view: String, sort: String) {
    val ui = LocalUi.current
    var artists by remember { mutableStateOf<List<Artist>?>(null) }
    LaunchedEffect(Unit) { artists = runCatching { c.api.artists() }.getOrDefault(emptyList()) }
    val a = artists?.let { if (sort == "albums") it.sortedByDescending { x -> x.albumCount } else it } ?: return Loading()
    GridOrList(a, view == "grid", { it.id },
        cell = { ShelfCard(c, ShelfItem("artist", it.id, it.name, "${it.albumCount} albums"), width = 120.dp) },
        row = { ar -> ListRow(ar.name, "${ar.albumCount} albums", { Icon(Icons.Default.Person, null) }) { ui.go("artist/${ar.id}") } })
}

@Composable
private fun GenresSection(c: AppContainer, view: String, sort: String) {
    val ui = LocalUi.current
    var genres by remember { mutableStateOf<List<Genre>?>(null) }
    LaunchedEffect(Unit) { genres = runCatching { c.api.genres() }.getOrDefault(emptyList()) }
    val g = genres?.let { if (sort == "count") it.sortedByDescending { x -> x.trackCount } else it } ?: return Loading()
    GridOrList(g, view == "grid", { it.name },
        cell = { genre ->
            androidx.compose.material3.OutlinedCard(onClick = { ui.go("genre/${Uri.encode(genre.name)}") }) {
                Column(Modifier.padding(16.dp)) {
                    Icon(Icons.Default.Sell, null, tint = MaterialTheme.colorScheme.primary)
                    Text(genre.name, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text("${genre.trackCount} songs", style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        row = { genre -> ListRow(genre.name, "${genre.trackCount} songs", { Icon(Icons.Default.Sell, null) }) { ui.go("genre/${Uri.encode(genre.name)}") } })
}

@Composable
private fun SongsSection(c: AppContainer, sort: String) {
    var tracks by remember(sort) { mutableStateOf<List<Track>?>(null) }
    LaunchedEffect(sort) { tracks = runCatching { c.api.allTracks(sort = sort) }.getOrDefault(emptyList()) }
    SongList(c, tracks, "All songs", PlaySource.LIBRARY)
}
