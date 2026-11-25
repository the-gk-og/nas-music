package org.homebeat.app.playback

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonPrimitive
import org.homebeat.app.data.api.MoveBody
import org.homebeat.app.data.api.NameBody
import org.homebeat.app.data.api.HomebeatApi
import org.homebeat.app.data.api.NewQueue
import org.homebeat.app.data.api.Playlist
import org.homebeat.app.data.api.Queue
import org.homebeat.app.data.api.QueuePatch
import org.homebeat.app.data.api.Track
import org.homebeat.app.data.api.TrackIdsBody

/**
 * Musicolet-style named queues shared with the user's other devices (docs/v2.md §2/§4). The local
 * player is the source of truth for what plays; every change is mirrored to the server best-effort,
 * so playback keeps working offline.
 */
class QueueSync(private val api: () -> HomebeatApi, private val scope: CoroutineScope) {
    private val _queues = MutableStateFlow<List<Queue>>(emptyList())
    val queues: StateFlow<List<Queue>> = _queues.asStateFlow()

    private val _currentId = MutableStateFlow<String?>(null)
    val currentId: StateFlow<String?> = _currentId.asStateFlow()

    /** Queue name as shown in the selector ("20. Search: conver"). */
    fun label(q: Queue) = "${q.slot}. ${q.name}"

    fun refresh() = scope.launch {
        runCatching { api().queues() }.onSuccess { list ->
            _queues.value = list.sortedBy { q -> q.slot }
            list.filter { it.isRadio }.forEach { radioIds += it.id }
        }
    }

    fun setCurrent(id: String?) { createSeq++; pendingProgress = null; _currentId.value = id }

    /** Radio queues known to this device (from /radio or a queue flagged radio by the server). */
    private val radioIds = java.util.Collections.synchronizedSet(mutableSetOf<String>())
    fun markRadio(id: String) { radioIds += id }
    fun isRadio(id: String?): Boolean = id != null && (id in radioIds || _queues.value.any { it.id == id && it.isRadio })

    @Volatile private var createSeq = 0
    /** Position reported while the new queue's id isn't known yet; sent once it is. */
    @Volatile private var pendingProgress: Pair<Int, Long>? = null

    /**
     * A list was played: create a server queue for it (replaces the oldest unused when 20 exist). Until the
     * server answers there is NO current queue, so the new list's position is never written onto the queue that
     * played before (that made the old queue "resume" at the wrong track).
     */
    fun created(name: String, tracks: List<Track>, startIndex: Int): kotlinx.coroutines.Job {
        val seq = ++createSeq
        _currentId.value = null
        pendingProgress = null
        return scope.launch {
            runCatching { api().createQueue(NewQueue(name, tracks.map { it.id }, startIndex)) }
                .onSuccess { q ->
                    if (seq != createSeq) return@onSuccess // another list was played meanwhile
                    _currentId.value = q.id
                    pendingProgress?.let { (i, p) -> pendingProgress = null; progress(i, p) }
                    refresh()
                }
        }
    }

    suspend fun load(id: String): Queue? = runCatching { api().queue(id) }.getOrNull()

    private fun mutate(block: suspend HomebeatApi.(String) -> Unit) {
        val id = _currentId.value ?: return
        scope.launch { runCatching { api().block(id) }; refresh() }
    }

    fun moved(from: Int, to: Int) = mutate { moveQueueTrack(it, MoveBody(from, to)) }
    fun removed(position: Int) = mutate { removeFromQueue(it, position) }
    fun inserted(trackIds: List<String>, position: Int) = mutate { addToQueue(it, TrackIdsBody(trackIds, JsonPrimitive(position))) }
    fun added(trackIds: List<String>, next: Boolean) =
        mutate { addToQueue(it, TrackIdsBody(trackIds, JsonPrimitive(if (next) "next" else "end"))) }
    fun progress(index: Int, positionMs: Long) {
        if (_currentId.value == null) { pendingProgress = index to positionMs; return }
        mutate { patchQueue(it, QueuePatch(currentIndex = index, positionMs = positionMs)) }
    }

    fun addTo(queueId: String, trackIds: List<String>) = scope.launch {
        runCatching { api().addToQueue(queueId, TrackIdsBody(trackIds, JsonPrimitive("end"))) }; refresh()
    }

    suspend fun rename(id: String, name: String) { runCatching { api().patchQueue(id, QueuePatch(name = name)) }; refresh() }
    suspend fun delete(id: String) {
        runCatching { api().deleteQueue(id) }
        if (_currentId.value == id) _currentId.value = null
        refresh()
    }
    suspend fun saveAsPlaylist(id: String, name: String): Playlist? = runCatching { api().saveQueueAsPlaylist(id, NameBody(name)) }.getOrNull()

    companion object {
        /** Musicolet behaviour: moving item [from] to [to] in a list. Pure, for tests and local UI. */
        fun <T> move(list: List<T>, from: Int, to: Int): List<T> {
            if (from == to || from !in list.indices || to !in list.indices) return list
            val m = list.toMutableList()
            m.add(to, m.removeAt(from))
            return m
        }

        /** Where the current item ends up after moving [from] → [to]. */
        fun currentAfterMove(current: Int, from: Int, to: Int): Int = when {
            current == from -> to
            from < current && to >= current -> current - 1
            from > current && to <= current -> current + 1
            else -> current
        }
    }
}
