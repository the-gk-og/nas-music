package org.nasmusic.app.data.api

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

// v2 models, hand-written from docs/v2.md §4/§5/§10 (openapi.yaml catches up in parallel).

@Serializable
data class ServerInfo(
    val serverId: String,
    val name: String = "NAS",
    val version: String = "",
    val lanUrl: String? = null,
    val tunnelUrl: String? = null,
    val certSha256: String? = null,
    val registration: String = "invite",
)

@Serializable
data class LoginRequest(
    val username: String,
    val password: String,
    val deviceName: String,
    val deviceKind: String = "android",
    val cookie: Boolean = false,
)

@Serializable
data class RegisterRequest(
    val inviteCode: String,
    val username: String,
    val password: String,
    val deviceName: String,
    val deviceKind: String = "android",
    val cookie: Boolean = false,
)

@Serializable
data class Session(
    val userId: String,
    val username: String,
    val isAdmin: Boolean = false,
    val deviceId: String,
    val deviceToken: String,
)

@Serializable
data class Me(val id: String? = null, val userId: String? = null, val username: String, val isAdmin: Boolean = false)

@Serializable
data class PasswordChange(val current: String, val new: String)

@Serializable
data class MyDevice(
    val id: String,
    val name: String,
    val kind: String = "other",
    val lastSeen: String? = null,
    val online: Boolean = false,
)

@Serializable
data class PairCode(val pairCode: String, val expiresAt: String? = null, val qrPayload: JsonElement) {
    /** Compact JSON for the QR (the contract sends the docs/pairing.md object; tolerate a string too). */
    val qrText: String get() = (qrPayload as? kotlinx.serialization.json.JsonPrimitive)?.takeIf { it.isString }?.content ?: qrPayload.toString()
}

@Serializable
data class FolderEntry(val name: String, val path: String, val trackCount: Int = 0)

@Serializable
data class FolderListing(
    val path: String = "",
    val parent: String? = null,
    val folders: List<FolderEntry> = emptyList(),
    val tracks: List<Track> = emptyList(),
)

@Serializable
data class Genre(val name: String, val trackCount: Int = 0)

@Serializable
data class TrackInfo(
    val id: String,
    val title: String,
    val artistId: String = "",
    val artist: String = "",
    val albumId: String = "",
    val album: String = "",
    val trackNo: Int? = null,
    val discNo: Int? = null,
    val year: Int? = null,
    val genre: String? = null,
    val durationS: Double = 0.0,
    val fileName: String? = null,
    val folder: String? = null,
    val albumArtist: String? = null,
    val composer: String? = null,
    val lyricist: String? = null,
    val comment: String? = null,
    val codec: String? = null,
    val bitrateKbps: Int? = null,
    val sampleRate: Int? = null,
    val bitsPerSample: Int? = null,
    val channels: Int? = null,
    val fileBytes: Long? = null,
)

@Serializable
data class Playlist(
    val id: String,
    val name: String,
    val trackCount: Int = 0,
    val createdAt: String? = null,
    val updatedAt: String? = null,
    val tracks: List<Track> = emptyList(),
    // v5: collaborative playlists
    val ownerId: String? = null,
    val owner: String? = null,
    val role: String = "owner",
    val rev: Int = 0,
    val memberCount: Int = 0,
    val collaborative: Boolean = false,
    /** Parallel to [tracks]. */
    val entries: List<PlaylistEntry> = emptyList(),
)

@Serializable
data class NameBody(val name: String)

@Serializable
data class TrackIdsBody(val trackIds: List<String>, val position: JsonElement? = null)

@Serializable
data class MoveBody(val from: Int, val to: Int)

@Serializable
data class Queue(
    val id: String,
    val name: String,
    val slot: Int = 0,
    val currentIndex: Int = 0,
    val positionMs: Long = 0,
    val trackCount: Int = 0,
    val updatedAt: String? = null,
    val tracks: List<Track> = emptyList(),
    /** v3: set (the seed) on radio queues; the client asks /radio/{id}/more when 5 tracks remain. */
    val radio: RadioSeed? = null,
    /** v3 QueueSource: what the queue was made from ({type, id, …}). */
    val source: kotlinx.serialization.json.JsonObject? = null,
    val kind: String? = null,
) {
    val isRadio: Boolean get() = radio != null || kind == "radio"
}

@Serializable
data class NewQueue(val name: String, val trackIds: List<String>, val startIndex: Int = 0)

@Serializable
data class QueuePatch(val name: String? = null, val currentIndex: Int? = null, val positionMs: Long? = null)

@Serializable
data class LyricsEdit(val raw: String)

@Serializable
data class Invite(
    val code: String,
    val url: String? = null,
    val expiresAt: String? = null,
    val note: String? = null,
    val usedBy: String? = null,
    val createdAt: String? = null,
)

@Serializable
data class NewInvite(val note: String? = null, val expiresInDays: Int = 7)

@Serializable
data class AdminUser(
    val id: String,
    val username: String,
    val isAdmin: Boolean = false,
    val disabled: Boolean = false,
    val lastLogin: String? = null,
)

@Serializable
data class AdminUserPatch(val disabled: Boolean? = null, val isAdmin: Boolean? = null)

@Serializable
data class NewPassword(val new: String)

/** GET/PUT /me/settings: `{settings: {key: value}}`; PUT merges (null deletes). Context switches are `context.<signal>`. */
@Serializable
data class UserSettings(val settings: Map<String, JsonElement> = emptyMap())

/** GET /me/data: shown to the user as-is. */
typealias MyData = JsonObject
