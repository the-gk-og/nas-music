package org.homebeat.app.lyrics

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.homebeat.app.data.api.ApiFactory
import org.homebeat.app.data.db.AppDatabase
import org.homebeat.app.data.db.DownloadEntity
import org.homebeat.app.data.db.LyricsEntity
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** The real Room DAO (in-memory, via Robolectric). */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35], application = android.app.Application::class)
class LyricsDaoTest {
    private lateinit var db: AppDatabase
    private val server = MockWebServer()

    @Before fun setUp() {
        db = Room.inMemoryDatabaseBuilder(ApplicationProvider.getApplicationContext<Context>(), AppDatabase::class.java)
            .allowMainThreadQueries().build()
        server.start()
    }

    @After fun tearDown() { db.close(); server.shutdown() }

    @Test fun `repository stores found lyrics and the 404 marker in Room`() = runBlocking {
        val api = ApiFactory.create(OkHttpClient(), server.url("/api/v1/").toString())
        server.enqueue(MockResponse().setBody("""{"trackId":"a","synced":false,"source":"embedded","lines":[],"text":"la la"}"""))
        server.enqueue(MockResponse().setResponseCode(404))
        val repo = LyricsRepository(db.lyrics(), { api.lyrics(it) }, ApiFactory.json)
        repo.prefetch("a")
        repo.prefetch("b")

        assertEquals("found", db.lyrics().get("a")!!.status)
        assertEquals("none", db.lyrics().get("b")!!.status)
        // A fresh repository (new process) reads both answers from Room without the network.
        val offline = LyricsRepository(db.lyrics(), { error("network must not be used") }, ApiFactory.json)
        assertEquals("la la", (offline.get("a") as LyricsResult.Found).lyrics.text)
        assertEquals(LyricsResult.None, offline.get("b"))
    }

    @Test fun `deleting a track drops its lyrics`() = runBlocking {
        db.lyrics().put(LyricsEntity("a", "none"))
        db.lyrics().delete("a")
        assertNull(db.lyrics().get("a"))
    }

    @Test fun `prune keeps lyrics of downloaded tracks`() = runBlocking {
        db.downloads().upsert(DownloadEntity("kept", "opus", "/x", 1, "mix", complete = true))
        db.lyrics().put(LyricsEntity("kept", "none", fetchedAtMs = 0))
        db.lyrics().put(LyricsEntity("streamed", "none", fetchedAtMs = 0))
        db.lyrics().put(LyricsEntity("recent", "none", fetchedAtMs = 10_000))
        db.lyrics().pruneStreamed(cutoffMs = 5_000)
        assertNotNull(db.lyrics().get("kept"))
        assertNull(db.lyrics().get("streamed"))
        assertNotNull(db.lyrics().get("recent"))
    }
}
