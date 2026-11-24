package org.nasmusic.app.voice

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.nasmusic.app.data.api.VoiceResult

/** What the app does with the server's answer to a voice command (pure; unit-tested). */
sealed interface VoiceAction {
    data class PlayQueue(val queueId: String) : VoiceAction
    data object Pause : VoiceAction
    data object Resume : VoiceAction
    data object Next : VoiceAction
    data object Previous : VoiceAction
    data class SetVolume(val volume: Float) : VoiceAction
    /** Nothing to do here: the server did it (another room, a playlist add) or it failed; just show [VoiceResult.speech]. */
    data object None : VoiceAction
}

object VoiceCommands {
    fun action(r: VoiceResult, currentVolume: Float): VoiceAction {
        if (!r.ok || !r.local) return VoiceAction.None
        return when (r.intent) {
            "play" -> r.queueId?.let { VoiceAction.PlayQueue(it) } ?: VoiceAction.None
            "pause" -> VoiceAction.Pause
            "resume" -> VoiceAction.Resume
            "next" -> VoiceAction.Next
            "previous" -> VoiceAction.Previous
            "volume" -> {
                val v = r.volume ?: (currentVolume + (r.volumeStep ?: 0f))
                VoiceAction.SetVolume(v.coerceIn(0f, 1f))
            }
            else -> VoiceAction.None
        }
    }

    /** "what's playing" answered from what this app shows (the server may not know about local playback). */
    fun nowPlayingSpeech(title: String?, artist: String?, playing: Boolean): String? {
        if (title.isNullOrBlank()) return null
        val who = if (artist.isNullOrBlank()) title else "$title by $artist"
        return if (playing) "This is $who." else "$who is paused."
    }

    /** Vosk's JSON ({"text": "..."} or {"partial": "..."}) to plain text. */
    fun voskText(json: String?, key: String = "text"): String =
        json?.let { runCatching { Json.parseToJsonElement(it).jsonObject[key]?.jsonPrimitive?.contentOrNull }.getOrNull() }
            .orEmpty().trim()
}
