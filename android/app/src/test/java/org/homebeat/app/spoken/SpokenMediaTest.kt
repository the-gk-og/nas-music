package org.homebeat.app.spoken

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.homebeat.app.connect.ConnectProtocol
import org.homebeat.app.connect.ConnectMessage
import org.homebeat.app.data.api.LenientJson
import org.homebeat.app.data.api.ApiFactory
import org.homebeat.app.data.api.SpokenChapter
import org.homebeat.app.data.api.Track
import org.homebeat.app.playback.MediaItems
import org.homebeat.app.playback.PlaySource
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Podcasts / audiobooks / stations as items of the main player (api 0.8). */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35], application = android.app.Application::class)
class SpokenMediaTest {
    private val book = SpokenItem(
        "book", "b1", "Long Book", "Author", "", 5400.0,
        chapters = listOf(SpokenChapter(0.0, "One"), SpokenChapter(2000.0, "Two")),
        partStarts = listOf(0.0, 1800.0, 3600.0), partDurations = listOf(1800.0, 1800.0, 1800.0), speed = 1.5f,
    )

    @Test fun bookBecomesOneItemPerPartCarryingTheWholeBook() {
        val items = SpokenMedia.mediaItems(book)
        assertEquals(listOf("book:b1:0", "book:b1:1", "book:b1:2"), items.map { it.mediaId })
        assertTrue(items.all { SpokenMedia.isSpoken(it) })
        assertEquals(book, SpokenMedia.item(items[2]))
        assertEquals(2, SpokenMedia.part(items[2]))
        assertEquals(3600.0 + 12.5, SpokenMedia.bookPositionS(book, 2, 12_500), 1e-9)
        assertTrue(items[1].localConfiguration!!.uri.toString().endsWith("stream/book/b1/1"))
    }

    @Test fun startsInThePartHoldingTheResumePoint() {
        assertEquals(1 to 200_000L, SpokenMedia.startAt(book, 2000.0))
        assertEquals(0 to 0L, SpokenMedia.startAt(book, -3.0))
        assertEquals(0 to 0L, SpokenMedia.startAt(SpokenItem("station", "s", "Radio", "", "", null), 99.0))
    }

    @Test fun musicIsNotSpokenAndSpokenHasNoTrackFacts() {
        val music = MediaItems.fromTrack(Track("t1", title = "Song"), PlaySource.LIBRARY)
        assertFalse(SpokenMedia.isSpoken(music))
        val ep = SpokenMedia.mediaItems(SpokenItem("episode", "e1", "Ep", "Show", "", 600.0, showId = "p1")).single()
        assertEquals("ep:e1", ep.mediaId)
        // No levelling / crossfade / DJ / sleep-at-album-end rules for podcasts: they have no TrackFacts.
        assertEquals(null, MediaItems.facts(ep))
    }

    @Test fun downloadedEpisodePlaysFromTheFile() {
        val f = java.io.File.createTempFile("episode", ".audio")
        val ep = SpokenMedia.mediaItems(SpokenItem("episode", "e1", "Ep", "Show", "", 600.0)) { f }.single()
        assertEquals("file", ep.localConfiguration!!.uri.scheme)
        f.delete()
    }

    @Test fun connectStateCarriesMedia() {
        val m = ConnectProtocol.parse("""{"type":"state","state":{"activeDeviceId":"web","queueId":null,"trackId":null,"positionMs":125000,"media":{"kind":"episode","id":"e1"}}}""")
        val s = (m as ConnectMessage.State).state
        assertEquals("episode", s.media?.kind)
        assertEquals("e1", s.media?.id)
        val music = ConnectProtocol.parse("""{"type":"state","state":{"queueId":"q","trackId":"t","media":null}}""") as ConnectMessage.State
        assertEquals(null, music.state.media)
    }

    @Test fun continueShelfKeepsEpisodeAndBookCards() {
        val e = ApiFactory.json.parseToJsonElement(
            """{"type":"episode","id":"e1","title":"Ep","subtitle":"Show","artUrl":"art/podcast/p1",
               "progress":{"positionMs":600000,"durationMs":3723000,"fraction":0.1612},"updatedAt":"2026-11-20T10:00:00.000Z",
               "episode":{"id":"e1","podcastId":"p1","title":"Ep"}}""",
        )
        val item = LenientJson.item(e)!!
        assertEquals("episode", item.type)
        assertEquals(0.1612f, item.progress!!.fraction, 1e-4f)
        assertEquals("p1", item.episode!!.podcastId)
    }
}
