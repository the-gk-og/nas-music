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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.automirrored.filled.Sort
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

private val CHIPS = listOf("playlists" to "Playlists", "albums" to "Albums", "artists" to "Artists", "genres" to "Genres", "folders" to "Folders", "songs" to "Songs")

private val SORTS = mapOf(
    "albums" to listOf("name" to "Title", "year" to "Year", "added" to "Recently added"),
    "songs" to listOf("title" to "Title", "added" to "Recently added", "year" to "Year"),
    "artists" to listOf("name" to "Name", "albums" to "Most albums"),
    "playlists" to listOf("name" to "Name", "updated" to "Recently updated"),
    "genres" to listOf("name" to "Name", "count" to "Most songs"),
)

/** Library (docs/v3-ux.md §3.3): filter chips, sort menu, grid/list toggle remembered per chip. */
@Composable
fun LibraryScreen(c: AppContainer) {
    val scope = rememberCoroutineScope()
    val settings by c.settings.settings.collectAsStateWithLifecycle()
    var chip by rememberSaveable { mutableStateOf("playlists") }
    var sort by rememberSaveable(chip) { mutableStateOf(SORTS[chip]?.first()?.first ?: "") }
    var sortMenu by remember { mutableStateOf(false) }
    val view = settings.viewFor(chip)

    Column(Modifier.fillMaxSize()) {
        TopBar(c, "Library")
        Row(Modifier.fillMaxWidth().padding(start = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Row(Modifier.weight(1f).horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                CHIPS.forEach { (k, label) -> FilterChip(chip == k, { chip = k }, label = { Text(label) }) }
            }
            if (SORTS[chip] != null) Box {
                IconButton(onClick = { sortMenu = true }) { Icon(Icons.AutoMirrored.Filled.Sort, "Sort") }
                DropdownMenu(sortMenu, { sortMenu = false }) {
                    SORTS[chip]!!.forEach { (k, label) -> DropdownMenuItem({ Text(if (k == sort) "✓ $label" else label) }, { sort = k; sortMenu = false }) }
                }
            }
            if (chip != "folders" && chip != "songs") IconButton(onClick = {
                scope.launch { c.settings.setLibraryView(chip, if (view == "grid") "list" else "grid") }
            }) { Icon(if (view == "grid") Icons.AutoMirrored.Filled.List else Icons.Default.GridView, if (view == "grid") "List view" else "Grid view") }
        }
        Box(Modifier.weight(1f)) {
            when (chip) {
                "playlists" -> PlaylistsSection(c, view, sort)
                "albums" -> AlbumsSection(c, view, sort)
                "artists" -> ArtistsSection(c, view, sort)
                "genres" -> GenresSection(c, view, sort)
                "folders" -> FoldersScreen(c, "")
                "songs" -> SongsSection(c, sort)
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
    ) { items(items, key = key) { cell(it) } }
    else LazyColumn(Modifier.fillMaxSize()) { items(items, key = key) { row(it) } }
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
    LaunchedEffect(Unit) { lists = runCatching { c.api.playlists() }.getOrDefault(emptyList()) }
    if (view == "list") { PlaylistsScreen(c); return }
    val sorted = lists.orEmpty().let { if (sort == "updated") it.sortedByDescending { p -> p.updatedAt } else it.sortedBy { p -> p.name.lowercase() } }
    val items = smartLists.map { ShelfItem("smart_list", it.kind, it.title) } + sorted.map { ShelfItem("playlist", it.id, it.name, "${it.trackCount} songs") }
    GridOrList(items, true, { it.type + it.id }, cell = { ShelfCard(c, it, width = 150.dp) }, row = {})
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
            cell = { ShelfCard(c, ShelfItem("album", it.id, it.title, it.artist, artUrl = org.nasmusic.app.data.FeedRepository.art(it.id)), width = 150.dp) },
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
