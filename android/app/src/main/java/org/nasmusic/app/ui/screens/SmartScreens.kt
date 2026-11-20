package org.nasmusic.app.ui.screens

import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Radio
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.NewSmartPlaylist
import org.nasmusic.app.data.api.RadioSeed
import org.nasmusic.app.data.api.SmartDefinition
import org.nasmusic.app.data.api.SmartFieldSpec
import org.nasmusic.app.data.api.SmartFields
import org.nasmusic.app.data.api.SmartLimit
import org.nasmusic.app.data.api.SmartNode
import org.nasmusic.app.data.api.SmartPatch
import org.nasmusic.app.data.api.SmartPlaylist
import org.nasmusic.app.data.api.SmartPreview
import org.nasmusic.app.data.api.SmartPreviewRequest
import org.nasmusic.app.data.smart.SmartRules
import org.nasmusic.app.playback.PlaySource
import org.nasmusic.app.ui.LocalUi
import org.nasmusic.app.ui.Loading
import org.nasmusic.app.ui.Message
import org.nasmusic.app.ui.ScreenTitle
import org.nasmusic.app.ui.songItems

/** api 0.7 smart playlist: rules summary + the server-evaluated, auto-updating track list. */
@Composable
fun SmartPlaylistScreen(c: AppContainer, id: String) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var sp by remember { mutableStateOf<SmartPlaylist?>(null) }
    var fields by remember { mutableStateOf<List<SmartFieldSpec>>(emptyList()) }
    var failed by remember { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    LaunchedEffect(id) {
        sp = runCatching { c.api.smartPlaylist(id) }.onFailure { failed = true }.getOrNull()
        fields = runCatching { c.api.smartFields().fields }.getOrDefault(emptyList())
    }
    if (failed && sp == null) return Message("Smart playlists need a connection to the server.")
    val p = sp ?: return Loading()
    val name = "Smart playlist: ${p.name}"
    Column(Modifier.fillMaxSize()) {
        ScreenTitle(p.name) {
            IconButton(onClick = { if (p.tracks.isNotEmpty()) c.player.play(p.tracks, 0, PlaySource.LIBRARY, name) }) { Icon(Icons.Default.PlayArrow, "Play") }
            IconButton(onClick = { if (p.tracks.isNotEmpty()) c.player.play(p.tracks.shuffled(), 0, PlaySource.LIBRARY, name) }) { Icon(Icons.Default.Shuffle, "Shuffle") }
            IconButton(onClick = {
                scope.launch { if (c.player.startRadio(RadioSeed("smart_playlist", id))) ui.go("now") else ui.toast = "Couldn't start the radio" }
            }) { Icon(Icons.Default.Radio, "Smart playlist radio") }
            Box {
                IconButton(onClick = { menu = true }) { Icon(Icons.Default.MoreVert, "More") }
                DropdownMenu(menu, { menu = false }) {
                    DropdownMenuItem({ Text("Edit rules") }, { menu = false; ui.go("smart-edit/$id") })
                    DropdownMenuItem({ Text("Pin to Home") }, { menu = false; scope.launch { runCatching { c.api.pin(org.nasmusic.app.data.api.NewPin("smart_playlist", id)) }; ui.toast = "Pinned to Home" } })
                    KeepOfflineItem(c, "smart_playlist", id, p.name, p.tracks) { menu = false }
                    DropdownMenuItem({ Text("Delete") }, {
                        menu = false
                        scope.launch { runCatching { c.api.deleteSmartPlaylist(id) }; ui.nav.popBackStack() }
                    })
                }
            }
        }
        Text(
            "${p.trackCount} songs · ${SmartRules.duration(p.durationS)}" + if (fields.isNotEmpty()) "\n${SmartRules.describe(p.definition, fields)}" else "",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
        )
        if (p.tracks.isEmpty()) Message("No songs match these rules right now. The list updates by itself as you listen.")
        else LazyColumn(Modifier.fillMaxSize()) { songItems(c, p.tracks, name, PlaySource.LIBRARY) }
    }
}

/** A small dropdown: the current label as a button, options in a menu. */
@Composable
private fun Pick(current: String, options: List<Pair<String, String>>, onPick: (String) -> Unit, modifier: Modifier = Modifier) {
    var open by remember { mutableStateOf(false) }
    Box(modifier) {
        OutlinedButton(onClick = { open = true }, contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 10.dp)) {
            Text(options.firstOrNull { it.first == current }?.second ?: current, maxLines = 1)
            Icon(Icons.Default.ArrowDropDown, null)
        }
        DropdownMenu(open, { open = false }) {
            options.forEach { (k, label) -> DropdownMenuItem({ Text(label) }, { open = false; onPick(k) }) }
        }
    }
}

@Composable
private fun NumberField(value: Double?, label: String, percent: Boolean, onChange: (Double) -> Unit) {
    val shown = value?.takeIf { !it.isNaN() }?.let { if (percent) (it * 100).toInt().toString() else if (it == Math.floor(it)) it.toLong().toString() else it.toString() }.orEmpty()
    var text by remember(shown) { mutableStateOf(shown) }
    OutlinedTextField(
        text, { t -> text = t; onChange(t.toDoubleOrNull()?.let { if (percent) it / 100 else it } ?: Double.NaN) },
        label = { Text(if (percent) "$label %" else label) }, singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal), modifier = Modifier.width(110.dp),
    )
}

@Composable
private fun RuleRow(r: SmartNode, cat: SmartFields, playlists: List<Pair<String, String>>, onChange: (SmartNode) -> Unit, onRemove: () -> Unit) {
    val f = cat.fields.firstOrNull { it.id == r.field }
    val bad = SmartRules.incomplete(listOf(r), cat.fields)
    Column(
        Modifier.fillMaxWidth().padding(vertical = 4.dp)
            .border(1.dp, if (bad) Color(0xFFCC5555) else MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(10.dp)).padding(8.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.horizontalScroll(rememberScrollState())) {
            Pick(r.field.orEmpty(), cat.fields.map { it.id to it.label }, { id -> cat.fields.firstOrNull { it.id == id }?.let { onChange(SmartRules.retarget(r, it)) } })
            if (f != null) Pick(r.op.orEmpty(), f.ops.map { it to (SmartRules.OP_LABELS[it] ?: it) }, { onChange(SmartRules.retarget(r, f, it)) })
            Spacer(Modifier.weight(1f, fill = false))
            IconButton(onClick = onRemove) { Icon(Icons.Default.Close, "Remove rule") }
        }
        if (f != null && r.op != "never" && r.op != "ever") Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            when (f.type) {
                "number", "percent" -> {
                    val pct = f.type == "percent"
                    if (r.op == "between") {
                        val p = r.pair ?: listOf(Double.NaN, Double.NaN)
                        NumberField(p.getOrNull(0), "From", pct) { onChange(r.copy(value = JsonArray(listOf(SmartRules.num(it), SmartRules.num(p.getOrElse(1) { Double.NaN }))))) }
                        NumberField(p.getOrNull(1), "To", pct) { onChange(r.copy(value = JsonArray(listOf(SmartRules.num(p.getOrElse(0) { Double.NaN }), SmartRules.num(it))))) }
                    } else NumberField(r.num, f.unit ?: "Value", pct) { onChange(r.copy(value = if (it.isNaN()) JsonNull else SmartRules.num(it))) }
                }
                "text" -> {
                    var t by remember(r.field) { mutableStateOf(r.text.orEmpty()) }
                    OutlinedTextField(t, { t = it; onChange(r.copy(value = JsonPrimitive(it))) }, label = { Text(f.label) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                }
                "date" -> if (r.op == "inLast" || r.op == "notInLast") {
                    NumberField(r.num, "How many", false) { onChange(r.copy(value = if (it.isNaN()) JsonNull else SmartRules.num(it))) }
                    Pick(r.unit ?: "days", cat.units.map { it to it }, { onChange(r.copy(unit = it)) })
                } else {
                    var t by remember(r.op) { mutableStateOf(r.text.orEmpty()) }
                    OutlinedTextField(t, { t = it; onChange(r.copy(value = JsonPrimitive(it))) }, label = { Text("YYYY-MM-DD") }, singleLine = true)
                }
                "bool" -> Pick(if (r.bool == true) "yes" else "no", listOf("yes" to "yes", "no" to "no"), { onChange(r.copy(value = JsonPrimitive(it == "yes"))) })
                "enum" -> Pick(r.text.orEmpty(), f.options.map { it to if (f.id == "decade") "${it}s" else it }, { onChange(r.copy(value = JsonPrimitive(it))) })
                "playlist" -> Pick(r.text.orEmpty().ifEmpty { "Choose a playlist" }, playlists, { onChange(r.copy(value = JsonPrimitive(it))) })
            }
        }
    }
}

/**
 * Rule editor. `param`: "new", "preset:<id>", or the id of the smart playlist to edit.
 * Simple rows (field · operator · value), one level of any/all groups, sort, limit and a live preview count.
 */
@Composable
fun SmartEditorScreen(c: AppContainer, param: String) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val editingId = param.takeUnless { it == "new" || it.startsWith("preset:") || it.isBlank() }
    var cat by remember { mutableStateOf<SmartFields?>(null) }
    var name by remember { mutableStateOf("") }
    var def by remember { mutableStateOf(SmartDefinition()) }
    var preset by remember { mutableStateOf<String?>(null) }
    var preview by remember { mutableStateOf<SmartPreview?>(null) }
    var previewErr by remember { mutableStateOf<String?>(null) }
    var playlists by remember { mutableStateOf<List<Pair<String, String>>>(emptyList()) }
    var failed by remember { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }

    fun usePreset(id: String) {
        val p = cat?.presets?.firstOrNull { it.id == id } ?: return
        def = p.definition; preset = p.id
        if (name.isBlank() || cat?.presets?.any { it.name == name } == true) name = p.name
    }
    LaunchedEffect(param) {
        cat = runCatching { c.api.smartFields() }.onFailure { failed = true }.getOrNull()
        if (param.startsWith("preset:")) usePreset(param.removePrefix("preset:"))
        else if (editingId != null) runCatching { c.api.smartPlaylist(editingId) }.onSuccess { name = it.name; def = it.definition; preset = it.preset }
        playlists = runCatching { c.api.playlists().map { it.id to it.name } }.getOrDefault(emptyList()) +
            runCatching { c.api.smartPlaylists().filter { it.id != editingId }.map { it.id to "${it.name} (smart)" } }.getOrDefault(emptyList())
    }
    LaunchedEffect(def, cat) {
        val fields = cat?.fields ?: return@LaunchedEffect
        if (SmartRules.incomplete(def.rules, fields)) { previewErr = "Finish the highlighted rules to see matching songs"; return@LaunchedEffect }
        delay(400)
        runCatching { c.api.previewSmartPlaylist(SmartPreviewRequest(def, editingId, 5)) }
            .onSuccess { preview = it; previewErr = null }
            .onFailure { previewErr = it.message ?: "Couldn't count" }
    }
    if (failed) return Message("Smart playlists need a connection to the server.")
    val k = cat ?: return Loading()

    fun setRules(rules: List<SmartNode>) { def = def.copy(rules = rules) }

    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        item { ScreenTitle(if (editingId != null) "Edit smart playlist" else "New smart playlist") }
        item { OutlinedTextField(name, { name = it }, label = { Text("Name") }, singleLine = true, modifier = Modifier.fillMaxWidth()) }
        if (editingId == null && k.presets.isNotEmpty()) item {
            Text("Start from", style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(top = 12.dp))
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                k.presets.forEach { p -> FilterChip(preset == p.id, { usePreset(p.id) }, { Text(p.name) }) }
            }
        }
        item {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 12.dp)) {
                Text("Match ")
                Pick(def.match, listOf("all" to "all", "any" to "any"), { def = def.copy(match = it) })
                Text(" of these rules")
            }
        }
        def.rules.forEachIndexed { i, r ->
            item(key = "r$i") {
                if (r.isGroup) Card(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                    Column(Modifier.padding(8.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("Match ")
                            Pick(r.match ?: "any", listOf("all" to "all", "any" to "any"), { m -> setRules(def.rules.toMutableList().also { it[i] = r.copy(match = m) }) })
                            Text(" of", modifier = Modifier.weight(1f))
                            IconButton(onClick = { setRules(def.rules.filterIndexed { j, _ -> j != i }) }) { Icon(Icons.Default.Close, "Remove group") }
                        }
                        r.rules.orEmpty().forEachIndexed { j, sub ->
                            RuleRow(sub, k, playlists, { n ->
                                setRules(def.rules.toMutableList().also { it[i] = r.copy(rules = r.rules.orEmpty().toMutableList().also { l -> l[j] = n }) })
                            }, { setRules(def.rules.toMutableList().also { it[i] = r.copy(rules = r.rules.orEmpty().filterIndexed { x, _ -> x != j }) }) })
                        }
                        TextButton(onClick = {
                            val f = k.fields.firstOrNull { it.id == "playCount" } ?: k.fields.first()
                            setRules(def.rules.toMutableList().also { it[i] = r.copy(rules = r.rules.orEmpty() + SmartRules.newRule(f)) })
                        }) { Icon(Icons.Default.Add, null); Text(" Add rule") }
                    }
                }
                else RuleRow(r, k, playlists, { n -> setRules(def.rules.toMutableList().also { it[i] = n }) }, { setRules(def.rules.filterIndexed { j, _ -> j != i }) })
            }
        }
        item {
            Row {
                TextButton(onClick = {
                    val f = k.fields.firstOrNull { it.id == "playCount" } ?: k.fields.first()
                    setRules(def.rules + SmartRules.newRule(f))
                }) { Icon(Icons.Default.Add, null); Text(" Add rule") }
                TextButton(onClick = {
                    val f = k.fields.firstOrNull { it.id == "favourite" } ?: k.fields.first()
                    setRules(def.rules + SmartNode(match = "any", rules = listOf(SmartRules.newRule(f))))
                }) { Icon(Icons.Default.Add, null); Text(" Add group") }
            }
        }
        item {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 8.dp)) {
                Text("Sort")
                Pick(def.sort, k.sorts.map { it.id to it.label }, { def = def.copy(sort = it) })
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 8.dp)) {
                Text("Limit")
                Pick(def.limit.type, listOf("none" to "No limit", "songs" to "Songs", "minutes" to "Minutes"), {
                    def = def.copy(limit = if (it == "none") SmartLimit() else SmartLimit(it, def.limit.value ?: if (it == "songs") 50 else 60))
                })
                if (def.limit.type != "none") NumberField(def.limit.value?.toDouble(), def.limit.type, false) {
                    def = def.copy(limit = def.limit.copy(value = it.takeIf { v -> !v.isNaN() && v >= 1 }?.toInt() ?: 1))
                }
            }
        }
        item {
            Card(Modifier.fillMaxWidth().padding(vertical = 12.dp)) {
                Column(Modifier.padding(12.dp)) {
                    val p = preview
                    when {
                        previewErr != null -> Text(previewErr!!, color = Color(0xFFE0A040))
                        p == null -> Text("Counting…")
                        else -> {
                            Text("${p.trackCount} songs · ${SmartRules.duration(p.durationS)}", style = MaterialTheme.typography.titleMedium)
                            p.tracks.forEach { t -> Text("${t.title} — ${t.artist}", style = MaterialTheme.typography.bodySmall, maxLines = 1) }
                        }
                    }
                }
            }
        }
        item {
            Row(Modifier.fillMaxWidth().padding(bottom = 32.dp), horizontalArrangement = Arrangement.End) {
                TextButton(onClick = { ui.nav.popBackStack() }) { Text("Cancel") }
                Spacer(Modifier.width(8.dp))
                Button(enabled = !saving, onClick = {
                    if (name.isBlank()) { ui.toast = "Give it a name"; return@Button }
                    if (SmartRules.incomplete(def.rules, k.fields)) { ui.toast = "Some rules are not finished"; return@Button }
                    saving = true
                    scope.launch {
                        runCatching {
                            if (editingId != null) c.api.updateSmartPlaylist(editingId, SmartPatch(name.trim(), def))
                            else c.api.createSmartPlaylist(NewSmartPlaylist(name.trim(), def, preset))
                        }.onSuccess { sp ->
                            ui.nav.popBackStack()
                            if (editingId == null) ui.go("smart/${sp.id}")
                        }.onFailure { ui.toast = it.message ?: "Couldn't save" }
                        saving = false
                    }
                }) { Icon(Icons.Default.Edit, null); Text(if (editingId != null) " Save" else " Create") }
            }
        }
    }
}
