package org.nasmusic.app.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Hand-written from api/openapi.yaml (v0.1.0). Keep in sync with the contract.

@Serializable
data class Health(val status: String, val serverId: String, val version: String)

@Serializable
data class PairRequest(val pairCode: String, val deviceName: String)

@Serializable
data class PairResponse(val deviceId: String, val deviceToken: String)

@Serializable
data class Artist(
    val id: String,
    val name: String,
    val albumCount: Int,
    val mbid: String? = null,
)

@Serializable
data class ArtistDetail(
    val id: String,
    val name: String,
    val albumCount: Int,
    val mbid: String? = null,
    val albums: List<Album> = emptyList(),
)

@Serializable
data class Album(
    val id: String,
    val title: String,
    val artistId: String,
    val artist: String,
    val year: Int? = null,
    val trackCount: Int,
    val durationS: Double? = null,
    val hasArt: Boolean = false,
    val mbid: String? = null,
)

@Serializable
data class AlbumDetail(
    val id: String,
    val title: String,
    val artistId: String,
    val artist: String,
    val year: Int? = null,
    val trackCount: Int,
    val durationS: Double? = null,
    val hasArt: Boolean = false,
    val mbid: String? = null,
    val tracks: List<Track> = emptyList(),
)

@Serializable
data class Track(
    val id: String,
    val title: String,
    val artistId: String,
    val artist: String,
    val albumId: String,
    val album: String,
    val trackNo: Int? = null,
    val discNo: Int? = null,
    val year: Int? = null,
    val genre: String? = null,
    val durationS: Double,
    val flacBytes: Long? = null,
    val starred: Boolean,
    val rating: Int? = null,
)

@Serializable
data class SearchResult(
    val artists: List<Artist> = emptyList(),
    val albums: List<Album> = emptyList(),
    val tracks: List<Track> = emptyList(),
)

@Serializable
data class MixTrack(val track: Track, val reason: String)

@Serializable
data class Mix(
    val id: String,
    val kind: String,
    val title: String,
    val createdAt: String,
    val durationS: Double? = null,
    val tracks: List<MixTrack>,
)

@Serializable
data class WhyResponse(val reason: String, val similarTo: List<Track> = emptyList())

@Serializable
enum class AudioFormat {
    @SerialName("opus") OPUS,
    @SerialName("flac") FLAC;

    val wire: String get() = name.lowercase()
}

@Serializable
data class ManifestItem(
    val trackId: String,
    val format: AudioFormat,
    val why: String,
    val mixIds: List<String> = emptyList(),
)

@Serializable
data class SyncManifest(val generatedAt: String, val items: List<ManifestItem>)

@Serializable
data class EventDto(
    val id: String,
    val type: String,
    val trackId: String,
    val at: String,
    val positionS: Double? = null,
    val durationS: Double? = null,
    val source: String? = null,
    val mixId: String? = null,
    val rating: Int? = null,
    val offline: Boolean = false,
)

@Serializable
data class EventBatch(val events: List<EventDto>)

@Serializable
data class EventsResponse(val accepted: Int, val duplicates: Int)

@Serializable
data class BuyLink(val name: String? = null, val url: String? = null)

@Serializable
data class Suggestion(
    val id: String,
    val artist: String,
    val album: String,
    val year: Int? = null,
    val artistMbid: String? = null,
    val releaseGroupMbid: String? = null,
    val coverUrl: String? = null,
    val previewUrl: String? = null,
    val buyLinks: List<BuyLink> = emptyList(),
    val reason: String,
    val status: String,
    val createdAt: String? = null,
)

@Serializable
data class SuggestionStatus(val status: String)
