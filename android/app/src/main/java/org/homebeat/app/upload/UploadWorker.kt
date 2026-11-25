package org.homebeat.app.upload

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.Build
import android.provider.OpenableColumns
import androidx.core.app.NotificationCompat
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import java.io.File
import java.io.IOException
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.homebeat.app.container
import org.homebeat.app.data.api.ApiFactory
import org.homebeat.app.net.Route

/**
 * Uploads one file (copied into cache/uploads/<work id>/ when queued, so a share-sheet grant that expires
 * doesn't matter) with a progress notification. Interrupted uploads resume from the server's offset: the
 * session id is kept in prefs until the file is done. Wi-Fi only = an UNMETERED network constraint.
 */
class UploadWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    override suspend fun doWork(): Result {
        val c = applicationContext.container
        val path = inputData.getString(KEY_FILE) ?: return Result.failure(workDataOf(KEY_ERROR to "nothing to upload"))
        val name = inputData.getString(KEY_NAME) ?: File(path).name
        val visibility = inputData.getString(KEY_VISIBILITY) ?: "everyone"
        val file = File(path)
        if (!file.isFile) return Result.failure(workDataOf(KEY_ERROR to "the file is gone"))
        if (c.secureStore.config.value?.isPaired != true) return Result.failure(workDataOf(KEY_ERROR to "not signed in"))
        runCatching { setForeground(foreground(name, 0, file.length())) } // not allowed from some background states
        if (c.router.refresh() == Route.OFFLINE) return Result.retry()
        val prefs = sessions(applicationContext)
        val key = id.toString()
        return try {
            val s = UploadEngine.send(
                ApiTransport(c.uploads, ApiFactory.json), FileChunkSource(file), name, visibility,
                sessionId = prefs.getString(key, null),
                onSession = { prefs.edit().putString(key, it.id).apply() },
                onProgress = { sent, total ->
                    setProgress(workDataOf(KEY_SENT to sent, KEY_TOTAL to total))
                    runCatching { notify(name, sent, total) }
                },
            )
            prefs.edit().remove(key).apply()
            file.parentFile?.deleteRecursively()
            Result.success(workDataOf(KEY_PATH to (s.upload?.path ?: ""), KEY_TRACK to (s.upload?.trackId ?: "")))
        } catch (e: UploadRefused) {
            prefs.edit().remove(key).apply()
            file.parentFile?.deleteRecursively()
            Result.failure(workDataOf(KEY_ERROR to (e.message ?: "refused")))
        } catch (e: IOException) {
            if (runAttemptCount >= MAX_ATTEMPTS) Result.failure(workDataOf(KEY_ERROR to "network: ${e.message}"))
            else Result.retry()
        }
    }

    private fun notification(name: String, sent: Long, total: Long) =
        NotificationCompat.Builder(applicationContext, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setContentTitle("Uploading $name")
            .setContentText(if (total > 0) "${sent * 100 / total}%" else "")
            .setProgress(100, if (total > 0) (sent * 100 / total).toInt() else 0, total <= 0)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .build()

    private fun foreground(name: String, sent: Long, total: Long): ForegroundInfo {
        ensureChannel(applicationContext)
        val n = notification(name, sent, total)
        return if (Build.VERSION.SDK_INT >= 29) ForegroundInfo(notificationId(), n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        else ForegroundInfo(notificationId(), n)
    }

    private suspend fun notify(name: String, sent: Long, total: Long) = setForeground(foreground(name, sent, total))

    private fun notificationId() = 7000 + (id.hashCode() and 0xfff)

    companion object {
        const val TAG = "upload"
        const val CHANNEL = "uploads"
        const val KEY_FILE = "file"
        const val KEY_NAME = "name"
        const val KEY_VISIBILITY = "visibility"
        const val KEY_SENT = "sent"
        const val KEY_TOTAL = "total"
        const val KEY_ERROR = "error"
        const val KEY_PATH = "path"
        const val KEY_TRACK = "trackId"
        private const val MAX_ATTEMPTS = 30
        private const val NAME_TAG = "name:"
        private const val SIZE_TAG = "size:"
        private const val VIS_TAG = "vis:"

        fun sessions(ctx: Context) = ctx.getSharedPreferences("upload_sessions", Context.MODE_PRIVATE)
        fun prefs(ctx: Context) = ctx.getSharedPreferences("uploads", Context.MODE_PRIVATE)
        fun wifiOnly(ctx: Context) = prefs(ctx).getBoolean("wifiOnly", false)
        fun setWifiOnly(ctx: Context, on: Boolean) = prefs(ctx).edit().putBoolean("wifiOnly", on).apply()

        fun nameOf(tags: Set<String>) = tags.firstOrNull { it.startsWith(NAME_TAG) }?.removePrefix(NAME_TAG) ?: "song"
        fun visibilityOf(tags: Set<String>) = tags.firstOrNull { it.startsWith(VIS_TAG) }?.removePrefix(VIS_TAG) ?: "everyone"
        fun sizeOf(tags: Set<String>) = tags.firstOrNull { it.startsWith(SIZE_TAG) }?.removePrefix(SIZE_TAG)?.toLongOrNull() ?: 0L

        fun ensureChannel(ctx: Context) {
            if (Build.VERSION.SDK_INT < 26) return
            val nm = ctx.getSystemService(NotificationManager::class.java)
            if (nm.getNotificationChannel(CHANNEL) == null)
                nm.createNotificationChannel(NotificationChannel(CHANNEL, "Uploads", NotificationManager.IMPORTANCE_LOW))
        }

        private val AUDIO_EXTS = setOf("flac", "mp3", "m4a", "mp4", "aac", "ogg", "oga", "opus", "wav")
        private val MIME_EXTS = mapOf(
            "audio/flac" to "flac", "audio/x-flac" to "flac", "audio/mpeg" to "mp3", "audio/mp3" to "mp3",
            "audio/mp4" to "m4a", "audio/x-m4a" to "m4a", "audio/aac" to "aac", "audio/ogg" to "ogg",
            "audio/opus" to "opus", "audio/wav" to "wav", "audio/x-wav" to "wav", "audio/vnd.wave" to "wav",
        )

        /** A file name the server will accept: keeps an audio extension, else adds one from the MIME type. */
        fun uploadName(display: String?, mime: String?): String {
            val base = (display ?: "upload").substringAfterLast('/').ifBlank { "upload" }
            val ext = base.substringAfterLast('.', "").lowercase()
            if (ext in AUDIO_EXTS) return base
            return "$base.${MIME_EXTS[mime?.lowercase()] ?: "flac"}"
        }

        /** Copy each shared/picked file into the app cache and queue one upload job per file. Returns how many. */
        suspend fun enqueue(ctx: Context, uris: List<Uri>, visibility: String, wifiOnly: Boolean): Int = withContext(Dispatchers.IO) {
            val wm = WorkManager.getInstance(ctx)
            var n = 0
            for (uri in uris) {
                val cr = ctx.contentResolver
                var display: String? = null
                runCatching {
                    cr.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cur ->
                        if (cur.moveToFirst()) display = cur.getString(0)
                    }
                }
                val name = uploadName(display ?: uri.lastPathSegment, cr.getType(uri))
                val workId = UUID.randomUUID()
                val dir = File(ctx.cacheDir, "uploads/$workId").apply { mkdirs() }
                val dest = File(dir, name.replace('/', '_'))
                val ok = runCatching {
                    cr.openInputStream(uri)?.use { input -> dest.outputStream().use { input.copyTo(it, 256 * 1024) } } != null
                }.getOrDefault(false)
                if (!ok || dest.length() == 0L) { dir.deleteRecursively(); continue }
                val req = OneTimeWorkRequestBuilder<UploadWorker>()
                    .setId(workId)
                    .setInputData(workDataOf(KEY_FILE to dest.path, KEY_NAME to name, KEY_VISIBILITY to visibility))
                    .setConstraints(Constraints.Builder()
                        .setRequiredNetworkType(if (wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED).build())
                    .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                    .addTag(TAG).addTag(NAME_TAG + name).addTag(SIZE_TAG + dest.length()).addTag(VIS_TAG + visibility)
                    .build()
                wm.enqueueUniqueWork("upload-$workId", ExistingWorkPolicy.KEEP, req)
                n++
            }
            n
        }

        /** Cancel a queued/running upload: stops the job, drops the cached copy and the server's partial file. */
        suspend fun cancel(ctx: Context, workId: UUID) {
            WorkManager.getInstance(ctx).cancelWorkById(workId)
            File(ctx.cacheDir, "uploads/$workId").deleteRecursively()
            val sid = sessions(ctx).getString(workId.toString(), null) ?: return
            sessions(ctx).edit().remove(workId.toString()).apply()
            runCatching { ctx.container.uploads.cancel(sid) }
        }

        /** Re-queue a failed upload whose cached copy is still there (network give-up); resumes its session. */
        fun retry(ctx: Context, workId: UUID, name: String, visibility: String): Boolean {
            val newId = UUID.randomUUID()
            val dir = File(ctx.cacheDir, "uploads/$newId")
            if (!File(ctx.cacheDir, "uploads/$workId").renameTo(dir)) return false
            val file = dir.listFiles()?.firstOrNull() ?: return false
            val sp = sessions(ctx)
            sp.getString(workId.toString(), null)?.let { sp.edit().remove(workId.toString()).putString(newId.toString(), it).apply() }
            val req = OneTimeWorkRequestBuilder<UploadWorker>()
                .setId(newId)
                .setInputData(workDataOf(KEY_FILE to file.path, KEY_NAME to name, KEY_VISIBILITY to visibility))
                .setConstraints(Constraints.Builder()
                    .setRequiredNetworkType(if (wifiOnly(ctx)) NetworkType.UNMETERED else NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .addTag(TAG).addTag(NAME_TAG + name).addTag(SIZE_TAG + file.length()).addTag(VIS_TAG + visibility)
                .build()
            WorkManager.getInstance(ctx).enqueueUniqueWork("upload-$newId", ExistingWorkPolicy.KEEP, req)
            return true
        }

        fun hasCachedCopy(ctx: Context, workId: UUID) = File(ctx.cacheDir, "uploads/$workId").listFiles()?.isNotEmpty() == true
    }
}
