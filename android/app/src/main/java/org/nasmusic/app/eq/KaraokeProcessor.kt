package org.nasmusic.app.eq

import androidx.media3.common.C
import androidx.media3.common.audio.AudioProcessor
import androidx.media3.common.audio.BaseAudioProcessor
import androidx.media3.common.util.UnstableApi
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Karaoke: turns the singer down by cancelling the centre of the stereo image (no AI; works on downloads too).
 *
 * With M = (L+R)/2 and S = (L-R)/2:  L' = S + k·M + (1-k)·LP(M),  R' = -S + k·M + (1-k)·LP(M).
 * The centre (where lead vocals sit) drops to [KEEP] (-20 dB) above ~[BASS_HZ] while bass and kick survive through
 * a 4th-order low-pass (two Butterworth biquads). Same maths as the server's `/stream?karaoke=true` ffmpeg filter
 * and the web player's lib/karaoke.ts. Switching crossfades over [FADE_MS] so it doesn't click. Mono streams
 * pass through untouched (nothing to cancel against).
 */
@UnstableApi
class KaraokeProcessor(private val enabled: () -> Boolean) : BaseAudioProcessor() {
    private var mix = 0.0          // 0 = dry, 1 = karaoke; glides towards the switch
    private var b0 = 0.0; private var b1 = 0.0; private var b2 = 0.0; private var a1 = 0.0; private var a2 = 0.0
    private val z1 = DoubleArray(2); private val z2 = DoubleArray(2)   // state of the two cascaded biquads

    override fun onConfigure(inputAudioFormat: AudioProcessor.AudioFormat): AudioProcessor.AudioFormat {
        val ok = (inputAudioFormat.encoding == C.ENCODING_PCM_16BIT || inputAudioFormat.encoding == C.ENCODING_PCM_FLOAT) &&
            inputAudioFormat.channelCount == 2
        if (!ok) return AudioProcessor.AudioFormat.NOT_SET
        design(inputAudioFormat.sampleRate)
        return inputAudioFormat
    }

    private fun design(sr: Int) {
        val w = 2 * PI * BASS_HZ / sr
        val cw = cos(w)
        val alpha = sin(w) / (2 * Q)
        val a0 = 1 + alpha
        b0 = (1 - cw) / 2 / a0
        b1 = (1 - cw) / a0
        b2 = b0
        a1 = -2 * cw / a0
        a2 = (1 - alpha) / a0
    }

    override fun queueInput(inputBuffer: ByteBuffer) {
        val remaining = inputBuffer.remaining()
        if (remaining == 0) return
        val fmt = inputAudioFormat
        val target = if (enabled()) 1.0 else 0.0
        val out = replaceOutputBuffer(remaining)
        if (mix == 0.0 && target == 0.0) {
            z1.fill(0.0); z2.fill(0.0)
            out.put(inputBuffer)
            out.flip()
            return
        }
        val float = fmt.encoding == C.ENCODING_PCM_FLOAT
        val frames = remaining / (if (float) 8 else 4)
        val step = 1000.0 / (FADE_MS * fmt.sampleRate)
        val inBuf = inputBuffer.order(ByteOrder.nativeOrder())
        // two biquads in series, transposed direct form II; state index 0 = first, 1 = second
        var s1a = z1[0]; var s2a = z2[0]; var s1b = z1[1]; var s2b = z2[1]
        for (i in 0 until frames) {
            val l: Double
            val r: Double
            if (float) { l = inBuf.getFloat().toDouble(); r = inBuf.getFloat().toDouble() }
            else { l = inBuf.getShort() / 32768.0; r = inBuf.getShort() / 32768.0 }
            val m = (l + r) / 2
            val s = (l - r) / 2
            val y1 = b0 * m + s1a
            s1a = b1 * m - a1 * y1 + s2a
            s2a = b2 * m - a2 * y1
            val low = b0 * y1 + s1b
            s1b = b1 * y1 - a1 * low + s2b
            s2b = b2 * y1 - a2 * low
            if (mix < target) mix = minOf(target, mix + step) else if (mix > target) mix = maxOf(target, mix - step)
            val centre = KEEP * m + (1 - KEEP) * low
            val lo = l + mix * (centre + s - l)
            val ro = r + mix * (centre - s - r)
            if (float) { out.putFloat(lo.toFloat()); out.putFloat(ro.toFloat()) }
            else {
                out.putShort((lo * 32767).toInt().coerceIn(-32768, 32767).toShort())
                out.putShort((ro * 32767).toInt().coerceIn(-32768, 32767).toShort())
            }
        }
        z1[0] = s1a; z2[0] = s2a; z1[1] = s1b; z2[1] = s2b
        out.flip()
    }

    override fun onFlush() { z1.fill(0.0); z2.fill(0.0) }
    override fun onReset() { z1.fill(0.0); z2.fill(0.0); mix = 0.0 }

    companion object {
        const val KEEP = 0.1
        const val BASS_HZ = 150.0
        const val FADE_MS = 60.0
        private val Q = 1 / sqrt(2.0)
    }
}
