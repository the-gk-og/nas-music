package org.nasmusic.app.playback

import android.net.Uri
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import org.nasmusic.app.data.api.NasMusicApi
import org.nasmusic.app.data.api.RadioRequest
import org.nasmusic.app.data.api.RadioSeed
import org.nasmusic.app.data.api.Track

/**
 * Browse tree for Android Auto (and any other MediaBrowser client), built only from AndroidX Media3:
 *
 *   root ─ Home (today's mixes) ─ mix:<id> ─ tracks
 *        ├ Playlists ─ playlist:<id> ─ tracks
 *        ├ Albums ─ album:<id> ─ tracks
 *        ├ Artists ─ artist:<id> ─ album:<id> ─ tracks
 *        └ Radio ─ radio:random, radio:favourites… (playable)
 *
 * Playable track entries have the id `play|<context>|<trackId>` so that tapping one plays the whole
 * context (album, playlist, mix, search results) from that track; [expand] turns it back into tracks.
 */
class AutoLibrary(private val api: () -> NasMusicApi) {
    data class Expanded(val tracks: List<Track>, val startIndex: Int, val queueName: String, val source: PlaySource,
                        val mixId: String? = null, val radioQueueId: String? = null)

    private var lastSearch: Pair<String, List<Track>>? = null

    fun root(): MediaItem = folder(ROOT, "nas-music", MediaMetadata.MEDIA_TYPE_FOLDER_MIXED)

    suspend fun children(parentId: String): List<MediaItem> {
        val a = api()
        return when {
            parentId == ROOT -> listOf(
                folder(HOME, "Home", MediaMetadata.MEDIA_TYPE_FOLDER_MIXED),
                folder(PLAYLISTS, "Playlists", MediaMetadata.MEDIA_TYPE_FOLDER_PLAYLISTS),
                folder(ALBUMS, "Albums", MediaMetadata.MEDIA_TYPE_FOLDER_ALBUMS),
                folder(ARTISTS, "Artists", MediaMetadata.MEDIA_TYPE_FOLDER_ARTISTS),
                folder(RADIO, "Radio", MediaMetadata.MEDIA_TYPE_FOLDER_RADIO_STATIONS),
            )
            parentId == HOME -> a.mixesToday().map {
                folder("mix:${it.id}", it.title, MediaMetadata.MEDIA_TYPE_PLAYLIST, subtitle = "${it.tracks.size} songs",
                    art = it.tracks.firstOrNull()?.track?.albumId)
            }
            parentId == PLAYLISTS -> a.playlists().map {
                folder("playlist:${it.id}", it.name, MediaMetadata.MEDIA_TYPE_PLAYLIST, subtitle = "${it.trackCount} songs")
            }
            parentId == ALBUMS -> a.albums(sort = "name", limit = 500).map {
                folder("album:${it.id}", it.title, MediaMetadata.MEDIA_TYPE_ALBUM, subtitle = it.artist, art = it.id)
            }
            parentId == ARTISTS -> a.artists().map {
                folder("artist:${it.id}", it.name, MediaMetadata.MEDIA_TYPE_ARTIST, subtitle = "${it.albumCount} albums")
            }
            parentId == RADIO -> RADIOS.map { (id, title) -> playable(id, title, MediaMetadata.MEDIA_TYPE_RADIO_STATION) }
            parentId.startsWith("artist:") -> a.artist(parentId.removePrefix("artist:")).albums.map {
                folder("album:${it.id}", it.title, MediaMetadata.MEDIA_TYPE_ALBUM, subtitle = it.year?.toString(), art = it.id)
            }
            parentId.startsWith("album:") || parentId.startsWith("playlist:") || parentId.startsWith("mix:") ->
                contextTracks(parentId).tracks.map { trackItem(parentId, it) }
            else -> emptyList()
        }
    }

    suspend fun search(query: String): List<MediaItem> {
        val tracks = runCatching { api().search(query, 40).tracks }.getOrDefault(emptyList())
        lastSearch = query to tracks
        return tracks.map { trackItem("search:$query", it) }
    }

    /** Turn a browse/search item (or a voice query) into what to play. Null = not ours (a plain track id). */
    suspend fun expand(mediaId: String): Expanded? {
        if (mediaId.startsWith("radio:")) return radio(mediaId.removePrefix("radio:"))
        val (ctx, trackId) = parsePlayId(mediaId) ?: return null
        val e = if (ctx.startsWith("search:")) {
            val q = ctx.removePrefix("search:")
            val tracks = lastSearch?.takeIf { it.first == q }?.second ?: api().search(q, 40).tracks
            Expanded(tracks, 0, "Search: $q", PlaySource.SEARCH)
        } else contextTracks(ctx)
        return e.copy(startIndex = e.tracks.indexOfFirst { it.id == trackId }.coerceAtLeast(0))
    }

    /** "Play <query>" by voice: an artist → artist radio, an album → the album, else matching songs. */
    suspend fun voice(query: String): Expanded? {
        if (query.isBlank()) return radio("random")
        val r = runCatching { api().search(query, 20) }.getOrNull() ?: return null
        val q = norm(query)
        r.artists.firstOrNull { norm(it.name) == q }?.let { return radio("artist:${it.id}", "${it.name} radio") }
        r.albums.firstOrNull { norm(it.title) == q || norm("${it.title} ${it.artist}") == q }?.let { return contextTracks("album:${it.id}") }
        if (r.tracks.isNotEmpty()) return Expanded(r.tracks, 0, "Search: $query", PlaySource.SEARCH)
        r.albums.firstOrNull()?.let { return contextTracks("album:${it.id}") }
        r.artists.firstOrNull()?.let { return radio("artist:${it.id}", "${it.name} radio") }
        return null
    }

    private suspend fun contextTracks(ctx: String): Expanded {
        val a = api()
        val id = ctx.substringAfter(':')
        return when {
            ctx.startsWith("album:") -> a.album(id).let { Expanded(it.tracks, 0, it.title, PlaySource.LIBRARY) }
            ctx.startsWith("playlist:") -> a.playlist(id).let { Expanded(it.tracks, 0, it.name, PlaySource.LIBRARY) }
            ctx.startsWith("mix:") -> a.mix(id).let { Expanded(it.tracks.map { mt -> mt.track }, 0, it.title, PlaySource.MIX, mixId = it.id) }
            else -> Expanded(emptyList(), 0, "", PlaySource.LIBRARY)
        }
    }

    private suspend fun radio(seed: String, title: String? = null): Expanded? {
        val (type, id) = seed.split(':', limit = 2).let { it[0] to it.getOrNull(1) }
        val q = runCatching { api().startRadio(RadioRequest(RadioSeed(type, id))) }.getOrNull() ?: return null
        return Expanded(q.tracks, 0, title ?: q.name, PlaySource.MIX, radioQueueId = q.id)
    }

    private fun trackItem(ctx: String, t: Track): MediaItem {
        val base = MediaItems.fromTrack(t, PlaySource.LIBRARY)
        return base.buildUpon().setMediaId(playId(ctx, t.id)).build()
    }

    private fun folder(id: String, title: String, type: Int, subtitle: String? = null, art: String? = null): MediaItem =
        MediaItem.Builder().setMediaId(id).setMediaMetadata(
            MediaMetadata.Builder().setTitle(title).setSubtitle(subtitle).setIsBrowsable(true).setIsPlayable(false)
                .setMediaType(type).apply { art?.let { setArtworkUri(Uri.parse(MediaItems.artUrl(it, 256))) } }.build(),
        ).build()

    private fun playable(id: String, title: String, type: Int): MediaItem =
        MediaItem.Builder().setMediaId(id).setMediaMetadata(
            MediaMetadata.Builder().setTitle(title).setIsBrowsable(false).setIsPlayable(true).setMediaType(type).build(),
        ).build()

    companion object {
        const val ROOT = "root"
        const val HOME = "home"
        const val PLAYLISTS = "playlists"
        const val ALBUMS = "albums"
        const val ARTISTS = "artists"
        const val RADIO = "radio"
        val RADIOS = listOf("radio:random" to "Your radio")

        fun playId(ctx: String, trackId: String) = "play|$ctx|$trackId"

        /** `play|<context>|<trackId>` → (context, trackId). The context may itself contain '|' (search text). */
        fun parsePlayId(id: String): Pair<String, String>? {
            if (!id.startsWith("play|")) return null
            val rest = id.removePrefix("play|")
            val cut = rest.lastIndexOf('|')
            if (cut <= 0 || cut == rest.length - 1) return null
            return rest.substring(0, cut) to rest.substring(cut + 1)
        }

        fun norm(s: String) = s.lowercase().replace(Regex("[^\\p{L}\\p{N}]+"), " ").trim()
    }
}
