package org.nasmusic.app.v7

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.ArtistPage
import org.nasmusic.app.data.api.SmartDefinition
import org.nasmusic.app.data.api.SmartFieldSpec
import org.nasmusic.app.data.api.SmartLimit
import org.nasmusic.app.data.api.SmartNode
import org.nasmusic.app.data.api.SmartPlaylist
import org.nasmusic.app.data.smart.SmartRules

class SmartAndArtistTest {
    private val json = ApiFactory.json
    private val fields = listOf(
        SmartFieldSpec("playCount", "Play count", "number", listOf("eq", "gte", "between")),
        SmartFieldSpec("lastPlayed", "Last played", "date", listOf("inLast", "notInLast", "before", "never")),
        SmartFieldSpec("energy", "Energy", "percent", listOf("gte", "between")),
        SmartFieldSpec("genre", "Genre", "text", listOf("is", "contains")),
        SmartFieldSpec("favourite", "Favourite", "bool", listOf("is", "isNot")),
    )

    @Test
    fun definitionEncodesRulesAndGroupsTheWayTheServerReadsThem() {
        val d = SmartDefinition("all", listOf(
            SmartRules.newRule(fields[0], "gte"),
            SmartNode(match = "any", rules = listOf(SmartRules.newRule(fields[4]))),
        ), "most_played", SmartLimit("songs", 50))
        val o = json.encodeToJsonElement(SmartDefinition.serializer(), d).jsonObject
        val rule = (o["rules"] as JsonArray)[0].jsonObject
        assertEquals(setOf("field", "op", "value"), rule.keys) // no "rules" key -> the server sees a rule
        assertEquals(JsonPrimitive(1L), rule["value"])
        val group = (o["rules"] as JsonArray)[1].jsonObject
        assertEquals(setOf("match", "rules"), group.keys)
        assertEquals("""{"type":"songs","value":50}""", o["limit"].toString())
        val back = json.decodeFromJsonElement(SmartDefinition.serializer(), o)
        assertEquals(d, back)
        assertTrue(back.rules[1].isGroup)
    }

    @Test
    fun rulesHelpers() {
        val r = SmartNode("playCount", "gte", JsonPrimitive(5))
        assertEquals(5.0, SmartRules.retarget(r, fields[0], "eq").num)
        assertEquals(listOf(1.0, 10.0), SmartRules.retarget(r, fields[0], "between").pair)
        assertEquals("", SmartRules.retarget(r, fields[3]).text)
        val dated = SmartRules.newRule(fields[1])
        assertEquals("months", dated.unit)
        assertNull(SmartRules.retarget(dated, fields[1], "never").unit)
        assertEquals("Play count is at least 5", SmartRules.describe(r, fields))
        assertEquals("Last played not in the last 1 month", SmartRules.describe(SmartNode("lastPlayed", "notInLast", JsonPrimitive(1), "months"), fields))
        assertEquals("Energy is between 50% and 80%", SmartRules.describe(SmartNode("energy", "between", JsonArray(listOf(JsonPrimitive(0.5), JsonPrimitive(0.8)))), fields))
        assertEquals("All songs", SmartRules.describe(SmartDefinition(), fields))
        assertTrue(SmartRules.incomplete(listOf(SmartNode("genre", "is", JsonPrimitive(" "))), fields))
        assertTrue(SmartRules.incomplete(listOf(SmartNode(match = "any", rules = listOf(SmartNode("nope", "is")))), fields))
        assertFalse(SmartRules.incomplete(listOf(r), fields))
        assertEquals("1 h 30 min", SmartRules.duration(5400.0))
    }

    @Test
    fun parsesServerShapes() {
        val sp = json.decodeFromString(SmartPlaylist.serializer(), """
            {"id":"sp1","name":"Loved","definition":{"match":"all","rules":[{"match":"any","rules":[
             {"field":"favourite","op":"is","value":true},{"field":"rating","op":"gte","value":4.0}]},
             {"field":"lastPlayed","op":"notInLast","value":1.0,"unit":"months"}],"sort":"random","limit":{"type":"songs","value":100}},
             "preset":"loved_not_played","createdAt":"x","updatedAt":"x","refreshedAt":"x","trackCount":0,"durationS":0.0,"rev":3,"tracks":[]}
        """.trimIndent())
        assertEquals(2, sp.definition.rules.size)
        assertEquals(true, sp.definition.rules[0].rules!![0].bool)
        val page = json.decodeFromString(ArtistPage.serializer(), """
            {"id":"a1","name":"The Band","mbid":null,"artUrl":"art/artist/a1","photoUrl":"art/artist/a1",
             "info":{"status":"ok","genres":["britpop"],"yearsActive":{"from":1994,"to":null},"lifeSpan":{"begin":"1994"},
               "links":[{"kind":"musicbrainz","url":"https://musicbrainz.org/artist/x"}],
               "bio":{"text":"A band.","source":"Wikipedia","url":"u","license":"CC BY-SA 4.0","licenseUrl":"l"},
               "image":{"source":"Wikimedia Commons","author":"Jane","license":"CC BY 2.0","licenseUrl":null,"filePage":"f","credit":null}},
             "stats":{"trackCount":2,"durationS":4.0,"playCount":2,"albumCount":0,"singleCount":1},
             "topTracks":[], "albums":[], "singles":[], "appearsOn":[],
             "similar":[{"id":"b","name":"B","albumCount":1,"mbid":null,"similarity":0.8,"artUrl":null}],
             "radioSeed":{"type":"artist","id":"a1","title":"The Band"}}
        """.trimIndent())
        assertEquals("Jane", page.info.image?.author)
        assertEquals(1994, page.info.yearsActive?.from)
        assertEquals(0.8, page.similar[0].similarity)
    }
}
