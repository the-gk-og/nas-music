package org.homebeat.app.local

import org.homebeat.app.data.api.Track
import org.homebeat.app.playback.OfflinePlayback
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalIdsTest {
    private fun t(id: String, title: String, artist: String = "A", album: String = "Al", albumId: String = "local:album:1", artistId: String = "local:artist:1", trackNo: Int? = null) =
        Track(id, title = title, artist = artist, album = album, albumId = albumId, artistId = artistId, trackNo = trackNo)

    @Test fun `ids round trip and never look like NAS ids`() {
        assertEquals("local:42", LocalIds.track(42))
        assertEquals(42L, LocalIds.mediaStoreId("local:42"))
        assertNull(LocalIds.mediaStoreId("42"))
        assertNull(LocalIds.mediaStoreId("local:album:7"))
        assertEquals(7L, LocalIds.albumMediaStoreId(LocalIds.album(7)))
        assertNull(LocalIds.albumMediaStoreId("abc123"))
        assertTrue(LocalIds.isLocal("local:artist:3"))
        assertFalse(LocalIds.isLocal("3f2a9c"))
        assertFalse(LocalIds.isLocal(null))
    }

    @Test fun `MediaStore track number splits into track and disc`() {
        assertEquals(5 to null, LocalIds.splitTrackNo(5))
        assertEquals(3 to 2, LocalIds.splitTrackNo(2003))
        assertEquals(null to null, LocalIds.splitTrackNo(0))
        assertEquals(null to 1, LocalIds.splitTrackNo(1000))
    }

    @Test fun `folder from relative path or full path`() {
        assertEquals("Music/Albums/Blur", LocalIds.folderOf("Music/Albums/Blur/", null))
        assertEquals("Music/Albums/Blur", LocalIds.folderOf(null, "/storage/emulated/0/Music/Albums/Blur/song.mp3"))
        assertEquals("Music", LocalIds.folderOf(null, "/storage/1234-ABCD/Music/x.flac"))
        assertEquals("", LocalIds.folderOf(null, null))
    }

    @Test fun `app-private and own media folders are skipped`() {
        assertTrue(LocalIds.isOwnFile("Android/data/org.homebeat.app/files/music", "org.homebeat.app"))
        assertTrue(LocalIds.isOwnFile("Android/media/org.homebeat.app", "org.homebeat.app"))
        assertFalse(LocalIds.isOwnFile("Music/Homebeat mixes", "org.homebeat.app"))
        assertFalse(LocalIds.isOwnFile("Android/media/com.whatsapp/WhatsApp/Media", "org.homebeat.app"))
    }

    @Test fun `folder choice includes subfolders and empty means all`() {
        assertTrue(LocalIds.inFolders("Music/Blur", emptySet()))
        assertTrue(LocalIds.inFolders("Music/Blur/Parklife", setOf("Music/Blur")))
        assertTrue(LocalIds.inFolders("Music/Blur", setOf("Music/Blur/")))
        assertFalse(LocalIds.inFolders("Music/Blurry", setOf("Music/Blur")))
        assertFalse(LocalIds.inFolders("Download", setOf("Music")))
    }

    @Test fun `folders are counted, biggest first`() {
        val f = LocalIds.folders(listOf("Music", "Download", "Music", "Music/Live"))
        assertEquals(listOf("Music" to 2, "Download" to 1, "Music/Live" to 1), f.map { it.path to it.trackCount })
    }

    @Test fun `search needs every word, title matches first`() {
        val tracks = listOf(
            t("local:1", "Girls & Boys", artist = "Blur", album = "Parklife"),
            t("local:2", "Parklife", artist = "Blur", album = "Parklife"),
            t("local:3", "Song 2", artist = "Blür", album = "Blur"),
        )
        assertEquals(listOf("local:2", "local:1"), LocalIds.search(tracks, "parklife blur").map { it.id })
        assertEquals(listOf("local:1", "local:2", "local:3"), LocalIds.search(tracks, "blur").map { it.id }.sorted())
        assertEquals(listOf("local:3"), LocalIds.search(tracks, "song 2").map { it.id })
        assertTrue(LocalIds.search(tracks, "   ").isEmpty())
    }

    @Test fun `songs the NAS also has are hidden from mixed results`() {
        val phone = listOf(t("local:1", "Song 2 (Remastered 2012)", artist = "Blur"), t("local:2", "Tender", artist = "Blur"))
        val nas = listOf(Track("abc", title = "Song 2", artist = "blur"))
        assertEquals(listOf("local:2"), LocalIds.withoutNasCopies(phone, nas).map { it.id })
        assertEquals(phone, LocalIds.withoutNasCopies(phone, emptyList()))
    }

    @Test fun `albums group by album id in track order`() {
        val tracks = listOf(
            t("local:2", "B", trackNo = 2), t("local:1", "A", trackNo = 1),
            t("local:3", "C", artist = "Other", album = "Mixtape", albumId = "local:album:9"),
        )
        val albums = LocalIds.albums(tracks)
        assertEquals(listOf("Al", "Mixtape"), albums.map { it.title })
        assertEquals(listOf("local:1", "local:2"), albums[0].tracks.map { it.id })
        assertEquals("A", albums[0].artist)
        val withGuest = LocalIds.albums(tracks + t("local:4", "D", artist = "Guest", trackNo = 3))
        assertEquals("Various artists", withGuest[0].artist)
    }

    @Test fun `recently added sorts by MediaStore id`() {
        val tracks = listOf(t("local:5", "x"), t("local:12", "y"), t("local:7", "z"))
        assertEquals(listOf("local:12", "local:7", "local:5"), LocalIds.sort(tracks, "added").map { it.id })
        assertEquals(listOf("x", "y", "z"), LocalIds.sort(tracks, "title").map { it.title })
    }

    @Test fun `phone songs count as playable offline`() {
        val list = listOf(Track("nas1"), Track("local:1"), Track("nas2"))
        val (kept, at) = OfflinePlayback.playable(list, 0, emptySet())!!
        assertEquals(listOf("local:1"), kept.map { it.id }); assertEquals(0, at)
        assertEquals(1, OfflinePlayback.nextPlayable(list.map { it.id }, 0, emptySet()))
    }
}
