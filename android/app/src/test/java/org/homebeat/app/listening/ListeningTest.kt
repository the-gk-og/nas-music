package org.homebeat.app.listening

import kotlin.math.abs
import kotlin.math.pow
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.homebeat.app.data.api.ReplayGain
import org.homebeat.app.data.api.Track
import org.homebeat.app.eq.Biquad
import org.homebeat.app.eq.EqEngine
import org.homebeat.app.eq.EqSettings
import org.homebeat.app.eq.HeadphoneProfile
import org.homebeat.app.eq.ParametricFilter
import org.homebeat.app.playback.AutoLibrary
import org.homebeat.app.playback.ListeningRules
import org.homebeat.app.playback.ListeningSettings
import org.homebeat.app.playback.MediaItems
import org.homebeat.app.playback.PlaySource
import org.homebeat.app.playback.SleepTimer
import org.homebeat.app.playback.SleepTimerState
import org.homebeat.app.playback.TrackFacts
import org.homebeat.app.playback.VolumeMixer
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35], application = android.app.Application::class)
class ListeningTest {
    private fun t(id: String, album: String, no: Int?, disc: Int? = 1, dur: Double = 200.0, rg: ReplayGain? = null) =
        TrackFacts.of(Track(id, title = id, albumId = album, trackNo = no, discNo = disc, durationS = dur, replayGain = rg))

    @Test fun albumOrder() {
        assertTrue(ListeningRules.followsOnAlbum(t("1", "x", 1), t("2", "x", 2)))
        assertFalse(ListeningRules.followsOnAlbum(t("1", "x", 1), t("3", "x", 3)))
        assertFalse(ListeningRules.followsOnAlbum(t("1", "x", 1), t("2", "y", 2)))
        assertTrue(ListeningRules.followsOnAlbum(t("9", "x", 9, 1), t("10", "x", 1, 2)))
        assertTrue(ListeningRules.followsOnAlbum(t("1", "x", null), t("2", "x", null)))
    }

    @Test fun crossfadeRules() {
        val s = ListeningSettings(crossfadeS = 8)
        assertEquals(0, ListeningSettings().crossfadeS)
        assertEquals(0L, ListeningRules.crossfadeMs(ListeningSettings(), t("1", "x", 1), t("2", "y", 1)))
        assertEquals(0L, ListeningRules.crossfadeMs(s, t("1", "x", 1), t("2", "x", 2)))
        assertEquals(8000L, ListeningRules.crossfadeMs(s, t("1", "x", 1), t("2", "y", 1)))
        assertEquals(4000L, ListeningRules.crossfadeMs(s, t("1", "x", 1, dur = 12.0), t("2", "y", 1)))
        for (x in listOf(0.0, 0.3, 0.7, 1.0)) {
            assertEquals(1.0, ListeningRules.fadeIn(x).pow(2) + ListeningRules.fadeOut(x).pow(2), 1e-9)
        }
        assertEquals(12, ListeningSettings(crossfadeS = 40, levelling = "loud").normalised().crossfadeS)
        assertEquals("auto", ListeningSettings(levelling = "loud").normalised().levelling)
    }

    @Test fun levellingGain() {
        val rg = ReplayGain(-6.0, 0.5, -4.0, 0.9)
        val s = ListeningSettings(levelling = "track", preventClipping = false)
        val tr = t("1", "x", 1, rg = rg)
        assertEquals(10.0.pow(-6 / 20.0), ListeningRules.gain(tr, s, false), 1e-9)
        assertEquals(10.0.pow(-4 / 20.0), ListeningRules.gain(tr, s, true), 1e-9)
        assertEquals(10.0.pow(-3 / 20.0), ListeningRules.gain(tr, s.copy(preampDb = 3f), false), 1e-9)
        assertEquals(1.0, ListeningRules.gain(tr, s.copy(levelling = "off"), false), 0.0)
        val quiet = t("2", "x", 2, rg = ReplayGain(10.0, 0.5))
        assertEquals(2.0, ListeningRules.gain(quiet, s.copy(preventClipping = true), false), 1e-9)
        // auto: album gain only for an unshuffled album in order
        val auto = ListeningSettings(levelling = "auto")
        assertTrue(ListeningRules.useAlbumGain(auto, t("0", "x", 1), t("1", "x", 2), null, shuffled = false))
        assertFalse(ListeningRules.useAlbumGain(auto, t("0", "x", 1), t("1", "x", 2), null, shuffled = true))
        assertFalse(ListeningRules.useAlbumGain(auto, t("0", "y", 1), t("1", "x", 2), t("2", "z", 1), shuffled = false))
        assertTrue(ListeningRules.useAlbumGain(auto.copy(levelling = "album"), null, t("1", "x", 2), null, shuffled = true))
    }

    @Test fun mixerOnlyAttenuates() {
        var applied = -1f
        val m = VolumeMixer { applied = it }
        m.levelling = 2f
        assertEquals(1f, applied)
        m.user = 0.5f; m.levelling = 0.5f; m.fade = 0.5f
        assertEquals(0.125f, applied, 1e-6f)
        m.sleep = 0f
        assertEquals(0f, applied)
        assertEquals(0.5f, m.user)
    }

    @Test fun replayGainSurvivesMediaItems() {
        val track = Track("t1", "T", albumId = "al", trackNo = 3, discNo = 2, durationS = 100.0, replayGain = ReplayGain(-7.5, 0.98, -8.0, 1.0))
        val back = MediaItems.toTrack(MediaItems.fromTrack(track, PlaySource.LIBRARY))
        assertEquals(track.replayGain, back.replayGain)
        assertEquals(2, back.discNo)
        assertNull(MediaItems.toTrack(MediaItems.fromTrack(track.copy(replayGain = null), PlaySource.LIBRARY)).replayGain)
        // The server's JSON shape parses
        val parsed = Json { ignoreUnknownKeys = true }.decodeFromString(Track.serializer(),
            """{"id":"a","replayGain":{"trackGain":-3.2,"trackPeak":0.7,"albumGain":null,"albumPeak":null}}""")
        assertEquals(ReplayGain(-3.2, 0.7, null, null), parsed.replayGain)
    }

    @Test fun sleepTimer() {
        val a1 = t("1", "x", 1); val a2 = t("2", "x", 2); val b1 = t("3", "y", 1)
        assertTrue(SleepTimer(SleepTimer.Mode.TRACK).stopsAt(a1, a2))
        assertFalse(SleepTimer(SleepTimer.Mode.ALBUM, albumId = "x").stopsAt(a1, a2))
        assertTrue(SleepTimer(SleepTimer.Mode.ALBUM, albumId = "x").stopsAt(a2, b1))
        assertTrue(SleepTimer(SleepTimer.Mode.ALBUM, albumId = "x").stopsAt(a2, null))
        assertFalse(SleepTimer(SleepTimer.Mode.MINUTES, 1).stopsAt(a1, b1))
        assertEquals(1f, SleepTimer.fadeLevel(30_000))
        assertEquals(0.25f, SleepTimer.fadeLevel(5_000), 1e-6f)
        assertEquals(0f, SleepTimer.fadeLevel(0))
        assertEquals("15 min", SleepTimer(SleepTimer.Mode.MINUTES, 1000 + 14 * 60_000 + 30_000).label(1000))
        val st = SleepTimerState()
        st.toggleFromNotification()
        assertEquals(SleepTimer.Mode.MINUTES, st.timer.value?.mode)
        st.toggleFromNotification()
        assertNull(st.timer.value)
    }

    @Test fun autoPlayIds() {
        val id = AutoLibrary.playId("album:abc", "t1")
        assertEquals("album:abc" to "t1", AutoLibrary.parsePlayId(id))
        assertEquals("search:a|b" to "t2", AutoLibrary.parsePlayId(AutoLibrary.playId("search:a|b", "t2")))
        assertNull(AutoLibrary.parsePlayId("t1"))
        assertNull(AutoLibrary.parsePlayId("play|album:x|"))
        assertEquals("ac dc", AutoLibrary.norm("AC/DC"))
    }

    @Test fun parametricFiltersHaveTheRequestedGain() {
        val fs = 48_000.0
        val pk = Biquad.parametric(ParametricFilter("PK", 1000.0, 6.0, 1.4), fs, 2)!!
        assertEquals(6.0, pk.responseDb(fs, 1000.0), 1e-3)
        val ls = Biquad.parametric(ParametricFilter("LSC", 105.0, 6.4, 0.7), fs, 2)!!
        assertEquals(6.4, ls.responseDb(fs, 10.0), 0.1)
        assertTrue(abs(ls.responseDb(fs, 5000.0)) < 0.05)
        assertEquals(3.2, ls.responseDb(fs, 105.0), 0.1)
        val hs = Biquad.parametric(ParametricFilter("HSC", 10000.0, -2.1, 0.7), fs, 2)!!
        assertEquals(-2.1, hs.responseDb(fs, 23_000.0), 0.1)
        assertTrue(abs(hs.responseDb(fs, 100.0)) < 0.01)
        assertNull(Biquad.parametric(ParametricFilter("XX", 1.0, 1.0, 1.0), fs, 2))
    }

    @Test fun headphoneProfileInTheChain() {
        val hp = HeadphoneProfile("x", "HD 650", "oratory1990", -6.1, listOf(ParametricFilter("LSC", 105.0, 6.4, 0.7)))
        assertFalse(EqEngine(EqSettings(enabled = true, headphone = hp), 48_000, 2).isIdentity)
        assertTrue(EqEngine(EqSettings(enabled = false, headphone = hp), 48_000, 2).isIdentity)
        // Profiles saved before this change still load (headphone defaults to null).
        val old = Json { ignoreUnknownKeys = true }.decodeFromString(EqSettings.serializer(), """{"enabled":true,"preset":"Rock"}""")
        assertNull(old.headphone)
        val json = Json.encodeToString(EqSettings.serializer(), EqSettings(headphone = hp))
        assertEquals(hp, Json.decodeFromString(EqSettings.serializer(), json).headphone)
    }
}
