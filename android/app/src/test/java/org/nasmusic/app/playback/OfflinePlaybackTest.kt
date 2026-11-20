package org.nasmusic.app.playback

import kotlin.random.Random
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.nasmusic.app.data.api.Mix
import org.nasmusic.app.data.api.MixTrack
import org.nasmusic.app.data.api.RadioSeed
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.data.db.DownloadEntity
import org.nasmusic.app.data.db.OfflinePinEntity
import org.nasmusic.app.ui.screens.OfflineHome

class OfflinePlaybackTest {
    private fun t(id: String, artist: String = "ar$id", album: String = "al$id") = Track(id, title = id, artistId = artist, albumId = album)

    @Test fun `plays only downloaded songs, starting at the tapped one or the next downloaded`() {
        val list = listOf(t("a"), t("b"), t("c"), t("d"))
        val (kept, at) = OfflinePlayback.playable(list, 1, setOf("a", "b", "d"))!!
        assertEquals(listOf("a", "b", "d"), kept.map { it.id }); assertEquals(1, at)
        val (_, at2) = OfflinePlayback.playable(list, 2, setOf("a", "b", "d"))!!
        assertEquals(2, at2) // "c" isn't downloaded: start at "d"
        val (_, at3) = OfflinePlayback.playable(list, 3, setOf("a"))!!
        assertEquals(0, at3) // nothing after: wrap round
        assertNull(OfflinePlayback.playable(list, 0, emptySet()))
    }

    @Test fun `next playable skips undownloaded items`() {
        assertEquals(3, OfflinePlayback.nextPlayable(listOf("a", "b", "c", "d"), 0, setOf("a", "d")))
        assertNull(OfflinePlayback.nextPlayable(listOf("a", "b"), 0, setOf("a")))
    }

    @Test fun `offline radio starts with the seed and mixes in related songs without artist repeats`() {
        val songs = (1..6).map { t("x$it", artist = "seedArtist", album = "seedAlbum") } + (1..20).map { t("o$it", artist = "a${it % 7}") }
        val r = OfflinePlayback.radio(RadioSeed("track", "x1"), songs, Random(1))
        assertEquals("x1", r.first().id)
        assertEquals(songs.size, r.size)
        assertEquals(r.size, r.map { it.id }.toSet().size)
        assertTrue(r.take(12).count { it.artistId == "seedArtist" } >= 4)
        for (k in 1 until r.size) if (r[k].artistId == r[k - 1].artistId) {
            // only allowed when nothing else was left to swap in
            assertTrue(r.drop(k).all { it.artistId == r[k].artistId })
        }
        assertTrue(OfflinePlayback.radio(RadioSeed("random"), emptyList()).isEmpty())
        assertEquals(10, OfflinePlayback.radio(RadioSeed("random"), songs, Random(2), size = 10).size)
    }

    @Test fun `offline home groups downloads`() {
        val tracks = listOf(t("a"), t("b"), t("c")).associateBy { it.id }
        val rows = listOf(
            DownloadEntity("a", "opus", "/a", 1, "favourite", complete = true, reasons = "favourite,mix"),
            DownloadEntity("b", "opus", "/b", 1, "recent", complete = true, reasons = "recent"),
            DownloadEntity("c", "opus", "", 0, "manual", complete = false),
        )
        val mix = Mix("m", "daily", "Daily", "2026-11-20T00:00:00Z", tracks = listOf(MixTrack(t("a"), "r"), MixTrack(t("z"), "r")))
        val empty = Mix("e", "daily", "Nothing held", "2026-11-20T00:00:00Z", tracks = listOf(MixTrack(t("z"), "r")))
        val home = OfflineHome.build(rows, tracks, listOf(mix, empty), listOf(OfflinePinEntity("album:x", "album", "x", "X", "b,c")))
        assertEquals(listOf("a"), home.favourites.map { it.id })
        assertEquals(listOf("b"), home.recent.map { it.id })
        assertEquals(listOf("Daily"), home.mixes.map { it.first.title })
        assertEquals(listOf("a"), home.mixes.single().second.map { it.id })
        assertEquals(listOf("b"), home.pins.single().second.map { it.id }) // "c" is still waiting to download
    }
}
