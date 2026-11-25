package org.homebeat.app.v5

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.homebeat.app.connect.ConnectMessage
import org.homebeat.app.connect.ConnectProtocol
import org.homebeat.app.data.api.Playlist
import org.homebeat.app.data.api.PlaylistActivity
import org.homebeat.app.data.api.TrackTagDetail
import org.homebeat.app.ui.describe
import org.homebeat.app.ui.tagChanges

class CollabAndTagsTest {
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }

    @Test fun `parses playlist pushes`() {
        val m = ConnectProtocol.parse("""{"type":"playlist","playlistId":"p1","rev":7,"deleted":false}""") as ConnectMessage.PlaylistChanged
        assertEquals(ConnectMessage.PlaylistChanged("p1", 7, false), m)
        val d = ConnectProtocol.parse("""{"type":"playlist","playlistId":"p2","rev":null,"deleted":true}""") as ConnectMessage.PlaylistChanged
        assertTrue(d.deleted)
        assertNull(d.rev)
        assertTrue(ConnectProtocol.parse("""{"type":"playlist"}""") is ConnectMessage.Unknown)
    }

    @Test fun `decodes a collaborative playlist and still decodes a v4 one`() {
        val p = json.decodeFromString<Playlist>(
            """{"id":"p","name":"Trip","trackCount":1,"durationS":2.0,"ownerId":"u1","owner":"alice","role":"editor","rev":3,
               "memberCount":1,"collaborative":true,"tracks":[],"entries":[{"id":"e1","trackId":"t1","addedAt":"x","addedById":"u2","addedBy":"bob"}]}""",
        )
        assertEquals("editor", p.role)
        assertEquals("bob", p.entries.single().addedBy)
        val old = json.decodeFromString<Playlist>("""{"id":"p","name":"Old","trackCount":0}""")
        assertEquals("owner", old.role)
        assertTrue(old.entries.isEmpty())
    }

    @Test fun `decodes tag detail and activity`() {
        val d = json.decodeFromString<TrackTagDetail>(
            """{"trackId":"t","path":"a.flac","albumId":"al","file":{"title":"Song 1","year":2001},"effective":{"title":"Fixed","year":2001},
               "overridden":["title"],"overrides":[],"lyrics":null,"hasCoverOverride":false}""",
        )
        assertEquals("Fixed", d.effective.title)
        val a = json.decodeFromString<PlaylistActivity>("""{"id":5,"at":"x","user":"bob","action":"add","trackTitle":"Song","detail":{"count":3}}""")
        assertEquals("added “Song” and 2 more", describe(a))
    }

    @Test fun `only changed fields are sent, blank clears, numbers validated`() {
        val init = mapOf("title" to "A", "genre" to "Rock", "year" to "2001")
        val set = tagChanges(init, mapOf("title" to "A", "genre" to "", "year" to "1999"), setOf("year"))!!
        assertEquals(setOf("genre", "year"), set.keys)
        assertEquals(JsonNull, set["genre"])
        assertEquals(JsonPrimitive(1999), set["year"])
        assertNull(tagChanges(init, mapOf("year" to "19x9"), setOf("year")))
    }
}
