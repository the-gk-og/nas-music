package org.homebeat.app.eq

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

/**
 * EQ profiles per audio output (speaker, wired, each Bluetooth device and USB DAC by name), persisted locally
 * (the EQ is per device, never synced). Follows output changes via AudioDeviceCallback. With [perDevice] off,
 * one "All outputs" profile is used everywhere.
 */
class EqRepository(context: Context) {
    private val prefs = context.getSharedPreferences("eq_profiles", Context.MODE_PRIVATE)
    private val audio = context.getSystemService(AudioManager::class.java)
    private val json = Json { ignoreUnknownKeys = true }
    private val mapSer = MapSerializer(String.serializer(), EqSettings.serializer())

    private val profiles: MutableMap<String, EqSettings> =
        runCatching { json.decodeFromString(mapSer, prefs.getString("profiles", null) ?: "{}") }.getOrDefault(emptyMap()).toMutableMap()

    private val _perDevice = MutableStateFlow(prefs.getBoolean("per_device", true))
    /** On: each output keeps its own EQ and it switches as you plug in / connect. Off: one EQ for everything. */
    val perDevice: StateFlow<Boolean> = _perDevice.asStateFlow()

    private val _output = MutableStateFlow(OutputDetector.current(audio))
    /** The physical output playing right now. */
    val output: StateFlow<AudioOutput> = _output.asStateFlow()

    /** Profile being edited in the UI (defaults to the current output's). */
    private val _editing = MutableStateFlow(EqProfiles.profileOutput(_output.value, _perDevice.value))
    val editing: StateFlow<AudioOutput> = _editing.asStateFlow()

    private val _active = MutableStateFlow(EqProfiles.lookup(profiles, _output.value, _perDevice.value))
    /** Settings applied to the audio right now (current output's profile). Read on the audio thread. */
    val active: StateFlow<EqSettings> = _active.asStateFlow()

    /** Karaoke: vocals turned down (KaraokeProcessor). Read on the audio thread. Not saved: easy to forget it's on. */
    val karaoke = MutableStateFlow(false)

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
        val followEditing = _editing.value.key == EqProfiles.profileOutput(_output.value, _perDevice.value).key
        _output.value = o
        _active.value = EqProfiles.lookup(profiles, o, _perDevice.value)
        if (followEditing) select(EqProfiles.profileOutput(o, _perDevice.value))
    }

    fun setPerDevice(on: Boolean) {
        if (on == _perDevice.value) return
        // Turning it off: the single EQ starts as whatever is playing now, so the sound doesn't jump.
        if (!on && EqProfiles.ALL.key !in profiles) {
            profiles[EqProfiles.ALL.key] = EqProfiles.lookup(profiles, _output.value, true)
            save()
        }
        _perDevice.value = on
        prefs.edit().putBoolean("per_device", on).apply()
        _active.value = EqProfiles.lookup(profiles, _output.value, on)
        select(EqProfiles.profileOutput(_output.value, on))
    }

    /** Outputs you can pick to edit: all with a saved profile plus the current one (just "All outputs" when per-device is off). */
    fun knownOutputs(): List<AudioOutput> {
        if (!_perDevice.value) return listOf(EqProfiles.ALL)
        val cur = _output.value
        val saved = profiles.keys.filter { it != EqProfiles.ALL.key }.map { OutputDetector.fromKey(it) }
        return (listOf(cur) + saved).distinctBy { it.key }
    }

    fun select(o: AudioOutput) {
        _editing.value = o
        _edited.value = EqProfiles.lookup(profiles, o, _perDevice.value)
    }

    fun update(transform: (EqSettings) -> EqSettings) {
        val next = transform(_edited.value)
        _edited.value = next
        profiles[_editing.value.key] = next
        if (_editing.value.key == EqProfiles.profileOutput(_output.value, _perDevice.value).key) _active.value = next
        save()
    }

    private fun save() = prefs.edit().putString("profiles", json.encodeToString(mapSer, profiles)).apply()
}

object OutputDetector {
    fun current(audio: AudioManager): AudioOutput {
        val devices = audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
        val bt = devices.firstOrNull {
            it.type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP || (android.os.Build.VERSION.SDK_INT >= 31 && it.type == AudioDeviceInfo.TYPE_BLE_HEADSET)
        }
        if (bt != null) return bluetooth(bt.productName?.toString().orEmpty())
        // USB DACs / dongles get their own profile, by name (one DAC sounds different from another).
        devices.firstOrNull { it.type == AudioDeviceInfo.TYPE_USB_HEADSET || it.type == AudioDeviceInfo.TYPE_USB_DEVICE }
            ?.let { return usb(it.productName?.toString().orEmpty()) }
        if (devices.any { it.type == AudioDeviceInfo.TYPE_WIRED_HEADPHONES || it.type == AudioDeviceInfo.TYPE_WIRED_HEADSET }) {
            return AudioOutput("wired", "Wired headphones", "wired")
        }
        return AudioOutput("speaker", "Phone speaker", "speaker")
    }

    fun bluetooth(name: String) = AudioOutput("bt:${name.ifBlank { "Bluetooth" }}", name.ifBlank { "Bluetooth" }, "bluetooth")

    fun usb(name: String) = AudioOutput("usb:${name.ifBlank { "USB audio" }}", name.ifBlank { "USB audio" }, "usb")

    fun fromKey(key: String): AudioOutput = when {
        key.startsWith("bt:") -> bluetooth(key.removePrefix("bt:"))
        key.startsWith("usb:") -> usb(key.removePrefix("usb:"))
        key == EqProfiles.ALL.key -> EqProfiles.ALL
        key == "wired" -> AudioOutput("wired", "Wired headphones", "wired")
        else -> AudioOutput("speaker", "Phone speaker", "speaker")
    }

    /** Value of the context `output` field (docs/v2.md §10). */
    fun contextLabel(o: AudioOutput): String = when (o.kind) {
        "bluetooth" -> "bluetooth:${o.label}"
        "usb" -> "wired" // the context contract knows speaker / wired / bluetooth
        else -> o.kind
    }
}
