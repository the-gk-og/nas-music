package org.nasmusic.app.data.db

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Upsert
import kotlinx.coroutines.flow.Flow
import org.nasmusic.app.feedback.EventQueueStore

@Dao
interface EventDao : EventQueueStore {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    override suspend fun insert(event: EventEntity)

    @Query("SELECT * FROM pending_events ORDER BY createdAtMs, id LIMIT :limit")
    override suspend fun oldest(limit: Int): List<EventEntity>

    @Query("DELETE FROM pending_events WHERE id IN (:ids)")
    override suspend fun delete(ids: List<String>)

    @Query("SELECT COUNT(*) FROM pending_events")
    fun count(): Flow<Int>
}

@Dao
interface DownloadDao {
    @Upsert
    suspend fun upsert(d: DownloadEntity)

    @Query("SELECT * FROM downloads")
    suspend fun all(): List<DownloadEntity>

    @Query("SELECT * FROM downloads")
    fun observeAll(): Flow<List<DownloadEntity>>

    @Query("SELECT * FROM downloads WHERE trackId = :id AND complete = 1")
    suspend fun completed(id: String): DownloadEntity?

    @Query("DELETE FROM downloads WHERE trackId = :id")
    suspend fun delete(id: String)
}

@Dao
interface LyricsDao : org.nasmusic.app.lyrics.LyricsCacheStore {
    @Query("SELECT * FROM lyrics WHERE trackId = :trackId")
    override suspend fun get(trackId: String): LyricsEntity?

    @Upsert
    override suspend fun put(entity: LyricsEntity)

    @Query("DELETE FROM lyrics WHERE trackId = :trackId")
    override suspend fun delete(trackId: String)

    /** Drops on-demand (streamed) entries older than [cutoffMs]; lyrics of downloaded tracks are kept. */
    @Query("DELETE FROM lyrics WHERE fetchedAtMs < :cutoffMs AND trackId NOT IN (SELECT trackId FROM downloads)")
    suspend fun pruneStreamed(cutoffMs: Long)
}

@Dao
interface CacheDao {
    @Upsert
    suspend fun put(c: CacheEntity)

    @Query("SELECT * FROM cache WHERE `key` = :key")
    suspend fun get(key: String): CacheEntity?

    @Query("SELECT * FROM cache WHERE `key` = :key")
    fun observe(key: String): Flow<CacheEntity?>
}
