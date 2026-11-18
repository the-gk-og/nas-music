package org.nasmusic.app.data

import org.nasmusic.app.data.api.Mix
import org.nasmusic.app.data.api.NasMusicApi
import org.nasmusic.app.data.api.Queue
import org.nasmusic.app.data.api.RadioSeed
import org.nasmusic.app.data.api.Shelf
import org.nasmusic.app.data.api.ShelfFeed
import org.nasmusic.app.data.api.ShelfItem
import org.nasmusic.app.data.api.ShelfProgress
import org.nasmusic.app.data.api.Suggestion
import org.nasmusic.app.data.api.ToGetItem
import org.nasmusic.app.data.api.Track
import retrofit2.HttpException

/**
 * Home / For You feeds (docs/v3-ux.md §3.1–3.2). Uses `/home` and `/foryou`; on a server without
 * them (404) builds the same shelf kinds from v2 endpoints so the screens still work.
 */
class FeedRepository(private val api: NasMusicApi) {

    suspend fun home(): ShelfFeed = try {
        api.home()
    } catch (e: HttpException) {
        if (e.code() != 404 && e.code() != 405) throw e
        ShelfFeed(
            listOfNotNull(
                runCatching { api.smartList("recently_played", limit = 60) }.getOrNull()
                    ?.let { Shelf("recently_played", "recently_played", "Recently played", layout = "grid2", items = recentAlbums(it)) },
                runCatching { api.queues() }.getOrNull()?.let { Shelf("continue_listening", "continue_listening", "Continue listening", items = continueItems(it)) },
                runCatching { api.pins() }.getOrNull()?.let { Shelf("pinned", "pinned", "Pinned", items = it) },
            ),
        )
    }

    suspend fun forYou(): ShelfFeed = try {
        api.forYou()
    } catch (e: HttpException) {
        if (e.code() != 404 && e.code() != 405) throw e
        ShelfFeed(
            listOfNotNull(
                runCatching { api.mixesToday() }.getOrNull()?.let { Shelf("mixes", "mixes", "Your mixes", items = mixItems(it)) },
                Shelf("radio", "radio", "Radio", items = listOf(ShelfItem("radio", "random", "Random radio", "From your taste", seed = RadioSeed("random")))),
                runCatching { api.suggestions() }.getOrNull()?.let { Shelf("suggestions", "suggestions", "Suggestions", items = suggestionItems(it)) },
                runCatching { api.toGet() }.getOrNull()?.let { Shelf("to_get", "to_get", "To get", items = toGetItems(it)) },
            ),
        )
    }

    companion object {
        fun art(albumId: String?) = albumId?.takeIf { it.isNotBlank() }?.let { "art/$it" }

        /** Distinct albums of recently played tracks, most recent first (max [max]). */
        fun recentAlbums(tracks: List<Track>, max: Int = 12): List<ShelfItem> =
            tracks.distinctBy { it.albumId }.take(max).map { ShelfItem("album", it.albumId, it.album, it.artist, artUrl = art(it.albumId)) }

        /** Queues left part-way through. */
        fun continueItems(queues: List<Queue>): List<ShelfItem> = queues
            .filter { q -> q.trackCount > 1 && q.currentIndex < q.trackCount - 1 && (q.currentIndex > 0 || q.positionMs > 0) }
            .sortedByDescending { it.updatedAt }
            .map { q ->
                ShelfItem(
                    "queue", q.id, q.name, "${q.currentIndex + 1} of ${q.trackCount}",
                    artUrl = art(q.tracks.getOrNull(q.currentIndex)?.albumId),
                    queue = q,
                    progress = ShelfProgress(q.currentIndex, q.trackCount, q.positionMs, q.id),
                )
            }

        fun mixItems(mixes: List<Mix>): List<ShelfItem> = mixes.map { m ->
            ShelfItem("mix", m.id, m.title, "${m.tracks.size} songs", artUrl = art(m.tracks.firstOrNull()?.track?.albumId))
        }

        fun suggestionItems(list: List<Suggestion>): List<ShelfItem> = list.filter { it.status == "new" || it.status == "want" }.map { s ->
            ShelfItem("suggestion", s.id, s.album, s.artist, artUrl = if (s.hasCover) "suggestions/${s.id}/cover" else null, suggestion = s)
        }

        fun toGetItems(list: List<ToGetItem>): List<ShelfItem> = list.filter { it.status == "want" }.map { t ->
            ShelfItem("toget", t.id, t.title, t.artist, artUrl = t.artUrl, toget = t)
        }
    }
}
