package org.homebeat.app.eq

import org.junit.Assert.assertEquals
import org.junit.Test

class EqProfilesTest {
    private val bass = EqSettings(enabled = true, preset = "Bass")
    private val car = EqSettings(enabled = true, preset = "Rock")
    private val one = EqSettings(enabled = true, preset = "Vocal")
    private val profiles = mapOf("wired" to bass, "bt:Toyota" to car, "all" to one)

    @Test fun `per device picks each output's own profile`() {
        assertEquals(car, EqProfiles.lookup(profiles, AudioOutput("bt:Toyota", "Toyota", "bluetooth"), true))
        assertEquals(bass, EqProfiles.lookup(profiles, AudioOutput("wired", "Wired headphones", "wired"), true))
        assertEquals(EqSettings(), EqProfiles.lookup(profiles, AudioOutput("speaker", "Phone speaker", "speaker"), true))
        // A USB DAC with no profile of its own keeps using the wired one (it used to share it).
        assertEquals(bass, EqProfiles.lookup(profiles, AudioOutput("usb:FiiO", "FiiO", "usb"), true))
        assertEquals(car, EqProfiles.lookup(profiles + ("usb:FiiO" to car), AudioOutput("usb:FiiO", "FiiO", "usb"), true))
    }

    @Test fun `off uses one profile for everything`() {
        assertEquals(one, EqProfiles.lookup(profiles, AudioOutput("bt:Toyota", "Toyota", "bluetooth"), false))
        assertEquals(one, EqProfiles.lookup(profiles, AudioOutput("speaker", "Phone speaker", "speaker"), false))
        assertEquals(EqProfiles.ALL, EqProfiles.profileOutput(AudioOutput("wired", "W", "wired"), false))
        assertEquals(EqSettings(), EqProfiles.lookup(emptyMap(), AudioOutput("wired", "W", "wired"), false))
    }
}
