package org.homebeat.app.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val Accent = Color(0xFF4ADE80)

/** True-black dark theme with one accent (green by default; docs/v3-ux.md §1, §7). */
private fun scheme(accent: Color) = darkColorScheme(
    primary = accent,
    onPrimary = Color(0xFF00210E),
    secondary = accent,
    onSecondary = Color(0xFF00210E),
    tertiary = accent,
    background = Color.Black,
    onBackground = Color.White,
    surface = Color.Black,
    onSurface = Color.White,
    surfaceVariant = Color(0xFF1C1C1C),
    onSurfaceVariant = Color(0xFFB8B8B8),
    surfaceContainer = Color(0xFF121212),
    surfaceContainerHigh = Color(0xFF222222),
    surfaceContainerHighest = Color(0xFF2A2A2A),
    surfaceContainerLow = Color(0xFF0E0E0E),
    outline = Color(0xFF5A5A5A),
    outlineVariant = Color(0xFF333333),
)

@Composable
fun HomebeatTheme(accent: Color = Accent, content: @Composable () -> Unit) =
    MaterialTheme(colorScheme = scheme(accent), content = content)
