package org.nasmusic.app.upload

import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import retrofit2.HttpException

/** The server said no (not allowed, quota, not audio, ...): retrying won't help. */
class UploadRefused(val code: Int, message: String) : Exception(message)

sealed interface AppendResult {
    data class Ok(val session: UploadSession) : AppendResult
    /** 409: the server has [offset] bytes; carry on from there. */
    data class Mismatch(val offset: Long) : AppendResult
}

/** What the engine needs from the server (the real one is [ApiTransport]; tests use a fake). */
interface UploadTransport {
    suspend fun create(filename: String, bytes: Long, visibility: String): UploadSession
    suspend fun status(id: String): UploadSession
    suspend fun append(id: String, offset: Long, chunk: ByteArray): AppendResult
}

interface ChunkSource {
    val size: Long
    fun read(offset: Long, length: Int): ByteArray
}

class FileChunkSource(private val file: File) : ChunkSource {
    override val size: Long get() = file.length()
    override fun read(offset: Long, length: Int): ByteArray = RandomAccessFile(file, "r").use { f ->
        f.seek(offset)
        val buf = ByteArray(length)
        f.readFully(buf)
        buf
    }
}

object UploadEngine {
    const val DEFAULT_CHUNK = 4 * 1024 * 1024

    /**
     * Upload [source] as [filename]. With [sessionId] (from an earlier, interrupted attempt) it resumes at the
     * server's offset; a session the server no longer knows starts over. Network errors (IOException) propagate:
     * the caller (a WorkManager job) retries later with the same session id.
     */
    suspend fun send(
        t: UploadTransport,
        source: ChunkSource,
        filename: String,
        visibility: String,
        sessionId: String? = null,
        chunkBytes: Int = DEFAULT_CHUNK,
        onSession: suspend (UploadSession) -> Unit = {},
        onProgress: suspend (sent: Long, total: Long) -> Unit = { _, _ -> },
    ): UploadSession {
        var s = sessionId?.let {
            try {
                t.status(it).takeIf { st -> st.status != "failed" }
            } catch (e: UploadRefused) {
                if (e.code == 404) null else throw e
            }
        } ?: t.create(filename, source.size, visibility)
        onSession(s)
        var offset = s.offset
        onProgress(offset, source.size)
        while (s.status == "uploading") {
            currentCoroutineContext().ensureActive()
            val len = minOf(chunkBytes.toLong(), source.size - offset).toInt()
            when (val r = t.append(s.id, offset, source.read(offset, len))) {
                is AppendResult.Ok -> { s = r.session; offset = s.offset }
                is AppendResult.Mismatch -> offset = r.offset
            }
            onProgress(offset, source.size)
        }
        if (s.status == "failed") throw UploadRefused(415, s.error ?: "upload failed")
        return s
    }
}

/** [UploadTransport] over the Retrofit API: HTTP refusals become [UploadRefused], the rest stays IOException. */
class ApiTransport(private val api: UploadsApi, private val json: Json) : UploadTransport {
    private val octets = "application/offset+octet-stream".toMediaType()

    private suspend fun <T> call(block: suspend () -> T): T = try {
        block()
    } catch (e: HttpException) {
        throw refusal(e.code(), e.response()?.errorBody()?.string())
    }

    private fun refusal(code: Int, body: String?): Exception {
        if (code >= 500 && code != 503) return IOException("server error $code")
        val detail = body?.let { runCatching { json.decodeFromString(OffsetError.serializer(), it).detail }.getOrNull() }
        return UploadRefused(code, detail ?: "HTTP $code")
    }

    override suspend fun create(filename: String, bytes: Long, visibility: String) =
        call { api.create(CreateUpload(filename, bytes, visibility)) }

    override suspend fun status(id: String) = call { api.status(id) }

    override suspend fun append(id: String, offset: Long, chunk: ByteArray): AppendResult {
        val resp = api.append(id, offset, chunk.toRequestBody(octets))
        if (resp.isSuccessful) return AppendResult.Ok(resp.body() ?: throw IOException("empty reply"))
        val body = resp.errorBody()?.string()
        if (resp.code() == 409) {
            val off = body?.let { runCatching { json.decodeFromString(OffsetError.serializer(), it).offset }.getOrNull() }
                ?: resp.headers()["Upload-Offset"]?.toLongOrNull()
            if (off != null) return AppendResult.Mismatch(off)
        }
        throw refusal(resp.code(), body)
    }
}
