package org.homebeat.app.v2

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.sin
import kotlin.math.sqrt
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.homebeat.app.context.ContextBuilder
import org.homebeat.app.context.ContextInputs
import org.homebeat.app.context.ContextSignal
import org.homebeat.app.context.Geohash
import org.homebeat.app.context.ListeningDetails
import org.homebeat.app.context.MotionClassifier
import org.homebeat.app.context.PlaceClusterer
import org.homebeat.app.eq.EqEngine
import org.homebeat.app.eq.EqSettings
import org.homebeat.app.playback.ArtworkScaler

class EqAndContextTest {
    private val fs = 48_000

    private fun sine(freq: Double, frames: Int = fs, channels: Int = 2, amp: Double = 0.1) =
        FloatArray(frames * channels) { i -> (amp * sin(2 * PI * freq * (i / channels) / fs)).toFloat() }

    /** RMS of the second half (after the filters settle). */
    private fun rms(buf: FloatArray, channels: Int = 2, ch: Int = 0): Double {
        val frames = buf.size / channels
        var sum = 0.0; var n = 0
        for (f in frames / 2 until frames) { val v = buf[f * channels + ch]; sum += v * v; n++ }
        return sqrt(sum / n)
    }

    @Test fun `disabled or flat eq is an exact pass-through`() {
        val a = sine(1000.0); val b = a.copyOf()
        val e = EqEngine(EqSettings(enabled = false, bandsDb = List(10) { 6f }), fs, 2)
        assertTrue(e.isIdentity)
        e.process(b, b.size / 2)
        assertTrue(a.contentEquals(b))
        assertTrue(EqEngine(EqSettings(enabled = true), fs, 2).isIdentity)
    }

    @Test fun `a 1 kHz boost raises 1 kHz by about 12 dB and leaves 63 Hz alone`() {
        val s = EqSettings(enabled = true).withBand(5, 12f)
        val hi = sine(1000.0); val before = rms(hi)
        EqEngine(s, fs, 2).process(hi, hi.size / 2)
        val gainDb = 20 * kotlin.math.log10(rms(hi) / before)
        assertEquals(12.0, gainDb, 0.7)

        val lo = sine(63.0); val loBefore = rms(lo)
        EqEngine(s, fs, 2).process(lo, lo.size / 2)
        assertEquals(0.0, 20 * kotlin.math.log10(rms(lo) / loBefore), 0.5)
    }

    @Test fun `mono, balance and preamp`() {
        val buf = FloatArray(200) { i -> if (i % 2 == 0) 0.4f else 0f } // left only
        EqEngine(EqSettings(enabled = true, mono = true), fs, 2).process(buf, 100)
        assertEquals(0.2f, buf[0], 1e-4f); assertEquals(0.2f, buf[1], 1e-4f)

        val b2 = FloatArray(200) { 0.3f }
        EqEngine(EqSettings(enabled = true, balance = 1f), fs, 2).process(b2, 100)
        assertEquals(0f, b2[0], 1e-6f); assertEquals(0.3f, b2[1], 1e-4f)

        val b3 = FloatArray(200) { 0.2f }
        EqEngine(EqSettings(enabled = true, preampDb = -6f), fs, 2).process(b3, 100)
        assertEquals(0.1f, b3[0], 0.002f)
    }

    @Test fun `output never exceeds full scale`() {
        val buf = sine(100.0, amp = 0.9)
        EqEngine(EqSettings(enabled = true, bassBoost = 100, preampDb = 6f).withBand(2, 12f), fs, 2).process(buf, buf.size / 2)
        assertTrue(buf.all { abs(it) <= 1f })
    }

    @Test fun `presets have ten bands`() {
        assertTrue(EqSettings.PRESETS.values.all { it.size == 10 })
        assertEquals(10, EqSettings.BANDS_HZ.size)
    }

    @Test fun `context contains only enabled signals`() {
        val inputs = ContextInputs(
            listening = ListeningDetails(seeks = listOf(1000L to 30_000L), sessionId = "s1", sessionPosition = 3, addedFrom = "search"),
            output = "bluetooth:moto buds", network = "wifi", charging = true, batteryPct = 80, screenOn = false,
            motion = "walking", cadenceSpm = 110, place = "home", geohash5 = "u4pru", searchQuery = "conver", localHour = 22, weekday = 3,
        )
        assertNull(ContextBuilder.build(emptySet(), inputs))
        val only = ContextBuilder.build(setOf(ContextSignal.OUTPUT, ContextSignal.LOCATION), inputs)!!
        assertEquals(setOf("output", "place", "geohash5"), only.keys)
        assertEquals("home", only["place"]!!.jsonPrimitive.content)
        val all = ContextBuilder.build(ContextSignal.entries.toSet(), inputs)!!
        assertTrue(all.keys.containsAll(listOf("seeks", "sessionId", "network", "batteryPct", "motion", "cadenceSpm", "searchQuery", "localHour", "weekday", "deviceKind")))
        assertFalse("lat" in all.keys || "lon" in all.keys)
    }

    @Test fun `geohash, clustering and motion`() {
        assertEquals("u4pru", Geohash.encode(57.64911, 10.40744, 5))
        val c = PlaceClusterer()
        val home = c.assign(51.5000, -0.1200)
        assertEquals("place-1", home.label)
        assertEquals(1, c.assign(51.5005, -0.1203).id) // ~60 m away: same place
        assertEquals(2, c.assign(51.5300, -0.1200).id) // ~3 km: new place
        c.rename(1, "home")
        assertEquals("home", c.all.first { it.id == 1 }.label)
        assertEquals(2, c.all.first { it.id == 1 }.count)
        assertEquals("still", MotionClassifier.classify(null, null))
        assertEquals("walking", MotionClassifier.classify(110, 1.4f))
        assertEquals("running", MotionClassifier.classify(165, 3f))
        assertEquals("vehicle", MotionClassifier.classify(0, 15f))
    }

    @Test fun `watch artwork is scaled to at most 512 px`() {
        assertEquals(512 to 512, ArtworkScaler.scaledSize(1024, 1024))
        assertEquals(512 to 288, ArtworkScaler.scaledSize(1920, 1080))
        assertEquals(300 to 300, ArtworkScaler.scaledSize(300, 300))
    }
}
