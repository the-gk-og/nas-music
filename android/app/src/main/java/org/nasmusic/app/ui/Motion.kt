package org.nasmusic.app.ui

import android.content.Context
import android.provider.Settings
import androidx.compose.animation.core.FiniteAnimationSpec
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.runtime.staticCompositionLocalOf

/** Settings → Appearance → Animations (docs/v3-ux.md §1), clamped by the OS "remove animations". */
enum class MotionLevel(val key: String, val label: String, val baseMs: Int) {
    OFF("off", "Off", 0),
    QUICK("quick", "Quick", 200),
    EXPRESSIVE("expressive", "Expressive", 420);

    val enabled: Boolean get() = this != OFF
    val expressive: Boolean get() = this == EXPRESSIVE

    fun <T> spec(factor: Float = 1f): FiniteAnimationSpec<T> = if (baseMs == 0) snap() else tween((baseMs * factor).toInt())

    companion object {
        fun from(key: String, osScale: Float): MotionLevel =
            if (osScale == 0f) OFF else entries.firstOrNull { it.key == key } ?: QUICK

        fun osAnimatorScale(context: Context): Float =
            runCatching { Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) }.getOrDefault(1f)
    }
}

val LocalMotion = staticCompositionLocalOf { MotionLevel.QUICK }
