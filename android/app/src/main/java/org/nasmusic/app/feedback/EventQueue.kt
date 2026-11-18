package org.nasmusic.app.feedback

import org.nasmusic.app.data.api.EventBatch
import org.nasmusic.app.data.api.EventDto
import org.nasmusic.app.data.api.EventsResponse
import org.nasmusic.app.data.db.EventEntity

/** The part of the Room DAO the uploader needs; a fake implements it in tests. */
interface EventQueueStore {
    suspend fun insert(event: EventEntity)
    suspend fun oldest(limit: Int): List<EventEntity>
    suspend fun delete(ids: List<String>)
}

fun EventEntity.toDto() = EventDto(
    id = id, type = type, trackId = trackId, at = at, positionS = positionS, durationS = durationS,
    source = source, mixId = mixId, rating = rating, offline = offline,
    context = context?.let { runCatching { kotlinx.serialization.json.Json.parseToJsonElement(it) as? kotlinx.serialization.json.JsonObject }.getOrNull() },
)

/**
 * Drains the queue to POST /events in batches. Events are deleted only after the server answered
 * 2xx; because the server de-duplicates on event id, a crash between upload and delete is harmless.
 */
class EventUploader(
    private val store: EventQueueStore,
    private val send: suspend (EventBatch) -> EventsResponse,
    private val batchSize: Int = 500,
) {
    init { require(batchSize in 1..1000) { "API allows at most 1000 events per batch" } }

    data class Outcome(val uploaded: Int, val error: Throwable?)

    suspend fun uploadAll(): Outcome {
        var uploaded = 0
        while (true) {
            val batch = store.oldest(batchSize)
            if (batch.isEmpty()) return Outcome(uploaded, null)
            try {
                send(EventBatch(batch.map { it.toDto() }))
            } catch (e: Exception) {
                return Outcome(uploaded, e)
            }
            store.delete(batch.map { it.id })
            uploaded += batch.size
            if (batch.size < batchSize) return Outcome(uploaded, null)
        }
    }
}
