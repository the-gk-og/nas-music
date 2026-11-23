package org.nasmusic.app.playback

import android.os.Bundle
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.session.MediaConstants
import androidx.media3.session.MediaLibraryService.LibraryParams
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.withTimeout
import org.nasmusic.app.data.api.NasMusicApi
import org.nasmusic.app.data.api.Queue
import org.nasmusic.app.data.api.RadioContext
import org.nasmusic.app.data.api.RadioRequest
import org.nasmusic.app.data.api.RadioSeed
import org.nasmusic.app.data.api.SearchResult
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.data.api.VibeParseRequest

/**
 * Browse tree for Android Auto (and any other MediaBrowser client: Android Automotive, Wear, Bluetooth
 * head units), built only from AndroidX Media3.
 *
 * Android Auto shows the root's children as tabs, and it only accepts *browsable* root children, at most
 * [MediaConstants.EXTRAS_KEY_ROOT_CHILDREN_LIMIT] of them (usually 4). A playable item at the root makes
 * Auto drop the tree and show only the now-playing card, so the root is all folders:
 *
 *   flat (no limit / ≥ 7):  For you · Continue listening · Playlists · Albums · Artists · Radio · Downloads
 *   compact (Auto, ≤ 6):    For you · Library (Playlists, Albums, Artists) · Radio · Downloads
 *
 *   For you    ─ "Play something for now" (radio:moment) · continue-listening queues · today's mixes
 *   Radio      ─ radio:moment, radio:random (playable)
 *   artist:<id>─ artist radio + album:<id> ─ tracks
 *
 * Playable track entries have the id `play|<context>|<trackId>` so that tapping one plays the whole
 * context (album, playlist, mix, downloads, search results) from that track; [expand] turns it back into
 * tracks. Server failures become a single explanatory row (not an error, which Auto shows as a dead end)
 * and the parent is remembered in [failed] so the service can ask Auto to reload it when the network
 * comes back.
 */
class AutoLibrary(
    private val api: () -> NasMusicApi,
    /** Smart-shuffle hints (local time, car mode, output). */
    private val hints: () -> RadioContext? = { null },
    /** Completed downloads (from the local database), so the Downloads tab works offline. */
    private val downloads: suspend () -> List<Track> = { emptyList() },
    /** False until the app is paired with a server. */
    private val paired: () -> Boolean = { true },
    private val timeoutMs: Long = 12_000,
) {
    data class Expanded(
        val tracks: List<Track>, val startIndex: Int, val queueName: String, val source: PlaySource,
        val mixId: String? = null, val radioQueueId: String? = null,
        /** Set when resuming a server queue ("Continue listening"): reuse it instead of creating one. */
        val queueId: String? = null, val startPositionMs: Long = 0,
    )

    private var lastSearch: Pair<String, SearchResult>? = null

    /** Root-children limit each browser asked for in its root hints (keyed by package). */
    private val rootLimits = ConcurrentHashMap<String, Int>()

    /** Parents whose last load failed (server unreachable); reloaded when the route changes. */
    val failed: MutableSet<String> = ConcurrentHashMap.newKeySet()

    fun root(): MediaItem = folder(ROOT, "nas-music", MediaMetadata.MEDIA_TYPE_FOLDER_MIXED)

    fun recentRoot(): MediaItem = folder(RECENT_ROOT, "Recent", MediaMetadata.MEDIA_TYPE_FOLDER_MIXED)

    /** Remembers the browser's root hints; returns the params to send back with the root. */
    fun onRoot(browserKey: String, params: LibraryParams?): LibraryParams {
        val limit = params?.extras?.getInt(MediaConstants.EXTRAS_KEY_ROOT_CHILDREN_LIMIT, 0) ?: 0
        rootLimits[browserKey] = limit
        return rootParams(params?.isRecent == true)
    }

    suspend fun children(parentId: String, browserKey: String = ""): List<MediaItem> {
        if (parentId == ROOT) return rootChildren(rootLimits[browserKey] ?: 0)
        if (parentId == LIBRARY) return libraryChildren()
        if (parentId == RADIO) return RADIOS.map { (id, title) -> playable(id, title, MediaMetadata.MEDIA_TYPE_RADIO_STATION) }
        if (parentId == DOWNLOADS) return downloadItems()
        if (!paired()) return listOf(message(NOT_PAIRED_ID, "Open nas-music on your phone", "Connect it to your server first"))
        return try {
            withTimeout(timeoutMs) { remoteChildren(parentId) }.also { failed.remove(parentId) }
        } catch (e: Exception) {
            if (e is kotlinx.coroutines.CancellationException && e !is kotlinx.coroutines.TimeoutCancellationException) throw e
            failed += parentId
            listOf(message(OFFLINE_ID, "Can't reach your music server", "Downloads still play offline"))
        }
    }

    private suspend fun remoteChildren(parentId: String): List<MediaItem> {
        val a = api()
        return when {
            parentId == FOR_YOU -> forYou()
            parentId == CONTINUE -> a.queues().let(::recentQueues).map(::queueItem)
            parentId == PLAYLISTS -> a.playlists().map {
                folder("playlist:${it.id}", it.name, MediaMetadata.MEDIA_TYPE_PLAYLIST, subtitle = songs(it.trackCount),
                    playableStyle = LIST)
            }
            parentId == ALBUMS -> a.albums(sort = "name", limit = 500).map {
                folder("album:${it.id}", it.title, MediaMetadata.MEDIA_TYPE_ALBUM, subtitle = it.artist, art = it.id,
                    playableStyle = LIST)
            }
            parentId == ARTISTS -> a.artists().map {
                folder("artist:${it.id}", it.name, MediaMetadata.MEDIA_TYPE_ARTIST,
                    subtitle = if (it.albumCount == 1) "1 album" else "${it.albumCount} albums", browsableStyle = GRID)
            }
            parentId.startsWith("artist:") -> {
                val d = a.artist(parentId.removePrefix("artist:"))
                listOf(playable("radio:$parentId", "${d.name} radio", MediaMetadata.MEDIA_TYPE_RADIO_STATION)) +
                    d.albums.map {
                        folder("album:${it.id}", it.title, MediaMetadata.MEDIA_TYPE_ALBUM, subtitle = it.year?.toString(),
                            art = it.id, playableStyle = LIST)
                    }
            }
            parentId.startsWith("album:") || parentId.startsWith("playlist:") || parentId.startsWith("mix:") ->
                contextTracks(parentId).tracks.map { trackItem(parentId, it) }
            else -> emptyList()
        }
    }

    /** "Play something for now", up to three queues to pick up again, today's mixes. Each part may fail alone. */
    private suspend fun forYou(): List<MediaItem> {
        val a = api()
        val start = playable(MOMENT, "Play something for now", MediaMetadata.MEDIA_TYPE_RADIO_STATION,
            subtitle = "Smart shuffle for right now", group = "Start")
        val queues = runCatching { recentQueues(a.queues()).take(3).map { queueItem(it, group = "Continue listening") } }
        val mixes = runCatching {
            a.mixesToday().map {
                folder("mix:${it.id}", it.title, MediaMetadata.MEDIA_TYPE_PLAYLIST, subtitle = songs(it.tracks.size),
                    art = it.tracks.firstOrNull()?.track?.albumId, group = "Today's mixes", playableStyle = LIST)
            }
        }
        // Both failing means the server is unreachable: let the caller show that (and retry later).
        if (queues.isFailure && mixes.isFailure) throw mixes.exceptionOrNull()!!
        return listOf(start) + queues.getOrDefault(emptyList()) + mixes.getOrDefault(emptyList())
    }

    private fun libraryChildren() = listOf(
        folder(PLAYLISTS, "Playlists", MediaMetadata.MEDIA_TYPE_FOLDER_PLAYLISTS, browsableStyle = LIST),
        folder(ALBUMS, "Albums", MediaMetadata.MEDIA_TYPE_FOLDER_ALBUMS, browsableStyle = GRID),
        folder(ARTISTS, "Artists", MediaMetadata.MEDIA_TYPE_FOLDER_ARTISTS, browsableStyle = GRID),
    )

    private suspend fun downloadItems(): List<MediaItem> {
        val tracks = runCatching { downloads() }.getOrDefault(emptyList())
        if (tracks.isEmpty()) return listOf(message(EMPTY_ID, "No downloads yet", "Songs you keep offline show up here"))
        return tracks.map { trackItem(DOWNLOADS, it) }
    }

    /** Children of the "recent" root (System UI media resumption): the queue to pick up again. */
    suspend fun recentChildren(): List<MediaItem> = runCatching {
        withTimeout(timeoutMs) { recentQueues(api().queues()).take(1).map(::queueItem) }
    }.getOrDefault(emptyList())

    /** Songs, albums and artists, grouped; cached so [expand] can play the same list. */
    suspend fun search(query: String): List<MediaItem> {
        val r = lastSearch?.takeIf { it.first == query }?.second
            ?: runCatching { withTimeout(timeoutMs) { api().search(query, 40) } }.getOrNull()?.also { lastSearch = query to it }
            ?: return emptyList()
        return searchItems(query, r)
    }

    /** One entry for anything the browser asks about by id (Media3 `onGetItem`). */
    suspend fun item(mediaId: String): MediaItem? = when {
        mediaId == ROOT -> root()
        mediaId == RECENT_ROOT -> recentRoot()
        mediaId in STATIC_FOLDERS -> (rootChildren(0) + libraryChildren()).firstOrNull { it.mediaId == mediaId }
        mediaId.startsWith("radio:") -> RADIOS.firstOrNull { it.first == mediaId }
            ?.let { playable(it.first, it.second, MediaMetadata.MEDIA_TYPE_RADIO_STATION) }
            ?: playable(mediaId, "Radio", MediaMetadata.MEDIA_TYPE_RADIO_STATION)
        else -> {
            val trackId = parsePlayId(mediaId)?.second ?: mediaId
            runCatching { withTimeout(timeoutMs) { api().track(trackId) } }.getOrNull()
                ?.let { t -> parsePlayId(mediaId)?.let { trackItem(it.first, t) } ?: MediaItems.fromTrack(t, PlaySource.LIBRARY) }
        }
    }

    /** Turn a browse/search item into what to play. Null = not ours (a plain track id). */
    suspend fun expand(mediaId: String): Expanded? {
        if (mediaId.startsWith("radio:")) return radio(mediaId.removePrefix("radio:"))
        if (mediaId.startsWith("queue:")) return resumeQueue(mediaId.removePrefix("queue:"))
        val (ctx, trackId) = parsePlayId(mediaId) ?: return null
        val e = when {
            ctx.startsWith("search:") -> {
                val q = ctx.removePrefix("search:")
                val tracks = lastSearch?.takeIf { it.first == q }?.second?.tracks ?: api().search(q, 40).tracks
                Expanded(tracks, 0, "Search: $q", PlaySource.SEARCH)
            }
            ctx == DOWNLOADS -> Expanded(downloads(), 0, "Downloads", PlaySource.LIBRARY)
            else -> contextTracks(ctx)
        }
        return e.copy(startIndex = e.tracks.indexOfFirst { it.id == trackId }.coerceAtLeast(0))
    }

    /** "Play <query>" by voice: an artist → artist radio, an album → the album, else matching songs. */
    suspend fun voice(query: String): Expanded? {
        val q = norm(query)
        if (query.isBlank() || q in SOMETHING) return radio("moment")
        val r = runCatching { api().search(query, 20) }.getOrNull() ?: return null
        r.artists.firstOrNull { norm(it.name) == q }?.let { return radio("artist:${it.id}", "${it.name} radio") }
        r.albums.firstOrNull { norm(it.title) == q || norm("${it.title} ${it.artist}") == q }?.let { return contextTracks("album:${it.id}") }
        // "Play something chill", "play gym music, no slow songs": a describe-a-vibe station, unless a song is
        // called exactly that.
        if (r.tracks.none { norm(it.title) == q }) vibe(query)?.let { return it }
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

    private suspend fun resumeQueue(id: String): Expanded? {
        val q = runCatching { api().queue(id) }.getOrNull() ?: return null
        if (q.tracks.isEmpty()) return null
        val idx = q.currentIndex.coerceIn(0, q.tracks.lastIndex)
        return Expanded(q.tracks, idx, q.name, PlaySource.LIBRARY, queueId = q.id, startPositionMs = q.positionMs.coerceAtLeast(0))
    }

    private suspend fun radio(seed: String, title: String? = null): Expanded? {
        val (type, id) = seed.split(':', limit = 2).let { it[0] to it.getOrNull(1) }
        val ctx = if (type == "moment") hints() else null
        val q = runCatching { api().startRadio(RadioRequest(RadioSeed(type, id), ctx)) }.getOrNull() ?: return null
        return Expanded(q.tracks, 0, title ?: q.name, PlaySource.MIX, radioQueueId = q.id)
    }

    /** Describe-a-vibe from a voice query; null when nothing in it was understood as a vibe. */
    private suspend fun vibe(query: String): Expanded? {
        val text = query.trim().removePrefix("something").trim()
        val parsed = runCatching { api().parseVibe(VibeParseRequest(text)) }.getOrNull() ?: return null
        if (parsed.constraints.none { it.kind != "genre" && it.kind != "like" }) return null
        val q = runCatching { api().startRadio(RadioRequest(RadioSeed("vibe", text = text, constraints = parsed.constraints))) }
            .getOrNull() ?: return null
        return Expanded(q.tracks, 0, q.name, PlaySource.MIX, radioQueueId = q.id)
    }

    private fun queueItem(q: Queue): MediaItem = queueItem(q, null)

    private fun queueItem(q: Queue, group: String?): MediaItem {
        val cur = q.tracks.getOrNull(q.currentIndex)
        return playable("queue:${q.id}", q.name, MediaMetadata.MEDIA_TYPE_PLAYLIST,
            subtitle = cur?.let { "${it.title} · ${it.artist}" } ?: songs(q.trackCount),
            art = cur?.albumId ?: q.tracks.firstOrNull()?.albumId, group = group)
    }

    companion object {
        const val ROOT = "root"
        const val RECENT_ROOT = "recent"
        const val FOR_YOU = "foryou"
        const val CONTINUE = "continue"
        const val LIBRARY = "library"
        const val PLAYLISTS = "playlists"
        const val ALBUMS = "albums"
        const val ARTISTS = "artists"
        const val RADIO = "radio"
        const val DOWNLOADS = "downloads"
        const val MOMENT = "radio:moment"
        const val OFFLINE_ID = "msg:offline"
        const val NOT_PAIRED_ID = "msg:unpaired"
        const val EMPTY_ID = "msg:empty"
        val STATIC_FOLDERS = setOf(FOR_YOU, CONTINUE, LIBRARY, PLAYLISTS, ALBUMS, ARTISTS, RADIO, DOWNLOADS)
        val RADIOS = listOf(MOMENT to "Play something for now", "radio:random" to "Your radio")
        /** Voice queries that mean "just play something" → the smart shuffle. */
        val SOMETHING = setOf("something", "some music", "music", "anything", "something for now", "something good")

        private const val LIST = MediaConstants.EXTRAS_VALUE_CONTENT_STYLE_LIST_ITEM
        private const val GRID = MediaConstants.EXTRAS_VALUE_CONTENT_STYLE_GRID_ITEM
        /** Root children at or above this limit are shown as one flat level. */
        const val FLAT_ROOT_SIZE = 7

        /** Content-style defaults for the whole tree (lists), plus the recent flag when it was asked for. */
        fun rootParams(recent: Boolean): LibraryParams = LibraryParams.Builder()
            .setRecent(recent)
            .setExtras(Bundle().apply {
                putInt(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_BROWSABLE, LIST)
                putInt(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_PLAYABLE, LIST)
            })
            .build()

        /** The root's children: all browsable, never more than [limit] (0 = the browser set no limit). */
        fun rootChildren(limit: Int): List<MediaItem> {
            val forYou = folder(FOR_YOU, "For you", MediaMetadata.MEDIA_TYPE_FOLDER_MIXED, browsableStyle = GRID)
            val radio = folder(RADIO, "Radio", MediaMetadata.MEDIA_TYPE_FOLDER_RADIO_STATIONS)
            val downloads = folder(DOWNLOADS, "Downloads", MediaMetadata.MEDIA_TYPE_FOLDER_MIXED)
            if (limit <= 0 || limit >= FLAT_ROOT_SIZE) return listOf(
                forYou,
                folder(CONTINUE, "Continue listening", MediaMetadata.MEDIA_TYPE_FOLDER_PLAYLISTS),
                folder(PLAYLISTS, "Playlists", MediaMetadata.MEDIA_TYPE_FOLDER_PLAYLISTS),
                folder(ALBUMS, "Albums", MediaMetadata.MEDIA_TYPE_FOLDER_ALBUMS, browsableStyle = GRID),
                folder(ARTISTS, "Artists", MediaMetadata.MEDIA_TYPE_FOLDER_ARTISTS, browsableStyle = GRID),
                radio,
                downloads,
            )
            return listOf(forYou, folder(LIBRARY, "Library", MediaMetadata.MEDIA_TYPE_FOLDER_MIXED), radio, downloads).take(limit)
        }

        /** Most recently used first; radio queues and empty ones are left out. */
        fun recentQueues(qs: List<Queue>): List<Queue> =
            qs.filter { it.trackCount > 0 || it.tracks.isNotEmpty() }.filter { it.radio == null }
                .sortedByDescending { it.updatedAt.orEmpty() }

        fun searchItems(query: String, r: SearchResult): List<MediaItem> =
            r.tracks.map { trackItem("search:$query", it, group = "Songs") } +
                r.albums.take(10).map {
                    folder("album:${it.id}", it.title, MediaMetadata.MEDIA_TYPE_ALBUM, subtitle = it.artist, art = it.id,
                        group = "Albums", playableStyle = LIST)
                } +
                r.artists.take(10).map { folder("artist:${it.id}", it.name, MediaMetadata.MEDIA_TYPE_ARTIST, group = "Artists") }

        fun trackItem(ctx: String, t: Track, group: String? = null): MediaItem {
            val base = MediaItems.fromTrack(t, PlaySource.LIBRARY)
            val md = base.mediaMetadata.buildUpon().setSubtitle(t.artist).apply {
                if (group != null) setExtras(Bundle(base.mediaMetadata.extras ?: Bundle()).apply {
                    putString(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_GROUP_TITLE, group)
                })
            }.build()
            return base.buildUpon().setMediaId(playId(ctx, t.id)).setMediaMetadata(md).build()
        }

        fun folder(
            id: String, title: String, type: Int, subtitle: String? = null, art: String? = null, group: String? = null,
            browsableStyle: Int? = null, playableStyle: Int? = null,
        ): MediaItem = MediaItem.Builder().setMediaId(id).setMediaMetadata(
            MediaMetadata.Builder().setTitle(title).setSubtitle(subtitle).setIsBrowsable(true).setIsPlayable(false)
                .setMediaType(type)
                .apply { art?.let { MediaItems.artUri(it, 256) }?.let { setArtworkUri(it) } }
                .setExtras(styleExtras(group, browsableStyle, playableStyle))
                .build(),
        ).build()

        fun playable(id: String, title: String, type: Int, subtitle: String? = null, art: String? = null, group: String? = null): MediaItem =
            MediaItem.Builder().setMediaId(id).setMediaMetadata(
                MediaMetadata.Builder().setTitle(title).setSubtitle(subtitle).setIsBrowsable(false).setIsPlayable(true)
                    .setMediaType(type)
                    .apply { art?.let { MediaItems.artUri(it, 256) }?.let { setArtworkUri(it) } }
                    .setExtras(styleExtras(group, null, null))
                    .build(),
            ).build()

        /** A row that only says something (offline, not paired, empty): neither browsable nor playable. */
        fun message(id: String, title: String, subtitle: String): MediaItem =
            MediaItem.Builder().setMediaId(id).setMediaMetadata(
                MediaMetadata.Builder().setTitle(title).setSubtitle(subtitle).setIsBrowsable(false).setIsPlayable(false)
                    .setMediaType(MediaMetadata.MEDIA_TYPE_MIXED).build(),
            ).build()

        private fun styleExtras(group: String?, browsable: Int?, playable: Int?): Bundle? {
            if (group == null && browsable == null && playable == null) return null
            return Bundle().apply {
                group?.let { putString(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_GROUP_TITLE, it) }
                browsable?.let { putInt(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_BROWSABLE, it) }
                playable?.let { putInt(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_PLAYABLE, it) }
            }
        }

        private fun songs(n: Int) = if (n == 1) "1 song" else "$n songs"

        fun playId(ctx: String, trackId: String) = "play|$ctx|$trackId"

        /** `play|<context>|<trackId>` → (context, trackId). The context may itself contain '|' (search text). */
        fun parsePlayId(id: String): Pair<String, String>? {
            if (!id.startsWith("play|")) return null
            val rest = id.removePrefix("play|")
            val cut = rest.lastIndexOf('|')
            if (cut <= 0 || cut == rest.length - 1) return null
            return rest.substring(0, cut) to rest.substring(cut + 1)
        }

        /** The track a browse id plays (for fallbacks when the context can't be loaded). */
        fun trackIdOf(mediaId: String): String = parsePlayId(mediaId)?.second ?: mediaId

        fun norm(s: String) = s.lowercase().replace(Regex("[^\\p{L}\\p{N}]+"), " ").trim()
    }
}
