package org.nasmusic.app.data.db

import android.content.Context
import androidx.room.AutoMigration
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(
    entities = [EventEntity::class, DownloadEntity::class, CacheEntity::class, LyricsEntity::class, OfflinePinEntity::class],
    version = 4,
    exportSchema = true,
    autoMigrations = [AutoMigration(from = 1, to = 2), AutoMigration(from = 2, to = 3), AutoMigration(from = 3, to = 4)],
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun events(): EventDao
    abstract fun downloads(): DownloadDao
    abstract fun cache(): CacheDao
    abstract fun lyrics(): LyricsDao
    abstract fun pins(): OfflinePinDao

    companion object {
        fun build(context: Context): AppDatabase =
            Room.databaseBuilder(context, AppDatabase::class.java, "nasmusic.db").build()
    }
}
