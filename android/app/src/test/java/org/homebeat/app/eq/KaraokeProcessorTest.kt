package org.homebeat.app.eq

import androidx.media3.common.C
import androidx.media3.common.audio.AudioProcessor
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.PI
import kotlin.math.sin
import kotlin.math.sqrt

class KaraokeProcessorTest {
    private val sr = 48_000

    /** Runs `seconds` of a stereo tone through the processor; returns (left RMS, right RMS) of the last half. */
    private fun run(on: Boolean, hz: Double, left: Double, right: Double, seconds: Double = 1.0): Pair<Double, Double> {
        val p = KaraokeProcessor { on }
        p.configure(AudioProcessor.AudioFormat(sr, 2, C.ENCODING_PCM_FLOAT))
        p.flush()
        val frames = (sr * seconds).toInt()
        val outL = ArrayList<Float>(); val outR = ArrayList<Float>()
        var i = 0
        while (i < frames) {
            val n = minOf(1024, frames - i)
            val buf = ByteBuffer.allocateDirect(n * 8).order(ByteOrder.nativeOrder())
            for (k in 0 until n) {
                val v = sin(2 * PI * hz * (i + k) / sr)
                buf.putFloat((left * v).toFloat()); buf.putFloat((right * v).toFloat())
            }
            buf.flip()
            p.queueInput(buf)
            val out = p.output.order(ByteOrder.nativeOrder())
            while (out.remaining() >= 8) { outL.add(out.getFloat()); outR.add(out.getFloat()) }
            i += n
        }
        fun rms(x: List<Float>) = sqrt(x.drop(x.size / 2).sumOf { it.toDouble() * it } / (x.size - x.size / 2))
        return rms(outL) to rms(outR)
    }

    private val sineRms = 1 / sqrt(2.0)

    @Test fun `centred vocal range drops to about a tenth`() {
        val (l, r) = run(true, 1000.0, 1.0, 1.0)
        assertEquals(KaraokeProcessor.KEEP * sineRms, l, 0.01)
        assertEquals(KaraokeProcessor.KEEP * sineRms, r, 0.01)
    }

    @Test fun `centred bass is kept`() {
        val (l, _) = run(true, 40.0, 1.0, 1.0)
        assertTrue("bass rms $l", l > 0.9 * sineRms)
    }

    @Test fun `hard-panned instruments are kept`() {
        val (l, r) = run(true, 1000.0, 1.0, 0.0)
        // side: L' = S = L/2 (+ a little centre), R' = -L/2: the instrument stays, spread across both sides
        assertTrue(l > 0.5 * sineRms && r > 0.4 * sineRms)
    }

    @Test fun `off is a bit-exact pass-through`() {
        val (l, r) = run(false, 1000.0, 0.5, 0.25)
        assertEquals(0.5 * sineRms, l, 1e-4)
        assertEquals(0.25 * sineRms, r, 1e-4)
    }

    @Test fun `mono is left alone`() {
        val p = KaraokeProcessor { true }
        assertEquals(AudioProcessor.AudioFormat.NOT_SET, p.configure(AudioProcessor.AudioFormat(sr, 1, C.ENCODING_PCM_16BIT)))
    }
}
