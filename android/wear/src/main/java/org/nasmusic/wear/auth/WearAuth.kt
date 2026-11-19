package org.nasmusic.wear.auth

import android.os.Build
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import okhttp3.OkHttpClient
import org.nasmusic.app.auth.AuthRepository
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.LoginRequest
import org.nasmusic.app.data.api.Session
import org.nasmusic.app.net.CertProbe
import org.nasmusic.app.net.HttpClients
import org.nasmusic.app.net.ProbeOutcome
import org.nasmusic.app.net.RoutedCallFactory
import org.nasmusic.app.net.ServerAddress
import org.nasmusic.app.net.ServerConfig
import org.nasmusic.app.net.TrustDecision
import org.nasmusic.app.pairing.SecureStore
import org.nasmusic.wear.BuildConfig
import retrofit2.HttpException
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import okhttp3.MediaType.Companion.toMediaType
import retrofit2.http.Body
import retrofit2.http.POST

/** POST /pair with deviceKind = watch (the shared PairRequest predates deviceKind). */
@Serializable
data class WatchPairRequest(
    val pairCode: String,
    val deviceName: String,
    val deviceKind: String = "watch",
    val appVersion: String? = null,
)

interface WatchAuthApi {
    @POST("pair")
    suspend fun pair(@Body body: WatchPairRequest): Session
}

/**
 * Sign-in for the watch. Same steps and trust rules as the phone (docs/v2.md §6):
 * address → probe `/server-info` → trust (public CA, known pin, or the user confirms the fingerprint)
 * → a one-time pair code from the phone/web ("Pair a device"), or username + password.
 * The resulting [ServerConfig] goes to the same Tink/Keystore-encrypted [SecureStore] as on the phone.
 */
class WearAuth(private val store: SecureStore, private val baseClient: OkHttpClient) {
    private val probe = CertProbe(baseClient)

    suspend fun probe(address: String): Pair<ProbeOutcome, TrustDecision> {
        val url = ServerAddress.normalize(address).getOrElse { throw AuthError(it.message ?: "Invalid address") }
        val outcome = try {
            probe.probe(url)
        } catch (e: Exception) {
            throw AuthError("Can't reach ${url.host}: ${e.message ?: e.javaClass.simpleName}")
        }
        val known = store.config.value?.takeIf {
            (it.serverId.isNotBlank() && it.serverId == outcome.info.serverId) || it.lanHttpUrl?.host == url.host
        }?.certSha256
        return outcome to CertProbe.decide(outcome, known)
    }

    suspend fun pair(p: ProbeOutcome, code: String): ServerConfig = signIn(p) { factory ->
        val api = Retrofit.Builder()
            .baseUrl(RoutedCallFactory.VIRTUAL_BASE)
            .callFactory(factory)
            .addConverterFactory(ApiFactory.json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(WatchAuthApi::class.java)
        try {
            api.pair(WatchPairRequest(normalizeCode(code), deviceName(), appVersion = BuildConfig.VERSION_NAME))
        } catch (e: HttpException) {
            throw AuthError(if (e.code() == 403) "Code is wrong or expired (codes last 10 minutes)." else "Pairing failed (HTTP ${e.code()}).")
        }
    }

    suspend fun login(p: ProbeOutcome, username: String, password: String): ServerConfig = signIn(p) { factory ->
        try {
            ApiFactory.create(factory).login(LoginRequest(username.trim(), password, deviceName(), deviceKind = "watch"))
        } catch (e: HttpException) {
            throw AuthError(AuthRepository.messageFor(e.code()))
        }
    }

    private suspend fun signIn(p: ProbeOutcome, call: suspend (RoutedCallFactory) -> Session): ServerConfig =
        withContext(Dispatchers.IO) {
            val (draft, route) = AuthRepository.draftConfig(p)
            val clients = HttpClients(baseClient, draft)
            val factory = RoutedCallFactory({ clients }, { route }, baseClient, forcedRoute = route)
            val session = try {
                call(factory)
            } catch (e: AuthError) {
                throw e
            } catch (e: Exception) {
                throw AuthError("Couldn't reach the server: ${e.message ?: e.javaClass.simpleName}")
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

    /** Forgets the token; keeps addresses and pin so signing in again is quicker. */
    fun signOut() {
        store.config.value?.let {
            store.save(it.copy(deviceId = null, deviceToken = null, userId = null, username = null, isAdmin = false))
        }
    }

    companion object {
        fun deviceName(): String =
            listOfNotNull(Build.MANUFACTURER, Build.MODEL).joinToString(" ").ifBlank { "Wear OS watch" }

        /** Codes are shown grouped ("ABCD-EFGH") and typed on a tiny keyboard: drop spaces and dashes. */
        fun normalizeCode(code: String): String = code.trim().replace(" ", "").replace("-", "").uppercase()

        /** First and last groups of a long fingerprint, enough to compare with the server dashboard. */
        fun shortFingerprint(fp: String): String {
            val parts = fp.split(':')
            return if (parts.size <= 8) fp else parts.take(4).joinToString(":") + " … " + parts.takeLast(4).joinToString(":")
        }
    }
}

class AuthError(message: String) : Exception(message)
