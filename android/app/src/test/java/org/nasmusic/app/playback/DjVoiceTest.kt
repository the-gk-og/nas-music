package org.nasmusic.app.playback

import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.DjIntroRequest

class DjVoiceTest {
    @Test fun `talks only in radio and mixes`() {
        assertTrue(DjLogic.eligible(isRadio = true, source = null))
        assertTrue(DjLogic.eligible(isRadio = false, source = "mix"))
        assertTrue(DjLogic.eligible(isRadio = false, source = "more_like"))
        assertFalse(DjLogic.eligible(isRadio = false, source = "library"))
        assertFalse(DjLogic.eligible(isRadio = false, source = null))
    }

    @Test fun `ducking ramps and stays in range`() {
        assertEquals(1f, DjLogic.ramp(1f, DjLogic.DUCK, 0f), 0f)
        assertEquals(DjLogic.DUCK, DjLogic.ramp(1f, DjLogic.DUCK, 1f), 0f)
        assertEquals(DjLogic.DUCK, DjLogic.ramp(1f, DjLogic.DUCK, 2f), 0f)
        assertTrue(DjLogic.DUCK > 0f && DjLogic.DUCK < 0.5f)
        val m = VolumeMixer {}
        m.user = 0.8f
        m.duck = DjLogic.DUCK
        assertEquals(0.8f * DjLogic.DUCK, m.total, 1e-6f)
    }

    @Test fun `spot keys differ by neighbour`() {
        val a = DjLogic.key(DjVoice.Spot("q", 3, "t3", "t2"))
        assertEquals("q|3|t3|t2", a)
        assertTrue(a != DjLogic.key(DjVoice.Spot("q", 3, "t3", null)))
    }

    @Test fun `status, intro and quiet 204 against the contract`() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            val api = ApiFactory.create(OkHttpClient(), server.url("/api/v1/").toString())
            server.enqueue(MockResponse().setBody("""{"state":"downloading","voice":"en_GB-cori-medium","detail":null,"enabled":true,"every":3}"""))
            server.enqueue(MockResponse().setBody("""{"clipId":"0123456789abcdef0123456789abcdef","text":"Here's Song by Band.","url":"stream/dj/0123456789abcdef0123456789abcdef","durationMs":2300,"voice":"en_GB-cori-medium"}"""))
            server.enqueue(MockResponse().setResponseCode(204))
            val st = api.djStatus()
            assertTrue(st.available && st.enabled)
            assertEquals("/api/v1/dj/status", server.takeRequest().path)
            val intro = api.djIntro(DjIntroRequest("t1", 3, "t0", "q1")).body()!!
            assertEquals(2300L, intro.durationMs)
            val req = server.takeRequest()
            assertEquals("/api/v1/dj/intro", req.path)
            assertTrue(req.body.readUtf8().contains("\"position\":3"))
            assertNull(api.djIntro(DjIntroRequest("t2", 4)).body())
        } finally {
            server.shutdown()
        }
    }
}
