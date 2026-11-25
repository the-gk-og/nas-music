package org.homebeat.wear.nowplaying

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import org.homebeat.app.data.api.Lyrics
import org.homebeat.app.data.api.HomebeatApi
import org.homebeat.app.lyrics.LyricsTimeline

/**
 * Synced lyrics for the watch's Now Playing line. Kept in a small in-memory cache (a "none" answer too,
 * so a song without lyrics isn't asked for again); unsynced lyrics are skipped since one line of plain
 * text doesn't help on a watch. Network failures aren't cached, so the next song change retries.
 */
object WearLyrics {
    private val cache = object : LinkedHashMap<String, Lyrics?>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Lyrics?>?) = size > 16
    }

    suspend fun get(api: HomebeatApi, trackId: String): Lyrics? {
        synchronized(cache) { if (cache.containsKey(trackId)) return cache[trackId] }
        val resp = try { api.lyrics(trackId) } catch (e: Exception) { return null }
        val found = resp.body()?.takeIf { resp.isSuccessful && it.synced && it.lines.isNotEmpty() }
        if (resp.isSuccessful || resp.code() == 404) synchronized(cache) { cache[trackId] = found }
        return found
    }

    /** The line being sung at [positionMs], or null (no synced lyrics, before the first line, or a gap). */
    fun lineAt(lyrics: Lyrics?, positionMs: Long): String? {
        val lines = lyrics?.lines ?: return null
        val i = LyricsTimeline.currentIndex(lines, positionMs)
        return lines.getOrNull(i)?.text?.takeIf { it.isNotBlank() }
    }
}

/** Current lyric line for [trackId] at [positionMs]; null while loading or when there is none. */
@Composable
fun rememberLyricLine(api: HomebeatApi, trackId: String?, positionMs: Long): String? {
    var lyrics by remember(trackId) { mutableStateOf<Lyrics?>(null) }
    LaunchedEffect(trackId) { lyrics = trackId?.let { WearLyrics.get(api, it) } }
    return WearLyrics.lineAt(lyrics, positionMs)
}
