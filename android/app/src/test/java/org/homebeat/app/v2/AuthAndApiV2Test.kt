package org.homebeat.app.v2

import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.homebeat.app.auth.AuthRepository
import org.homebeat.app.data.api.ApiFactory
import org.homebeat.app.data.api.LoginRequest
import org.homebeat.app.data.api.Lyrics
import org.homebeat.app.data.api.LyricLine
import org.homebeat.app.data.api.MoveBody
import org.homebeat.app.data.api.NewQueue
import org.homebeat.app.data.api.ServerInfo
import org.homebeat.app.data.api.TrackIdsBody
import org.homebeat.app.lyrics.LrcFormat
import org.homebeat.app.net.CertProbe
import org.homebeat.app.net.PinnedCertTrustManager
import org.homebeat.app.net.ProbeOutcome
import org.homebeat.app.net.Route
import org.homebeat.app.net.ServerAddress
import org.homebeat.app.net.TrustDecision
import org.homebeat.app.ui.TrackFormat
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.HttpUrl.Companion.toHttpUrl

class AuthAndApiV2Test {
    private val server = MockWebServer()
    private val cert = HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()

    @Before fun setUp() {
        server.useHttps(HandshakeCertificates.Builder().heldCertificate(cert).build().sslSocketFactory(), false)
        server.start()
    }

    @After fun tearDown() = server.shutdown()

    private fun fp() = PinnedCertTrustManager.formatFingerprint(PinnedCertTrustManager.sha256(cert.certificate.encoded))

    @Test fun `address normalisation`() {
        assertEquals("https://192.168.68.2:8443/", ServerAddress.normalize("192.168.68.2").getOrThrow().toString())
        assertEquals("https://192.168.68.2:9000/", ServerAddress.normalize("192.168.68.2:9000").getOrThrow().toString())
        assertEquals("https://music.example.com/", ServerAddress.normalize("music.example.com/api/v1/").getOrThrow().toString())
        assertEquals("https://music.example.com/", ServerAddress.normalize("https://music.example.com").getOrThrow().toString())
        assertTrue(ServerAddress.normalize("http://192.168.1.2").isFailure)
        assertTrue(ServerAddress.normalize("  ").isFailure)
        assertTrue(ServerAddress.isLanHost("10.0.0.5"))
        assertTrue(ServerAddress.isLanHost("nas.local"))
        assertFalse(ServerAddress.isLanHost("8.8.8.8"))
        assertFalse(ServerAddress.isLanHost("music.example.com"))
    }

    @Test fun `probe reads server-info and fingerprints the self-signed cert`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"serverId":"s1","name":"Home","version":"2.0","lanUrl":"https://192.168.68.2:8443","tunnelUrl":"https://music.example.com","certSha256":"${fp()}","registration":"invite"}"""))
        val out = CertProbe(OkHttpClient()).probe(server.url("/"))
        assertEquals("/api/v1/server-info", server.takeRequest().path)
        assertTrue(CertProbe.same(fp(), out.fingerprint))
        assertFalse(out.caTrusted)
        assertEquals("s1", out.info.serverId)
        assertEquals(TrustDecision.ASK_USER, CertProbe.decide(out, null))
        assertEquals(TrustDecision.KNOWN_PIN, CertProbe.decide(out, fp().lowercase().replace(":", "")))
        assertEquals(TrustDecision.PIN_CHANGED, CertProbe.decide(out, "00".repeat(32)))
        assertEquals(TrustDecision.CA_TRUSTED, CertProbe.decide(out.copy(caTrusted = true), null))
    }

    @Test fun `draft config puts the entered address on the right route`() {
        val info = ServerInfo("s1", "Home", "2", "https://192.168.68.2:8443", "https://music.example.com", "AB".repeat(32).chunked(2).joinToString(":"))
        val auth = AuthRepository.Companion
        val (lanCfg, lanRoute) = auth.draftConfig(ProbeOutcome("https://192.168.68.2:8443".toHttpUrl(), "CD:".repeat(31) + "CD", false, info))
        assertEquals(Route.LAN, lanRoute)
        assertEquals("https://192.168.68.2:8443", lanCfg.lanUrl)
        assertTrue(lanCfg.certSha256.startsWith("CD")) // the cert the user trusted
        assertEquals("https://music.example.com", lanCfg.tunnelUrl)
        val (tunCfg, tunRoute) = auth.draftConfig(ProbeOutcome("https://music.example.com".toHttpUrl(), "EF:".repeat(31) + "EF", true, info))
        assertEquals(Route.TUNNEL, tunRoute)
        assertEquals("https://music.example.com", tunCfg.tunnelUrl)
        assertTrue(tunCfg.certSha256.startsWith("AB")) // LAN pin learned over the tunnel
    }

    @Test fun `account validation and error messages`() {
        assertNull(AuthRepository.validateNewAccount("elijah_1", "longenough"))
        assertTrue(AuthRepository.validateNewAccount("ab", "longenough")!!.startsWith("Username"))
        assertTrue(AuthRepository.validateNewAccount("bad name", "longenough")!!.startsWith("Username"))
        assertTrue(AuthRepository.validateNewAccount("okname", "short")!!.startsWith("Password"))
        assertTrue(AuthRepository.messageFor(409).contains("taken"))
        assertTrue(AuthRepository.messageFor(403).contains("invite"))
        assertTrue(AuthRepository.messageFor(429).contains("Too many"))
    }

    @Test fun `v2 endpoints over MockWebServer`() = runBlocking {
        val plain = MockWebServer().apply { start() }
        try {
            val api = ApiFactory.create(OkHttpClient(), plain.url("/api/v1/").toString())
            plain.enqueue(MockResponse().setBody("""{"userId":"u1","username":"eli","isAdmin":true,"deviceId":"d1","deviceToken":"tok"}"""))
            assertEquals("tok", api.login(LoginRequest("eli", "pw", "Pixel")).deviceToken)
            val login = plain.takeRequest()
            assertEquals("/api/v1/auth/login", login.path)
            assertTrue(login.body.readUtf8().contains("\"deviceKind\":\"android\""))

            plain.enqueue(MockResponse().setBody("""{"id":"q1","name":"Search: conver","slot":20,"currentIndex":1,"positionMs":0,"tracks":[]}"""))
            assertEquals(20, api.createQueue(NewQueue("Search: conver", listOf("a", "b"), 1)).slot)
            assertEquals("""{"name":"Search: conver","trackIds":["a","b"],"startIndex":1}""", plain.takeRequest().body.readUtf8())

            plain.enqueue(MockResponse().setResponseCode(204))
            api.addToQueue("q1", TrackIdsBody(listOf("c"), JsonPrimitive("next")))
            assertEquals("""{"trackIds":["c"],"position":"next"}""", plain.takeRequest().body.readUtf8())

            plain.enqueue(MockResponse().setResponseCode(204))
            api.moveQueueTrack("q1", MoveBody(0, 2))
            val mv = plain.takeRequest()
            assertEquals("/api/v1/queues/q1/move", mv.path)

            plain.enqueue(MockResponse().setBody("""{"id":"t1","title":"Conversations with Strangers","fileName":"Conversations with Strangers.mp3","folder":"B-dic","codec":"flac","bitsPerSample":16,"sampleRate":44100,"bitrateKbps":912,"durationS":230}"""))
            val info = api.trackInfo("t1")
            assertEquals("FLAC · 16-bit · 44.1 kHz · 912 kbps", TrackFormat.line(info))
            assertEquals("[unknown]", TrackFormat.orUnknown(info.composer))

            plain.enqueue(MockResponse().setBody("""{"path":"","parent":null,"folders":[{"name":"B-dic","path":"B-dic","trackCount":3}],"tracks":[]}"""))
            assertEquals("B-dic", api.folders().folders.single().path)
            assertEquals("/api/v1/library/folders?path=", plain.run { takeRequest(); takeRequest() }.path)
        } finally {
            plain.shutdown()
        }
    }

    @Test fun `lyrics editor text`() {
        val l = Lyrics("t", true, "user", listOf(LyricLine(0, "hi"), LyricLine(83_450, "there")), "hi\nthere", canEdit = true)
        assertEquals("[00:00.00]hi\n[01:23.45]there", LrcFormat.toEditable(l))
        assertEquals("plain", LrcFormat.toEditable(Lyrics("t", false, "embedded", emptyList(), "plain")))
        assertEquals("Your edit", LrcFormat.sourceLabel("user"))
        assertEquals(".lrc file", LrcFormat.sourceLabel("lrc_file"))
        assertEquals("Library › B-dic › Live", TrackFormat.location("B-dic/Live"))
    }

}
