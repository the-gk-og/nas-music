package org.homebeat.app.eq

import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.sin
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RtaTest {
    private val fs = 48_000

    private fun sine(f: Double, amp: Double) = FloatArray(Spectrum.SIZE) { (amp * sin(2 * PI * f * it / fs)).toFloat() }

    private fun band(f: Double) = Spectrum.CENTRES.indices.minByOrNull { abs(Spectrum.CENTRES[it] - f) }!!

    @Test fun `a sine reads its level in its own band and little elsewhere`() {
        for (f in listOf(100.0, 1000.0, 8000.0)) {
            val db = Spectrum.bandsDb(sine(f, 0.5), fs)
            val i = band(f)
            // Hann spreads a tone over ~3 bins; the band holding it sums them back to the sine's level (−6 dB).
            assertEquals("$f Hz", -6.0, db[i], 1.5)
            assertTrue("$f Hz leaks ${db[i + 6]}", db[i + 6] < -50) // an octave up
        }
        assertTrue(Spectrum.bandsDb(FloatArray(Spectrum.SIZE), fs).all { it <= -119.0 })
    }

    @Test fun `fft matches a plain dft`() {
        val x = DoubleArray(Spectrum.SIZE) { sin(it * 0.37) + 0.3 * sin(it * 1.9 + 1) }
        val re = x.copyOf(); val im = DoubleArray(x.size)
        Spectrum.fft(re, im)
        for (k in listOf(0, 7, 240, 1234)) {
            var r = 0.0; var m = 0.0
            for (n in x.indices) { val a = -2 * PI * k * n / x.size; r += x[n] * kotlin.math.cos(a); m += x[n] * sin(a) }
            assertEquals(r, re[k], 1e-6); assertEquals(m, im[k], 1e-6)
        }
    }

    @Test fun `tap mixes to mono and keeps the latest samples`() {
        RtaTap.clear()
        val dst = FloatArray(Spectrum.SIZE)
        val bb = ByteBuffer.allocate(Spectrum.SIZE * 2 * 2).order(ByteOrder.nativeOrder())
        for (i in 0 until Spectrum.SIZE) { bb.putShort(16384); bb.putShort(0) }
        bb.flip()
        assertTrue(!RtaTap.snapshot(dst))
        RtaTap.push(bb, float = false, channels = 2, rate = 44_100)
        assertTrue(RtaTap.snapshot(dst))
        assertEquals(0.25f, dst.last(), 1e-6f)
        assertEquals(44_100, RtaTap.sampleRate)
    }
}
