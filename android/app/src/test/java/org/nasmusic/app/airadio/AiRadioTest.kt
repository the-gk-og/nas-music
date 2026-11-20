package org.nasmusic.app.airadio

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.Queue
import org.nasmusic.app.data.api.RadioRequest
import org.nasmusic.app.data.api.RadioSeed
import org.nasmusic.app.data.api.SteerResponse
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.data.api.VibeParse
import org.nasmusic.app.playback.AiRadio
import java.time.LocalDateTime

class AiRadioTest {
    private fun track(id: String) = Track(id, "T$id", "ar", "A", "al", "Al", durationS = 1.0, starred = false)

    @Test fun contextUsesIsoWeekdayAndCar() {
        val fri = LocalDateTime.of(2026, 11, 20, 19, 5)
        val c = AiRadio.contextFor(fri, car = false, output = "bluetooth:Buds")
        assertEquals(19, c.localHour); assertEquals(5, c.weekday); assertEquals("phone", c.deviceKind); assertEquals("bluetooth:Buds", c.output)
        val car = AiRadio.contextFor(LocalDateTime.of(2026, 11, 22, 8, 0), car = true, output = null)
        assertEquals(7, car.weekday); assertEquals("car", car.deviceKind); assertEquals("car", car.output)
    }

    @Test fun steerKeepsCurrentAndNext() {
        val local = listOf("a", "b", "c", "d", "e")
        val server = listOf("a", "b", "c", "x", "b", "y").map(::track)
        assertEquals(listOf("x", "y"), AiRadio.upcomingAfterSteer(local, server, 1).map { it.id })
        assertEquals(emptyList<String>(), AiRadio.upcomingAfterSteer(local, server.take(2), 1).map { it.id })
    }

    @Test fun radioModelsRoundTrip() {
        val json = ApiFactory.json
        val req = json.encodeToString(RadioRequest.serializer(), RadioRequest(RadioSeed("moment"), AiRadio.contextFor(LocalDateTime.of(2026, 1, 5, 9, 0), false, null)))
        val o = Json.parseToJsonElement(req).jsonObject
        assertEquals("moment", o["seed"]!!.jsonObject["type"]!!.toString().trim('"'))
        assertNull(o["seed"]!!.jsonObject["text"])        // explicitNulls = false
        val parsed = json.decodeFromString(VibeParse.serializer(),
            """{"text":"gym","title":"Gym","constraints":[{"id":"energy:0.65-","kind":"energy","label":"High energy","min":0.65,"future":1}]}""")
        assertEquals("High energy", parsed.constraints.single().label)
        val steer = json.decodeFromString(SteerResponse.serializer(), """{"station":{"radioId":"r","kind":"moment","title":"Smart shuffle",
            "stationKey":"moment:phone","explanation":"Because it's Friday evening.","steering":{"energy":1,"variety":0,"moreLike":[],
            "lessLike":[{"type":"artist","id":"ar","name":"A"}],"never":[]},"steeringSummary":"more energy"},
            "queue":{"id":"q","name":"Smart shuffle","radio":{"type":"moment","explanation":"x"},"tracks":[]}}""")
        assertEquals(1, steer.station.steering.energy)
        assertEquals("artist", steer.station.steering.lessLike.single().type)
        assertEquals(true, steer.queue.isRadio)
        assertEquals("x", (steer.queue as Queue).radio?.explanation)
    }
}
