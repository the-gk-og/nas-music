package org.nasmusic.app.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.QueueMusic
import androidx.compose.material.icons.filled.Album
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Radio
import androidx.compose.material.icons.filled.ShoppingBag
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.Sell
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import coil3.compose.AsyncImage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.NewPin
import org.nasmusic.app.data.api.RadioSeed
import org.nasmusic.app.data.api.ShelfItem
import org.nasmusic.app.net.RoutedCallFactory
import org.nasmusic.app.playback.MediaItems
import org.nasmusic.app.playback.PlaySource

/** Art for any shelf item: our album art, or a server-proxied image, else an icon tile. */
@Composable
fun ShelfArt(item: ShelfItem, size: Dp, round: Boolean = false) {
    val shape = if (round) CircleShape else RoundedCornerShape(8.dp)
    val px = if (size > 100.dp) 512 else 256
    val model = artModel(item.artUrl, px) ?: item.track?.albumId?.let { MediaItems.artUrl(it, px) }
    Box(Modifier.size(size).clip(shape).background(MaterialTheme.colorScheme.surfaceContainerHigh), contentAlignment = Alignment.Center) {
        Icon(iconFor(item.type), null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(size / 3))
        if (model != null) AsyncImage(model, null, contentScale = ContentScale.Crop, modifier = Modifier.fillMaxSize())
    }
}

/** `artUrl` is relative to the API base; add a size hint unless one is there. */
fun artModel(artUrl: String?, px: Int): String? = artUrl?.takeIf { it.isNotBlank() }?.let {
    RoutedCallFactory.virtual(if ('?' in it) it else "$it?size=$px")
}

fun iconFor(type: String): ImageVector = when (type) {
    "album" -> Icons.Default.Album
    "artist" -> Icons.Default.Person
    "radio" -> Icons.Default.Radio
    "mix" -> Icons.Default.AutoAwesome
    "queue" -> Icons.AutoMirrored.Filled.QueueMusic
    "toget", "suggestion", "catalog" -> Icons.Default.ShoppingBag
    "device" -> Icons.Default.Devices
    "genre" -> Icons.Default.Sell
    "folder" -> Icons.Default.Folder
    "playlist", "smart_list" -> Icons.AutoMirrored.Filled.List
    else -> Icons.Default.MusicNote
}

/** What tapping a shelf card does, for every item type the server may send. */
fun openShelfItem(c: AppContainer, ui: UiController, scope: CoroutineScope, item: ShelfItem, siblings: List<ShelfItem> = emptyList()) {
    when (item.type) {
        "album" -> ui.go("album/${item.id}")
        "artist" -> ui.go("artist/${item.id}")
        "playlist" -> ui.go("playlist/${item.id}")
        "smart_list" -> ui.go("list/${item.id}")
        "genre" -> ui.go("genre/${android.net.Uri.encode(item.id)}")
        "folder" -> ui.go("folder/${android.net.Uri.encode(item.id)}")
        "toget" -> ui.go("toget")
        "suggestion" -> ui.go("discover")
        "catalog" -> item.catalog?.ownedId?.takeIf { item.catalog.owned }?.let { ui.go("${if (item.catalog.type == "artist") "artist" else "album"}/$it") } ?: ui.go("search")
        "device" -> c.player.transferHere()
        "queue" -> scope.launch {
            val id = item.progress?.queueId ?: item.queue?.id ?: item.id
            c.queueSync.load(id)?.let { c.player.playQueue(it) }; ui.go("now")
        }
        "mix" -> scope.launch {
            runCatching { c.api.mix(item.id) }.onSuccess { m ->
                c.player.play(m.tracks.map { it.track }, 0, PlaySource.MIX, m.title, m.id, m.tracks.map { it.reason })
                ui.go("now")
            }.onFailure { ui.toast = "Couldn't load that mix" }
        }
        "radio" -> scope.launch {
            if (c.player.startRadio(item.seed ?: RadioSeed("random"))) ui.go("now") else ui.toast = "Couldn't start the radio"
        }
        "track" -> {
            val tracks = siblings.mapNotNull { it.track }.ifEmpty { listOfNotNull(item.track) }
            val i = tracks.indexOfFirst { it.id == item.id }.coerceAtLeast(0)
            if (tracks.isNotEmpty()) c.player.play(tracks, i, PlaySource.LIBRARY, item.title)
        }
    }
}

/** PinType in the contract. */
private val PINNABLE = setOf("album", "artist", "playlist", "mix", "queue", "smart_list", "genre", "folder")

/** A YouTube-Music-style card: art + title + subtitle. Long-press → Pin to Home / Start radio. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun ShelfCard(c: AppContainer, item: ShelfItem, width: Dp = 140.dp, siblings: List<ShelfItem> = emptyList(), onUnpin: (() -> Unit)? = null) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var menu by remember { mutableStateOf(false) }
    Box {
        Column(
            Modifier.width(width).combinedClickable(onClick = { openShelfItem(c, ui, scope, item, siblings) }, onLongClick = { menu = true }),
        ) {
            ShelfArt(item, width, round = item.type == "artist")
            item.progress?.let { p -> LinearProgressIndicator(progress = { p.fraction }, modifier = Modifier.fillMaxWidth().padding(top = 2.dp)) }
            Text(item.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 6.dp))
            item.subtitle?.let { Text(it, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        }
        DropdownMenu(menu, { menu = false }) {
            if (item.type in PINNABLE) {
                if (onUnpin != null) DropdownMenuItem({ Text("Unpin from Home") }, { menu = false; onUnpin() })
                else DropdownMenuItem({ Text("Pin to Home") }, {
                    menu = false
                    scope.launch { ui.toast = if (runCatching { c.api.pin(NewPin(item.type, item.id)) }.isSuccess) "Pinned to Home" else "Couldn't pin" }
                })
            }
            radioSeedFor(item)?.let { seed ->
                DropdownMenuItem({ Text("Start radio") }, { menu = false; scope.launch { if (c.player.startRadio(seed)) ui.go("now") } })
            }
        }
    }
}

fun radioSeedFor(item: ShelfItem): RadioSeed? = when (item.type) {
    "album", "artist", "playlist", "track" -> RadioSeed(item.type, item.id)
    else -> null
}

@Composable
fun ShelfHeader(title: String, action: (@Composable () -> Unit)? = null) {
    Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 8.dp, top = 20.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(title, style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
        action?.invoke()
    }
}

@Composable
fun ShelfRow(c: AppContainer, items: List<ShelfItem>, cardWidth: Dp = 140.dp, onUnpin: ((ShelfItem) -> Unit)? = null) {
    LazyRow(contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        items(items, key = { it.type + ":" + it.id }) { ShelfCard(c, it, cardWidth, items, onUnpin?.let { f -> { f(it) } }) }
    }
}

/** Spotify-style 2-row quick picks: small art + title tiles, scrolled horizontally in pairs. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun QuickPicks(c: AppContainer, items: List<ShelfItem>) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val columns = items.chunked(2)
    LazyRow(contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        items(columns) { pair ->
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                pair.forEach { item ->
                    Row(
                        Modifier.width(220.dp).height(56.dp).clip(RoundedCornerShape(6.dp)).background(MaterialTheme.colorScheme.surfaceContainerHigh)
                            .combinedClickable(onClick = { openShelfItem(c, ui, scope, item, items) }),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        ShelfArt(item, 56.dp)
                        Text(item.title, maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(horizontal = 10.dp))
                    }
                }
            }
        }
    }
}
