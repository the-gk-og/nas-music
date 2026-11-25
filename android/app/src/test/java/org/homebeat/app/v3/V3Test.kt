package org.homebeat.app.v3

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
import org.homebeat.app.data.FeedRepository
import org.homebeat.app.data.api.ApiFactory
import org.homebeat.app.data.api.globalSearch
import org.homebeat.app.data.api.HomebeatApi
import org.homebeat.app.data.api.NewToGet
import org.homebeat.app.data.api.Queue
import org.homebeat.app.data.api.RadioRequest
import org.homebeat.app.data.api.RadioSeed
import org.homebeat.app.data.api.Track
import org.homebeat.app.playback.RadioLogic
import org.homebeat.app.ui.MotionLevel
import org.homebeat.app.ui.screens.CoverGesture

class V3Test {
    private val server = MockWebServer()
    private lateinit var api: HomebeatApi

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

    @Test fun `radio more returns the whole queue - everything past what we hold is new`() {
        val json = ApiFactory.json
        val local = listOf("1", "2")
        fun parse(s: String) = Json.parseToJsonElement(s).jsonObject
        assertEquals(listOf("3"), RadioLogic.newTracks(parse("""{"id":"q1","tracks":[${t("1")},${t("2")},${t("3")}]}"""), local, json).map { it.id })
        // Locally reordered queue: still only the appended tail is new (no duplicates).
        assertEquals(listOf("3"), RadioLogic.newTracks(parse("""{"id":"q1","tracks":[${t("2")},${t("1")},${t("3")}]}"""), local, json).map { it.id })
        assertEquals(listOf("3"), RadioLogic.newTracks(parse("""{"added":[${t("3")}]}"""), local, json).map { it.id })
        assertTrue(RadioLogic.newTracks(parse("""{}"""), local, json).isEmpty())
    }

    @Test fun `start radio and more over the wire`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"id":"q9","name":"Random radio","slot":3,"radio":{"type":"random","title":"Random"},"source":{"type":"radio"},"tracks":[${t("1")}]}"""))
        val q = api.startRadio(RadioRequest(RadioSeed("artist", "ar1")))
        assertTrue(q.isRadio)
        val req = server.takeRequest()
        assertEquals("/api/v1/radio", req.path)
        assertEquals("""{"seed":{"type":"artist","id":"ar1"}}""", req.body.readUtf8())
        server.enqueue(MockResponse().setBody("""{"id":"q9","name":"Random radio","slot":3,"tracks":[]}"""))
        api.radioMore("q9")
        assertEquals("/api/v1/radio/q9/more", server.takeRequest().path)
        assertTrue(Queue("x", "y", kind = "radio").isRadio)
        assertFalse(Queue("x", "y").isRadio)
    }

    @Test fun `home falls back to v2 endpoints when the server has no home feed`() = runBlocking {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when (request.path!!.substringBefore('?')) {
                "/api/v1/home" -> MockResponse().setResponseCode(404)
                "/api/v1/lists/recently_played" -> MockResponse().setBody("[${t("1", "a")},${t("2", "a")},${t("3", "b")}]")
                "/api/v1/queues" -> MockResponse().setBody("""[{"id":"q1","name":"Album: X","slot":1,"currentIndex":2,"positionMs":0,"trackCount":10},{"id":"q2","name":"Done","slot":2,"currentIndex":0,"positionMs":0,"trackCount":5}]""")
                "/api/v1/pins" -> MockResponse().setBody("""[{"type":"playlist","id":"p1","title":"car","artUrl":null}]""")
                else -> MockResponse().setResponseCode(404)
            }
        }
        val feed = FeedRepository(api).home()
        val byKind = feed.shelves.associateBy { it.kind }
        assertEquals(listOf("a", "b"), byKind["recently_played"]!!.items.map { it.id }) // distinct albums
        assertEquals("art/a", byKind["recently_played"]!!.items.first().artUrl)
        assertEquals(listOf("q1"), byKind["continue_listening"]!!.items.map { it.id }) // q2 not started
        assertEquals(0.3f, byKind["continue_listening"]!!.items.single().progress!!.fraction, 0.001f)
        assertEquals("car", byKind["pinned"]!!.items.single().title)
    }

    @Test fun `home uses the server feed when present`() = runBlocking {
        server.enqueue(MockResponse().setBody(
            """{"shelves":[{"id":"recently_played","kind":"recently_played","title":"Recently played","layout":"grid2",
               "items":[{"type":"mix","id":"m1","title":"Daily Mix 1","artUrl":"art/al1","mix":{"kind":"daily","trackCount":30},"futureField":1},
                        {"type":"queue","id":"q1","title":"Album: X","progress":{"index":2,"count":10,"positionMs":5000,"queueId":"q1"}},
                        {"type":"device","id":"d2","title":"Desktop","device":{"kind":"desktop","isActive":true,"playing":true,"nowPlaying":null}}]}]}""",
        ))
        val shelf = FeedRepository(api).home().shelves.single()
        assertEquals("recently_played", shelf.kind)
        assertEquals("art/al1", shelf.items[0].artUrl)
        assertEquals("q1", shelf.items[1].progress!!.queueId)
        assertTrue(shelf.items[2].device!!.isActive)
    }

    @Test fun `global search with catalogue and to get`() = runBlocking {
        server.enqueue(MockResponse().setBody(
            """{"query":"band","top":{"type":"catalog","id":"dz:album:1","title":"New LP","artUrl":"catalog/dz:album:1/cover",
               "catalog":{"id":"dz:album:1","type":"album","title":"New LP","artist":"Band","hasPreview":true,"owned":false,"links":[]}},
               "tracks":[${t("1")}],"albums":[],"artists":[],"playlists":[],"queues":[],
               "catalog":[{"id":"dz:album:1","type":"album","title":"New LP","artist":"Band","hasPreview":true,"artUrl":"catalog/dz:album:1/cover","owned":false,"links":[{"name":"Bandcamp","url":"https://b"}]},
               {"id":"dz:album:2","type":"album","title":"Old LP","artist":"Band","owned":true,"ownedId":"al9","hasPreview":false,"links":[]}],"catalogError":null}""",
        ))
        val r = api.globalSearch("band")
        assertEquals("/api/v1/search?q=band&scope=all&limit=20", server.takeRequest().path)
        assertEquals("dz:album:1", r.top!!.catalog!!.id)
        assertEquals("Bandcamp", r.catalog.first().links.single().name)
        assertEquals(1, r.catalog.count { !it.owned })
        server.enqueue(MockResponse().setBody("""{"id":"tg1","type":"album","title":"New LP","artist":"Band","status":"want","createdAt":"2026-11-19T00:00:00Z","links":[]}"""))
        assertEquals("album", api.addToGet(NewToGet(catalogId = "dz:album:1")).type)
        assertEquals("""{"catalogId":"dz:album:1"}""", server.takeRequest().body.readUtf8())
        server.enqueue(MockResponse().setBody("[]"))
        api.toGet(status = "all")
        assertEquals("/api/v1/toget?status=all", server.takeRequest().path)
        server.enqueue(MockResponse().setBody("""[{"type":"album","id":"al1","title":"LP"}]"""))
        assertEquals(1, (api.pin(org.homebeat.app.data.api.NewPin("album", "al1")) as kotlinx.serialization.json.JsonArray).size)
        assertEquals("""{"type":"album","id":"al1"}""", server.takeRequest().body.readUtf8())
    }

    @Test fun `fallback shelf builders`() {
        assertEquals(1, FeedRepository.recentAlbums(listOf(track("1"), track("2"))).size)
        val items = FeedRepository.toGetItems(listOf(
            org.homebeat.app.data.api.ToGetItem("1", title = "A", status = "want", catalogId = "dz:1", artUrl = "catalog/dz:1/cover"),
            org.homebeat.app.data.api.ToGetItem("2", title = "B", status = "got"),
        ))
        assertEquals(listOf("1"), items.map { it.id })
        assertEquals("catalog/dz:1/cover", items.single().artUrl)
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

    @Test fun `appearance maps to the synced settings keys`() {
        val s = org.homebeat.app.data.prefs.AppSettings(
            animations = "expressive", defaultView = "list", pageStyle = "simple", accent = 0xFF60A5FA,
            homeShelves = listOf("pinned", "-devices", "start_radio"),
        )
        val remote = org.homebeat.app.data.prefs.AppearanceSync.toRemote(s)
        assertEquals("blue", remote["appearance.accent"]!!.toString().trim('"'))
        assertEquals("""["pinned","devices","start_radio"]""", remote["home.shelves"].toString())
        assertEquals("""["devices"]""", remote["home.hidden"].toString())
        val back = org.homebeat.app.data.prefs.AppearanceSync.fromRemote(remote, org.homebeat.app.data.prefs.AppSettings())
        assertEquals(s.homeShelves, back.homeShelves)
        assertEquals("expressive", back.animations)
        assertEquals(0xFF60A5FA, back.accent)
        assertEquals(0xFF123456, org.homebeat.app.data.prefs.AppearanceSync.accentFrom("#123456"))
        val bad = org.homebeat.app.data.prefs.AppearanceSync.fromRemote(mapOf("appearance.animations" to kotlinx.serialization.json.JsonPrimitive("wild")), s)
        assertEquals("expressive", bad.animations) // invalid values keep the local one
        assertEquals("recently_played", org.homebeat.app.data.prefs.AppSettings.migrateShelfId("recent"))
        assertEquals("-continue_listening", org.homebeat.app.data.prefs.AppSettings.migrateShelfId("-continue"))
    }
}
