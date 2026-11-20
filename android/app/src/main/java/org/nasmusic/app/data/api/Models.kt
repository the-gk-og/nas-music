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
    /** v3 (optional until the server sends them): the user's most played tracks by this artist, similar artists. */
    val topTracks: List<Track> = emptyList(),
    val similar: List<Artist> = emptyList(),
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
    /** api 0.6: contains explicit tracks (of those this user can see) — "E" badge. */
    val explicit: Boolean = false,
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
    val explicit: Boolean = false,
)

@Serializable
data class Track(
    val id: String,
    // Defaults so a partial track (e.g. a missing file in a queue) still parses.
    val title: String = "",
    val artistId: String = "",
    val artist: String = "",
    val albumId: String = "",
    val album: String = "",
    val trackNo: Int? = null,
    val discNo: Int? = null,
    val year: Int? = null,
    val genre: String? = null,
    val durationS: Double = 0.0,
    val flacBytes: Long? = null,
    val starred: Boolean = false,
    val rating: Int? = null,
    /** v2: in playlists/queues when the file is gone. */
    val missing: Boolean = false,
    /** api 0.5: volume levelling (ReplayGain 2.0 scale); null until the server has measured the track. */
    val replayGain: ReplayGain? = null,
    /** api 0.6: explicit | probable | clean | unknown ("probable" = lyrics word list, not certain). */
    val explicit: String? = null,
    /** api 0.6, queues/playlists only: hidden by this user's family mode — the player skips it. */
    val familyHidden: Boolean = false,
) {
    val isExplicit: Boolean get() = explicit == "explicit"
}

/** api 0.6 family mode. Locked = set by an admin; loosening it needs the admin's PIN. */
@Serializable
data class FamilyMode(
    val enabled: Boolean = false,
    val hideProbable: Boolean = true,
    val hideUnknown: Boolean = false,
    val locked: Boolean = false,
    val hasPin: Boolean = false,
)

// v7 voice control
@Serializable
data class VoiceToken(val id: String, val name: String, val createdAt: String, val lastUsed: String? = null, val token: String? = null)

@Serializable
data class VoiceTokens(val tokens: List<VoiceToken> = emptyList())

@Serializable
data class NewVoiceToken(val name: String)

@Serializable
data class VoiceDevice(
    val id: String,
    val name: String,
    val kind: String = "other",
    val castType: String? = null,
    val online: Boolean = false,
    val aliases: List<String> = emptyList(),
    val isDefault: Boolean = false,
)

@Serializable
data class VoiceDevices(val devices: List<VoiceDevice> = emptyList())

@Serializable
data class VoiceDevicePrefs(val id: String? = null, val aliases: List<String>? = null, val isDefault: Boolean? = null)

@Serializable
data class FamilyUpdate(
    val enabled: Boolean? = null,
    val hideProbable: Boolean? = null,
    val hideUnknown: Boolean? = null,
    val pin: String? = null,
)

@Serializable
data class ReplayGain(
    val trackGain: Double,
    val trackPeak: Double? = null,
    val albumGain: Double? = null,
    val albumPeak: Double? = null,
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
data class LyricLine(val timeMs: Long? = null, val text: String)

@Serializable
data class Lyrics(
    val trackId: String,
    val synced: Boolean,
    val source: String,
    val lines: List<LyricLine> = emptyList(),
    val text: String = "",
    /** v2: the server accepts PUT /tracks/{id}/lyrics overrides (source becomes "user"). */
    val canEdit: Boolean = false,
    /** v2: every source that has lyrics for this track (source dropdown); pick one with ?source=. */
    val sources: List<String> = emptyList(),
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
    val durationS: Double? = null,
    /** Size of the file /stream returns for this format. */
    val bytes: Long? = null,
    /** false = [bytes] is an estimate (Opus not transcoded yet): plan with it, never verify against it. */
    val bytesExact: Boolean = false,
)

@Serializable
data class SyncManifest(val generatedAt: String, val items: List<ManifestItem>)

/** `GET /sync/smart`: smart-download candidates (favourites, this week's mixes, recent plays, AI picks). */
@Serializable
data class SmartCandidates(val generatedAt: String = "", val items: List<SmartCandidateDto> = emptyList())

@Serializable
data class SmartCandidateDto(
    val trackId: String,
    val reasons: List<String> = emptyList(),
    val score: Double = 0.0,
    val plays: Int = 0,
    val lastPlayedAt: String? = null,
    val durationS: Double? = null,
    val flacBytes: Long? = null,
    val track: Track? = null,
)

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
    /** v2 §10: opt-in listening context; only enabled signals are present. */
    val context: kotlinx.serialization.json.JsonObject? = null,
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
    val hasCover: Boolean = false,
    val hasPreview: Boolean = false,
    /** Upstream sources the server proxies; the app must never fetch these. */
    val coverUrl: String? = null,
    val previewUrl: String? = null,
    val buyLinks: List<BuyLink> = emptyList(),
    val reason: String,
    val status: String,
    val createdAt: String? = null,
)

@Serializable
data class SuggestionStatus(val status: String)

@Serializable
data class AutoEqEntry(val id: String, val name: String, val source: String = "", val rig: String? = null, val form: String = "")

@Serializable
data class AutoEqSearch(val total: Int = 0, val results: List<AutoEqEntry> = emptyList())
