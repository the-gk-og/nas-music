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
        container = AppContainer(this)
        container.router.start()
        container.eq.start()
        container.contextCollector.start()
        container.connect.start()
        container.startServerInfoRefresh()
        // Streaming is the default; the nightly download only exists while "Offline favourites" is on.
        container.scope.launch {
            container.settings.settings.map { it.offlineFavourites to it.syncHour }.distinctUntilChanged().collect { (on, hour) ->
                if (on) SyncScheduler.scheduleNight(this@NasMusicApp, hour) else SyncScheduler.cancelNight(this@NasMusicApp)
            }
        }
        EventUploadWorker.enqueue(this)
    }

    /** Cover art goes through the routed call factory so it works on LAN (pinned) and tunnel. */
    override fun newImageLoader(context: PlatformContext): ImageLoader =
        ImageLoader.Builder(context)
            .components { add(OkHttpNetworkFetcherFactory(callFactory = { container.callFactory })) }
            .build()
}

val android.content.Context.container: AppContainer
    get() = (applicationContext as NasMusicApp).container
