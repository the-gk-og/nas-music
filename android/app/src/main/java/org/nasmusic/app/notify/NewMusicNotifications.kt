package org.nasmusic.app.notify

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.time.Duration
import java.time.Instant
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.nasmusic.app.MainActivity
import org.nasmusic.app.R
import org.nasmusic.app.container
import org.nasmusic.app.data.api.AppNotification
import org.nasmusic.app.data.api.NasMusicApi
import org.nasmusic.app.data.api.ReadNotifications
import org.nasmusic.app.net.Route

/**
 * api 0.7 "new music added" notifications without Google push: the Connect socket delivers them while the
 * app runs, and [NewMusicWorker] polls GET /me/notifications?since= about once an hour (and every reconnect
 * catches up). Each notification is shown as a system notification once ([lastShown] is the newest
 * createdAt already shown). Whether the server creates them at all is the user's `notify.newMusic` setting.
 */
class NotificationRepository(private val context: Context, private val api: () -> NasMusicApi) {
    private val prefs = context.getSharedPreferences("notifications", Context.MODE_PRIVATE)
    private val lock = Mutex()
    private val _unread = MutableStateFlow(0)
    val unread: StateFlow<Int> = _unread
    private val _items = MutableStateFlow<List<AppNotification>>(emptyList())
    val items: StateFlow<List<AppNotification>> = _items

    private var lastShown: String?
        get() = prefs.getString(KEY_LAST, null)
        set(v) { prefs.edit().putString(KEY_LAST, v).apply() }

    /** Fetch what's new since the last one shown and raise system notifications for it. */
    suspend fun catchUp(now: Instant = Instant.now()) = lock.withLock {
        val r = runCatching { api().notifications(since = lastShown) }.getOrNull() ?: return@withLock
        _unread.value = r.unread
        val (show, newest) = NotificationLogic.toShow(r.items, lastShown, now)
        show.forEach { notify(it) }
        if (newest != null) lastShown = newest
    }

    /** From Connect. */
    suspend fun pushed(n: AppNotification) = lock.withLock {
        val last = lastShown
        if (last != null && n.createdAt <= last) return@withLock
        _unread.value = _unread.value + 1
        notify(n)
        lastShown = n.createdAt
    }

    suspend fun load() {
        runCatching { api().notifications() }.onSuccess { _items.value = it.items; _unread.value = it.unread }
    }

    suspend fun markAllRead() {
        runCatching { api().readNotifications(ReadNotifications()) }.onSuccess { r ->
            _unread.value = r.unread
            _items.value = _items.value.map { it.copy(read = true) }
            NotificationManagerCompat.from(context).cancel(NOTIFICATION_ID)
        }
    }

    private fun notify(n: AppNotification) {
        val nm = NotificationManagerCompat.from(context)
        if (!nm.areNotificationsEnabled()) return
        ensureChannel(context)
        val open = PendingIntent.getActivity(
            context, 7,
            Intent(context, MainActivity::class.java).putExtra(MainActivity.EXTRA_ROUTE, ROUTE)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val b = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(n.title)
            .setContentText(n.body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(n.body))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setCategory(NotificationCompat.CATEGORY_RECOMMENDATION)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
        try {
            nm.notify(NOTIFICATION_ID, b.build())   // one at a time: a newer batch replaces the older one
        } catch (_: SecurityException) {
            // POST_NOTIFICATIONS revoked
        }
    }

    companion object {
        const val CHANNEL = "new_music"
        const val NOTIFICATION_ID = 7301
        const val ROUTE = "list/new"
        private const val KEY_LAST = "lastShown"

        fun ensureChannel(context: Context) {
            if (Build.VERSION.SDK_INT < 26) return
            val nm = context.getSystemService(NotificationManager::class.java) ?: return
            if (nm.getNotificationChannel(CHANNEL) != null) return
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "New music", NotificationManager.IMPORTANCE_DEFAULT).apply {
                    description = "When new songs or albums are added to the library"
                },
            )
        }
    }
}

/** Pure decision logic (unit-tested). */
object NotificationLogic {
    /** Don't pop up old news on a fresh install / first run. */
    val FIRST_RUN_WINDOW: Duration = Duration.ofDays(1)

    /** (notifications to show, the new lastShown). `items` may be in any order. */
    fun toShow(items: List<AppNotification>, lastShown: String?, now: Instant): Pair<List<AppNotification>, String?> {
        val newer = items.filter { lastShown == null || it.createdAt > lastShown }.sortedBy { it.createdAt }
        val newest = newer.lastOrNull()?.createdAt ?: lastShown
        val unread = newer.filter { !it.read }
        val show = if (lastShown == null) {
            unread.filter { runCatching { Instant.parse(it.createdAt) }.getOrNull()?.isAfter(now - FIRST_RUN_WINDOW) == true }.takeLast(1)
        } else unread.takeLast(1)   // several missed batches: the newest one says enough
        return show to newest
    }
}

/** Periodic check (no FCM): about hourly, whenever there's a network. */
class NewMusicWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val c = applicationContext.container
        if (c.secureStore.config.value?.isPaired != true) return Result.success()
        if (c.router.refresh() == Route.OFFLINE) return Result.success()
        c.notifications.catchUp()
        return Result.success()
    }

    companion object {
        fun schedule(context: Context) {
            val req = PeriodicWorkRequestBuilder<NewMusicWorker>(1, TimeUnit.HOURS, 20, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork("new-music-check", ExistingPeriodicWorkPolicy.KEEP, req)
        }
    }
}
