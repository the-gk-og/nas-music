package org.homebeat.app.auto

import android.net.Uri
import android.os.Bundle
import androidx.media3.common.MediaItem
import androidx.media3.session.MediaConstants
import androidx.media3.session.MediaLibraryService.LibraryParams
import java.io.File
import java.io.IOException
import java.lang.reflect.Proxy
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.homebeat.app.data.api.AlbumDetail
import org.homebeat.app.data.api.HomebeatApi
import org.homebeat.app.data.api.Queue
import org.homebeat.app.data.api.SearchResult
import org.homebeat.app.data.api.Track
import org.homebeat.app.playback.ArtProvider
import org.homebeat.app.playback.AutoLibrary
import org.homebeat.app.playback.MediaItems
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35], application = android.app.Application::class)
class AutoLibraryTest {
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
    private val auth = "org.homebeat.app.art"

    @Before fun setUp() { MediaItems.artAuthority = auth }
    @After fun tearDown() { MediaItems.artAuthority = null }

    private fun t(id: String, album: String = "al1") = Track(id = id, title = "Song $id", artist = "Artist", albumId = album, album = "Album")

    /** A HomebeatApi answering from [answers] by method name; anything else throws IOException (server down). */
    private fun api(answers: Map<String, (Array<Any?>) -> Any?>): HomebeatApi =
        Proxy.newProxyInstance(HomebeatApi::class.java.classLoader, arrayOf(HomebeatApi::class.java)) { _, m, args ->
            answers[m.name]?.invoke(args ?: emptyArray()) ?: throw IOException("offline: ${m.name}")
        } as HomebeatApi

    private fun MediaItem.browsable() = mediaMetadata.isBrowsable == true
    private fun MediaItem.playable() = mediaMetadata.isPlayable == true
    private fun MediaItem.extra(k: String) = mediaMetadata.extras?.get(k)

    private fun hints(limit: Int): LibraryParams =
        LibraryParams.Builder().setExtras(Bundle().apply { putInt(MediaConstants.EXTRAS_KEY_ROOT_CHILDREN_LIMIT, limit) }).build()

    @Test fun `root children are all browsable and respect Android Auto's limit`() = runBlocking {
        val lib = AutoLibrary({ api(emptyMap()) })
        val out = lib.onRoot("com.google.android.projection.gearhead", hints(4))
        assertEquals(MediaConstants.EXTRAS_VALUE_CONTENT_STYLE_LIST_ITEM, out.extras.getInt(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_BROWSABLE))
        val auto = lib.children(AutoLibrary.ROOT, "com.google.android.projection.gearhead")
        assertEquals(listOf("foryou", "library", "radio", "downloads"), auto.map { it.mediaId })
        assertTrue(auto.all { it.browsable() && !it.playable() })

        // A browser without a limit (Wear, AAOS media centre, tests) gets every section at the top.
        lib.onRoot("other", null)
        val flat = lib.children(AutoLibrary.ROOT, "other").map { it.mediaId }
        assertEquals(listOf("foryou", "continue", "playlists", "albums", "artists", "radio", "downloads"), flat)
        assertEquals(2, AutoLibrary.rootChildren(2).size)
        assertTrue(lib.root().browsable())
    }

    @Test fun `library folder and albums use grid hints`() = runBlocking {
        val lib = AutoLibrary({ api(emptyMap()) })
        val kids = lib.children(AutoLibrary.LIBRARY)
        assertEquals(listOf("playlists", "albums", "artists"), kids.map { it.mediaId })
        assertEquals(MediaConstants.EXTRAS_VALUE_CONTENT_STYLE_GRID_ITEM, kids[1].extra(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_BROWSABLE))
    }

    @Test fun `for you starts with the smart shuffle, then queues, then mixes`() = runBlocking {
        val lib = AutoLibrary({
            api(mapOf(
                "queues" to { _ -> listOf(
                    Queue("q-old", "Old", trackCount = 3, updatedAt = "2026-01-01"),
                    Queue("q-new", "Road trip", trackCount = 2, currentIndex = 1, updatedAt = "2026-11-01", tracks = listOf(t("a"), t("b", "al2"))),
                    Queue("q-empty", "Empty", trackCount = 0, updatedAt = "2026-12-01"),
                ) },
                "mixesToday" to { _ -> emptyList<Any>() },
            ))
        })
        val items = lib.children(AutoLibrary.FOR_YOU)
        assertEquals(listOf("radio:moment", "queue:q-new", "queue:q-old"), items.map { it.mediaId })
        assertTrue(items.all { it.playable() })
        assertEquals("Continue listening", items[1].extra(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_GROUP_TITLE))
        assertEquals("Song b · Artist", items[1].mediaMetadata.subtitle)
        assertEquals(ArtProvider.uri(auth, "al2", 256), items[1].mediaMetadata.artworkUri)
    }

    @Test fun `album children play the whole album from the tapped song`() = runBlocking {
        val detail = json.decodeFromString<AlbumDetail>(
            """{"id":"al1","title":"Album","artistId":"x","artist":"X","trackCount":3,
               "tracks":[{"id":"t1","title":"1","albumId":"al1"},{"id":"t2","title":"2","albumId":"al1"},{"id":"t3","title":"3","albumId":"al1"}]}""",
        )
        val lib = AutoLibrary({ api(mapOf("album" to { _ -> detail })) })
        val kids = lib.children("album:al1")
        assertEquals("play|album:al1|t2", kids[1].mediaId)
        assertTrue(kids.all { it.playable() && !it.browsable() })
        assertEquals(ArtProvider.uri(auth, "al1", 512), kids[0].mediaMetadata.artworkUri)
        val e = lib.expand(kids[1].mediaId)!!
        assertEquals(listOf("t1", "t2", "t3"), e.tracks.map { it.id })
        assertEquals(1, e.startIndex)
    }

    @Test fun `continue listening resumes the server queue where it was`() = runBlocking {
        val q = Queue("q1", "Road trip", currentIndex = 1, positionMs = 42_000, trackCount = 2, tracks = listOf(t("a"), t("b")))
        val lib = AutoLibrary({ api(mapOf("queue" to { _ -> q })) })
        val e = lib.expand("queue:q1")!!
        assertEquals("q1", e.queueId)
        assertEquals(1, e.startIndex)
        assertEquals(42_000, e.startPositionMs)
    }

    @Test fun `server failures become a message row and are remembered for reload`() = runBlocking {
        val lib = AutoLibrary({ api(emptyMap()) })
        val kids = lib.children(AutoLibrary.ALBUMS)
        assertEquals(AutoLibrary.OFFLINE_ID, kids.single().mediaId)
        assertFalse(kids.single().browsable() || kids.single().playable())
        assertTrue(AutoLibrary.ALBUMS in lib.failed)

        val unpaired = AutoLibrary({ api(emptyMap()) }, paired = { false })
        assertEquals(AutoLibrary.NOT_PAIRED_ID, unpaired.children(AutoLibrary.PLAYLISTS).single().mediaId)
    }

    @Test fun `downloads work offline and play as one queue`() = runBlocking {
        val lib = AutoLibrary({ api(emptyMap()) }, downloads = { listOf(t("d1"), t("d2")) })
        val kids = lib.children(AutoLibrary.DOWNLOADS)
        assertEquals(listOf("play|downloads|d1", "play|downloads|d2"), kids.map { it.mediaId })
        val e = lib.expand("play|downloads|d2")!!
        assertEquals(1, e.startIndex)
        assertEquals(2, e.tracks.size)
        assertEquals(AutoLibrary.EMPTY_ID, AutoLibrary({ api(emptyMap()) }).children(AutoLibrary.DOWNLOADS).single().mediaId)
    }

    @Test fun `search groups songs, albums and artists`() = runBlocking {
        val r = json.decodeFromString<SearchResult>(
            """{"tracks":[{"id":"t1","title":"Hey","albumId":"al1"}],
               "albums":[{"id":"al1","title":"Hey","artistId":"x","artist":"X","trackCount":1}],
               "artists":[{"id":"x","name":"X","albumCount":1}]}""",
        )
        var calls = 0
        val lib = AutoLibrary({ api(mapOf("search" to { _ -> calls++; r })) })
        val items = lib.search("hey")
        assertEquals(listOf("play|search:hey|t1", "album:al1", "artist:x"), items.map { it.mediaId })
        assertEquals(listOf("Songs", "Albums", "Artists"), items.map { it.extra(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_GROUP_TITLE) })
        lib.search("hey")
        assertEquals("expand", 0, lib.expand("play|search:hey|t1")!!.startIndex)
        assertEquals("search results are reused for paging and playing", 1, calls)
    }

    @Test fun `item lookups cover folders, radios and browse ids`() = runBlocking {
        val lib = AutoLibrary({ api(mapOf("track" to { a -> t(a[0] as String) })) })
        assertTrue(lib.item(AutoLibrary.ALBUMS)!!.browsable())
        assertTrue(lib.item("radio:moment")!!.playable())
        assertEquals("play|album:al1|t9", lib.item("play|album:al1|t9")!!.mediaId)
        assertEquals("t9", AutoLibrary.trackIdOf("play|album:al1|t9"))
        assertEquals("t9", AutoLibrary.trackIdOf("t9"))
    }

    @Test fun `art URIs round-trip and use the cached sizes`() {
        val u = ArtProvider.uri(auth, "album 1/x", 200)
        assertEquals("content", u.scheme)
        assertEquals(auth, u.authority)
        assertEquals(ArtProvider.ArtRequest("album 1/x", 256), ArtProvider.parse(u))
        assertEquals(512, ArtProvider.parse(Uri.parse("content://$auth/album/a?size=9999"))!!.size)
        assertEquals(512, ArtProvider.parse(Uri.parse("content://$auth/album/a"))!!.size)
        assertNull(ArtProvider.parse(Uri.parse("content://$auth/track/a")))
        assertNull(ArtProvider.parse(Uri.parse("content://other.files/album/a")))
        assertNull(ArtProvider.parse(Uri.parse("http://nas.invalid/api/v1/art/a")))
        assertTrue(ArtProvider.isOurs(u, auth))
        assertFalse(ArtProvider.ArtRequest("../x", 128).fileName().contains("/"))

        assertNull(MediaItems.artUri(""))
        MediaItems.artAuthority = null
        assertEquals("http://nas.invalid/api/v1/art/a?size=256", MediaItems.artUri("a", 256).toString())
    }

    @Test fun `art cache trims oldest files first`() {
        val dir = File(System.getProperty("java.io.tmpdir"), "art-trim-${System.nanoTime()}").apply { mkdirs() }
        try {
            val old = File(dir, "old").apply { writeBytes(ByteArray(600)); setLastModified(1_000) }
            val new = File(dir, "new").apply { writeBytes(ByteArray(600)); setLastModified(2_000_000) }
            ArtProvider.trim(dir, max = 1000)
            assertFalse(old.exists())
            assertNotNull(new.takeIf { it.exists() })
        } finally { dir.deleteRecursively() }
    }
}
