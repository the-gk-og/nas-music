package org.nasmusic.app.eq

import android.content.Context
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json

/** An audio output the EQ keeps a profile for. */
data class AudioOutput(val key: String, val label: String, val kind: String)

/**
 * EQ profiles per audio output (speaker, wired, each Bluetooth device by name), persisted locally
 * (the EQ is per device, never synced). Follows output changes via AudioDeviceCallback.
 */
class EqRepository(context: Context) {
    private val prefs = context.getSharedPreferences("eq_profiles", Context.MODE_PRIVATE)
    private val audio = context.getSystemService(AudioManager::class.java)
    private val json = Json { ignoreUnknownKeys = true }
    private val mapSer = MapSerializer(String.serializer(), EqSettings.serializer())

    private val profiles: MutableMap<String, EqSettings> =
        runCatching { json.decodeFromString(mapSer, prefs.getString("profiles", "{}")!!) }.getOrDefault(emptyMap()).toMutableMap()

    private val _output = MutableStateFlow(OutputDetector.current(audio))
    val output: StateFlow<AudioOutput> = _output.asStateFlow()

    /** Profile being edited in the UI (defaults to the current output). */
    private val _editing = MutableStateFlow(_output.value)
    val editing: StateFlow<AudioOutput> = _editing.asStateFlow()

    private val _active = MutableStateFlow(profiles[_output.value.key] ?: EqSettings())
    /** Settings applied to the audio right now (current output's profile). Read on the audio thread. */
    val active: StateFlow<EqSettings> = _active.asStateFlow()

    private val _edited = MutableStateFlow(_active.value)
    val edited: StateFlow<EqSettings> = _edited.asStateFlow()

    fun start() {
        audio.registerAudioDeviceCallback(object : AudioDeviceCallback() {
            override fun onAudioDevicesAdded(added: Array<out AudioDeviceInfo>?) = refresh()
            override fun onAudioDevicesRemoved(removed: Array<out AudioDeviceInfo>?) = refresh()
        }, null)
    }

    private fun refresh() {
        val o = OutputDetector.current(audio)
        val followEditing = _editing.value.key == _output.value.key
        _output.value = o
        _active.value = profiles[o.key] ?: EqSettings()
        if (followEditing) select(o)
    }

    /** All outputs with a saved profile, plus the current one. */
    fun knownOutputs(): List<AudioOutput> {
        val cur = _output.value
        val saved = profiles.keys.map { OutputDetector.fromKey(it) }
        return (listOf(cur) + saved).distinctBy { it.key }
    }

    fun select(o: AudioOutput) {
        _editing.value = o
        _edited.value = profiles[o.key] ?: EqSettings()
    }

    fun update(transform: (EqSettings) -> EqSettings) {
        val next = transform(_edited.value)
        _edited.value = next
        profiles[_editing.value.key] = next
        if (_editing.value.key == _output.value.key) _active.value = next
        prefs.edit().putString("profiles", json.encodeToString(mapSer, profiles)).apply()
    }
}

object OutputDetector {
    fun current(audio: AudioManager): AudioOutput {
        val devices = audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
        val bt = devices.firstOrNull {
            it.type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP || (android.os.Build.VERSION.SDK_INT >= 31 && it.type == AudioDeviceInfo.TYPE_BLE_HEADSET)
        }
        if (bt != null) return bluetooth(bt.productName?.toString().orEmpty())
        if (devices.any { it.type == AudioDeviceInfo.TYPE_WIRED_HEADPHONES || it.type == AudioDeviceInfo.TYPE_WIRED_HEADSET || it.type == AudioDeviceInfo.TYPE_USB_HEADSET }) {
            return AudioOutput("wired", "Wired headphones", "wired")
        }
        return AudioOutput("speaker", "Phone speaker", "speaker")
    }

    fun bluetooth(name: String) = AudioOutput("bt:${name.ifBlank { "Bluetooth" }}", name.ifBlank { "Bluetooth" }, "bluetooth")

    fun fromKey(key: String): AudioOutput = when {
        key.startsWith("bt:") -> bluetooth(key.removePrefix("bt:"))
        key == "wired" -> AudioOutput("wired", "Wired headphones", "wired")
        else -> AudioOutput("speaker", "Phone speaker", "speaker")
    }

    /** Value of the context `output` field (docs/v2.md §10). */
    fun contextLabel(o: AudioOutput): String = when (o.kind) {
        "bluetooth" -> "bluetooth:${o.label}"
        else -> o.kind
    }
}
