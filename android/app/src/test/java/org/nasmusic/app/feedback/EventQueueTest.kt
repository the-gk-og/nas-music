package org.nasmusic.app.feedback

import java.time.Instant
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.NasMusicApi
import org.nasmusic.app.data.db.EventEntity

/** In-memory stand-in for the Room DAO, same semantics (insert ignores duplicate ids, FIFO). */
class FakeQueue : EventQueueStore {
    val rows = LinkedHashMap<String, EventEntity>()
    override suspend fun insert(event: EventEntity) { rows.putIfAbsent(event.id, event) }
    override suspend fun oldest(limit: Int) = rows.values.take(limit)
    override suspend fun delete(ids: List<String>) { ids.forEach { rows.remove(it) } }
}

class EventQueueTest {
    private val server = MockWebServer()
    private lateinit var api: NasMusicApi

    @Before fun setUp() {
        server.start()
        api = ApiFactory.create(OkHttpClient(), server.url("/api/v1/").toString())
    }

    @After fun tearDown() = server.shutdown()

    private fun ev(i: Int) = EventEntity(id = "id-$i", type = "play", trackId = "t$i", at = "2026-11-18T03:00:00Z", createdAtMs = i.toLong())

    @Test fun `uploads in batches and empties the queue`() = runBlocking {
        val q = FakeQueue().apply { repeat(1200) { insert(ev(it)) } }
        repeat(3) { server.enqueue(MockResponse().setBody("""{"accepted":500,"duplicates":0}""")) }
        val out = EventUploader(q, { api.events(it) }, batchSize = 500).uploadAll()
        assertEquals(1200, out.uploaded)
        assertTrue(q.rows.isEmpty())
        assertEquals(3, server.requestCount)
        val first = Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject["events"]!!.jsonArray
        assertEquals(500, first.size)
        assertEquals("id-0", first[0].jsonObject["id"]!!.jsonPrimitive.content)
        assertEquals("/api/v1/events", server.takeRequest().path)
    }

    @Test fun `server error keeps events for the next attempt`() = runBlocking {
        val q = FakeQueue().apply { repeat(10) { insert(ev(it)) } }
        server.enqueue(MockResponse().setResponseCode(500))
        val out = EventUploader(q, { api.events(it) }).uploadAll()
        assertNotNull(out.error)
        assertEquals(0, out.uploaded)
        assertEquals(10, q.rows.size)
    }

    @Test fun `partial progress is kept when a later batch fails`() = runBlocking {
        val q = FakeQueue().apply { repeat(7) { insert(ev(it)) } }
        server.enqueue(MockResponse().setBody("""{"accepted":5,"duplicates":0}"""))
        server.enqueue(MockResponse().setResponseCode(503))
        val out = EventUploader(q, { api.events(it) }, batchSize = 5).uploadAll()
        assertEquals(5, out.uploaded)
        assertEquals(listOf("id-5", "id-6"), q.rows.keys.toList())
    }

    @Test fun `retry after failure resends the same idempotent ids`() = runBlocking {
        val q = FakeQueue().apply { insert(ev(1)) }
        server.enqueue(MockResponse().setResponseCode(502))
        server.enqueue(MockResponse().setBody("""{"accepted":0,"duplicates":1}"""))
        EventUploader(q, { api.events(it) }).uploadAll()
        EventUploader(q, { api.events(it) }).uploadAll()
        val a = server.takeRequest().body.readUtf8()
        val b = server.takeRequest().body.readUtf8()
        assertEquals(a, b)
        assertTrue(q.rows.isEmpty())
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    @Test fun `recorder marks offline events and only requests upload when online`() = runTest {
        val q = FakeQueue()
        var online = false
        var uploads = 0
        val rec = EventRecorder(q, TestScope(StandardTestDispatcher(testScheduler)), { online }, { uploads++ },
            now = { Instant.parse("2026-11-18T03:00:00Z") })
        rec.record(EventType.SKIP, "t1", positionS = 12.5, durationS = 200.0, source = "mix", mixId = "m1")
        online = true
        rec.record(EventType.THUMB_UP, "t2")
        advanceUntilIdle()
        val (a, b) = q.rows.values.toList()
        assertTrue(a.offline)
        assertEquals("skip", a.type)
        assertEquals(12.5, a.positionS!!, 0.0)
        assertEquals("m1", a.mixId)
        assertEquals("2026-11-18T03:00:00Z", a.at)
        assertFalse(b.offline)
        assertTrue(a.id != b.id)
        assertEquals(1, uploads)
    }
}
