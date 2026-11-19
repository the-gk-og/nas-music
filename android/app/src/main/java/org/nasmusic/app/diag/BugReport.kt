package org.nasmusic.app.diag

import android.os.Build
import android.os.Process
import java.io.File
import kotlinx.serialization.Serializable
import org.nasmusic.app.BuildConfig

@Serializable
data class CrashFile(val name: String, val text: String)

/**
 * Body of `POST /me/bug-report` (the server stores it for the owner; no third-party services).
 * Written before the server's contract had it: field names chosen to be self-explanatory.
 */
@Serializable
data class BugReportBody(
    val description: String,
    val appVersion: String,
    val versionCode: Int,
    val device: String,
    val androidVersion: String,
    val sdk: Int,
    val screen: String,
    /** Route, Connect and network state at the time of the report. */
    val state: Map<String, String> = emptyMap(),
    /** The app's last ~2000 log lines (logcat of this process). */
    val log: String,
    val crashes: List<CrashFile> = emptyList(),
)

@Serializable
data class BugReportResponse(val id: String? = null)

object BugReport {
    const val MAX_LOG_LINES = 2000
    private const val MAX_CRASH_CHARS = 60_000

    /** This app's own logcat (apps may read their own log lines without any permission). */
    fun readLog(lines: Int = MAX_LOG_LINES): String = runCatching {
        val p = ProcessBuilder("logcat", "-d", "-v", "threadtime", "-t", lines.toString(), "--pid", Process.myPid().toString())
            .redirectErrorStream(true).start()
        p.inputStream.bufferedReader().use { it.readText() }.also { p.waitFor() }
    }.getOrElse { "(couldn't read the log: ${it.message})" }

    fun lastLines(text: String, n: Int = MAX_LOG_LINES): String = text.lines().takeLast(n).joinToString("\n")

    fun build(description: String, screen: String, state: Map<String, String>, log: String, crashFiles: List<File>): BugReportBody =
        BugReportBody(
            description = description.trim(),
            appVersion = BuildConfig.VERSION_NAME,
            versionCode = BuildConfig.VERSION_CODE,
            device = "${Build.MANUFACTURER} ${Build.MODEL}".trim(),
            androidVersion = Build.VERSION.RELEASE ?: "?",
            sdk = Build.VERSION.SDK_INT,
            screen = screen,
            state = state,
            log = lastLines(log),
            crashes = crashFiles.take(10).map { CrashFile(it.name, runCatching { it.readText() }.getOrDefault("").take(MAX_CRASH_CHARS)) },
        )
}
