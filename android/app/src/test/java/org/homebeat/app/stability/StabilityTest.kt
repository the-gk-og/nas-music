package org.homebeat.app.stability

import java.io.File
import java.nio.file.Files
import java.time.Instant
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.homebeat.app.connect.ConnectClient
import org.homebeat.app.connect.ConnectMessage
import org.homebeat.app.connect.ConnectProtocol
import org.homebeat.app.data.api.ApiFactory
import org.homebeat.app.diag.BugReport
import org.homebeat.app.diag.CrashLog
import org.homebeat.app.net.Route
import org.homebeat.app.playback.PlaybackRecovery
import androidx.media3.common.PlaybackException

class StabilityTest {
    @Test fun `crash text has version, thread, screen and stack`() {
        CrashLog.currentScreen = "foryou"
        val t = CrashLog.format("crash", "main", IllegalStateException("MediaController method is called from a wrong thread"), now = Instant.parse("2026-11-19T03:00:00Z"))
        assertTrue(t.startsWith("crash at 2026-11-19T03:00:00Z"))
        assertTrue("screen foryou" in t)
        assertTrue("IllegalStateException: MediaController method is called from a wrong thread" in t)
        assertTrue("StabilityTest" in t) // stack trace included
    }

    @Test fun `bug report keeps the last 2000 log lines and the crash files`() {
        val log = (1..2500).joinToString("\n") { "line $it" }
        val dir = Files.createTempDirectory("crashes").toFile()
        val f = File(dir, "crash-1.txt").apply { writeText("boom") }
        val body = BugReport.build("it crashed", "now", mapOf("route" to "LAN"), log, listOf(f))
        val lines = body.logs.lines()
        assertEquals(2000, lines.size)
        assertEquals("line 501", lines.first())
        assertEquals("line 2500", lines.last())
        assertEquals("===== crash-1.txt =====\nboom", body.crash)
        assertEquals("now", body.extra!!["screen"])
        assertEquals("LAN", body.extra!!["route"])
        dir.deleteRecursively()
    }

    @Test fun `bug report goes to POST me-bug-report`() = runBlocking {
        val server = MockWebServer().apply { start() }
        try {
            val api = ApiFactory.create(OkHttpClient(), server.url("/api/v1/").toString())
            server.enqueue(MockResponse().setBody("""{"id":"br-12"}"""))
            val r = api.bugReport(BugReport.build("x", "home", emptyMap(), "a\nb", emptyList()))
            assertEquals("br-12", r.id)
            val req = server.takeRequest()
            assertEquals("POST", req.method)
            assertEquals("/api/v1/me/bug-report", req.path)
            val body = req.body.readUtf8()
            assertTrue(body.contains("\"description\":\"x\"") && body.contains("\"logs\":\"a\\nb\"") && body.contains("\"appVersion\""))
        } finally {
            server.shutdown()
        }
    }

    @Test fun `playback recovery - network errors retry with backoff, bad tracks skip`() {
        val net = PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED
        assertEquals(PlaybackRecovery.Action.Retry(1000), PlaybackRecovery.decide(net, 0, true))
        assertEquals(PlaybackRecovery.Action.Retry(8000), PlaybackRecovery.decide(net, 3, true))
        assertEquals(PlaybackRecovery.Action.Retry(30_000), PlaybackRecovery.decide(net, 5, true))
        assertEquals(PlaybackRecovery.Action.Stop, PlaybackRecovery.decide(net, PlaybackRecovery.MAX_RETRIES, true))
        assertEquals(PlaybackRecovery.Action.Skip, PlaybackRecovery.decide(PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS, 0, true))
        assertEquals(PlaybackRecovery.Action.Stop, PlaybackRecovery.decide(PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS, 0, false))
        assertEquals(PlaybackRecovery.Action.Skip, PlaybackRecovery.decide(PlaybackException.ERROR_CODE_DECODING_FAILED, 0, true))
        assertEquals(PlaybackRecovery.Action.Retry(1000), PlaybackRecovery.decide(PlaybackException.ERROR_CODE_TIMEOUT, 0, true))
    }

    @Test fun `connect events wait for the playback service and takeover starts it`() = runBlocking {
        val c = ConnectClient({ null }, MutableStateFlow(Route.OFFLINE), CoroutineScope(Dispatchers.Unconfined), { "x" }, "1")
        var started = 0
        c.onTakeover = { started++ }
        c.handle(ConnectProtocol.parse("""{"type":"welcome","deviceId":"me","devices":[]}"""))
        // Takeover arrives before anything collects events (service not running yet)…
        c.handle(ConnectProtocol.parse("""{"type":"takeover","state":{"queueId":"q1","index":2,"rev":5}}"""))
        assertEquals(1, started)
        // …and is still delivered once the service starts collecting.
        val m = withTimeout(1000) { c.events.first() }
        assertTrue(m is ConnectMessage.Takeover)
        assertEquals("q1", (m as ConnectMessage.Takeover).state.queueId)
        assertTrue(c.isActiveHere)
    }
}
