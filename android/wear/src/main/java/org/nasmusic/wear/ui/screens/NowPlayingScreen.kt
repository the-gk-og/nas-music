package org.nasmusic.wear.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.QueueMusic
import androidx.compose.material.icons.automirrored.filled.VolumeDown
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.SkipPrevious
import androidx.compose.material.icons.filled.Speaker
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.rotary.onRotaryScrollEvent
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.wear.compose.material.Button
import androidx.wear.compose.material.ButtonDefaults
import androidx.wear.compose.material.CircularProgressIndicator
import androidx.wear.compose.material.Icon
import androidx.wear.compose.material.MaterialTheme
import androidx.wear.compose.material.Scaffold
import androidx.wear.compose.material.Text
import androidx.wear.compose.material.TimeText
import kotlinx.coroutines.delay
import org.nasmusic.wear.WearContainer
import org.nasmusic.wear.nowplaying.rememberLyricLine
import org.nasmusic.wear.sleep.SleepStatus
import org.nasmusic.wear.sleep.label
import org.nasmusic.wear.sleep.rememberSleepControls
import org.nasmusic.wear.ui.Art

/**
 * Now playing, here or on another device (then the watch is a Connect remote): cover, title, where
 * it plays, the lyric line being sung (synced lyrics only), a progress ring around the edge,
 * prev / play-pause / next, and volume on the crown/bezel.
 */
@Composable
fun NowPlayingScreen(c: WearContainer, openQueue: () -> Unit, openDevices: () -> Unit) {
    val ui by c.player.ui.collectAsState()
    val connected by c.connect.connected.collectAsState()
    val sleep by c.sleep.status.collectAsState()
    val sleepControls = rememberSleepControls(c)
    val lyricLine = rememberLyricLine(c.api, ui.track?.id, ui.positionMs)
    LaunchedEffect(Unit) {
        c.player.connectLocal()
        while (true) { c.player.refreshLocal(); delay(1_000) }
    }
    val focus = remember { FocusRequester() }
    val rotaryAcc = remember { mutableFloatStateOf(0f) }
    LaunchedEffect(Unit) { focus.requestFocus() }

    Scaffold(timeText = { TimeText() }) {
        Box(
            Modifier
                .fillMaxSize()
                .onRotaryScrollEvent { e ->
                    // ~one volume step per bezel click / short crown turn.
                    rotaryAcc.floatValue += e.verticalScrollPixels
                    val steps = (rotaryAcc.floatValue / 40f).toInt()
                    if (steps != 0) { rotaryAcc.floatValue -= steps * 40f; c.player.volumeBy(steps) }
                    true
                }
                .focusRequester(focus)
                .focusable(),
            contentAlignment = Alignment.Center,
        ) {
            val progress = if (ui.durationMs > 0) (ui.positionMs.toFloat() / ui.durationMs).coerceIn(0f, 1f) else 0f
            CircularProgressIndicator(
                progress = progress,
                modifier = Modifier.fillMaxSize().padding(2.dp),
                startAngle = 300f,
                endAngle = 240f,
                strokeWidth = 4.dp,
            )
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
                modifier = Modifier.padding(horizontal = 22.dp),
            ) {
                Spacer(Modifier.height(14.dp))
                Art(ui.track?.albumId, Modifier.size(40.dp))
                Spacer(Modifier.height(4.dp))
                Text(
                    ui.track?.title ?: "Nothing playing",
                    style = MaterialTheme.typography.title3,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center,
                )
                Text(
                    when {
                        ui.track == null -> if (connected) "Pick something from Browse" else "Connecting…"
                        ui.remoteDevice != null -> "${ui.track?.artist.orEmpty()} · on ${ui.remoteDevice?.name}"
                        else -> ui.track?.artist.orEmpty()
                    },
                    style = MaterialTheme.typography.caption2,
                    color = MaterialTheme.colors.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center,
                )
                if (lyricLine != null) {
                    Text(
                        lyricLine,
                        style = MaterialTheme.typography.caption2,
                        color = MaterialTheme.colors.primary,
                        maxLines = 2, overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(2.dp))
                } else {
                    Spacer(Modifier.height(6.dp))
                }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Small(Icons.Filled.SkipPrevious, "Previous") { c.player.prev() }
                    Button(
                        onClick = { c.player.playPause() },
                        modifier = Modifier.size(ButtonDefaults.DefaultButtonSize),
                    ) {
                        Icon(if (ui.playing) Icons.Filled.Pause else Icons.Filled.PlayArrow, if (ui.playing) "Pause" else "Play")
                    }
                    Small(Icons.Filled.SkipNext, "Next") { c.player.next() }
                }
                Spacer(Modifier.height(4.dp))
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    Tiny(Icons.AutoMirrored.Filled.VolumeDown, "Volume down") { c.player.volumeBy(-1) }
                    Tiny(Icons.AutoMirrored.Filled.QueueMusic, "Queue", onClick = openQueue)
                    Tiny(Icons.Filled.Speaker, "Play on…", onClick = openDevices)
                    Tiny(Icons.AutoMirrored.Filled.VolumeUp, "Volume up") { c.player.volumeBy(1) }
                }
                Text(
                    "Vol ${(ui.volume * 100).toInt()}%",
                    style = MaterialTheme.typography.caption3,
                    color = MaterialTheme.colors.onSurfaceVariant,
                    modifier = Modifier.alpha(0.8f),
                )
                // Sleep detection is watching (or just paused): tap to stop it.
                val sleepLabel = sleep.label()
                if (sleepLabel != null && sleep !is SleepStatus.Paused) {
                    Text(
                        "☾ $sleepLabel",
                        style = MaterialTheme.typography.caption3,
                        color = MaterialTheme.colors.secondary,
                        maxLines = 1, overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center,
                        modifier = Modifier.clickable { sleepControls.stop() },
                    )
                }
            }
        }
    }
}

@Composable
private fun Small(icon: ImageVector, desc: String, onClick: () -> Unit) =
    Button(onClick = onClick, modifier = Modifier.size(ButtonDefaults.SmallButtonSize), colors = ButtonDefaults.secondaryButtonColors()) {
        Icon(icon, desc)
    }

@Composable
private fun Tiny(icon: ImageVector, desc: String, onClick: () -> Unit) =
    Button(onClick = onClick, modifier = Modifier.size(ButtonDefaults.ExtraSmallButtonSize), colors = ButtonDefaults.secondaryButtonColors()) {
        Icon(icon, desc, modifier = Modifier.size(16.dp))
    }
