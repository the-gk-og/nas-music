package org.nasmusic.app.upload

import java.io.IOException
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.OkHttpClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class UploadEngineTest {
    private class Bytes(private val b: ByteArray) : ChunkSource {
        override val size = b.size.toLong()
        override fun read(offset: Long, length: Int) = b.copyOfRange(offset.toInt(), offset.toInt() + length)
    }

    /** In-memory server: keeps the bytes, answers like the real one; `script` can inject failures per append. */
    private class Fake(val size: Long, val script: MutableList<(Long) -> Exception?> = mutableListOf()) : UploadTransport {
        val received = java.io.ByteArrayOutputStream()
        val appends = mutableListOf<Long>()
        var known = true
        var creates = 0
        fun s(status: String = if (received.size().toLong() == size) "done" else "uploading") =
            UploadSession("s1", "a.flac", size, received.size().toLong(), status = status)

        override suspend fun create(filename: String, bytes: Long, visibility: String): UploadSession { creates++; known = true; return s() }
        override suspend fun status(id: String): UploadSession = if (known) s() else throw UploadRefused(404, "no such upload")
        override suspend fun append(id: String, offset: Long, chunk: ByteArray): AppendResult {
            appends += offset
            script.removeFirstOrNull()?.invoke(offset)?.let { throw it }
            if (offset != received.size().toLong()) return AppendResult.Mismatch(received.size().toLong())
            received.write(chunk)
            return AppendResult.Ok(s())
        }
    }

    private val data = ByteArray(10) { it.toByte() }

    @Test fun sendsInChunksWithProgress() = runBlocking {
        val f = Fake(10)
        val progress = mutableListOf<Long>()
        val s = UploadEngine.send(f, Bytes(data), "a.flac", "everyone", chunkBytes = 4, onProgress = { sent, _ -> progress += sent })
        assertEquals("done", s.status)
        assertEquals(listOf(0L, 4L, 8L), f.appends)
        assertEquals(listOf(0L, 4L, 8L, 10L), progress)
        assertTrue(data.contentEquals(f.received.toByteArray()))
    }

    @Test fun resumesAnInterruptedSessionFromTheServerOffset() = runBlocking {
        val f = Fake(10, mutableListOf({ null }, { IOException("connection reset") }))
        var sid: String? = null
        try {
            UploadEngine.send(f, Bytes(data), "a.flac", "everyone", chunkBytes = 4, onSession = { sid = it.id })
            fail("expected the network error to reach the worker")
        } catch (_: IOException) {
        }
        // WorkManager runs the job again with the saved session id
        val s = UploadEngine.send(f, Bytes(data), "a.flac", "everyone", sessionId = sid, chunkBytes = 4)
        assertEquals("done", s.status)
        assertEquals(1, f.creates)
        assertEquals(listOf(0L, 4L, 4L, 8L), f.appends)
        assertTrue(data.contentEquals(f.received.toByteArray()))
    }

    @Test fun startsOverWhenTheServerForgotTheSession() = runBlocking {
        val f = Fake(10).apply { known = false }
        val s = UploadEngine.send(f, Bytes(data), "a.flac", "everyone", sessionId = "old", chunkBytes = 4)
        assertEquals("done", s.status)
        assertEquals(1, f.creates)
    }

    @Test fun apiTransportMapsResponses() = runBlocking {
        val server = MockWebServer()
        server.enqueue(MockResponse().setResponseCode(409).setBody("""{"detail":"offset mismatch","offset":4}"""))
        server.enqueue(MockResponse().setResponseCode(415).setBody("""{"detail":"not an audio file"}"""))
        server.enqueue(MockResponse().setBody("""{"id":"s1","filename":"a.flac","bytes":10,"offset":10,"status":"done",
            "upload":{"id":"u1","path":"alice/A/B/01 X.flac","trackId":"t","bytes":10,"format":"flac"}}"""))
        server.start()
        val api = UploadsApi.create(OkHttpClient(), server.url("/api/v1/").toString())
        val t = ApiTransport(api, Json { ignoreUnknownKeys = true })
        assertEquals(AppendResult.Mismatch(4), t.append("s1", 0, ByteArray(4)))
        try {
            t.append("s1", 4, ByteArray(4)); fail()
        } catch (e: UploadRefused) {
            assertEquals(415, e.code); assertEquals("not an audio file", e.message)
        }
        val ok = t.append("s1", 4, ByteArray(6)) as AppendResult.Ok
        assertEquals("alice/A/B/01 X.flac", ok.session.upload?.path)
        val req = server.takeRequest()
        assertEquals("PATCH", req.method)
        assertEquals("/api/v1/uploads/s1", req.path)
        assertEquals("0", req.getHeader("Upload-Offset"))
        server.shutdown()
    }

    @Test fun uploadNames() {
        assertEquals("song.flac", UploadWorker.uploadName("song.flac", "audio/flac"))
        assertEquals("Voice 001.m4a", UploadWorker.uploadName("Voice 001", "audio/mp4"))
        assertEquals("x.wav", UploadWorker.uploadName("dir/x", "audio/x-wav"))
    }
}
