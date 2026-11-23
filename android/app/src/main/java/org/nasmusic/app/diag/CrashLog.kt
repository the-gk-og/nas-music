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
 *
 * Only an uncaught exception writes a `crash-` file: a normal exit (swipe away, the system killing a
 * backgrounded app, the playback service stopping) never runs the handler, so it is never a "crash".
 * The "it crashed last time" prompt shows once per new crash: [markCrashesSeen] (dismiss) or
 * [markSent] (sent or queued) records the newest crash the user has seen.
 */
object CrashLog {
    const val TAG = "nas-music"
    private const val KEEP = 15
    @Volatile private var dir: File? = null
    @Volatile private var seenFile: File? = null

    /** Last screen the user was on (set by the UI), for crash and bug reports. */
    @Volatile var currentScreen: String = "?"

    fun install(context: Context) {
        dir = File(context.filesDir, "crashes").apply { mkdirs() }
        seenFile = File(context.filesDir, "crash-prompt-seen")
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
        d.listFiles()?.filter { it.isFile }?.sortedByDescending { it.lastModified() }?.drop(KEEP)?.forEach { it.delete() }
    }

    /** Saved crash/error logs, newest first. */
    fun files(): List<File> = dir?.listFiles()?.filter { it.isFile }?.sortedByDescending { it.lastModified() }.orEmpty()

    private fun seenUpTo(): Long = runCatching { seenFile?.readText()?.trim()?.toLong() }.getOrNull() ?: 0L

    /** True when there is a crash the user hasn't been asked about yet (see [CrashPrompt.shouldPrompt]). */
    fun hasUnsentCrash(): Boolean = CrashPrompt.shouldPrompt(files().map { it.name }, seenUpTo(), System.currentTimeMillis())

    /** The newest unseen crash file (shown in the prompt), if any. */
    fun newestCrash(): File? = files().firstOrNull { (CrashPrompt.crashTime(it.name) ?: 0L) > seenUpTo() }

    /** The user dismissed the crash prompt: don't ask again until there is a newer crash. */
    fun markCrashesSeen() {
        val newest = CrashPrompt.newestCrashTime(files().map { it.name }) ?: return
        if (newest > seenUpTo()) runCatching { seenFile?.writeText(newest.toString()) }
    }

    /** The logs went into a report (sent or queued): move them out of the next report and the prompt. */
    fun markSent() {
        markCrashesSeen()
        val d = dir ?: return
        val sent = File(d, "sent").apply { mkdirs() }
        files().forEach { it.renameTo(File(sent, it.name)) }
        sent.listFiles()?.sortedByDescending { it.lastModified() }?.drop(KEEP)?.forEach { it.delete() }
    }
}

/** Pure "should the app offer a bug report after a crash?" logic (unit-tested). */
object CrashPrompt {
    /** Crashes older than this are not worth an interruption any more (they still go into manual reports). */
    const val MAX_AGE_MS = 7L * 24 * 60 * 60 * 1000
    private val CRASH = Regex("""^crash-(\d+)\.txt$""")

    /** The time a `crash-<millis>.txt` file was written, or null for anything else (`error-` files, `sent/`…). */
    fun crashTime(name: String): Long? = CRASH.matchEntire(name)?.groupValues?.get(1)?.toLongOrNull()

    fun newestCrashTime(names: List<String>): Long? = names.mapNotNull(::crashTime).maxOrNull()

    fun shouldPrompt(names: List<String>, seenUpTo: Long, now: Long, maxAgeMs: Long = MAX_AGE_MS): Boolean =
        names.mapNotNull(::crashTime).any { it > seenUpTo && now - it <= maxAgeMs }
}
