package org.homebeat.app.ui.screens

import android.content.ContentValues
import android.content.Intent
import android.graphics.Bitmap
import android.os.Build
import android.provider.MediaStore
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.ExitToApp
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.ChevronLeft
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.PersonAdd
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Save
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
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
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil3.SingletonImageLoader
import coil3.request.ImageRequest
import coil3.request.SuccessResult
import coil3.request.allowHardware
import coil3.toBitmap
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.homebeat.app.AppContainer
import org.homebeat.app.data.api.Blend
import org.homebeat.app.data.api.BlendCandidate
import org.homebeat.app.data.api.BlendInvite
import org.homebeat.app.data.api.BlendName
import org.homebeat.app.data.api.NewBlend
import org.homebeat.app.data.api.NewToGet
import org.homebeat.app.data.api.ReleaseRadar
import org.homebeat.app.data.api.ShelfItem
import org.homebeat.app.data.api.WrappedStats
import org.homebeat.app.data.api.releaseRadar
import org.homebeat.app.playback.MediaItems
import org.homebeat.app.playback.PlaySource
import org.homebeat.app.ui.LocalUi
import org.homebeat.app.ui.Loading
import org.homebeat.app.ui.Message
import org.homebeat.app.ui.ShelfArt
import org.homebeat.app.ui.ShelfCard
import org.homebeat.app.ui.ShelfHeader
import org.homebeat.app.ui.wrapped.WrappedRenderer
import org.homebeat.app.ui.wrapped.blendReason
import org.homebeat.app.ui.wrapped.cardAlbumIds
import org.homebeat.app.ui.wrapped.cardColors
import org.homebeat.app.ui.wrapped.releaseDate
import org.homebeat.app.ui.wrapped.wrappedCards
import java.io.File
import java.time.LocalDate

@Composable
private fun BackBar(title: String, actions: @Composable () -> Unit = {}) {
    val ui = LocalUi.current
    Row(Modifier.fillMaxWidth().padding(end = 8.dp, top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = { ui.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
        Text(title, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
        actions()
    }
}

// ---------- Release Radar ----------

@Composable
fun ReleaseRadarScreen(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var rr by remember { mutableStateOf<ReleaseRadar?>(null) }
    var failed by remember { mutableStateOf(false) }
    val added = remember { mutableStateMapOf<String, Boolean>() }
    LaunchedEffect(Unit) { rr = runCatching { c.discovery.releaseRadar() }.onFailure { failed = true }.getOrNull() }
    Column(Modifier.fillMaxSize()) {
        BackBar("Release Radar")
        val r = rr
        when {
            failed -> Message("Release Radar needs a connection to a server that has it.")
            r == null -> Loading()
            else -> LazyColumn(Modifier.fillMaxSize()) {
                item {
                    Text(
                        "New albums, EPs and singles from the artists you play" + (r.updatedAt?.let { " · updated ${releaseDate(it.take(10))}" } ?: " · refreshed every Friday") + ".",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 16.dp),
                    )
                    ShelfHeader("New from artists you play")
                }
                if (r.notOwned.isEmpty()) item { Message("Nothing new right now.") }
                items(r.notOwned, key = { it.id }) { it ->
                    val cat = it.catalog
                    val tgStatus = (cat?.toGet?.get("status") as? kotlinx.serialization.json.JsonPrimitive)?.content
                    val isAdded = added[it.id] ?: (tgStatus != null && tgStatus != "dismissed")
                    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        ShelfArt(it, 56.dp)
                        Column(Modifier.weight(1f).padding(horizontal = 12.dp)) {
                            Text(it.title, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(
                                listOfNotNull(it.subtitle, it.release?.type, releaseDate(it.release?.date).ifEmpty { null }, if (it.release?.upcoming == true) "Upcoming" else null).joinToString(" · "),
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis,
                            )
                        }
                        if (isAdded) Icon(Icons.Default.Check, "In To get", tint = MaterialTheme.colorScheme.primary)
                        else IconButton(onClick = {
                            scope.launch {
                                runCatching { c.api.addToGet(NewToGet(catalogId = it.id)) }
                                    .onSuccess { _ -> added[it.id] = true; ui.toast = "Added \"${it.title}\" to To get" }
                                    .onFailure { ui.toast = "Couldn't add it" }
                            }
                        }) { Icon(Icons.Default.Add, "Add to To get") }
                    }
                }
                if (r.inLibrary.isNotEmpty()) item {
                    ShelfHeader("In your library")
                    LazyRow(contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        items(r.inLibrary, key = { it.id }) { ShelfCard(c, it, 140.dp, r.inLibrary) }
                    }
                }
                item { Spacer(Modifier.height(24.dp)) }
            }
        }
    }
}

// ---------- Wrapped ----------

@OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
@Composable
fun WrappedScreen(c: AppContainer, yearArg: String) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val cfg by c.secureStore.config.collectAsStateWithLifecycle()
    var year by remember { mutableIntStateOf(yearArg.toIntOrNull() ?: LocalDate.now().year) }
    var server by remember { mutableStateOf(false) }
    var years by remember { mutableStateOf(listOf(year)) }
    var data by remember { mutableStateOf<WrappedStats?>(null) }
    var failed by remember { mutableStateOf(false) }
    var index by remember { mutableIntStateOf(0) }
    val covers = remember { mutableStateMapOf<String, Bitmap>() }
    LaunchedEffect(Unit) { runCatching { c.discovery.wrappedYears() }.onSuccess { if (it.isNotEmpty()) years = it } }
    LaunchedEffect(year, server) {
        data = null; failed = false; index = 0
        data = runCatching { if (server) c.discovery.adminWrapped(year) else c.discovery.wrapped(year) }.onFailure { failed = true }.getOrNull()
    }
    val cards = remember(data, server) { data?.let { wrappedCards(it, if (server) "The server's" else "Your") }.orEmpty() }
    LaunchedEffect(cards) {
        val loader = SingletonImageLoader.get(c.context)
        cardAlbumIds(cards).filter { it !in covers }.forEach { id ->
            val req = ImageRequest.Builder(c.context).data(MediaItems.artUrl(id, 256)).allowHardware(false).build()
            (loader.execute(req) as? SuccessResult)?.image?.toBitmap()?.let { covers[id] = it }
        }
    }
    val d = data
    val card = cards.getOrNull(index)
    val bitmap = remember(card, covers.size, d) {
        if (card == null || d == null) null
        else WrappedRenderer.render(card, cardColors(d, index), covers.toMap(), "Homebeat · ${d.year}")
    }

    Column(Modifier.fillMaxSize()) {
        BackBar("Wrapped")
        FlowRow(Modifier.padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            years.forEach { y -> FilterChip(y == year, { year = y }, label = { Text("$y") }) }
            if (cfg?.isAdmin == true) {
                FilterChip(!server, { server = false }, label = { Text("Me") })
                FilterChip(server, { server = true }, label = { Text("Whole server") })
            }
        }
        when {
            failed -> Message("Wrapped needs a connection to a server that has it.")
            bitmap == null -> Loading()
            else -> Column(Modifier.fillMaxSize().padding(16.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Row(Modifier.fillMaxWidth().padding(bottom = 8.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    cards.indices.forEach { i ->
                        Box(Modifier.weight(1f).height(3.dp).clip(RoundedCornerShape(2.dp)).background(if (i <= index) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.surfaceContainerHighest))
                    }
                }
                Box(Modifier.weight(1f).aspectRatio(9f / 16f)) {
                    Image(bitmap.asImageBitmap(), card?.title, Modifier.fillMaxSize().clip(RoundedCornerShape(16.dp)))
                    Row(Modifier.fillMaxSize()) {
                        Box(Modifier.weight(1f).fillMaxSize().clickable { if (index > 0) index-- })
                        Box(Modifier.weight(1f).fillMaxSize().clickable { if (index < cards.size - 1) index++ })
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 8.dp)) {
                    IconButton(onClick = { if (index > 0) index-- }, enabled = index > 0) { Icon(Icons.Default.ChevronLeft, "Previous") }
                    Text("${index + 1} / ${cards.size}", style = MaterialTheme.typography.bodySmall)
                    IconButton(onClick = { if (index < cards.size - 1) index++ }, enabled = index < cards.size - 1) { Icon(Icons.Default.ChevronRight, "Next") }
                    Spacer(Modifier.width(8.dp))
                    OutlinedButton(onClick = { scope.launch { ui.toast = if (saveImage(c, bitmap, "wrapped-${d?.year}-${index + 1}")) "Saved to Pictures" else "Couldn't save the image" } }) {
                        Icon(Icons.Default.Save, null); Spacer(Modifier.width(6.dp)); Text("Save")
                    }
                    Spacer(Modifier.width(8.dp))
                    Button(onClick = { scope.launch { shareImage(c, bitmap, "wrapped-${d?.year}-${index + 1}") } }) {
                        Icon(Icons.Default.Share, null); Spacer(Modifier.width(6.dp)); Text("Share")
                    }
                }
            }
        }
    }
}

/** Pictures/Homebeat via MediaStore (Android 10+; no storage permission). Older phones: use Share. */
private suspend fun saveImage(c: AppContainer, bmp: Bitmap, name: String): Boolean = withContext(Dispatchers.IO) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return@withContext false
    runCatching {
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, "$name.png")
            put(MediaStore.Images.Media.MIME_TYPE, "image/png")
            put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/Homebeat")
        }
        val resolver = c.context.contentResolver
        val uri = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values) ?: return@runCatching false
        resolver.openOutputStream(uri)?.use { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) } ?: return@runCatching false
        true
    }.getOrDefault(false)
}

private suspend fun shareImage(c: AppContainer, bmp: Bitmap, name: String) {
    val uri = withContext(Dispatchers.IO) {
        val dir = File(c.context.cacheDir, "shared").apply { mkdirs() }
        val f = File(dir, "$name.png")
        f.outputStream().use { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) }
        FileProvider.getUriForFile(c.context, "${c.context.packageName}.files", f)
    }
    val send = Intent(Intent.ACTION_SEND).setType("image/png").putExtra(Intent.EXTRA_STREAM, uri)
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    c.context.startActivity(Intent.createChooser(send, "Share Wrapped").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
}

// ---------- Blend ----------

@OptIn(ExperimentalMaterial3Api::class, androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
@Composable
fun BlendScreen(c: AppContainer, id: String) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val cfg by c.secureStore.config.collectAsStateWithLifecycle()
    val me = cfg?.userId
    var blend by remember { mutableStateOf<Blend?>(null) }
    var candidates by remember { mutableStateOf<List<BlendCandidate>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var dialog by remember { mutableStateOf<String?>(null) }   // "invite" | "rename" | "leave"
    var text by remember { mutableStateOf("") }
    val picked = remember { mutableStateMapOf<String, Boolean>() }
    var name by remember { mutableStateOf("") }

    LaunchedEffect(id) {
        if (id == "new") candidates = runCatching { c.discovery.blendCandidates() }.getOrElse { error = "Couldn't reach the server."; emptyList() }
        else blend = runCatching { c.discovery.blend(id) }.getOrElse { error = "This blend is gone, or you're no longer in it."; null }
    }
    fun act(block: suspend () -> Unit) = scope.launch {
        busy = true
        runCatching { block() }.onFailure { ui.toast = (it as? retrofit2.HttpException)?.response()?.errorBody()?.string()?.substringAfter("\"detail\":\"")?.substringBefore("\"") ?: "Something went wrong" }
        busy = false
    }
    val b = blend
    val tracks = b?.tracks.orEmpty().map { it.track }
    fun play(i: Int, shuffle: Boolean = false) {
        if (tracks.isEmpty()) return
        c.player.play(if (shuffle) tracks.shuffled() else tracks, i, PlaySource.MIX, "Blend: ${b?.name}")
        ui.go("now")
    }

    Column(Modifier.fillMaxSize()) {
        BackBar(if (id == "new") "Start a blend" else b?.name ?: "Blend") {
            if (b?.myStatus == "joined") {
                IconButton(onClick = { text = ""; dialog = "invite" }) { Icon(Icons.Default.PersonAdd, "Invite") }
                IconButton(onClick = { act { blend = c.discovery.refreshBlend(id) } }, enabled = !busy) { Icon(Icons.Default.Refresh, "Refresh") }
            }
        }
        when {
            error != null -> Message(error!!)
            id == "new" -> {
                val list = candidates
                if (list == null) Loading() else LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
                    item {
                        Text(
                            "A blend is a playlist that mixes your taste with other people's on this server and updates every night. " +
                                "Each person you invite has to say yes before any of their listening is used, and anyone can leave at any time.",
                            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        if (list.isEmpty()) Message("Nobody here can be invited yet.")
                    }
                    items(list, key = { it.userId }) { u ->
                        Row(Modifier.fillMaxWidth().clickable { picked[u.username] = !(picked[u.username] ?: false) }, verticalAlignment = Alignment.CenterVertically) {
                            Checkbox(picked[u.username] == true, { picked[u.username] = it })
                            Text(u.username)
                        }
                    }
                    item {
                        OutlinedTextField(name, { name = it.take(80) }, label = { Text("Name (optional)") }, singleLine = true, modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
                        val who = picked.filterValues { it }.keys.toList()
                        Button(onClick = { act { val nb = c.discovery.createBlend(NewBlend(who, name.trim().ifEmpty { null })); ui.back(); ui.go("blend/${nb.id}") } }, enabled = who.isNotEmpty() && !busy) {
                            Text(if (who.size > 1) "Send invites" else "Send invite")
                        }
                    }
                }
            }
            b == null -> Loading()
            else -> LazyColumn(Modifier.fillMaxSize()) {
                item {
                    Column(Modifier.padding(horizontal = 16.dp)) {
                        if (b.matchPct != null) Row(verticalAlignment = Alignment.Bottom) {
                            Text("${b.matchPct}%", style = MaterialTheme.typography.displaySmall, fontWeight = FontWeight.Bold)
                            Text(" taste match", modifier = Modifier.padding(bottom = 8.dp))
                        } else Text(if (b.myStatus == "invited") "Invitation" else "Waiting for others to join", color = MaterialTheme.colorScheme.onSurfaceVariant)
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            b.members.forEach { m -> AssistChip({}, label = { Text((if (m.userId == me) "You" else m.username) + if (m.status == "invited") " (invited)" else "") }) }
                        }
                        if (b.pairs.size > 1) Text(b.pairs.joinToString(" · ") { "${it.aName} & ${it.bName}: ${it.pct ?: "–"}%" }, style = MaterialTheme.typography.bodySmall)
                    }
                }
                if (b.myStatus == "invited") item {
                    Column(Modifier.padding(16.dp)) {
                        Text("${b.invitedBy ?: b.createdBy?.username ?: "Someone"} invited you to this blend. If you accept, your plays and favourites are used to build it, and the other members see which songs came from your taste.")
                        Row(Modifier.padding(top = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { act { blend = c.discovery.acceptBlend(id) } }, enabled = !busy) { Text("Accept") }
                            OutlinedButton(onClick = { act { c.discovery.declineBlend(id); ui.back() } }, enabled = !busy) { Text("Decline") }
                        }
                    }
                } else {
                    item {
                        Row(Modifier.padding(horizontal = 16.dp, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                            Button(onClick = { play(0) }, enabled = tracks.isNotEmpty()) { Icon(Icons.Default.PlayArrow, null); Text("Play") }
                            OutlinedButton(onClick = { play(0, true) }, enabled = tracks.isNotEmpty()) { Icon(Icons.Default.Shuffle, null); Text("Shuffle") }
                            TextButton(onClick = { text = b.name; dialog = "rename" }) { Text("Rename") }
                            TextButton(onClick = { dialog = "leave" }) { Icon(Icons.AutoMirrored.Filled.ExitToApp, null); Text("Leave") }
                        }
                    }
                    if (b.tracks.isEmpty()) item { Message("No songs yet — they appear once at least two people have joined.") }
                    val names = b.members.associate { it.userId to it.username }
                    itemsIndexed(b.tracks, key = { i, t -> "$i:${t.track.id}" }) { i, t ->
                        Row(Modifier.fillMaxWidth().clickable { play(i) }.padding(horizontal = 16.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                            ShelfArt(ShelfItem("track", t.track.id, t.track.title, track = t.track), 48.dp)
                            Column(Modifier.weight(1f).padding(start = 12.dp)) {
                                Text(t.track.title, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text(t.track.artist, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                                Text(blendReason(t.reason, t.forUsers, names, me), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary, maxLines = 1)
                            }
                        }
                    }
                }
                item { Spacer(Modifier.height(24.dp)) }
            }
        }
    }

    when (dialog) {
        "invite", "rename" -> AlertDialog(
            onDismissRequest = { dialog = null },
            title = { Text(if (dialog == "invite") "Invite" else "Rename blend") },
            text = { OutlinedTextField(text, { text = it.take(80) }, label = { Text(if (dialog == "invite") "Username" else "Name") }, singleLine = true, keyboardOptions = KeyboardOptions.Default) },
            confirmButton = {
                TextButton(onClick = {
                    val d = dialog; dialog = null
                    if (text.isNotBlank()) act { blend = if (d == "invite") c.discovery.inviteToBlend(id, BlendInvite(listOf(text.trim()))) else c.discovery.renameBlend(id, BlendName(text.trim())) }
                }) { Text(if (dialog == "invite") "Invite" else "Save") }
            },
            dismissButton = { TextButton(onClick = { dialog = null }) { Text("Cancel") } },
        )
        "leave" -> AlertDialog(
            onDismissRequest = { dialog = null },
            title = { Text("Leave blend") },
            text = { Text("Leave \"${b?.name}\"? Your listening stops being used in it right away.") },
            confirmButton = { TextButton(onClick = { dialog = null; act { c.discovery.leaveBlend(id); ui.back() } }) { Text("Leave") } },
            dismissButton = { TextButton(onClick = { dialog = null }) { Text("Cancel") } },
        )
    }
}
