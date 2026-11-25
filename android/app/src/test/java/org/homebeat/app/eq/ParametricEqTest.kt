package org.homebeat.app.eq

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.log10
import kotlin.math.sin
import kotlin.math.sqrt
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ParametricEqTest {
    private val fs = 48_000

    private fun sine(freq: Double, frames: Int = fs, amp: Double = 0.1) =
        FloatArray(frames * 2) { i -> (amp * sin(2 * PI * freq * (i / 2) / fs)).toFloat() }

    private fun rms(buf: FloatArray): Double {
        val frames = buf.size / 2
        var sum = 0.0; var n = 0
        for (f in frames / 2 until frames) { val v = buf[f * 2]; sum += v * v; n++ }
        return sqrt(sum / n)
    }

    private fun gainDb(s: EqSettings, freq: Double): Double {
        val b = sine(freq); val before = rms(b)
        EqEngine(s, fs, 2).process(b, b.size / 2)
        return 20 * log10(rms(b) / before)
    }

    private fun para(vararg b: EqBand) = EqSettings(enabled = true, mode = EqSettings.PARAMETRIC, parametric = b.toList())

    @Test fun `parametric mode replaces the graphic sliders`() {
        val s = para(EqBand("PK", 2000.0, -9.0, 2.0)).copy(bandsDb = List(10) { 6f })
        assertEquals(-9.0, gainDb(s, 2000.0), 0.5)
        assertEquals(0.0, gainDb(s, 100.0), 0.3) // graphic +6 dB not applied
    }

    @Test fun `pass filters roll off at their slope`() {
        val hp12 = para(EqBand("HP", 200.0, q = 0.707))
        assertEquals(-3.0, EqEngine.curveDb(hp12, doubleArrayOf(200.0))[0], 0.2)
        val h = EqEngine.curveDb(hp12, doubleArrayOf(25.0, 50.0))
        assertEquals(-12.0, h[0] - h[1], 1.0)
        val lp24 = para(EqBand("LP", 1000.0, slope = 24))
        assertEquals(-3.0, EqEngine.curveDb(lp24, doubleArrayOf(1000.0))[0], 0.2)
        // An octave well above the corner (and well below Nyquist, where the bilinear transform steepens things).
        val lo24 = para(EqBand("LP", 200.0, slope = 24))
        val oct = EqEngine.curveDb(lo24, doubleArrayOf(1600.0, 3200.0))
        assertEquals(-24.0, oct[1] - oct[0], 1.5)
        val lp48 = para(EqBand("LP", 200.0, slope = 48))
        val o48 = EqEngine.curveDb(lp48, doubleArrayOf(1600.0, 3200.0))
        assertEquals(-48.0, o48[1] - o48[0], 2.0)
        assertTrue(gainDb(lp24, 5000.0) < -40)
        assertEquals(0.0, gainDb(lp24, 100.0), 0.3)
        // Turned off: no effect at all.
        assertTrue(EqEngine(para(EqBand("LP", 1000.0, on = false)), fs, 2).isIdentity)
    }

    @Test fun `resonant pass filters and boosts get auto headroom`() {
        assertEquals(0.0, EqEngine.headroomDb(para(EqBand("HP", 80.0, slope = 24))), 0.05)
        val q4 = 20 * log10(4.0)
        assertEquals(-q4, EqEngine.headroomDb(para(EqBand("LP", 5000.0, q = 4.0))), 0.3)
        assertEquals(-8.0, EqEngine.headroomDb(para(EqBand("LSC", 120.0, 8.0, 0.7))), 0.2)
    }

    @Test fun `editing a band glides without clicks, removing one fades it out`() {
        val e = EqEngine(para(EqBand("PK", 1000.0, 0.0, 1.0)), fs, 2)
        val a = sine(1000.0, frames = 9600, amp = 0.5)
        e.process(a, 2400)
        e.update(para(EqBand("PK", 1000.0, -12.0, 1.0)))
        val b = a.copyOfRange(4800, 9600)
        e.process(b, 2400)
        var prev = a[4798].toDouble(); var maxStep = 0.0
        for (f in 0 until 2400) { val v = b[f * 2].toDouble(); maxStep = maxOf(maxStep, abs(v - prev)); prev = v }
        assertTrue("step $maxStep", maxStep < 0.07)
        e.update(para())
        val c = a.copyOfRange(9600, a.size)
        e.process(c, c.size / 2)
        assertTrue(e.isIdentity)
    }

    @Test fun `bands clamp and import from AutoEq and the graphic sliders`() {
        val b = EqBand("xx", 5.0, 99.0, 0.0, slope = 30).clamped()
        assertEquals("PK", b.type); assertEquals(10.0, b.fc, 0.0); assertEquals(15.0, b.gainDb, 0.0); assertEquals(0.1, b.q, 0.0); assertEquals(24, b.slope)
        val hp = HeadphoneProfile("x", "HD 650", "oratory1990", -6.1, listOf(ParametricFilter("LSC", 105.0, 6.4, 0.7), ParametricFilter("PK", 8800.0, 5.1, 1.42)))
        assertEquals(listOf("LSC", "PK"), EqBand.fromAutoEq(hp).map { it.type })
        val g = EqBand.fromGraphic(listOf(0f, 3f, 0f, 0f, 0f, -2f, 0f, 0f, 0f, 0f))
        assertEquals(listOf(63.0, 1000.0), g.map { it.fc })
        assertFalse(EqSettings().isParametric)
    }
}
