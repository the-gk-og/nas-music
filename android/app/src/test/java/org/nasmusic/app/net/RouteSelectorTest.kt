package org.nasmusic.app.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RouteSelectorTest {
    @Test fun `no network is offline even with a tunnel`() {
        assertEquals(Route.OFFLINE, RouteSelector.select(false, ProbeResult.OK, hasTunnel = true))
    }

    @Test fun `reachable LAN wins over tunnel`() {
        assertEquals(Route.LAN, RouteSelector.select(true, ProbeResult.OK, hasTunnel = true))
    }

    @Test fun `unreachable LAN falls back to tunnel`() {
        assertEquals(Route.TUNNEL, RouteSelector.select(true, ProbeResult.UNREACHABLE, hasTunnel = true))
    }

    @Test fun `unreachable LAN without tunnel is offline`() {
        assertEquals(Route.OFFLINE, RouteSelector.select(true, ProbeResult.UNREACHABLE, hasTunnel = false))
    }

    @Test fun `a different server on the LAN address is not our LAN`() {
        assertEquals(Route.TUNNEL, RouteSelector.select(true, ProbeResult.WRONG_SERVER, hasTunnel = true))
    }

    @Test fun `health classification`() {
        assertEquals(ProbeResult.OK, RouteSelector.classifyHealth("abc", "abc", "ok"))
        assertEquals(ProbeResult.WRONG_SERVER, RouteSelector.classifyHealth("abc", "xyz", "ok"))
        assertEquals(ProbeResult.OK, RouteSelector.classifyHealth("", "xyz", "ok")) // id unknown (manual pairing)
        assertEquals(ProbeResult.UNREACHABLE, RouteSelector.classifyHealth("abc", null, null))
        assertEquals(ProbeResult.UNREACHABLE, RouteSelector.classifyHealth("abc", "abc", "starting"))
    }

    @Test fun `sync is LAN only unless tunnel sync is allowed`() {
        assertTrue(RouteSelector.syncAllowed(Route.LAN, allowTunnelSync = false))
        assertFalse(RouteSelector.syncAllowed(Route.TUNNEL, allowTunnelSync = false))
        assertTrue(RouteSelector.syncAllowed(Route.TUNNEL, allowTunnelSync = true))
        assertFalse(RouteSelector.syncAllowed(Route.OFFLINE, allowTunnelSync = true))
    }
}
