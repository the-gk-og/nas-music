package org.homebeat.app.playback

import android.app.UiModeManager
import android.content.Context
import android.content.res.Configuration
import android.media.AudioManager
import org.homebeat.app.data.api.RadioContext
import org.homebeat.app.data.api.Track
import org.homebeat.app.eq.OutputDetector
import java.time.LocalDateTime

/** AI radio helpers (api 0.6): smart-shuffle context and applying a steer to the local player. */
object AiRadio {
    /** Steering actions the server knows. */
    const val MORE_LIKE = "more_like"
    const val LESS_LIKE = "less_like"
    const val MORE_ENERGY = "more_energy"
    const val CALMER = "calmer"
    const val MORE_VARIETY = "more_variety"
    const val MORE_FAMILIAR = "more_familiar"
    const val NEVER_HERE = "never_here"
    const val REMOVE = "remove"
    const val RESET = "reset"

    val NEEDS_TRACK = setOf(MORE_LIKE, LESS_LIKE, NEVER_HERE)

    val VIBE_EXAMPLES = listOf("chill rainy evening", "gym, no slow songs", "focus, no lyrics", "2000s throwbacks")

    /** Hints for "Play something for now": local time, and car when Android Auto / car mode is on. */
    fun context(ctx: Context, now: LocalDateTime = LocalDateTime.now(), forceCar: Boolean = false): RadioContext {
        val car = forceCar || runCatching {
            ctx.getSystemService(UiModeManager::class.java)?.currentModeType == Configuration.UI_MODE_TYPE_CAR
        }.getOrDefault(false)
        val output = runCatching {
            OutputDetector.contextLabel(OutputDetector.current(ctx.getSystemService(AudioManager::class.java)))
        }.getOrNull()
        return contextFor(now, car, output)
    }

    fun contextFor(now: LocalDateTime, car: Boolean, output: String?): RadioContext = RadioContext(
        localHour = now.hour,
        weekday = now.dayOfWeek.value,
        deviceKind = if (car) "car" else "phone",
        output = if (car) "car" else output,
    )

    /**
     * After a steer the server keeps the queue up to the current track and the next one (which may already be
     * buffered) and replaces the rest. Returns what to append locally after removing our items past index + 1.
     */
    fun upcomingAfterSteer(localIds: List<String>, server: List<Track>, index: Int): List<Track> {
        val keep = localIds.take((index + 2).coerceAtLeast(0)).toSet()
        return server.drop((index + 2).coerceAtLeast(0)).filter { it.id !in keep }
    }
}
