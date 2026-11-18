package org.nasmusic.app.sync

import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Request
import org.nasmusic.app.data.api.AudioFormat
import org.nasmusic.app.net.RoutedCallFactory

/**
 * Downloads a track into app-private storage. Resumable: bytes go to `<id>.<ext>.part`, and a later
 * attempt sends `Range: bytes=<partSize>-`. A 206 appends; a 200 starts over. On completion the
 * part file is renamed atomically.
 */
class Downloader(private val callFactory: Call.Factory, private val dir: File) {

    fun fileFor(trackId: String, format: AudioFormat): File =
        File(dir, "${safe(trackId)}.${if (format == AudioFormat.FLAC) "flac" else "opus"}")

    suspend fun download(trackId: String, format: AudioFormat, opusKbps: Int): File = withContext(Dispatchers.IO) {
        dir.mkdirs()
        val target = fileFor(trackId, format)
        val part = File(target.path + ".part")
        val url = RoutedCallFactory.virtual("stream/$trackId").toHttpUrl().newBuilder()
            .addQueryParameter("format", format.wire)
            .apply { if (format == AudioFormat.OPUS) addQueryParameter("bitrate", opusKbps.toString()) }
            .build()
        val have = if (part.exists()) part.length() else 0L
        val req = Request.Builder().url(url).apply { if (have > 0) header("Range", "bytes=$have-") }.build()
        callFactory.newCall(req).execute().use { resp ->
            val append = when (resp.code) {
                206 -> true
                200 -> false
                416 -> { part.delete(); throw IOException("Range not satisfiable, restarting next time") }
                else -> throw IOException("HTTP ${resp.code} downloading $trackId")
            }
            val body = resp.body ?: throw IOException("Empty body")
            FileOutputStream(part, append).use { out ->
                body.byteStream().use { input ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        coroutineContext.ensureActive()
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                    }
                }
            }
        }
        if (!part.renameTo(target)) throw IOException("Could not finalise $target")
        target
    }

    fun partialBytes(trackId: String, format: AudioFormat): Long =
        File(fileFor(trackId, format).path + ".part").takeIf { it.exists() }?.length() ?: 0L

    fun deleteAll(trackId: String) {
        for (f in AudioFormat.entries) {
            val t = fileFor(trackId, f)
            t.delete()
            File(t.path + ".part").delete()
        }
    }

    private fun safe(id: String) = id.replace(Regex("[^A-Za-z0-9._-]"), "_")
}
