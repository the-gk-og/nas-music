package org.homebeat.app.lyrics

import android.content.Context

/**
 * Per-song lyrics timing nudge, kept on this device. Positive = lines appear later (for lyrics that run
 * ahead of the music), negative = earlier.
 */
class LyricsOffsets(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("lyrics_offsets", Context.MODE_PRIVATE)

    fun get(trackId: String): Long = clamp(prefs.getLong(trackId, 0L))

    fun set(trackId: String, ms: Long) {
        val v = clamp(ms)
        prefs.edit().apply { if (v == 0L) remove(trackId) else putLong(trackId, v) }.apply()
    }

    companion object {
        const val STEP_MS = 250L
        private const val LIMIT_MS = 30_000L
        fun clamp(ms: Long): Long = ms.coerceIn(-LIMIT_MS, LIMIT_MS)
    }
}
