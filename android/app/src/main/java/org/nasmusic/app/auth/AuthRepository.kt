package org.nasmusic.app.auth

import android.os.Build
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.LoginRequest
import org.nasmusic.app.data.api.NasMusicApi
import org.nasmusic.app.data.api.RegisterRequest
import org.nasmusic.app.data.api.Session
import org.nasmusic.app.net.HttpClients
import org.nasmusic.app.net.ProbeOutcome
import org.nasmusic.app.net.Route
import org.nasmusic.app.net.RoutedCallFactory
import org.nasmusic.app.net.ServerAddress
import org.nasmusic.app.net.ServerConfig
import org.nasmusic.app.pairing.SecureStore
import retrofit2.HttpException

/** v2 sign-in: server address → (trust) → username/password or invite code. */
class AuthRepository(
    private val store: SecureStore,
    private val baseClient: OkHttpClient,
) {
    /**
     * The config to talk to the server we probed, before any token exists. The entered address
     * becomes LAN (pinned, self-signed) or tunnel (CA-trusted); the other address comes from
     * /server-info.
     */
    fun draftConfig(p: ProbeOutcome): Pair<ServerConfig, Route> {
        val entered = ServerAddress.origin(p.url)
        return if (p.caTrusted) {
            ServerConfig(
                name = p.info.name, serverId = p.info.serverId,
                lanUrl = p.info.lanUrl?.trimEnd('/').orEmpty(),
                tunnelUrl = entered,
                certSha256 = p.info.certSha256.orEmpty(),
            ) to Route.TUNNEL
        } else {
            ServerConfig(
                name = p.info.name, serverId = p.info.serverId,
                lanUrl = entered,
                tunnelUrl = p.info.tunnelUrl?.trimEnd('/')?.takeIf { it.isNotBlank() },
                certSha256 = p.fingerprint,
            ) to Route.LAN
        }
    }

    suspend fun login(p: ProbeOutcome, username: String, password: String): ServerConfig =
        signIn(p) { api -> api.login(LoginRequest(username.trim(), password, deviceName())) }

    suspend fun register(p: ProbeOutcome, invite: String, username: String, password: String): ServerConfig {
        validateNewAccount(username, password)?.let { throw AuthException(it) }
        return signIn(p) { api -> api.register(RegisterRequest(invite.trim(), username.trim(), password, deviceName())) }
    }

    private suspend fun signIn(p: ProbeOutcome, call: suspend (NasMusicApi) -> Session): ServerConfig =
        withContext(Dispatchers.IO) {
            val (draft, route) = draftConfig(p)
            val clients = HttpClients(baseClient, draft)
            val api = ApiFactory.create(RoutedCallFactory({ clients }, { route }, baseClient, forcedRoute = route))
            val session = try {
                call(api)
            } catch (e: HttpException) {
                throw AuthException(messageFor(e.code()))
            }
            val cfg = draft.copy(
                deviceId = session.deviceId,
                deviceToken = session.deviceToken,
                userId = session.userId,
                username = session.username,
                isAdmin = session.isAdmin,
            )
            store.save(cfg)
            cfg
        }

    /**
     * Revokes the token on the server (best effort) and forgets it locally. Keeps the addresses and
     * the LAN pin so signing in again (or as someone else) doesn't ask to trust the cert again.
     */
    suspend fun logout(api: NasMusicApi) {
        runCatching { api.logout() }
        store.config.value?.let {
            store.save(it.copy(deviceId = null, deviceToken = null, userId = null, username = null, isAdmin = false))
        }
    }

    /** Pin learned earlier for this host (previous login, or /server-info over the tunnel). */
    fun knownPinFor(p: ProbeOutcome): String? {
        val cfg = store.config.value ?: return null
        val sameServer = cfg.serverId.isNotBlank() && cfg.serverId == p.info.serverId
        val sameHost = cfg.lanHttpUrl?.host == p.url.host
        return cfg.certSha256.takeIf { sameServer || sameHost }
    }

    /**
     * Keeps addresses current from /server-info. The LAN pin is only replaced with what the server
     * reports when that answer came over the CA-validated tunnel.
     */
    fun applyServerInfo(info: org.nasmusic.app.data.api.ServerInfo, via: Route) {
        val cur = store.config.value ?: return
        if (cur.serverId.isNotBlank() && cur.serverId != info.serverId) return
        val next = cur.copy(
            name = info.name,
            serverId = info.serverId,
            lanUrl = info.lanUrl?.trimEnd('/')?.takeIf { it.isNotBlank() } ?: cur.lanUrl,
            tunnelUrl = info.tunnelUrl?.trimEnd('/')?.takeIf { it.isNotBlank() } ?: cur.tunnelUrl,
            certSha256 = if (via == Route.TUNNEL && !info.certSha256.isNullOrBlank()) info.certSha256 else cur.certSha256,
        )
        if (next != cur) store.save(next)
    }

    companion object {
        fun deviceName(): String =
            listOfNotNull(Build.MANUFACTURER, Build.MODEL).joinToString(" ").ifBlank { "Android" }

        fun messageFor(code: Int): String = when (code) {
            401 -> "Wrong username or password."
            403 -> "That invite code is invalid, used or expired."
            409 -> "That username is taken."
            422, 400 -> "Check the username (3–32 letters, digits, _ . -) and password (8+ characters)."
            429 -> "Too many attempts. Wait a minute and try again."
            else -> "Sign-in failed (HTTP $code)."
        }

        private val USERNAME = Regex("^[A-Za-z0-9_.-]{3,32}$")

        /** Same rules as the server (docs/v2.md §4); null = ok. */
        fun validateNewAccount(username: String, password: String): String? = when {
            !USERNAME.matches(username.trim()) -> "Username: 3–32 characters, letters, digits, _ . -"
            password.length < 8 -> "Password: at least 8 characters"
            else -> null
        }
    }
}

class AuthException(message: String) : Exception(message)
