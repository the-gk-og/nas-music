package org.nasmusic.app.ui.screens

import android.os.SystemClock
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.animateScrollBy
import androidx.compose.foundation.layout.Column
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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.nestedscroll.NestedScrollConnection
import androidx.compose.ui.input.nestedscroll.NestedScrollSource
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.nasmusic.app.data.api.Lyrics
import org.nasmusic.app.lyrics.LyricsTimeline

private const val USER_SCROLL_PAUSE_MS = 4000L

/** Synced lyrics follow playback (tap a line to seek); unsynced lyrics are plain scrollable text. */
@Composable
fun LyricsView(lyrics: Lyrics, positionMs: Long, onSeek: (Long) -> Unit, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxSize()) {
        if (lyrics.synced && lyrics.lines.isNotEmpty()) SyncedLyrics(lyrics, positionMs, onSeek, Modifier.weight(1f))
        else PlainLyrics(lyrics, Modifier.weight(1f))
        Text(
            if (lyrics.source == "lrc_file") "Lyrics from .lrc file" else "Lyrics from file tags",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
            modifier = Modifier.padding(16.dp),
        )
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
            Text(
                if (instrumental) "♪" else line.text,
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
                    .clickable(enabled = line.timeMs != null) { line.timeMs?.let(onSeek) }
                    .padding(horizontal = 24.dp, vertical = 10.dp),
            )
        }
    }
}

/** Scroll so item [index] is vertically centred. */
private suspend fun LazyListState.centerOn(index: Int) {
    fun visible() = layoutInfo.visibleItemsInfo.firstOrNull { it.index == index }
    if (visible() == null) scrollToItem(index)
    val item = visible() ?: return
    val viewport = layoutInfo.viewportEndOffset + layoutInfo.viewportStartOffset
    animateScrollBy((item.offset + item.size / 2 - viewport / 2).toFloat())
}
