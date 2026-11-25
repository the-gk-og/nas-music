package org.homebeat.app.voice

import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import org.vosk.LibVosk
import org.vosk.LogLevel
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.RecognitionListener
import org.vosk.android.SpeechService
import java.io.File

/** Speech events while listening. */
sealed interface Heard {
    data class Partial(val text: String) : Heard
    data class Final(val text: String) : Heard
    data class Failed(val error: String) : Heard
}

/**
 * Listens on the microphone with Vosk, entirely on the phone. One utterance per [listen]: the flow emits
 * partial text and ends with a [Heard.Final] (empty if nothing was said within the timeout) or [Heard.Failed].
 * The loaded model is kept for the next time (loading takes a second).
 */
object VoiceListener {
    private const val RATE = 16000f
    private var model: Pair<String, Model>? = null

    @Synchronized
    private fun model(dir: File): Model {
        model?.let { (path, m) -> if (path == dir.path) return m }
        LibVosk.setLogLevel(LogLevel.WARNINGS)
        return Model(dir.path).also { model = dir.path to it }
    }

    fun listen(modelDir: File, timeoutMs: Int = 8000): Flow<Heard> = callbackFlow {
        var service: SpeechService? = null
        var rec: Recognizer? = null
        try {
            rec = Recognizer(model(modelDir), RATE)
            service = SpeechService(rec, RATE)
            service.startListening(object : RecognitionListener {
                override fun onPartialResult(hypothesis: String?) {
                    VoiceCommands.voskText(hypothesis, "partial").takeIf { it.isNotEmpty() }?.let { trySend(Heard.Partial(it)) }
                }
                override fun onResult(hypothesis: String?) {
                    val t = VoiceCommands.voskText(hypothesis)
                    if (t.isNotEmpty()) { trySend(Heard.Final(t)); close() }
                }
                override fun onFinalResult(hypothesis: String?) {
                    trySend(Heard.Final(VoiceCommands.voskText(hypothesis))); close()
                }
                override fun onError(exception: Exception?) {
                    trySend(Heard.Failed(exception?.message ?: "microphone error")); close()
                }
                override fun onTimeout() {
                    trySend(Heard.Final("")); close()
                }
            }, timeoutMs)
        } catch (e: Throwable) {
            trySend(Heard.Failed(e.message ?: e.javaClass.simpleName)); close()
        }
        awaitClose {
            runCatching { service?.stop() }
            runCatching { service?.shutdown() }
            runCatching { rec?.close() }
        }
    }
}
