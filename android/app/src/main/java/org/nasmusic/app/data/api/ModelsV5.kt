package org.nasmusic.app.data.api

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

// v5 (api/openapi.yaml 0.5.0): collaborative playlists and admin tag editing.

@Serializable
data class PlaylistEntry(
    val id: String,
    val trackId: String,
    val addedAt: String? = null,
    val addedById: String? = null,
    val addedBy: String? = null,
)

@Serializable
data class PlaylistMember(
    val userId: String,
    val username: String,
    val role: String,
    val addedAt: String? = null,
)

@Serializable
data class PlaylistActivity(
    val id: Long,
    val at: String,
    val userId: String? = null,
    val user: String? = null,
    val action: String,
    val trackId: String? = null,
    val trackTitle: String? = null,
    val detail: JsonObject = JsonObject(emptyMap()),
)

@Serializable
data class UserRef(val id: String, val username: String)

@Serializable
data class AddEntriesBody(val trackIds: List<String>, val afterEntryId: String? = null)

@Serializable
data class MoveEntryBody(val afterEntryId: String?)

@Serializable
data class NewMemberBody(val userId: String? = null, val username: String? = null, val role: String = "editor")

@Serializable
data class RoleBody(val role: String)

@Serializable
data class TagValues(
    val title: String? = null,
    val artist: String? = null,
    val albumArtist: String? = null,
    val album: String? = null,
    val trackNo: Int? = null,
    val discNo: Int? = null,
    val year: Int? = null,
    val genre: String? = null,
)

@Serializable
data class TrackTagDetail(
    val trackId: String,
    val path: String = "",
    val albumId: String = "",
    val file: TagValues = TagValues(),
    val effective: TagValues = TagValues(),
    val overridden: List<String> = emptyList(),
    val lyrics: String? = null,
    val hasCoverOverride: Boolean = false,
)

/** `set` is a raw object so a field can be explicitly null (= clear that tag) while unset fields are omitted. */
@Serializable
data class TagEditBody(val trackIds: List<String>, val set: JsonObject = JsonObject(emptyMap()), val revert: List<String> = emptyList())

@Serializable
data class TagEditResult(val batchId: String = "", val changed: Int = 0, val albumId: String? = null)

@Serializable
data class TagSuggestion(
    val score: Int? = null,
    val title: String? = null,
    val artist: String? = null,
    val album: String? = null,
    val albumArtist: String? = null,
    val year: Int? = null,
    val trackNo: Int? = null,
    val discNo: Int? = null,
)

@Serializable
data class TagSuggestions(val kind: String = "", val results: List<TagSuggestion> = emptyList())

// v5 (openapi 0.5.0): Release Radar, Wrapped, Blend.

@Serializable
data class ReleaseInfo(val date: String? = null, val type: String? = null, val upcoming: Boolean = false, val artist: String? = null)

@Serializable
data class ShelfBlend(val status: String? = null, val matchPct: Int? = null, val trackCount: Int = 0, val members: List<String> = emptyList())

@Serializable
data class ShelfWrapped(val year: Int, val final: Boolean = false)

data class ReleaseRadar(val updatedAt: String?, val inLibrary: List<ShelfItem>, val notOwned: List<ShelfItem>)

@Serializable
data class WrappedTotals(
    val minutes: Int = 0, val plays: Int = 0, val skips: Int = 0, val tracks: Int = 0, val artists: Int = 0,
    val albums: Int = 0, val days: Int = 0,
)

@Serializable
data class WrappedTrack(val track: Track, val plays: Int = 0, val minutes: Double = 0.0)

@Serializable
data class WrappedArtist(val artist: Artist, val artUrl: String? = null, val plays: Int = 0, val minutes: Double = 0.0)

@Serializable
data class WrappedAlbum(val album: Album, val plays: Int = 0, val minutes: Double = 0.0)

@Serializable
data class WrappedGenre(val name: String, val minutes: Double = 0.0, val share: Double = 0.0)

@Serializable
data class WrappedStreaks(val longest: Int = 0, val longestFrom: String? = null, val longestTo: String? = null, val current: Int = 0)

@Serializable
data class WrappedDiscoveries(val artists: Int = 0, val tracks: Int = 0, val share: Double = 0.0, val topArtists: List<WrappedArtist> = emptyList())

@Serializable
data class AuraMood(val name: String, val score: Double = 0.0)

@Serializable
data class AudioAura(
    val label: String, val description: String = "", val energy: Double = 0.0, val danceability: Double? = null,
    val valence: Double? = null, val bpm: Int? = null, val moods: List<AuraMood> = emptyList(), val colors: List<String> = emptyList(),
)

@Serializable
data class TopDay(val date: String, val minutes: Int = 0)

/** GET /wrapped and /admin/wrapped (the server-only fields stay null for the other). */
@Serializable
data class WrappedStats(
    val year: Int,
    val final: Boolean = false,
    val totals: WrappedTotals = WrappedTotals(),
    val topTracks: List<WrappedTrack> = emptyList(),
    val topArtists: List<WrappedArtist> = emptyList(),
    val topAlbums: List<WrappedAlbum> = emptyList(),
    val topGenres: List<WrappedGenre> = emptyList(),
    val byHour: List<Double> = emptyList(),
    val byWeekday: List<Double> = emptyList(),
    val byMonth: List<Double> = emptyList(),
    val peakHour: Int? = null,
    val peakWeekday: Int? = null,
    val topDay: TopDay? = null,
    val aura: AudioAura? = null,
    val streaks: WrappedStreaks? = null,
    val discoveries: WrappedDiscoveries? = null,
    val listeners: Int? = null,
    val users: Int? = null,
)

@Serializable
data class BlendMember(val userId: String, val username: String = "", val status: String = "joined", val joinedAt: String? = null)

@Serializable
data class BlendPair(val a: String? = null, val b: String? = null, val aName: String? = null, val bName: String? = null, val pct: Int? = null)

@Serializable
data class BlendTrack(val track: Track, val forUsers: List<String> = emptyList(), val reason: String = "top")

@Serializable
data class BlendUser(val userId: String, val username: String = "")

@Serializable
data class Blend(
    val id: String,
    val name: String,
    val createdBy: BlendUser? = null,
    val myStatus: String = "joined",
    val invitedBy: String? = null,
    val members: List<BlendMember> = emptyList(),
    val matchPct: Int? = null,
    val pairs: List<BlendPair> = emptyList(),
    val trackCount: Int = 0,
    val updatedAt: String? = null,
    val tracks: List<BlendTrack> = emptyList(),
)

@Serializable
data class BlendCandidate(val userId: String, val username: String)

@Serializable
data class NewBlend(val usernames: List<String>, val name: String? = null)

@Serializable
data class BlendName(val name: String)

@Serializable
data class BlendInvite(val usernames: List<String>)
