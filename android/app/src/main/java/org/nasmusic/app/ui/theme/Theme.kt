package org.nasmusic.app.ui.theme

import android.os.Build
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

private val Dark = darkColorScheme(
    primary = Color(0xFF8FD3B6),
    onPrimary = Color(0xFF00382A),
    secondary = Color(0xFFB4CCBF),
    background = Color(0xFF0F1412),
    surface = Color(0xFF0F1412),
    surfaceVariant = Color(0xFF1F2925),
)

/** Always dark (it is a music player); uses Material You colours on Android 12+. */
@Composable
fun NasMusicTheme(content: @Composable () -> Unit) {
    val scheme = if (Build.VERSION.SDK_INT >= 31) dynamicDarkColorScheme(LocalContext.current) else Dark
    MaterialTheme(colorScheme = scheme, content = content)
}
