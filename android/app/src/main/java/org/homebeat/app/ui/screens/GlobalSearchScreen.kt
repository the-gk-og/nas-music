package org.homebeat.app.ui.screens

import org.homebeat.app.data.api.home
import org.homebeat.app.data.api.forYou
import org.homebeat.app.data.api.pins
import org.homebeat.app.data.api.globalSearch
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
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AddShoppingCart
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.TravelExplore
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.TextButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.media3.common.MediaItem
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.homebeat.app.AppContainer
import org.homebeat.app.data.api.CatalogItem
import org.homebeat.app.data.api.LyricsMatch
import org.homebeat.app.data.api.SearchResults
import org.homebeat.app.data.api.NewToGet
import org.homebeat.app.data.api.ShelfItem
import org.homebeat.app.net.RoutedCallFactory
import org.homebeat.app.playback.PlaySource
import org.homebeat.app.ui.LocalUi
import org.homebeat.app.ui.Message
import org.homebeat.app.ui.ShelfArt
import org.homebeat.app.ui.ShelfHeader
import org.homebeat.app.ui.ShelfRow
import org.homebeat.app.ui.songItems
import retrofit2.HttpException

object CatalogUrls {
    fun preview(id: String) = RoutedCallFactory.virtual("catalog/${android.net.Uri.encode(id)}/preview")
}

/** Global search (docs/v3-ux.md §4): your library, playlists, and music you don't own yet. */
@Composable
fun GlobalSearchScreen(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var query by rememberSaveable { mutableStateOf("") }
    var lyricsOnly by rememberSaveable { mutableStateOf(false) }
    var result by remember { mutableStateOf<SearchResults?>(null) }
    var previewing by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(query, lyricsOnly) {
        if (query.isBlank()) { result = null; return@LaunchedEffect }
        delay(300)
        result = runCatching {
            if (lyricsOnly) c.api.globalSearch(query.trim(), scope = "lyrics", limit = 50) else c.api.globalSearch(query.trim())
        }.recoverCatching { e ->
            // Server without /search yet: library-only search.
            if (e is HttpException && e.code() == 404) c.api.search(query.trim()).let { SearchResults(tracks = it.tracks, albums = it.albums, artists = it.artists) } else throw e
        }.getOrElse { if (lyricsOnly) SearchResults() else null }  // a server older than api 0.9 has no lyrics search
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
        val ok = runCatching { c.api.addToGet(NewToGet(catalogId = item.id)) }.isSuccess
        ui.toast = if (ok) "Added to To get" else "Couldn't add"
    }

    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            query, { query = it }, leadingIcon = { Icon(Icons.Default.Search, null) },
            trailingIcon = { org.homebeat.app.ui.VoiceCommandButton(c, onSearch = { query = it }) },
            placeholder = { Text("Songs, albums, artists — even ones you don't own") }, singleLine = true,
            shape = RoundedCornerShape(28.dp),
            modifier = Modifier.fillMaxWidth().padding(12.dp),
        )
        if (query.isNotBlank()) Row(Modifier.padding(horizontal = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(!lyricsOnly, { lyricsOnly = false }, label = { Text("All") })
            FilterChip(lyricsOnly, { lyricsOnly = true }, label = { Text("Lyrics") })
        }
        val q = query.trim()
        // Songs stored on the phone (Settings → Music on this phone) are searched right here, so they show
        // even offline or while the server is still answering.
        val phoneReady = rememberLocalMusicReady(c)
        val phoneTracks by c.localMusic.tracks.collectAsStateWithLifecycle()
        val phoneHits = remember(q, phoneTracks, phoneReady) {
            if (phoneReady && q.isNotEmpty()) org.homebeat.app.local.LocalIds.search(phoneTracks, q) else emptyList()
        }
        val r = result ?: run {
            if (!lyricsOnly && phoneHits.isNotEmpty()) {
                LazyColumn(Modifier.fillMaxSize()) {
                    item(key = "h-phone") { ShelfHeader("On this phone") }
                    songItems(c, phoneHits, "Search: ${q.take(12)}", PlaySource.SEARCH, searchQuery = q)
                }
                return@Column
            }
            return@Column Message(if (query.isBlank()) "Search your library and beyond." else "Searching…")
        }
        // A song the server also has shows once, as the server's.
        val phone = org.homebeat.app.local.LocalIds.withoutNasCopies(phoneHits, r.tracks + r.lyrics.map { it.track })
        val lyricTracks = r.lyrics.map { it.track }
        fun playLyric(i: Int) = c.player.play(lyricTracks, i, PlaySource.SEARCH, "Lyrics: ${q.take(12)}", searchQuery = q)
        if (lyricsOnly) {
            if (r.lyrics.isEmpty()) return@Column Message("No song has “$q” in its lyrics.")
            LazyColumn(Modifier.fillMaxSize()) {
                itemsIndexed(r.lyrics, key = { i, m -> "ly:$i:${m.track.id}" }) { i, m -> LyricsRow(m, onClick = { playLyric(i) }) }
            }
            return@Column
        }
        LazyColumn(Modifier.fillMaxSize()) {
            r.top?.let { top -> item(key = "top") { ShelfHeader("Top result"); TopResult(c, top, onSample = ::toggleSample, previewing = previewing) } }
            if (r.tracks.isNotEmpty()) item(key = "h-songs") { ShelfHeader("Songs") }
            songItems(c, r.tracks.take(8), "Search: ${q.take(12)}", PlaySource.SEARCH, searchQuery = q)
            if (phone.isNotEmpty()) item(key = "h-phone") { ShelfHeader("On this phone") }
            songItems(c, phone.take(8), "Search: ${q.take(12)}", PlaySource.SEARCH, searchQuery = q)
            if (r.albums.isNotEmpty()) item(key = "albums") {
                ShelfHeader("Albums")
                ShelfRow(c, r.albums.map { ShelfItem("album", it.id, it.title, it.artist, artUrl = org.homebeat.app.data.FeedRepository.art(it.id), album = it) })
            }
            if (r.artists.isNotEmpty()) item(key = "artists") {
                ShelfHeader("Artists")
                ShelfRow(c, r.artists.map { ShelfItem("artist", it.id, it.name, "${it.albumCount} albums") }, cardWidth = 110.dp)
            }
            if (r.playlists.isNotEmpty()) item(key = "playlists") {
                ShelfHeader("Playlists")
                ShelfRow(c, r.playlists.map { ShelfItem("playlist", it.id, it.name, "${it.trackCount} songs") })
            }
            if (r.lyrics.isNotEmpty()) item(key = "h-lyrics") {
                ShelfHeader("In lyrics") { if (r.lyrics.size >= 5) TextButton(onClick = { lyricsOnly = true }) { Text("See all") } }
            }
            itemsIndexed(r.lyrics, key = { i, m -> "ly:$i:${m.track.id}" }) { i, m -> LyricsRow(m, onClick = { playLyric(i) }) }
            val notOwned = r.catalog.filterNot { it.owned }
            if (notOwned.isNotEmpty()) item(key = "h-cat") { ShelfHeader("Not in your library") }
            itemsIndexed(notOwned, key = { i, it -> "cat:$i:${it.id}" }) { _, item ->
                CatalogRow(item, previewing == item.id, onSample = { toggleSample(item) }, onAdd = { addToGet(item) }, onSimilar = { item.artist?.let { query = it } })
            }
            r.catalogError?.let { err -> item(key = "caterr") { Text("Online catalogue unavailable: $err", style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(16.dp)) } }
            if (r.tracks.isEmpty() && phone.isEmpty() && r.albums.isEmpty() && r.artists.isEmpty() && r.catalog.isEmpty() && r.lyrics.isEmpty()) item { Message("No results.") }
        }
    }
}

/** Top result is a generic ShelfItem (library first, else catalogue). */
@Composable
private fun TopResult(c: AppContainer, top: ShelfItem, onSample: (CatalogItem) -> Unit, previewing: String?) {
    val ui = LocalUi.current
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    Card(Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            ShelfArt(top, 96.dp, round = top.type == "artist")
            Column(Modifier.weight(1f).padding(horizontal = 16.dp).clickable { org.homebeat.app.ui.openShelfItem(c, ui, scope, top) }) {
                Text(top.title, style = MaterialTheme.typography.titleLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(listOfNotNull(top.type.replaceFirstChar { it.uppercase() }, top.subtitle).joinToString(" · "), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (top.type == "track") IconButton(onClick = { org.homebeat.app.ui.openShelfItem(c, ui, scope, top) }) { Icon(Icons.Default.PlayArrow, "Play") }
            top.catalog?.takeIf { it.hasPreview }?.let { cat ->
                IconButton(onClick = { onSample(cat) }) { Icon(if (previewing == cat.id) Icons.Default.Stop else Icons.Default.PlayArrow, "Play sample") }
            }
        }
    }
}

/** A song whose lyrics matched, with the line that matched under its title. */
@Composable
private fun LyricsRow(m: LyricsMatch, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        ShelfArt(ShelfItem("track", m.track.id, m.track.title, artUrl = org.homebeat.app.data.FeedRepository.art(m.track.albumId)), 52.dp)
        Column(Modifier.weight(1f)) {
            Text(m.track.title, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(m.track.artist, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text("“${m.line}”", style = MaterialTheme.typography.bodySmall, fontStyle = FontStyle.Italic, maxLines = 1,
                overflow = TextOverflow.Ellipsis)
        }
    }
}

@Composable
private fun CatalogRow(item: CatalogItem, playing: Boolean, onSample: () -> Unit, onAdd: () -> Unit, onSimilar: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        ShelfArt(ShelfItem("catalog", item.id, item.title, artUrl = item.artUrl), 52.dp)
        Column(Modifier.weight(1f)) {
            Text(item.title, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(listOfNotNull(item.artist, item.type, item.year?.toString(), item.toGet?.let { "on your To get list" }).joinToString(" · "), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        if (item.hasPreview) IconButton(onClick = onSample) { Icon(if (playing) Icons.Default.Stop else Icons.Default.PlayArrow, "Play 30-second sample") }
        IconButton(onClick = onAdd) { Icon(Icons.Default.AddShoppingCart, "Add to To get") }
        IconButton(onClick = onSimilar, enabled = item.artist != null) { Icon(Icons.Default.TravelExplore, "Find similar in my library") }
    }
}

