package org.nasmusic.app.data

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import org.nasmusic.app.data.api.Mix
import org.nasmusic.app.data.api.NasMusicApi
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.data.db.AppDatabase
import org.nasmusic.app.data.db.CacheEntity
import org.nasmusic.app.data.db.DownloadEntity

/** Server data with an offline fallback (last known mixes/favourites, downloaded tracks). */
class LibraryRepository(
    val api: NasMusicApi,
    private val db: AppDatabase,
    private val json: Json,
) {
    private val mixList = ListSerializer(Mix.serializer())
    private val trackList = ListSerializer(Track.serializer())

    suspend fun cacheMixes(mixes: List<Mix>) =
        db.cache().put(CacheEntity(KEY_MIXES, json.encodeToString(mixList, mixes)))

    suspend fun cacheFavourites(tracks: List<Track>) =
        db.cache().put(CacheEntity(KEY_FAVS, json.encodeToString(trackList, tracks)))

    val cachedMixes: Flow<List<Mix>> = db.cache().observe(KEY_MIXES).map { c ->
        c?.let { runCatching { json.decodeFromString(mixList, it.json) }.getOrNull() }.orEmpty()
    }

    val cachedFavourites: Flow<List<Track>> = db.cache().observe(KEY_FAVS).map { c ->
        c?.let { runCatching { json.decodeFromString(trackList, it.json) }.getOrNull() }.orEmpty()
    }

    val downloads: Flow<List<DownloadEntity>> = db.downloads().observeAll()

    /** Tries the server first and caches the answer; falls back to the cache when offline. */
    suspend fun todayMixes(): Pair<List<Mix>, Boolean> = try {
        api.mixesToday().also { cacheMixes(it) } to true
    } catch (e: Exception) {
        db.cache().get(KEY_MIXES)?.let { runCatching { json.decodeFromString(mixList, it.json) }.getOrNull() }
            .orEmpty() to false
    }

    suspend fun favourites(): Pair<List<Track>, Boolean> = try {
        api.favourites().also { cacheFavourites(it) } to true
    } catch (e: Exception) {
        db.cache().get(KEY_FAVS)?.let { runCatching { json.decodeFromString(trackList, it.json) }.getOrNull() }
            .orEmpty() to false
    }

    suspend fun localPath(trackId: String): String? = db.downloads().completed(trackId)?.path

    fun decodeTrack(text: String?): Track? = text?.let { runCatching { json.decodeFromString(Track.serializer(), it) }.getOrNull() }

    companion object {
        private const val KEY_MIXES = "mixes_today"
        private const val KEY_FAVS = "favourites"
        private val codec = Json { ignoreUnknownKeys = true }
        fun encodeTrack(t: Track): String = codec.encodeToString(Track.serializer(), t)
    }
}
