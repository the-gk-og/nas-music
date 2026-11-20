package org.nasmusic.app.sync

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import org.nasmusic.app.data.LibraryRepository
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.data.db.AppDatabase
import org.nasmusic.app.data.db.CacheEntity
import org.nasmusic.app.data.db.DownloadEntity
import org.nasmusic.app.data.db.OfflinePinEntity
import org.nasmusic.app.data.prefs.SettingsRepository

/** Result of the last sync run, shown on the Downloads screen. */
@Serializable
data class SyncStatus(
    val atMs: Long,
    val ok: Boolean,
    val summary: String,
    val downloaded: Int = 0,
    val failed: Int = 0,
    val evicted: Int = 0,
)

/**
 * Downloads the user asks for (single tracks, or whole playlists/albums kept offline) and the state the
 * Downloads screen shows. Manual and kept-collection downloads are fetched by [SyncWorker] in
 * "protected only" mode right away (same route rules as night sync) and are never evicted.
 */
class OfflineRepository(
    private val context: Context,
    private val db: AppDatabase,
    private val downloader: Downloader,
    private val settings: SettingsRepository,
    scope: CoroutineScope,
) {
    val downloads: Flow<List<DownloadEntity>> = db.downloads().observeAll()
    val pins: Flow<List<OfflinePinEntity>> = db.pins().observeAll()

    /** Track ids with a complete local file, for offline filtering and "downloaded" badges. */
    val downloadedIds: StateFlow<Set<String>> = downloads
        .map { list -> list.filter { it.complete }.map { it.trackId }.toSet() }
        .stateIn(scope, SharingStarted.Eagerly, emptySet())

    /** Downloaded tracks with metadata, for the offline library. */
    val offlineTracks: StateFlow<List<Track>> = downloads
        .map { list -> list.filter { it.complete }.sortedByDescending { it.updatedAtMs }.mapNotNull { d -> decode(d.trackJson) } }
        .stateIn(scope, SharingStarted.Eagerly, emptyList())

    val status: Flow<SyncStatus?> = db.cache().observe(KEY_STATUS).map { e ->
        e?.let { runCatching { json.decodeFromString(SyncStatus.serializer(), it.json) }.getOrNull() }
    }

    suspend fun saveStatus(s: SyncStatus) = db.cache().put(CacheEntity(KEY_STATUS, json.encodeToString(SyncStatus.serializer(), s)))

    fun isPinned(type: String, id: String): Flow<Boolean> =
        pins.map { list -> list.any { it.key == OfflinePinEntity.key(type, id) } }

    /** "Download" on a song (or a selection): keep these offline until the user removes them. */
    suspend fun downloadTracks(tracks: List<Track>) {
        val dao = db.downloads()
        for (t in tracks.filterNot { it.missing }.distinctBy { it.id }) {
            val cur = dao.get(t.id)
            dao.upsert(
                cur?.copy(manual = true, trackJson = cur.trackJson ?: LibraryRepository.encodeTrack(t))
                    ?: pending(t).copy(manual = true),
            )
        }
        SyncScheduler.downloadProtectedNow(context)
    }

    /** "Download" on a playlist or album: its tracks stay offline, and follow later changes to it. */
    suspend fun pin(type: String, id: String, title: String, tracks: List<Track>) {
        val key = OfflinePinEntity.key(type, id)
        val present = tracks.filterNot { it.missing }.distinctBy { it.id }
        db.pins().upsert(OfflinePinEntity(key, type, id, title, present.joinToString(",") { it.id }, refreshedAtMs = System.currentTimeMillis()))
        val dao = db.downloads()
        for (t in present) {
            val cur = dao.get(t.id)
            val row = cur ?: pending(t)
            if (key !in row.pinKeys) dao.upsert(row.copy(pins = (row.pinKeys + key).joinToString(",")))
        }
        SyncScheduler.downloadProtectedNow(context)
    }

    suspend fun unpin(key: String) {
        db.pins().delete(key)
        for (row in db.downloads().all().filter { key in it.pinKeys }) {
            release(row.copy(pins = (row.pinKeys - key).joinToString(",")))
        }
    }

    suspend fun removeManual(trackId: String) {
        val row = db.downloads().get(trackId) ?: return
        release(row.copy(manual = false))
    }

    /** Deletes every download that isn't manual or in a kept collection (e.g. after turning smart downloads off). */
    suspend fun removeAutomatic(): Int {
        var n = 0
        for (row in db.downloads().all().filter { !it.isProtected }) { delete(row.trackId); n++ }
        return n
    }

    /**
     * A row that may have stopped being protected. While smart downloads or offline favourites is on the
     * file stays and the next sync decides; otherwise nothing would ever clean it up, so it goes now.
     */
    private suspend fun release(row: DownloadEntity) {
        if (row.isProtected) { db.downloads().upsert(row); return }
        val s = settings.current()
        when {
            !row.complete -> delete(row.trackId)
            s.smartDownloads -> db.downloads().upsert(row.copy(origin = "smart"))
            s.offlineFavourites -> db.downloads().upsert(row.copy(origin = "sync"))
            else -> delete(row.trackId)
        }
    }

    private suspend fun delete(trackId: String) {
        downloader.deleteAll(trackId)
        db.downloads().get(trackId)?.path?.takeIf { it.isNotBlank() }?.let { java.io.File(it).delete() }
        db.downloads().delete(trackId)
    }

    private fun pending(t: Track) = DownloadEntity(
        trackId = t.id, format = "opus", path = "", bytes = 0, why = "manual", complete = false,
        trackJson = LibraryRepository.encodeTrack(t), origin = "manual",
    )

    private fun decode(text: String?): Track? = text?.let { runCatching { json.decodeFromString(Track.serializer(), it) }.getOrNull() }

    companion object {
        const val KEY_STATUS = "sync_status"
        private val json = Json { ignoreUnknownKeys = true }
    }
}
