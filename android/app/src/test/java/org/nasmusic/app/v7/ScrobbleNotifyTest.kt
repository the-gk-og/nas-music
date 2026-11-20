package org.nasmusic.app.v7

import java.time.Instant
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.nasmusic.app.connect.ConnectMessage
import org.nasmusic.app.connect.ConnectProtocol
import org.nasmusic.app.data.api.AppNotification
import org.nasmusic.app.data.api.NotificationList
import org.nasmusic.app.data.api.ScrobbleService
import org.nasmusic.app.data.api.ScrobbleStatus
import org.nasmusic.app.notify.NotificationLogic
import org.nasmusic.app.ui.screens.scrobbleLine

class ScrobbleNotifyTest {
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
    private val now = Instant.parse("2026-11-20T12:00:00Z")
    private fun n(id: String, at: String, read: Boolean = false) = AppNotification(id, "new_music", "t$id", "b", createdAt = at, read = read)

    @Test fun connectNotificationMessage() {
        val m = ConnectProtocol.parse(
            """{"type":"notification","notification":{"id":"n1","kind":"new_music","title":"12 new songs",
               "body":"2 new albums from AJR","data":{"tracks":12,"albums":2,"artists":["AJR"],"albumIds":["a"],"trackIds":[]},
               "createdAt":"2026-11-20T11:00:00Z","read":false,"extra":1}}""",
        )
        assertTrue(m is ConnectMessage.Notification)
        val note = (m as ConnectMessage.Notification).notification
        assertEquals("12 new songs", note.title)
        assertEquals(2, note.data.albums)
        assertTrue(ConnectProtocol.parse("""{"type":"notification"}""") is ConnectMessage.Unknown)
    }

    @Test fun firstRunShowsOnlyRecentNewestUnread() {
        val items = listOf(n("old", "2026-11-10T00:00:00Z"), n("new", "2026-11-20T10:00:00Z"), n("newer", "2026-11-20T11:00:00Z", read = true))
        val (show, newest) = NotificationLogic.toShow(items, null, now)
        assertEquals(listOf("new"), show.map { it.id })
        assertEquals("2026-11-20T11:00:00Z", newest)
        val (none, last) = NotificationLogic.toShow(listOf(n("old", "2026-11-10T00:00:00Z")), null, now)
        assertTrue(none.isEmpty())
        assertEquals("2026-11-10T00:00:00Z", last)
    }

    @Test fun laterRunsShowNewestUnseenOnce() {
        val items = listOf(n("a", "2026-11-01T00:00:00Z"), n("b", "2026-11-02T00:00:00Z"), n("c", "2026-11-03T00:00:00Z"))
        val (show, newest) = NotificationLogic.toShow(items, "2026-11-01T00:00:00Z", now)
        assertEquals(listOf("c"), show.map { it.id })
        assertEquals("2026-11-03T00:00:00Z", newest)
        val (again, same) = NotificationLogic.toShow(items, newest, now)
        assertTrue(again.isEmpty())
        assertEquals(newest, same)
    }

    @Test fun parsesStatusAndLists() {
        val st = json.decodeFromString(ScrobbleStatus.serializer(),
            """{"services":[{"service":"listenbrainz","available":true,"connected":true,"enabled":true,"username":"me",
               "nowPlaying":true,"needsReauth":false,"pending":1,"failed":0,"sent":5,"lastScrobbleAt":"2026-11-20T10:00:00Z"},
               {"service":"lastfm","available":false,"connected":false,"enabled":false,"nowPlaying":true,"needsReauth":false,
               "pending":0,"failed":0,"sent":0}],"import":null,"importedEvents":0}""")
        assertEquals(2, st.services.size)
        assertEquals("Connected as me · last scrobble 2026-11-20 10:00 · 1 waiting", scrobbleLine(st.services[0]))
        assertEquals("Not set up on this server", scrobbleLine(st.services[1]))
        assertEquals(1, json.decodeFromString(NotificationList.serializer(), """{"items":[],"unread":1}""").unread)
    }

    @Test fun scrobbleLineStates() {
        val base = ScrobbleService("lastfm", connected = true, enabled = true, username = "u")
        assertEquals("Not connected", scrobbleLine(base.copy(connected = false)))
        assertTrue(scrobbleLine(base.copy(needsReauth = true, pending = 3)).endsWith("reconnect (3 waiting)"))
        assertEquals("Connected as u · paused", scrobbleLine(base.copy(enabled = false)))
        assertEquals("Connected as u · no scrobbles yet · 2 failed", scrobbleLine(base.copy(failed = 2)))
    }
}
