package org.homebeat.app.ui.screens

import android.os.SystemClock
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.animateScrollBy
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.RecordVoiceOver
import androidx.compose.material.icons.filled.Timer
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.TextButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.nestedscroll.NestedScrollConnection
import androidx.compose.ui.input.nestedscroll.NestedScrollSource
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.homebeat.app.container
import org.homebeat.app.data.api.Lyrics
import org.homebeat.app.lyrics.LyricsOffsets
import org.homebeat.app.lyrics.LyricsTimeline

private const val USER_SCROLL_PAUSE_MS = 4000L

/**
 * Synced lyrics follow playback (tap a line to seek), with word-by-word highlighting for enhanced LRC and
 * an Earlier / Later timing nudge remembered per song; unsynced lyrics are plain scrollable text.
 */
@Composable
fun LyricsView(lyrics: Lyrics, positionMs: Long, onSeek: (Long) -> Unit, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val offsets = remember(context) { LyricsOffsets(context) }
    var offset by remember(lyrics.trackId) { mutableLongStateOf(offsets.get(lyrics.trackId)) }
    var timingOpen by remember { mutableStateOf(false) }
    val synced = lyrics.synced && lyrics.lines.isNotEmpty()
    fun nudge(to: Long) { offset = LyricsOffsets.clamp(to); offsets.set(lyrics.trackId, offset) }

    Column(modifier.fillMaxSize()) {
        if (synced) SyncedLyrics(lyrics, positionMs - offset, { ms -> onSeek((ms + offset).coerceAtLeast(0)) }, Modifier.weight(1f))
        else PlainLyrics(lyrics, Modifier.weight(1f))
        if (synced && timingOpen) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.CenterHorizontally),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                FilledTonalButton(onClick = { nudge(offset - LyricsOffsets.STEP_MS) }) { Text("Earlier") }
                TextButton(onClick = { nudge(0) }, enabled = offset != 0L) { Text(LyricsTimeline.formatOffset(offset)) }
                FilledTonalButton(onClick = { nudge(offset + LyricsOffsets.STEP_MS) }) { Text("Later") }
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                when (lyrics.source) {
                    "lrc_file" -> "Lyrics from .lrc file"
                    "lrclib" -> "Lyrics from LRCLIB"
                    "user" -> "Your edit"
                    else -> "Lyrics from file tags"
                },
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
                modifier = Modifier.weight(1f).padding(16.dp),
            )
            // Karaoke: turn the vocals down on this phone (KaraokeProcessor); pairs with the word-by-word lyrics.
            val karaokeFlow = remember(context) { context.container.eq.karaoke }
            val karaoke by karaokeFlow.collectAsState()
            IconButton(onClick = { karaokeFlow.value = !karaoke }) {
                Icon(
                    Icons.Filled.RecordVoiceOver, if (karaoke) "Karaoke on: vocals turned down" else "Karaoke: turn the vocals down",
                    tint = if (karaoke) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (synced) {
                IconButton(onClick = { timingOpen = !timingOpen }) {
                    Icon(
                        Icons.Filled.Timer, "Lyrics timing",
                        tint = if (timingOpen || offset != 0L) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun PlainLyrics(lyrics: Lyrics, modifier: Modifier) {
    val text = lyrics.text.ifBlank { lyrics.lines.joinToString("\n") { it.text } }
    Text(
        text,
        style = MaterialTheme.typography.bodyLarge.copy(lineHeight = MaterialTheme.typography.bodyLarge.lineHeight * 1.2f),
        color = MaterialTheme.colorScheme.onSurface,
        modifier = modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 8.dp),
    )
}

@Composable
private fun SyncedLyrics(lyrics: Lyrics, positionMs: Long, onSeek: (Long) -> Unit, modifier: Modifier) {
    val lines = lyrics.lines
    val current = LyricsTimeline.currentIndex(lines, positionMs)
    val state = rememberLazyListState()
    var lastUserScroll by remember { mutableLongStateOf(0L) }

    // Any finger scroll pauses auto-follow for a few seconds.
    val userScroll = remember {
        object : NestedScrollConnection {
            override fun onPreScroll(available: Offset, source: NestedScrollSource): Offset {
                if (source == NestedScrollSource.UserInput) lastUserScroll = SystemClock.uptimeMillis()
                return Offset.Zero
            }
        }
    }

    LaunchedEffect(current, lastUserScroll) {
        val sinceUser = SystemClock.uptimeMillis() - lastUserScroll
        if (sinceUser < USER_SCROLL_PAUSE_MS) delay(USER_SCROLL_PAUSE_MS - sinceUser)
        if (current >= 0) state.centerOn(current)
    }

    LazyColumn(
        state = state,
        // Generous padding so the first and last lines can sit in the middle too.
        contentPadding = PaddingValues(vertical = 200.dp),
        modifier = modifier.fillMaxWidth().nestedScroll(userScroll),
    ) {
        itemsIndexed(lines) { i, line ->
            val active = i == current
            val instrumental = LyricsTimeline.isInstrumental(line)
            val colors = MaterialTheme.colorScheme
            // Expressive motion: the active line grows in smoothly (docs/v3-ux.md §1).
            val motion = org.homebeat.app.ui.LocalMotion.current
            val scale by androidx.compose.animation.core.animateFloatAsState(
                if (active && motion.expressive) 1.06f else 1f, motion.spec(1.5f), label = "lyric",
            )
            val words = line.words
            Text(
                if (instrumental) androidx.compose.ui.text.AnnotatedString("♪")
                else if (active && !words.isNullOrEmpty()) karaoke(words, LyricsTimeline.currentWordIndex(words, positionMs), colors.primary, colors.onSurface.copy(alpha = 0.5f))
                else androidx.compose.ui.text.AnnotatedString(line.text),
                style = if (active) MaterialTheme.typography.headlineSmall.copy(fontWeight = FontWeight.Bold)
                else MaterialTheme.typography.titleLarge,
                color = when {
                    instrumental -> colors.onSurfaceVariant.copy(alpha = if (active) 0.8f else 0.35f)
                    active -> colors.primary
                    i < current -> colors.onSurfaceVariant.copy(alpha = 0.55f)
                    else -> colors.onSurface.copy(alpha = 0.8f)
                },
                textAlign = TextAlign.Start,
                modifier = Modifier.fillMaxWidth()
                    .graphicsLayer { scaleX = scale; scaleY = scale; transformOrigin = androidx.compose.ui.graphics.TransformOrigin(0f, 0.5f) }
                    .clickable(enabled = line.timeMs != null) { line.timeMs?.let(onSeek) }
                    .padding(horizontal = 24.dp, vertical = 10.dp),
            )
        }
    }
}

/** The active line, word by word: sung words in [sung], upcoming ones dimmed. */
private fun karaoke(words: List<org.homebeat.app.data.api.LyricWord>, current: Int, sung: androidx.compose.ui.graphics.Color, upcoming: androidx.compose.ui.graphics.Color) =
    buildAnnotatedString {
        words.forEachIndexed { j, w -> withStyle(SpanStyle(color = if (j <= current) sung else upcoming)) { append(w.text) } }
    }

/** Scroll so item [index] is vertically centred. */
private suspend fun LazyListState.centerOn(index: Int) {
    fun visible() = layoutInfo.visibleItemsInfo.firstOrNull { it.index == index }
    if (visible() == null) scrollToItem(index)
    val item = visible() ?: return
    val viewport = layoutInfo.viewportEndOffset + layoutInfo.viewportStartOffset
    animateScrollBy((item.offset + item.size / 2 - viewport / 2).toFloat())
}
