package org.nasmusic.wear.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.ScalingLazyListScope
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.material.Chip
import androidx.wear.compose.material.ChipDefaults
import androidx.wear.compose.material.CircularProgressIndicator
import androidx.wear.compose.material.Colors
import androidx.wear.compose.material.Icon
import androidx.wear.compose.material.ListHeader
import androidx.wear.compose.material.MaterialTheme
import androidx.wear.compose.material.PositionIndicator
import androidx.wear.compose.material.Scaffold
import androidx.wear.compose.material.Text
import androidx.wear.compose.material.TimeText
import androidx.wear.compose.material.Vignette
import androidx.wear.compose.material.VignettePosition
import coil3.compose.AsyncImage
import org.nasmusic.wear.playback.WearMediaItems

val NasColors = Colors(
    primary = Color(0xFF8AB4F8),
    primaryVariant = Color(0xFF1E3A5F),
    secondary = Color(0xFFA8C7FA),
    onPrimary = Color(0xFF0B1D33),
)

@Composable
fun NasTheme(content: @Composable () -> Unit) = MaterialTheme(colors = NasColors, content = content)

/** A scrolling screen with the time on top, the scroll indicator and the round-screen vignette. */
@Composable
fun ListScreen(content: ScalingLazyListScope.() -> Unit) {
    val state = rememberScalingLazyListState()
    Scaffold(
        timeText = { TimeText() },
        vignette = { Vignette(vignettePosition = VignettePosition.TopAndBottom) },
        positionIndicator = { PositionIndicator(scalingLazyListState = state) },
    ) {
        ScalingLazyColumn(
            modifier = Modifier.fillMaxSize(),
            state = state,
            contentPadding = PaddingValues(horizontal = 8.dp, vertical = 28.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            content = content,
        )
    }
}

fun ScalingLazyListScope.header(text: String) = item {
    ListHeader { Text(text, textAlign = TextAlign.Center, maxLines = 2, overflow = TextOverflow.Ellipsis) }
}

fun ScalingLazyListScope.message(text: String) = item {
    Text(
        text,
        modifier = Modifier.fillMaxWidth(),
        textAlign = TextAlign.Center,
        style = MaterialTheme.typography.caption2,
        color = MaterialTheme.colors.onSurfaceVariant,
    )
}

fun ScalingLazyListScope.loading() = item { CircularProgressIndicator(modifier = Modifier.size(32.dp)) }

@Composable
fun NavChip(
    label: String,
    secondary: String? = null,
    icon: ImageVector? = null,
    artAlbumId: String? = null,
    primary: Boolean = false,
    enabled: Boolean = true,
    onClick: () -> Unit,
) = Chip(
    onClick = onClick,
    enabled = enabled,
    modifier = Modifier.fillMaxWidth(),
    colors = if (primary) ChipDefaults.primaryChipColors() else ChipDefaults.secondaryChipColors(),
    label = { Text(label, maxLines = 1, overflow = TextOverflow.Ellipsis) },
    secondaryLabel = secondary?.let { { Text(it, maxLines = 1, overflow = TextOverflow.Ellipsis) } },
    icon = when {
        artAlbumId != null -> { { Art(artAlbumId, Modifier.size(ChipDefaults.LargeIconSize)) } }
        icon != null -> { { Icon(icon, contentDescription = null, modifier = Modifier.size(ChipDefaults.IconSize)) } }
        else -> null
    },
)

@Composable
fun Art(albumId: String?, modifier: Modifier = Modifier, size: Int = 96) {
    Box(modifier.clip(RoundedCornerShape(6.dp))) {
        if (!albumId.isNullOrBlank()) {
            AsyncImage(
                model = WearMediaItems.artUrl(albumId, size),
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

fun formatTime(ms: Long): String {
    val s = (ms / 1000).coerceAtLeast(0)
    return "%d:%02d".format(s / 60, s % 60)
}
