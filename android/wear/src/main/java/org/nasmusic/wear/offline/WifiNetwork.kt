package org.nasmusic.wear.offline

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import java.net.InetAddress
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.Dns
import okhttp3.OkHttpClient

/**
 * Wear OS sends traffic over the phone's Bluetooth proxy by default, which is slow and drains both
 * batteries. For big downloads an app has to ask for Wi-Fi explicitly (the system then turns the
 * radio on) and bind its sockets to that network. Release the lease as soon as the transfer is done.
 */
class WifiLease(private val cm: ConnectivityManager, private val cb: ConnectivityManager.NetworkCallback, val network: Network) :
    AutoCloseable {

    /** [base] with every socket and DNS lookup going over this Wi-Fi network. */
    fun bind(base: OkHttpClient): OkHttpClient = base.newBuilder()
        .socketFactory(network.socketFactory)
        .dns(object : Dns {
            override fun lookup(hostname: String): List<InetAddress> = network.getAllByName(hostname).toList()
        })
        .build()

    override fun close() { runCatching { cm.unregisterNetworkCallback(cb) } }

    companion object {
        /** Asks for an unmetered Wi-Fi network; null if none came up within [timeoutMs]. */
        suspend fun acquire(context: Context, timeoutMs: Long = 30_000): WifiLease? {
            val cm = context.getSystemService(ConnectivityManager::class.java)
            val req = NetworkRequest.Builder()
                .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
                .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                .addCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
                .build()
            var callback: ConnectivityManager.NetworkCallback? = null
            val network = withTimeoutOrNull(timeoutMs) {
                suspendCancellableCoroutine<Network> { cont ->
                    val cb = object : ConnectivityManager.NetworkCallback() {
                        override fun onAvailable(network: Network) {
                            if (cont.isActive) cont.resumeWith(Result.success(network))
                        }
                    }
                    callback = cb
                    cm.requestNetwork(req, cb)
                }
            }
            val cb = callback ?: return null
            if (network == null) {
                runCatching { cm.unregisterNetworkCallback(cb) }
                return null
            }
            return WifiLease(cm, cb, network)
        }
    }
}
