package org.homebeat.app.local

import java.text.Normalizer
import org.homebeat.app.data.api.Track

/** An album made of songs stored on the phone. */
data class LocalAlbum(val id: String, val title: String, val artist: String, val year: Int?, val tracks: List<Track>)

/** An artist with songs stored on the phone. */
data class LocalArtist(val id: String, val name: String, val trackCount: Int, val albumCount: Int)

/** A folder on the phone that holds music, for the "only these folders" picker. */
data class LocalFolder(val path: String, val trackCount: Int)

/**
 * Songs on the phone itself (MediaStore), shown next to the NAS library. They use the normal [Track] model so
 * every list, the player and the queue work unchanged; the ids carry a `local:` prefix so nothing that talks to
 * the server (events, queues, favourites, Connect, Cast) ever sends them there.
 *
 * Ids: track `local:<mediaStoreId>`, album `local:album:<albumId>`, artist `local:artist:<artistId>`.
 * Pure (no Android), unit tested.
 */
object LocalIds {
    const val PREFIX = "local:"
    private const val ALBUM = "local:album:"
    private const val ARTIST = "local:artist:"

    fun isLocal(id: String?): Boolean = id != null && id.startsWith(PREFIX)
    fun anyLocal(tracks: List<Track>): Boolean = tracks.any { isLocal(it.id) }

    fun track(mediaStoreId: Long) = "$PREFIX$mediaStoreId"
    fun album(albumId: Long) = "$ALBUM$albumId"
    fun artist(artistId: Long) = "$ARTIST$artistId"

    /** The MediaStore row id of a local track id, or null for anything else (albums, artists, NAS ids). */
    fun mediaStoreId(trackId: String): Long? = if (isLocal(trackId)) trackId.removePrefix(PREFIX).toLongOrNull() else null
    fun albumMediaStoreId(albumId: String): Long? = if (albumId.startsWith(ALBUM)) albumId.removePrefix(ALBUM).toLongOrNull() else null
    fun isLocalAlbum(id: String?) = id != null && id.startsWith(ALBUM)
    fun isLocalArtist(id: String?) = id != null && id.startsWith(ARTIST)

    /** MediaStore's TRACK column is disc * 1000 + track. Returns (track, disc); zeros are "unknown". */
    fun splitTrackNo(raw: Int): Pair<Int?, Int?> {
        if (raw <= 0) return null to null
        val disc = raw / 1000
        val track = raw % 1000
        return track.takeIf { it > 0 } to disc.takeIf { it > 0 }
    }

    /** "Music/Albums/X/" or "/storage/emulated/0/Music/Albums/X/a.mp3" → "Music/Albums/X". */
    fun folderOf(relativePath: String?, dataPath: String?): String {
        relativePath?.trim('/')?.takeIf { it.isNotEmpty() }?.let { return it }
        val parent = dataPath?.substringBeforeLast('/', "")?.takeIf { it.isNotEmpty() } ?: return ""
        // Drop the storage volume ("/storage/emulated/0", "/storage/1234-ABCD") so paths match RELATIVE_PATH.
        val m = Regex("^/storage/(emulated/\\d+|[^/]+)/?").find(parent)
        return (if (m != null) parent.substring(m.range.last + 1) else parent).trim('/')
    }

    /** Files Homebeat itself (or its watch/desktop siblings) keeps in shared storage are not "your music". */
    fun isOwnFile(folder: String, packageName: String): Boolean {
        val f = folder.lowercase()
        return f.startsWith("android/data/") || f.startsWith("android/media/${packageName.lowercase()}") ||
            f.contains("/android/data/")
    }

    /** True when [folder] is one of [chosen] or inside one; an empty choice means every folder. */
    fun inFolders(folder: String, chosen: Set<String>): Boolean =
        chosen.isEmpty() || chosen.any { c -> val p = c.trim('/'); folder == p || folder.startsWith("$p/") }

    /** Folders with a song count, most songs first. */
    fun folders(folderOfTrack: List<String>): List<LocalFolder> =
        folderOfTrack.groupingBy { it }.eachCount().map { LocalFolder(it.key, it.value) }
            .sortedWith(compareByDescending<LocalFolder> { it.trackCount }.thenBy { it.path.lowercase() })

    /** Lowercase, no accents, no "(Remastered 2011)" / "[Live]" tails, words only. */
    fun norm(s: String): String {
        val noAccents = Normalizer.normalize(s, Normalizer.Form.NFD).replace(Regex("\\p{M}+"), "")
        return noAccents.lowercase()
            .replace(Regex("[(\\[][^)\\]]*[)\\]]"), " ")
            .replace(Regex("[^\\p{L}\\p{N}]+"), " ")
            .trim()
    }

    private fun key(t: Track) = norm(t.title) + "|" + norm(t.artist)

    /**
     * Phone songs minus the ones the NAS already has (same title and artist), so a song you also own on the
     * server, or a copy of your library on the phone, doesn't show twice in mixed results.
     */
    fun withoutNasCopies(local: List<Track>, nas: List<Track>): List<Track> {
        if (nas.isEmpty()) return local
        val have = nas.filterNot { isLocal(it.id) }.mapTo(HashSet(), ::key)
        return local.filterNot { key(it) in have }
    }

    /** Every word of [query] must appear in title, artist or album. Title matches first. */
    fun search(tracks: List<Track>, query: String, limit: Int = 50): List<Track> {
        val words = norm(query).split(' ').filter { it.isNotEmpty() }
        if (words.isEmpty()) return emptyList()
        val hits = tracks.filter { t ->
            val hay = norm("${t.title} ${t.artist} ${t.album}")
            words.all { hay.contains(it) }
        }
        val q = norm(query)
        fun rank(t: Track): Int {
            val title = norm(t.title)
            return when {
                title.startsWith(q) -> 0
                words.all { title.contains(it) } -> 1
                words.any { title.contains(it) } -> 2
                else -> 3
            }
        }
        return hits.sortedWith(compareBy<Track>(::rank, { it.title.lowercase() })).take(limit)
    }

    /** title | artist | album | added (newest first; MediaStore ids grow as files are added). */
    fun sort(tracks: List<Track>, by: String): List<Track> = when (by) {
        "artist" -> tracks.sortedWith(compareBy({ it.artist.lowercase() }, { it.album.lowercase() }, { it.discNo ?: 0 }, { it.trackNo ?: 0 }))
        "album" -> tracks.sortedWith(compareBy({ it.album.lowercase() }, { it.discNo ?: 0 }, { it.trackNo ?: 0 }))
        "added" -> tracks.sortedByDescending { mediaStoreId(it.id) ?: 0L }
        else -> tracks.sortedBy { it.title.lowercase() }
    }

    fun albums(tracks: List<Track>): List<LocalAlbum> =
        tracks.groupBy { it.albumId }.map { (id, ts) ->
            val ordered = ts.sortedWith(compareBy({ it.discNo ?: 0 }, { it.trackNo ?: 0 }, { it.title.lowercase() }))
            val artists = ts.map { it.artist }.distinct()
            LocalAlbum(id, ts.first().album, if (artists.size == 1) artists[0] else "Various artists", ts.mapNotNull { it.year }.maxOrNull(), ordered)
        }.sortedBy { it.title.lowercase() }

    fun artists(tracks: List<Track>): List<LocalArtist> =
        tracks.groupBy { it.artistId }.map { (id, ts) -> LocalArtist(id, ts.first().artist, ts.size, ts.map { it.albumId }.distinct().size) }
            .sortedBy { it.name.lowercase() }
}
