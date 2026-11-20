package org.nasmusic.app.eq

import androidx.media3.common.C
import androidx.media3.common.audio.AudioProcessor
import androidx.media3.common.audio.BaseAudioProcessor
import androidx.media3.common.util.UnstableApi
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * A low shelf (RBJ cookbook, 180 Hz, S = 1) whose gain the DJ automix moves for the bass swap. Flat (0 dB) is a
 * pass-through. The gain glides towards [targetDb] by at most [MAX_STEP_DB] per buffer, so changes don't click.
 * One instance per player (filter state is per stream).
 */
@UnstableApi
class BassShelfProcessor : BaseAudioProcessor() {
    /** Set from the playback thread; read on the audio thread. */
    @Volatile var targetDb: Double = 0.0

    private var currentDb = 0.0
    private var coefDb = Double.NaN
    private var b0 = 1.0; private var b1 = 0.0; private var b2 = 0.0; private var a1 = 0.0; private var a2 = 0.0
    private var z1 = DoubleArray(2); private var z2 = DoubleArray(2)
    private var scratch = FloatArray(0)

    override fun onConfigure(inputAudioFormat: AudioProcessor.AudioFormat): AudioProcessor.AudioFormat {
        val ok = (inputAudioFormat.encoding == C.ENCODING_PCM_16BIT || inputAudioFormat.encoding == C.ENCODING_PCM_FLOAT) &&
            inputAudioFormat.channelCount in 1..2
        return if (ok) inputAudioFormat else AudioProcessor.AudioFormat.NOT_SET
    }

    private fun design(db: Double, sr: Int) {
        val a = 10.0.pow(db / 40)
        val w = 2 * PI * FREQ_HZ / sr
        val cw = cos(w); val sw = sin(w)
        val alpha = sw / 2 * sqrt(2.0) // S = 1
        val sa = 2 * sqrt(a) * alpha
        val a0 = (a + 1) + (a - 1) * cw + sa
        b0 = a * ((a + 1) - (a - 1) * cw + sa) / a0
        b1 = 2 * a * ((a - 1) - (a + 1) * cw) / a0
        b2 = a * ((a + 1) - (a - 1) * cw - sa) / a0
        a1 = -2 * ((a - 1) + (a + 1) * cw) / a0
        a2 = ((a + 1) + (a - 1) * cw - sa) / a0
        coefDb = db
    }

    override fun queueInput(inputBuffer: ByteBuffer) {
        val remaining = inputBuffer.remaining()
        if (remaining == 0) return
        val fmt = inputAudioFormat
        val target = targetDb
        currentDb += (target - currentDb).coerceIn(-MAX_STEP_DB, MAX_STEP_DB)
        val out = replaceOutputBuffer(remaining)
        if (abs(currentDb) < 0.05 && abs(target) < 0.05) {
            currentDb = 0.0
            z1.fill(0.0); z2.fill(0.0)
            out.put(inputBuffer)
            out.flip()
            return
        }
        if (currentDb != coefDb) design(currentDb, fmt.sampleRate)
        val bytes = if (fmt.encoding == C.ENCODING_PCM_FLOAT) 4 else 2
        val samples = remaining / bytes
        if (scratch.size < samples) scratch = FloatArray(samples)
        val inBuf = inputBuffer.order(ByteOrder.nativeOrder())
        if (bytes == 2) for (i in 0 until samples) scratch[i] = inBuf.getShort() / 32768f
        else for (i in 0 until samples) scratch[i] = inBuf.getFloat()
        val ch = fmt.channelCount
        for (i in 0 until samples) {
            val c = i % ch
            val x = scratch[i].toDouble()
            val y = b0 * x + z1[c]
            z1[c] = b1 * x - a1 * y + z2[c]
            z2[c] = b2 * x - a2 * y
            scratch[i] = y.toFloat()
        }
        if (bytes == 2) for (i in 0 until samples) out.putShort((scratch[i] * 32767f).toInt().coerceIn(-32768, 32767).toShort())
        else for (i in 0 until samples) out.putFloat(scratch[i])
        out.flip()
    }

    override fun onFlush() { z1.fill(0.0); z2.fill(0.0) }
    override fun onReset() { z1.fill(0.0); z2.fill(0.0); currentDb = 0.0; coefDb = Double.NaN; scratch = FloatArray(0) }

    companion object {
        const val FREQ_HZ = 180.0
        const val MAX_STEP_DB = 3.0
    }
}
