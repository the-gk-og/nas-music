package org.nasmusic.app.ui.screens

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.Radio
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import java.time.LocalTime
import kotlinx.coroutines.launch
import org.nasmusic.app.AppContainer
import org.nasmusic.app.data.api.ShelfFeed
import org.nasmusic.app.data.api.RadioSeed
import org.nasmusic.app.data.api.Shelf
import org.nasmusic.app.data.api.ToGetItem
import org.nasmusic.app.data.api.ToGetPatch
import org.nasmusic.app.ui.LocalUi
import org.nasmusic.app.ui.Loading
import org.nasmusic.app.ui.Message
import org.nasmusic.app.ui.QuickPicks
import org.nasmusic.app.ui.ShelfArt
import org.nasmusic.app.ui.ShelfHeader
import org.nasmusic.app.ui.ShelfRow

/** Top bar shared by Home / For You / Library: title, search shortcut, profile avatar menu. */
@Composable
fun TopBar(c: AppContainer, title: String) {
    val ui = LocalUi.current
    val cfg by c.secureStore.config.collectAsStateWithLifecycle()
    var menu by remember { mutableStateOf(false) }
    Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 8.dp, top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
        IconButton(onClick = { ui.go("search") }) { Icon(Icons.Default.Search, "Search") }
        Box {
            Box(
                Modifier.size(34.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primary).clickable { menu = true },
                contentAlignment = Alignment.Center,
            ) { Text((cfg?.username ?: "?").take(1).uppercase(), color = MaterialTheme.colorScheme.onPrimary, fontWeight = FontWeight.Bold) }
            DropdownMenu(menu, { menu = false }) {
                listOf("Settings" to "settings", "Equalizer" to "equalizer", "Devices" to null, "Data & recommendations" to "data", "Account" to "account")
                    .plus(if (cfg?.isAdmin == true) listOf("Admin" to "admin") else emptyList())
                    .forEach { (label, route) ->
                        DropdownMenuItem({ Text(label) }, { menu = false; if (route == null) ui.devicesOpen = true else ui.go(route) })
                    }
            }
        }
    }
}

/** Shelf kinds this app knows how to draw; others are skipped (contract rule). */
private val KNOWN_KINDS = setOf(
    "start_radio", "devices", "recently_played", "continue_listening", "pinned", "mixes", "radio", "because_you_played",
    "release_radar", "suggestions", "to_get", "blends", "wrapped",
)

private fun greeting(): String = when (LocalTime.now().hour) {
    in 5..11 -> "Good morning"
    in 12..17 -> "Good afternoon"
    else -> "Good evening"
}

// ---------- Home (docs/v3-ux.md §3.1) ----------

@Composable
fun HomeScreen(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val cfg by c.secureStore.config.collectAsStateWithLifecycle()
    val settings by c.settings.settings.collectAsStateWithLifecycle()
    val devices by c.connect.devices.collectAsStateWithLifecycle()
    val state by c.connect.state.collectAsStateWithLifecycle()
    val me by c.connect.myDeviceId.collectAsStateWithLifecycle()
    var feed by remember { mutableStateOf<ShelfFeed?>(null) }
    var failed by remember { mutableStateOf(false) }
    var reload by remember { mutableStateOf(0) }
    val route by c.router.route.collectAsStateWithLifecycle()
    val offline = route == org.nasmusic.app.net.Route.OFFLINE
    // Reload when the connection comes back; while offline, don't even try (no error state, just downloads).
    LaunchedEffect(reload, offline) {
        if (offline) return@LaunchedEffect
        failed = false
        feed = runCatching { c.feeds.home() }.onFailure { failed = true }.getOrNull()
    }
    // Start radio and devices are drawn locally (devices are live over Connect); the rest by kind.
    val byKind = feed?.shelves.orEmpty().filter { it.kind != "start_radio" && it.kind != "devices" }.associateBy { it.kind }

    LazyColumn(Modifier.fillMaxSize()) {
        item { TopBar(c, "${greeting()}${cfg?.username?.let { ", $it" } ?: ""}") }
        if (offline || (failed && feed == null)) offlineHomeItems(c)
        settings.homeShelves.filterNot { it.startsWith("-") }.forEach { id ->
            when (id) {
                "start_radio" -> item(key = id) {
                    Button(
                        onClick = { scope.launch { if (c.player.startRadio(RadioSeed("random"))) ui.go("now") else ui.toast = "Couldn't start the radio" } },
                        modifier = Modifier.fillMaxWidth().padding(16.dp).height(64.dp),
                        shape = RoundedCornerShape(16.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.primary),
                    ) { Icon(Icons.Default.Radio, null); Spacer(Modifier.width(12.dp)); Text("Start a random radio", style = MaterialTheme.typography.titleMedium) }
                }
                "devices" -> {
                    val others = devices.filter { it.online && it.id != me }
                    if (others.isNotEmpty()) item(key = id) {
                        ShelfHeader("Your devices")
                        LazyRow(contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            items(others, key = { it.id }) { d ->
                                val active = state?.activeDeviceId == d.id
                                OutlinedCard(onClick = { c.player.transferHere() }, modifier = Modifier.width(200.dp)) {
                                    Column(Modifier.padding(12.dp)) {
                                        Row(verticalAlignment = Alignment.CenterVertically) {
                                            Icon(Icons.Default.Devices, null, tint = if (active) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
                                            Text(d.name, Modifier.padding(start = 8.dp), maxLines = 1, overflow = TextOverflow.Ellipsis)
                                        }
                                        Text(
                                            if (active && state?.playing == true) "Playing now · tap to take over here" else if (active) "Paused · tap to take over" else "Idle",
                                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
                else -> byKind[id]?.takeIf { it.items.isNotEmpty() }?.let { shelf -> item(key = "k:$id") { HomeShelf(c, shelf) { reload++ } } }
            }
        }
        // Shelves the server adds that the app doesn't know yet still show, after the known ones.
        feed?.shelves.orEmpty().filter { s -> settings.homeShelves.none { it.removePrefix("-") == s.kind } && s.items.isNotEmpty() && s.kind in KNOWN_KINDS }
            .forEachIndexed { i, s -> item(key = "x:$i:${s.id}") { HomeShelf(c, s) { reload++ } } }
        if (feed == null && !failed && !offline) item { Box(Modifier.height(200.dp)) { Loading() } }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

@Composable
private fun HomeShelf(c: AppContainer, shelf: Shelf, onChanged: () -> Unit) {
    val scope = rememberCoroutineScope()
    ShelfHeader(shelf.title)
    when {
        shelf.layout == "grid2" || shelf.kind == "recently_played" -> QuickPicks(c, shelf.items)
        shelf.kind == "pinned" -> ShelfRow(c, shelf.items, onUnpin = { item ->
            scope.launch { runCatching { c.api.unpin(item.type, item.id) }; onChanged() }
        })
        else -> ShelfRow(c, shelf.items)
    }
}

// ---------- For You (docs/v3-ux.md §3.2) ----------

@Composable
fun ForYouScreen(c: AppContainer) {
    val ui = LocalUi.current
    var feed by remember { mutableStateOf<ShelfFeed?>(null) }
    var failed by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { feed = runCatching { c.feeds.forYou() }.onFailure { failed = true }.getOrNull() }
    LazyColumn(Modifier.fillMaxSize()) {
        item { TopBar(c, "For You") }
        feed?.shelves.orEmpty().filter { it.items.isNotEmpty() && it.kind in KNOWN_KINDS }.forEachIndexed { i, shelf ->
            item(key = "$i:${shelf.id}") {
                ShelfHeader(shelf.title) {
                    when (shelf.kind) {
                        "to_get" -> TextButton(onClick = { ui.go("toget") }) { Text("See all") }
                        "suggestions" -> TextButton(onClick = { ui.go("discover") }) { Text("See all") }
                        "mixes" -> TextButton(onClick = { ui.go("mixes") }) { Text("See all") }
                        "release_radar" -> TextButton(onClick = { ui.go("release-radar") }) { Text("See all") }
                    }
                }
                shelf.subtitle?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(start = 16.dp, bottom = 6.dp)) }
                if (shelf.layout == "grid2") QuickPicks(c, shelf.items) else ShelfRow(c, shelf.items, cardWidth = if (shelf.kind == "mixes" || shelf.kind == "wrapped") 160.dp else 140.dp)
            }
        }
        if (feed == null && !failed) item { Box(Modifier.height(200.dp)) { Loading() } }
        if (failed) item { Message("For You needs a connection to the server.") }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

// ---------- To get (docs/v3-ux.md §4) ----------

@Composable
fun ToGetScreen(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var list by remember { mutableStateOf<List<ToGetItem>?>(null) }
    var filter by rememberSaveable { mutableStateOf("want") }
    suspend fun reload() { list = runCatching { c.api.toGet(status = "all") }.getOrElse { ui.toast = "Couldn't load To get"; emptyList() } }
    LaunchedEffect(Unit) { reload() }
    fun patch(t: ToGetItem, status: String) = scope.launch { runCatching { c.api.patchToGet(t.id, ToGetPatch(status = status)) }; reload() }

    Column(Modifier.fillMaxSize()) {
        TopBar(c, "To get")
        Row(Modifier.padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("want" to "Want", "got" to "Got", "dismissed" to "Dismissed").forEach { (k, label) ->
                FilterChip(filter == k, { filter = k }, label = { Text(label) })
            }
        }
        val shown = list?.filter { it.status == filter }
        when {
            shown == null -> Loading()
            shown.isEmpty() -> Message(if (filter == "want") "Nothing yet. Search for music you don't own and tap \"Add to To get\"." else "Nothing here.")
            else -> LazyColumn(Modifier.fillMaxSize()) {
                items(shown, key = { it.id }) { t ->
                    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        ShelfArt(org.nasmusic.app.data.api.ShelfItem("toget", t.id, t.title, artUrl = t.artUrl), 56.dp)
                        Column(Modifier.weight(1f).padding(horizontal = 12.dp)) {
                            Text(t.title, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(listOfNotNull(t.artist, t.type, t.year?.toString()).joinToString(" · "), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            t.note?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                            Row {
                                t.links.forEach { l ->
                                    if (l.url != null) TextButton(onClick = {
                                        runCatching { c.context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(l.url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
                                    }) { Text(l.name ?: "Link") }
                                }
                            }
                        }
                        if (t.status != "got") IconButton(onClick = { patch(t, "got") }) { Icon(Icons.Default.Check, "Mark as got") }
                        if (t.status == "want") IconButton(onClick = { patch(t, "dismissed") }) { Icon(Icons.Default.Close, "Dismiss") }
                        else IconButton(onClick = { scope.launch { runCatching { c.api.deleteToGet(t.id) }; reload() } }) { Icon(Icons.Default.Delete, "Delete") }
                    }
                }
            }
        }
    }
}
