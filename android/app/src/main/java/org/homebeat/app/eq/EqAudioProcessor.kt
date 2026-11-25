package org.homebeat.app.eq

import androidx.media3.common.C
import androidx.media3.common.audio.AudioProcessor
import androidx.media3.common.audio.BaseAudioProcessor
import androidx.media3.common.util.UnstableApi
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * ExoPlayer audio processor running [EqEngine] on 16-bit or float PCM, mono or stereo. Settings
 * come from [current] and are re-read every buffer; changes glide in over a few ms instead of rebuilding the filters.
 */
@UnstableApi
class EqAudioProcessor(private val current: () -> EqSettings) : BaseAudioProcessor() {
    private var engine: EqEngine? = null
    private var scratch = FloatArray(0)

    override fun onConfigure(inputAudioFormat: AudioProcessor.AudioFormat): AudioProcessor.AudioFormat {
        val ok = (inputAudioFormat.encoding == C.ENCODING_PCM_16BIT || inputAudioFormat.encoding == C.ENCODING_PCM_FLOAT) &&
            inputAudioFormat.channelCount in 1..2
        engine = null
        // Unsupported formats pass through untouched (processor inactive).
        return if (ok) inputAudioFormat else AudioProcessor.AudioFormat.NOT_SET
    }

    override fun queueInput(inputBuffer: ByteBuffer) {
        val fmt = inputAudioFormat
        val bytesPerSample = if (fmt.encoding == C.ENCODING_PCM_FLOAT) 4 else 2
        val remaining = inputBuffer.remaining()
        if (remaining == 0) return
        val samples = remaining / bytesPerSample
        val s = current()
        // Same stream: retune in place (filter state kept, gains glide) so moving a slider doesn't click.
        val e = engine?.takeIf { it.sampleRate == fmt.sampleRate && it.channels == fmt.channelCount }
            ?.also { if (it.settings != s) it.update(s) }
            ?: EqEngine(s, fmt.sampleRate, fmt.channelCount).also { engine = it }

        val out = replaceOutputBuffer(remaining)
        if (e.isIdentity) {
            out.put(inputBuffer)
        } else {
            if (scratch.size < samples) scratch = FloatArray(samples)
            val inBuf = inputBuffer.order(ByteOrder.nativeOrder())
            if (bytesPerSample == 2) for (i in 0 until samples) scratch[i] = inBuf.getShort() / 32768f
            else for (i in 0 until samples) scratch[i] = inBuf.getFloat()
            e.process(scratch, samples / fmt.channelCount)
            if (bytesPerSample == 2) for (i in 0 until samples) out.putShort((scratch[i] * 32767f).toInt().coerceIn(-32768, 32767).toShort())
            else for (i in 0 until samples) out.putFloat(scratch[i])
        }
        out.flip()
    }

    override fun onFlush() { engine = null }
    override fun onReset() { engine = null; scratch = FloatArray(0) }
}
