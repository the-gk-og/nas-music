package org.nasmusic.app.ui.screens

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.PlaylistAdd
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Equalizer
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.FavoriteBorder
import androidx.compose.material.icons.filled.FormatSize
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.MoreHoriz
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Repeat
import androidx.compose.material.icons.filled.RepeatOne
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.SkipPrevious
import androidx.compose.material.icons.filled.SpeakerGroup
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.media3.common.Player
import androidx.palette.graphics.Palette
import coil3.SingletonImageLoader
import coil3.compose.AsyncImage
import coil3.request.ImageRequest
import coil3.request.allowHardware
import coil3.toBitmap
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.LyricsEdit
import org.nasmusic.app.feedback.EventType
import org.nasmusic.app.lyrics.LrcFormat
import org.nasmusic.app.lyrics.LyricsResult
import org.nasmusic.app.playback.MediaItems
import org.nasmusic.app.ui.LocalUi
import org.nasmusic.app.ui.Message
import org.nasmusic.app.ui.SongMenuTarget
import org.nasmusic.app.ui.formatDuration

/**
 * Musicolet-style Now Playing: big cover (tap → lyrics), centred title/artist/album, action row,
 * seek bar, transport row; background tinted from the cover's dominant colour. Works as a remote
 * when another device is playing ("Playing on …").
 */
@Composable
fun NowPlayingScreen(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val s by c.player.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { while (true) { c.player.refresh(); delay(250) } }
    val item = s.item ?: return Message("Nothing playing. Pick a song from Albums, Playlists or Search.")
    val trackId = item.mediaId
    val track = remember(item) { MediaItems.toTrack(item) }
    val extras = item.mediaMetadata.extras

    var showLyrics by remember { mutableStateOf(false) }
    LaunchedEffect(ui.lyricsRequested) { if (ui.lyricsRequested) { showLyrics = true; ui.lyricsRequested = false } }
    val flip by animateFloatAsState(if (showLyrics) 180f else 0f, tween(350), label = "flip")
    var starred by remember(trackId) { mutableStateOf(extras?.getBoolean(MediaItems.EXTRA_STARRED) ?: false) }
    var volumeOpen by remember { mutableStateOf(false) }
    var seeking by remember { mutableStateOf<Float?>(null) }
    val tint = rememberDominantColor(MediaItems.artUrl(track.albumId, 128))

    Box(
        Modifier.fillMaxSize().background(
            Brush.verticalGradient(listOf(tint.copy(alpha = 0.55f), tint.copy(alpha = 0.25f), MaterialTheme.colorScheme.background)),
        ),
    ) {
        Column(Modifier.fillMaxSize().padding(horizontal = 16.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                s.remote?.let {
                    Text("Playing on ${it.deviceName}", color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f).padding(start = 8.dp))
                } ?: Spacer(Modifier.weight(1f))
                IconButton(onClick = { ui.devicesOpen = true }) {
                    Icon(Icons.Default.SpeakerGroup, "Devices", tint = if (s.remote != null) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
                }
            }
            // Cover ⇄ lyrics flip card
            Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
                if (flip <= 90f) {
                    AsyncImage(
                        model = item.mediaMetadata.artworkUri?.toString(),
                        contentDescription = "Album art — tap for lyrics",
                        contentScale = ContentScale.Crop,
                        modifier = Modifier.fillMaxWidth().aspectRatio(1f).graphicsLayer { rotationY = flip; cameraDistance = 12f * density }
                            .clickable { showLyrics = true },
                    )
                } else {
                    Box(Modifier.fillMaxSize().graphicsLayer { rotationY = flip - 180f; cameraDistance = 12f * density }) {
                        LyricsPanel(c, trackId, s.positionMs, onClose = { showLyrics = false })
                    }
                }
            }
            Spacer(Modifier.height(12.dp))
            Text(track.title, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(track.artist, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center, maxLines = 1,
                modifier = Modifier.clickable(enabled = track.artistId.isNotBlank()) { ui.go("artist/${track.artistId}") })
            Text(track.album, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.clickable(enabled = track.albumId.isNotBlank()) { ui.go("album/${track.albumId}") })
            Spacer(Modifier.height(8.dp))
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = {
                    starred = !starred
                    c.events.record(if (starred) EventType.STAR else EventType.UNSTAR, trackId, context = c.contextCollector.snapshot())
                    scope.launch { runCatching { if (starred) c.api.star(trackId) else c.api.unstar(trackId) } }
                }) { Icon(if (starred) Icons.Default.Favorite else Icons.Default.FavoriteBorder, if (starred) "Remove from favourites" else "Favourite", tint = if (starred) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface) }
                IconButton(onClick = { ui.songInfo = trackId }) { Icon(Icons.Default.Info, "Song info") }
                IconButton(onClick = { ui.addToPlaylist = listOf(track) }) { Icon(Icons.AutoMirrored.Filled.PlaylistAdd, "Add to playlist") }
                IconButton(onClick = { ui.songMenu = SongMenuTarget(track, isCurrent = true) }) { Icon(Icons.Default.MoreHoriz, "More") }
                Spacer(Modifier.weight(1f))
                IconButton(onClick = c.player::cycleRepeat) {
                    Icon(
                        if (s.repeat == Player.REPEAT_MODE_ONE) Icons.Default.RepeatOne else Icons.Default.Repeat, "Repeat",
                        tint = if (s.repeat == Player.REPEAT_MODE_OFF) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.primary,
                    )
                }
                IconButton(onClick = { c.player.setShuffle(!s.shuffle) }) {
                    Icon(Icons.Default.Shuffle, "Shuffle", tint = if (s.shuffle) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(formatDuration(s.positionMs / 1000.0), style = MaterialTheme.typography.bodySmall)
                Slider(
                    value = seeking ?: if (s.durationMs > 0) (s.positionMs.toFloat() / s.durationMs).coerceIn(0f, 1f) else 0f,
                    onValueChange = { seeking = it },
                    onValueChangeFinished = { seeking?.let { c.player.seekTo((it * s.durationMs).toLong()) }; seeking = null },
                    colors = SliderDefaults.colors(thumbColor = Color.White, activeTrackColor = Color.White),
                    modifier = Modifier.weight(1f).padding(horizontal = 12.dp),
                )
                Text(formatDuration(s.durationMs / 1000.0), style = MaterialTheme.typography.bodySmall)
            }
            Row(Modifier.fillMaxWidth().padding(bottom = 8.dp), horizontalArrangement = Arrangement.SpaceEvenly, verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = { volumeOpen = true }) { Icon(Icons.AutoMirrored.Filled.VolumeUp, "Volume", tint = MaterialTheme.colorScheme.onSurfaceVariant) }
                IconButton(onClick = c.player::previous, modifier = Modifier.size(56.dp)) { Icon(Icons.Default.SkipPrevious, "Previous", Modifier.size(40.dp)) }
                IconButton(onClick = c.player::togglePlay, modifier = Modifier.size(72.dp)) {
                    Icon(if (s.isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow, "Play/pause", Modifier.size(56.dp))
                }
                IconButton(onClick = c.player::next, modifier = Modifier.size(56.dp)) { Icon(Icons.Default.SkipNext, "Next", Modifier.size(40.dp)) }
                IconButton(onClick = { ui.go("equalizer") }) { Icon(Icons.Default.Equalizer, "Equalizer", tint = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
        }
    }

    if (volumeOpen) {
        AlertDialog(
            onDismissRequest = { volumeOpen = false },
            title = { Text("Volume") },
            text = { Slider(s.volume, { c.player.setVolume(it) }) },
            confirmButton = { TextButton(onClick = { volumeOpen = false }) { Text("Done") } },
        )
    }
}

/** Dominant colour of the cover via Palette (falls back to a dark grey). */
@Composable
private fun rememberDominantColor(url: String): Color {
    val ctx = LocalContext.current
    var color by remember(url) { mutableStateOf(Color(0xFF2A2522)) }
    LaunchedEffect(url) {
        val req = ImageRequest.Builder(ctx).data(url).allowHardware(false).size(96).build()
        val bmp = runCatching { SingletonImageLoader.get(ctx).execute(req).image?.toBitmap() }.getOrNull() ?: return@LaunchedEffect
        val p = Palette.from(bmp).generate()
        val rgb = p.darkMutedSwatch?.rgb ?: p.dominantSwatch?.rgb ?: return@LaunchedEffect
        color = Color(rgb)
    }
    return color
}

/**
 * The lyrics side of the cover: header (source dropdown, edit, text size, close) and the synced /
 * plain view. When there are none: the file-name message and "Add lyrics".
 */
@Composable
private fun LyricsPanel(c: AppContainer, trackId: String, positionMs: Long, onClose: () -> Unit) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val settings by c.settings.settings.collectAsStateWithLifecycle()
    var result by remember(trackId) { mutableStateOf<LyricsResult?>(null) }
    var fileName by remember(trackId) { mutableStateOf<String?>(null) }
    var editing by remember { mutableStateOf<String?>(null) }
    var sourceMenu by remember { mutableStateOf(false) }
    var reload by remember { mutableStateOf(0) }
    LaunchedEffect(trackId, reload) { result = c.lyrics.get(trackId) }
    LaunchedEffect(trackId, result) {
        if (result is LyricsResult.None && fileName == null) fileName = runCatching { c.api.trackInfo(trackId).fileName }.getOrNull()
    }
    val lyrics = (result as? LyricsResult.Found)?.lyrics

    Column(Modifier.fillMaxSize()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.weight(1f)) {
                Row(Modifier.clickable { sourceMenu = true }.padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(lyrics?.let { LrcFormat.sourceLabel(it.source) } ?: "Embedded lyrics", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Icon(Icons.Default.ArrowDropDown, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                DropdownMenu(sourceMenu, { sourceMenu = false }) {
                    DropdownMenuItem({ Text("✓ " + (lyrics?.let { LrcFormat.sourceLabel(it.source) } ?: "Embedded lyrics")) }, { sourceMenu = false })
                    if (lyrics?.source == "user") {
                        DropdownMenuItem({ Text("Revert to the file's lyrics") }, {
                            sourceMenu = false
                            scope.launch {
                                runCatching { c.api.deleteLyrics(trackId) }.onFailure { ui.toast = "Couldn't revert" }
                                c.lyrics.drop(trackId); reload++
                            }
                        })
                    }
                }
            }
            IconButton(onClick = { editing = lyrics?.let(LrcFormat::toEditable).orEmpty() }) { Icon(Icons.Default.Edit, "Edit lyrics") }
            IconButton(onClick = {
                val next = when { settings.lyricsScale < 1f -> 1f; settings.lyricsScale < 1.3f -> 1.4f; settings.lyricsScale < 1.7f -> 1.8f; else -> 0.8f }
                scope.launch { c.settings.setLyricsScale(next) }
            }) { Icon(Icons.Default.FormatSize, "Text size") }
            IconButton(onClick = onClose) { Icon(Icons.Default.Close, "Close lyrics") }
        }
        when (val r = result) {
            null -> Spacer(Modifier.weight(1f))
            is LyricsResult.Found -> Box(Modifier.weight(1f)) {
                MaterialTheme(typography = MaterialTheme.typography.scaled(settings.lyricsScale)) {
                    LyricsView(r.lyrics, positionMs, onSeek = c.player::seekTo)
                }
            }
            LyricsResult.None -> Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(8.dp)) {
                Text(
                    "Could not find embedded lyrics in tags of \"${fileName ?: "this"}\" file.",
                    fontSize = (22 * settings.lyricsScale).sp, lineHeight = (28 * settings.lyricsScale).sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(16.dp))
                OutlinedButton(onClick = { editing = "" }) { Icon(Icons.Default.Edit, null); Spacer(Modifier.width(8.dp)); Text("Add lyrics") }
            }
            LyricsResult.Unavailable -> Text("Lyrics aren't available offline for this song.", modifier = Modifier.weight(1f).padding(8.dp))
        }
    }

    editing?.let { text ->
        LyricsEditor(text, onDismiss = { editing = null }) { raw ->
            editing = null
            scope.launch {
                runCatching { c.api.putLyrics(trackId, LyricsEdit(raw)) }
                    .onSuccess { ui.toast = "Lyrics saved" }
                    .onFailure { ui.toast = "Couldn't save lyrics (needs the server)" }
                c.lyrics.drop(trackId); reload++
            }
        }
    }
}

@Composable
private fun LyricsEditor(initial: String, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    var text by remember { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (initial.isBlank()) "Add lyrics" else "Edit lyrics") },
        text = {
            Column {
                Text("Plain text, or LRC lines like [01:23.45]Lyric for synced lyrics. Saved on the server for your account; the music file isn't changed.", style = MaterialTheme.typography.bodySmall)
                OutlinedTextField(text, { text = it }, modifier = Modifier.fillMaxWidth().height(320.dp).padding(top = 8.dp))
            }
        },
        confirmButton = { TextButton(enabled = text.isNotBlank(), onClick = { onSave(text) }) { Text("Save") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

private fun androidx.compose.material3.Typography.scaled(f: Float) = copy(
    titleLarge = titleLarge.copy(fontSize = titleLarge.fontSize * f, lineHeight = titleLarge.lineHeight * f),
    headlineSmall = headlineSmall.copy(fontSize = headlineSmall.fontSize * f, lineHeight = headlineSmall.lineHeight * f),
    bodyLarge = bodyLarge.copy(fontSize = bodyLarge.fontSize * f, lineHeight = bodyLarge.lineHeight * f),
)

