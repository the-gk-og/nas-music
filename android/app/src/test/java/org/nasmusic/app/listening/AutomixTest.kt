package org.nasmusic.app.listening

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.nasmusic.app.data.api.TrackMix
import org.nasmusic.app.playback.Automix
import org.nasmusic.app.playback.DjOptions
import org.nasmusic.app.playback.ListeningRules
import org.nasmusic.app.playback.ListeningSettings
import org.nasmusic.app.playback.TrackFacts
import kotlin.math.abs
import kotlin.math.roundToLong

/** Same cases as web/src/lib/automix.test.ts: the two players must plan identical transitions. */
class AutomixTest {
    private fun mix(bpm: Double, mixIn: Double = 0.5, downbeat: Double = 0.5, stable: Boolean = true, lastBeat: Double? = null) = TrackMix(
        bpm = bpm, beatS = 0.5, downbeatS = downbeat, beatsPerBar = 4, gridStable = stable, mixInS = mixIn,
        mixOutS = 0.5 + 60 / bpm * 4 * 96, lastBeatS = lastBeat ?: (0.5 + 60 / bpm * 4 * 112),
    )
    private val o = DjOptions(16, true, 6.0)
    private fun near(a: Double, b: Double, eps: Double = 1e-6) = abs(a - b) < eps
    private fun plan(am: TrackMix?, bm: TrackMix?, opts: DjOptions = o, aDur: Double = 240.0) =
        Automix.plan("a", aDur, am, "b", 240.0, bm, opts)

    @Test fun tempoMatchHalfDouble() {
        assertEquals(1.0, Automix.tempoMatch(128.0, 126.0).first, 0.0)
        assertEquals(2.0, Automix.tempoMatch(140.0, 70.5).first, 0.0)
        assertEquals(0.5, Automix.tempoMatch(87.0, 172.0).first, 0.0)
        assertEquals(16, Automix.djBeats(20))
        assertEquals(32, Automix.djBeats(30))
    }

    @Test fun beatmatchedPlanAlignsDownbeats() {
        val am = mix(124.0)
        val p = plan(am, mix(126.0, mixIn = 2.0, downbeat = 0.1))!!
        assertTrue(p.beatmatch)
        assertTrue(near(p.outStartS, am.mixOutS!!))
        assertEquals(16, p.beats)
        assertTrue(near(p.rate, 124.0 / 126.0))
        val n = (p.inStartS - 0.1) / (60.0 / 126 * 4)
        assertTrue(near(n, n.roundToLong().toDouble(), 1e-9))
        // after 8 outgoing beats the incoming has advanced 8 of its own beats
        assertTrue(near(Automix.expectedIn(p, p.outStartS + 8 * 60.0 / 124) - p.inStartS, 8 * 60.0 / 126, 1e-9))
    }

    @Test fun shortOutroMovesStartEarlierOnABar() {
        val last = 0.5 + 60.0 / 124 * 4 * 100
        val p = plan(mix(124.0, lastBeat = last), mix(126.0), o.copy(beats = 32))!!
        assertTrue(p.outStartS + p.durationS <= last + 1e-6)
        val bars = (p.outStartS - 0.5) / (60.0 / 124 * 4)
        assertTrue(near(bars, bars.roundToLong().toDouble(), 1e-9))
        assertEquals(32, p.beats)
    }

    @Test fun fallbacks() {
        val far = plan(mix(124.0), mix(100.0, mixIn = 3.0))!!
        assertFalse(far.beatmatch)
        assertEquals(1.0, far.rate, 0.0)
        assertEquals(6.0, far.durationS, 0.0)
        assertEquals(3.0, far.inStartS, 0.0)
        assertFalse(plan(mix(124.0), mix(126.0), o.copy(allowTempo = false))!!.beatmatch)
        assertTrue(plan(mix(124.0), mix(124.2), o.copy(allowTempo = false))!!.beatmatch)
        assertFalse(plan(mix(124.0), mix(124.0, stable = false))!!.beatmatch)
        assertNull(plan(mix(124.0), null))
        assertTrue(plan(mix(140.0), mix(71.0))!!.let { it.beatmatch && it.multiplier == 2.0 })
    }

    @Test fun curves() {
        val p = plan(mix(124.0), mix(124.0))!!
        assertEquals(0.0, Automix.volIn(p, 0.0), 1e-9)
        assertEquals(1.0, Automix.volIn(p, 0.5), 1e-9)
        assertEquals(1.0, Automix.volOut(p, 0.5), 1e-9)
        assertEquals(0.0, Automix.volOut(p, 1.0), 1e-9)
        assertEquals(Automix.BASS_KILL_DB, Automix.bassIn(p, 0.0), 0.0)
        assertEquals(0.0, Automix.bassIn(p, 1.0), 0.0)
        assertEquals(Automix.BASS_KILL_DB / 2, Automix.bassIn(p, 0.5), 1e-9)
        for (x in listOf(0.0, 0.3, 0.5, 0.52, 1.0)) assertEquals(Automix.BASS_KILL_DB, Automix.bassIn(p, x) + Automix.bassOut(p, x), 1e-9)
        val q = p.copy(rate = 0.95, inBeatS = 0.5)
        assertEquals(0.95, Automix.rampRate(q, 0.0), 1e-9)
        assertEquals(1.0, Automix.rampRate(q, Automix.RAMP_BEATS * 0.5), 1e-9)
        assertEquals(1.0, Automix.correctedRate(1.0, 0.001)!!, 0.0)
        assertTrue(Automix.correctedRate(1.0, 0.01)!! < 1.0)
        assertEquals(0.98, Automix.correctedRate(1.0, 0.1)!!, 1e-9)
        assertNull(Automix.correctedRate(1.0, 0.2))
    }

    @Test fun settingsAndRules() {
        assertEquals("crossfade", ListeningSettings(crossfadeS = 5).normalised().transition)
        assertEquals("off", ListeningSettings().normalised().transition)
        assertEquals(16, ListeningSettings(transition = "dj", djBeats = 20).normalised().djBeats)
        val dj = ListeningSettings(transition = "dj").normalised()
        assertTrue(dj.djOrdering)
        val x = TrackFacts("x", "alb", 1, 1, 240.0)
        val y = TrackFacts("y", "alb", 2, 1, 240.0)
        val z = TrackFacts("z", "other", 1, 1, 240.0)
        assertFalse(ListeningRules.djFor(dj, x, y))
        assertTrue(ListeningRules.djFor(dj, x, z))
        assertEquals(6000L, ListeningRules.crossfadeMs(dj, x, z))
        assertEquals(0L, ListeningRules.crossfadeMs(ListeningSettings(transition = "off", crossfadeS = 5), x, z))
        assertNotNull(ListeningRules.djPlan(dj, x.copy(mix = mix(124.0)), z.copy(mix = mix(125.0))))
    }

    @Test fun reorderKeepsHeadAndReasons() {
        val items = listOf("a", "b", "c", "d", "c")
        val reasons = listOf("ra", "rb", "rc", "rd", "rc2")
        val (out, rs) = Automix.reorder(items, { it }, 1, reasons, listOf("c", "d", "b", "c"))!!
        assertEquals(listOf("a", "c", "d", "b", "c"), out)
        assertEquals(listOf("ra", "rc", "rd", "rb", "rc2"), rs)
        assertNull(Automix.reorder(items, { it }, 1, reasons, listOf("c", "d", "b")))
        assertNull(Automix.reorder(items, { it }, 1, null, listOf("x", "d", "b", "c")))
    }
}
