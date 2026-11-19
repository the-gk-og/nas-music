package org.nasmusic.wear.offline

import java.io.File
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import org.nasmusic.app.data.api.AudioFormat
import org.nasmusic.app.data.api.Track

/** A list the user asked to keep on the watch. [kind] = playlist | album | favourites. */
@Serializable
data class OfflineList(
    val kind: String,
    val id: String,
    val name: String,
    /** Snapshot from the last sync, so the list can be browsed and played with no network. */
    val tracks: List<Track> = emptyList(),
)

@Serializable
private data class OfflineFile(val lists: List<OfflineList> = emptyList())

data class SyncStatus(
    val running: Boolean = false,
    val done: Int = 0,
    val total: Int = 0,
    val message: String? = null,
    val lastOkAtMs: Long = 0,
)

/**
 * The offline lists and the downloaded files (`files/music/<trackId>.opus`, written by the shared
 * [org.nasmusic.app.sync.Downloader]). The watch only downloads Opus: FLAC would fill it in no time.
 */
class OfflineStore(root: File) {
    val musicDir = File(root, "music")
    private val stateFile = File(root, "offline.json")
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    private val _lists = MutableStateFlow(load())
    val lists: StateFlow<List<OfflineList>> = _lists.asStateFlow()

    private val _status = MutableStateFlow(SyncStatus())
    val status: StateFlow<SyncStatus> = _status.asStateFlow()

    private fun load(): List<OfflineList> = runCatching {
        json.decodeFromString(OfflineFile.serializer(), stateFile.readText()).lists
    }.getOrDefault(emptyList())

    @Synchronized private fun save(lists: List<OfflineList>) {
        stateFile.parentFile?.mkdirs()
        val tmp = File(stateFile.path + ".tmp")
        tmp.writeText(json.encodeToString(OfflineFile.serializer(), OfflineFile(lists)))
        tmp.renameTo(stateFile)
        _lists.value = lists
    }

    fun isOffline(kind: String, id: String) = _lists.value.any { it.kind == kind && it.id == id }

    fun add(kind: String, id: String, name: String, tracks: List<Track> = emptyList()) {
        if (isOffline(kind, id)) return
        save(_lists.value + OfflineList(kind, id, name, tracks))
    }

    fun remove(kind: String, id: String) = save(_lists.value.filterNot { it.kind == kind && it.id == id })

    fun replaceSnapshot(kind: String, id: String, tracks: List<Track>) =
        save(_lists.value.map { if (it.kind == kind && it.id == id) it.copy(tracks = tracks) else it })

    fun fileFor(trackId: String): File = File(musicDir, "${safe(trackId)}.${AudioFormat.OPUS.wire}")

    /** The finished download for [trackId], or null. */
    fun localFile(trackId: String): File? = fileFor(trackId).takeIf { it.isFile && it.length() > 0 }

    fun downloadedCount(list: OfflineList) = list.tracks.count { localFile(it.id) != null }

    fun usedBytes(): Long = musicDir.listFiles()?.sumOf { it.length() } ?: 0L

    fun setStatus(s: SyncStatus) { _status.value = s }

    companion object {
        /** Must match Downloader.safe(). */
        fun safe(id: String) = id.replace(Regex("[^A-Za-z0-9._-]"), "_")
    }
}
