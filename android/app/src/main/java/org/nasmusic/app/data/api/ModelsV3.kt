package org.nasmusic.app.data.api

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

// v3 (docs/v3-ux.md). Written before the server's openapi.yaml had these endpoints: every field
// except id/title is optional so the app tolerates the final shapes; adjust when the contract lands.

/** One card on a Home / For You shelf. */
@Serializable
data class ShelfItem(
    /** album | playlist | mix | radio | artist | track | queue | suggestion | toget | smart_list */
    val type: String,
    val id: String,
    val title: String,
    val subtitle: String? = null,
    /** Album whose art to show (our /art endpoint). */
    val albumId: String? = null,
    /** Catalogue/suggestion art proxied by the server (relative API path, e.g. "suggestions/s1/cover"). */
    val artPath: String? = null,
    /** 0..1 for "Continue listening". */
    val progress: Float? = null,
    /** For radio cards: the seed to start. */
    val seed: RadioSeed? = null,
    val track: Track? = null,
)

@Serializable
data class Shelf(
    /** start_radio | devices | recent | continue | pinned | mixes | radio | because | release_radar | suggestions | toget | … */
    val id: String,
    val title: String,
    val items: List<ShelfItem> = emptyList(),
    /** Layout hint: "row" (default), "grid2" (2-row quick picks), "button". */
    val layout: String? = null,
)

@Serializable
data class Feed(val shelves: List<Shelf> = emptyList())

@Serializable
data class RadioSeed(
    /** random | track | artist | album | playlist */
    val type: String,
    val id: String? = null,
)

@Serializable
data class RadioRequest(val seed: RadioSeed)

/** A result from the online catalogue (MusicBrainz + Deezer, proxied by the server). */
@Serializable
data class CatalogItem(
    val id: String,
    /** album | track | artist */
    val kind: String = "album",
    val title: String,
    val artist: String = "",
    val album: String? = null,
    val year: Int? = null,
    val hasPreview: Boolean = false,
    val hasCover: Boolean = false,
    /** Already in the library (matched by MBID or normalised names). */
    val owned: Boolean = false,
    /** Library id when owned (track/album/artist id). */
    val ownedId: String? = null,
    val mbid: String? = null,
)

@Serializable
data class GlobalSearch(
    val top: SearchTop? = null,
    val tracks: List<Track> = emptyList(),
    val albums: List<Album> = emptyList(),
    val artists: List<Artist> = emptyList(),
    val playlists: List<Playlist> = emptyList(),
    val catalog: List<CatalogItem> = emptyList(),
)

/** The "Top result": one of track/album/artist/playlist/catalog. */
@Serializable
data class SearchTop(
    val type: String,
    val track: Track? = null,
    val album: Album? = null,
    val artist: Artist? = null,
    val playlist: Playlist? = null,
    val catalog: CatalogItem? = null,
)

@Serializable
data class ToGetItem(
    val id: String,
    /** album | track */
    val kind: String = "album",
    val title: String,
    val artist: String = "",
    val album: String? = null,
    /** want | got | dismissed */
    val status: String = "want",
    val note: String? = null,
    val catalogId: String? = null,
    val hasCover: Boolean = false,
    val links: List<BuyLink> = emptyList(),
    val createdAt: String? = null,
)

@Serializable
data class NewToGet(
    val catalogId: String? = null,
    val kind: String = "album",
    val title: String,
    val artist: String,
    val album: String? = null,
    val note: String? = null,
)

@Serializable
data class ToGetPatch(val status: String? = null, val note: String? = null)

@Serializable
data class Pin(
    /** album | playlist | artist | mix | queue */
    val type: String,
    val id: String,
    val title: String = "",
    val subtitle: String? = null,
    val albumId: String? = null,
)

@Serializable
data class NewPin(val type: String, val id: String)

/** Radio extension: either the tracks appended, or the whole queue (tracks beyond what we hold). */
typealias RadioMore = JsonObject
