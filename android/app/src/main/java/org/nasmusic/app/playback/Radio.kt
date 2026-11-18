package org.nasmusic.app.playback

import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import org.nasmusic.app.data.api.Track

/** Endless radio queues (docs/v3-ux.md §5): ask for more when 5 tracks remain. */
object RadioLogic {
    const val REMAINING_THRESHOLD = 5

    /** Tracks still to play after the current one. */
    fun remaining(count: Int, currentIndex: Int): Int = (count - currentIndex - 1).coerceAtLeast(0)

    fun needsMore(count: Int, currentIndex: Int): Boolean = count > 0 && remaining(count, currentIndex) <= REMAINING_THRESHOLD

    /**
     * New tracks from a `/radio/{id}/more` answer. The contract returns the whole queue (`{id, tracks}`)
     * with new tracks appended, so everything past what we hold is new. `{added:[…]}` or a bare
     * `{tracks:[…]}` without an id are taken as only-new for robustness.
     */
    fun newTracks(response: JsonObject, localIds: List<String>, json: Json): List<Track> {
        val ser = ListSerializer(Track.serializer())
        (response["added"] as? JsonArray)?.let { return json.decodeFromJsonElement(ser, it) }
        val tracks = (response["tracks"] as? JsonArray)?.let { json.decodeFromJsonElement(ser, it) } ?: return emptyList()
        return if ("id" in response) tracks.drop(localIds.size) else tracks
    }
}
