package org.nasmusic.app.pairing

import android.os.Build
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.nasmusic.app.data.api.PairRequest
import org.nasmusic.app.net.HttpClients
import org.nasmusic.app.net.Route
import org.nasmusic.app.net.RoutedCallFactory
import org.nasmusic.app.net.ServerConfig
import org.nasmusic.app.data.api.ApiFactory
import okhttp3.OkHttpClient
import retrofit2.HttpException

class PairingRepository(
    private val store: SecureStore,
    private val baseClient: OkHttpClient,
) {
    /** Pairs over LAN first (pinned cert), falling back to the tunnel. Saves the config on success. */
    suspend fun pair(payload: QrPayload): ServerConfig = withContext(Dispatchers.IO) {
        val draft = ServerConfig(
            name = payload.name,
            serverId = payload.serverId,
            lanUrl = payload.lanUrl.trimEnd('/'),
            tunnelUrl = payload.tunnelUrl?.trimEnd('/')?.takeIf { it.isNotBlank() },
            certSha256 = payload.certSha256,
            cfAccessClientId = payload.cfAccessClientId?.takeIf { it.isNotBlank() },
            cfAccessClientSecret = payload.cfAccessClientSecret?.takeIf { it.isNotBlank() },
        )
        val clients = HttpClients(baseClient, draft)
        val routes = buildList { add(Route.LAN); if (draft.hasTunnel) add(Route.TUNNEL) }
        var lastError: Throwable? = null
        for (route in routes) {
            val factory = RoutedCallFactory({ clients }, { route }, baseClient, forcedRoute = route)
            val api = ApiFactory.create(factory)
            try {
                val health = runCatching { api.health() }.getOrNull()
                val resp = api.pair(PairRequest(payload.pairCode.trim(), deviceName()))
                val saved = draft.copy(
                    serverId = draft.serverId.ifBlank { health?.serverId.orEmpty() },
                    deviceId = resp.deviceId,
                    deviceToken = resp.deviceToken,
                )
                store.save(saved)
                return@withContext saved
            } catch (e: HttpException) {
                if (e.code() == 403) throw PairingException("Pairing code is invalid or expired (codes last 10 minutes).")
                lastError = e
            } catch (e: Exception) {
                lastError = e
            }
        }
        throw PairingException("Could not reach the server: ${lastError?.message ?: "unknown error"}", lastError)
    }

    fun unpair() = store.save(null)

    /** Keeps the token but updates addresses / CF credentials from the Settings screen. */
    fun updateAddresses(lanUrl: String, tunnelUrl: String?, cfId: String?, cfSecret: String?) {
        val cur = store.config.value ?: return
        store.save(
            cur.copy(
                lanUrl = lanUrl.trimEnd('/'),
                tunnelUrl = tunnelUrl?.trimEnd('/')?.takeIf { it.isNotBlank() },
                cfAccessClientId = cfId?.takeIf { it.isNotBlank() },
                cfAccessClientSecret = cfSecret?.takeIf { it.isNotBlank() },
            ),
        )
    }

    private fun deviceName(): String =
        listOfNotNull(Build.MANUFACTURER, Build.MODEL).joinToString(" ").ifBlank { "Android" }
}

class PairingException(message: String, cause: Throwable? = null) : Exception(message, cause)
