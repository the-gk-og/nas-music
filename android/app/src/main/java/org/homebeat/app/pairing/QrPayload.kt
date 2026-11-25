package org.homebeat.app.pairing

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import org.homebeat.app.net.PinnedCertTrustManager

/** The JSON in the pairing QR (docs/pairing.md). */
@Serializable
data class QrPayload(
    val v: Int,
    val name: String = "NAS",
    val serverId: String = "",
    val lanUrl: String,
    val tunnelUrl: String? = null,
    val certSha256: String,
    val pairCode: String,
    val cfAccessClientId: String? = null,
    val cfAccessClientSecret: String? = null,
) {
    companion object {
        private val json = Json { ignoreUnknownKeys = true }

        fun parse(text: String): Result<QrPayload> = runCatching {
            val p = json.decodeFromString(serializer(), text.trim())
            require(p.v == 1) { "Unsupported pairing QR version ${p.v}" }
            require(p.lanUrl.startsWith("https://")) { "lanUrl must be https" }
            require(p.tunnelUrl.isNullOrBlank() || p.tunnelUrl.startsWith("https://")) { "tunnelUrl must be https" }
            PinnedCertTrustManager.parseFingerprint(p.certSha256)
            require(p.pairCode.isNotBlank()) { "Missing pairCode" }
            p
        }
    }
}
