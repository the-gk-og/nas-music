package org.nasmusic.app.playback

import kotlin.random.Random
import org.nasmusic.app.data.api.RadioSeed
import org.nasmusic.app.data.api.Track

/** What playback needs to know to fall back to downloads when there's no connection. */
class OfflineSource(
    val isOffline: () -> Boolean,
    val downloadedIds: () -> Set<String>,
    val downloadedTracks: () -> List<Track>,
    val toast: (String) -> Unit,
) {
    companion object {
        val NONE = OfflineSource({ false }, { emptySet() }, { emptyList() }, {})
    }
}

/** Offline fallbacks: play only what's downloaded, and a radio made from downloads. Pure, unit tested. */
object OfflinePlayback {
    /**
     * The downloaded part of [tracks] and where to start in it: the tapped song if it's downloaded, else
     * the next downloaded one after it (wrapping round). Null when nothing in the list is downloaded.
     */
    fun playable(tracks: List<Track>, start: Int, downloaded: Set<String>): Pair<List<Track>, Int>? {
        val kept = tracks.withIndex().filter { it.value.id in downloaded }
        if (kept.isEmpty()) return null
        val s = start.coerceIn(0, tracks.size - 1)
        val at = kept.indexOfFirst { it.index >= s }.takeIf { it >= 0 } ?: 0
        return kept.map { it.value } to at
    }

    /** Index of the next downloaded item after [current] in [ids], or null if there is none. */
    fun nextPlayable(ids: List<String>, current: Int, downloaded: Set<String>): Int? =
        ((current + 1) until ids.size).firstOrNull { ids[it] in downloaded }

    /**
     * A radio from downloaded songs: the seed (if downloaded) first, then songs by the same artist and from
     * the same album mixed in with everything else, shuffled. A playlist or random seed just shuffles.
     * No artist twice in a row where it can be avoided.
     */
    fun radio(seed: RadioSeed, downloaded: List<Track>, random: Random = Random.Default, size: Int = 50): List<Track> {
        val all = downloaded.distinctBy { it.id }
        if (all.isEmpty()) return emptyList()
        val seedTrack = if (seed.type == "track") all.firstOrNull { it.id == seed.id } else null
        val artistId = when (seed.type) {
            "artist" -> seed.id
            "track" -> seedTrack?.artistId
            "album" -> all.firstOrNull { it.albumId == seed.id }?.artistId
            else -> null
        }
        val albumId = if (seed.type == "album") seed.id else seedTrack?.albumId
        val rest = all.filter { it.id != seedTrack?.id }
        val close = rest.filter { (artistId != null && it.artistId == artistId) || (albumId != null && it.albumId == albumId) }.shuffled(random)
        val other = rest.filter { it !in close }.shuffled(random)
        // Related songs get roughly every other slot at first, then the rest.
        val merged = ArrayList<Track>()
        var i = 0
        var j = 0
        while (i < close.size || j < other.size) {
            if (i < close.size && (merged.size % 2 == 0 || j >= other.size)) merged += close[i++] else merged += other[j++]
        }
        val ordered = spreadArtists(listOfNotNull(seedTrack) + merged) // index 0 (the seed) never moves
        return ordered.take(size)
    }

    /** Swaps songs forward so the same artist doesn't play twice in a row when another choice exists. */
    fun spreadArtists(tracks: List<Track>): List<Track> {
        val out = tracks.toMutableList()
        for (k in 1 until out.size) {
            if (out[k].artistId.isNotBlank() && out[k].artistId == out[k - 1].artistId) {
                val swap = (k + 1 until out.size).firstOrNull { out[it].artistId != out[k - 1].artistId } ?: continue
                val t = out[k]; out[k] = out[swap]; out[swap] = t
            }
        }
        return out
    }
}
