package org.nasmusic.app.data.api

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

// v3 (docs/v3-ux.md), matching the server's openapi.yaml draft (server-core bfc0758).
// Unknown shelf kinds / item types must be skipped by clients.

@Serializable
data class ShelfFeed(val shelves: List<Shelf> = emptyList())

@Serializable
data class Shelf(
    /** Stable id for ordering/hiding, e.g. "recently_played", "byp:album:<id>". */
    val id: String,
    /** start_radio | devices | recently_played | continue_listening | pinned | mixes | radio | because_you_played | release_radar | suggestions | to_get | blends | wrapped */
    val kind: String = id,
    val title: String,
    val subtitle: String? = null,
    /** button | row | grid2 */
    val layout: String? = null,
    val items: List<ShelfItem> = emptyList(),
)

@Serializable
data class ShelfProgress(val index: Int = 0, val count: Int = 0, val positionMs: Long = 0, val queueId: String? = null) {
    val fraction: Float get() = if (count > 0) ((index + 1f) / count).coerceIn(0f, 1f) else 0f
}

@Serializable
data class ShelfDevice(
    val kind: String = "other",
    val canPlay: Boolean = true,
    val isActive: Boolean = false,
    val playing: Boolean = false,
    val nowPlaying: Track? = null,
)

/** A generic card. `type` says what tapping it does; the matching optional object carries details. */
@Serializable
data class ShelfItem(
    /** track | album | artist | playlist | mix | radio | queue | device | catalog | suggestion | toget | smart_list | genre | folder */
    val type: String,
    val id: String,
    val title: String,
    val subtitle: String? = null,
    /** Relative to the API base, e.g. "art/<albumId>" or "catalog/<id>/cover"; null = no art. */
    val artUrl: String? = null,
    val track: Track? = null,
    val album: Album? = null,
    val artist: Artist? = null,
    val playlist: Playlist? = null,
    val queue: Queue? = null,
    val mix: JsonObject? = null,
    val seed: RadioSeed? = null,
    val device: ShelfDevice? = null,
    val progress: ShelfProgress? = null,
    val catalog: CatalogItem? = null,
    val suggestion: Suggestion? = null,
    val toget: ToGetItem? = null,
    val playedAt: String? = null,
    /** v5 release_radar shelves */
    val release: ReleaseInfo? = null,
    val addedAt: String? = null,
    /** v5 blends shelf; status "new" = the "Start a blend" card */
    val blend: ShelfBlend? = null,
    val wrapped: ShelfWrapped? = null,
)

@Serializable
data class RadioSeed(
    /** random | track | artist | album | playlist */
    val type: String,
    val id: String? = null,
    /** Set by the server in responses. */
    val title: String? = null,
)

@Serializable
data class RadioRequest(val seed: RadioSeed)

/** Music from the online catalogue (Deezer + MusicBrainz, proxied). `id` is opaque ("dz:album:302127"). */
@Serializable
data class CatalogItem(
    val id: String,
    /** album | track | artist */
    val type: String = "album",
    val title: String,
    val artist: String? = null,
    val album: String? = null,
    val year: Int? = null,
    val durationS: Double? = null,
    val mbid: String? = null,
    val artUrl: String? = null,
    val hasPreview: Boolean = false,
    val owned: Boolean = false,
    val ownedId: String? = null,
    /** {id, status} when it is already on the To get list. */
    val toGet: JsonObject? = null,
    val links: List<BuyLink> = emptyList(),
)

@Serializable
data class SearchResults(
    val query: String = "",
    val top: ShelfItem? = null,
    val tracks: List<Track> = emptyList(),
    val albums: List<Album> = emptyList(),
    val artists: List<Artist> = emptyList(),
    val playlists: List<Playlist> = emptyList(),
    val queues: List<Queue> = emptyList(),
    /** "Not in your library". */
    val catalog: List<CatalogItem> = emptyList(),
    val catalogError: String? = null,
)

@Serializable
data class ToGetItem(
    val id: String,
    /** album | track */
    val type: String = "album",
    val title: String,
    val artist: String = "",
    val year: Int? = null,
    val mbid: String? = null,
    val catalogId: String? = null,
    val artUrl: String? = null,
    val hasPreview: Boolean = false,
    val note: String? = null,
    /** want | got | dismissed */
    val status: String = "want",
    /** search | suggestion | manual */
    val source: String? = null,
    val suggestionId: String? = null,
    val ownedId: String? = null,
    val createdAt: String? = null,
    val updatedAt: String? = null,
    val gotAt: String? = null,
    val links: List<BuyLink> = emptyList(),
)

/** Either [catalogId] (from search) or [type] + [title] + [artist]. */
@Serializable
data class NewToGet(
    val catalogId: String? = null,
    val type: String? = null,
    val title: String? = null,
    val artist: String? = null,
    val year: Int? = null,
    val mbid: String? = null,
    val note: String? = null,
)

@Serializable
data class ToGetPatch(val status: String? = null, val note: String? = null)

@Serializable
data class NewPin(val type: String, val id: String, val position: Int? = null)

