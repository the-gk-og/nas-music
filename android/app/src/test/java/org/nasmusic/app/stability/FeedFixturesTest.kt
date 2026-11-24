package org.nasmusic.app.stability

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
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
import org.nasmusic.app.data.FeedRepository
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.LenientJson
import org.nasmusic.app.data.api.NasMusicApi

/** /foryou and /home fixtures shaped like api/openapi.yaml, with the nulls and junk real servers send. */
class FeedFixturesTest {
    private fun fixture(name: String) = javaClass.classLoader!!.getResource("fixtures/$name")!!.readText()
    private val server = MockWebServer()
    private lateinit var api: NasMusicApi

    @Before fun setUp() { server.start(); api = ApiFactory.create(OkHttpClient(), server.url("/api/v1/").toString()) }
    @After fun tearDown() = server.shutdown()

    @Test fun `for you fixture parses, skipping unknown kinds, types and broken elements`() = runBlocking {
        server.enqueue(MockResponse().setBody(fixture("foryou.json")))
        val feed = FeedRepository(api).forYou()
        val kinds = feed.shelves.map { it.kind }
        assertEquals(listOf("mixes", "radio", "because_you_played", "suggestions", "to_get", "release_radar", "wrapped", "radio"), kinds)
        assertFalse("podcasts_for_you" in kinds)
        val byId = feed.shelves.associateBy { it.id }
        assertEquals(3, byId.getValue("mixes").items.size) // duplicate id kept; the UI keys by index
        assertNull(byId.getValue("radio").items[2].seed) // null seed: the tap falls back to a random radio
        val partial = byId.getValue("byp:album:al1").items[1]
        assertEquals("t3", partial.track?.id) // partial track parses thanks to Track defaults
        assertNull(byId.getValue("suggestions").items[1].suggestion)
        assertEquals("Bandcamp", byId.getValue("to_get").items[0].toget?.links?.single()?.name)
        assertTrue(byId.getValue("wrapped").items.isEmpty()) // items: null
        // Unknown type, no id, blank id, non-objects dropped; title null tolerated.
        assertEquals(listOf("al5"), byId.getValue("weird-items").items.map { it.id })
        assertEquals("", byId.getValue("weird-items").items.single().title)
    }

    @Test fun `home fixture parses every item type`() = runBlocking {
        server.enqueue(MockResponse().setBody(fixture("home.json")))
        val feed = FeedRepository(api).home()
        val items = feed.shelves.flatMap { it.items }
        assertEquals(
            setOf("radio", "device", "album", "playlist", "mix", "smart_list", "genre", "folder", "queue", "artist"),
            items.map { it.type }.toSet(),
        )
        val q2 = feed.shelves.first { it.kind == "continue_listening" }.items[1]
        assertEquals(0f, q2.progress?.fraction ?: -1f, 0f) // count 0 must not divide by zero
        assertEquals("t2", items.first { it.id == "d2" }.device?.nowPlaying?.id)
    }

    @Test fun `garbage never throws`() {
        listOf("""{}""", """[]""", """{"shelves":null}""", """{"shelves":{}}""", """null""", """{"shelves":[1,2,3]}""").forEach {
            LenientJson.feed(Json.parseToJsonElement(it))
        }
        assertTrue(LenientJson.search(Json.parseToJsonElement("""{"tracks":[{"id":"t"},{"nope":1}],"top":{"type":"x"},"catalog":null}""")).let {
            it.tracks.size == 1 && it.top == null && it.catalog.isEmpty()
        })
    }

    @Test fun `search with a bad top result and partial lists still returns the good parts`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"query":"x","top":null,"tracks":[{"id":"t1","title":"A"}],"albums":[{"broken":true}],"catalog":[{"id":"dz:1","title":"C","owned":false}],"catalogError":"deezer timeout"}"""))
        val r = org.nasmusic.app.data.api.LenientJson.search(api.globalSearchRaw("x"))
        assertEquals(1, r.tracks.size)
        assertTrue(r.albums.isEmpty())
        assertEquals("deezer timeout", r.catalogError)
        assertTrue(r.lyrics.isEmpty()) // a server older than api 0.9 sends no lyrics group
    }

    @Test fun `lyrics matches parse, skipping broken ones`() {
        val r = LenientJson.search(Json.parseToJsonElement(
            """{"lyrics":[{"track":{"id":"t1","title":"A"},"line":"we were dancing"},{"line":"no track"},{"track":{"id":"t2","title":"B"}}]}"""))
        assertEquals(listOf("t1", "t2"), r.lyrics.map { it.track.id })
        assertEquals("we were dancing", r.lyrics[0].line)
        assertEquals("", r.lyrics[1].line)
    }
}
