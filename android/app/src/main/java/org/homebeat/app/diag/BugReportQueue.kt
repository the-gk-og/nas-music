package org.homebeat.app.diag

import android.content.Context
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.Json
import org.homebeat.app.container
import retrofit2.HttpException

/**
 * Bug reports that couldn't be sent (offline, server down) wait in `files/bug-reports-pending/` and
 * [BugReportWorker] sends them once there is a connection.
 */
class BugReportQueue(private val dir: File) {
    constructor(context: Context) : this(File(context.filesDir, "bug-reports-pending"))

    fun add(body: BugReportBody) {
        dir.mkdirs()
        File(dir, "report-${System.currentTimeMillis()}.json").writeText(BugReport.encode(body))
        pending().drop(MAX).forEach { it.delete() }
    }

    /** Newest first. */
    fun pending(): List<File> = dir.listFiles()?.filter { it.isFile && it.name.endsWith(".json") }
        ?.sortedByDescending { it.name }.orEmpty()

    sealed interface Outcome { data object Done : Outcome; data object RetryLater : Outcome }

    /** Sends every queued report: sent and permanently refused ones are removed, the rest kept. */
    suspend fun flush(send: suspend (BugReportBody) -> Unit): Outcome {
        var retry = false
        for (f in pending().reversed()) {
            val body = runCatching { json.decodeFromString(BugReportBody.serializer(), f.readText()) }.getOrNull()
            if (body == null) { f.delete(); continue }
            try {
                send(body)
                f.delete()
            } catch (e: HttpException) {
                if (BugReport.retryable(e.code())) retry = true
                else { Log.w(CrashLog.TAG, "Queued bug report refused (HTTP ${e.code()}), dropped"); f.delete() }
            } catch (e: IOException) {
                retry = true
            }
        }
        return if (retry) Outcome.RetryLater else Outcome.Done
    }

    companion object {
        const val MAX = 3
        private val json = Json { ignoreUnknownKeys = true }
    }
}

class BugReportWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val c = applicationContext.container
        if (c.secureStore.config.value?.isPaired != true) return Result.success()
        val out = BugReportQueue(applicationContext).flush { c.api.bugReport(it) }
        return if (out == BugReportQueue.Outcome.Done) Result.success() else Result.retry()
    }

    companion object {
        fun enqueue(context: Context) {
            val req = OneTimeWorkRequestBuilder<BugReportWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork("send-bug-reports", ExistingWorkPolicy.REPLACE, req)
        }
    }
}
