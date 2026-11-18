package org.nasmusic.app.sync

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit
import org.nasmusic.app.container
import org.nasmusic.app.feedback.EventUploader
import org.nasmusic.app.net.Route

/** Uploads queued feedback events whenever there is a connection. */
class EventUploadWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val c = applicationContext.container
        if (c.secureStore.config.value?.isPaired != true) return Result.success()
        if (c.router.refresh() == Route.OFFLINE) return Result.retry()
        val out = EventUploader(c.db.events(), { c.api.events(it) }).uploadAll()
        return if (out.error == null) Result.success() else Result.retry()
    }

    companion object {
        fun enqueue(context: Context) {
            val req = OneTimeWorkRequestBuilder<EventUploadWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setInitialDelay(5, TimeUnit.SECONDS) // let a burst of events batch up
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork("upload-events", ExistingWorkPolicy.KEEP, req)
        }
    }
}
