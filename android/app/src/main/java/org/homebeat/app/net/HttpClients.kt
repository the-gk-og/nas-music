package org.homebeat.app.net

import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import okhttp3.Interceptor
import okhttp3.OkHttpClient

/**
 * Builds one OkHttp client per route.
 *
 * LAN: trusts only the pinned self-signed certificate, and only for the LAN host.
 * Tunnel: normal public-CA validation plus Cloudflare Access service-token headers.
 * Both: `Authorization: Bearer <deviceToken>` when paired.
 */
class HttpClients(
    private val base: OkHttpClient,
    val config: ServerConfig,
) {
    private val auth = Interceptor { chain ->
        val token = config.deviceToken
        val req = if (token.isNullOrBlank()) chain.request()
        else chain.request().newBuilder().header("Authorization", "Bearer $token").build()
        chain.proceed(req)
    }

    val lan: OkHttpClient by lazy {
        if (!config.hasLanPin) return@lazy base.newBuilder().addInterceptor(auth).build()
        val tm = PinnedCertTrustManager(config.certSha256)
        val ssl = SSLContext.getInstance("TLS").apply { init(null, arrayOf(tm), null) }
        val lanHost = config.lanHttpUrl?.host
        base.newBuilder()
            .sslSocketFactory(ssl.socketFactory, tm)
            // The cert is pinned byte-for-byte, so the name in it does not matter, but we only
            // ever accept it for the configured LAN host.
            .hostnameVerifier { hostname, _ -> lanHost != null && hostname.equals(lanHost, ignoreCase = true) }
            .addInterceptor(auth)
            .build()
    }

    /** LAN client with the 1 s timeout used for reachability probes. */
    val lanProbe: OkHttpClient by lazy {
        lan.newBuilder()
            .callTimeout(1, TimeUnit.SECONDS)
            .connectTimeout(1, TimeUnit.SECONDS)
            .build()
    }

    val tunnel: OkHttpClient by lazy {
        val id = config.cfAccessClientId
        val secret = config.cfAccessClientSecret
        base.newBuilder()
            .addInterceptor { chain ->
                val b = chain.request().newBuilder()
                if (!id.isNullOrBlank() && !secret.isNullOrBlank()) {
                    b.header("CF-Access-Client-Id", id)
                    b.header("CF-Access-Client-Secret", secret)
                }
                chain.proceed(b.build())
            }
            .addInterceptor(auth)
            .build()
    }

    fun forRoute(route: Route): OkHttpClient = when (route) {
        Route.LAN -> lan
        Route.TUNNEL, Route.OFFLINE -> if (config.hasTunnel) tunnel else lan
    }

    companion object {
        fun baseClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .build()
    }
}
