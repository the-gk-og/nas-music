package org.nasmusic.app.spoken

import java.io.File
import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.Request
import org.nasmusic.app.data.api.SpokenApi
import org.nasmusic.app.net.RoutedCallFactory

/**
 * Podcast episodes saved on the phone (files/spoken/<episodeId>.audio), fetched through the server
 * (GET /stream/episode/{id}: its downloaded copy or a proxy of the feed), so they play with no network.
 * Shows with autoDownload keep their newest [AUTO_KEEP] unplayed episodes; played downloads of those
 * shows are removed again. Resumes a partial file with Range.
 */
class SpokenDownloads(private val callFactory: Call.Factory, private val dir: File) {
    private val _ids = MutableStateFlow(scan())
    val ids: StateFlow<Set<String>> = _ids.asStateFlow()
    private val _busy = MutableStateFlow<Set<String>>(emptySet())
    val busy: StateFlow<Set<String>> = _busy.asStateFlow()

    private fun scan(): Set<String> =
        dir.listFiles()?.filter { it.name.endsWith(".audio") }?.map { it.name.removeSuffix(".audio") }?.toSet() ?: emptySet()

    fun file(id: String): File? = File(dir, "${safe(id)}.audio").takeIf { it.exists() }

    suspend fun download(id: String): File = withContext(Dispatchers.IO) {
        dir.mkdirs()
        _busy.value = _busy.value + id
        try {
            val target = File(dir, "${safe(id)}.audio")
            val part = File(dir, "${safe(id)}.part")
            val have = if (part.exists()) part.length() else 0L
            val req = Request.Builder().url(RoutedCallFactory.virtual("stream/episode/${safe(id)}"))
                .apply { if (have > 0) header("Range", "bytes=$have-") }.build()
            callFactory.newCall(req).execute().use { resp ->
                val append = when (resp.code) {
                    206 -> true
                    200 -> false
                    416 -> { part.delete(); throw IOException("Range not satisfiable, try again") }
                    else -> throw IOException("HTTP ${resp.code}")
                }
                val body = resp.body ?: throw IOException("empty body")
                java.io.FileOutputStream(part, append).use { out -> body.byteStream().copyTo(out, 1 shl 16) }
            }
            if (!part.renameTo(target)) throw IOException("could not save the episode")
            _ids.value = _ids.value + id
            target
        } finally {
            _busy.value = _busy.value - id
        }
    }

    fun delete(id: String) {
        File(dir, "${safe(id)}.audio").delete()
        File(dir, "${safe(id)}.part").delete()
        _ids.value = _ids.value - id
    }

    /** Auto-download pass (on the home network): newest unplayed episodes of autoDownload shows. */
    suspend fun autoDownload(api: SpokenApi): Int {
        var n = 0
        val shows = runCatching { api.podcasts().podcasts }.getOrNull() ?: return 0
        for (show in shows.filter { it.autoDownload }) {
            val eps = runCatching { api.episodes(show.id, "all", 50).episodes }.getOrNull() ?: continue
            val keep = eps.filter { it.progress?.played != true }.take(AUTO_KEEP).map { it.id }.toSet()
            eps.filter { it.progress?.played == true && it.id in _ids.value }.forEach { delete(it.id) }
            for (id in keep) if (id !in _ids.value && runCatching { download(id) }.isSuccess) n++
        }
        return n
    }

    private fun safe(id: String) = id.replace(Regex("[^A-Za-z0-9_-]"), "_")

    companion object {
        const val AUTO_KEEP = 3
    }
}
