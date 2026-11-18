package org.nasmusic.app.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.Album
import org.nasmusic.app.data.api.AlbumDetail
import org.nasmusic.app.data.api.Artist
import org.nasmusic.app.data.api.ArtistDetail
import org.nasmusic.app.data.api.SearchResult
import org.nasmusic.app.playback.PlaySource
import org.nasmusic.app.ui.AlbumArt
import org.nasmusic.app.ui.Loading
import org.nasmusic.app.ui.Message
import org.nasmusic.app.ui.TrackRow

@Composable
fun LibraryScreen(c: AppContainer, onArtist: (String) -> Unit, onAlbum: (String) -> Unit) {
    var query by rememberSaveable { mutableStateOf("") }
    var artists by remember { mutableStateOf<List<Artist>?>(null) }
    var results by remember { mutableStateOf<SearchResult?>(null) }
    var error by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        artists = runCatching { c.api.artists() }.onFailure { error = "Library needs a connection to the server." }.getOrNull()
    }
    LaunchedEffect(query) {
        if (query.isBlank()) { results = null; return@LaunchedEffect }
        delay(300) // debounce typing
        results = runCatching { c.api.search(query) }.getOrNull()
    }

    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            query, { query = it },
            leadingIcon = { Icon(Icons.Default.Search, null) },
            placeholder = { Text("Search artists, albums, tracks") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(12.dp),
        )
        val r = results
        if (r != null) {
            LazyColumn(Modifier.fillMaxSize()) {
                if (r.artists.isNotEmpty()) item { Header("Artists") }
                items(r.artists, key = { "a" + it.id }) { ArtistRow(it) { onArtist(it.id) } }
                if (r.albums.isNotEmpty()) item { Header("Albums") }
                items(r.albums, key = { "l" + it.id }) { AlbumRow(it) { onAlbum(it.id) } }
                if (r.tracks.isNotEmpty()) item { Header("Tracks") }
                itemsIndexed(r.tracks, key = { _, t -> "t" + t.id }) { i, t ->
                    TrackRow(t) { c.player.play(r.tracks, i, PlaySource.SEARCH) }
                }
            }
        } else {
            val list = artists
            when {
                error != null -> Message(error!!)
                list == null -> Loading()
                else -> LazyColumn(Modifier.fillMaxSize()) {
                    items(list, key = { it.id }) { ArtistRow(it) { onArtist(it.id) } }
                }
            }
        }
    }
}

@Composable
fun ArtistScreen(c: AppContainer, artistId: String, onAlbum: (String) -> Unit) {
    var artist by remember { mutableStateOf<ArtistDetail?>(null) }
    LaunchedEffect(artistId) { artist = runCatching { c.api.artist(artistId) }.getOrNull() }
    val a = artist ?: return Loading()
    LazyColumn(Modifier.fillMaxSize()) {
        item { Text(a.name, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.padding(16.dp)) }
        items(a.albums, key = { it.id }) { AlbumRow(it) { onAlbum(it.id) } }
    }
}

@Composable
fun AlbumScreen(c: AppContainer, albumId: String) {
    var album by remember { mutableStateOf<AlbumDetail?>(null) }
    LaunchedEffect(albumId) { album = runCatching { c.api.album(albumId) }.getOrNull() }
    val a = album ?: return Loading()
    LazyColumn(Modifier.fillMaxSize()) {
        item {
            Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                AlbumArt(a.id, 120.dp)
                Column {
                    Text(a.title, style = MaterialTheme.typography.titleLarge)
                    Text(listOfNotNull(a.artist, a.year?.toString()).joinToString(" · "))
                }
            }
        }
        itemsIndexed(a.tracks, key = { _, t -> t.id }) { i, t ->
            TrackRow(t, subtitle = listOfNotNull(t.trackNo?.toString(), t.artist).joinToString(". ")) {
                c.player.play(a.tracks, i, PlaySource.LIBRARY)
            }
        }
    }
}

@Composable
private fun Header(text: String) =
    Text(text, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(16.dp, 12.dp, 16.dp, 4.dp))

@Composable
private fun ArtistRow(a: Artist, onClick: () -> Unit) {
    Column(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 10.dp)) {
        Text(a.name)
        Text("${a.albumCount} albums", style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun AlbumRow(a: Album, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        AlbumArt(a.id, 56.dp)
        Column {
            Text(a.title)
            Text(listOfNotNull(a.artist, a.year?.toString(), "${a.trackCount} tracks").joinToString(" · "), style = MaterialTheme.typography.bodySmall)
        }
    }
}
