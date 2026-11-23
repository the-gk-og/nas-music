package org.nasmusic.app.diag

import java.io.File
import java.io.IOException
import java.nio.file.Files
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

class BugReportBodyTest {
    private fun compose(log: String, crashes: List<Pair<String, String>> = emptyList(), max: Int = BugReport.MAX_BODY_BYTES) =
        BugReport.compose("  it broke\nsteps  ", "1.2 (12)", "Android 15 · Google Pixel 8", mapOf("screen" to "now"), log, crashes, max)

    /** Field names must be the ones in api/openapi.yaml's BugReportIn (the server rejects a body without `logs`). */
    @Test fun `body matches BugReportIn in the contract`() {
        val spec = File("../../api/openapi.yaml").readText()
        val schema = spec.substringAfter("    BugReportIn:\n").substringBefore("\n    BugReport:")
        val props = Regex("""^        (\w+): """, RegexOption.MULTILINE).findAll(schema.substringAfter("properties:")).map { it.groupValues[1] }.toSet()
        assertTrue("required: [logs]" in schema)
        val sent = Json.parseToJsonElement(BugReport.encode(compose("a", listOf("crash-1.txt" to "boom")))).jsonObject.keys
        assertTrue("logs" in sent)
        assertEquals(emptySet<String>(), sent - props)
    }

    @Test fun `contract limits are applied`() {
        val b = BugReport.compose("x".repeat(6000), "v".repeat(80), "p".repeat(150), emptyMap(), "l", emptyList())
        assertEquals(5000, b.description!!.length)
        assertEquals(50, b.appVersion!!.length)
        assertEquals(100, b.platform!!.length)
        assertEquals("it broke\nsteps", compose("l").description)
    }

    @Test fun `huge logs and crashes are trimmed under the body limit, newest lines kept`() {
        val log = (1..2000).joinToString("\n") { "11-24 10:00:00.000 1234 1234 I nas-music: \"quoted\" line $it " + "é".repeat(400) }
        val crashes = (1..10).map { "crash-$it.txt" to "java.lang.IllegalStateException: boom $it\n" + "\tat x.y(Z.kt:1)\n".repeat(5000) }
        val b = compose(log, crashes, 1_500_000)
        assertTrue(BugReport.encodedSize(b) <= 1_500_000)
        assertTrue(b.logs.endsWith("line 2000 " + "é".repeat(400)))
        assertTrue(b.logs.startsWith("(older log lines trimmed)"))
        assertTrue(b.crash!!.startsWith("===== crash-1.txt ====="))
        // Even a tiny limit converges.
        assertTrue(BugReport.encodedSize(compose(log, crashes, 20_000)) <= 20_000)
    }

    @Test fun `failure message uses the server's detail`() {
        assertEquals("The server refused the report: invalid bug report (HTTP 422).",
            BugReport.failureMessage(422, """{"detail":"invalid bug report"}"""))
        assertTrue("too many bug reports today" in BugReport.failureMessage(429, """{"detail":"too many bug reports today"}"""))
        assertEquals("Sending failed (HTTP 500).", BugReport.failureMessage(500, "<html>oops</html>"))
        assertTrue("Update the server" in BugReport.failureMessage(404, """{"detail":"Not Found"}"""))
        assertTrue(BugReport.retryable(503) && BugReport.retryable(429) && !BugReport.retryable(422))
    }

    @Test fun `offline reports are queued and sent later`() = runBlocking {
        val dir = Files.createTempDirectory("pending").toFile()
        try {
            val q = BugReportQueue(dir)
            q.add(compose("one"))
            Thread.sleep(2)
            q.add(compose("two"))
            assertEquals(BugReportQueue.Outcome.RetryLater, q.flush { throw IOException("offline") })
            assertEquals(2, q.pending().size)
            val sent = mutableListOf<String>()
            assertEquals(BugReportQueue.Outcome.Done, q.flush { sent += it.logs })
            assertEquals(listOf("one", "two"), sent) // oldest first
            assertTrue(q.pending().isEmpty())
            // A permanent refusal drops the report instead of retrying forever.
            q.add(compose("bad"))
            val refused = HttpException(Response.error<Any>(422, "{}".toResponseBody()))
            assertEquals(BugReportQueue.Outcome.Done, q.flush { throw refused })
            assertFalse(q.pending().isNotEmpty())
            repeat(5) { q.add(compose("r$it")); Thread.sleep(2) }
            assertEquals(BugReportQueue.MAX, q.pending().size)
        } finally {
            dir.deleteRecursively()
        }
    }
}
