package org.homebeat.app.voice

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.util.zip.ZipInputStream

/**
 * The offline English speech model for voice commands (Vosk small en-US, Apache-2.0, about 40 MB). It isn't
 * bundled in the APK: it's downloaded once, on the user's say-so, into the app's private storage. Speech is
 * recognised on the phone; nothing but the recognised text is sent anywhere (to your own server).
 */
class SpeechModel(private val root: File, private val http: OkHttpClient) {
    val dir: File get() = File(root, NAME)

    fun isInstalled(): Boolean = ModelFiles.looksComplete(dir)

    /** Download and unpack the model; [progress] gets 0..1 (or -1 when the size is unknown). */
    suspend fun download(progress: (Float) -> Unit = {}) = withContext(Dispatchers.IO) {
        val tmp = File(root, "$NAME.partial").apply { deleteRecursively() }
        try {
            http.newCall(Request.Builder().url(URL).build()).execute().use { res ->
                if (!res.isSuccessful) throw IOException("HTTP ${res.code}")
                val body = res.body ?: throw IOException("empty response")
                val total = body.contentLength()
                val counted = CountingStream(body.byteStream()) { n -> progress(if (total > 0) n.toFloat() / total else -1f) }
                ModelFiles.unzip(counted, tmp)
            }
            val unpacked = ModelFiles.modelRoot(tmp) ?: throw IOException("not a speech model")
            dir.deleteRecursively()
            if (!unpacked.renameTo(dir)) unpacked.copyRecursively(dir, overwrite = true)
        } finally {
            tmp.deleteRecursively()
        }
        if (!isInstalled()) throw IOException("the speech model is incomplete")
    }

    fun delete() { dir.deleteRecursively() }

    companion object {
        const val NAME = "vosk-en-us"
        const val URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
        const val SIZE_MB = 40
    }
}

/** Pure file helpers (unit-tested). */
object ModelFiles {
    /** Unzip [input] into [dest], refusing entries that would land outside it ("zip slip"). */
    fun unzip(input: InputStream, dest: File) {
        dest.mkdirs()
        val base = dest.canonicalFile
        ZipInputStream(input.buffered()).use { zip ->
            while (true) {
                val e = zip.nextEntry ?: break
                val out = File(base, e.name).canonicalFile
                if (out != base && !out.path.startsWith(base.path + File.separator)) throw IOException("bad entry ${e.name}")
                if (e.isDirectory) out.mkdirs() else {
                    out.parentFile?.mkdirs()
                    out.outputStream().use { zip.copyTo(it) }
                }
            }
        }
    }

    /** The folder holding the model (the zip has one top folder, vosk-model-…), or null. */
    fun modelRoot(unpacked: File): File? {
        if (looksComplete(unpacked)) return unpacked
        return unpacked.listFiles()?.firstOrNull { it.isDirectory && looksComplete(it) }
    }

    fun looksComplete(dir: File): Boolean =
        File(dir, "am/final.mdl").isFile && File(dir, "conf/mfcc.conf").isFile
}

private class CountingStream(private val inner: InputStream, private val onRead: (Long) -> Unit) : InputStream() {
    private var n = 0L
    private var lastReport = 0L
    private fun count(k: Int) {
        if (k <= 0) return
        n += k
        if (n - lastReport >= 256 * 1024) { lastReport = n; onRead(n) }
    }
    override fun read(): Int = inner.read().also { if (it >= 0) count(1) }
    override fun read(b: ByteArray, off: Int, len: Int): Int = inner.read(b, off, len).also { count(it) }
    override fun close() = inner.close()
}
