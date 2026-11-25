package org.homebeat.app

import android.content.Context
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import org.homebeat.app.data.LibraryRepository
import org.homebeat.app.data.api.ApiFactory
import org.homebeat.app.data.db.AppDatabase
import org.homebeat.app.data.prefs.SettingsRepository
import org.homebeat.app.feedback.EventRecorder
import org.homebeat.app.lyrics.LyricsRepository
import org.homebeat.app.net.HttpClients
import org.homebeat.app.net.Route
import org.homebeat.app.net.RoutedCallFactory
import org.homebeat.app.net.ServerConfig
import org.homebeat.app.net.ServerRouter
import org.homebeat.app.pairing.PairingRepository
import org.homebeat.app.pairing.SecureStore
import org.homebeat.app.playback.PlayerConnection
import org.homebeat.app.auth.AuthRepository
import org.homebeat.app.connect.ConnectClient
import org.homebeat.app.context.ContextCollector
import org.homebeat.app.eq.EqRepository
import org.homebeat.app.net.CertProbe
import org.homebeat.app.playback.AbRepeat
import org.homebeat.app.playback.QueueSync
import kotlinx.coroutines.launch
import org.homebeat.app.sync.Downloader
import org.homebeat.app.sync.EventUploadWorker

/** Hand-rolled dependency container (no DI framework needed for an app this size). */
class AppContainer(val context: Context) {
    // The handler logs failures in background work instead of letting one kill the app.
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default + org.homebeat.app.diag.CrashLog.handler)
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
    val uploads = org.homebeat.app.upload.UploadsApi.create(callFactory)
    val spokenApi: org.homebeat.app.data.api.SpokenApi = ApiFactory.spoken(callFactory)

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
    val offline = org.homebeat.app.sync.OfflineRepository(context, db, downloader, settings, scope)
    /** Songs stored on the phone itself (Settings → Music on this phone; off by default). */
    val localMusic = org.homebeat.app.local.LocalLibrary(context, scope)
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
        org.homebeat.app.playback.OfflineSource(
            isOffline = { router.route.value == Route.OFFLINE },
            downloadedIds = { offline.downloadedIds.value },
            // Offline radio also draws on songs stored on the phone.
            downloadedTracks = { offline.offlineTracks.value + localMusic.tracks.value },
            toast = { ui.toast(it) },
        ),
        listening = { listening.settings.value },
        spokenApi = { spokenApi },
    ).also { p ->
        // Binding a MediaController starts the playback service, which then applies the takeover.
        connect.onTakeover = { android.os.Handler(android.os.Looper.getMainLooper()).post { p.connect() } }
    }
    val abRepeat = AbRepeat()
    /** Format of what this phone is playing ("FLAC 16/44.1", "Opus 128"); set by the playback service. */
    val nowFormat = kotlinx.coroutines.flow.MutableStateFlow<org.homebeat.app.playback.NowFormat?>(null)
    /** Messages from background parts (playback service, workers) to show as a snackbar. */
    val ui = UiMessages().also { u -> connect.onSpeakerError = { u.toast(it) } }
    val eq = EqRepository(context)
    val listening = org.homebeat.app.playback.ListeningRepository(context)
    val sleep = org.homebeat.app.playback.SleepTimerState()
    /** Wake-up alarm settings (see [org.homebeat.app.alarm.WakeAlarm]). */
    val alarm = org.homebeat.app.alarm.AlarmStore(context)
    val contextCollector = ContextCollector(context, scope)
    val feeds = org.homebeat.app.data.FeedRepository(api)
    /**
     * Podcasts, audiobooks, internet radio: played by the main player (Connect, Cast, notification, resume), but
     * never part of music features (no events, scrobbles, recommender, Wrapped, music queues).
     */
    val spoken: org.homebeat.app.spoken.SpokenPlayer = org.homebeat.app.spoken.SpokenPlayer(
        player, { spokenApi }, org.homebeat.app.spoken.SpokenDownloads(callFactory, File(context.filesDir, "spoken")),
        org.homebeat.app.spoken.SpokenProgressStore(context, { spokenApi }, scope),
    )
    val family = org.homebeat.app.data.FamilyRepository { api }.also { f -> connect.onFamilyChanged = { f.changed(it) } }
    /** api 0.7 notifications: Connect pushes + catch-up on every (re)connect; NewMusicWorker polls in the background. */
    val notifications = org.homebeat.app.notify.NotificationRepository(context) { api }.also { n ->
        connect.onNotification = { pushed -> scope.launch { if (pushed == null) n.catchUp() else n.pushed(pushed) } }
    }
    /** A screen to open from a notification tap (MainActivity sets it, HomebeatRoot navigates). */
    val pendingRoute = kotlinx.coroutines.flow.MutableStateFlow<String?>(null)

    /** Keep both addresses (and, via the tunnel, the LAN pin) current from /server-info. */
    fun startServerInfoRefresh() = scope.launch {
        router.route.collect { r ->
            if (r == Route.OFFLINE || secureStore.config.value?.isPaired != true) return@collect
            runCatching { api.serverInfo() }.onSuccess { auth.applyServerInfo(it, r) }
            queueSync.refresh()
            contextCollector.syncFrom(api)
            pullAppearance()
            family.refresh()
            runCatching { spoken.flush() }
            if (r == Route.LAN) scope.launch { runCatching { spoken.downloads.autoDownload(spokenApi) } }
        }
    }

    /** Appearance follows the user across devices via /me/settings (docs/v3-ux.md §7). */
    private suspend fun pullAppearance() {
        val remote = runCatching { api.mySettings().settings }.getOrNull() ?: return
        val cur = settings.current()
        val next = org.homebeat.app.data.prefs.AppearanceSync.fromRemote(remote, cur)
        if (next != cur) settings.applyAppearance(next)
    }

    fun startAppearancePush() {
        settings.onAppearanceChanged = { s ->
            if (secureStore.config.value?.isPaired == true) {
                runCatching { api.putMySettings(org.homebeat.app.data.api.UserSettings(org.homebeat.app.data.prefs.AppearanceSync.toRemote(s))) }
            }
        }
    }
}

class UiMessages {
    private val _messages = kotlinx.coroutines.flow.MutableSharedFlow<String>(extraBufferCapacity = 8)
    val messages: kotlinx.coroutines.flow.SharedFlow<String> = _messages
    fun toast(text: String) { _messages.tryEmit(text) }
}
