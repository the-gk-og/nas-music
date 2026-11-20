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
import org.nasmusic.app.lyrics.LyricsRepository
import org.nasmusic.app.net.HttpClients
import org.nasmusic.app.net.Route
import org.nasmusic.app.net.RoutedCallFactory
import org.nasmusic.app.net.ServerConfig
import org.nasmusic.app.net.ServerRouter
import org.nasmusic.app.pairing.PairingRepository
import org.nasmusic.app.pairing.SecureStore
import org.nasmusic.app.playback.PlayerConnection
import org.nasmusic.app.auth.AuthRepository
import org.nasmusic.app.connect.ConnectClient
import org.nasmusic.app.context.ContextCollector
import org.nasmusic.app.eq.EqRepository
import org.nasmusic.app.net.CertProbe
import org.nasmusic.app.playback.AbRepeat
import org.nasmusic.app.playback.QueueSync
import kotlinx.coroutines.launch
import org.nasmusic.app.sync.Downloader
import org.nasmusic.app.sync.EventUploadWorker

/** Hand-rolled dependency container (no DI framework needed for an app this size). */
class AppContainer(val context: Context) {
    // The handler logs failures in background work instead of letting one kill the app.
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default + org.nasmusic.app.diag.CrashLog.handler)
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
    val discovery = ApiFactory.discovery(callFactory)
    val jam = ApiFactory.jam(callFactory)
    val uploads = org.nasmusic.app.upload.UploadsApi.create(callFactory)

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
    val offline = org.nasmusic.app.sync.OfflineRepository(context, db, downloader, settings, scope)
    val lyrics = LyricsRepository(db.lyrics(), { api.lyrics(it) }, ApiFactory.json)

    // ---- v2 ----
    val certProbe = CertProbe(baseClient)
    val auth = AuthRepository(secureStore, baseClient)
    val queueSync = QueueSync({ api }, scope)
    val connect = ConnectClient(
        clients = ::clients,
        route = router.route,
        scope = scope,
        deviceName = { AuthRepository.deviceName() },
        appVersion = BuildConfig.VERSION_NAME,
    )
    val player = PlayerConnection(
        context, connect, queueSync, { api }, scope,
        org.nasmusic.app.playback.OfflineSource(
            isOffline = { router.route.value == Route.OFFLINE },
            downloadedIds = { offline.downloadedIds.value },
            downloadedTracks = { offline.offlineTracks.value },
            toast = { ui.toast(it) },
        ),
    ).also { p ->
        // Binding a MediaController starts the playback service, which then applies the takeover.
        connect.onTakeover = { android.os.Handler(android.os.Looper.getMainLooper()).post { p.connect() } }
    }
    val abRepeat = AbRepeat()
    /** Messages from background parts (playback service, workers) to show as a snackbar. */
    val ui = UiMessages().also { u -> connect.onSpeakerError = { u.toast(it) } }
    val eq = EqRepository(context)
    val listening = org.nasmusic.app.playback.ListeningRepository(context)
    val sleep = org.nasmusic.app.playback.SleepTimerState()
    val contextCollector = ContextCollector(context, scope)
    val feeds = org.nasmusic.app.data.FeedRepository(api)
    val family = org.nasmusic.app.data.FamilyRepository { api }.also { f -> connect.onFamilyChanged = { f.changed(it) } }

    /** Keep both addresses (and, via the tunnel, the LAN pin) current from /server-info. */
    fun startServerInfoRefresh() = scope.launch {
        router.route.collect { r ->
            if (r == Route.OFFLINE || secureStore.config.value?.isPaired != true) return@collect
            runCatching { api.serverInfo() }.onSuccess { auth.applyServerInfo(it, r) }
            queueSync.refresh()
            contextCollector.syncFrom(api)
            pullAppearance()
            family.refresh()
        }
    }

    /** Appearance follows the user across devices via /me/settings (docs/v3-ux.md §7). */
    private suspend fun pullAppearance() {
        val remote = runCatching { api.mySettings().settings }.getOrNull() ?: return
        val cur = settings.current()
        val next = org.nasmusic.app.data.prefs.AppearanceSync.fromRemote(remote, cur)
        if (next != cur) settings.applyAppearance(next)
    }

    fun startAppearancePush() {
        settings.onAppearanceChanged = { s ->
            if (secureStore.config.value?.isPaired == true) {
                runCatching { api.putMySettings(org.nasmusic.app.data.api.UserSettings(org.nasmusic.app.data.prefs.AppearanceSync.toRemote(s))) }
            }
        }
    }
}

class UiMessages {
    private val _messages = kotlinx.coroutines.flow.MutableSharedFlow<String>(extraBufferCapacity = 8)
    val messages: kotlinx.coroutines.flow.SharedFlow<String> = _messages
    fun toast(text: String) { _messages.tryEmit(text) }
}
