package org.nasmusic.app.ui

import kotlinx.serialization.encodeToString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.VoiceDevicePrefs
import org.nasmusic.app.data.api.VoiceDevices

class VoiceUiTest {
    @Test
    fun aliasesAreTrimmedAndBlanksDropped() {
        assertEquals(listOf("bedroom", "Stacey's room"), parseAliases(" bedroom ,  Stacey's   room , ,"))
        assertEquals(emptyList<String>(), parseAliases("  "))
    }

    @Test
    fun prefsOnlySendWhatChanged() {
        val s = ApiFactory.json.encodeToString(VoiceDevicePrefs(isDefault = true))
        assertEquals("{\"isDefault\":true}", s)
        assertFalse(ApiFactory.json.encodeToString(VoiceDevicePrefs(aliases = listOf("x"))).contains("isDefault"))
    }

    @Test
    fun devicesDecodeWithUnknownKinds() {
        val d = ApiFactory.json.decodeFromString<VoiceDevices>(
            """{"devices":[{"id":"spk:homeassistant:media_player.k","name":"Kitchen","kind":"speaker","castType":"homeassistant","online":false,"aliases":["kitchen"],"isDefault":true,"future":1}]}""",
        )
        assertEquals("Kitchen", d.devices.single().name)
        assertEquals(listOf("kitchen"), d.devices.single().aliases)
    }
}
