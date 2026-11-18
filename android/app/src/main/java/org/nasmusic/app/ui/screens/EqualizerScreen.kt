package org.nasmusic.app.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Bluetooth
import androidx.compose.material.icons.filled.Headphones
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.Speaker
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.TrackChanges
import androidx.compose.material.icons.filled.Waves
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin
import org.nasmusic.app.AppContainer
import org.nasmusic.app.eq.EqSettings
import org.nasmusic.app.ui.LocalUi

/** Equalizer per audio output (docs/v2.md §6): 10-band page and effects page. */
@Composable
fun EqualizerScreen(c: AppContainer) {
    val ui = LocalUi.current
    val eq by c.eq.edited.collectAsStateWithLifecycle()
    val editing by c.eq.editing.collectAsStateWithLifecycle()
    val output by c.eq.output.collectAsStateWithLifecycle()
    val now by c.player.state.collectAsStateWithLifecycle()
    var page by rememberSaveable { mutableIntStateOf(0) }
    var outputs by remember { mutableStateOf(false) }

    Column(Modifier.fillMaxSize()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { ui.nav.popBackStack() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
            Text("Equalizer", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.padding(start = 16.dp))
        }
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp).border(BorderStroke(1.dp, MaterialTheme.colorScheme.outline), RoundedCornerShape(10.dp)),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.weight(1f)) {
                Row(Modifier.clickable { outputs = true }.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(outputIcon(editing.kind), null, tint = MaterialTheme.colorScheme.primary)
                    Text(
                        editing.label + if (editing.key == output.key) "" else "  (not connected)",
                        color = MaterialTheme.colorScheme.primary, modifier = Modifier.weight(1f).padding(start = 12.dp),
                    )
                    Icon(Icons.Default.KeyboardArrowDown, "Choose output")
                }
                DropdownMenu(outputs, { outputs = false }) {
                    c.eq.knownOutputs().forEach { o ->
                        DropdownMenuItem(
                            { Text(o.label + if (o.key == output.key) "  • now" else "") },
                            { outputs = false; c.eq.select(o) },
                            leadingIcon = { Icon(outputIcon(o.kind), null) },
                        )
                    }
                }
            }
            Switch(eq.enabled, { on -> c.eq.update { it.copy(enabled = on) } }, modifier = Modifier.padding(end = 12.dp))
        }
        Box(Modifier.weight(1f)) {
            if (page == 0) BandsPage(c, eq) else EffectsPage(c, eq, now.volume)
        }
        HorizontalDivider(Modifier.padding(horizontal = 80.dp))
        Row(Modifier.fillMaxWidth().padding(8.dp), horizontalArrangement = Arrangement.SpaceEvenly) {
            IconButton(onClick = { page = 0 }) { Icon(Icons.Default.Tune, "Bands", tint = if (page == 0) Color.White else MaterialTheme.colorScheme.onSurfaceVariant) }
            IconButton(onClick = { page = 1 }) { Icon(Icons.Default.TrackChanges, "Effects", tint = if (page == 1) Color.White else MaterialTheme.colorScheme.onSurfaceVariant) }
        }
    }
}

private fun outputIcon(kind: String) = when (kind) {
    "bluetooth" -> Icons.Default.Bluetooth
    "wired" -> Icons.Default.Headphones
    else -> Icons.Default.Speaker
}

@Composable
private fun BandsPage(c: AppContainer, eq: EqSettings) {
    var presets by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedButton(onClick = {}, shape = RoundedCornerShape(50)) { Icon(Icons.Default.Waves, null); Text("  10") }
            Box {
                OutlinedButton(onClick = { presets = true }, shape = RoundedCornerShape(50)) { Text(eq.preset) }
                DropdownMenu(presets, { presets = false }) {
                    (EqSettings.PRESETS.keys + "Custom").forEach { name ->
                        DropdownMenuItem({ Text(name) }, {
                            presets = false
                            EqSettings.PRESETS[name]?.let { bands -> c.eq.update { it.copy(bandsDb = bands, preset = name, enabled = true) } }
                        })
                    }
                }
            }
        }
        Spacer(Modifier.weight(0.3f))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
            EqSettings.BAND_LABELS.forEachIndexed { i, label ->
                Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.weight(1f)) {
                    Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text("%.1f".format(eq.bandsDb[i]), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        Row(Modifier.fillMaxWidth().weight(1f).padding(vertical = 16.dp), horizontalArrangement = Arrangement.SpaceEvenly) {
            eq.bandsDb.forEachIndexed { i, db ->
                VerticalSlider(db, -12f..12f, Modifier.weight(1f).fillMaxHeight()) { v ->
                    c.eq.update { it.withBand(i, (v * 2).roundToInt() / 2f).copy(enabled = true) }
                }
            }
        }
    }
}

/** Vertical fader like the reference: track above the thumb in grey, below it in the accent. */
@Composable
private fun VerticalSlider(value: Float, range: ClosedFloatingPointRange<Float>, modifier: Modifier, onChange: (Float) -> Unit) {
    val accent = MaterialTheme.colorScheme.primary
    val current by rememberUpdatedState(value)
    val onChangeState by rememberUpdatedState(onChange)
    Canvas(
        modifier.pointerInput(Unit) {
            detectVerticalDragGestures { change, _ ->
                change.consume()
                val frac = 1f - (change.position.y / size.height).coerceIn(0f, 1f)
                onChangeState(range.start + frac * (range.endInclusive - range.start))
            }
        },
    ) {
        val x = size.width / 2
        val frac = (current - range.start) / (range.endInclusive - range.start)
        val thumbY = size.height * (1 - frac)
        val thumbH = 56.dp.toPx(); val thumbW = 26.dp.toPx()
        drawLine(Color.Gray, Offset(x, 0f), Offset(x, thumbY), strokeWidth = 2.dp.toPx())
        drawLine(accent, Offset(x, thumbY), Offset(x, size.height), strokeWidth = 2.dp.toPx())
        drawRoundRect(Color.Black, topLeft = Offset(x - thumbW / 2, thumbY - thumbH / 2), size = androidx.compose.ui.geometry.Size(thumbW, thumbH),
            cornerRadius = androidx.compose.ui.geometry.CornerRadius(10.dp.toPx()))
        drawRoundRect(Color.LightGray, topLeft = Offset(x - thumbW / 2, thumbY - thumbH / 2), size = androidx.compose.ui.geometry.Size(thumbW, thumbH),
            cornerRadius = androidx.compose.ui.geometry.CornerRadius(10.dp.toPx()), style = Stroke(1.5.dp.toPx()))
        drawLine(Color.White, Offset(x - thumbW / 3, thumbY), Offset(x + thumbW / 3, thumbY), strokeWidth = 2.dp.toPx())
    }
}

@Composable
private fun EffectsPage(c: AppContainer, eq: EqSettings, volume: Float) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
            KnobWithLabel("Bass boost", "${eq.bassBoost}%", eq.bassBoost / 100f) { f -> c.eq.update { it.copy(bassBoost = (f * 100).roundToInt(), enabled = true) } }
            KnobWithLabel("Surround Sound", "${eq.surround}%", eq.surround / 100f) { f -> c.eq.update { it.copy(surround = (f * 100).roundToInt(), enabled = true) } }
        }
        HorizontalDivider(Modifier.padding(vertical = 16.dp))
        Text("Preamp", fontWeight = FontWeight.Bold)
        Row(Modifier.padding(vertical = 8.dp).border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(8.dp))) {
            listOf(Triple("Headroom", "-6 dB", -6f), Triple("Off", "0 dB", 0f), Triple("Boost", "+1 dB", 1f)).forEach { (name, sub, db) ->
                val sel = eq.preampDb == db
                Column(
                    Modifier.clickable { c.eq.update { it.copy(preampDb = db, enabled = true) } }
                        .then(if (sel) Modifier.border(1.dp, MaterialTheme.colorScheme.primary) else Modifier)
                        .padding(horizontal = 18.dp, vertical = 6.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    val col = if (sel) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant
                    Text(name, color = col); Text(sub, color = col)
                }
            }
        }
        Text("%+.1f dB".format(eq.preampDb), style = MaterialTheme.typography.bodySmall)
        Slider(eq.preampDb, { v -> c.eq.update { it.copy(preampDb = (v * 2).roundToInt() / 2f, enabled = true) } }, valueRange = -12f..6f, modifier = Modifier.padding(horizontal = 24.dp))
        HorizontalDivider(Modifier.padding(vertical = 16.dp))
        Text("Volume", fontWeight = FontWeight.Bold)
        Slider(volume, { c.player.setVolume(it) }, modifier = Modifier.padding(horizontal = 24.dp))
        HorizontalDivider(Modifier.padding(vertical = 16.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly, verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(10.dp)).width(110.dp)) {
                listOf("Stereo" to false, "Mono" to true).forEach { (label, mono) ->
                    val sel = eq.mono == mono
                    Text(
                        label, textAlign = TextAlign.Center,
                        color = if (sel) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.fillMaxWidth()
                            .then(if (sel) Modifier.background(MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)).border(1.dp, MaterialTheme.colorScheme.primary, RoundedCornerShape(10.dp)) else Modifier)
                            .clickable { c.eq.update { it.copy(mono = mono, enabled = true) } }.padding(vertical = 14.dp),
                    )
                }
            }
            val l = ((1 - eq.balance).coerceIn(0f, 1f) * 100).roundToInt()
            val r = ((1 + eq.balance).coerceIn(0f, 1f) * 100).roundToInt()
            KnobWithLabel("Audio channel balance", "Left:$l, Right:$r", (eq.balance + 1) / 2) { f ->
                val b = f * 2 - 1
                c.eq.update { it.copy(balance = if (kotlin.math.abs(b) < 0.04f) 0f else b, enabled = true) }
            }
        }
    }
}

/** Rotary knob: drag up/down (or around) to change 0..1. Indicator sweeps −135°…+135°. */
@Composable
private fun KnobWithLabel(title: String, value: String, fraction: Float, onChange: (Float) -> Unit) {
    val accent = MaterialTheme.colorScheme.primary
    val current by rememberUpdatedState(fraction)
    val onChangeState by rememberUpdatedState(onChange)
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Canvas(
            Modifier.size(120.dp).pointerInput(Unit) {
                detectDragGestures { change, drag ->
                    change.consume()
                    onChangeState((current + (drag.x - drag.y) / 300f).coerceIn(0f, 1f))
                }
            },
        ) {
            val r = size.minDimension / 2 - 4.dp.toPx()
            drawCircle(Color(0xFF1E1E1E), r)
            drawCircle(Color.Gray, r, style = Stroke(2.dp.toPx()))
            val angle = Math.toRadians((-135.0 + 270.0 * current) - 90.0)
            val c0 = Offset(size.width / 2, size.height / 2)
            val inner = Offset(c0.x + (r * 0.55f * cos(angle)).toFloat(), c0.y + (r * 0.55f * sin(angle)).toFloat())
            val outer = Offset(c0.x + (r * 0.8f * cos(angle)).toFloat(), c0.y + (r * 0.8f * sin(angle)).toFloat())
            drawLine(accent, inner, outer, strokeWidth = 3.dp.toPx())
        }
        Text(title, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 8.dp))
        Text(value)
        Spacer(Modifier.height(4.dp))
    }
}
