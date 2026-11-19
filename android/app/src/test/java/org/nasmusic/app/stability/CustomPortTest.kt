package org.nasmusic.app.stability

import kotlinx.coroutines.runBlocking
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import org.nasmusic.app.auth.AuthRepository
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.ServerInfo
import org.nasmusic.app.net.HttpClients
import org.nasmusic.app.net.ProbeOutcome
import org.nasmusic.app.net.Route
import org.nasmusic.app.net.RoutedCallFactory
import org.nasmusic.app.net.ServerAddress
import org.nasmusic.app.net.ServerConfig

/** Relay readiness: a public address with a custom port must work everywhere. */
class CustomPortTest {
    private val relay = "https://music.whoactuallyneeds.men:8443"

    @Test fun `typed forms of a public address with a port normalise to the same origin`() {
        listOf(relay, "music.whoactuallyneeds.men:8443", "$relay/", "$relay/api/v1", " $relay ").forEach {
            assertEquals(relay, ServerAddress.origin(ServerAddress.normalize(it).getOrThrow()))
        }
        assertFalse(ServerAddress.isLanHost("music.whoactuallyneeds.men"))
        // No port typed on a public name: plain 443, not the LAN default 8443.
        assertEquals("https://music.whoactuallyneeds.men", ServerAddress.origin(ServerAddress.normalize("music.whoactuallyneeds.men").getOrThrow()))
    }

    @Test fun `stream, art and connect URLs keep the port`() {
        val base = relay.toHttpUrl()
        assertEquals("$relay/api/v1/stream/t1?format=opus&bitrate=160",
            RoutedCallFactory.rewrite(RoutedCallFactory.virtual("stream/t1?format=opus&bitrate=160").toHttpUrl(), base).toString())
        assertEquals("$relay/api/v1/connect", RoutedCallFactory.rewrite(RoutedCallFactory.virtual("connect").toHttpUrl(), base).toString())
    }

    @Test fun `a CA-trusted login on a custom port becomes the remote route`() {
        val p = ProbeOutcome(relay.toHttpUrl(), "AA:".repeat(31) + "AA", caTrusted = true,
            info = ServerInfo("s1", lanUrl = "https://192.168.68.2:8443", tunnelUrl = null, certSha256 = "BB:".repeat(31) + "BB"))
        val (cfg, route) = AuthRepository.draftConfig(p)
        assertEquals(Route.TUNNEL, route)
        assertEquals(relay, cfg.tunnelUrl)
        assertEquals(8443, cfg.tunnelHttpUrl?.port)
    }

    @Test fun `requests on the remote route reach a non-default port with normal TLS rules`() = runBlocking {
        val server = MockWebServer().apply { start() } // random port, like :8443
        try {
            server.enqueue(MockResponse().setBody("[]"))
            val cfg = ServerConfig(lanUrl = "", tunnelUrl = server.url("/").toString().trimEnd('/'), certSha256 = "", deviceToken = "tok")
            val clients = HttpClients(OkHttpClient(), cfg)
            val api = ApiFactory.create(RoutedCallFactory({ clients }, { Route.TUNNEL }, OkHttpClient()))
            api.mixesToday()
            val req = server.takeRequest()
            assertEquals(server.port, req.requestUrl?.port)
            assertEquals("Bearer tok", req.getHeader("Authorization"))
        } finally {
            server.shutdown()
        }
    }
}
