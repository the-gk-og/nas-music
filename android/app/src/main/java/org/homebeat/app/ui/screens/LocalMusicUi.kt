package org.homebeat.app.ui.screens

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Sort
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import org.homebeat.app.AppContainer
import org.homebeat.app.data.api.Track
import org.homebeat.app.local.LocalIds
import org.homebeat.app.local.LocalLibrary
import org.homebeat.app.playback.PlaySource
import org.homebeat.app.ui.AlbumArt
import org.homebeat.app.ui.LocalUi
import org.homebeat.app.ui.Loading
import org.homebeat.app.ui.Message
import org.homebeat.app.ui.ScreenTitle
import org.homebeat.app.ui.SongList
import org.homebeat.app.ui.songItems

/** Screens for music stored on the phone (see [LocalLibrary]). */
object LocalRoutes {
    const val ALBUM = "local-album/{id}"
    const val ARTIST = "local-artist/{id}"
    fun album(id: String) = "local-album/${Uri.encode(id)}"
    fun artist(id: String) = "local-artist/${Uri.encode(id)}"
}

/** True while "Music on this phone" is on and the permission is granted; re-checked when the app resumes. */
@Composable
fun rememberLocalMusicReady(c: AppContainer): Boolean {
    val on by c.localMusic.enabled.collectAsStateWithLifecycle()
    var granted by remember { mutableStateOf(c.localMusic.hasPermission()) }
    val owner = LocalLifecycleOwner.current
    DisposableEffect(owner) {
        val o = LifecycleEventObserver { _, e ->
            if (e == Lifecycle.Event.ON_RESUME) {
                val now = c.localMusic.hasPermission()
                if (now && !granted) c.localMusic.rescan()
                granted = now
            }
        }
        owner.lifecycle.addObserver(o)
        onDispose { owner.lifecycle.removeObserver(o) }
    }
    return on && granted
}

/** Settings → Music on this phone: the switch, the permission, and an optional folder choice. */
@Composable
fun LocalMusicSettings(c: AppContainer) {
    val lib = c.localMusic
    val on by lib.enabled.collectAsStateWithLifecycle()
    val tracks by lib.tracks.collectAsStateWithLifecycle()
    val chosen by lib.folders.collectAsStateWithLifecycle()
    val scanning by lib.scanning.collectAsStateWithLifecycle()
    var granted by remember { mutableStateOf(lib.hasPermission()) }
    var picking by remember { mutableStateOf(false) }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        granted = ok
        if (ok) lib.rescan()
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("Show music stored on this phone")
            Text(
                "Adds the songs already on your phone to Library and Search, next to your server's music. They play " +
                    "straight from the phone (also offline) and stay private: nothing about them is sent to the server.",
                style = MaterialTheme.typography.bodySmall,
            )
        }
        Switch(on, { v ->
            lib.setEnabled(v)
            if (v && !lib.hasPermission()) ask.launch(LocalLibrary.PERMISSION)
        })
    }
    if (!on) return
    when {
        !granted -> OutlinedButton(onClick = { ask.launch(LocalLibrary.PERMISSION) }) { Text("Allow access to music files") }
        else -> {
            Text(
                when {
                    scanning && tracks.isEmpty() -> "Looking for music…"
                    else -> "${tracks.size} songs " + if (chosen.isEmpty()) "from every folder" else "from ${chosen.size} chosen folder" + (if (chosen.size == 1) "" else "s")
                },
                style = MaterialTheme.typography.bodySmall,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { picking = true }) { Text("Choose folders") }
                TextButton(onClick = { lib.rescan() }) { Text("Rescan") }
            }
        }
    }
    if (picking) FolderPicker(c) { picking = false }
}

@Composable
private fun FolderPicker(c: AppContainer, onDismiss: () -> Unit) {
    val all by c.localMusic.allFolders.collectAsStateWithLifecycle()
    val saved by c.localMusic.folders.collectAsStateWithLifecycle()
    var sel by remember { mutableStateOf(saved) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Folders to include") },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState())) {
                Text("None ticked = every folder with music.", style = MaterialTheme.typography.bodySmall)
                if (all.isEmpty()) Text("No music found on this phone yet.", modifier = Modifier.padding(top = 12.dp))
                all.forEach { f ->
                    val ticked = f.path in sel
                    Row(Modifier.fillMaxWidth().clickable { sel = if (ticked) sel - f.path else sel + f.path }, verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(ticked, { sel = if (ticked) sel - f.path else sel + f.path })
                        Column(Modifier.weight(1f)) {
                            Text(f.path.ifEmpty { "(top of storage)" }, maxLines = 2, overflow = TextOverflow.Ellipsis)
                            Text("${f.trackCount} songs", style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { c.localMusic.setFolders(sel); onDismiss() }) { Text("Save") } },
        dismissButton = {
            Row {
                TextButton(onClick = { sel = emptySet() }) { Text("All folders") }
                TextButton(onClick = onDismiss) { Text("Cancel") }
            }
        },
    )
}

private val LOCAL_SORTS = listOf("title" to "Title", "artist" to "Artist", "album" to "Album", "added" to "Recently added")

/** Library → "On this phone": songs, albums and artists stored on the phone. */
@Composable
fun LocalMusicSection(c: AppContainer) {
    val ui = LocalUi.current
    val ready = rememberLocalMusicReady(c)
    val tracks by c.localMusic.tracks.collectAsStateWithLifecycle()
    val scanning by c.localMusic.scanning.collectAsStateWithLifecycle()
    var tab by rememberSaveable { mutableStateOf("songs") }
    var sort by rememberSaveable { mutableStateOf("title") }
    var sortMenu by remember { mutableStateOf(false) }
    if (!ready) {
        Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Turn on \u201cShow music stored on this phone\u201d in Settings and allow access to music files.")
            OutlinedButton(onClick = { ui.go("settings") }) { Text("Open Settings") }
        }
        return
    }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Row(Modifier.weight(1f).horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("songs" to "Songs", "albums" to "Albums", "artists" to "Artists").forEach { (k, label) ->
                    FilterChip(tab == k, { tab = k }, label = { Text(label) })
                }
            }
            if (tab == "songs") Box {
                IconButton(onClick = { sortMenu = true }) { Icon(Icons.AutoMirrored.Filled.Sort, "Sort") }
                DropdownMenu(sortMenu, { sortMenu = false }) {
                    LOCAL_SORTS.forEach { (k, label) -> DropdownMenuItem({ Text(if (k == sort) "✓ $label" else label) }, { sort = k; sortMenu = false }) }
                }
            }
        }
        if (tracks.isEmpty()) {
            if (scanning) Loading() else Message("No music found on this phone. You can choose folders in Settings.")
            return@Column
        }
        when (tab) {
            "albums" -> {
                val albums = remember(tracks) { LocalIds.albums(tracks) }
                LazyVerticalGrid(
                    GridCells.Adaptive(140.dp), Modifier.fillMaxSize(), contentPadding = PaddingValues(12.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp), verticalArrangement = Arrangement.spacedBy(16.dp),
                ) {
                    items(albums, key = { it.id }) { a ->
                        Column(Modifier.clickable { ui.go(LocalRoutes.album(a.id)) }) {
                            AlbumArt(a.id, 150.dp)
                            Text(a.title, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 6.dp))
                            Text(a.artist, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
            "artists" -> {
                val artists = remember(tracks) { LocalIds.artists(tracks) }
                LazyColumn(Modifier.fillMaxSize()) {
                    items(artists, key = { it.id }) { ar ->
                        Row(Modifier.fillMaxWidth().clickable { ui.go(LocalRoutes.artist(ar.id)) }.padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Default.Person, null)
                            Spacer(Modifier.width(14.dp))
                            Column {
                                Text(ar.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text("${ar.trackCount} songs · ${ar.albumCount} albums", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                }
            }
            else -> {
                val sorted = remember(tracks, sort) { LocalIds.sort(tracks, sort) }
                LazyColumn(Modifier.fillMaxSize()) {
                    item(key = "actions") { PlayShuffleRow(c, sorted, "On this phone") }
                    songItems(c, sorted, "On this phone", PlaySource.LIBRARY)
                }
            }
        }
    }
}

@Composable
private fun PlayShuffleRow(c: AppContainer, tracks: List<Track>, queueName: String) {
    Row(Modifier.padding(horizontal = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        TextButton(onClick = { c.player.play(tracks, 0, PlaySource.LIBRARY, queueName) }) { Icon(Icons.Default.PlayArrow, null); Text(" Play") }
        TextButton(onClick = { c.player.play(tracks.shuffled(), 0, PlaySource.LIBRARY, queueName) }) { Icon(Icons.Default.Shuffle, null); Text(" Shuffle") }
        Spacer(Modifier.weight(1f))
        Icon(Icons.Default.PhoneAndroid, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(" ${tracks.size} on this phone", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
fun LocalAlbumScreen(c: AppContainer, id: String) {
    val tracks by c.localMusic.tracks.collectAsStateWithLifecycle()
    val album = remember(tracks, id) { LocalIds.albums(tracks.filter { it.albumId == id }).firstOrNull() }
    Column(Modifier.fillMaxSize()) {
        if (album == null) { ScreenTitle("Album"); Message("This album isn't on the phone any more."); return@Column }
        LazyColumn(Modifier.fillMaxSize()) {
            item(key = "head") {
                Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                    AlbumArt(album.id, 120.dp)
                    Column(Modifier.padding(start = 16.dp)) {
                        Text(album.title, style = MaterialTheme.typography.titleLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        Text(listOfNotNull(album.artist, album.year?.toString()).joinToString(" · "), color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text("Stored on this phone", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                PlayShuffleRow(c, album.tracks, "Album: ${album.title}")
            }
            songItems(c, album.tracks, "Album: ${album.title}", PlaySource.LIBRARY)
        }
    }
}

@Composable
fun LocalArtistScreen(c: AppContainer, id: String) {
    val tracks by c.localMusic.tracks.collectAsStateWithLifecycle()
    val mine = remember(tracks, id) { LocalIds.sort(tracks.filter { it.artistId == id }, "album") }
    val name = mine.firstOrNull()?.artist ?: "Artist"
    Column(Modifier.fillMaxSize()) {
        ScreenTitle(name)
        SongList(c, mine, "Artist: $name", PlaySource.LIBRARY, empty = "No songs by this artist on the phone.")
    }
}
