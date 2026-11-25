package org.homebeat.app.playback

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.homebeat.app.data.prefs.AppSettings
import org.homebeat.app.net.Route

class StreamQualityTest {
    @Test fun networkKind() {
        assertEquals(NetworkKind.HOME, NetworkKind.of(Route.LAN, unmetered = true))
        assertEquals(NetworkKind.HOME, NetworkKind.of(Route.LAN, unmetered = false))
        assertEquals(NetworkKind.MOBILE, NetworkKind.of(Route.TUNNEL, unmetered = false))
        assertEquals(NetworkKind.REMOTE, NetworkKind.of(Route.TUNNEL, unmetered = true))
    }

    @Test fun autoIsLosslessAtHomeAnd128Away() {
        assertEquals(StreamSpec.LOSSLESS, StreamQuality.resolve(StreamQuality.AUTO, NetworkKind.HOME))
        assertEquals(StreamSpec(false, 128), StreamQuality.resolve(StreamQuality.AUTO, NetworkKind.MOBILE))
        assertEquals(StreamSpec(false, 128), StreamQuality.resolve(StreamQuality.AUTO, NetworkKind.REMOTE))
        assertEquals(StreamSpec(false, 256), StreamQuality.resolve(StreamQuality.OPUS_256, NetworkKind.HOME))
        assertEquals(StreamSpec.LOSSLESS, StreamQuality.resolve(StreamQuality.LOSSLESS, NetworkKind.MOBILE))
    }

    @Test fun keysRoundTripAndUnknownIsAuto() {
        StreamQuality.entries.forEach { assertEquals(it, StreamQuality.from(it.key)) }
        assertEquals(StreamQuality.AUTO, StreamQuality.from(null))
        assertEquals(StreamQuality.AUTO, StreamQuality.from("opus999"))
        // Opus choices are the server's bitrate tiers.
        assertEquals(listOf(64, 96, 128, 160, 192, 256), StreamQuality.entries.map { it.kbps }.filter { it > 0 })
    }

    @Test fun settingsPerNetwork() {
        val s = AppSettings(streamQualityHome = "lossless", streamQualityMobile = "opus96", streamQualityRemote = "auto")
        assertEquals(StreamQuality.LOSSLESS, s.streamQuality(NetworkKind.HOME))
        assertEquals(StreamQuality.OPUS_96, s.streamQuality(NetworkKind.MOBILE))
        assertEquals(StreamQuality.AUTO, s.streamQuality(NetworkKind.REMOTE))
        assertEquals(StreamQuality.AUTO, AppSettings().streamQuality(NetworkKind.HOME))
    }

    @Test fun specQueryAndParse() {
        assertEquals("format=flac", StreamSpec.LOSSLESS.query)
        assertEquals("format=opus&bitrate=96", StreamSpec(false, 96).query)
        assertEquals(StreamSpec.LOSSLESS, StreamSpec.ofUrl("http://nas.invalid/api/v1/stream/t1?format=flac"))
        assertEquals(StreamSpec(false, 192), StreamSpec.ofUrl("http://nas.invalid/api/v1/stream/t1?format=opus&bitrate=192"))
        assertEquals(StreamSpec(false, 160), StreamSpec.ofUrl("http://nas.invalid/api/v1/stream/t1"))
        assertNull(StreamSpec.ofUrl("file:///data/music/t1.flac"))
        assertEquals("Opus 128", StreamSpec(false, 128).label)
    }

    @Test fun formatLabels() {
        assertEquals("FLAC 16/44.1", FormatLabel.original("FLAC", 16, 44100, 900))
        assertEquals("FLAC 24/96", FormatLabel.original("flac", 24, 96000, null))
        assertEquals("FLAC 48 kHz", FormatLabel.original("FLAC", null, 48000, null))
        assertEquals("MP3 320", FormatLabel.original("MP3", null, 44100, 320))
        assertEquals("Original", FormatLabel.original(null, null, null, null))
    }
}
