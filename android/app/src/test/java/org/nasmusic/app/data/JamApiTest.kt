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
import org.nasmusic.app.data.api.JamApi
import org.nasmusic.app.data.api.JamSettingsBody

/** Jam host models against JSON shaped like api/openapi.yaml's JamSnapshot (server output, trimmed). */
class JamApiTest {
    private val server = MockWebServer()
    private lateinit var api: JamApi

    @Before fun setUp() {
        server.start()
        api = ApiFactory.jam(OkHttpClient(), server.url("/api/v1/").toString())
    }

    @After fun tearDown() = server.shutdown()

    private val snapshot = """
        {"jam":{"id":"j1","hostName":"alice","settings":{"approval":true,"allowPlayNext":false,"maxPerGuest":null},
          "createdAt":"x","expiresAt":"y","ended":false,"joinCode":"abc","joinUrl":"https://m/jam/abc",
          "lanJoinUrl":"https://l/jam/abc","joinExpiresAt":"z","queueId":"q"},
         "serverTime":"t",
         "nowPlaying":{"track":{"id":"t1","title":"Song","artist":"A","album":"B","albumId":"al","durationS":2.0},
           "position":0,"addedBy":{"guestId":"g1","name":"Sam","isHost":false},"positionMs":1200,
           "positionAt":"t","playing":true,"speed":1.0,"votes":{"up":1,"down":0,"mine":0}},
         "upcoming":[{"position":1,"track":{"id":"t2","title":"Next","artist":"A","album":"B","albumId":"al","durationS":3},
           "addedBy":{"guestId":null,"name":"alice","isHost":true},"votes":{"up":0,"down":2,"mine":0}}],
         "pending":[{"id":"r1","trackId":"t3","track":null,"playNext":true,"status":"pending","createdAt":"c",
           "by":{"guestId":"g2","name":"Kim"}}],
         "guests":[{"id":"g1","name":"Sam","isMember":false},{"id":"g2","name":"Kim","isMember":true}],
         "me":{"guestId":null,"name":"alice","isHost":true}}
    """.trimIndent()

    @Test fun parsesSnapshotAndCurrent() = runBlocking {
        server.enqueue(MockResponse().setBody(snapshot))
        server.enqueue(MockResponse().setResponseCode(204))
        val s = api.current().body()!!
        assertEquals("abc", s.jam.joinCode)
        assertNull(s.jam.settings.maxPerGuest)
        assertTrue(s.jam.settings.approval)
        assertEquals("Sam", s.nowPlaying!!.addedBy.name)
        assertEquals(2, s.upcoming[0].votes.down)
        assertTrue(s.upcoming[0].addedBy.isHost)
        assertEquals("Kim", s.pending[0].by!!.name)
        assertTrue(s.guests[1].isMember)
        assertNull(api.current().body())
    }

    @Test fun settingsSendsExplicitNullCap() = runBlocking {
        server.enqueue(MockResponse().setBody(snapshot))
        api.settings("j1", JamSettingsBody(approval = false, allowPlayNext = true, maxPerGuest = null).json())
        val req = server.takeRequest()
        assertEquals("PATCH", req.method)
        assertEquals("/api/v1/jams/j1", req.path)
        // ApiFactory.json drops null properties; "no cap" must still arrive as an explicit null.
        val body = req.body.readUtf8()
        assertTrue(body, body.contains("\"maxPerGuest\":null"))
        assertTrue(body, body.contains("\"allowPlayNext\":true"))
    }
}
