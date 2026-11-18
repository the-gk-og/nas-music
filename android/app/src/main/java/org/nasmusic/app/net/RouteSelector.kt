package org.nasmusic.app.net

/**
 * Pure route decision, separated from Android so it can be unit-tested.
 *
 * - No network at all → OFFLINE.
 * - LAN health probe succeeded (and the server id matches the paired one) → LAN.
 * - Otherwise the tunnel if one is configured, else OFFLINE.
 */
object RouteSelector {
    fun select(networkAvailable: Boolean, lanProbe: ProbeResult, hasTunnel: Boolean): Route = when {
        !networkAvailable -> Route.OFFLINE
        lanProbe == ProbeResult.OK -> Route.LAN
        hasTunnel -> Route.TUNNEL
        else -> Route.OFFLINE
    }

    /** Classify a /health answer: a different server answering on the LAN address is not "our" LAN. */
    fun classifyHealth(expectedServerId: String, actualServerId: String?, status: String?): ProbeResult = when {
        actualServerId == null || status != "ok" -> ProbeResult.UNREACHABLE
        expectedServerId.isNotBlank() && expectedServerId != actualServerId -> ProbeResult.WRONG_SERVER
        else -> ProbeResult.OK
    }

    /** Whether the night sync may run on [route]. */
    fun syncAllowed(route: Route, allowTunnelSync: Boolean): Boolean = when (route) {
        Route.LAN -> true
        Route.TUNNEL -> allowTunnelSync
        Route.OFFLINE -> false
    }
}

enum class ProbeResult { OK, UNREACHABLE, WRONG_SERVER }
