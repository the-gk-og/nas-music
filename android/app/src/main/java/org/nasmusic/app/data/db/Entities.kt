package org.nasmusic.app.data.db

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
)

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
