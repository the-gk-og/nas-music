package org.homebeat.app.spoken

import android.net.Uri
import android.os.Bundle
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import java.io.File
import kotlinx.serialization.Serializable
import org.homebeat.app.data.api.ApiFactory
import org.homebeat.app.data.api.Audiobook
import org.homebeat.app.data.api.Episode
import org.homebeat.app.data.api.SpokenApi
import org.homebeat.app.data.api.SpokenChapter
import org.homebeat.app.data.api.Station
import org.homebeat.app.net.RoutedCallFactory

/** A podcast episode, audiobook or internet radio station, as the main player plays it. */
@Serializable
data class SpokenItem(
    val kind: String, // episode | book | station
    val id: String,
    val title: String,
    val subtitle: String,
    val artUrl: String,
    val durationS: Double?,
    val chapters: List<SpokenChapter> = emptyList(),
    val partStarts: List<Double> = listOf(0.0),
    val partDurations: List<Double> = emptyList(),
    val showId: String? = null,
    /** Playback speed for this item (episodes: the show's; books: the book's). */
    val speed: Float = 1f,
    val skipSilence: Boolean = false,
) {
    val live: Boolean get() = kind == "station"
}

/**
 * Podcasts, audiobooks and internet radio go through the same PlaybackService, queue, notification, Connect and
 * Cast as music (api 0.8.0). They stay apart from music features: [isSpoken] items never produce listening
 * events, scrobbles, levelling/crossfade/DJ transitions, and never go into the server's music queues.
 *
 * MediaIds: `ep:<id>`, `book:<id>:<part>`, `st:<id>`. Each MediaItem carries the whole [SpokenItem] as JSON in its
 * extras (chapters, part layout, speed) and its part index, so the service and every UI can work from the item.
 */
object SpokenMedia {
    const val EXTRA_SPOKEN = "nas.spoken"
    const val EXTRA_PART = "nas.spokenPart"

    fun isSpoken(item: MediaItem?): Boolean = item?.mediaMetadata?.extras?.containsKey(EXTRA_SPOKEN) == true ||
        item?.mediaId?.let { it.startsWith("ep:") || it.startsWith("book:") || it.startsWith("st:") } == true

    fun item(mi: MediaItem?): SpokenItem? = mi?.mediaMetadata?.extras?.getString(EXTRA_SPOKEN)?.let {
        runCatching { ApiFactory.json.decodeFromString(SpokenItem.serializer(), it) }.getOrNull()
    }

    fun part(mi: MediaItem?): Int = mi?.mediaMetadata?.extras?.getInt(EXTRA_PART, 0) ?: 0

    /** Position in the whole episode/book (seconds) for [positionMs] inside [mi]. */
    fun bookPositionS(item: SpokenItem, part: Int, positionMs: Long): Double =
        (item.partStarts.getOrNull(part) ?: 0.0) + positionMs / 1000.0

    fun mediaId(kind: String, id: String, part: Int = 0) = when (kind) {
        "episode" -> "ep:$id"
        "book" -> "book:$id:$part"
        else -> "st:$id"
    }

    fun art(kind: String, id: String, size: Int = 512) = RoutedCallFactory.virtual("art/$kind/${Uri.encode(id)}?size=$size")

    /** Every MediaItem of [item] (books: one per part), with URIs (a downloaded episode plays from the file). */
    fun mediaItems(item: SpokenItem, localFile: (String) -> File? = { null }): List<MediaItem> {
        val json = ApiFactory.json.encodeToString(SpokenItem.serializer(), item)
        fun meta(part: Int) = MediaMetadata.Builder()
            .setTitle(item.title).setArtist(item.subtitle).setAlbumTitle(item.subtitle)
            .setArtworkUri(item.artUrl.takeIf { it.isNotBlank() }?.let(Uri::parse))
            .setIsPlayable(true).setIsBrowsable(false)
            .setMediaType(
                when (item.kind) {
                    "book" -> MediaMetadata.MEDIA_TYPE_AUDIO_BOOK_CHAPTER
                    "station" -> MediaMetadata.MEDIA_TYPE_RADIO_STATION
                    else -> MediaMetadata.MEDIA_TYPE_PODCAST_EPISODE
                },
            )
            .apply {
                if (item.kind != "station") (item.partDurations.getOrNull(part) ?: item.durationS)?.let { setDurationMs((it * 1000).toLong()) }
            }
            .setExtras(Bundle().apply { putString(EXTRA_SPOKEN, json); putInt(EXTRA_PART, part) })
            .build()
        return when (item.kind) {
            "episode" -> {
                val uri = localFile(item.id)?.let(Uri::fromFile) ?: Uri.parse(RoutedCallFactory.virtual("stream/episode/${Uri.encode(item.id)}"))
                listOf(MediaItem.Builder().setMediaId(mediaId("episode", item.id)).setUri(uri).setMediaMetadata(meta(0)).build())
            }
            "book" -> item.partStarts.indices.map { p ->
                MediaItem.Builder().setMediaId(mediaId("book", item.id, p))
                    .setUri(RoutedCallFactory.virtual("stream/book/${Uri.encode(item.id)}/$p"))
                    .setMediaMetadata(meta(p)).build()
            }
            else -> listOf(
                MediaItem.Builder().setMediaId(mediaId("station", item.id))
                    .setUri(RoutedCallFactory.virtual("stream/station/${Uri.encode(item.id)}"))
                    .setMediaMetadata(meta(0)).build(),
            )
        }
    }

    /** Where to start: (item index, offset ms) for book-wide second [posS]. */
    fun startAt(item: SpokenItem, posS: Double): Pair<Int, Long> {
        if (item.kind == "station") return 0 to 0L
        val (idx, off) = SpokenLogic.partAt(item.partStarts, item.partDurations, posS.coerceAtLeast(0.0))
        return idx to (off * 1000).toLong()
    }

    fun fromEpisode(ep: Episode): SpokenItem = SpokenItem(
        "episode", ep.id, ep.title, ep.podcastTitle.orEmpty(), art("podcast", ep.podcastId), ep.durationS, ep.chapters,
        showId = ep.podcastId, speed = ep.speed.toFloat(), skipSilence = ep.skipSilence,
    )

    fun fromBook(b: Audiobook): SpokenItem = SpokenItem(
        "book", b.id, b.title, listOfNotNull(b.author, b.narrator?.let { "read by $it" }).joinToString(" · "),
        art("book", b.id), b.durationS,
        chapters = b.chapters.map { SpokenChapter(it.startS, it.title) },
        partStarts = b.parts.map { it.startS }.ifEmpty { listOf(0.0) }, partDurations = b.parts.map { it.durationS },
        speed = (b.progress?.speed ?: 1.0).toFloat(),
    )

    fun fromStation(s: Station): SpokenItem = SpokenItem(
        "station", s.id, s.name,
        listOfNotNull(s.country, *s.tags.take(2).toTypedArray()).joinToString(" · ").ifBlank { "Internet radio" },
        if (s.hasImage) art("station", s.id, 256) else "", null,
    )

    /**
     * Look an item up by kind + id (Connect `media`, a takeover, Continue listening), with its saved resume point
     * in seconds (0 when played to the end or never started; a station is always 0).
     */
    suspend fun load(api: SpokenApi, kind: String, id: String): Pair<SpokenItem, Double>? = runCatching {
        when (kind) {
            "episode" -> api.episode(id).let { fromEpisode(it) to (it.progress?.takeIf { p -> !p.played }?.positionS ?: 0.0) }
            "book" -> api.book(id).let { fromBook(it) to (it.progress?.takeIf { p -> !p.played }?.positionS ?: 0.0) }
            "station" -> api.stations().stations.firstOrNull { it.id == id }?.let { fromStation(it) to 0.0 }
            else -> null
        }
    }.getOrNull()
}
