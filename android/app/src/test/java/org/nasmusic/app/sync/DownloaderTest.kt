package org.nasmusic.app.sync

import java.io.File
import java.nio.file.Files
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.nasmusic.app.data.api.AudioFormat
import org.nasmusic.app.net.HttpClients
import org.nasmusic.app.net.Route
import org.nasmusic.app.net.RoutedCallFactory
import org.nasmusic.app.net.ServerConfig

class DownloaderTest {
    private val server = MockWebServer()
    private lateinit var dir: File
    private lateinit var downloader: Downloader

    @Before fun setUp() {
        server.start()
        dir = Files.createTempDirectory("dl").toFile()
        val cfg = ServerConfig(lanUrl = "https://192.0.2.1", tunnelUrl = server.url("/").toString(), certSha256 = "00".repeat(32), deviceToken = "t")
        val base = OkHttpClient()
        val clients = HttpClients(base, cfg)
        downloader = Downloader(RoutedCallFactory({ clients }, { Route.TUNNEL }, base), dir)
    }

    @After fun tearDown() { server.shutdown(); dir.deleteRecursively() }

    @Test fun `fresh download requests opus at the bitrate and finalises the file`() = runBlocking {
        server.enqueue(MockResponse().setBody("hello"))
        val f = downloader.download("t1", AudioFormat.OPUS, 160)
        assertEquals("hello", f.readText())
        val req = server.takeRequest()
        assertEquals("/api/v1/stream/t1?format=opus&bitrate=160", req.path)
        assertNull(req.getHeader("Range"))
        assertFalse(File(f.path + ".part").exists())
    }

    @Test fun `partial file resumes with a Range request`() = runBlocking {
        File(downloader.fileFor("t2", AudioFormat.FLAC).path + ".part").writeText("abc")
        server.enqueue(MockResponse().setResponseCode(206).setBody("defg"))
        val f = downloader.download("t2", AudioFormat.FLAC, 160)
        assertEquals("abcdefg", f.readText())
        val req = server.takeRequest()
        assertEquals("bytes=3-", req.getHeader("Range"))
        assertEquals("/api/v1/stream/t2?format=flac", req.path)
    }

    @Test fun `server ignoring Range restarts from scratch`() = runBlocking {
        File(downloader.fileFor("t3", AudioFormat.OPUS).path + ".part").writeText("junk")
        server.enqueue(MockResponse().setResponseCode(200).setBody("full"))
        assertEquals("full", downloader.download("t3", AudioFormat.OPUS, 160).readText())
    }
}
