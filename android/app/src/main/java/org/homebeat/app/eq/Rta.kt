package org.homebeat.app.eq

import java.nio.ByteBuffer
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.log10
import kotlin.math.pow
import kotlin.math.sin

/**
 * Real-time analyser tap: the EQ processor copies what it plays (mixed to mono) in here while the RTA is showing,
 * and the EQ screen reads the latest [Spectrum.SIZE] samples. Lock-free; a torn read just shows one odd frame.
 */
object RtaTap {
    /** Only collect while someone is looking (the EQ page sets this). */
    @Volatile var enabled = false

    @Volatile var sampleRate = 48_000
        private set

    private val ring = FloatArray(Spectrum.SIZE * 2)
    @Volatile private var written = 0L

    /** Push interleaved PCM (16-bit or float, native order) from the processor's output buffer. */
    fun push(buf: ByteBuffer, float: Boolean, channels: Int, rate: Int) {
        sampleRate = rate
        val bytes = if (float) 4 else 2
        val frames = buf.remaining() / (bytes * channels)
        var w = written
        for (f in 0 until frames) {
            var sum = 0f
            for (ch in 0 until channels) sum += if (float) buf.getFloat() else buf.getShort() / 32768f
            ring[(w % ring.size).toInt()] = sum / channels
            w++
        }
        written = w
    }

    /** Latest [Spectrum.SIZE] samples into [dst]; false until that many have been played. */
    fun snapshot(dst: FloatArray): Boolean {
        val end = written
        if (end < dst.size) return false
        val start = end - dst.size
        for (i in dst.indices) dst[i] = ring[((start + i) % ring.size).toInt()]
        return true
    }

    fun clear() { written = 0 }
}

/** Spectrum for the RTA: Hann-windowed FFT, summed into 1/6-octave bands, in dB where a full-scale sine is 0 dB. */
object Spectrum {
    const val SIZE = 4096

    /** Band centres, 1/6 octave from 20 Hz to 20 kHz. */
    val CENTRES: DoubleArray = DoubleArray(61) { i -> 20.0 * 2.0.pow(i / 6.0) }.filter { it <= 20_500 }.toDoubleArray()

    private val window = DoubleArray(SIZE) { i -> 0.5 - 0.5 * cos(2 * PI * i / SIZE) }
    private val windowPower = window.sumOf { it * it }
    private val cosT = DoubleArray(SIZE / 2) { cos(2 * PI * it / SIZE) }
    private val sinT = DoubleArray(SIZE / 2) { -sin(2 * PI * it / SIZE) }

    /** In-place radix-2 FFT. */
    fun fft(re: DoubleArray, im: DoubleArray) {
        val n = re.size
        var j = 0
        for (i in 1 until n) {
            var bit = n shr 1
            while (j and bit != 0) { j = j xor bit; bit = bit shr 1 }
            j = j xor bit
            if (i < j) { var t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t }
        }
        var len = 2
        while (len <= n) {
            val step = SIZE / len
            for (i in 0 until n step len) for (k in 0 until len / 2) {
                val c = cosT[k * step]; val s = sinT[k * step]
                val a = i + k; val b = a + len / 2
                val tr = re[b] * c - im[b] * s; val ti = re[b] * s + im[b] * c
                re[b] = re[a] - tr; im[b] = im[a] - ti
                re[a] += tr; im[a] += ti
            }
            len = len shl 1
        }
    }

    /** Level per [CENTRES] band in dB (floor -120). [samples] must be [SIZE] long. */
    fun bandsDb(samples: FloatArray, fs: Int, out: DoubleArray = DoubleArray(CENTRES.size)): DoubleArray {
        val re = DoubleArray(SIZE) { samples[it] * window[it] }
        val im = DoubleArray(SIZE)
        fft(re, im)
        val binHz = fs.toDouble() / SIZE
        val edge = 2.0.pow(1.0 / 12)
        for (b in CENTRES.indices) {
            val lo = CENTRES[b] / edge; val hi = CENTRES[b] * edge
            var k0 = kotlin.math.ceil(lo / binHz).toInt().coerceAtLeast(1)
            var k1 = kotlin.math.floor(hi / binHz).toInt().coerceAtMost(SIZE / 2 - 1)
            // Low bands are narrower than the window's main lobe (3 bins): sum the 3 bins around the centre instead.
            if (k1 - k0 < 2) {
                val kc = kotlin.math.round(CENTRES[b] / binHz).toInt().coerceIn(2, SIZE / 2 - 2)
                k0 = kc - 1; k1 = kc + 1
            }
            var p = 0.0
            for (k in k0..k1) p += re[k] * re[k] + im[k] * im[k]
            // Parseval: 2·Σ|X|²/(N·Σw²) is the mean square; ×2 makes a sine of amplitude A read 20·log10(A).
            val ms = 2 * p / (SIZE * windowPower)
            out[b] = if (CENTRES[b] >= fs / 2.0) -120.0 else (10 * log10(2 * ms + 1e-12)).coerceAtLeast(-120.0)
        }
        return out
    }
}
