package org.homebeat.app.eq

/** An audio output the EQ keeps a profile for. */
data class AudioOutput(val key: String, val label: String, val kind: String)

/** Which saved profile an output uses (pure, for tests). */
object EqProfiles {
    /** The single profile used when per-device EQ is off. */
    val ALL = AudioOutput("all", "All outputs", "all")

    fun profileOutput(o: AudioOutput, perDevice: Boolean) = if (perDevice) o else ALL

    /** The output's own profile; a USB DAC without one borrows the wired profile (they shared it before). */
    fun lookup(profiles: Map<String, EqSettings>, o: AudioOutput, perDevice: Boolean): EqSettings {
        if (!perDevice) return profiles[ALL.key] ?: EqSettings()
        return profiles[o.key] ?: (if (o.kind == "usb") profiles["wired"] else null) ?: EqSettings()
    }
}
