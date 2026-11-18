package org.nasmusic.app.v2

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.nasmusic.app.connect.ConnectClient
import org.nasmusic.app.connect.ConnectMessage
import org.nasmusic.app.connect.ConnectProtocol
import org.nasmusic.app.connect.PlaybackState
import org.nasmusic.app.net.Route
import org.nasmusic.app.playback.AbRepeat
import org.nasmusic.app.playback.QueueSync

class ConnectAndQueuesTest {
    @Test fun `parses welcome, devices, state, command, takeover, release, queues`() {
        val w = ConnectProtocol.parse(
            """{"type":"welcome","deviceId":"d1","devices":[{"id":"d1","name":"Pixel","kind":"android","canPlay":true,"online":true,"isActive":false}],
               "state":{"activeDeviceId":"d2","queueId":"q1","index":3,"trackId":"t9","positionMs":61234,"positionAt":"2026-11-18T09:00:00.000Z","playing":true,"rev":42}}""",
        ) as ConnectMessage.Welcome
        assertEquals("d1", w.deviceId)
        assertEquals("Pixel", w.devices.single().name)
        assertEquals(42L, w.state!!.rev)
        val cmd = ConnectProtocol.parse("""{"type":"command","action":"seek","args":{"positionMs":5000},"fromDeviceId":"d2"}""") as ConnectMessage.Command
        assertEquals("seek", cmd.action)
        assertEquals("5000", cmd.args["positionMs"]!!.jsonPrimitive.content)
        assertTrue(ConnectProtocol.parse("""{"type":"release"}""") is ConnectMessage.Release)
        assertEquals("q7", (ConnectProtocol.parse("""{"type":"queues","queueId":"q7"}""") as ConnectMessage.QueuesChanged).queueId)
        assertTrue(ConnectProtocol.parse("""{"type":"takeover","state":{"index":1}}""") is ConnectMessage.Takeover)
        assertTrue(ConnectProtocol.parse("not json") is ConnectMessage.Unknown)
        assertTrue(ConnectProtocol.parse("""{"type":"state","state":{"rev":3,"playing":false}}""") is ConnectMessage.State)
    }

    @Test fun `outgoing messages`() {
        val hello = Json.parseToJsonElement(ConnectProtocol.hello("Pixel 8", "0.1.5")).jsonObject
        assertEquals("hello", hello["type"]!!.jsonPrimitive.content)
        assertEquals("android", hello["kind"]!!.jsonPrimitive.content)
        val t = Json.parseToJsonElement(ConnectProtocol.transfer("d3", true)).jsonObject
        assertEquals("d3", t["toDeviceId"]!!.jsonPrimitive.content)
        val c = Json.parseToJsonElement(ConnectProtocol.command("pause", target = "d2")).jsonObject
        assertEquals("d2", c["targetDeviceId"]!!.jsonPrimitive.content)
        val s = Json.parseToJsonElement(ConnectProtocol.state(PlaybackState(index = 2, rev = 5))).jsonObject
        assertEquals("2", s["state"]!!.jsonObject["index"]!!.jsonPrimitive.content)
    }

    @Test fun `position extrapolates while playing`() {
        val at = java.time.Instant.parse("2026-11-18T09:00:00Z").toEpochMilli()
        val s = PlaybackState(positionMs = 10_000, positionAt = "2026-11-18T09:00:00Z", playing = true, speed = 1.5f)
        assertEquals(10_000 + 3_000L, s.positionAt(at + 2_000))
        assertEquals(10_000L, s.copy(playing = false).positionAt(at + 2_000))
    }

    @Test fun `backoff grows to 30 s and rev ordering`() {
        assertEquals(1000L, ConnectProtocol.backoffMs(0, 0.5))
        assertEquals(8000L, ConnectProtocol.backoffMs(3, 0.5))
        assertEquals(30_000L, ConnectProtocol.backoffMs(12, 0.5))
        assertTrue(ConnectProtocol.backoffMs(0, 0.0) in 800..1200)
        assertFalse(ConnectProtocol.accept(PlaybackState(rev = 5), PlaybackState(rev = 4)))
        assertTrue(ConnectProtocol.accept(PlaybackState(rev = 5), PlaybackState(rev = 6)))
        assertTrue(ConnectProtocol.accept(null, PlaybackState(rev = 0)))
    }

    @Test fun `client becomes remote only when another online device is active`() {
        val c = ConnectClient({ null }, MutableStateFlow(Route.OFFLINE), CoroutineScope(Dispatchers.Unconfined), { "x" }, "1")
        c.handle(ConnectProtocol.parse("""{"type":"welcome","deviceId":"me","devices":[{"id":"me","name":"Phone"},{"id":"pc","name":"Desktop","online":true}],"state":{"activeDeviceId":"pc","rev":1}}"""))
        assertTrue(c.isRemote)
        assertEquals("Desktop", c.activeDevice()!!.name)
        c.handle(ConnectProtocol.parse("""{"type":"devices","devices":[{"id":"me","name":"Phone"},{"id":"pc","name":"Desktop","online":false}]}"""))
        assertFalse(c.isRemote)
        c.handle(ConnectProtocol.parse("""{"type":"state","state":{"activeDeviceId":"me","rev":2}}"""))
        assertTrue(c.isActiveHere)
        c.handle(ConnectProtocol.parse("""{"type":"state","state":{"activeDeviceId":"pc","rev":1}}""")) // stale
        assertTrue(c.isActiveHere)
    }

    @Test fun `queue move semantics`() {
        assertEquals(listOf("b", "c", "a", "d"), QueueSync.move(listOf("a", "b", "c", "d"), 0, 2))
        assertEquals(listOf("d", "a", "b", "c"), QueueSync.move(listOf("a", "b", "c", "d"), 3, 0))
        assertEquals(listOf("a"), QueueSync.move(listOf("a"), 0, 5))
        assertEquals(2, QueueSync.currentAfterMove(current = 0, from = 0, to = 2))
        assertEquals(1, QueueSync.currentAfterMove(current = 2, from = 0, to = 3))
        assertEquals(3, QueueSync.currentAfterMove(current = 2, from = 3, to = 1))
        assertEquals(2, QueueSync.currentAfterMove(current = 2, from = 4, to = 3))
    }

    @Test fun `a-b repeat`() {
        val r = AbRepeat()
        r.setA("t", 10_000)
        assertNull(AbRepeat.check(r.loop.value, "t", 50_000)) // no B yet
        r.setB("t", 10_200) // too close to A: ignored
        assertNull(r.loop.value!!.bMs)
        r.setB("t", 20_000)
        assertEquals(10_000L, AbRepeat.check(r.loop.value, "t", 20_000))
        assertNull(AbRepeat.check(r.loop.value, "t", 15_000))
        assertNull(AbRepeat.check(r.loop.value, "other", 25_000))
    }
}
