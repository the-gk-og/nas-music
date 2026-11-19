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
