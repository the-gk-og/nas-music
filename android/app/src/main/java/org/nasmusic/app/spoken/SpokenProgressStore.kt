package org.nasmusic.app.spoken

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.builtins.ListSerializer
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.ProgressBatch
import org.nasmusic.app.data.api.ProgressWrite
import org.nasmusic.app.data.api.SpokenApi

/**
 * Resume points of podcast episodes and audiobooks, sent to PUT /spoken/progress (newest `updatedAt` wins on the
 * server). Writes go to an on-disk outbox first, so a position recorded offline (or just before the app is
 * killed) reaches the server later without rewinding newer progress from another device. Used by the
 * PlaybackService (positions while playing) and the UI (mark played).
 */
class SpokenProgressStore(context: Context, private val api: () -> SpokenApi, private val scope: CoroutineScope) {
    private val prefs = context.getSharedPreferences("spoken", Context.MODE_PRIVATE)
    private val ser = ListSerializer(ProgressWrite.serializer())
    private val outbox = ProgressOutbox(
        runCatching { ApiFactory.json.decodeFromString(ser, prefs.getString("outbox", "[]")!!) }.getOrDefault(emptyList()),
    )
    private var flushing: Job? = null

    /** The newest unsent position of an item, if any (it wins over the server's until sent). */
    fun pending(kind: String, id: String): ProgressWrite? = outbox.get(kind, id)

    fun record(w: ProgressWrite, flush: Boolean = false) {
        outbox.add(w)
        persist()
        if (flush) flushSoon()
    }

    fun position(item: SpokenItem, positionS: Double, flush: Boolean = false) {
        if (item.kind == "station") return
        record(
            ProgressWrite(item.kind, item.id, positionS = Math.round(positionS * 10) / 10.0, durationS = item.durationS,
                speed = if (item.kind == "book") item.speed.toDouble() else null),
            flush,
        )
    }

    fun markPlayed(kind: String, id: String, played: Boolean) = record(ProgressWrite(kind, id, played = played), flush = true)

    fun flushSoon() {
        if (flushing?.isActive == true) return
        flushing = scope.launch { flush() }
    }

    suspend fun flush() {
        val batch = outbox.take()
        if (batch.isEmpty()) return
        val ok = withContext(Dispatchers.IO) { runCatching { api().putProgress(ProgressBatch(batch)) }.isSuccess }
        if (!ok) outbox.restore(batch)
        persist()
    }

    private fun persist() {
        prefs.edit().putString("outbox", ApiFactory.json.encodeToString(ser, outbox.snapshot())).apply()
    }
}
