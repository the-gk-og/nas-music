package org.nasmusic.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Radio
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilledTonalIconButton
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil3.compose.AsyncImage
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.AlbumDetail
import org.nasmusic.app.data.api.ArtistDetail
import org.nasmusic.app.data.api.RadioSeed
import org.nasmusic.app.data.api.ShelfItem
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.playback.MediaItems
import org.nasmusic.app.playback.PlaySource
import org.nasmusic.app.ui.LocalMotion
import org.nasmusic.app.ui.LocalUi
import org.nasmusic.app.ui.Loading
import org.nasmusic.app.ui.ShelfHeader
import org.nasmusic.app.ui.ShelfRow
import org.nasmusic.app.ui.songItems

/** Page style for album/artist/playlist pages: the global default, switchable per page from ⋯ (docs/v3-ux.md §3.4). */
@Composable
private fun rememberPageStyle(c: AppContainer): androidx.compose.runtime.MutableState<String> {
    val settings by c.settings.settings.collectAsStateWithLifecycle()
    return remember(settings.pageStyle) { mutableStateOf(settings.pageStyle) }
}

@Composable
private fun PageMenu(style: String, onStyle: (String) -> Unit, extra: @Composable (close: () -> Unit) -> Unit = {}) {
    var open by remember { mutableStateOf(false) }
    Box {
        IconButton(onClick = { open = true }) { Icon(Icons.Default.MoreVert, "More") }
        DropdownMenu(open, { open = false }) {
            DropdownMenuItem({ Text(if (style == "big") "Simple list view" else "Big header view") }, { open = false; onStyle(if (style == "big") "simple" else "big") })
            extra { open = false }
        }
    }
}

/**
 * Big header: large cover, the art's colour bleeding into black, Play / Shuffle / Radio / ⋯.
 * Expressive motion adds a parallax on the cover as you scroll.
 */
@Composable
private fun BigHeader(
    artModel: Any?,
    title: String,
    subtitle: String?,
    meta: String?,
    tintUrl: String?,
    listState: LazyListState,
    onPlay: () -> Unit,
    onShuffle: () -> Unit,
    onRadio: (() -> Unit)?,
    onSubtitle: (() -> Unit)?,
    menu: @Composable () -> Unit,
    round: Boolean = false,
) {
    val tint = tintUrl?.let { rememberDominantColor(it) } ?: MaterialTheme.colorScheme.surfaceContainerHigh
    val motion = LocalMotion.current
    Column(
        Modifier.fillMaxWidth().background(Brush.verticalGradient(listOf(tint.copy(alpha = 0.8f), Color.Black))),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) { menu() }
        AsyncImage(
            artModel, null, contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxWidth(0.62f).height(240.dp).clip(if (round) androidx.compose.foundation.shape.CircleShape else RoundedCornerShape(8.dp))
                .graphicsLayer {
                    if (motion.expressive && listState.firstVisibleItemIndex == 0) {
                        translationY = listState.firstVisibleItemScrollOffset * 0.4f
                        alpha = 1f - (listState.firstVisibleItemScrollOffset / 900f).coerceIn(0f, 0.6f)
                    }
                },
        )
        Spacer(Modifier.height(16.dp))
        Text(title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, modifier = Modifier.padding(horizontal = 24.dp))
        subtitle?.let {
            Text(it, color = MaterialTheme.colorScheme.primary, modifier = Modifier.then(if (onSubtitle != null) Modifier.clickable(onClick = onSubtitle) else Modifier))
        }
        meta?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        Row(Modifier.padding(16.dp), horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
            FilledTonalIconButton(onClick = onShuffle) { Icon(Icons.Default.Shuffle, "Shuffle") }
            Button(onClick = onPlay, modifier = Modifier.height(52.dp)) { Icon(Icons.Default.PlayArrow, null); Text(" Play") }
            if (onRadio != null) FilledTonalIconButton(onClick = onRadio) { Icon(Icons.Default.Radio, "Radio") }
        }
    }
}

@Composable
private fun SimpleHeader(c: AppContainer, albumId: String?, title: String, subtitle: String?, meta: String?, onPlay: () -> Unit, onShuffle: () -> Unit, onRadio: (() -> Unit)?, menu: @Composable () -> Unit) {
    Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
        albumId?.let { org.nasmusic.app.ui.AlbumArt(it, 96.dp) }
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
            subtitle?.let { Text(it, color = MaterialTheme.colorScheme.primary) }
            meta?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
            Row {
                IconButton(onClick = onPlay) { Icon(Icons.Default.PlayArrow, "Play") }
                IconButton(onClick = onShuffle) { Icon(Icons.Default.Shuffle, "Shuffle") }
                if (onRadio != null) IconButton(onClick = onRadio) { Icon(Icons.Default.Radio, "Radio") }
            }
        }
        menu()
    }
}

private fun startRadio(c: AppContainer, ui: org.nasmusic.app.ui.UiController, scope: kotlinx.coroutines.CoroutineScope, seed: RadioSeed) = scope.launch {
    if (c.player.startRadio(seed)) ui.go("now") else ui.toast = "Couldn't start the radio"
}

@Composable
fun AlbumScreen(c: AppContainer, albumId: String) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var album by remember { mutableStateOf<AlbumDetail?>(null) }
    var more by remember { mutableStateOf<List<ShelfItem>>(emptyList()) }
    var style by rememberPageStyle(c)
    val list = rememberLazyListState()
    LaunchedEffect(albumId) {
        album = runCatching { c.api.album(albumId) }.getOrNull()
        album?.let { a ->
            more = runCatching { c.api.artist(a.artistId).albums }.getOrDefault(emptyList())
                .filter { it.id != a.id }.map { ShelfItem("album", it.id, it.title, it.year?.toString(), artUrl = org.nasmusic.app.data.FeedRepository.art(it.id)) }
        }
    }
    val a = album ?: return Loading()
    val name = "Album: ${a.title}"
    val play = { c.player.play(a.tracks, 0, PlaySource.LIBRARY, name) }
    val shuffle = { c.player.play(a.tracks.shuffled(), 0, PlaySource.LIBRARY, name) }
    val radio = { startRadio(c, ui, scope, RadioSeed("album", a.id)); Unit }
    val meta = listOfNotNull(a.year?.toString(), "${a.tracks.size} songs").joinToString(" · ")
    val menu: @Composable () -> Unit = {
        PageMenu(style, { style = it }) { close ->
            DropdownMenuItem({ Text("Add all to queue") }, { close(); c.player.addToQueue(a.tracks) })
            DropdownMenuItem({ Text("Add all to playlist") }, { close(); ui.addToPlaylist = a.tracks })
            DropdownMenuItem({ Text("Pin to Home") }, { close(); scope.launch { runCatching { c.api.pin(org.nasmusic.app.data.api.NewPin("album", a.id)) }; ui.toast = "Pinned to Home" } })
        }
    }
    LazyColumn(Modifier.fillMaxSize(), state = list) {
        item {
            if (style == "big") BigHeader(MediaItems.artUrl(a.id, 512), a.title, a.artist, meta, MediaItems.artUrl(a.id, 128), list, play, shuffle, radio, { ui.go("artist/${a.artistId}") }, menu)
            else SimpleHeader(c, a.id, a.title, a.artist, meta, play, shuffle, radio, menu)
        }
        songItems(c, a.tracks, name, PlaySource.LIBRARY)
        if (style == "big" && more.isNotEmpty()) item { ShelfHeader("More by ${a.artist}"); ShelfRow(c, more) }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

@Composable
fun ArtistScreen(c: AppContainer, artistId: String) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var artist by remember { mutableStateOf<ArtistDetail?>(null) }
    var style by rememberPageStyle(c)
    val list = rememberLazyListState()
    LaunchedEffect(artistId) { artist = runCatching { c.api.artist(artistId) }.getOrNull() }
    val a = artist ?: return Loading()
    val coverAlbum = a.albums.firstOrNull()?.id
    val radio = { startRadio(c, ui, scope, RadioSeed("artist", a.id)); Unit }
    // Without top tracks from the server, "Play" plays the newest album.
    suspend fun allTracks(): List<Track> = a.topTracks.ifEmpty {
        a.albums.sortedByDescending { it.year ?: 0 }.firstOrNull()?.let { runCatching { c.api.album(it.id).tracks }.getOrNull() }.orEmpty()
    }
    val play = { scope.launch { allTracks().takeIf { it.isNotEmpty() }?.let { c.player.play(it, 0, PlaySource.LIBRARY, "Artist: ${a.name}") } }; Unit }
    val shuffle = { scope.launch { allTracks().takeIf { it.isNotEmpty() }?.let { c.player.play(it.shuffled(), 0, PlaySource.LIBRARY, "Artist: ${a.name}") } }; Unit }
    val menu: @Composable () -> Unit = {
        PageMenu(style, { style = it }) { close ->
            DropdownMenuItem({ Text("Pin to Home") }, { close(); scope.launch { runCatching { c.api.pin(org.nasmusic.app.data.api.NewPin("artist", a.id)) }; ui.toast = "Pinned to Home" } })
        }
    }
    LazyColumn(Modifier.fillMaxSize(), state = list) {
        item {
            if (style == "big") BigHeader(coverAlbum?.let { MediaItems.artUrl(it, 512) }, a.name, null, "${a.albums.size} albums", coverAlbum?.let { MediaItems.artUrl(it, 128) }, list, play, shuffle, radio, null, menu, round = true)
            else SimpleHeader(c, coverAlbum, a.name, null, "${a.albums.size} albums", play, shuffle, radio, menu)
        }
        if (a.topTracks.isNotEmpty()) {
            item { ShelfHeader("Top tracks") }
            songItems(c, a.topTracks.take(5), "Artist: ${a.name}", PlaySource.LIBRARY)
        }
        item {
            ShelfHeader("Albums")
            ShelfRow(c, a.albums.sortedByDescending { it.year ?: 0 }.map { ShelfItem("album", it.id, it.title, it.year?.toString(), artUrl = org.nasmusic.app.data.FeedRepository.art(it.id)) })
        }
        item {
            ShelfHeader("Artist radio")
            ShelfRow(c, listOf(ShelfItem("radio", "artist:${a.id}", "${a.name} radio", "Similar sound from your library", artUrl = org.nasmusic.app.data.FeedRepository.art(coverAlbum), seed = RadioSeed("artist", a.id))))
        }
        if (a.similar.isNotEmpty()) item {
            ShelfHeader("Similar artists")
            ShelfRow(c, a.similar.map { ShelfItem("artist", it.id, it.name, "${it.albumCount} albums") }, cardWidth = 110.dp)
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

