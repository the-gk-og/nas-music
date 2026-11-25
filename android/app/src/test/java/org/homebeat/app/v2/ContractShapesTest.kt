package org.homebeat.app.v2

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.homebeat.app.context.ContextBuilder
import org.homebeat.app.context.ContextInputs
import org.homebeat.app.context.ContextSignal
import org.homebeat.app.context.ListeningDetails
import org.homebeat.app.data.api.ApiFactory
import org.homebeat.app.data.api.HomebeatApi
import org.homebeat.app.data.api.UserSettings

/** Shapes from the server's v2 contract draft (fee706b). */
class ContractShapesTest {
    private val server = MockWebServer()
    private lateinit var api: HomebeatApi

    @Before fun setUp() { server.start(); api = ApiFactory.create(OkHttpClient(), server.url("/api/v1/").toString()) }
    @After fun tearDown() = server.shutdown()

    @Test fun `settings are wrapped in a settings object`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"settings":{"context.location":true,"offline.favourites":false}}"""))
        assertEquals(JsonPrimitive(true), api.mySettings().settings["context.location"])
        server.takeRequest()
        server.enqueue(MockResponse().setBody("""{"settings":{}}"""))
        api.putMySettings(UserSettings(mapOf("context.motion" to JsonPrimitive(true))))
        assertEquals("""{"settings":{"context.motion":true}}""", server.takeRequest().body.readUtf8())
    }

    @Test fun `pair code QR payload is an object`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"pairCode":"ABCD2345","expiresAt":"2026-11-19T10:00:00Z","qrPayload":{"v":1,"lanUrl":"https://x","pairCode":"ABCD2345"}}"""))
        assertEquals("""{"v":1,"lanUrl":"https://x","pairCode":"ABCD2345"}""", api.pairCode().qrText)
    }

    @Test fun `deleting everything sends confirm`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"eventsDeleted":3}"""))
        api.deleteMyData("all", confirm = true)
        assertEquals("/api/v1/me/data?signal=all&confirm=true", server.takeRequest().path)
        server.enqueue(MockResponse().setBody("{}"))
        api.deleteMyData("location")
        assertEquals("/api/v1/me/data?signal=location", server.takeRequest().path)
    }

    @Test fun `lyrics list their sources and can be fetched by source`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"trackId":"t","synced":false,"source":"user","sources":["user","embedded"],"canEdit":true,"lines":[],"text":"x"}"""))
        val l = api.lyrics("t", "embedded").body()!!
        assertEquals(listOf("user", "embedded"), l.sources)
        assertEquals("/api/v1/tracks/t/lyrics?source=embedded", server.takeRequest().path)
    }

    @Test fun `volume changes are objects with position`() {
        val o = ContextBuilder.build(setOf(ContextSignal.LISTENING), ContextInputs(listening = ListeningDetails(volumeChanges = listOf(12_000L to 0.5f))))!!
        assertEquals("""[{"atMs":12000,"volume":0.5}]""", o["volumeChanges"].toString())
    }
}
