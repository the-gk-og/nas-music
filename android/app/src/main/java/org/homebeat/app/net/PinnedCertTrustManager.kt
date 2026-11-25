package org.homebeat.app.net

import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.X509TrustManager

/**
 * Accepts exactly one certificate: the server's self-signed LAN cert whose SHA-256 (of the DER
 * encoding) was delivered in the pairing QR. Used only by the LAN client.
 */
class PinnedCertTrustManager(fingerprint: String) : X509TrustManager {
    private val pin: ByteArray = parseFingerprint(fingerprint)

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        val leaf = chain?.firstOrNull() ?: throw CertificateException("Empty certificate chain")
        val actual = sha256(leaf.encoded)
        if (!MessageDigest.isEqual(actual, pin)) {
            throw CertificateException("LAN certificate does not match the pinned fingerprint")
        }
    }

    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        throw CertificateException("Client certificates are not supported")
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()

    companion object {
        fun sha256(bytes: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(bytes)

        /** Accepts "AB:CD:…", "abcd…" or with spaces. Throws on anything that is not 32 bytes of hex. */
        fun parseFingerprint(text: String): ByteArray {
            val hex = text.filterNot { it == ':' || it.isWhitespace() }.lowercase()
            require(hex.length == 64 && hex.all { it in '0'..'9' || it in 'a'..'f' }) {
                "certSha256 must be a SHA-256 fingerprint (64 hex digits)"
            }
            return ByteArray(32) { i -> hex.substring(i * 2, i * 2 + 2).toInt(16).toByte() }
        }

        fun formatFingerprint(bytes: ByteArray): String = bytes.joinToString(":") { "%02X".format(it) }
    }
}
