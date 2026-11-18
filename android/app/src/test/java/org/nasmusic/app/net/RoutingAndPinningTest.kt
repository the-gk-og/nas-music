package org.nasmusic.app.net

import java.io.IOException
import kotlinx.coroutines.runBlocking
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.nasmusic.app.data.api.ApiFactory

class RoutingAndPinningTest {
    private val server = MockWebServer()
    private val cert = HeldCertificate.Builder().addSubjectAlternativeName("localhost").commonName("nas").build()
    private val otherCert = HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()
    private val base = OkHttpClient()

    @Before fun setUp() {
        server.useHttps(HandshakeCertificates.Builder().heldCertificate(cert).build().sslSocketFactory(), false)
        server.start()
    }

    @After fun tearDown() = server.shutdown()

    private fun fp(c: HeldCertificate) = PinnedCertTrustManager.formatFingerprint(PinnedCertTrustManager.sha256(c.certificate.encoded))

    private fun lanConfig(pin: String) = ServerConfig(
        serverId = "srv",
        lanUrl = "https://localhost:${server.port}",
        certSha256 = pin,
        deviceToken = "tok123",
    )

    @Test fun `LAN accepts the pinned self-signed cert and sends the bearer token`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"status":"ok","serverId":"srv","version":"0.1"}"""))
        val clients = HttpClients(base, lanConfig(fp(cert)))
        val api = ApiFactory.create(RoutedCallFactory({ clients }, { Route.LAN }, base))
        assertEquals("srv", api.health().serverId)
        val req = server.takeRequest()
        assertEquals("/api/v1/health", req.path)
        assertEquals("Bearer tok123", req.getHeader("Authorization"))
        assertNull(req.getHeader("CF-Access-Client-Id"))
    }

    @Test fun `LAN rejects a certificate that does not match the pin`() {
        server.enqueue(MockResponse().setBody("{}"))
        val clients = HttpClients(base, lanConfig(fp(otherCert)))
        val url = RoutedCallFactory.rewrite(RoutedCallFactory.virtual("health").toHttpUrl(), clients.config.lanHttpUrl!!)
        try {
            clients.lan.newCall(Request.Builder().url(url).build()).execute().close()
            fail("expected TLS failure")
        } catch (e: IOException) {
            // expected
        }
    }

    @Test fun `tunnel route rewrites the virtual host and adds CF Access headers`() = runBlocking {
        val plain = MockWebServer().apply { start() }
        try {
            plain.enqueue(MockResponse().setBody("[]"))
            val cfg = ServerConfig(
                lanUrl = "https://192.0.2.1:8443",
                tunnelUrl = plain.url("/").toString().trimEnd('/'),
                certSha256 = fp(cert),
                cfAccessClientId = "cf-id",
                cfAccessClientSecret = "cf-secret",
                deviceToken = "tok",
            )
            val clients = HttpClients(base, cfg)
            val api = ApiFactory.create(RoutedCallFactory({ clients }, { Route.TUNNEL }, base))
            assertTrue(api.mixesToday().isEmpty())
            val req = plain.takeRequest()
            assertEquals("/api/v1/mixes/today", req.path)
            assertEquals("cf-id", req.getHeader("CF-Access-Client-Id"))
            assertEquals("cf-secret", req.getHeader("CF-Access-Client-Secret"))
            assertEquals("Bearer tok", req.getHeader("Authorization"))
        } finally {
            plain.shutdown()
        }
    }

    @Test fun `rewrite keeps query and a base path prefix`() {
        val out = RoutedCallFactory.rewrite(
            "http://nas.invalid/api/v1/stream/a%2Fb?format=opus".toHttpUrl(),
            "https://music.example.com/prefix/".toHttpUrl(),
        )
        assertEquals("https://music.example.com/prefix/api/v1/stream/a%2Fb?format=opus", out.toString())
    }

    @Test fun `fingerprint parsing accepts colon and plain hex`() {
        val hex = "ab".repeat(32)
        val colon = hex.chunked(2).joinToString(":").uppercase()
        assertTrue(PinnedCertTrustManager.parseFingerprint(hex).contentEquals(PinnedCertTrustManager.parseFingerprint(colon)))
    }

    @Test(expected = IllegalArgumentException::class)
    fun `fingerprint parsing rejects short input`() {
        PinnedCertTrustManager.parseFingerprint("AB:CD")
    }
}
