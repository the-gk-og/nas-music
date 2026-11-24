package org.nasmusic.app.data.api

import kotlinx.serialization.Serializable

// ---------- api 0.9 (schema v9): playlist folders, classical works ----------

/** One of the user's own playlist folders (GET /playlist-folders lists them depth first). */
@Serializable
data class PlaylistFolder(
    val id: String,
    val name: String,
    val parentId: String? = null,
    val createdAt: String? = null,
    val playlistCount: Int = 0,
)

@Serializable
data class NewPlaylistFolder(val name: String, val parentId: String? = null)

@Serializable
data class NewPlaylistIn(val name: String, val folderId: String? = null)

/** Classical: the tracks of one album that share a work. `tracks` only from GET /library/works/{id}. */
@Serializable
data class Work(
    val id: String,
    val title: String,
    val composer: String? = null,
    val albumId: String = "",
    val album: String = "",
    /** The album artist: the performers. */
    val artist: String = "",
    val year: Int? = null,
    val trackCount: Int = 0,
    val durationS: Double = 0.0,
    val hasArt: Boolean = false,
    val tracks: List<Track> = emptyList(),
)

@Serializable
data class Composer(val name: String, val workCount: Int = 0, val albumCount: Int = 0, val trackCount: Int = 0)
