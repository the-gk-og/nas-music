package org.nasmusic.app.lyrics

import kotlinx.serialization.json.Json
import org.nasmusic.app.data.api.Lyrics
import org.nasmusic.app.data.db.LyricsEntity
import retrofit2.Response

/** The part of the Room DAO the repository needs; a fake can implement it in tests. */
interface LyricsCacheStore {
    suspend fun get(trackId: String): LyricsEntity?
    suspend fun put(entity: LyricsEntity)
    suspend fun delete(trackId: String)
}

sealed interface LyricsResult {
    data class Found(val lyrics: Lyrics) : LyricsResult
    /** The server answered 404: this track has no lyrics. */
    data object None : LyricsResult
    /** Not cached and the server could not be reached; worth asking again later. */
    data object Unavailable : LyricsResult
}

/**
 * Lyrics with a three-level lookup: memory → Room → server. Both 200 and 404 are stored in Room
 * (404 as a "none" marker) so the app doesn't keep asking. A "none" marker is re-checked after
 * [noneTtlMs] in case a .lrc file was added on the NAS.
 */
class LyricsRepository(
    private val store: LyricsCacheStore,
    private val fetch: suspend (String) -> Response<Lyrics>,
    private val json: Json,
    private val now: () -> Long = System::currentTimeMillis,
    private val noneTtlMs: Long = 7L * 24 * 3600 * 1000,
) {
    private val memory = object : LinkedHashMap<String, LyricsResult>(32, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, LyricsResult>?) = size > 64
    }

    suspend fun get(trackId: String): LyricsResult {
        synchronized(memory) { memory[trackId] }?.let { return it }
        val cached = fromStore(trackId)
        if (cached != null) return remember(trackId, cached)
        return fetchAndStore(trackId).also { if (it != LyricsResult.Unavailable) remember(trackId, it) }
    }

    /** For SyncWorker: fetch only if there is no usable cached answer. Returns true if the server was asked. */
    suspend fun prefetch(trackId: String): Boolean {
        if (fromStore(trackId) != null) return false
        val r = fetchAndStore(trackId)
        if (r != LyricsResult.Unavailable) remember(trackId, r)
        return true
    }

    suspend fun drop(trackId: String) {
        synchronized(memory) { memory.remove(trackId) }
        store.delete(trackId)
    }

    private suspend fun fromStore(trackId: String): LyricsResult? {
        val e = store.get(trackId) ?: return null
        return when (e.status) {
            STATUS_FOUND -> e.json?.let { runCatching { json.decodeFromString(Lyrics.serializer(), it) }.getOrNull() }
                ?.let { LyricsResult.Found(it) }
            STATUS_NONE -> if (now() - e.fetchedAtMs < noneTtlMs) LyricsResult.None else null
            else -> null
        }
    }

    private suspend fun fetchAndStore(trackId: String): LyricsResult {
        val resp = try { fetch(trackId) } catch (e: Exception) { return LyricsResult.Unavailable }
        return when {
            resp.isSuccessful && resp.body() != null -> {
                val l = resp.body() ?: return LyricsResult.Unavailable
                store.put(LyricsEntity(trackId, STATUS_FOUND, json.encodeToString(Lyrics.serializer(), l), now()))
                LyricsResult.Found(l)
            }
            resp.code() == 404 -> {
                store.put(LyricsEntity(trackId, STATUS_NONE, null, now()))
                LyricsResult.None
            }
            else -> LyricsResult.Unavailable
        }
    }

    private fun remember(trackId: String, r: LyricsResult): LyricsResult {
        synchronized(memory) { memory[trackId] = r }
        return r
    }

    companion object {
        const val STATUS_FOUND = "found"
        const val STATUS_NONE = "none"
    }
}
