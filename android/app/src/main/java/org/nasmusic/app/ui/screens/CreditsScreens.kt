package org.nasmusic.app.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil3.compose.AsyncImage
import kotlinx.coroutines.delay
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.CreditEntry
import org.nasmusic.app.data.api.CreditPerson
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.data.api.TrackCredits
import org.nasmusic.app.playback.MediaItems
import org.nasmusic.app.playback.PlaySource
import org.nasmusic.app.ui.LocalUi
import org.nasmusic.app.ui.Loading
import org.nasmusic.app.ui.Message
import org.nasmusic.app.ui.SongRow

// ---------- api 0.12: song credits ----------

@Composable
private fun CreditsBackBar(title: String) {
    val ui = LocalUi.current
    Row(Modifier.fillMaxWidth().padding(end = 8.dp, top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = { ui.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
        Text(title, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f),
            maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

/** Producers, songwriters, musicians and engineers of one song; tap a person for their other work in the library. */
@Composable
fun CreditsScreen(c: AppContainer, trackId: String) {
    val ui = LocalUi.current
    val uriHandler = androidx.compose.ui.platform.LocalUriHandler.current
    var track by remember { mutableStateOf<Track?>(null) }
    var credits by remember { mutableStateOf<TrackCredits?>(null) }
    var failed by remember { mutableStateOf(false) }
    LaunchedEffect(trackId) {
        track = runCatching { c.api.track(trackId) }.getOrNull()
        credits = runCatching { c.api.trackCredits(trackId) }.onFailure { failed = true }.getOrNull()
        // The server looks the song up on MusicBrainz in the background: poll a few times while it's pending.
        var n = 0
        while (credits?.status == "pending" && n < 8) {
            delay(2000L + n * 1000L); n++
            credits = runCatching { c.api.trackCredits(trackId) }.getOrNull() ?: break
        }
    }
    Column(Modifier.fillMaxSize()) {
        CreditsBackBar("Credits")
        val cr = credits
        when {
            failed && cr == null -> Message("Song credits need a server with api 0.12 or newer.")
            cr == null -> Loading()
            else -> LazyColumn(Modifier.fillMaxSize()) {
                track?.let { t ->
                    item {
                        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                            AsyncImage(MediaItems.artUrl(t.albumId, 128), null, Modifier.size(56.dp).clip(RoundedCornerShape(6.dp)))
                            Column(Modifier.weight(1f).padding(start = 12.dp)) {
                                Text(t.title, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text("${t.artist} · ${t.album}", style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                        }
                    }
                }
                cr.groups.forEach { g ->
                    item(key = "h:${g.id}") {
                        Text(g.label, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold,
                            modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 18.dp, bottom = 4.dp))
                    }
                    items(g.people, key = { "${g.id}:${it.personId}" }) { p ->
                        CreditRow(p, onOpen = { ui.go("person/${android.net.Uri.encode(p.personId)}") },
                            onArtist = p.artistId?.let { aid -> { ui.go("artist/$aid") } })
                    }
                }
                item {
                    val note = when {
                        cr.status == "pending" -> "Looking this song up on MusicBrainz…"
                        cr.groups.isNotEmpty() -> null
                        cr.status == "not_found" -> "MusicBrainz doesn't know this song yet, and its tags name no composer or lyricist."
                        cr.status == "error" -> "Couldn't reach MusicBrainz. Try again later."
                        else -> "No credits for this song."
                    }
                    note?.let {
                        Text(it, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(16.dp))
                    }
                    cr.musicbrainzUrl?.let { url ->
                        Text("Credits from MusicBrainz. Missing someone? Anyone can add credits there.",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.fillMaxWidth().clickable { runCatching { uriHandler.openUri(url) } }.padding(16.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun CreditRow(p: CreditEntry, onOpen: () -> Unit, onArtist: (() -> Unit)?) {
    Row(Modifier.fillMaxWidth().clickable(onClick = onOpen).padding(start = 16.dp, end = 8.dp, top = 8.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(p.name, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(p.roles.joinToString(", ") + if (p.albumWide) " (album)" else "", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (p.otherTracks > 0) {
                Text("${p.otherTracks} other song${if (p.otherTracks == 1) "" else "s"} in your library",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
            }
        }
        if (onArtist != null) IconButton(onClick = onArtist) { Icon(Icons.Default.Person, "Artist page") }
        Icon(Icons.Default.ChevronRight, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** One credited person and every song in the library they worked on, grouped by their role on it. */
@OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
@Composable
fun PersonScreen(c: AppContainer, personId: String) {
    val ui = LocalUi.current
    val uriHandler = androidx.compose.ui.platform.LocalUriHandler.current
    var person by remember { mutableStateOf<CreditPerson?>(null) }
    var failed by remember { mutableStateOf(false) }
    LaunchedEffect(personId) { person = runCatching { c.api.person(personId) }.onFailure { failed = true }.getOrNull() }
    Column(Modifier.fillMaxSize()) {
        CreditsBackBar(person?.name ?: "Credits")
        val p = person
        when {
            failed && p == null -> Message("Nothing in your library credits this person.")
            p == null -> Loading()
            else -> {
                val songs = remember(p) { p.songs }
                val all = songs.map { it.first }
                val queueName = "Credits: ${p.name}"
                val sections = remember(songs) {
                    songs.groupBy { it.second.joinToString(", ") }.entries.sortedByDescending { it.value.size }
                }
                LazyColumn(Modifier.fillMaxSize()) {
                    item {
                        Column(Modifier.padding(horizontal = 16.dp)) {
                            if (p.roles.isNotEmpty()) Text(p.roles.joinToString(" · "), color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Text("Credited on ${p.trackCount} song${if (p.trackCount == 1) "" else "s"} in your library",
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Row(Modifier.padding(top = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Button({ if (all.isNotEmpty()) c.player.play(all, 0, PlaySource.LIBRARY, queueName) }) {
                                    Icon(Icons.Default.PlayArrow, null); Text("Play")
                                }
                                OutlinedButton({ if (all.isNotEmpty()) c.player.play(all.shuffled(), 0, PlaySource.LIBRARY, queueName) }) {
                                    Icon(Icons.Default.Shuffle, null); Text("Shuffle")
                                }
                            }
                            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 4.dp)) {
                                p.artistId?.let { aid -> AssistChip({ ui.go("artist/$aid") }, { Text("Artist page") }) }
                                p.musicbrainzUrl?.let { url ->
                                    AssistChip({ runCatching { uriHandler.openUri(url) } }, { Text("MusicBrainz") },
                                        trailingIcon = { Icon(Icons.AutoMirrored.Filled.OpenInNew, null, Modifier.size(16.dp)) })
                                }
                            }
                        }
                    }
                    sections.forEach { (role, list) ->
                        if (sections.size > 1) item(key = "r:$role") {
                            Text(role.uppercase(), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 2.dp))
                        }
                        itemsIndexed(list, key = { i, s -> "$role:$i:${s.first.id}" }) { _, s ->
                            val t = s.first
                            SongRow(c, t, onClick = { c.player.play(all, all.indexOf(t).coerceAtLeast(0), PlaySource.LIBRARY, queueName) })
                        }
                    }
                }
            }
        }
    }
}
