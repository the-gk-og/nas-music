@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package org.homebeat.app.ui.screens

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.dp
import kotlin.math.abs
import kotlin.math.ln
import kotlin.math.pow
import kotlin.math.roundToInt
import org.homebeat.app.AppContainer
import org.homebeat.app.eq.EqBand
import org.homebeat.app.eq.EqEngine
import org.homebeat.app.eq.EqSettings

private const val F_MIN = 20.0
private const val F_MAX = 20_000.0
private const val DB_RANGE = 18.0

private fun freqToX(f: Double, w: Float) = (ln(f / F_MIN) / ln(F_MAX / F_MIN) * w).toFloat()
private fun xToFreq(x: Float, w: Float) = F_MIN * (F_MAX / F_MIN).pow((x / w).coerceIn(0f, 1f).toDouble())
private fun dbToY(db: Double, h: Float) = ((DB_RANGE - db.coerceIn(-DB_RANGE, DB_RANGE)) / (2 * DB_RANGE) * h).toFloat()
private fun yToDb(y: Float, h: Float) = DB_RANGE - (y / h).coerceIn(0f, 1f) * 2 * DB_RANGE

private fun freqLabel(f: Double) = if (f >= 1000) "%.1f k".format(f / 1000).replace(".0 k", " k") else "${f.roundToInt()}"

/** Parametric EQ: response curve with draggable band handles, band chips, and the selected band's controls. */
@Composable
fun ParametricEqPage(c: AppContainer, eq: EqSettings) {
    var sel by rememberSaveable { mutableIntStateOf(0) }
    val bands = eq.parametric
    if (sel > bands.lastIndex) sel = bands.lastIndex.coerceAtLeast(0)
    fun set(i: Int, b: EqBand) = c.eq.update { it.withParam(i, b).copy(enabled = true) }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp)) {
        ResponseCurve(eq, sel, onSelect = { sel = it }, onDrag = { i, b -> set(i, b) })
        Row(Modifier.horizontalScroll(rememberScrollState()).padding(vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            bands.forEachIndexed { i, b ->
                FilterChip(
                    selected = i == sel, onClick = { sel = i },
                    label = { Text("${i + 1} · ${EqBand.TYPE_LABELS[b.type]} ${freqLabel(b.fc)}", color = if (b.on) Color.Unspecified else Color.Gray) },
                )
            }
            if (bands.size < EqSettings.MAX_PARAM_BANDS) {
                OutlinedButton(onClick = {
                    // New bands start flat at a free-ish spot so adding one never changes the sound.
                    val f = listOf(1000.0, 250.0, 4000.0, 60.0, 10_000.0, 500.0, 2000.0).firstOrNull { f -> bands.none { abs(ln(it.fc / f)) < 0.3 } } ?: 1000.0
                    c.eq.update { it.copy(parametric = it.parametric + EqBand("PK", f, 0.0, 1.0), enabled = true) }
                    sel = bands.size
                }) { Icon(Icons.Default.Add, null); Text(" Band") }
            }
        }
        bands.getOrNull(sel)?.let { b -> BandEditor(b, onChange = { set(sel, it) }, onDelete = {
            c.eq.update { it.copy(parametric = it.parametric.filterIndexed { j, _ -> j != sel }) }
        }) }
        if (bands.isEmpty()) Text("No bands yet. Add one, or start from below.", color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClick = { c.eq.update { it.copy(parametric = EqBand.fromGraphic(it.bandsDb), enabled = true) }; sel = 0 }) { Text("Copy sliders") }
            eq.headphone?.let { hp ->
                // Moves the AutoEq correction into editable bands (and turns the separate correction off, so it isn't doubled).
                TextButton(onClick = { c.eq.update { it.copy(parametric = EqBand.fromAutoEq(hp).take(EqSettings.MAX_PARAM_BANDS), headphone = null, enabled = true) }; sel = 0 }) {
                    Text("Edit ${hp.name} correction")
                }
            }
            if (bands.isNotEmpty()) TextButton(onClick = { c.eq.update { it.copy(parametric = emptyList()) } }) { Text("Clear") }
        }
    }
}

@Composable
private fun ResponseCurve(eq: EqSettings, sel: Int, onSelect: (Int) -> Unit, onDrag: (Int, EqBand) -> Unit) {
    val accent = MaterialTheme.colorScheme.primary
    val grid = MaterialTheme.colorScheme.outline.copy(alpha = 0.4f)
    val freqs = remember { DoubleArray(160) { i -> F_MIN * (F_MAX / F_MIN).pow(i / 159.0) } }
    val curve = remember(eq) { EqEngine.curveDb(eq, freqs) }
    val headroom = remember(eq) { EqEngine.headroomDb(eq) }
    val bands by rememberUpdatedState(eq.parametric)
    val selState by rememberUpdatedState(sel)
    val select by rememberUpdatedState(onSelect)
    val drag by rememberUpdatedState(onDrag)
    var dragging by remember { mutableStateOf(-1) }

    fun nearest(p: Offset, w: Float, h: Float): Int = bands.indices.minByOrNull { i ->
        val b = bands[i]
        val dx = freqToX(b.fc, w) - p.x; val dy = dbToY(if (b.isPass) 0.0 else b.gainDb, h) - p.y
        dx * dx + dy * dy
    } ?: -1

    Box {
        Canvas(
            Modifier.fillMaxWidth().height(220.dp)
                .pointerInput(Unit) { detectTapGestures { p -> nearest(p, size.width.toFloat(), size.height.toFloat()).takeIf { it >= 0 }?.let(select) } }
                .pointerInput(Unit) {
                    detectDragGestures(
                        onDragStart = { p -> dragging = nearest(p, size.width.toFloat(), size.height.toFloat()); if (dragging >= 0) select(dragging) },
                        onDragEnd = { dragging = -1 }, onDragCancel = { dragging = -1 },
                    ) { change, _ ->
                        change.consume()
                        val i = if (dragging >= 0) dragging else selState
                        val b = bands.getOrNull(i) ?: return@detectDragGestures
                        val w = size.width.toFloat(); val h = size.height.toFloat()
                        val f = xToFreq(change.position.x, w).let { (it * 10).roundToInt() / 10.0 }
                        val g = if (b.isPass) b.gainDb else (yToDb(change.position.y, h) * 2).roundToInt() / 2.0
                        drag(i, b.copy(fc = f, gainDb = g))
                    }
                },
        ) {
            val w = size.width; val h = size.height
            for (f in listOf(50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0, 5000.0, 10_000.0)) {
                val x = freqToX(f, w); drawLine(grid, Offset(x, 0f), Offset(x, h))
            }
            for (db in listOf(-12.0, -6.0, 0.0, 6.0, 12.0)) {
                val y = dbToY(db, h); drawLine(if (db == 0.0) grid.copy(alpha = 0.9f) else grid, Offset(0f, y), Offset(w, y))
            }
            val path = Path()
            freqs.forEachIndexed { i, f ->
                val p = Offset(freqToX(f, w), dbToY(curve[i], h))
                if (i == 0) path.moveTo(p.x, p.y) else path.lineTo(p.x, p.y)
            }
            drawPath(path, accent, style = Stroke(2.5.dp.toPx()))
            eq.parametric.forEachIndexed { i, b ->
                val p = Offset(freqToX(b.fc, w), dbToY(if (b.isPass) 0.0 else b.gainDb, h))
                val col = if (!b.on) Color.Gray else if (i == sel) accent else Color.White
                drawCircle(col, if (i == sel) 9.dp.toPx() else 6.dp.toPx(), p)
            }
        }
        Text(
            if (headroom < -0.05) "Auto headroom %.1f dB".format(headroom) else "",
            style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.align(Alignment.TopEnd).padding(4.dp),
        )
    }
}

@Composable
private fun BandEditor(b: EqBand, onChange: (EqBand) -> Unit, onDelete: () -> Unit) {
    var types by remember { mutableStateOf(false) }
    Column {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box {
                OutlinedButton(onClick = { types = true }) { Text(EqBand.TYPE_LABELS[b.type] ?: b.type) }
                DropdownMenu(types, { types = false }) {
                    EqBand.TYPES.forEach { t ->
                        DropdownMenuItem({ Text(EqBand.TYPE_LABELS[t] ?: t) }, {
                            types = false
                            // Pass filters start Butterworth; peaks start where they were.
                            onChange(if (t == "LP" || t == "HP") b.copy(type = t, q = 0.707) else b.copy(type = t))
                        })
                    }
                }
            }
            Text("On", Modifier.padding(start = 16.dp, end = 8.dp))
            Switch(b.on, { onChange(b.copy(on = it)) })
            Box(Modifier.weight(1f))
            IconButton(onClick = onDelete) { Icon(Icons.Default.Delete, "Remove band") }
        }
        LabeledSlider("Frequency", "${freqLabel(b.fc)} Hz", (ln(b.fc / F_MIN) / ln(F_MAX / F_MIN)).toFloat()) { v ->
            onChange(b.copy(fc = (F_MIN * (F_MAX / F_MIN).pow(v.toDouble()) * 10).roundToInt() / 10.0))
        }
        if (!b.isPass) {
            LabeledSlider("Gain", "%+.1f dB".format(b.gainDb), ((b.gainDb + EqBand.MAX_GAIN) / (2 * EqBand.MAX_GAIN)).toFloat()) { v ->
                onChange(b.copy(gainDb = ((v * 2 * EqBand.MAX_GAIN - EqBand.MAX_GAIN) * 2).roundToInt() / 2.0))
            }
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Slope", Modifier.width(90.dp))
                EqBand.SLOPES.forEach { s ->
                    FilterChip(selected = b.slope == s, onClick = { onChange(b.copy(slope = s)) }, label = { Text("$s dB/oct") }, modifier = Modifier.padding(end = 6.dp))
                }
            }
        }
        if (!b.isPass || b.slope == 12) {
            // Q on a log scale, 0.1 … 20.
            LabeledSlider("Q", "%.2f".format(b.q), (ln(b.q / 0.1) / ln(200.0)).toFloat()) { v ->
                onChange(b.copy(q = (0.1 * 200.0.pow(v.toDouble()) * 100).roundToInt() / 100.0))
            }
        }
    }
}

@Composable
private fun LabeledSlider(label: String, value: String, fraction: Float, onChange: (Float) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.width(90.dp))
        Slider(fraction.coerceIn(0f, 1f), onChange, Modifier.weight(1f))
        Text(value, Modifier.width(76.dp).padding(start = 8.dp), style = MaterialTheme.typography.bodySmall)
    }
}
