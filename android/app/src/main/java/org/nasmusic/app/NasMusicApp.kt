package org.nasmusic.app

import android.app.Application
import coil3.ImageLoader
import coil3.PlatformContext
import coil3.SingletonImageLoader
import coil3.network.okhttp.OkHttpNetworkFetcherFactory
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import org.nasmusic.app.sync.EventUploadWorker
import org.nasmusic.app.sync.SyncScheduler

class NasMusicApp : Application(), SingletonImageLoader.Factory {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        org.nasmusic.app.diag.CrashLog.install(this)
        container = AppContainer(this)
        container.router.start()
        container.eq.start()
        container.contextCollector.start()
        container.connect.start()
        org.nasmusic.app.cast.NativeCast.init(container)   // Google Cast (full flavour); no-op in foss
        container.startServerInfoRefresh()
        container.startAppearancePush()
        // Streaming is the default; the nightly sync only exists while offline favourites, smart downloads
        // or a playlist/album kept offline needs it.
        container.scope.launch {
            kotlinx.coroutines.flow.combine(container.settings.settings, container.offline.pins) { s, pins ->
                (s.offlineFavourites || s.smartDownloads || pins.isNotEmpty()) to s.syncHour
            }.distinctUntilChanged().collect { (on, hour) ->
                if (on) SyncScheduler.scheduleNight(this@NasMusicApp, hour) else SyncScheduler.cancelNight(this@NasMusicApp)
            }
        }
        EventUploadWorker.enqueue(this)
        org.nasmusic.app.notify.NewMusicWorker.schedule(this)
    }

    /** Cover art goes through the routed call factory so it works on LAN (pinned) and tunnel. */
    override fun newImageLoader(context: PlatformContext): ImageLoader =
        ImageLoader.Builder(context)
            .components { add(OkHttpNetworkFetcherFactory(callFactory = { container.callFactory })) }
            .build()
}

val android.content.Context.container: AppContainer
    get() = (applicationContext as NasMusicApp).container
