package org.nasmusic.wear.offline

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.StatFs
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CancellationException
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Request
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.AudioFormat
import org.nasmusic.app.data.api.Health
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.net.HttpClients
import org.nasmusic.app.net.ProbeResult
import org.nasmusic.app.net.Route
import org.nasmusic.app.net.RouteSelector
import org.nasmusic.app.net.RoutedCallFactory
import org.nasmusic.app.net.ServerConfig
import org.nasmusic.app.sync.Downloader
import org.nasmusic.wear.R
import org.nasmusic.wear.WearApp
import org.nasmusic.wear.WearPrefs

/**
 * Brings the offline lists up to date: refreshes each list's tracks, downloads what's missing (Opus,
 * within the storage budget), deletes what is no longer wanted. Wi-Fi only unless the user allows
 * otherwise; then it explicitly requests Wi-Fi rather than trickling over the Bluetooth proxy.
 */
class OfflineSyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    override suspend fun doWork(): Result {
        val c = (applicationContext as WearApp).container
        val store = c.offline
        val cfg = c.secureStore.config.value?.takeIf { it.isPaired } ?: return Result.success()
        if (store.lists.value.isEmpty()) {
            cleanUp(store, emptySet())
            return Result.success()
        }
        val prefs = c.settings.current
        runCatching { setForeground(foregroundInfo(0, 0)) }

        val lease = if (prefs.downloadWifiOnly) {
            store.setStatus(store.status.value.copy(running = true, message = "Waiting for Wi-Fi…"))
            WifiLease.acquire(applicationContext) ?: run {
                store.setStatus(store.status.value.copy(running = false, message = "No Wi-Fi. Will retry."))
                return Result.retry()
            }
        } else null

        try {
            val base = lease?.bind(c.baseClient) ?: c.baseClient
            val clients = HttpClients(base, cfg)
            val route = pickRoute(clients, cfg)
            val factory = RoutedCallFactory({ clients }, { route }, base, forcedRoute = route)
            val api = ApiFactory.create(factory)
            val downloader = Downloader(factory, store.musicDir)

            // 1. Fresh snapshots of every list (a failure keeps the old snapshot).
            for (l in store.lists.value) {
                val tracks: List<Track>? = runCatching {
                    when (l.kind) {
                        "playlist" -> api.playlist(l.id).tracks
                        "album" -> api.album(l.id).tracks
                        "favourites" -> api.favourites()
                        else -> null
                    }
                }.getOrNull()
                if (tracks != null) store.replaceSnapshot(l.kind, l.id, tracks)
            }

            // 2. Plan within the budget.
            val wanted = store.lists.value.flatMap { it.tracks }
            val existing = wanted.mapNotNull { t -> store.localFile(t.id)?.let { t.id to it.length() } }.toMap()
            val stat = StatFs(store.musicDir.apply { mkdirs() }.path)
            val budget = OfflinePlan.budget(
                capBytes = prefs.storageCapMb.toLong() * 1024 * 1024,
                usedBytes = store.usedBytes(),
                freeBytes = stat.availableBytes,
                reserveBytes = WearPrefs.RESERVE_MB * 1024 * 1024,
            )
            val plan = OfflinePlan.plan(wanted, existing, budget, prefs.opusKbps)

            // 3. Delete files that are no longer wanted (or no longer fit) before downloading.
            cleanUp(store, plan.keep + plan.download.map { it.id })

            // 4. Download.
            var done = 0
            var failed = 0
            val total = plan.download.size
            store.setStatus(store.status.value.copy(running = true, done = 0, total = total, message = null))
            for (t in plan.download) {
                if (isStopped) break
                try {
                    downloader.download(t.id, AudioFormat.OPUS, prefs.opusKbps)
                    done++
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    failed++
                    Log.w(TAG, "download ${t.id} failed", e)
                }
                store.setStatus(store.status.value.copy(running = true, done = done, total = total))
                runCatching { setForeground(foregroundInfo(done, total)) }
            }
            val msg = buildList {
                if (failed > 0) add("$failed failed")
                if (plan.skipped > 0) add("${plan.skipped} didn't fit")
            }.joinToString(", ").ifEmpty { null }
            store.setStatus(SyncStatus(false, done, total, msg, if (failed == 0) System.currentTimeMillis() else store.status.value.lastOkAtMs))
            return if (failed > 0 && runAttemptCount < 3) Result.retry() else Result.success()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w(TAG, "offline sync failed", e)
            store.setStatus(store.status.value.copy(running = false, message = "Sync failed: ${e.message ?: e.javaClass.simpleName}"))
            return Result.retry()
        } finally {
            lease?.close()
            if (store.status.value.running) store.setStatus(store.status.value.copy(running = false))
        }
    }

    private fun cleanUp(store: OfflineStore, keepIds: Set<String>) {
        val keepNames = keepIds.map { OfflineStore.safe(it) }.toSet()
        store.musicDir.listFiles()?.forEach { f ->
            val stem = f.name.removeSuffix(".part").substringBeforeLast('.')
            if (stem !in keepNames) f.delete()
        }
    }

    /** LAN when the pinned server answers /health on this network, else the tunnel. */
    private fun pickRoute(clients: HttpClients, cfg: ServerConfig): Route {
        val lan = cfg.lanHttpUrl
        val probe = if (lan == null) ProbeResult.UNREACHABLE else try {
            val url = RoutedCallFactory.rewrite(RoutedCallFactory.virtual("health").toHttpUrl(), lan)
            clients.lanProbe.newCall(Request.Builder().url(url).build()).execute().use { resp ->
                if (!resp.isSuccessful) ProbeResult.UNREACHABLE
                else {
                    val h = ApiFactory.json.decodeFromString(Health.serializer(), resp.body?.string().orEmpty())
                    RouteSelector.classifyHealth(cfg.serverId, h.serverId, h.status)
                }
            }
        } catch (e: IOException) {
            ProbeResult.UNREACHABLE
        } catch (e: IllegalArgumentException) {
            ProbeResult.UNREACHABLE
        }
        return RouteSelector.select(true, probe, cfg.hasTunnel).takeIf { it != Route.OFFLINE } ?: Route.LAN
    }

    private fun foregroundInfo(done: Int, total: Int): ForegroundInfo {
        val nm = applicationContext.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Offline downloads", NotificationManager.IMPORTANCE_LOW))
        val n = NotificationCompat.Builder(applicationContext, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("Downloading music")
            .setContentText(if (total > 0) "$done of $total" else "Preparing…")
            .setProgress(total, done, total == 0)
            .setOngoing(true)
            .setSilent(true)
            .build()
        return if (Build.VERSION.SDK_INT >= 34) ForegroundInfo(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        else ForegroundInfo(NOTIF_ID, n)
    }

    companion object {
        private const val TAG = "OfflineSync"
        private const val CHANNEL = "offline"
        private const val NOTIF_ID = 42
        private const val NOW = "offline-sync-now"
        private const val DAILY = "offline-sync-daily"

        /** Run soon (after the user adds/removes a list or taps "Sync now"). */
        fun runNow(context: Context) {
            val req = OneTimeWorkRequestBuilder<OfflineSyncWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(androidx.work.BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(NOW, ExistingWorkPolicy.REPLACE, req)
        }

        /** Nightly refresh while the watch charges (playlists change on the server). */
        fun schedule(context: Context) {
            val req = PeriodicWorkRequestBuilder<OfflineSyncWorker>(1, TimeUnit.DAYS)
                .setConstraints(
                    Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).setRequiresCharging(true).build(),
                )
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(DAILY, ExistingPeriodicWorkPolicy.KEEP, req)
        }
    }
}
