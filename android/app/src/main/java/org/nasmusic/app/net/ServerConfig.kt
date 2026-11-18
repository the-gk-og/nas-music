package org.nasmusic.app.net

import kotlinx.serialization.Serializable
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/** Everything the app knows about the paired server. Stored encrypted (see [org.nasmusic.app.pairing.SecureStore]). */
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
) {
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
