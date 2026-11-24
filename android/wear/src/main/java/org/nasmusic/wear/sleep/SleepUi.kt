package org.nasmusic.wear.sleep

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import org.nasmusic.wear.WearContainer

/** Starts/stops sleep detection from the UI, asking for heart rate first if needed. */
class SleepControls(private val c: WearContainer, private val ask: (startAfter: Boolean) -> Unit) {
    private val granted: Boolean
        get() = c.context.checkSelfPermission(Manifest.permission.BODY_SENSORS) == PackageManager.PERMISSION_GRANTED

    fun start() = if (granted) SleepWatchService.start(c.context) else ask(true)

    /** When the setting is switched on: ask for heart rate while the user is looking, without starting. */
    fun requestHeartRate() { if (!granted) ask(false) }

    fun stop() {
        c.sleep.noAutoUntilMs = System.currentTimeMillis() + 6 * 60 * 60_000L
        SleepWatchService.stop(c.context)
    }

    fun toggle() = if (c.sleep.watching) stop() else start()
}

@Composable
fun rememberSleepControls(c: WearContainer): SleepControls {
    val ctx = LocalContext.current
    val startAfter = remember { booleanArrayOf(false) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (!granted) c.ui.toast("No heart rate: going by stillness only, which is slower")
        if (startAfter[0]) SleepWatchService.start(ctx)
    }
    return remember(c, launcher) {
        SleepControls(c) { after -> startAfter[0] = after; launcher.launch(Manifest.permission.BODY_SENSORS) }
    }
}

/** Short status for a chip or caption, or null when off. */
fun SleepStatus.label(): String? = when (this) {
    SleepStatus.Off -> null
    is SleepStatus.Watching -> if (stillSinceMs != null) "Watching for sleep · you're still" else "Watching for sleep"
    SleepStatus.Fading -> "Dozed off? Fading out…"
    is SleepStatus.Paused -> "Paused: you fell asleep"
}
