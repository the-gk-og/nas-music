package org.nasmusic.app

import android.content.Context
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import org.nasmusic.app.data.LibraryRepository
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.db.AppDatabase
import org.nasmusic.app.data.prefs.SettingsRepository
import org.nasmusic.app.feedback.EventRecorder
import org.nasmusic.app.net.HttpClients
import org.nasmusic.app.net.Route
import org.nasmusic.app.net.RoutedCallFactory
import org.nasmusic.app.net.ServerConfig
import org.nasmusic.app.net.ServerRouter
import org.nasmusic.app.pairing.PairingRepository
import org.nasmusic.app.pairing.SecureStore
import org.nasmusic.app.playback.PlayerConnection
import org.nasmusic.app.sync.Downloader
import org.nasmusic.app.sync.EventUploadWorker

/** Hand-rolled dependency container (no DI framework needed for an app this size). */
class AppContainer(val context: Context) {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    val baseClient = HttpClients.baseClient()

    val secureStore = SecureStore(context)
    val settings = SettingsRepository(context, scope)
    val db = AppDatabase.build(context)

    @Volatile private var cachedClients: Pair<ServerConfig, HttpClients>? = null

    /** Per-route OkHttp clients for the current config, rebuilt only when the config changes. */
    fun clients(): HttpClients? {
        val cfg = secureStore.config.value ?: return null
        cachedClients?.let { (c, h) -> if (c == cfg) return h }
        return HttpClients(baseClient, cfg).also { cachedClients = cfg to it }
    }

    val router = ServerRouter(context, secureStore.config, ::clients, scope)
    val callFactory = RoutedCallFactory(::clients, { router.route.value }, baseClient)
    val api = ApiFactory.create(callFactory)

    val pairing = PairingRepository(secureStore, baseClient)
    val musicDir: File = File(context.filesDir, "music")
    val downloader = Downloader(callFactory, musicDir)
    fun lanOnlyDownloader() = Downloader(callFactory.pinnedTo(Route.LAN), musicDir)

    val events = EventRecorder(
        store = db.events(),
        scope = scope,
        isOnline = { router.isOnline },
        requestUpload = { EventUploadWorker.enqueue(context) },
    )

    val library = LibraryRepository(api, db, ApiFactory.json)
    val player = PlayerConnection(context)
}
