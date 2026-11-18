package org.nasmusic.app.v3

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.nasmusic.app.data.FeedRepository
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.NasMusicApi
import org.nasmusic.app.data.api.NewToGet
import org.nasmusic.app.data.api.Queue
import org.nasmusic.app.data.api.RadioRequest
import org.nasmusic.app.data.api.RadioSeed
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.playback.RadioLogic
import org.nasmusic.app.ui.MotionLevel
import org.nasmusic.app.ui.screens.CoverGesture

class V3Test {
    private val server = MockWebServer()
    private lateinit var api: NasMusicApi

    @Before fun setUp() { server.start(); api = ApiFactory.create(OkHttpClient(), server.url("/api/v1/").toString()) }
    @After fun tearDown() = server.shutdown()

    private fun t(id: String, album: String = "al-$id") = """{"id":"$id","title":"T$id","artistId":"ar","artist":"A","albumId":"$album","album":"Album $album","durationS":200,"starred":false}"""
    private fun track(id: String) = Track(id, "T$id", "ar", "A", "al", "Al", durationS = 1.0, starred = false)

    @Test fun `radio asks for more when five tracks remain`() {
        assertFalse(RadioLogic.needsMore(25, 0))
        assertFalse(RadioLogic.needsMore(25, 18)) // 6 left
        assertTrue(RadioLogic.needsMore(25, 19)) // 5 left
        assertTrue(RadioLogic.needsMore(25, 24))
        assertFalse(RadioLogic.needsMore(0, 0))
    }

    @Test fun `radio more accepts added, new tracks, or the whole queue`() {
        val json = ApiFactory.json
        val local = listOf("1", "2")
        fun parse(s: String) = Json.parseToJsonElement(s).jsonObject
        assertEquals(listOf("3"), RadioLogic.newTracks(parse("""{"added":[${t("3")}]}"""), local, json).map { it.id })
        assertEquals(listOf("3", "4"), RadioLogic.newTracks(parse("""{"tracks":[${t("3")},${t("4")}]}"""), local, json).map { it.id })
        assertEquals(listOf("3"), RadioLogic.newTracks(parse("""{"id":"q1","tracks":[${t("1")},${t("2")},${t("3")}]}"""), local, json).map { it.id })
        assertTrue(RadioLogic.newTracks(parse("""{}"""), local, json).isEmpty())
    }

    @Test fun `start radio and more over the wire`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"id":"q9","name":"Random radio","slot":3,"radio":true,"tracks":[${t("1")}]}"""))
        val q = api.startRadio(RadioRequest(RadioSeed("artist", "ar1")))
        assertTrue(q.isRadio)
        val req = server.takeRequest()
        assertEquals("/api/v1/radio", req.path)
        assertEquals("""{"seed":{"type":"artist","id":"ar1"}}""", req.body.readUtf8())
        server.enqueue(MockResponse().setBody("""{"added":[]}"""))
        api.radioMore("q9")
        assertEquals("/api/v1/radio/q9/more", server.takeRequest().path)
        assertTrue(Queue("x", "y", kind = "radio").isRadio)
    }

    @Test fun `home falls back to v2 endpoints when the server has no home feed`() = runBlocking {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when (request.path!!.substringBefore('?')) {
                "/api/v1/home" -> MockResponse().setResponseCode(404)
                "/api/v1/lists/recently_played" -> MockResponse().setBody("[${t("1", "a")},${t("2", "a")},${t("3", "b")}]")
                "/api/v1/queues" -> MockResponse().setBody("""[{"id":"q1","name":"Album: X","slot":1,"currentIndex":2,"positionMs":0,"trackCount":10},{"id":"q2","name":"Done","slot":2,"currentIndex":0,"positionMs":0,"trackCount":5}]""")
                "/api/v1/pins" -> MockResponse().setBody("""[{"type":"playlist","id":"p1","title":"car"}]""")
                else -> MockResponse().setResponseCode(404)
            }
        }
        val feed = FeedRepository(api).home()
        val byId = feed.shelves.associateBy { it.id }
        assertEquals(listOf("a", "b"), byId["recent"]!!.items.map { it.id }) // distinct albums
        assertEquals(listOf("q1"), byId["continue"]!!.items.map { it.id }) // q2 not started
        assertEquals(0.3f, byId["continue"]!!.items.single().progress!!, 0.001f)
        assertEquals("car", byId["pinned"]!!.items.single().title)
    }

    @Test fun `home uses the server feed when present`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"shelves":[{"id":"recent","title":"Recently played","layout":"grid2","items":[{"type":"mix","id":"m1","title":"Daily Mix 1","futureField":1}]}]}"""))
        val feed = FeedRepository(api).home()
        assertEquals("m1", feed.shelves.single().items.single().id)
    }

    @Test fun `global search with catalogue and to get`() = runBlocking {
        server.enqueue(MockResponse().setBody(
            """{"top":{"type":"catalog","catalog":{"id":"dz:1","kind":"album","title":"New LP","artist":"Band","hasPreview":true,"owned":false}},
               "tracks":[${t("1")}],"catalog":[{"id":"dz:1","kind":"album","title":"New LP","artist":"Band","hasPreview":true,"hasCover":true,"owned":false},
               {"id":"dz:2","kind":"album","title":"Old LP","artist":"Band","owned":true,"ownedId":"al9"}]}""",
        ))
        val r = api.globalSearch("band")
        assertEquals("/api/v1/search?q=band&limit=20", server.takeRequest().path)
        assertEquals("dz:1", r.top!!.catalog!!.id)
        assertEquals(1, r.catalog.count { !it.owned })
        server.enqueue(MockResponse().setBody("""{"id":"tg1","kind":"album","title":"New LP","artist":"Band","status":"want"}"""))
        api.addToGet(NewToGet("dz:1", "album", "New LP", "Band"))
        assertEquals("""{"catalogId":"dz:1","kind":"album","title":"New LP","artist":"Band"}""", server.takeRequest().body.readUtf8())
    }

    @Test fun `fallback shelf builders`() {
        assertEquals(1, FeedRepository.recentAlbums(listOf(track("1"), track("2"))).size)
        val items = FeedRepository.toGetItems(listOf(
            org.nasmusic.app.data.api.ToGetItem("1", title = "A", status = "want", catalogId = "dz:1", hasCover = true),
            org.nasmusic.app.data.api.ToGetItem("2", title = "B", status = "got"),
        ))
        assertEquals(listOf("1"), items.map { it.id })
        assertEquals("catalog/dz:1/cover?size=256", items.single().artPath)
    }

    @Test fun `cover gestures`() {
        assertEquals(CoverGesture.NEXT, CoverGesture.classify(-200f, 10f, 800f))
        assertEquals(CoverGesture.PREVIOUS, CoverGesture.classify(200f, -20f, 800f))
        assertEquals(CoverGesture.MINIMISE, CoverGesture.classify(15f, 250f, 800f))
        assertEquals(CoverGesture.NONE, CoverGesture.classify(40f, 30f, 800f))
        assertEquals(CoverGesture.NONE, CoverGesture.classify(0f, -300f, 800f)) // swipe up does nothing on the cover
    }

    @Test fun `motion level respects the OS setting`() {
        assertEquals(MotionLevel.OFF, MotionLevel.from("expressive", 0f))
        assertEquals(MotionLevel.EXPRESSIVE, MotionLevel.from("expressive", 1f))
        assertEquals(MotionLevel.QUICK, MotionLevel.from("unknown", 1f))
        assertEquals(0, MotionLevel.OFF.baseMs)
    }
}
