package org.nasmusic.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.SkipPrevious
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.StarBorder
import androidx.compose.material.icons.filled.ThumbDown
import androidx.compose.material.icons.filled.ThumbUp
import androidx.compose.material.icons.outlined.ThumbDown
import androidx.compose.material.icons.outlined.ThumbUp
import androidx.compose.material3.Card
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconToggleButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil3.compose.AsyncImage
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.ApiUrls
import org.nasmusic.app.data.api.Lyrics
import org.nasmusic.app.lyrics.LyricsResult
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.material.icons.filled.Lyrics
import org.nasmusic.app.data.api.WhyResponse
import retrofit2.HttpException
import org.nasmusic.app.feedback.EventType
import org.nasmusic.app.playback.MediaItems
import org.nasmusic.app.playback.PlaySource
import org.nasmusic.app.ui.Message
import org.nasmusic.app.ui.formatDuration

@Composable
fun NowPlayingScreen(c: AppContainer, onClose: () -> Unit) {
    val s by c.player.state.collectAsStateWithLifecycle()
    var showLyrics by remember { mutableStateOf(false) }
    // Faster position updates while the lyrics sheet is open so the highlighted line keeps up.
    LaunchedEffect(showLyrics) { while (true) { c.player.refresh(); delay(if (showLyrics) 150 else 500) } }
    val item = s.item ?: return Message("Nothing playing")
    val trackId = item.mediaId
    val extras = item.mediaMetadata.extras
    val source = extras?.getString(MediaItems.EXTRA_SOURCE)
    val mixId = extras?.getString(MediaItems.EXTRA_MIX_ID)
    val durationS = s.durationMs / 1000.0
    val scope = rememberCoroutineScope()

    var why by remember(trackId) { mutableStateOf<WhyResponse?>(null) }
    var starred by remember(trackId) { mutableStateOf(extras?.getBoolean(MediaItems.EXTRA_STARRED) ?: false) }
    var thumb by remember(trackId) { mutableIntStateOf(0) }
    var rating by remember(trackId) { mutableIntStateOf(0) }
    var moreLikeBusy by remember { mutableStateOf(false) }
    var moreLikeMessage by remember(trackId) { mutableStateOf<String?>(null) }
    var seeking by remember { mutableStateOf<Float?>(null) }
    LaunchedEffect(trackId) { why = runCatching { c.api.why(trackId) }.getOrNull() }
    var lyrics by remember(trackId) { mutableStateOf<LyricsResult?>(null) }
    LaunchedEffect(trackId) { lyrics = c.lyrics.get(trackId) }
    val found = (lyrics as? LyricsResult.Found)?.lyrics

    fun record(type: EventType, rate: Int? = null) = c.events.record(
        type, trackId, positionS = s.positionMs / 1000.0, durationS = durationS.takeIf { it > 0 },
        source = source, mixId = mixId, rating = rate,
    )

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(Modifier.fillMaxWidth()) {
            IconButton(onClick = onClose) { Icon(Icons.Default.ExpandMore, "Close") }
        }
        AsyncImage(
            model = item.mediaMetadata.artworkUri?.toString(),
            contentDescription = "Album art",
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxWidth().aspectRatio(1f).clip(RoundedCornerShape(12.dp)),
        )
        Text(item.mediaMetadata.title?.toString().orEmpty(), style = MaterialTheme.typography.headlineSmall, textAlign = TextAlign.Center)
        Text(
            listOfNotNull(item.mediaMetadata.artist, item.mediaMetadata.albumTitle).joinToString(" · "),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )

        Slider(
            value = seeking ?: if (s.durationMs > 0) s.positionMs.toFloat() / s.durationMs else 0f,
            onValueChange = { seeking = it },
            onValueChangeFinished = { seeking?.let { c.player.seekTo((it * s.durationMs).toLong()) }; seeking = null },
        )
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(formatDuration(s.positionMs / 1000.0), style = MaterialTheme.typography.bodySmall)
            Text(formatDuration(durationS), style = MaterialTheme.typography.bodySmall)
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            IconButton(onClick = c.player::previous) { Icon(Icons.Default.SkipPrevious, "Previous", Modifier.size(36.dp)) }
            FilledIconButton(onClick = c.player::togglePlay, modifier = Modifier.size(64.dp)) {
                Icon(if (s.isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow, "Play/pause", Modifier.size(36.dp))
            }
            IconButton(onClick = c.player::next, enabled = s.hasNext) { Icon(Icons.Default.SkipNext, "Next", Modifier.size(36.dp)) }
        }

        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            // Tapping the active thumb again clears it (thumb_clear).
            IconToggleButton(thumb == -1, { if (thumb == -1) { thumb = 0; record(EventType.THUMB_CLEAR) } else { thumb = -1; record(EventType.THUMB_DOWN) } }) {
                Icon(if (thumb == -1) Icons.Filled.ThumbDown else Icons.Outlined.ThumbDown, "Thumbs down")
            }
            IconToggleButton(starred, { on ->
                starred = on
                record(if (on) EventType.STAR else EventType.UNSTAR)
                scope.launch { runCatching { if (on) c.api.star(trackId) else c.api.unstar(trackId) } }
            }) {
                Icon(if (starred) Icons.Filled.Star else Icons.Filled.StarBorder, if (starred) "Unstar" else "Star")
            }
            IconToggleButton(thumb == 1, { if (thumb == 1) { thumb = 0; record(EventType.THUMB_CLEAR) } else { thumb = 1; record(EventType.THUMB_UP) } }) {
                Icon(if (thumb == 1) Icons.Filled.ThumbUp else Icons.Outlined.ThumbUp, "Thumbs up")
            }
        }
        RatingBar(rating) { r -> rating = r; record(EventType.RATE, r) }

        if (found != null) {
            OutlinedButton(onClick = { showLyrics = true }) { Icon(Icons.Default.Lyrics, null); Text(" Lyrics") }
        }
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("Why this song?", style = MaterialTheme.typography.titleSmall)
                Text(why?.reason ?: extras?.getString(MediaItems.EXTRA_REASON) ?: "…")
                why?.similarTo?.takeIf { it.isNotEmpty() }?.let { sim ->
                    Text(
                        "Sounds like " + sim.joinToString(", ") { "${it.title} by ${it.artist}" },
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
        }
        FilledTonalButton(
            enabled = !moreLikeBusy,
            onClick = {
                moreLikeBusy = true
                moreLikeMessage = null
                scope.launch {
                    runCatching { c.api.moreLike(trackId) }
                        .onSuccess { mix ->
                            c.player.play(mix.tracks.map { it.track }, 0, PlaySource.MORE_LIKE, mix.id, mix.tracks.map { it.reason })
                        }
                        .onFailure { e -> moreLikeMessage = ApiUrls.moreLikeError((e as? HttpException)?.code()) }
                    moreLikeBusy = false
                }
            },
        ) { Icon(Icons.Default.AutoAwesome, null); Text(" More like this") }
        moreLikeMessage?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
        }
    }
    if (showLyrics && found != null) {
        LyricsSheet(found, s.positionMs, onSeek = c.player::seekTo, onDismiss = { showLyrics = false })
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun LyricsSheet(lyrics: Lyrics, positionMs: Long, onSeek: (Long) -> Unit, onDismiss: () -> Unit) {
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        LyricsView(lyrics, positionMs, onSeek, Modifier.fillMaxHeight(0.9f))
    }
}

@Composable
private fun RatingBar(rating: Int, onRate: (Int) -> Unit) {
    Row {
        for (i in 1..5) {
            IconButton(onClick = { onRate(i) }, modifier = Modifier.size(36.dp)) {
                Icon(
                    if (i <= rating) Icons.Filled.Star else Icons.Filled.StarBorder,
                    "Rate $i",
                    tint = MaterialTheme.colorScheme.secondary,
                    modifier = Modifier.size(20.dp),
                )
            }
        }
    }
}
