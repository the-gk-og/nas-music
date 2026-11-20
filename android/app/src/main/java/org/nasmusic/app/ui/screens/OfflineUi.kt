package org.nasmusic.app.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudOff
import androidx.compose.material.icons.filled.DownloadDone
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material3.Card
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.Mix
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.data.db.DownloadEntity
import org.nasmusic.app.data.db.OfflinePinEntity
import org.nasmusic.app.playback.PlaySource
import org.nasmusic.app.ui.LocalUi
import org.nasmusic.app.ui.Message
import org.nasmusic.app.ui.ShelfHeader
import org.nasmusic.app.ui.songItems

/** What the offline Home shows, grouped from what's downloaded. */
data class OfflineHome(
    val favourites: List<Track>,
    /** Cached mixes with their downloaded songs (only mixes with at least one). */
    val mixes: List<Pair<Mix, List<Track>>>,
    val recent: List<Track>,
    val pins: List<Pair<OfflinePinEntity, List<Track>>>,
) {
    companion object {
        fun build(rows: List<DownloadEntity>, tracks: Map<String, Track>, mixes: List<Mix>, pins: List<OfflinePinEntity>): OfflineHome {
            val done = rows.filter { it.complete && it.trackId in tracks }
            val held = done.map { it.trackId }.toSet()
            val favs = done.filter { r -> "favourite" in r.reasons.split(',') || r.why == "favourite" || tracks[r.trackId]?.starred == true }
            val mixOut = mixes.mapNotNull { m -> m.tracks.mapNotNull { mt -> tracks[mt.track.id]?.takeIf { it.id in held } }.takeIf { it.isNotEmpty() }?.let { m to it } }
            val recent = done.filter { r -> "recent" in r.reasons.split(',') }.sortedByDescending { it.priority }
            val pinOut = pins.mapNotNull { p -> p.ids.filter { it in held }.mapNotNull { tracks[it] }.takeIf { it.isNotEmpty() }?.let { p to it } }
            return OfflineHome(favs.mapNotNull { tracks[it.trackId] }, mixOut, recent.mapNotNull { tracks[it.trackId] }, pinOut)
        }
    }
}

/** Offline banner + shelves made from downloads, for Home when there's no connection. */
fun LazyListScope.offlineHomeItems(c: AppContainer) {
    item(key = "offline-banner") { OfflineBanner(c) }
    item(key = "offline-shelves") { OfflineShelves(c) }
}

@Composable
private fun OfflineBanner(c: AppContainer) {
    val ui = LocalUi.current
    val tracks by c.offline.offlineTracks.collectAsStateWithLifecycle()
    Card(Modifier.fillMaxWidth().padding(16.dp)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Default.CloudOff, null)
                Spacer(Modifier.width(12.dp))
                Text("You're offline · ${tracks.size} songs downloaded", style = MaterialTheme.typography.titleMedium)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilledTonalButton(onClick = {
                    if (tracks.isNotEmpty()) c.player.play(tracks.shuffled(), 0, PlaySource.LIBRARY, "Downloads (shuffled)")
                }, enabled = tracks.isNotEmpty()) { Icon(Icons.Default.Shuffle, null); Text(" Shuffle downloads") }
                OutlinedButton(onClick = { ui.go("downloads") }) { Text("Downloads") }
            }
        }
    }
}

@Composable
private fun OfflineShelves(c: AppContainer) {
    val rows by c.offline.downloads.collectAsStateWithLifecycle(emptyList())
    val tracks by c.offline.offlineTracks.collectAsStateWithLifecycle()
    val mixes by c.library.cachedMixes.collectAsStateWithLifecycle(emptyList())
    val pins by c.offline.pins.collectAsStateWithLifecycle(emptyList())
    val home = remember(rows, tracks, mixes, pins) { OfflineHome.build(rows, tracks.associateBy { it.id }, mixes, pins) }
    Column {
        if (home.mixes.isNotEmpty()) {
            ShelfHeader("Mixes on this phone")
            home.mixes.forEach { (m, t) -> CollectionRow(m.title, "${t.size} of ${m.tracks.size} songs downloaded") { c.player.play(t, 0, PlaySource.MIX, m.title, mixId = m.id) } }
        }
        if (home.pins.isNotEmpty()) {
            ShelfHeader("Kept offline")
            home.pins.forEach { (p, t) -> CollectionRow(p.title, "${p.type.replaceFirstChar { it.uppercase() }} · ${t.size} songs") { c.player.play(t, 0, PlaySource.LIBRARY, p.title) } }
        }
        if (home.favourites.isNotEmpty()) {
            ShelfHeader("Favourites")
            CollectionRow("Downloaded favourites", "${home.favourites.size} songs") { c.player.play(home.favourites, 0, PlaySource.FAVOURITES, "Favourites") }
        }
        if (home.recent.isNotEmpty()) {
            ShelfHeader("Recently played")
            CollectionRow("Recently played, downloaded", "${home.recent.size} songs") { c.player.play(home.recent, 0, PlaySource.LIBRARY, "Recently played") }
        }
        if (tracks.isEmpty()) Message("Nothing is downloaded yet. Turn on Smart downloads in Settings, or download songs, albums and playlists from their menus.")
    }
}

@Composable
private fun CollectionRow(title: String, subtitle: String, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Icon(Icons.Default.DownloadDone, null, tint = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** Library › Downloaded: everything on the phone, kept collections first, then songs by artist and album. */
@Composable
fun DownloadedSection(c: AppContainer) {
    val tracks by c.offline.offlineTracks.collectAsStateWithLifecycle()
    val pins by c.offline.pins.collectAsStateWithLifecycle(emptyList())
    val byId = remember(tracks) { tracks.associateBy { it.id } }
    val sorted = remember(tracks) {
        tracks.sortedWith(compareBy<Track>({ it.artist.lowercase() }, { it.album.lowercase() }, { it.discNo ?: 0 }, { it.trackNo ?: 0 }, { it.title.lowercase() }))
    }
    if (tracks.isEmpty()) return Message("Nothing downloaded yet.")
    LazyColumn(Modifier.fillMaxSize()) {
        items(pins, key = { "p:" + it.key }) { p ->
            val t = p.ids.mapNotNull { byId[it] }
            if (t.isNotEmpty()) CollectionRow(p.title, "${p.type.replaceFirstChar { it.uppercase() }} · ${t.size} songs") { c.player.play(t, 0, PlaySource.LIBRARY, p.title) }
        }
        item { Text("${sorted.size} songs", style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) }
        songItems(c, sorted, "Downloads", PlaySource.LIBRARY)
        item { Spacer(Modifier.height(24.dp)) }
    }
}
