package org.nasmusic.app.data

import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.AudioFormat
import org.nasmusic.app.data.api.NasMusicApi
import org.nasmusic.app.data.api.PairRequest
import org.nasmusic.app.data.api.SuggestionStatus

/** Checks the hand-written models against JSON shaped like api/openapi.yaml. */
class ApiContractTest {
    private val server = MockWebServer()
    private lateinit var api: NasMusicApi

    @Before fun setUp() {
        server.start()
        api = ApiFactory.create(OkHttpClient(), server.url("/api/v1/").toString())
    }

    @After fun tearDown() = server.shutdown()

    private val track = """{"id":"t1","title":"Song","artistId":"a1","artist":"Band","albumId":"al1","album":"LP",
        "trackNo":3,"discNo":null,"year":null,"genre":null,"durationS":201.5,"flacBytes":30000000,"starred":true,"rating":null}"""

    @Test fun `mixes parse with nullable fields`() = runBlocking {
        server.enqueue(MockResponse().setBody(
            """[{"id":"m1","kind":"daily","title":"Daily Mix","createdAt":"2026-11-18T02:00:00Z","durationS":2400,
               "tracks":[{"track":$track,"reason":"You loved this last week"}],"extraFieldFromNewerServer":1}]""",
        ))
        val m = api.mixesToday().single()
        assertEquals("daily", m.kind)
        assertEquals(201.5, m.tracks.single().track.durationS, 0.0)
        assertNull(m.tracks.single().track.discNo)
        assertEquals("/api/v1/mixes/today", server.takeRequest().path)
    }

    @Test fun `manifest request and response`() = runBlocking {
        server.enqueue(MockResponse().setBody(
            """{"generatedAt":"2026-11-18T02:00:00Z","items":[{"trackId":"t1","format":"flac","why":"favourite"},
               {"trackId":"t2","format":"opus","why":"mix","mixIds":["m1"]}]}""",
        ))
        val man = api.manifest("flac")
        assertEquals(AudioFormat.FLAC, man.items[0].format)
        assertEquals(listOf("m1"), man.items[1].mixIds)
        assertEquals("/api/v1/sync/manifest?favouritesFormat=flac", server.takeRequest().path)
    }

    @Test fun `pair, star, suggestion status`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"deviceId":"d1","deviceToken":"secret"}"""))
        server.enqueue(MockResponse().setResponseCode(204))
        server.enqueue(MockResponse().setResponseCode(204))
        assertEquals("secret", api.pair(PairRequest("ABCD1234", "Pixel")).deviceToken)
        assertTrue(api.star("t1").isSuccessful)
        assertTrue(api.setSuggestionStatus("s1", SuggestionStatus("not_interested")).isSuccessful)
        assertEquals("""{"pairCode":"ABCD1234","deviceName":"Pixel"}""", server.takeRequest().body.readUtf8())
        assertEquals("PUT", server.takeRequest().method)
        val s = server.takeRequest()
        assertEquals("/api/v1/suggestions/s1", s.path)
        assertEquals("""{"status":"not_interested"}""", s.body.readUtf8())
    }

    @Test fun `why and suggestions`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"reason":"Similar to X","similarTo":[$track]}"""))
        server.enqueue(MockResponse().setBody(
            """[{"id":"s1","artist":"A","album":"B","year":null,"coverUrl":null,"previewUrl":"https://p/x.mp3",
               "buyLinks":[{"name":"Bandcamp","url":"https://b"}],"reason":"r","status":"new","createdAt":"2026-11-18T00:00:00Z"}]""",
        ))
        assertEquals("t1", api.why("t1").similarTo.single().id)
        assertEquals("Bandcamp", api.suggestions().single().buyLinks.single().name)
    }
}
