package org.nasmusic.app.net

import java.security.KeyStore
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.ServerInfo

/** What we learned from one unauthenticated look at a server address. */
data class ProbeOutcome(
    val url: HttpUrl,
    /** SHA-256 of the leaf certificate, colon hex. */
    val fingerprint: String,
    /** True when the cert chain is publicly trusted and matches the host name (e.g. the tunnel). */
    val caTrusted: Boolean,
    val info: ServerInfo,
)

enum class TrustDecision { CA_TRUSTED, KNOWN_PIN, ASK_USER, PIN_CHANGED }

/**
 * Connects once to `GET /api/v1/server-info` accepting any certificate, records the leaf cert and
 * whether the system would have trusted it. Nothing secret is sent on this connection; the
 * fingerprint is then either trusted automatically or shown to the user (trust on first use).
 */
class CertProbe(private val base: OkHttpClient) {

    suspend fun probe(url: HttpUrl): ProbeOutcome = withContext(Dispatchers.IO) {
        val system = systemTrustManager()
        var leaf: X509Certificate? = null
        var chainTrusted = false
        var hostOk = false
        val capturing = object : X509TrustManager {
            override fun checkServerTrusted(chain: Array<out X509Certificate>, authType: String) {
                leaf = chain.firstOrNull()
                chainTrusted = runCatching { system.checkServerTrusted(chain, authType) }.isSuccess
            }
            override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) =
                throw CertificateException("unsupported")
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        }
        val ssl = SSLContext.getInstance("TLS").apply { init(null, arrayOf(capturing), null) }
        val client = base.newBuilder()
            .sslSocketFactory(ssl.socketFactory, capturing)
            .hostnameVerifier { host, session ->
                hostOk = HttpsURLConnection.getDefaultHostnameVerifier().verify(host, session)
                true
            }
            .callTimeout(10, TimeUnit.SECONDS)
            .build()
        val target = url.newBuilder().encodedPath(url.encodedPath.trimEnd('/') + "/api/v1/server-info").build()
        val info = client.newCall(Request.Builder().url(target).build()).execute().use { resp ->
            if (!resp.isSuccessful) throw java.io.IOException("This doesn't look like a nas-music server (HTTP ${resp.code})")
            ApiFactory.json.decodeFromString(ServerInfo.serializer(), resp.body?.string().orEmpty())
        }
        val cert = leaf ?: throw java.io.IOException("No certificate presented")
        ProbeOutcome(
            url = url,
            fingerprint = PinnedCertTrustManager.formatFingerprint(PinnedCertTrustManager.sha256(cert.encoded)),
            caTrusted = chainTrusted && hostOk,
            info = info,
        )
    }

    companion object {
        fun systemTrustManager(): X509TrustManager {
            val f = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
            f.init(null as KeyStore?)
            return f.trustManagers.filterIsInstance<X509TrustManager>().first()
        }

        /**
         * Trust-on-first-use rule (docs/v2.md §6): a publicly trusted cert needs no pin; a self-signed
         * one is accepted silently only if it matches a pin learned earlier (via /server-info over the
         * tunnel, or a previous login); otherwise the user decides.
         */
        fun decide(outcome: ProbeOutcome, knownPin: String?): TrustDecision {
            if (outcome.caTrusted) return TrustDecision.CA_TRUSTED
            if (knownPin.isNullOrBlank()) return TrustDecision.ASK_USER
            return if (same(knownPin, outcome.fingerprint)) TrustDecision.KNOWN_PIN else TrustDecision.PIN_CHANGED
        }

        fun same(a: String, b: String): Boolean = runCatching {
            PinnedCertTrustManager.parseFingerprint(a).contentEquals(PinnedCertTrustManager.parseFingerprint(b))
        }.getOrDefault(false)
    }
}
