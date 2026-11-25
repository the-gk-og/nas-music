package org.homebeat.app.feedback

import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import org.homebeat.app.data.db.EventEntity

/** Writes feedback events to the Room queue and pokes the uploader. */
class EventRecorder(
    private val store: EventQueueStore,
    private val scope: CoroutineScope,
    private val isOnline: () -> Boolean,
    private val requestUpload: () -> Unit,
    private val now: () -> Instant = Instant::now,
) {
    fun record(
        type: EventType,
        trackId: String,
        positionS: Double? = null,
        durationS: Double? = null,
        source: String? = null,
        mixId: String? = null,
        rating: Int? = null,
        context: kotlinx.serialization.json.JsonObject? = null,
    ) {
        val online = isOnline()
        val e = EventEntity(
            id = UUID.randomUUID().toString(),
            type = type.wire,
            trackId = trackId,
            at = now().toString(),
            positionS = positionS,
            durationS = durationS,
            source = source,
            mixId = mixId,
            rating = rating,
            offline = !online,
            context = context?.toString(),
        )
        scope.launch {
            store.insert(e)
            if (online) requestUpload()
        }
    }
}
