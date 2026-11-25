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

    @Test fun `disabled eq is an exact pass-through, flat eq only delays by the limiter look-ahead`() {
        val a = sine(1000.0); val b = a.copyOf()
        val e = EqEngine(EqSettings(enabled = false, bandsDb = List(10) { 6f }), fs, 2)
        assertTrue(e.isIdentity)
        e.process(b, b.size / 2)
        assertTrue(a.contentEquals(b))

        val flat = EqEngine(EqSettings(enabled = true), fs, 2)
        assertFalse(flat.isIdentity)
        val c = a.copyOf()
        flat.process(c, c.size / 2)
        val lat = (EqEngine.LOOKAHEAD_S * fs).toInt()
        for (f in 1000 until 1100) assertEquals(a[(f - lat) * 2], c[f * 2], 1e-6f)
    }

    @Test fun `a 1 kHz boost lifts 1 kHz about 12 dB over 63 Hz, with auto headroom so neither clips`() {
        val s = EqSettings(enabled = true).withBand(5, 12f)
        val e = EqEngine(s, fs, 2)
        assertEquals(-12.0 - EqEngine.SAFETY_DB, e.autoHeadroomDb, 0.3)
        val hi = sine(1000.0); val before = rms(hi)
        e.process(hi, hi.size / 2)
        val hiDb = 20 * kotlin.math.log10(rms(hi) / before)
        assertEquals(-EqEngine.SAFETY_DB, hiDb, 0.7) // the boost is paid for by the headroom

        val lo = sine(63.0); val loBefore = rms(lo)
        EqEngine(s, fs, 2).process(lo, lo.size / 2)
        val loDb = 20 * kotlin.math.log10(rms(lo) / loBefore)
        assertEquals(12.0, hiDb - loDb, 0.7)
    }

    @Test fun `cuts need no headroom and presets stay gentle`() {
        assertEquals(0.0, EqEngine.headroomDb(EqSettings(enabled = false, bandsDb = List(10) { 6f })), 1e-9)
        for ((name, bands) in EqSettings.PRESETS) {
            val h = EqEngine.headroomDb(EqSettings(enabled = true, bandsDb = bands))
            assertTrue("$name costs ${h} dB", h > -8.0)
        }
    }

    @Test fun `changing a band glides without a click`() {
        val e = EqEngine(EqSettings(enabled = true), fs, 2)
        val a = sine(1000.0, frames = 4800, amp = 0.5)
        e.process(a, 2400) // first half flat
        e.update(EqSettings(enabled = true).withBand(5, -12f))
        val b = a.copyOfRange(4800, a.size)
        e.process(b, 2400)
        // Sample-to-sample steps stay within what a 0.5 amplitude 1 kHz sine can do (2π·f/fs·A ≈ 0.065).
        var maxStep = 0.0
        var prev = a[4798].toDouble()
        for (f in 0 until 2400) { val v = b[f * 2].toDouble(); maxStep = maxOf(maxStep, abs(v - prev)); prev = v }
        assertTrue("step $maxStep", maxStep < 0.07)
        // …and it does get there.
        val c = sine(1000.0, amp = 0.5); val before = rms(c)
        e.process(c, c.size / 2)
        assertEquals(-12.0 - EqEngine.SAFETY_DB, 20 * kotlin.math.log10(rms(c) / before), 0.7)
    }

    @Test fun `mono, balance and preamp`() {
        val buf = FloatArray(2000) { i -> if (i % 2 == 0) 0.4f else 0f } // left only
        EqEngine(EqSettings(enabled = true, mono = true), fs, 2).process(buf, 1000)
        assertEquals(0.2f, buf[1000], 1e-4f); assertEquals(0.2f, buf[1001], 1e-4f)

        val b2 = FloatArray(2000) { 0.3f }
        EqEngine(EqSettings(enabled = true, balance = 1f), fs, 2).process(b2, 1000)
        assertEquals(0f, b2[1000], 1e-6f); assertEquals(0.3f, b2[1001], 1e-4f)

        val b3 = FloatArray(2000) { 0.2f }
        EqEngine(EqSettings(enabled = true, preampDb = -6f), fs, 2).process(b3, 1000)
        assertEquals(0.1f, b3[1000], 0.002f)
    }

    @Test fun `limiter turns loud peaks down smoothly instead of clipping them`() {
        // A full-scale 100 Hz sine pushed +6 dB: the limiter holds it at the ceiling with a steady gain, so the
        // waveform keeps its shape (no flat tops).
        val buf = sine(100.0, amp = 0.95)
        EqEngine(EqSettings(enabled = true, preampDb = 6f), fs, 2).process(buf, buf.size / 2)
        val tail = (fs / 2 until fs).map { buf[it * 2].toDouble() }
        assertTrue(tail.all { abs(it) <= EqEngine.LIMIT + 1e-6 })
        // Count samples sitting on the ceiling: a clipper would flatten about a third of every cycle at +6 dB.
        val pinned = tail.count { abs(it) > EqEngine.LIMIT - 1e-4 }
        assertTrue("pinned $pinned", pinned < tail.size / 50)
        // And it really is a sine still: peak / rms ≈ √2.
        val rmsTail = sqrt(tail.sumOf { it * it } / tail.size)
        assertEquals(sqrt(2.0), tail.maxOf { abs(it) } / rmsTail, 0.05)
    }

    @Test fun `active filters leave a safety margin even when they only cut`() {
        assertEquals(-EqEngine.SAFETY_DB, EqEngine.headroomDb(EqSettings(enabled = true, bandsDb = List(10) { -6f })), 1e-9)
    }

    @Test fun `output never exceeds full scale`() {
        val buf = sine(100.0, amp = 0.9)
        EqEngine(EqSettings(enabled = true, bassBoost = 100, preampDb = 6f, surround = 100).withBand(2, 12f), fs, 2).process(buf, buf.size / 2)
        assertTrue(buf.all { abs(it) <= EqEngine.LIMIT + 1e-6f })
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
