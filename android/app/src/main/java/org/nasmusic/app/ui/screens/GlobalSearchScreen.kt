package org.nasmusic.app.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AddShoppingCart
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.TravelExplore
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
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
import androidx.media3.common.MediaItem
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.CatalogItem
import org.nasmusic.app.data.api.GlobalSearch
import org.nasmusic.app.data.api.NewToGet
import org.nasmusic.app.data.api.ShelfItem
import org.nasmusic.app.data.api.SearchTop
import org.nasmusic.app.net.RoutedCallFactory
import org.nasmusic.app.playback.PlaySource
import org.nasmusic.app.ui.LocalUi
import org.nasmusic.app.ui.Message
import org.nasmusic.app.ui.ShelfArt
import org.nasmusic.app.ui.ShelfHeader
import org.nasmusic.app.ui.ShelfRow
import org.nasmusic.app.ui.songItems
import retrofit2.HttpException

object CatalogUrls {
    fun cover(id: String, size: Int = 256) = "catalog/${android.net.Uri.encode(id)}/cover?size=$size"
    fun preview(id: String) = RoutedCallFactory.virtual("catalog/${android.net.Uri.encode(id)}/preview")
}

/** Global search (docs/v3-ux.md §4): your library, playlists, and music you don't own yet. */
@Composable
fun GlobalSearchScreen(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var query by rememberSaveable { mutableStateOf("") }
    var result by remember { mutableStateOf<GlobalSearch?>(null) }
    var previewing by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(query) {
        if (query.isBlank()) { result = null; return@LaunchedEffect }
        delay(300)
        result = runCatching { c.api.globalSearch(query.trim()) }.recoverCatching { e ->
            // Server without /search yet: library-only search.
            if (e is HttpException && e.code() == 404) c.api.search(query.trim()).let { GlobalSearch(tracks = it.tracks, albums = it.albums, artists = it.artists) } else throw e
        }.getOrNull()
    }
    // 30 s samples play on their own player (proxied by the server, never stored), leaving the queue alone.
    val sampler = remember {
        ExoPlayer.Builder(c.context).setMediaSourceFactory(DefaultMediaSourceFactory(OkHttpDataSource.Factory(c.callFactory))).build()
    }
    DisposableEffect(Unit) { onDispose { sampler.release() } }
    fun toggleSample(item: CatalogItem) {
        if (previewing == item.id) { sampler.stop(); previewing = null; return }
        sampler.setMediaItem(MediaItem.fromUri(CatalogUrls.preview(item.id))); sampler.prepare(); sampler.play()
        previewing = item.id
    }
    fun addToGet(item: CatalogItem) = scope.launch {
        val ok = runCatching { c.api.addToGet(NewToGet(item.id, item.kind, item.title, item.artist, item.album)) }.isSuccess
        ui.toast = if (ok) "Added to To get" else "Couldn't add"
    }

    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        OutlinedTextField(
            query, { query = it }, leadingIcon = { Icon(Icons.Default.Search, null) },
            placeholder = { Text("Songs, albums, artists — even ones you don't own") }, singleLine = true,
            shape = RoundedCornerShape(28.dp),
            modifier = Modifier.fillMaxWidth().padding(12.dp),
        )
        val r = result ?: return@Column Message(if (query.isBlank()) "Search your library and beyond." else "Searching…")
        val q = query.trim()
        LazyColumn(Modifier.fillMaxSize()) {
            r.top?.let { top -> item(key = "top") { ShelfHeader("Top result"); TopResult(c, top, onSample = ::toggleSample, previewing = previewing) } }
            if (r.tracks.isNotEmpty()) item(key = "h-songs") { ShelfHeader("Songs") }
            songItems(c, r.tracks.take(8), "Search: ${q.take(12)}", PlaySource.SEARCH, searchQuery = q)
            if (r.albums.isNotEmpty()) item(key = "albums") {
                ShelfHeader("Albums")
                ShelfRow(c, r.albums.map { ShelfItem("album", it.id, it.title, it.artist, albumId = it.id) })
            }
            if (r.artists.isNotEmpty()) item(key = "artists") {
                ShelfHeader("Artists")
                ShelfRow(c, r.artists.map { ShelfItem("artist", it.id, it.name, "${it.albumCount} albums") }, cardWidth = 110.dp)
            }
            if (r.playlists.isNotEmpty()) item(key = "playlists") {
                ShelfHeader("Playlists")
                ShelfRow(c, r.playlists.map { ShelfItem("playlist", it.id, it.name, "${it.trackCount} songs") })
            }
            val notOwned = r.catalog.filterNot { it.owned }
            if (notOwned.isNotEmpty()) item(key = "h-cat") { ShelfHeader("Not in your library") }
            items(notOwned, key = { "cat:" + it.id }) { item ->
                CatalogRow(item, previewing == item.id, onSample = { toggleSample(item) }, onAdd = { addToGet(item) }, onSimilar = { query = item.artist })
            }
            if (r.tracks.isEmpty() && r.albums.isEmpty() && r.artists.isEmpty() && r.catalog.isEmpty()) item { Message("No results.") }
        }
    }
}

@Composable
private fun TopResult(c: AppContainer, top: SearchTop, onSample: (CatalogItem) -> Unit, previewing: String?) {
    val ui = LocalUi.current
    Card(Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            val (item, open) = when {
                top.album != null -> ShelfItem("album", top.album.id, top.album.title, top.album.artist, albumId = top.album.id) to { ui.go("album/${top.album.id}") }
                top.artist != null -> ShelfItem("artist", top.artist.id, top.artist.name, "Artist") to { ui.go("artist/${top.artist.id}") }
                top.playlist != null -> ShelfItem("playlist", top.playlist.id, top.playlist.name, "Playlist") to { ui.go("playlist/${top.playlist.id}") }
                top.track != null -> ShelfItem("track", top.track.id, top.track.title, top.track.artist, albumId = top.track.albumId) to {
                    c.player.play(listOf(top.track), 0, PlaySource.SEARCH, "Search: ${top.track.title.take(12)}")
                }
                top.catalog != null -> ShelfItem("toget", top.catalog.id, top.catalog.title, top.catalog.artist,
                    artPath = if (top.catalog.hasCover) CatalogUrls.cover(top.catalog.id) else null) to { onSample(top.catalog) }
                else -> return@Row
            }
            ShelfArt(item, 96.dp, round = item.type == "artist")
            Column(Modifier.weight(1f).padding(horizontal = 16.dp).clickable(onClick = open)) {
                Text(item.title, style = MaterialTheme.typography.titleLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
                item.subtitle?.let { Text(it, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
            if (top.track != null) IconButton(onClick = open) { Icon(Icons.Default.PlayArrow, "Play") }
            if (top.catalog?.hasPreview == true) IconButton(onClick = { onSample(top.catalog) }) {
                Icon(if (previewing == top.catalog.id) Icons.Default.Stop else Icons.Default.PlayArrow, "Play sample")
            }
        }
    }
}

@Composable
private fun CatalogRow(item: CatalogItem, playing: Boolean, onSample: () -> Unit, onAdd: () -> Unit, onSimilar: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        ShelfArt(ShelfItem("toget", item.id, item.title, artPath = if (item.hasCover) CatalogUrls.cover(item.id, 128) else null), 52.dp)
        Column(Modifier.weight(1f)) {
            Text(item.title, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(listOfNotNull(item.artist, item.kind, item.year?.toString()).joinToString(" · "), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        if (item.hasPreview) IconButton(onClick = onSample) { Icon(if (playing) Icons.Default.Stop else Icons.Default.PlayArrow, "Play 30-second sample") }
        IconButton(onClick = onAdd) { Icon(Icons.Default.AddShoppingCart, "Add to To get") }
        IconButton(onClick = onSimilar) { Icon(Icons.Default.TravelExplore, "Find similar in my library") }
    }
}

