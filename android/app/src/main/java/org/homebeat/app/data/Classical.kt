package org.homebeat.app.data

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import org.homebeat.app.data.api.HomebeatApi
import org.homebeat.app.data.api.PlaylistFolder
import org.homebeat.app.data.api.Track
import org.homebeat.app.data.api.UserSettings

/** api 0.10 classical mode: group albums by work, movement names, composer before performer. Pure helpers. */
object Classical {
    data class Group(val workId: String?, val work: String?, val composer: String?, val tracks: List<Track>, val start: Int)

    /** Consecutive tracks of the same work form one group; other tracks are grouped as they come. */
    fun groupByWork(tracks: List<Track>): List<Group> {
        val out = mutableListOf<Group>()
        tracks.forEachIndexed { i, t ->
            val last = out.lastOrNull()
            if (last != null && last.workId == t.workId) out[out.size - 1] = last.copy(tracks = last.tracks + t)
            else out += Group(t.workId, t.work, t.composer, listOf(t), i)
        }
        return out
    }

    fun hasWorks(tracks: List<Track>) = tracks.any { it.workId != null }

    private val ROMAN = listOf(100 to "C", 90 to "XC", 50 to "L", 40 to "XL", 10 to "X", 9 to "IX", 5 to "V", 4 to "IV", 1 to "I")

    fun toRoman(n: Int): String {
        var left = n
        val sb = StringBuilder()
        for ((v, r) in ROMAN) while (left >= v) { sb.append(r); left -= v }
        return sb.toString()
    }

    /** Under a work heading: "II. Andante con moto"; otherwise the title. */
    fun movementTitle(t: Track): String {
        val m = t.movement
        if (t.work == null || m == null) return t.title
        return t.movementNo?.let { "${toRoman(it)}. $m" } ?: m
    }

    /** Composer before performer: "Ludwig van Beethoven · Berliner Philharmoniker". */
    fun artistLine(t: Track, classical: Boolean): String {
        val comp = t.composer
        return if (!classical || comp.isNullOrBlank() || comp == t.artist) t.artist else "$comp · ${t.artist}"
    }
}

/** Playlist folder tree helpers (the server lists folders depth first: parents before children). */
object PlaylistFolders {
    fun children(all: List<PlaylistFolder>, parentId: String?) = all.filter { it.parentId == parentId }

    /** From the top level down to [id]. */
    fun path(all: List<PlaylistFolder>, id: String?): List<PlaylistFolder> {
        val byId = all.associateBy { it.id }
        val out = ArrayDeque<PlaylistFolder>()
        val seen = mutableSetOf<String>()
        var f: PlaylistFolder? = id?.let { byId[it] }
        while (f != null && seen.add(f.id)) {
            out.addFirst(f)
            f = f.parentId?.let { byId[it] }
        }
        return out.toList()
    }

    /** "Move to" choices: (null = top level, depth 0) then each folder with its depth; excluding [excluding] and its subtree. */
    fun choices(all: List<PlaylistFolder>, excluding: String? = null): List<Pair<PlaylistFolder?, Int>> {
        val out = mutableListOf<Pair<PlaylistFolder?, Int>>(null to 0)
        fun walk(parent: String?, depth: Int) {
            for (f in children(all, parent)) {
                if (f.id == excluding) continue
                out += f to depth
                walk(f.id, depth + 1)
            }
        }
        walk(null, 1)
        return out
    }
}

/** The synced `library.classical` setting, cached on the phone so album pages don't wait for the server. */
object ClassicalMode {
    const val KEY = "library.classical"
    private val state = MutableStateFlow(false)
    val on: StateFlow<Boolean> = state
    private var loaded = false

    /** Read the cached value, then the server's (once per process). */
    suspend fun ensure(context: Context, api: HomebeatApi) {
        if (loaded) return
        loaded = true
        val prefs = context.getSharedPreferences("classical", Context.MODE_PRIVATE)
        state.value = prefs.getBoolean("on", false)
        val remote = runCatching { (api.mySettings().settings[KEY] as? JsonPrimitive)?.booleanOrNull }.getOrNull() ?: return
        state.value = remote
        prefs.edit().putBoolean("on", remote).apply()
    }

    suspend fun set(context: Context, api: HomebeatApi, on: Boolean) {
        state.value = on
        context.getSharedPreferences("classical", Context.MODE_PRIVATE).edit().putBoolean("on", on).apply()
        runCatching { api.putMySettings(UserSettings(mapOf(KEY to JsonPrimitive(on)))) }
    }
}
