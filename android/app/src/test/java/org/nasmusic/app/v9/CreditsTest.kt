package org.nasmusic.app.v9

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.CreditPerson
import org.nasmusic.app.data.api.TrackCredits

/** api 0.9 song credits: the models against JSON shaped like api/openapi.yaml (TrackCredits, CreditPerson). */
class CreditsTest {
    private val json = ApiFactory.json

    @Test
    fun trackCreditsParse() {
        val c = json.decodeFromString(TrackCredits.serializer(), """
            {"trackId":"t1","status":"ok","fetchedAt":"2026-11-24T10:00:00Z","recordingMbid":"r1",
             "musicbrainzUrl":"https://musicbrainz.org/recording/r1","newerField":1,
             "groups":[{"id":"producers","label":"Producers","people":[
               {"personId":"mb:p1","name":"Pat Producer","mbid":"p1","roles":["Co-producer"],"albumWide":false,
                "fromTags":false,"otherTracks":3,"artistId":null}]},
              {"id":"writers","label":"Songwriters","people":[
               {"personId":"n:tara tag","name":"Tara Tag","mbid":null,"roles":["Composer"],"fromTags":true,"otherTracks":0}]}]}
        """.trimIndent())
        assertEquals("ok", c.status)
        assertEquals(listOf("producers", "writers"), c.groups.map { it.id })
        val p = c.groups[0].people.single()
        assertEquals(3, p.otherTracks)
        assertNull(p.artistId)
        assertTrue(c.groups[1].people.single().fromTags)
    }

    @Test
    fun pendingWithNoGroups() {
        val c = json.decodeFromString(TrackCredits.serializer(), """{"trackId":"t1","status":"pending","groups":[]}""")
        assertEquals("pending", c.status)
        assertTrue(c.groups.isEmpty())
    }

    @Test
    fun personTracksCarryTheirRoles() {
        val p = json.decodeFromString(CreditPerson.serializer(), """
            {"id":"mb:p1","name":"Pat Producer","mbid":"p1","musicbrainzUrl":null,"artistId":"a9",
             "roles":["Producer","Guitar"],"trackCount":2,"tracks":[
              {"id":"t1","title":"Song","artistId":"a1","artist":"Band","albumId":"al1","album":"LP","durationS":201.5,
               "starred":false,"credits":["Producer","Guitar"]},
              {"id":"t2","title":"Other","artistId":"a1","artist":"Band","albumId":"al1","album":"LP","durationS":180,
               "starred":true,"credits":["Producer"]}]}
        """.trimIndent())
        val songs = p.songs
        assertEquals(listOf("t1", "t2"), songs.map { it.first.id })
        assertEquals(listOf("Producer", "Guitar"), songs[0].second)
        assertEquals("a9", p.artistId)
    }
}
