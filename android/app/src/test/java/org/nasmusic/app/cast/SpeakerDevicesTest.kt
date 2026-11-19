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
}
