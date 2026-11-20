package org.nasmusic.app.data.db

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/** A feedback event waiting to be uploaded to POST /events. [id] is the idempotency UUID. */
@Entity(tableName = "pending_events")
data class EventEntity(
    @PrimaryKey val id: String,
    val type: String,
    val trackId: String,
    val at: String,
    val positionS: Double? = null,
    val durationS: Double? = null,
    val source: String? = null,
    val mixId: String? = null,
    val rating: Int? = null,
    val offline: Boolean = false,
    val createdAtMs: Long = System.currentTimeMillis(),
    /** v2 opt-in context (JSON object), only enabled signals. */
    val context: String? = null,
)

/** A track held offline in app-private storage. */
@Entity(tableName = "downloads")
data class DownloadEntity(
    @PrimaryKey val trackId: String,
    val format: String,
    val path: String,
    val bytes: Long,
    /** "mix" or "favourite". */
    val why: String,
    val mixIds: String = "",
    val complete: Boolean,
    /** Track metadata JSON so the offline library can be shown without the server. */
    val trackJson: String? = null,
    val updatedAtMs: Long = System.currentTimeMillis(),
    // ---- v4: smart downloads ----
    /** Who manages the file when it isn't protected: "sync" (offline favourites manifest) or "smart". */
    @ColumnInfo(defaultValue = "sync") val origin: String = "sync",
    /** The user downloaded this track themself: never evicted. */
    @ColumnInfo(defaultValue = "0") val manual: Boolean = false,
    /** Keys of collections kept offline that contain it ("playlist:<id>", "album:<id>"), comma-separated. */
    @ColumnInfo(defaultValue = "") val pins: String = "",
    /** Opus bitrate of the file; 0 for FLAC or older downloads (which used 160). */
    @ColumnInfo(defaultValue = "0") val kbps: Int = 0,
    /** Smart downloads: why it was picked (comma-separated) and its priority, for the Downloads screen. */
    @ColumnInfo(defaultValue = "") val reasons: String = "",
    @ColumnInfo(defaultValue = "0") val priority: Double = 0.0,
) {
    val pinKeys: List<String> get() = pins.split(',').filter { it.isNotBlank() }
    /** Manual downloads and tracks of kept collections are never evicted. */
    val isProtected: Boolean get() = manual || pinKeys.isNotEmpty()
    val qualityKey: String get() = if (format == "flac") "flac" else "opus${if (kbps > 0) kbps else 160}"
}

/** A playlist or album the user keeps offline ("Download" on its page). Its tracks are protected. */
@Entity(tableName = "offline_pins")
data class OfflinePinEntity(
    /** "playlist:<id>" or "album:<id>" */
    @PrimaryKey val key: String,
    val type: String,
    val id: String,
    val title: String,
    /** Track ids from the last successful refresh, comma-separated. */
    val trackIds: String = "",
    val addedAtMs: Long = System.currentTimeMillis(),
    val refreshedAtMs: Long = 0,
) {
    val ids: List<String> get() = trackIds.split(',').filter { it.isNotBlank() }
    companion object { fun key(type: String, id: String) = "$type:$id" }
}

/** Cached lyrics. [status] is "found" (with [json]) or "none" (server said 404, don't keep asking). */
@Entity(tableName = "lyrics")
data class LyricsEntity(
    @PrimaryKey val trackId: String,
    val status: String,
    val json: String? = null,
    val fetchedAtMs: Long = System.currentTimeMillis(),
)

/** Last known server responses (today's mixes, favourites) for offline display. */
@Entity(tableName = "cache")
data class CacheEntity(
    @PrimaryKey val key: String,
    val json: String,
    val updatedAtMs: Long = System.currentTimeMillis(),
)
