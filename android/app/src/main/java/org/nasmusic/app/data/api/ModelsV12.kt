package org.nasmusic.app.data.api

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive

// ---------- api 0.12 (schema v12): song credits ----------

/** One credited person on a track (GET /library/tracks/{id}/credits). */
@Serializable
data class CreditEntry(
    val personId: String,
    val name: String,
    val mbid: String? = null,
    /** Display labels: "Co-producer", "Electric guitar", "Composer", ... */
    val roles: List<String> = emptyList(),
    /** Credited on the album rather than this song specifically. */
    val albumWide: Boolean = false,
    /** Only known from the file's composer/lyricist tags. */
    val fromTags: Boolean = false,
    /** Other songs in the caller's library they are credited on. */
    val otherTracks: Int = 0,
    /** Their artist page in this library, if they are also an artist here. */
    val artistId: String? = null,
)

@Serializable
data class CreditGroup(
    /** producers | writers | musicians | engineers | other */
    val id: String,
    val label: String,
    val people: List<CreditEntry> = emptyList(),
)

@Serializable
data class TrackCredits(
    val trackId: String,
    /** ok | not_found | error | pending (poll again) | none (credits are off on the server) */
    val status: String = "none",
    val fetchedAt: String? = null,
    val recordingMbid: String? = null,
    val musicbrainzUrl: String? = null,
    val groups: List<CreditGroup> = emptyList(),
)

/** GET /people/{id}: everything in the library one credited person worked on. */
@Serializable
data class CreditPerson(
    val id: String,
    val name: String,
    val mbid: String? = null,
    val musicbrainzUrl: String? = null,
    val artistId: String? = null,
    val roles: List<String> = emptyList(),
    val trackCount: Int = 0,
    /** Track objects with an extra `credits` list (their roles on that track). */
    val tracks: List<JsonObject> = emptyList(),
) {
    /** Each track with this person's roles on it. */
    val songs: List<Pair<Track, List<String>>>
        get() = tracks.mapNotNull { o ->
            val t = runCatching { ApiFactory.json.decodeFromJsonElement(Track.serializer(), o) }.getOrNull() ?: return@mapNotNull null
            val roles = runCatching { o["credits"]?.jsonArray?.map { it.jsonPrimitive.content } }.getOrNull().orEmpty()
            t to roles
        }
}
