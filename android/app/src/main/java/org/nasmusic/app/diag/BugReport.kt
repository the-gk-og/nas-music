package org.nasmusic.app.diag

import android.os.Build
import android.os.Process
import java.io.File
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.nasmusic.app.BuildConfig

/**
 * Body of `POST /me/bug-report` — `BugReportIn` in api/openapi.yaml. The server keeps it for 30 days and
 * shows it in the admin console (no third-party services). Total body ≤ 2 MB on the server.
 */
@Serializable
data class BugReportBody(
    val description: String? = null,
    /** ≤ 50 chars. */
    val appVersion: String? = null,
    /** ≤ 100 chars, e.g. "Android 15 · Google Pixel 8". */
    val platform: String? = null,
    /** The app's own recent logcat. */
    val logs: String,
    /** Saved crash / error stack traces, newest first. */
    val crash: String? = null,
    /** Screen, route, Connect and playback state, device details. */
    val extra: Map<String, String>? = null,
)

@Serializable
data class BugReportResponse(val id: String? = null)

object BugReport {
    const val MAX_LOG_LINES = 2000
    /** Under the server's 2 MB limit with room to spare (JSON escaping, headers, proxies). */
    const val MAX_BODY_BYTES = 1_500_000
    const val MAX_CRASH_CHARS = 400_000
    private const val MAX_CRASH_FILE_CHARS = 60_000

    /** This app's own logcat (apps may read their own log lines without any permission). */
    fun readLog(lines: Int = MAX_LOG_LINES): String = runCatching {
        val p = ProcessBuilder("logcat", "-d", "-v", "threadtime", "-t", lines.toString(), "--pid", Process.myPid().toString())
            .redirectErrorStream(true).start()
        p.inputStream.bufferedReader().use { it.readText() }.also { p.waitFor() }
    }.getOrElse { "(couldn't read the log: ${it.message})" }

    fun lastLines(text: String, n: Int = MAX_LOG_LINES): String = text.lines().takeLast(n).joinToString("\n")

    /** Crash files joined newest first, each capped, the whole capped at [MAX_CRASH_CHARS]; null if none. */
    fun joinCrashes(files: List<Pair<String, String>>): String? {
        if (files.isEmpty()) return null
        return files.take(10).joinToString("\n\n") { (name, text) -> "===== $name =====\n" + text.take(MAX_CRASH_FILE_CHARS) }
            .take(MAX_CRASH_CHARS)
    }

    fun build(description: String, screen: String, state: Map<String, String>, log: String, crashFiles: List<File>): BugReportBody {
        val device = "${Build.MANUFACTURER} ${Build.MODEL}".trim()
        return compose(
            description = description,
            appVersion = "${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})",
            platform = "Android ${Build.VERSION.RELEASE ?: "?"} · $device",
            extra = mapOf("screen" to screen, "device" to device, "sdk" to Build.VERSION.SDK_INT.toString(),
                "flavor" to BuildConfig.FLAVOR) + state,
            log = log,
            crashes = crashFiles.map { it.name to runCatching { it.readText() }.getOrDefault("") },
        )
    }

    /** Pure part of [build]: field limits from the contract, and the whole body kept under [maxBytes]. */
    fun compose(
        description: String, appVersion: String, platform: String, extra: Map<String, String>,
        log: String, crashes: List<Pair<String, String>>, maxBytes: Int = MAX_BODY_BYTES,
    ): BugReportBody = fit(
        BugReportBody(
            description = description.trim().take(5000).ifEmpty { null },
            appVersion = appVersion.take(50),
            platform = platform.take(100),
            logs = lastLines(log),
            crash = joinCrashes(crashes),
            extra = extra,
        ),
        maxBytes,
    )

    fun encode(body: BugReportBody): String = json.encodeToString(BugReportBody.serializer(), body)

    fun encodedSize(body: BugReportBody): Int = encode(body).toByteArray(Charsets.UTF_8).size

    /** Drops the oldest log lines (then crash text) until the JSON body fits. */
    fun fit(body: BugReportBody, maxBytes: Int = MAX_BODY_BYTES): BugReportBody {
        var b = body
        repeat(40) {
            val over = encodedSize(b) - maxBytes
            if (over <= 0) return b
            b = if (b.logs.length > 1000) {
                val cut = (over + over / 4 + 1024).coerceAtMost(b.logs.length)
                b.copy(logs = "(older log lines trimmed)\n" + b.logs.substring(cut).substringAfter('\n'))
            } else {
                val c = b.crash.orEmpty()
                b.copy(logs = b.logs.takeLast(1000), crash = c.take((c.length - over - 1024).coerceAtLeast(0)).ifEmpty { null })
            }
        }
        return b
    }

    private val json = Json { explicitNulls = false; encodeDefaults = true }

    /** A message for the user from a failed report: the server's `detail` if it sent one. */
    fun failureMessage(code: Int, errorBody: String?): String {
        val detail = errorBody?.let { s ->
            runCatching { Json.parseToJsonElement(s).jsonObject["detail"]?.jsonPrimitive?.content }.getOrNull()
        }?.takeIf { it.isNotBlank() }
        return when {
            code == 404 || code == 405 -> "Your server doesn't accept bug reports yet. Update the server."
            code == 401 || code == 403 -> "The server refused the report (${detail ?: "not signed in"}). Re-pair the app."
            detail != null -> "The server refused the report: $detail (HTTP $code)."
            else -> "Sending failed (HTTP $code)."
        }
    }

    /** Errors where sending the same report later can work. */
    fun retryable(code: Int): Boolean = code == 408 || code == 429 || code >= 500
}
