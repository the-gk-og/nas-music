package org.homebeat.app.net

import java.io.IOException
import okhttp3.Call
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * A single [Call.Factory] shared by Retrofit, Coil and ExoPlayer.
 *
 * Everyone builds URLs against the virtual base [VIRTUAL_BASE]. At call time this factory swaps in
 * the active route's real base URL and dispatches to that route's OkHttp client (with its TLS and
 * header setup). So a queue built at home keeps working when the phone walks out the door.
 *
 * Requests to any other host (e.g. Discover preview URLs) go through [plain].
 */
class RoutedCallFactory(
    private val clients: () -> HttpClients?,
    private val route: () -> Route,
    private val plain: OkHttpClient,
    private val forcedRoute: Route? = null,
) : Call.Factory {

    override fun newCall(request: Request): Call {
        if (request.url.host != VIRTUAL_HOST) return plain.newCall(request)
        val c = clients() ?: return plain.newCall(request.newBuilder().url(UNPAIRED_URL).build())
        val r = forcedRoute ?: route()
        val effective = when {
            r == Route.OFFLINE && c.config.hasTunnel -> Route.TUNNEL
            r == Route.OFFLINE -> Route.LAN
            else -> r
        }
        val base = c.config.baseFor(effective) ?: c.config.lanHttpUrl
            ?: throw IllegalStateException("No server address configured")
        return c.forRoute(effective).newCall(request.newBuilder().url(rewrite(request.url, base)).build())
    }

    /** A copy of this factory that always uses [route] (e.g. LAN-only night sync). */
    fun pinnedTo(route: Route) = RoutedCallFactory(clients, { route }, plain, route)

    companion object {
        const val VIRTUAL_HOST = "nas.invalid"
        const val VIRTUAL_BASE = "http://$VIRTUAL_HOST/api/v1/"
        private val UNPAIRED_URL = "http://unpaired.invalid/".toHttpUrl()

        fun virtual(path: String): String = VIRTUAL_BASE + path.trimStart('/')

        /** Replace scheme/host/port of [url] with [base], keeping [base]'s path prefix (if any). */
        fun rewrite(url: HttpUrl, base: HttpUrl): HttpUrl {
            val prefix = base.encodedPath.trimEnd('/')
            return url.newBuilder()
                .scheme(base.scheme)
                .host(base.host)
                .port(base.port)
                .encodedPath(prefix + url.encodedPath)
                .build()
        }
    }
}

class NotPairedException : IOException("Not paired with a server")
