package org.nasmusic.app.pairing

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class QrPayloadTest {
    private val fp = (1..32).joinToString(":") { "AB" }

    @Test fun `parses the documented payload`() {
        val p = QrPayload.parse(
            """{"v":1,"name":"Home NAS","serverId":"s1","lanUrl":"https://192.168.1.50:8443","tunnelUrl":null,
               "certSha256":"$fp","pairCode":"ABCD1234","cfAccessClientId":null,"cfAccessClientSecret":null}""",
        ).getOrThrow()
        assertEquals("https://192.168.1.50:8443", p.lanUrl)
        assertNull(p.tunnelUrl)
        assertEquals("ABCD1234", p.pairCode)
    }

    @Test fun `rejects other QR codes and bad pins`() {
        assertTrue(QrPayload.parse("https://example.com").isFailure)
        assertTrue(QrPayload.parse("""{"v":1,"lanUrl":"https://x","certSha256":"AB","pairCode":"x"}""").isFailure)
        assertTrue(QrPayload.parse("""{"v":2,"lanUrl":"https://x","certSha256":"$fp","pairCode":"x"}""").isFailure)
        assertTrue(QrPayload.parse("""{"v":1,"lanUrl":"http://x","certSha256":"$fp","pairCode":"x"}""").isFailure)
    }
}
