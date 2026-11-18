package org.nasmusic.app.data

import org.nasmusic.app.data.api.Feed
import org.nasmusic.app.data.api.Mix
import org.nasmusic.app.data.api.NasMusicApi
import org.nasmusic.app.data.api.Pin
import org.nasmusic.app.data.api.Queue
import org.nasmusic.app.data.api.Shelf
import org.nasmusic.app.data.api.ShelfItem
import org.nasmusic.app.data.api.Suggestion
import org.nasmusic.app.data.api.ToGetItem
import org.nasmusic.app.data.api.Track
import retrofit2.HttpException

/**
 * Home / For You feeds (docs/v3-ux.md §3.1–3.2). Uses `/home` and `/foryou` when the server has
 * them; until then (404) builds the same shelves from v2 endpoints so the screens work today.
 */
class FeedRepository(private val api: NasMusicApi) {

    suspend fun home(): Feed = try {
        api.home()
    } catch (e: HttpException) {
        if (e.code() != 404 && e.code() != 405) throw e
        Feed(
            listOfNotNull(
                runCatching { api.smartList("recently_played", limit = 60) }.getOrNull()
                    ?.let { Shelf("recent", "Recently played", recentAlbums(it), layout = "grid2") },
                runCatching { api.queues() }.getOrNull()?.let { Shelf("continue", "Continue listening", continueItems(it)) },
                runCatching { api.pins() }.getOrNull()?.let { Shelf("pinned", "Pinned", pinItems(it)) },
            ),
        )
    }

    suspend fun forYou(): Feed = try {
        api.forYou()
    } catch (e: HttpException) {
        if (e.code() != 404 && e.code() != 405) throw e
        Feed(
            listOfNotNull(
                runCatching { api.mixesToday() }.getOrNull()?.let { Shelf("mixes", "Your mixes", mixItems(it)) },
                Shelf("radio", "Radio", listOf(ShelfItem("radio", "random", "Random radio", "From your taste", seed = org.nasmusic.app.data.api.RadioSeed("random")))),
                runCatching { api.suggestions() }.getOrNull()?.let { Shelf("suggestions", "Suggestions", suggestionItems(it)) },
                runCatching { api.toGet() }.getOrNull()?.let { Shelf("toget", "To get", toGetItems(it)) },
            ),
        )
    }

    companion object {
        /** Distinct albums of recently played tracks, most recent first (max [max]). */
        fun recentAlbums(tracks: List<Track>, max: Int = 12): List<ShelfItem> =
            tracks.distinctBy { it.albumId }.take(max).map { ShelfItem("album", it.albumId, it.album, it.artist, albumId = it.albumId) }

        /** Queues left part-way through. */
        fun continueItems(queues: List<Queue>): List<ShelfItem> = queues
            .filter { q -> q.trackCount > 1 && q.currentIndex < q.trackCount - 1 && (q.currentIndex > 0 || q.positionMs > 0) }
            .sortedByDescending { it.updatedAt }
            .map { q ->
                ShelfItem(
                    "queue", q.id, q.name, "${q.currentIndex + 1} of ${q.trackCount}",
                    albumId = q.tracks.getOrNull(q.currentIndex)?.albumId,
                    progress = (q.currentIndex + 1f) / q.trackCount,
                )
            }

        fun pinItems(pins: List<Pin>): List<ShelfItem> =
            pins.map { ShelfItem(it.type, it.id, it.title.ifBlank { it.type }, it.subtitle, albumId = it.albumId ?: it.id.takeIf { _ -> it.type == "album" }) }

        fun mixItems(mixes: List<Mix>): List<ShelfItem> = mixes.map { m ->
            ShelfItem("mix", m.id, m.title, "${m.tracks.size} songs", albumId = m.tracks.firstOrNull()?.track?.albumId)
        }

        fun suggestionItems(list: List<Suggestion>): List<ShelfItem> = list.filter { it.status == "new" || it.status == "want" }.map { s ->
            ShelfItem("suggestion", s.id, s.album, s.artist, artPath = if (s.hasCover) "suggestions/${s.id}/cover?size=256" else null)
        }

        fun toGetItems(list: List<ToGetItem>): List<ShelfItem> = list.filter { it.status == "want" }.map { t ->
            ShelfItem("toget", t.id, t.title, t.artist, artPath = t.catalogId?.takeIf { _ -> t.hasCover }?.let { "catalog/$it/cover?size=256" })
        }
    }
}
