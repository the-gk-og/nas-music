package org.homebeat.app.net

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/** Parses what people type into the server address field. */
object ServerAddress {
    const val DEFAULT_LAN_PORT = 8443

    /**
     * "192.168.68.2" → https://192.168.68.2:8443, "192.168.68.2:8443" → https://192.168.68.2:8443,
     * "music.example.com" → https://music.example.com, trailing "/" or "/api/v1" removed.
     * Plain http is refused (tokens must not travel in clear text).
     */
    fun normalize(input: String): Result<HttpUrl> = runCatching {
        var t = input.trim().trimEnd('/')
        require(t.isNotEmpty()) { "Enter the server address" }
        require(!t.startsWith("http://", ignoreCase = true)) { "Use https:// (the server only accepts HTTPS)" }
        if (!t.startsWith("https://", ignoreCase = true)) t = "https://$t"
        t = t.removeSuffix("/api/v1").trimEnd('/')
        var url = t.toHttpUrlOrNull() ?: throw IllegalArgumentException("Not a valid address")
        val explicitPort = Regex("^https://[^/]+:\\d+", RegexOption.IGNORE_CASE).containsMatchIn(t)
        if (!explicitPort && isLanHost(url.host)) url = url.newBuilder().port(DEFAULT_LAN_PORT).build()
        url
    }

    /** Private IPv4 ranges, IPv6 ULA/link-local, *.local and *.lan names. */
    fun isLanHost(host: String): Boolean {
        val h = host.lowercase()
        if (h.endsWith(".local") || h.endsWith(".lan") || h == "localhost") return true
        val parts = h.split('.').mapNotNull { it.toIntOrNull() }
        if (parts.size == 4 && h.count { it == '.' } == 3) {
            return parts[0] == 10 || parts[0] == 127 ||
                (parts[0] == 172 && parts[1] in 16..31) ||
                (parts[0] == 192 && parts[1] == 168) ||
                (parts[0] == 169 && parts[1] == 254) ||
                (parts[0] == 100 && parts[1] in 64..127) // CGNAT / Tailscale
        }
        return h.startsWith("fd") || h.startsWith("fe80") // IPv6 literals arrive without brackets
    }

    /** Origin string without trailing slash, as stored in [ServerConfig]. */
    fun origin(url: HttpUrl): String = url.toString().trimEnd('/')
}
