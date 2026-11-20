package org.nasmusic.app.cast

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.nasmusic.app.connect.ConnectMessage
import org.nasmusic.app.connect.ConnectProtocol

/** Server-side speakers arrive as Connect devices (kind "speaker", castType) - api/openapi.yaml ConnectDevice. */
class SpeakerDevicesTest {
    @Test fun `speaker devices parse with cast type`() {
        val m = ConnectProtocol.parse(
            """{"type":"devices","devices":[
                {"id":"d1","name":"Pixel","kind":"android","canPlay":true,"online":true,"isActive":true},
                {"id":"spk:chromecast:abc","name":"Kitchen","kind":"speaker","canPlay":true,"online":true,
                 "isActive":false,"castType":"chromecast","futureField":1}]}""",
        ) as ConnectMessage.Devices
        val (phone, spk) = m.devices
        assertFalse(phone.isSpeaker)
        assertNull(phone.castType)
        assertTrue(spk.isSpeaker)
        assertEquals("chromecast", spk.castType)
    }

    @Test fun `errors keep their source`() {
        val e = ConnectProtocol.parse("""{"type":"error","detail":"Kitchen: no","source":"speaker"}""") as ConnectMessage.Error
        assertEquals("speaker", e.source)
        val plain = ConnectProtocol.parse("""{"type":"error","detail":"only the active device reports state"}""") as ConnectMessage.Error
        assertNull(plain.source)
    }

    @Test fun `connecting state parses and labels the remote`() {
        val m = ConnectProtocol.parse(
            """{"type":"state","state":{"activeDeviceId":"spk:chromecast:abc","playing":false,"connecting":true,"rev":3}}""",
        ) as ConnectMessage.State
        assertTrue(m.state.connecting)
        assertFalse(m.state.playing)
        val r = org.nasmusic.app.playback.RemoteInfo("spk:chromecast:abc", "Kitchen", isSpeaker = true, connecting = true)
        assertEquals("Connecting to Kitchen…", r.label)
        assertEquals("Playing on Kitchen", r.copy(connecting = false).label)
        // Old servers don't send it.
        val old = ConnectProtocol.parse("""{"type":"state","state":{"activeDeviceId":"x","rev":1}}""") as ConnectMessage.State
        assertFalse(old.state.connecting)
    }
}
