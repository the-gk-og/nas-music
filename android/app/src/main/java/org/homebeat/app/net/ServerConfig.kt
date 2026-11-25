package org.homebeat.app.net

import kotlinx.serialization.Serializable
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/** Everything the app knows about the paired server. Stored encrypted (see [org.homebeat.app.pairing.SecureStore]). */
@Serializable
data class ServerConfig(
    val name: String = "NAS",
    val serverId: String = "",
    val lanUrl: String,
    val tunnelUrl: String? = null,
    val certSha256: String,
    val cfAccessClientId: String? = null,
    val cfAccessClientSecret: String? = null,
    val deviceId: String? = null,
    val deviceToken: String? = null,
    // v2 accounts
    val userId: String? = null,
    val username: String? = null,
    val isAdmin: Boolean = false,
) {
    /** False when the LAN address has a publicly trusted cert (or none is known yet): no pin, normal CA checks. */
    val hasLanPin: Boolean get() = certSha256.isNotBlank()
    val lanHttpUrl: HttpUrl? get() = lanUrl.toHttpUrlOrNull()
    val tunnelHttpUrl: HttpUrl? get() = tunnelUrl?.takeIf { it.isNotBlank() }?.toHttpUrlOrNull()
    val hasTunnel: Boolean get() = tunnelHttpUrl != null
    val isPaired: Boolean get() = !deviceToken.isNullOrBlank()

    fun baseFor(route: Route): HttpUrl? = when (route) {
        Route.LAN -> lanHttpUrl
        Route.TUNNEL -> tunnelHttpUrl
        Route.OFFLINE -> null
    }
}

enum class Route { LAN, TUNNEL, OFFLINE }
