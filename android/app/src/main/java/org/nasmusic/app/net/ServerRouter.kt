package org.nasmusic.app.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import java.io.IOException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Request
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.Health

/**
 * Holds the LAN and tunnel addresses (via [clients]) and decides which one is active.
 * Re-probes `GET /api/v1/health` on the LAN address (1 s timeout) on every network change.
 */
class ServerRouter(
    context: Context,
    private val config: StateFlow<ServerConfig?>,
    private val clients: () -> HttpClients?,
    private val scope: CoroutineScope,
) {
    private val cm = context.getSystemService(ConnectivityManager::class.java)
    private val _route = MutableStateFlow(Route.OFFLINE)
    val route: StateFlow<Route> = _route.asStateFlow()

    private val _lanProbe = MutableStateFlow(ProbeResult.UNREACHABLE)
    val lanProbe: StateFlow<ProbeResult> = _lanProbe.asStateFlow()

    private val mutex = Mutex()
    private val kicks = Channel<Unit>(Channel.CONFLATED)

    private val _unmetered = MutableStateFlow(true)
    /** Whether the active network is unmetered (Wi-Fi/ethernet); refreshed with the route. */
    val unmetered: StateFlow<Boolean> = _unmetered.asStateFlow()

    val isOnline: Boolean get() = _route.value != Route.OFFLINE

    fun start() {
        cm.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) { kicks.trySend(Unit) }
            override fun onLost(network: Network) { kicks.trySend(Unit) }
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) { kicks.trySend(Unit) }
        })
        scope.launch { for (k in kicks) refresh() }
        scope.launch {
            config.map { it?.copy(deviceToken = null) }.distinctUntilChanged().collect { kicks.trySend(Unit) }
        }
    }

    /** Probe now and return the resulting route. */
    suspend fun refresh(): Route = mutex.withLock {
        val cfg = config.value
        val networkAvailable = networkAvailable()
        val probe = if (cfg != null && networkAvailable) probeLan(cfg) else ProbeResult.UNREACHABLE
        _lanProbe.value = probe
        _unmetered.value = isUnmetered()
        val r = RouteSelector.select(networkAvailable && cfg != null, probe, cfg?.hasTunnel == true)
        _route.value = r
        r
    }

    fun isUnmetered(): Boolean {
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
    }

    private fun networkAvailable(): Boolean {
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    private suspend fun probeLan(cfg: ServerConfig): ProbeResult = withContext(Dispatchers.IO) {
        val c = clients() ?: return@withContext ProbeResult.UNREACHABLE
        val base = cfg.lanHttpUrl ?: return@withContext ProbeResult.UNREACHABLE
        val url = RoutedCallFactory.rewrite(RoutedCallFactory.virtual("health").toHttpUrl(), base)
        try {
            c.lanProbe.newCall(Request.Builder().url(url).build()).execute().use { resp ->
                if (!resp.isSuccessful) return@use ProbeResult.UNREACHABLE
                val h = ApiFactory.json.decodeFromString(Health.serializer(), resp.body?.string().orEmpty())
                RouteSelector.classifyHealth(cfg.serverId, h.serverId, h.status)
            }
        } catch (e: IOException) {
            ProbeResult.UNREACHABLE
        } catch (e: IllegalArgumentException) {
            ProbeResult.UNREACHABLE
        }
    }
}
