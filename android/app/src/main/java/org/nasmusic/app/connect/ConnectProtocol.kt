package org.nasmusic.app.connect

import java.time.Instant
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.encodeToJsonElement
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

/** docs/v2.md §5 PlaybackState. */
@Serializable
data class PlaybackState(
    val activeDeviceId: String? = null,
    val queueId: String? = null,
    val index: Int = 0,
    val trackId: String? = null,
    val positionMs: Long = 0,
    val positionAt: String? = null,
    val playing: Boolean = false,
    val volume: Float = 1f,
    val shuffle: Boolean = false,
    val repeat: String = "off",
    val speed: Float = 1f,
    val rev: Long = 0,
) {
    /** Position now: while playing it advances from positionAt at [speed]. */
    fun positionAt(nowMs: Long): Long {
        if (!playing) return positionMs
        val at = positionAt?.let { runCatching { Instant.parse(it).toEpochMilli() }.getOrNull() } ?: return positionMs
        return positionMs + ((nowMs - at).coerceAtLeast(0) * speed).toLong()
    }
}

@Serializable
data class ConnectDevice(
    val id: String,
    val name: String,
    val kind: String = "other",
    val canPlay: Boolean = true,
    val online: Boolean = true,
    val isActive: Boolean = false,
    /** Speakers only (kind "speaker"): chromecast, dlna, homeassistant, snapcast. The server plays on them. */
    val castType: String? = null,
) {
    val isSpeaker: Boolean get() = kind == "speaker"
}

sealed interface ConnectMessage {
    data class Welcome(val deviceId: String, val devices: List<ConnectDevice>, val state: PlaybackState?) : ConnectMessage
    data class Devices(val devices: List<ConnectDevice>) : ConnectMessage
    data class State(val state: PlaybackState) : ConnectMessage
    data class Command(val action: String, val args: JsonObject, val fromDeviceId: String?) : ConnectMessage
    data class Takeover(val state: PlaybackState) : ConnectMessage
    data object Release : ConnectMessage
    data class QueuesChanged(val queueId: String?) : ConnectMessage
    data object Pong : ConnectMessage
    /** `source` "speaker": a speaker problem worth showing (others are protocol noise). */
    data class Error(val detail: String, val source: String? = null) : ConnectMessage
    data class Unknown(val type: String?) : ConnectMessage
}

object ConnectProtocol {
    val json = Json { ignoreUnknownKeys = true; explicitNulls = false; encodeDefaults = true }

    fun parse(text: String): ConnectMessage {
        val o = runCatching { json.parseToJsonElement(text).jsonObject }.getOrNull() ?: return ConnectMessage.Unknown(null)
        val type = (o["type"] as? JsonPrimitive)?.contentOrNull
        fun state(key: String = "state") = o[key]?.let { runCatching { json.decodeFromJsonElement<PlaybackState>(it) }.getOrNull() }
        fun devices() = o["devices"]?.jsonArray?.mapNotNull { runCatching { json.decodeFromJsonElement<ConnectDevice>(it) }.getOrNull() }.orEmpty()
        return when (type) {
            "welcome" -> ConnectMessage.Welcome(o["deviceId"]?.jsonPrimitive?.contentOrNull.orEmpty(), devices(), state())
            "devices" -> ConnectMessage.Devices(devices())
            "state" -> state()?.let { ConnectMessage.State(it) } ?: runCatching {
                // Some servers send the state inline instead of nested.
                ConnectMessage.State(json.decodeFromJsonElement<PlaybackState>(o))
            }.getOrDefault(ConnectMessage.Unknown(type))
            "command" -> ConnectMessage.Command(
                o["action"]?.jsonPrimitive?.contentOrNull.orEmpty(),
                (o["args"] as? JsonObject) ?: JsonObject(emptyMap()),
                o["fromDeviceId"]?.jsonPrimitive?.contentOrNull,
            )
            "takeover" -> ConnectMessage.Takeover(state() ?: PlaybackState())
            "release" -> ConnectMessage.Release
            "queues" -> ConnectMessage.QueuesChanged(o["queueId"]?.jsonPrimitive?.contentOrNull)
            "pong" -> ConnectMessage.Pong
            "error" -> ConnectMessage.Error(o["detail"]?.jsonPrimitive?.contentOrNull.orEmpty(), o["source"]?.jsonPrimitive?.contentOrNull)
            else -> ConnectMessage.Unknown(type)
        }
    }

    fun hello(deviceName: String, appVersion: String, canPlay: Boolean = true) = buildJsonObject {
        put("type", "hello"); put("deviceName", deviceName); put("kind", "android")
        put("canPlay", canPlay); put("appVersion", appVersion)
    }.toString()

    fun state(s: PlaybackState) = JsonObject(
        mapOf("type" to JsonPrimitive("state"), "state" to json.encodeToJsonElement(s)),
    ).toString()

    fun command(action: String, args: Map<String, JsonElement> = emptyMap(), target: String? = null) = buildJsonObject {
        put("type", "command"); put("action", action); put("args", JsonObject(args))
        target?.let { put("targetDeviceId", it) }
    }.toString()

    fun transfer(toDeviceId: String, play: Boolean) = buildJsonObject {
        put("type", "transfer"); put("toDeviceId", toDeviceId); put("play", play)
    }.toString()

    const val PING = """{"type":"ping"}"""

    /** Reconnect delay: 1, 2, 4 … 30 s, with ±20 % jitter from [random] in 0..1. */
    fun backoffMs(attempt: Int, random: Double = 0.5): Long {
        val base = (1000L shl attempt.coerceIn(0, 5)).coerceAtMost(30_000L)
        return (base * (0.8 + 0.4 * random)).toLong()
    }

    /** Server rule: state updates are rev-ordered; ignore anything older than what we have. */
    fun accept(current: PlaybackState?, incoming: PlaybackState): Boolean = current == null || incoming.rev >= current.rev
}
