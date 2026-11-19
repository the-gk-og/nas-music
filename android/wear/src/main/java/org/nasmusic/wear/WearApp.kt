package org.nasmusic.wear

import android.app.Application
import android.content.Context
import android.os.Handler
import android.os.Looper
import coil3.ImageLoader
import coil3.PlatformContext
import coil3.SingletonImageLoader
import coil3.network.okhttp.OkHttpNetworkFetcherFactory
import kotlinx.coroutines.CoroutineExceptionHandler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.launch
import org.nasmusic.app.auth.AuthRepository
import org.nasmusic.app.data.FeedRepository
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.net.HttpClients
import org.nasmusic.app.net.Route
import org.nasmusic.app.net.RoutedCallFactory
import org.nasmusic.app.net.ServerConfig
import org.nasmusic.app.net.ServerRouter
import org.nasmusic.app.pairing.SecureStore
import org.nasmusic.wear.auth.WearAuth
import org.nasmusic.wear.connect.WearConnect
import org.nasmusic.wear.nowplaying.NowPlayingStore
import org.nasmusic.wear.offline.OfflineStore
import org.nasmusic.wear.offline.OfflineSyncWorker
import org.nasmusic.wear.playback.PlayerHub

class WearApp : Application(), SingletonImageLoader.Factory {
    lateinit var container: WearContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = WearContainer(this)
        container.router.start()
        container.startServerInfoRefresh()
        if (container.offline.lists.value.isNotEmpty()) OfflineSyncWorker.schedule(this)
    }

    override fun newImageLoader(context: PlatformContext): ImageLoader =
        ImageLoader.Builder(context)
            .components { add(OkHttpNetworkFetcherFactory(callFactory = { container.callFactory })) }
            .build()
}

/** Hand-rolled dependency container, like the phone's AppContainer. */
class WearContainer(val context: Context) {
    val scope = CoroutineScope(
        SupervisorJob() + Dispatchers.Default + CoroutineExceptionHandler { _, e -> android.util.Log.e("nas-wear", "background", e) },
    )
    val baseClient = HttpClients.baseClient()
    val secureStore = SecureStore(context)
    val settings = WearSettings(context)

    @Volatile private var cachedClients: Pair<ServerConfig, HttpClients>? = null

    fun clients(): HttpClients? {
        val cfg = secureStore.config.value ?: return null
        cachedClients?.let { (c, h) -> if (c == cfg) return h }
        return HttpClients(baseClient, cfg).also { cachedClients = cfg to it }
    }

    /** Same LAN-first routing as the phone: probe /health on the pinned LAN address on every network change. */
    val router = ServerRouter(context, secureStore.config, ::clients, scope)
    val callFactory = RoutedCallFactory(::clients, { router.route.value }, baseClient)
    val api = ApiFactory.create(callFactory)
    val feeds = FeedRepository(api)
    val auth = WearAuth(secureStore, baseClient)
    /** The phone's AuthRepository, used only for its /server-info address/pin update rule. */
    private val phoneAuth = AuthRepository(secureStore, baseClient)
    val offline = OfflineStore(context.filesDir)
    val nowPlaying = NowPlayingStore(context)
    val ui = UiMessages()

    val connect = WearConnect(
        clients = ::clients,
        route = router.route,
        scope = scope,
        deviceName = { WearAuth.deviceName() },
        appVersion = BuildConfig.VERSION_NAME,
    )
    val player = PlayerHub(context, connect, { api }, nowPlaying, scope).also { hub ->
        // Another device handed playback to the watch: binding a controller starts the service, which applies it.
        connect.onTakeover = { Handler(Looper.getMainLooper()).post { hub.connectLocal() } }
    }

    /** Keep both addresses (and, via the CA-validated tunnel, the LAN pin) current from /server-info. */
    fun startServerInfoRefresh() = scope.launch {
        router.route.collect { r ->
            if (r == Route.OFFLINE || secureStore.config.value?.isPaired != true) return@collect
            runCatching { api.serverInfo() }.onSuccess { phoneAuth.applyServerInfo(it, r) }
        }
    }
}

class UiMessages {
    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 8)
    val messages: SharedFlow<String> = _messages
    fun toast(text: String) { _messages.tryEmit(text) }
}
