package org.nasmusic.app.ui

import android.content.ClipData
import android.content.ClipboardManager
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.automirrored.filled.PlaylistAdd
import androidx.compose.material.icons.automirrored.filled.QueueMusic
import androidx.compose.material.icons.filled.Album
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Lyrics
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.QuestionMark
import androidx.compose.material.icons.filled.Repeat
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.Speed
import androidx.compose.material.icons.filled.ThumbDown
import androidx.compose.material.icons.filled.ThumbUp
import androidx.compose.material.icons.filled.Cast
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.Watch
import androidx.compose.material.icons.filled.Speaker
import androidx.compose.material.icons.automirrored.filled.PlaylistPlay
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.ApiUrls
import org.nasmusic.app.data.api.NameBody
import org.nasmusic.app.data.api.Playlist
import org.nasmusic.app.data.api.TrackIdsBody
import org.nasmusic.app.data.api.TrackInfo
import org.nasmusic.app.feedback.EventType
import org.nasmusic.app.playback.PlaySource
import retrofit2.HttpException

/** All app-wide sheets and dialogs, driven by [UiController]. */
@Composable
fun GlobalSheets(c: AppContainer) {
    val ui = LocalUi.current
    ui.songMenu?.let { SongMenuSheet(c, it) { ui.songMenu = null } }
    ui.songInfo?.let { SongInfoSheet(c, it) { ui.songInfo = null } }
    ui.addToPlaylist?.let { AddToPlaylistDialog(c, it.map { t -> t.id }) { ui.addToPlaylist = null } }
    ui.addToQueue?.let { AddToQueueDialog(c, it.map { t -> t.id }) { ui.addToQueue = null } }
    ui.tagEdit?.let { TagEditDialog(c, it) { ui.tagEdit = null; ui.tagsRev++ } }
    if (ui.devicesOpen) DevicesSheet(c) { ui.devicesOpen = false }
    if (ui.vibeOpen) org.nasmusic.app.ui.screens.VibeDialog(c) { ui.vibeOpen = false }
    if (ui.speedOpen) SpeedPitchDialog(c) { ui.speedOpen = false }
    if (ui.abOpen) AbRepeatDialog(c) { ui.abOpen = false }
    if (ui.queueSheet) QueueSheet(c) { ui.queueSheet = false }
    ui.bugReport?.let { afterCrash -> BugReportDialog(c, afterCrash) { ui.bugReport = null } }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SongMenuSheet(c: AppContainer, target: SongMenuTarget, onDismiss: () -> Unit) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val t = target.track
    val now by c.player.state.collectAsStateWithLifecycle()
    val isCurrent = target.isCurrent || now.item?.mediaId == t.id
    fun close(then: () -> Unit) { onDismiss(); then() }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.verticalScroll(rememberScrollState()).navigationBarsPadding().padding(bottom = 8.dp)) {
            Text(t.title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(horizontal = 24.dp))
            Text("${t.artist} · ${t.album}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 24.dp, vertical = 4.dp))
            if (isCurrent) {
                MenuItem(Icons.Default.Lyrics, "Show lyrics") { close { ui.lyricsRequested = true; ui.go("now") } }
                MenuItem(Icons.Default.Repeat, "A-B repeat") { close { ui.abOpen = true } }
                MenuItem(Icons.Default.Speed, "Play speed and pitch") { close { ui.speedOpen = true } }
                HorizontalDivider(Modifier.padding(horizontal = 24.dp, vertical = 4.dp))
            }
            target.queueIndex?.let { idx ->
                MenuItem(Icons.Default.Close, "Remove from queue") { close { c.player.removeItem(idx) } }
            }
            MenuItem(Icons.AutoMirrored.Filled.PlaylistPlay, "Play next") { close { c.player.playNext(listOf(t)); ui.toast = "Playing next" } }
            MenuItem(Icons.AutoMirrored.Filled.QueueMusic, "Add to a queue") { close { ui.addToQueue = listOf(t) } }
            MenuItem(Icons.AutoMirrored.Filled.PlaylistAdd, "Add to playlists") { close { ui.addToPlaylist = listOf(t) } }
            HorizontalDivider(Modifier.padding(horizontal = 24.dp, vertical = 4.dp))
            if (t.albumId.isNotBlank()) MenuItem(Icons.Default.Album, "Go to album") { close { ui.go("album/${t.albumId}") } }
            if (t.artistId.isNotBlank()) MenuItem(Icons.Default.Person, "Go to artist") { close { ui.go("artist/${t.artistId}") } }
            MenuItem(Icons.Default.AutoAwesome, "More like this") {
                close {
                    scope.launch {
                        runCatching { c.api.moreLike(t.id) }
                            .onSuccess { mix -> c.player.play(mix.tracks.map { it.track }, 0, PlaySource.MORE_LIKE, "More like: ${t.title}", mix.id, mix.tracks.map { it.reason }) }
                            .onFailure { e -> ui.toast = ApiUrls.moreLikeError((e as? HttpException)?.code()) }
                    }
                }
            }
            MenuItem(Icons.Default.Info, "Song info") { close { ui.songInfo = t.id } }
            if (c.secureStore.config.value?.isAdmin == true) {
                MenuItem(Icons.Default.Edit, "Edit tags") { close { ui.tagEdit = TagEditTarget(listOf(t.id), t.title) } }
            }
            if (isCurrent) {
                MenuItem(Icons.Default.QuestionMark, "Why this song?") {
                    close {
                        scope.launch {
                            ui.toast = runCatching { c.api.why(t.id).reason }.getOrDefault("Couldn't reach the server.")
                        }
                    }
                }
                MenuItem(Icons.Default.ThumbUp, "Thumbs up") { close { recordThumb(c, now, EventType.THUMB_UP); ui.toast = "Noted: more like this" } }
                MenuItem(Icons.Default.ThumbDown, "Thumbs down") { close { recordThumb(c, now, EventType.THUMB_DOWN); ui.toast = "Noted: less like this" } }
            }
            MenuItem(Icons.Default.Share, "Share") {
                close {
                    val cfg = c.secureStore.config.value
                    val base = cfg?.tunnelUrl ?: cfg?.lanUrl.orEmpty()
                    val link = "$base/track/${t.id}"
                    c.context.getSystemService(ClipboardManager::class.java)?.setPrimaryClip(ClipData.newPlainText("nas-music link", link))
                    ui.toast = "Link copied"
                }
            }
        }
    }
}

private fun recordThumb(c: AppContainer, now: org.nasmusic.app.playback.NowPlaying, type: EventType) {
    val item = now.item ?: return
    val x = item.mediaMetadata.extras
    c.events.record(
        type, item.mediaId, positionS = now.positionMs / 1000.0, durationS = (now.durationMs / 1000.0).takeIf { it > 0 },
        source = x?.getString(org.nasmusic.app.playback.MediaItems.EXTRA_SOURCE), mixId = x?.getString(org.nasmusic.app.playback.MediaItems.EXTRA_MIX_ID),
        context = c.contextCollector.snapshot(),
    )
}

@Composable
private fun MenuItem(icon: ImageVector, label: String, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 24.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(24.dp))
        Text(label, style = MaterialTheme.typography.bodyLarge)
    }
}

@Composable
private fun SongInfoSheet(c: AppContainer, trackId: String, onDismiss: () -> Unit) {
    val ui = LocalUi.current
    var info by remember { mutableStateOf<TrackInfo?>(null) }
    var failed by remember { mutableStateOf(false) }
    LaunchedEffect(trackId) { info = runCatching { c.api.trackInfo(trackId) }.onFailure { failed = true }.getOrNull() }
    AlertDialog(
        onDismissRequest = onDismiss,
        icon = { Icon(Icons.Default.Info, null) },
        title = { Text("Song info") },
        text = {
            val i = info
            when {
                failed -> Text("Couldn't load song info.")
                i == null -> Loading()
                else -> Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        AlbumArt(i.albumId, 72.dp)
                        Field("File name", TrackFormat.orUnknown(i.fileName))
                    }
                    Field("Location", TrackFormat.location(i.folder))
                    HorizontalDivider()
                    Field("Title", TrackFormat.orUnknown(i.title))
                    Field("Album", TrackFormat.orUnknown(i.album), link = i.albumId.takeIf { it.isNotBlank() }?.let { { onDismiss(); ui.go("album/$it") } })
                    Field("Artist", TrackFormat.orUnknown(i.artist), link = i.artistId.takeIf { it.isNotBlank() }?.let { { onDismiss(); ui.go("artist/$it") } })
                    Field("Album-Artist", TrackFormat.orUnknown(i.albumArtist))
                    Field("Composer", TrackFormat.orUnknown(i.composer))
                    Field("Genre", TrackFormat.orUnknown(i.genre), link = i.genre?.takeIf { it.isNotBlank() }?.let { g -> { onDismiss(); ui.go("genre/${android.net.Uri.encode(g)}") } })
                    Field("Lyricist", TrackFormat.orUnknown(i.lyricist))
                    Field("Track number", TrackFormat.orUnknown(i.trackNo))
                    Field("Disc number", TrackFormat.orUnknown(i.discNo))
                    Field("Year", TrackFormat.orUnknown(i.year))
                    Field("Comment", TrackFormat.orUnknown(i.comment))
                    HorizontalDivider()
                    Field("Duration", formatDuration(i.durationS))
                    TrackFormat.line(i).takeIf { it.isNotBlank() }?.let { Field("Format", it) }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("DONE") } },
    )
}

@Composable
private fun Field(label: String, value: String, link: (() -> Unit)? = null) {
    Column(Modifier.fillMaxWidth().then(if (link != null) Modifier.clickable(onClick = link) else Modifier)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(label, fontWeight = FontWeight.Bold)
            if (link != null) Icon(Icons.AutoMirrored.Filled.OpenInNew, "Open", tint = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(start = 8.dp).size(16.dp))
        }
        Text(value, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun AddToPlaylistDialog(c: AppContainer, trackIds: List<String>, onDismiss: () -> Unit) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var lists by remember { mutableStateOf<List<Playlist>?>(null) }
    var newName by remember { mutableStateOf("") }
    LaunchedEffect(Unit) { lists = runCatching { c.api.playlists().filter { it.role != "viewer" } }.getOrDefault(emptyList()) }
    fun add(id: String, name: String) = scope.launch {
        runCatching { c.api.addToPlaylist(id, TrackIdsBody(trackIds)) }
            .onSuccess { ui.toast = "Added to $name" }.onFailure { ui.toast = "Couldn't add to $name" }
        onDismiss()
    }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Add to playlist") },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState())) {
                lists?.forEach { p ->
                    Text(p.name, Modifier.fillMaxWidth().clickable { add(p.id, p.name) }.padding(vertical = 12.dp))
                } ?: Loading()
                OutlinedTextField(newName, { newName = it }, label = { Text("New playlist") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            }
        },
        confirmButton = {
            TextButton(enabled = newName.isNotBlank(), onClick = {
                scope.launch {
                    runCatching { c.api.createPlaylist(NameBody(newName.trim())) }.onSuccess { add(it.id, it.name) }
                        .onFailure { ui.toast = "Couldn't create playlist"; onDismiss() }
                }
            }) { Text("Create & add") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun AddToQueueDialog(c: AppContainer, trackIds: List<String>, onDismiss: () -> Unit) {
    val ui = LocalUi.current
    val queues by c.queueSync.queues.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { c.queueSync.refresh() }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Add to a queue") },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState())) {
                if (queues.isEmpty()) Text("No queues yet. Play something to create one.")
                queues.forEach { q ->
                    Text(c.queueSync.label(q), Modifier.fillMaxWidth().clickable {
                        c.queueSync.addTo(q.id, trackIds)
                        ui.toast = "Added to ${q.name}"
                        onDismiss()
                    }.padding(vertical = 12.dp))
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )
}

@Composable
private fun SpeedPitchDialog(c: AppContainer, onDismiss: () -> Unit) {
    val s by c.player.state.collectAsStateWithLifecycle()
    var speed by remember { mutableFloatStateOf(s.speed) }
    var pitch by remember { mutableFloatStateOf(s.pitch) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Play speed and pitch") },
        text = {
            Column {
                Text("Speed: %.2fx".format(speed))
                Slider(speed, { speed = (it * 20).toInt() / 20f; c.player.setSpeedPitch(speed, pitch) }, valueRange = 0.5f..2f)
                Text("Pitch: %.2fx".format(pitch))
                Slider(pitch, { pitch = (it * 20).toInt() / 20f; c.player.setSpeedPitch(speed, pitch) }, valueRange = 0.5f..2f)
                if (s.remote != null) Text("Pitch only applies when playing on this phone.", style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Done") } },
        dismissButton = { TextButton(onClick = { speed = 1f; pitch = 1f; c.player.setSpeedPitch(1f, 1f) }) { Text("Reset") } },
    )
}

@Composable
private fun AbRepeatDialog(c: AppContainer, onDismiss: () -> Unit) {
    val s by c.player.state.collectAsStateWithLifecycle()
    val loop by c.abRepeat.loop.collectAsStateWithLifecycle()
    val id = s.item?.mediaId
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("A-B repeat") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("A: ${loop?.takeIf { it.trackId == id }?.aMs?.let { formatDuration(it / 1000.0) } ?: "—"}    B: ${loop?.takeIf { it.trackId == id }?.bMs?.let { formatDuration(it / 1000.0) } ?: "—"}")
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(enabled = id != null, onClick = { id?.let { c.abRepeat.setA(it, s.positionMs) } }) { Text("Set A here") }
                    OutlinedButton(enabled = id != null && loop?.trackId == id, onClick = { id?.let { c.abRepeat.setB(it, s.positionMs) } }) { Text("Set B here") }
                }
                if (s.remote != null) Text("A-B repeat works when playing on this phone.", style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Done") } },
        dismissButton = { TextButton(onClick = { c.abRepeat.clear() }) { Text("Clear") } },
    )
}

internal fun castLabel(castType: String?): String = when (castType) {
    "chromecast" -> "Google Cast"
    "dlna" -> "DLNA"
    "homeassistant" -> "Home Assistant"
    "snapcast" -> "Snapcast"
    else -> "Speaker"
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DevicesSheet(c: AppContainer, onDismiss: () -> Unit) {
    val devices by c.connect.devices.collectAsStateWithLifecycle()
    val state by c.connect.state.collectAsStateWithLifecycle()
    val me by c.connect.myDeviceId.collectAsStateWithLifecycle()
    val connected by c.connect.connected.collectAsStateWithLifecycle()
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(Modifier.navigationBarsPadding().padding(bottom = 16.dp)) {
            Text("Devices", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(horizontal = 24.dp, vertical = 8.dp))
            if (!connected) Text("Not connected to the server. Devices appear when you're online.", modifier = Modifier.padding(horizontal = 24.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
            // This phone first, then your other devices, then speakers the server can cast to.
            devices.filter { it.online }.sortedWith(compareBy({ it.id != me }, { it.isSpeaker })).forEach { d ->
                val active = state?.activeDeviceId == d.id
                Row(
                    Modifier.fillMaxWidth().clickable(enabled = d.canPlay && !active) { c.connect.transfer(d.id, true); onDismiss() }
                        .padding(horizontal = 24.dp, vertical = 14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(
                        when {
                            d.castType == "chromecast" -> Icons.Default.Cast
                            d.isSpeaker -> Icons.Default.Speaker
                            else -> when (d.kind) { "android" -> Icons.Default.PhoneAndroid; "desktop" -> Icons.Default.Computer; "web" -> Icons.Default.Language; "watch" -> Icons.Default.Watch; else -> Icons.Default.Speaker }
                        },
                        null, tint = if (active) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
                    )
                    Spacer(Modifier.width(20.dp))
                    Column(Modifier.weight(1f)) {
                        Text(d.name + if (d.id == me) " (this phone)" else "", color = if (active) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
                        val status = when {
                            active && state?.playing == true -> "Playing"
                            active -> "Active"
                            !d.canPlay -> "Remote only"
                            d.isSpeaker -> "Tap to play on this speaker"
                            else -> "Tap to play here"
                        }
                        Text(
                            if (d.isSpeaker) "$status · ${castLabel(d.castType)}" else status,
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
            // Google Cast straight from the phone (full build only; the FOSS build has no Google code).
            org.nasmusic.app.cast.NativeCast.DevicesRow(c)
            if (devices.none { it.online }) Row(Modifier.padding(24.dp)) { Icon(Icons.Default.Devices, null); Text("  Sign in on another device to control it from here.") }
        }
    }
}

/**
 * The queue popup from Now Playing: a bottom sheet in portrait; in landscape or on wide screens a
 * full-height panel on the right, so it never squeezes or covers the player controls awkwardly.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun QueueSheet(c: AppContainer, onDismiss: () -> Unit) {
    val ui = LocalUi.current
    val conf = androidx.compose.ui.platform.LocalConfiguration.current
    val openFull = { onDismiss(); ui.go("queue") }
    if (QueueSheetStyle.of(conf.screenWidthDp, conf.screenHeightDp) == QueueSheetStyle.SIDE) {
        androidx.compose.ui.window.Dialog(
            onDismissRequest = onDismiss,
            properties = androidx.compose.ui.window.DialogProperties(usePlatformDefaultWidth = false),
        ) {
            androidx.compose.foundation.layout.Box(Modifier.fillMaxSize().clickable(onClick = onDismiss)) {
                androidx.compose.material3.Surface(
                    modifier = Modifier.align(Alignment.CenterEnd).fillMaxHeight()
                        .width(minOf(460, (conf.screenWidthDp * 0.55f).toInt()).dp)
                        // Swallow taps so they don't reach the dismiss area behind the panel.
                        .pointerInput(Unit) { detectTapGestures { } },
                    color = MaterialTheme.colorScheme.surfaceContainer,
                ) {
                    Column(Modifier.windowInsetsPadding(WindowInsets.safeDrawing)) { org.nasmusic.app.ui.screens.QueuePanel(c, onOpenFull = openFull) }
                }
            }
        }
    } else {
        ModalBottomSheet(onDismissRequest = onDismiss) {
            Column(Modifier.fillMaxHeight(0.85f)) { org.nasmusic.app.ui.screens.QueuePanel(c, onOpenFull = openFull) }
        }
    }
}

enum class QueueSheetStyle {
    BOTTOM, SIDE;

    companion object {
        /** Side panel when wider than tall (phone landscape) or on tablet widths. */
        fun of(widthDp: Int, heightDp: Int): QueueSheetStyle = if (widthDp > heightDp || widthDp >= 600) SIDE else BOTTOM
    }
}
