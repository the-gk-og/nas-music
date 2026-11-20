package org.nasmusic.wear.ui.screens

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.DownloadDone
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Radio
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material.icons.automirrored.filled.PlaylistPlay
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import kotlinx.coroutines.launch
import org.nasmusic.app.data.api.Album
import org.nasmusic.app.data.api.Playlist
import org.nasmusic.app.data.api.RadioRequest
import org.nasmusic.app.data.api.Shelf
import org.nasmusic.app.data.api.ShelfItem
import org.nasmusic.app.data.api.Track
import org.nasmusic.wear.WearContainer
import org.nasmusic.wear.offline.OfflineSyncWorker
import org.nasmusic.wear.ui.ListScreen
import org.nasmusic.wear.ui.NavChip
import org.nasmusic.wear.ui.header
import org.nasmusic.wear.ui.loading
import org.nasmusic.wear.ui.message

/** Lists the watch can open as a track list (and keep offline). */
object ListKinds {
    const val PLAYLIST = "playlist"
    const val ALBUM = "album"
    const val FAVOURITES = "favourites"
    const val MIX = "mix"
    const val QUEUE = "queue"
    /** api 0.7: an artist's top songs (by your plays) */
    const val ARTIST = "artist"
    /** api 0.7: a smart playlist (evaluated on the server) */
    const val SMART = "smart_playlist"
    val OFFLINE_CAPABLE = setOf(PLAYLIST, ALBUM, FAVOURITES, SMART)
}

/** Loads a list from the server, falling back to its offline snapshot. */
suspend fun loadList(c: WearContainer, kind: String, id: String): Pair<String, List<Track>>? {
    val remote = runCatching {
        when (kind) {
            ListKinds.PLAYLIST -> c.api.playlist(id).let { it.name to it.tracks }
            ListKinds.ALBUM -> c.api.album(id).let { it.title to it.tracks }
            ListKinds.FAVOURITES -> "Favourites" to c.api.favourites()
            ListKinds.MIX -> c.api.mix(id).let { m -> m.title to m.tracks.map { it.track } }
            ListKinds.QUEUE -> c.api.queue(id).let { it.name to it.tracks }
            ListKinds.ARTIST -> c.api.artistPage(id).let { "${it.name}: top songs" to it.topTracks }
            ListKinds.SMART -> c.api.smartPlaylist(id).let { it.name to it.tracks }
            else -> null
        }
    }.getOrNull()
    if (remote != null) return remote
    return c.offline.lists.value.firstOrNull { it.kind == kind && it.id == id }?.let { it.name to it.tracks }
}

/** The server's Home feed (docs/v3-ux.md §3.1), flattened to what a watch can open. */
@Composable
fun FeedScreen(c: WearContainer, open: (kind: String, id: String) -> Unit, nowPlaying: () -> Unit) {
    var shelves by remember { mutableStateOf<List<Shelf>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) {
        runCatching { c.feeds.home() }.onSuccess { shelves = it.shelves }.onFailure { error = "Can't reach the server"; shelves = emptyList() }
    }
    ListScreen {
        header("Home")
        val list = shelves
        if (list == null) { loading(); return@ListScreen }
        error?.let { message(it) }
        for (shelf in list) {
            val items = shelf.items.filter { it.type in OPENABLE }
            if (items.isEmpty()) continue
            header(shelf.title)
            items.take(8).forEach { it ->
                item {
                    NavChip(
                        label = it.title,
                        secondary = it.subtitle,
                        artAlbumId = artAlbum(it),
                        icon = if (it.type == "radio") Icons.Filled.Radio else Icons.AutoMirrored.Filled.PlaylistPlay,
                    ) {
                        when (it.type) {
                            "album" -> open(ListKinds.ALBUM, it.id)
                            "playlist" -> open(ListKinds.PLAYLIST, it.id)
                            "mix" -> open(ListKinds.MIX, it.id)
                            "queue" -> open(ListKinds.QUEUE, it.id)
                            "artist" -> open(ListKinds.ARTIST, it.id)
                            "smart_playlist" -> open(ListKinds.SMART, it.id)
                            "track" -> it.track?.let { t -> c.player.playTracks(listOf(t), 0, t.title); nowPlaying() }
                            "radio" -> scope.launch {
                                val seed = it.seed ?: return@launch
                                // AI radio: the smart shuffle ("Play something for now") is told it's on a watch.
                                val hints = if (seed.type == "moment") java.time.LocalDateTime.now().let { now ->
                                    org.nasmusic.app.data.api.RadioContext(now.hour, now.dayOfWeek.value, "watch")
                                } else null
                                runCatching { c.api.startRadio(RadioRequest(seed, hints)) }.onSuccess { q ->
                                    c.player.playTracks(q.tracks, 0, q.name); nowPlaying()
                                }.onFailure { c.ui.toast("Couldn't start radio") }
                            }
                        }
                    }
                }
            }
        }
        if (list.isEmpty() && error == null) message("Nothing here yet.")
    }
}

private val OPENABLE = setOf("album", "playlist", "mix", "queue", "track", "radio", "artist", "smart_playlist")

private fun artAlbum(i: ShelfItem): String? =
    i.album?.id ?: i.track?.albumId ?: i.artUrl?.takeIf { it.startsWith("art/") && !it.startsWith("art/artist/") }?.removePrefix("art/")?.substringBefore('?')

@Composable
fun PlaylistsScreen(c: WearContainer, open: (kind: String, id: String) -> Unit) {
    var playlists by remember { mutableStateOf<List<Playlist>?>(null) }
    LaunchedEffect(Unit) { playlists = runCatching { c.api.playlists() }.getOrNull() ?: emptyList() }
    ListScreen {
        header("Playlists")
        item { NavChip("Favourites", icon = Icons.Filled.Favorite) { open(ListKinds.FAVOURITES, "favourites") } }
        val list = playlists
        if (list == null) loading()
        else if (list.isEmpty()) message("No playlists (or offline). Downloaded lists are under Downloads.")
        else list.forEach { p ->
            item {
                NavChip(p.name, "${p.trackCount} songs", icon = Icons.AutoMirrored.Filled.PlaylistPlay) { open(ListKinds.PLAYLIST, p.id) }
            }
        }
    }
}

@Composable
fun AlbumsScreen(c: WearContainer, open: (kind: String, id: String) -> Unit) {
    var albums by remember { mutableStateOf<List<Album>?>(null) }
    LaunchedEffect(Unit) {
        // Newest additions first; a watch is no place to scroll through the whole library.
        albums = runCatching { c.api.albums(sort = "added", limit = 100) }.getOrNull()
            ?: runCatching { c.api.albums(limit = 100) }.getOrNull() ?: emptyList()
    }
    ListScreen {
        header("Albums")
        val list = albums
        if (list == null) loading()
        else if (list.isEmpty()) message("No albums (or offline).")
        else list.forEach { a -> item { NavChip(if (a.explicit) "🅴 ${a.title}" else a.title, a.artist, artAlbumId = a.id) { open(ListKinds.ALBUM, a.id) } } }
    }
}

/** A playlist / album / mix / queue: play, shuffle, keep offline, or tap a song to start there. */
@Composable
fun TrackListScreen(c: WearContainer, kind: String, id: String, nowPlaying: () -> Unit) {
    var data by remember { mutableStateOf<Pair<String, List<Track>>?>(null) }
    var failed by remember { mutableStateOf(false) }
    val offlineLists by c.offline.lists.collectAsState()
    val offline = offlineLists.firstOrNull { it.kind == kind && it.id == id }
    LaunchedEffect(kind, id) { data = loadList(c, kind, id); failed = data == null }
    ListScreen {
        val d = data
        if (d == null) {
            if (failed) message("Couldn't load this list.") else loading()
            return@ListScreen
        }
        val (name, tracks) = d
        header(name)
        item {
            NavChip("Play", "${tracks.size} songs", icon = Icons.Filled.PlayArrow, primary = true, enabled = tracks.isNotEmpty()) {
                c.player.playTracks(tracks, 0, name); nowPlaying()
            }
        }
        item {
            NavChip("Shuffle", icon = Icons.Filled.Shuffle, enabled = tracks.isNotEmpty()) {
                c.player.playTracks(tracks.shuffled(), 0, name); nowPlaying()
            }
        }
        if (kind in ListKinds.OFFLINE_CAPABLE) item {
            if (offline == null) {
                NavChip("Download to watch", icon = Icons.Filled.CloudDownload) {
                    c.offline.add(kind, id, name, tracks)
                    OfflineSyncWorker.runNow(c.context)
                    OfflineSyncWorker.schedule(c.context)
                    c.ui.toast(if (c.settings.current.downloadWifiOnly) "Downloading on Wi-Fi" else "Downloading")
                }
            } else {
                NavChip(
                    "On watch",
                    "${c.offline.downloadedCount(offline)}/${offline.tracks.size} downloaded · tap to remove",
                    icon = Icons.Filled.DownloadDone,
                ) {
                    c.offline.remove(kind, id)
                    OfflineSyncWorker.runNow(c.context)
                }
            }
        }
        tracks.forEachIndexed { i, t ->
            item {
                NavChip(if (t.isExplicit) "🅴 ${t.title}" else t.title, t.artist, icon = if (c.offline.localFile(t.id) != null) Icons.Filled.DownloadDone else null) {
                    c.player.playTracks(tracks, i, name); nowPlaying()
                }
            }
        }
    }
}

