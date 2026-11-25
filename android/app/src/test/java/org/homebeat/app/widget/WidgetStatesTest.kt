package org.homebeat.app.widget

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.homebeat.app.data.api.FamilyMode
import org.homebeat.app.data.api.ShelfItem
import org.homebeat.app.data.api.Track
import org.homebeat.app.playback.MediaItems
import org.homebeat.app.playback.NowPlaying
import org.homebeat.app.playback.PlaySource
import org.homebeat.app.playback.RemoteInfo
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35], application = android.app.Application::class)
class WidgetStatesTest {
    private val track = Track("t1", title = "Song", artistId = "ar", artist = "Band", albumId = "al", album = "Album", starred = true)
    private val item = MediaItems.fromTrack(track, PlaySource.LIBRARY)

    @Test fun localPlayerMapsTitleArtistCoverAndHeart() {
        val info = WidgetStates.fromNowPlaying(NowPlaying(item = item, isPlaying = true, hasNext = true))
        assertEquals("t1", info.trackId)
        assertEquals("Song", info.title)
        assertEquals("Band", info.artist)
        assertEquals("al", info.albumId)
        assertTrue(info.starred)
        assertTrue(info.isPlaying)
        assertTrue(info.hasNext)
        assertNull(info.deviceLabel)
        assertEquals("Band", WidgetStates.subtitle(info))
        assertEquals(PrimaryAction.TOGGLE, WidgetStates.primaryAction(info))
    }

    @Test fun remoteDeviceShowsPlayingOnAndControlsStayToggle() {
        val np = NowPlaying(item = item, isPlaying = false, remote = RemoteInfo("d1", "Kitchen", isSpeaker = true))
        val info = WidgetStates.fromNowPlaying(np)
        assertEquals("Playing on Kitchen", info.deviceLabel)
        assertEquals("Band · Playing on Kitchen", WidgetStates.subtitle(info))
        assertEquals(PrimaryAction.TOGGLE, WidgetStates.primaryAction(info))
        val tile = WidgetStates.tile(info)
        assertEquals("Song", tile.label)
        assertEquals("Band · Playing on Kitchen", tile.subtitle)
        assertFalse(tile.active)
        assertEquals(TileIcon.PLAY, tile.icon)
    }

    @Test fun castFromThisPhoneShowsSpeakerName() {
        val info = WidgetStates.fromNowPlaying(NowPlaying(item = item, isPlaying = true), castName = "Living room")
        assertEquals("Playing on Living room", info.deviceLabel)
    }

    @Test fun remoteWithTrackStillLoadingKeepsTheDeviceLabel() {
        val info = WidgetStates.fromNowPlaying(NowPlaying(remote = RemoteInfo("d1", "Kitchen", connecting = true)))
        assertFalse(info.hasTrack)
        assertEquals("Connecting to Kitchen…", WidgetStates.subtitle(info))
    }

    @Test fun choosePrefersRemoteThenLiveLocalThenRemembered() {
        val remote = PlayingInfo("r", "Remote", deviceLabel = "Playing on Kitchen", isPlaying = true)
        val local = PlayingInfo("l", "Local", isPlaying = true)
        val remembered = PlayingInfo("m", "Last", isPlaying = true, live = false)
        assertEquals(remote, WidgetStates.choose(remote, local, remembered))
        assertEquals(local, WidgetStates.choose(null, local, remembered))
        val shown = WidgetStates.choose(null, PlayingInfo(), remembered)
        assertEquals("m", shown.trackId)
        assertFalse("a remembered song is never shown as playing", shown.isPlaying)
        assertFalse(shown.live)
        assertEquals(PrimaryAction.RESUME, WidgetStates.primaryAction(shown))
        assertFalse(WidgetStates.choose(null, null, null).hasTrack)
    }

    @Test fun nothingQueuedMeansPlaySomethingForNow() {
        val empty = WidgetStates.choose(null, null, null)
        assertEquals(PrimaryAction.PLAY_SOMETHING, WidgetStates.primaryAction(empty))
        val tile = WidgetStates.tile(empty)
        assertEquals(WidgetStates.PLAY_SOMETHING, tile.label)
        assertEquals(TileIcon.SPARKLE, tile.icon)
        assertEquals("Nothing playing", WidgetStates.subtitle(empty))
    }

    @Test fun playingTileIsActiveWithPauseIcon() {
        val tile = WidgetStates.tile(PlayingInfo("t", "Song", "Band", isPlaying = true))
        assertTrue(tile.active)
        assertEquals(TileIcon.PAUSE, tile.icon)
        assertEquals("Band", tile.subtitle)
    }

    @Test fun starOverrideWinsForThatTrackOnly() {
        val info = PlayingInfo("t1", starred = false)
        assertTrue(WidgetStates.withStar(info, mapOf("t1" to true)).starred)
        assertFalse(WidgetStates.withStar(info, mapOf("other" to true)).starred)
    }

    @Test fun sizeBuckets() {
        assertEquals(WidgetSize.SMALL, WidgetStates.sizeFor(110f, 110f))
        assertEquals(WidgetSize.MEDIUM, WidgetStates.sizeFor(250f, 110f))
        assertEquals(WidgetSize.LARGE, WidgetStates.sizeFor(250f, 260f))
        assertEquals(WidgetSize.SMALL, WidgetStates.sizeFor(150f, 300f))
        // The Glance responsive sizes land in their own buckets.
        assertEquals(WidgetSize.SMALL, WidgetStates.sizeFor(NowPlayingWidget.SMALL.width.value, NowPlayingWidget.SMALL.height.value))
        assertEquals(WidgetSize.MEDIUM, WidgetStates.sizeFor(NowPlayingWidget.MEDIUM.width.value, NowPlayingWidget.MEDIUM.height.value))
        assertEquals(WidgetSize.LARGE, WidgetStates.sizeFor(NowPlayingWidget.LARGE.width.value, NowPlayingWidget.LARGE.height.value))
    }

    @Test fun familyTileRespectsTheAdminLock() {
        assertEquals(FamilyTileUi(active = true, locked = false, subtitle = "On"), WidgetStates.familyTile(FamilyMode(enabled = true)))
        assertEquals(FamilyTileUi(active = false, locked = false, subtitle = "Off"), WidgetStates.familyTile(FamilyMode()))
        val locked = WidgetStates.familyTile(FamilyMode(enabled = true, locked = true))
        assertTrue(locked.locked)
        assertEquals("On · locked", locked.subtitle)
    }

    @Test fun shortcutsArePinsFirstThenRecentWithoutDuplicates() {
        val pins = listOf(ShelfItem("album", "a", "A"), ShelfItem("device", "d", "Kitchen"))
        val recent = listOf(ShelfItem("album", "a", "A again"), ShelfItem("album", "b", "B"), ShelfItem("album", "c", "C"), ShelfItem("album", "e", "E"))
        assertEquals(listOf("a", "b", "c"), WidgetHub.pickShortcuts(pins, recent).map { it.id })
    }
}
