package org.nasmusic.app

import android.app.Application
import coil3.ImageLoader
import coil3.PlatformContext
import coil3.SingletonImageLoader
import coil3.network.okhttp.OkHttpNetworkFetcherFactory
import org.nasmusic.app.sync.EventUploadWorker
import org.nasmusic.app.sync.SyncScheduler

class NasMusicApp : Application(), SingletonImageLoader.Factory {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        container.router.start()
        SyncScheduler.scheduleNight(this, container.settings.settings.value.syncHour)
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
