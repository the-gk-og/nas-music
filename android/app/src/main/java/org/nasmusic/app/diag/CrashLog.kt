package org.nasmusic.app.diag

import android.content.Context
import android.os.Build
import android.util.Log
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter
import java.time.Instant
import kotlinx.coroutines.CoroutineExceptionHandler
import org.nasmusic.app.BuildConfig

/**
 * Local crash log (no third-party crash services). Fatal crashes are written to
 * `files/crashes/crash-<time>.txt` before the process dies; non-fatal errors caught by the
 * coroutine handlers are logged and kept in the same folder as `error-<time>.txt`.
 * Both go into the in-app bug report.
 */
object CrashLog {
    const val TAG = "nas-music"
    private const val KEEP = 15
    @Volatile private var dir: File? = null

    /** Last screen the user was on (set by the UI), for crash and bug reports. */
    @Volatile var currentScreen: String = "?"

    fun install(context: Context) {
        dir = File(context.filesDir, "crashes").apply { mkdirs() }
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, e ->
            runCatching { write("crash", thread.name, e) }
            previous?.uncaughtException(thread, e)
        }
    }

    /** For coroutine scopes: log instead of killing the app. */
    val handler = CoroutineExceptionHandler { _, e -> nonFatal("coroutine", e) }

    fun nonFatal(where: String, e: Throwable) {
        Log.e(TAG, "Non-fatal error in $where", e)
        runCatching { write("error", Thread.currentThread().name, e, where) }
    }

    fun format(kind: String, thread: String, e: Throwable, where: String? = null, now: Instant = Instant.now()): String {
        val sw = StringWriter()
        e.printStackTrace(PrintWriter(sw))
        return buildString {
            appendLine("$kind at $now")
            appendLine("app ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE}) · ${Build.MANUFACTURER} ${Build.MODEL} · Android ${Build.VERSION.RELEASE} (SDK ${Build.VERSION.SDK_INT})")
            appendLine("thread $thread · screen $currentScreen" + (where?.let { " · in $it" } ?: ""))
            append(sw)
        }
    }

    private fun write(kind: String, thread: String, e: Throwable, where: String? = null) {
        val d = dir ?: return
        File(d, "$kind-${System.currentTimeMillis()}.txt").writeText(format(kind, thread, e, where))
        d.listFiles()?.sortedByDescending { it.lastModified() }?.drop(KEEP)?.forEach { it.delete() }
    }

    /** Saved crash/error logs, newest first. */
    fun files(): List<File> = dir?.listFiles()?.filter { it.isFile }?.sortedByDescending { it.lastModified() }.orEmpty()

    fun hasUnsentCrash(): Boolean = files().any { it.name.startsWith("crash-") }

    fun markSent() {
        val d = dir ?: return
        val sent = File(d, "sent").apply { mkdirs() }
        files().forEach { it.renameTo(File(sent, it.name)) }
        sent.listFiles()?.sortedByDescending { it.lastModified() }?.drop(KEEP)?.forEach { it.delete() }
    }
}
