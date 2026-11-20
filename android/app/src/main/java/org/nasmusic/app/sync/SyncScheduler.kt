package org.nasmusic.app.sync

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import java.time.Duration
import java.time.LocalDateTime
import java.util.concurrent.TimeUnit

object SyncScheduler {
    const val NIGHT = "night-sync"
    const val NOW = "sync-now"
    const val PROTECTED = "download-now"

    /**
     * Daily sync aligned to [hour]:00, only on an unmetered network while charging. The worker
     * itself skips runs that start outside the [hour, hour+2h) window (e.g. charger plugged in at 9am).
     */
    fun scheduleNight(context: Context, hour: Int, replace: Boolean = false) {
        val req = PeriodicWorkRequestBuilder<SyncWorker>(24, TimeUnit.HOURS)
            .setInitialDelay(delayUntil(LocalDateTime.now(), hour).toMillis(), TimeUnit.MILLISECONDS)
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.UNMETERED)
                    .setRequiresCharging(true)
                    .build(),
            )
            .addTag(NIGHT)
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            NIGHT,
            if (replace) ExistingPeriodicWorkPolicy.CANCEL_AND_REENQUEUE else ExistingPeriodicWorkPolicy.KEEP,
            req,
        )
    }

    fun cancelNight(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(NIGHT)
    }

    /** The "Sync now" button: any connected network, no charging requirement. Route rules still apply. */
    fun syncNow(context: Context) {
        val req = OneTimeWorkRequestBuilder<SyncWorker>()
            .setInputData(workDataOf(SyncWorker.KEY_MANUAL to true))
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .addTag(NOW)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(NOW, ExistingWorkPolicy.KEEP, req)
    }

    /**
     * Manual downloads / kept collections: fetch them as soon as there's a connection the route rules
     * allow (home network, or the tunnel if tunnel sync is on). Not on the home network yet → retried.
     */
    fun downloadProtectedNow(context: Context) {
        val req = OneTimeWorkRequestBuilder<SyncWorker>()
            .setInputData(workDataOf(SyncWorker.KEY_PROTECTED_ONLY to true))
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(androidx.work.BackoffPolicy.LINEAR, 15, TimeUnit.MINUTES)
            .addTag(PROTECTED)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(PROTECTED, ExistingWorkPolicy.APPEND_OR_REPLACE, req)
    }

    fun delayUntil(now: LocalDateTime, hour: Int): Duration {
        var next = now.toLocalDate().atTime(hour, 0)
        if (!next.isAfter(now)) next = next.plusDays(1)
        return Duration.between(now, next)
    }

    fun inWindow(nowHour: Int, nowMinute: Int, startHour: Int, windowHours: Int): Boolean {
        val minutesSinceStart = ((nowHour - startHour).mod(24)) * 60 + nowMinute
        return minutesSinceStart < windowHours * 60
    }
}
