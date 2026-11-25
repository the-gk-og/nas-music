package org.homebeat.app.lyrics

import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.homebeat.app.data.api.ApiFactory
import org.homebeat.app.data.api.HomebeatApi
import org.homebeat.app.data.db.LyricsEntity

class FakeLyricsStore : LyricsCacheStore {
    val rows = HashMap<String, LyricsEntity>()
    override suspend fun get(trackId: String) = rows[trackId]
    override suspend fun put(entity: LyricsEntity) { rows[entity.trackId] = entity }
    override suspend fun delete(trackId: String) { rows.remove(trackId) }
}

class LyricsRepositoryTest {
    private val server = MockWebServer()
    private lateinit var api: HomebeatApi
    private val store = FakeLyricsStore()
    private var clock = 1_000_000L
    private lateinit var repo: LyricsRepository

    private val synced = """{"trackId":"t1","synced":true,"source":"lrc_file",
        "lines":[{"timeMs":0,"text":"hello"},{"timeMs":4200,"text":""},{"timeMs":8000,"text":"world"}],
        "text":"hello\n\nworld"}"""

    @Before fun setUp() {
        server.start()
        api = ApiFactory.create(OkHttpClient(), server.url("/api/v1/").toString())
        repo = LyricsRepository(store, { api.lyrics(it) }, ApiFactory.json, now = { clock }, noneTtlMs = 1000)
    }

    @After fun tearDown() = server.shutdown()

    @Test fun `fetches, parses and caches synced lyrics`() = runBlocking {
        server.enqueue(MockResponse().setBody(synced))
        val r = repo.get("t1") as LyricsResult.Found
        assertEquals("/api/v1/tracks/t1/lyrics", server.takeRequest().path)
        assertTrue(r.lyrics.synced)
        assertEquals(4200L, r.lyrics.lines[1].timeMs)
        assertEquals("found", store.rows["t1"]!!.status)
        // Second call is served from memory/Room, not the server.
        assertTrue(repo.get("t1") is LyricsResult.Found)
        assertEquals(1, server.requestCount)
    }

    @Test fun `unsynced lyrics keep null timestamps`() = runBlocking {
        server.enqueue(MockResponse().setBody(
            """{"trackId":"t2","synced":false,"source":"embedded","lines":[{"timeMs":null,"text":"a"},{"text":"b"}],"text":"a\nb"}""",
        ))
        val l = (repo.get("t2") as LyricsResult.Found).lyrics
        assertFalse(l.synced)
        assertNull(l.lines[0].timeMs)
        assertEquals("a\nb", l.text)
    }

    @Test fun `404 is stored as a none marker and not asked again`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(404))
        assertEquals(LyricsResult.None, repo.get("t3"))
        assertEquals("none", store.rows["t3"]!!.status)
        assertNull(store.rows["t3"]!!.json)
        assertFalse(repo.prefetch("t3"))
        assertEquals(1, server.requestCount)
    }

    @Test fun `none marker expires so newly added lrc files are found`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(404))
        server.enqueue(MockResponse().setBody(synced))
        assertTrue(repo.prefetch("t1"))
        clock += 2000 // past the TTL
        assertTrue(repo.prefetch("t1"))
        assertEquals("found", store.rows["t1"]!!.status)
    }

    @Test fun `server errors are not cached`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500))
        server.enqueue(MockResponse().setBody(synced))
        assertEquals(LyricsResult.Unavailable, repo.get("t1"))
        assertTrue(store.rows.isEmpty())
        assertTrue(repo.get("t1") is LyricsResult.Found)
    }

    @Test fun `drop removes cached lyrics`() = runBlocking {
        server.enqueue(MockResponse().setBody(synced))
        server.enqueue(MockResponse().setBody(synced))
        repo.get("t1")
        repo.drop("t1")
        assertTrue(store.rows.isEmpty())
        repo.get("t1")
        assertEquals(2, server.requestCount) // memory copy was dropped too
    }
}
