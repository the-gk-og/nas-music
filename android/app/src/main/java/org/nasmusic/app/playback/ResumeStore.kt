package org.nasmusic.app.playback

import java.io.File
import java.time.Instant
import kotlinx.serialization.Serializable
import org.nasmusic.app.connect.PlaybackState
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.spoken.SpokenItem

/** One entry of the saved music queue (enough to rebuild its MediaItem offline). */
@Serializable
data class ResumeEntry(val track: Track, val source: String = PlaySource.LIBRARY.wire, val mixId: String? = null)

/** What was loaded in the player: a music queue, or a podcast episode / audiobook / station. Written when it changes. */
@Serializable
data class ResumeQueue(
    val entries: List<ResumeEntry> = emptyList(),
    val spoken: SpokenItem? = null,
    /** The server queue it mirrors (null offline / spoken). */
    val queueId: String? = null,
)

/** Where in it playback was. Written often (every few seconds while playing, on pause, skip, close). */
@Serializable
data class ResumePosition(
    val index: Int = 0,
    val positionMs: Long = 0,
    /** mediaId at [index], to find the right item if the queue file and this one disagree. */
    val itemId: String? = null,
    val shuffle: Boolean = false,
    /** Player.REPEAT_MODE_* */
    val repeat: Int = 0,
    val savedAt: Long = 0,
    val queueId: String? = null,
)

/** Pure rules for restoring playback (unit-tested). */
object ResumeLogic {
    /**
     * The (index, positionMs) to put the player at, given the restored queue's mediaIds. The saved index wins
     * when the item there is the saved one; otherwise the saved item is looked up (the queue file was written
     * after a move); otherwise the index is clamped and the position dropped. Null = nothing to restore.
     */
    fun restorePoint(ids: List<String>, pos: ResumePosition?): Pair<Int, Long>? {
        if (ids.isEmpty()) return null
        if (pos == null) return 0 to 0L
        val p = pos.positionMs.coerceAtLeast(0)
        if (pos.index in ids.indices && (pos.itemId == null || ids[pos.index] == pos.itemId)) return pos.index to p
        val found = pos.itemId?.let { ids.indexOf(it) } ?: -1
        if (found >= 0) return found to p
        return pos.index.coerceIn(0, ids.size - 1) to 0L
    }

    /**
     * After restoring the local snapshot, should this device adopt what the server last saw playing instead?
     * Yes when another device played something more recently (so the phone opens where the web player stopped),
     * that device isn't playing now (then this phone is just a remote), and it was a real queue or spoken item.
     */
    fun preferServer(localSavedAt: Long?, server: PlaybackState?, myDeviceId: String?, slackMs: Long = 5_000): Boolean {
        val s = server ?: return false
        if (s.playing) return false
        if (s.queueId == null && s.media == null) return false
        if (s.activeDeviceId != null && s.activeDeviceId == myDeviceId) return false
        val at = s.positionAt?.let { runCatching { Instant.parse(it).toEpochMilli() }.getOrNull() } ?: return false
        return localSavedAt == null || at > localSavedAt + slackMs
    }
}

/** On-disk snapshot of the player for "resume exactly where you left off" (app killed, swiped away, rebooted). */
class ResumeStore(private val dir: File) {
    private val queueFile get() = File(dir, "resume_queue.json")
    private val posFile get() = File(dir, "resume_position.json")

    @Synchronized
    fun saveQueue(q: ResumeQueue) = write(queueFile, ApiFactory.json.encodeToString(ResumeQueue.serializer(), q))

    @Synchronized
    fun savePosition(p: ResumePosition) = write(posFile, ApiFactory.json.encodeToString(ResumePosition.serializer(), p))

    @Synchronized
    fun load(): Pair<ResumeQueue, ResumePosition?>? {
        val q = read(queueFile)?.let { runCatching { ApiFactory.json.decodeFromString(ResumeQueue.serializer(), it) }.getOrNull() } ?: return null
        if (q.entries.isEmpty() && q.spoken == null) return null
        val p = read(posFile)?.let { runCatching { ApiFactory.json.decodeFromString(ResumePosition.serializer(), it) }.getOrNull() }
        return q to p
    }

    @Synchronized
    fun savedAt(): Long? = read(posFile)?.let { runCatching { ApiFactory.json.decodeFromString(ResumePosition.serializer(), it).savedAt }.getOrNull() }

    @Synchronized
    fun clear() { queueFile.delete(); posFile.delete() }

    private fun read(f: File): String? = runCatching { f.takeIf { it.exists() }?.readText() }.getOrNull()

    /** Write-then-rename, so a kill mid-write never leaves half a file. */
    private fun write(f: File, text: String) {
        runCatching {
            dir.mkdirs()
            val tmp = File(dir, f.name + ".tmp")
            tmp.writeText(text)
            if (!tmp.renameTo(f)) { f.delete(); tmp.renameTo(f) }
        }
    }
}
