package org.homebeat.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.FilledIconButton
import androidx.compose.material.icons.filled.SpeakerGroup
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Bookmark
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.DownloadDone
import androidx.compose.material.icons.filled.Forward30
import androidx.compose.material.icons.filled.MenuBook
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Podcasts
import androidx.compose.material.icons.filled.Radio
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Replay
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.SkipPrevious
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil3.compose.AsyncImage
import kotlinx.coroutines.launch
import org.homebeat.app.AppContainer
import org.homebeat.app.data.api.Audiobook
import org.homebeat.app.data.api.AudiobookList
import org.homebeat.app.data.api.ContinueItem
import org.homebeat.app.data.api.Episode
import org.homebeat.app.data.api.Podcast
import org.homebeat.app.data.api.PodcastPatch
import org.homebeat.app.data.api.PodcastSearchResult
import org.homebeat.app.data.api.Station
import org.homebeat.app.data.api.StationBody
import org.homebeat.app.data.api.StationSearchResult
import org.homebeat.app.data.api.SubscribeBody
import org.homebeat.app.net.RoutedCallFactory
import org.homebeat.app.spoken.SpokenLogic
import org.homebeat.app.ui.LocalUi
import org.homebeat.app.ui.Loading
import org.homebeat.app.ui.Message

private val SPEEDS = listOf(0.8f, 1f, 1.1f, 1.2f, 1.3f, 1.5f, 1.75f, 2f, 2.5f, 3f)

private fun art(kind: String, id: String, size: Int = 256) = RoutedCallFactory.virtual("art/$kind/${android.net.Uri.encode(id)}?size=$size")

@Composable
fun SpokenArt(url: String?, icon: ImageVector, size: Dp, round: Boolean = false) {
    Box(
        Modifier.size(size).clip(if (round) CircleShape else RoundedCornerShape(6.dp)).background(MaterialTheme.colorScheme.surfaceContainerHigh),
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, null, tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f), modifier = Modifier.size(size / 2.5f))
        if (!url.isNullOrBlank()) AsyncImage(url, null, contentScale = ContentScale.Crop, modifier = Modifier.fillMaxSize())
    }
}

@Composable
private fun BackBar(title: String, actions: @Composable () -> Unit = {}) {
    val ui = LocalUi.current
    Row(Modifier.fillMaxWidth().padding(4.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = { ui.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
        Text(title, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
        actions()
    }
}

@Composable
private fun Row2(title: String, subtitle: String?, leading: @Composable () -> Unit, onClick: () -> Unit, trailing: @Composable () -> Unit = {}) {
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        leading()
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, maxLines = 2, overflow = TextOverflow.Ellipsis)
            subtitle?.takeIf { it.isNotBlank() }?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        trailing()
    }
}

/** Library → Podcasts & more: Podcasts · Audiobooks · Radio. */
@Composable
fun SpokenScreen(c: AppContainer, initialTab: String = "podcasts") {
    var tab by rememberSaveable { mutableStateOf(initialTab) }
    Column(Modifier.fillMaxSize()) {
        BackBar("Podcasts & more")
        Row(Modifier.padding(horizontal = 12.dp).horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("podcasts" to "Podcasts", "books" to "Audiobooks", "radio" to "Radio").forEach { (k, l) ->
                FilterChip(tab == k, { tab = k }, label = { Text(l) })
            }
        }
        Box(Modifier.weight(1f)) {
            when (tab) {
                "podcasts" -> PodcastsTab(c)
                "books" -> BooksTab(c)
                else -> RadioTab(c)
            }
        }
    }
}

@Composable
private fun SearchField(hint: String, busy: Boolean, onSubmit: (String) -> Unit) {
    var q by rememberSaveable { mutableStateOf("") }
    Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        OutlinedTextField(q, { q = it }, Modifier.weight(1f), placeholder = { Text(hint) }, singleLine = true)
        Spacer(Modifier.width(8.dp))
        if (busy) CircularProgressIndicator(Modifier.size(24.dp))
        else IconButton(onClick = { if (q.isNotBlank()) onSubmit(q.trim()) }) {
            Icon(if (q.trim().startsWith("http")) Icons.Default.Add else Icons.Default.Search, if (q.trim().startsWith("http")) "Add" else "Search")
        }
    }
}

@Composable
private fun ContinueRow(c: AppContainer, kind: String) {
    var items by remember { mutableStateOf<List<ContinueItem>>(emptyList()) }
    LaunchedEffect(kind) { items = runCatching { c.spokenApi.continueListening().items }.getOrDefault(emptyList()).filter { it.kind == kind } }
    if (items.isEmpty()) return
    Text("Continue listening", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp))
    Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        items.forEach { it ->
            val title = it.episode?.title ?: it.book?.title ?: ""
            val url = it.episode?.let { e -> art("podcast", e.podcastId) } ?: it.book?.let { b -> art("book", b.id) }
            Column(Modifier.width(120.dp).clickable {
                if (it.kind == "episode") it.episode?.let { e -> c.spoken.playEpisode(e.id) } else it.book?.let { b -> c.spoken.playBook(b.id) }
            }) {
                SpokenArt(url, if (it.kind == "book") Icons.Default.MenuBook else Icons.Default.Podcasts, 120.dp)
                Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall)
                LinearProgressIndicator(
                    progress = { (it.progress.positionS / (it.progress.durationS ?: 1.0)).toFloat().coerceIn(0f, 1f) },
                    modifier = Modifier.fillMaxWidth().padding(top = 2.dp),
                )
            }
        }
    }
}

@Composable
private fun PodcastsTab(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var shows by remember { mutableStateOf<List<Podcast>?>(null) }
    var inbox by remember { mutableStateOf<List<Episode>>(emptyList()) }
    var results by remember { mutableStateOf<List<PodcastSearchResult>?>(null) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        runCatching { c.spokenApi.podcasts().podcasts }.onSuccess { shows = it }.onFailure { error = it.message }
        inbox = runCatching { c.spokenApi.inbox("unplayed", 30).episodes }.getOrDefault(emptyList())
    }
    fun subscribe(url: String) = scope.launch {
        busy = true
        runCatching { c.spokenApi.subscribe(SubscribeBody(url)) }
            .onSuccess { ui.go("podcast/${it.id}") }.onFailure { ui.toast = it.message ?: "Could not subscribe" }
        busy = false
    }
    LazyColumn(Modifier.fillMaxSize()) {
        item {
            SearchField("Search podcasts or paste an RSS URL", busy) { q ->
                if (q.startsWith("http")) subscribe(q)
                else scope.launch {
                    busy = true
                    runCatching { c.spokenApi.searchPodcasts(q) }.onSuccess { results = it.results; if (it.results.isEmpty()) ui.toast = it.errors.firstOrNull() ?: "Nothing found" }
                        .onFailure { ui.toast = it.message }
                    busy = false
                }
            }
        }
        results?.let { rs ->
            item { Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Directory", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                TextButton(onClick = { results = null }) { Text("Close") }
            } }
            items(rs, key = { "r" + it.feedUrl }) { r ->
                Row2(r.title + if (r.explicit) " 🅴" else "", listOfNotNull(r.author, r.genre).joinToString(" · "),
                    { SpokenArt(r.imageUrl, Icons.Default.Podcasts, 48.dp) },
                    { if (r.subscribed) ui.go("podcast/${r.id}") else subscribe(r.feedUrl) }) {
                    if (r.subscribed) Icon(Icons.Default.CheckCircle, "Subscribed") else Icon(Icons.Default.Add, "Subscribe")
                }
            }
        }
        item { ContinueRow(c, "episode") }
        val s = shows
        when {
            error != null && s == null -> item { Message(error!!) }
            s == null -> item { Box(Modifier.fillMaxWidth().padding(32.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() } }
            s.isEmpty() && results == null -> item { Message("No podcasts yet. Search the directory, or paste a show's RSS feed URL.") }
            else -> {
                if (s.isNotEmpty()) item { Text("Your shows", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(16.dp, 12.dp, 16.dp, 4.dp)) }
                items(s, key = { "s" + it.id }) { p ->
                    Row2(p.title, if ((p.unplayed ?: 0) > 0) "${p.unplayed} unplayed" else "All played",
                        { SpokenArt(art("podcast", p.id), Icons.Default.Podcasts, 56.dp) }, { ui.go("podcast/${p.id}") })
                }
                if (inbox.isNotEmpty()) item { Text("New episodes", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(16.dp, 16.dp, 16.dp, 4.dp)) }
                items(inbox, key = { "e" + it.id }) { e -> EpisodeRow(c, e, showTitle = true) }
            }
        }
    }
}

@Composable
private fun EpisodeRow(c: AppContainer, e: Episode, showTitle: Boolean = false, onChanged: () -> Unit = {}) {
    val scope = rememberCoroutineScope()
    val ui = LocalUi.current
    val st by c.spoken.state.collectAsStateWithLifecycle()
    val downloaded by c.spoken.downloads.ids.collectAsStateWithLifecycle()
    val busy by c.spoken.downloads.busy.collectAsStateWithLifecycle()
    val current = st.item?.kind == "episode" && st.item?.id == e.id
    var menu by remember { mutableStateOf(false) }
    val played = e.progress?.played == true
    Row2(
        e.title,
        listOfNotNull(
            if (showTitle) e.podcastTitle else null, e.publishedAt?.take(10),
            SpokenLogic.remaining(e.progress?.positionS, e.durationS, played).ifBlank { null },
            if (e.id in downloaded) "on phone" else null,
        ).joinToString(" · "),
        leading = {
            IconButton(onClick = { if (current) c.spoken.toggle() else c.spoken.playEpisode(e.id) }) {
                Icon(if (current && st.playing) Icons.Default.Pause else Icons.Default.PlayArrow, if (current && st.playing) "Pause" else "Play")
            }
        },
        onClick = { menu = true },
    ) {
        when {
            e.id in busy -> CircularProgressIndicator(Modifier.size(20.dp))
            e.id in downloaded -> Icon(Icons.Default.DownloadDone, "Downloaded", tint = MaterialTheme.colorScheme.primary)
            else -> IconButton(onClick = {
                scope.launch { runCatching { c.spoken.downloads.download(e.id) }.onFailure { ui.toast = "Download failed: ${it.message}" } }
            }) { Icon(Icons.Default.Download, "Download") }
        }
        DropdownMenu(menu, { menu = false }) {
            DropdownMenuItem({ Text(if (played) "Mark unplayed" else "Mark played") }, {
                menu = false; c.spoken.markPlayed("episode", e.id, !played); onChanged()
            })
            if (e.id in downloaded) DropdownMenuItem({ Text("Remove download") }, { menu = false; c.spoken.downloads.delete(e.id) })
            e.summary?.let { s -> DropdownMenuItem({ Text(s, maxLines = 6, style = MaterialTheme.typography.bodySmall) }, { menu = false }) }
        }
    }
}

/** One show: settings + episodes. */
@Composable
fun PodcastScreen(c: AppContainer, id: String) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var show by remember { mutableStateOf<Podcast?>(null) }
    var eps by remember { mutableStateOf<List<Episode>?>(null) }
    var filter by rememberSaveable { mutableStateOf("all") }
    var rev by remember { mutableIntStateOf(0) }
    var settingsOpen by remember { mutableStateOf(false) }
    LaunchedEffect(id, filter, rev) {
        show = runCatching { c.spokenApi.podcast(id) }.getOrNull()
        eps = runCatching { c.spokenApi.episodes(id, filter).episodes }.getOrDefault(emptyList())
    }
    fun patch(p: PodcastPatch) = scope.launch { runCatching { c.spokenApi.patchPodcast(id, p) }.onSuccess { show = it }.onFailure { ui.toast = it.message } }
    Column(Modifier.fillMaxSize()) {
        BackBar(show?.title ?: "") {
            IconButton(onClick = { scope.launch { runCatching { c.spokenApi.refresh(id) }; rev++ } }) { Icon(Icons.Default.Refresh, "Check for new episodes") }
            IconButton(onClick = { settingsOpen = true }) { Icon(Icons.Default.Tune, "Show settings") }
        }
        show?.let { p ->
            Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                SpokenArt(art("podcast", p.id, 512), Icons.Default.Podcasts, 96.dp)
                Column(Modifier.padding(start = 12.dp)) {
                    p.author?.let { Text(it, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    p.summary?.let { Text(it, style = MaterialTheme.typography.bodySmall, maxLines = 4, overflow = TextOverflow.Ellipsis) }
                    p.lastError?.let { Text("Last check failed: $it", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
                }
            }
        }
        Row(Modifier.padding(12.dp).horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("all" to "All", "unplayed" to "Unplayed", "in_progress" to "In progress", "downloaded" to "On server").forEach { (k, l) ->
                FilterChip(filter == k, { filter = k }, label = { Text(l) })
            }
        }
        val e = eps
        when {
            e == null -> Loading()
            e.isEmpty() -> Message("No episodes here.")
            else -> LazyColumn(Modifier.weight(1f)) { items(e, key = { it.id }) { EpisodeRow(c, it) { rev++ } } }
        }
    }
    val p = show
    if (settingsOpen && p != null) AlertDialog(
        onDismissRequest = { settingsOpen = false },
        confirmButton = { TextButton(onClick = { settingsOpen = false }) { Text("Done") } },
        title = { Text(p.title) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Speed: ${p.speed}×")
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    SPEEDS.forEach { s -> FilterChip(p.speed.toFloat() == s, { patch(PodcastPatch(speed = s.toDouble())) }, label = { Text("$s×") }) }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Skip silence", Modifier.weight(1f)); Switch(p.skipSilence, { patch(PodcastPatch(skipSilence = it)) })
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Download new episodes to this phone (home Wi-Fi)", Modifier.weight(1f)); Switch(p.autoDownload, { patch(PodcastPatch(autoDownload = it)) })
                }
                Text("Keep on the server: ${p.serverKeepLast ?: 0} newest (shared)")
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    listOf(0, 1, 3, 5, 10).forEach { n -> FilterChip(p.serverKeepLast == n, { patch(PodcastPatch(serverKeepLast = n)) }, label = { Text(if (n == 0) "stream" else "$n") }) }
                }
                TextButton(onClick = { scope.launch { runCatching { c.spokenApi.markShowPlayed(id) }; rev++; settingsOpen = false } }) { Text("Mark all played") }
                TextButton(onClick = {
                    scope.launch { runCatching { c.spokenApi.unsubscribe(id) }; settingsOpen = false; ui.back() }
                }) { Text("Unsubscribe", color = MaterialTheme.colorScheme.error) }
            }
        },
    )
}

@Composable
private fun BooksTab(c: AppContainer) {
    val ui = LocalUi.current
    var data by remember { mutableStateOf<AudiobookList?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) { runCatching { c.spokenApi.books("recent") }.onSuccess { data = it }.onFailure { error = it.message } }
    val d = data
    when {
        error != null -> Message(error!!)
        d == null -> Loading()
        !d.enabled -> Message("Audiobooks are not set up on this server (an admin sets audiobooks_root).")
        d.books.isEmpty() -> Message("No audiobooks found yet.")
        else -> LazyColumn(Modifier.fillMaxSize()) {
            item { ContinueRow(c, "book") }
            items(d.books, key = { it.id }) { b ->
                Row2(b.title, listOfNotNull(b.author, SpokenLogic.remaining(b.progress?.positionS, b.durationS, b.progress?.played == true)).joinToString(" · "),
                    { SpokenArt(art("book", b.id), Icons.Default.MenuBook, 56.dp) }, { ui.go("book/${b.id}") })
            }
        }
    }
}

@Composable
fun BookScreen(c: AppContainer, id: String) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var book by remember { mutableStateOf<Audiobook?>(null) }
    var rev by remember { mutableIntStateOf(0) }
    val st by c.spoken.state.collectAsStateWithLifecycle()
    LaunchedEffect(id, rev) { book = runCatching { c.spokenApi.book(id) }.getOrNull() }
    val b = book
    if (b == null) {
        Column { BackBar(""); Loading() }
        return
    }
    val current = st.item?.kind == "book" && st.item?.id == id
    val pos = if (current) st.positionS else b.progress?.positionS ?: 0.0
    val ch = SpokenLogic.chapterAt(b.chapters.map { it.startS }, pos)
    LazyColumn(Modifier.fillMaxSize()) {
        item { BackBar(b.title) }
        item {
            Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                SpokenArt(art("book", b.id, 512), Icons.Default.MenuBook, 120.dp)
                Column(Modifier.padding(start = 16.dp)) {
                    b.author?.let { Text(it, fontWeight = FontWeight.SemiBold) }
                    b.narrator?.let { Text("Read by $it", style = MaterialTheme.typography.bodySmall) }
                    Text("${SpokenLogic.clock(b.durationS)} · ${SpokenLogic.remaining(b.progress?.positionS, b.durationS, b.progress?.played == true)}",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.size(8.dp))
                    Button(onClick = { if (current) c.spoken.toggle() else c.spoken.playBook(id) }) {
                        Icon(if (current && st.playing) Icons.Default.Pause else Icons.Default.PlayArrow, null)
                        Text(if (current && st.playing) "Pause" else if ((b.progress?.positionS ?: 0.0) > 5 && b.progress?.played != true) "Resume" else "Play")
                    }
                }
            }
        }
        b.description?.let { d -> item { Text(d, style = MaterialTheme.typography.bodySmall, maxLines = 8, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(16.dp)) } }
        if (b.bookmarks.isNotEmpty()) {
            item { Text("Bookmarks", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(16.dp, 12.dp, 16.dp, 4.dp)) }
            items(b.bookmarks, key = { "b" + it.id }) { bm ->
                Row2(SpokenLogic.clock(bm.positionS), bm.note, { Icon(Icons.Default.Bookmark, null) },
                    { if (current) c.spoken.seekTo(bm.positionS) else c.spoken.playBook(id, bm.positionS) }) {
                    IconButton(onClick = { scope.launch { runCatching { c.spokenApi.deleteBookmark(id, bm.id) }; rev++ } }) { Icon(Icons.Default.Delete, "Delete bookmark") }
                }
            }
        }
        item { Text("Chapters", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(16.dp, 12.dp, 16.dp, 4.dp)) }
        items(b.chapters, key = { "c" + it.index }) { chp ->
            Row(
                Modifier.fillMaxWidth().clickable { if (current) c.spoken.seekTo(chp.startS) else c.spoken.playBook(id, chp.startS) }
                    .padding(horizontal = 16.dp, vertical = 10.dp),
            ) {
                Text("${chp.index + 1}", Modifier.width(32.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(chp.title, Modifier.weight(1f), color = if (chp.index == ch) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(SpokenLogic.clock(chp.startS), style = MaterialTheme.typography.bodySmall)
            }
            HorizontalDivider()
        }
        item { Spacer(Modifier.size(24.dp)) }
    }
}

@Composable
private fun RadioTab(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var stations by remember { mutableStateOf<List<Station>?>(null) }
    var results by remember { mutableStateOf<List<StationSearchResult>?>(null) }
    var busy by remember { mutableStateOf(false) }
    val st by c.spoken.state.collectAsStateWithLifecycle()
    suspend fun reload() { stations = runCatching { c.spokenApi.stations().stations }.getOrDefault(emptyList()) }
    LaunchedEffect(Unit) { reload() }
    LazyColumn(Modifier.fillMaxSize()) {
        item {
            SearchField("Search stations or paste a stream URL", busy) { q ->
                scope.launch {
                    busy = true
                    if (q.startsWith("http")) runCatching { c.spokenApi.addStation(StationBody(q)) }
                        .onSuccess { ui.toast = "Added ${it.name}"; reload() }.onFailure { ui.toast = it.message }
                    else runCatching { c.spokenApi.searchStations(q).results }.onSuccess { results = it }.onFailure { ui.toast = it.message }
                    busy = false
                }
            }
        }
        results?.let { rs ->
            item { Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Radio Browser", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                TextButton(onClick = { results = null }) { Text("Close") }
            } }
            items(rs, key = { "r" + it.id }) { r ->
                Row2(r.name, listOfNotNull(r.country, r.codec?.let { cd -> cd + (r.bitrate?.let { " ${it}k" } ?: "") }, *r.tags.take(3).toTypedArray()).joinToString(" · "),
                    { SpokenArt(r.imageUrl, Icons.Default.Radio, 48.dp, round = true) }, {
                        if (!r.favourite) scope.launch {
                            runCatching {
                                c.spokenApi.addStation(StationBody(r.streamUrl, r.name, r.homepage, r.imageUrl, r.country, r.tags, r.codec, r.bitrate, r.explicit, r.radiobrowserUuid, probe = false))
                            }.onSuccess { s -> results = rs.map { if (it.id == r.id) it.copy(favourite = true) else it }; reload(); c.spoken.playStation(s) }
                                .onFailure { ui.toast = it.message }
                        }
                    }) { Icon(if (r.favourite) Icons.Default.CheckCircle else Icons.Default.Add, if (r.favourite) "Added" else "Add") }
            }
        }
        val s = stations
        when {
            s == null -> item { Box(Modifier.fillMaxWidth().padding(32.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() } }
            s.isEmpty() && results == null -> item { Message("No stations yet. Search Radio Browser, or paste a stream URL.") }
            else -> {
                if (s.isNotEmpty()) item { Text("Your stations", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(16.dp, 12.dp, 16.dp, 4.dp)) }
                items(s, key = { "s" + it.id }) { stn ->
                    val playing = st.item?.kind == "station" && st.item?.id == stn.id
                    Row2(stn.name, if (playing) st.nowPlaying ?: "Live" else listOfNotNull(stn.country, *stn.tags.take(3).toTypedArray()).joinToString(" · "),
                        { SpokenArt(if (stn.hasImage) art("station", stn.id) else null, Icons.Default.Radio, 48.dp, round = true) },
                        { if (playing) c.spoken.toggle() else c.spoken.playStation(stn) }) {
                        IconButton(onClick = { scope.launch { runCatching { c.spokenApi.removeStation(stn.id) }; reload() } }) { Icon(Icons.Default.Delete, "Remove ${stn.name}") }
                    }
                }
            }
        }
    }
}

/** The mini player while a podcast / audiobook / station is the current item of the player (here or remote). */
@Composable
fun SpokenMiniBar(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val s by c.spoken.state.collectAsStateWithLifecycle()
    LaunchedEffect(s.playing) { while (s.playing) { c.player.refresh(); kotlinx.coroutines.delay(1000) } }
    val item = s.item ?: return
    var expanded by remember { mutableStateOf(false) }
    var sleepMenu by remember { mutableStateOf(false) }
    val chapters = item.chapters
    Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainer)) {
        if (!s.live) LinearProgressIndicator(
            progress = { ((s.positionS / (item.durationS ?: 0.0).coerceAtLeast(1.0))).toFloat().coerceIn(0f, 1f) },
            modifier = Modifier.fillMaxWidth(),
        )
        Row(Modifier.padding(horizontal = 8.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Row(Modifier.weight(1f).clickable {
                when (item.kind) { "episode" -> item.showId?.let { ui.go("podcast/$it") }; "book" -> ui.go("book/${item.id}"); else -> expanded = !expanded }
            }, verticalAlignment = Alignment.CenterVertically) {
                SpokenArt(item.artUrl, when (item.kind) { "book" -> Icons.Default.MenuBook; "station" -> Icons.Default.Radio; else -> Icons.Default.Podcasts }, 40.dp, round = s.live)
                Column(Modifier.padding(horizontal = 10.dp)) {
                    Text(if (s.live && s.nowPlaying != null) s.nowPlaying!! else item.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium)
                    val sub = when {
                        s.error != null -> s.error!!
                        s.remoteLabel != null -> s.remoteLabel!!
                        s.live -> if (s.nowPlaying != null) item.title else if (s.buffering) "Connecting…" else "Live"
                        s.chapterIndex >= 0 && chapters.size > 1 -> chapters[s.chapterIndex].title
                        else -> item.subtitle
                    }
                    Text(sub, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (!s.live) IconButton(onClick = { c.spoken.skip(-15.0) }) { Icon(Icons.Default.Replay, "Back 15 seconds") }
            IconButton(onClick = { c.spoken.toggle() }) { Icon(if (s.playing) Icons.Default.Pause else Icons.Default.PlayArrow, if (s.playing) "Pause" else "Play") }
            if (!s.live) IconButton(onClick = { c.spoken.skip(30.0) }) { Icon(Icons.Default.Forward30, "Forward 30 seconds") }
            IconButton(onClick = { expanded = !expanded }) { Icon(Icons.Default.Tune, "More controls") }
        }
        if (expanded) Column(Modifier.padding(horizontal = 12.dp, vertical = 4.dp)) {
            if (!s.live) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(SpokenLogic.clock(s.positionS), style = MaterialTheme.typography.bodySmall)
                    var drag by remember(item.id) { mutableStateOf<Float?>(null) }
                    Slider(
                        value = drag ?: s.positionS.toFloat(), onValueChange = { drag = it },
                        onValueChangeFinished = { drag?.let { c.spoken.seekTo(it.toDouble()) }; drag = null },
                        valueRange = 0f..((item.durationS ?: 1.0).toFloat().coerceAtLeast(1f)), modifier = Modifier.weight(1f).padding(horizontal = 8.dp),
                    )
                    Text("-" + SpokenLogic.clock(((item.durationS ?: 0.0) - s.positionS) / s.speed), style = MaterialTheme.typography.bodySmall)
                }
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically) {
                    if (chapters.size > 1) {
                        IconButton(onClick = { c.spoken.chapter(false) }) { Icon(Icons.Default.SkipPrevious, "Previous chapter") }
                        IconButton(onClick = { c.spoken.chapter(true) }) { Icon(Icons.Default.SkipNext, "Next chapter") }
                    }
                    SPEEDS.forEach { sp -> FilterChip(s.speed == sp, { c.spoken.setSpeed(sp) }, label = { Text("$sp×") }) }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Skip silence", Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                    Switch(s.skipSilence, { c.spoken.setSkipSilence(it) })
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box {
                    TextButton(onClick = { sleepMenu = true }) {
                        Text("Sleep: " + when {
                            s.sleepEndOfChapter -> "end of chapter"
                            s.sleepEndsAt != null -> "${((s.sleepEndsAt!! - System.currentTimeMillis()) / 60_000 + 1)} min"
                            else -> "off"
                        })
                    }
                    DropdownMenu(sleepMenu, { sleepMenu = false }) {
                        DropdownMenuItem({ Text("Off") }, { c.spoken.setSleep(0); sleepMenu = false })
                        listOf(5, 10, 15, 30, 45, 60, 90).forEach { m -> DropdownMenuItem({ Text("$m min") }, { c.spoken.setSleep(m); sleepMenu = false }) }
                        if (chapters.size > 1) DropdownMenuItem({ Text("End of chapter") }, { c.spoken.setSleep(-1); sleepMenu = false })
                    }
                }
                if (item.kind == "book") TextButton(onClick = {
                    scope.launch { ui.toast = if (c.spoken.addBookmark(null)) "Bookmark added" else "Could not add bookmark" }
                }) { Icon(Icons.Default.Bookmark, null); Text("Bookmark") }
                Spacer(Modifier.weight(1f))
                IconButton(onClick = { c.spoken.close() }) { Icon(Icons.Default.Close, "Stop") }
            }
        }
    }
}

/** Now Playing for a podcast episode / audiobook / station (the main player's current item). */
@Composable
fun SpokenNowPlaying(c: AppContainer) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val s by c.spoken.state.collectAsStateWithLifecycle()
    val np by c.player.state.collectAsStateWithLifecycle()
    val item = s.item ?: return
    val chapters = item.chapters
    var sleepMenu by remember { mutableStateOf(false) }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 12.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                when (item.kind) { "book" -> "Audiobook"; "station" -> "Internet radio"; else -> "Podcast" },
                Modifier.weight(1f), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            IconButton(onClick = { ui.devicesOpen = true }) {
                Icon(Icons.Default.SpeakerGroup, "Devices", tint = if (np.remote != null) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
            }
        }
        Spacer(Modifier.size(8.dp))
        SpokenArt(item.artUrl.replace("size=256", "size=512"), when (item.kind) { "book" -> Icons.Default.MenuBook; "station" -> Icons.Default.Radio; else -> Icons.Default.Podcasts }, 280.dp)
        Spacer(Modifier.size(16.dp))
        Text(if (s.live && s.nowPlaying != null) s.nowPlaying!! else item.title, style = MaterialTheme.typography.titleLarge, maxLines = 3, overflow = TextOverflow.Ellipsis)
        Text(
            when {
                s.live && s.nowPlaying != null -> item.title
                s.chapterIndex >= 0 && chapters.size > 1 -> chapters[s.chapterIndex].title
                else -> item.subtitle
            },
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2,
            modifier = Modifier.clickable {
                when (item.kind) { "episode" -> item.showId?.let { ui.go("podcast/$it") }; "book" -> ui.go("book/${item.id}") }
            },
        )
        s.remoteLabel?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary) }
        Spacer(Modifier.size(12.dp))
        if (!s.live) {
            var drag by remember(item.id) { mutableStateOf<Float?>(null) }
            Slider(
                value = drag ?: s.positionS.toFloat(), onValueChange = { drag = it },
                onValueChangeFinished = { drag?.let { c.spoken.seekTo(it.toDouble()) }; drag = null },
                valueRange = 0f..((item.durationS ?: 1.0).toFloat().coerceAtLeast(1f)), modifier = Modifier.fillMaxWidth(),
            )
            Row(Modifier.fillMaxWidth()) {
                Text(SpokenLogic.clock(drag?.toDouble() ?: s.positionS), style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                Text("-" + SpokenLogic.clock(((item.durationS ?: 0.0) - s.positionS) / s.speed.coerceAtLeast(0.25f)), style = MaterialTheme.typography.bodySmall)
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (!s.live && chapters.size > 1) IconButton(onClick = { c.spoken.chapter(false) }) { Icon(Icons.Default.SkipPrevious, "Previous chapter") }
            if (!s.live) IconButton(onClick = { c.spoken.skip(-15.0) }) { Icon(Icons.Default.Replay, "Back 15 seconds") }
            FilledIconButton(onClick = { c.spoken.toggle() }, modifier = Modifier.size(64.dp)) {
                Icon(if (s.playing) Icons.Default.Pause else Icons.Default.PlayArrow, if (s.playing) "Pause" else "Play", Modifier.size(36.dp))
            }
            if (!s.live) IconButton(onClick = { c.spoken.skip(30.0) }) { Icon(Icons.Default.Forward30, "Forward 30 seconds") }
            if (!s.live && chapters.size > 1) IconButton(onClick = { c.spoken.chapter(true) }) { Icon(Icons.Default.SkipNext, "Next chapter") }
        }
        if (!s.live) {
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                SPEEDS.forEach { sp -> FilterChip(s.speed == sp, { c.spoken.setSpeed(sp) }, label = { Text("$sp×") }) }
            }
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text("Skip silence", Modifier.weight(1f))
                Switch(s.skipSilence, { c.spoken.setSkipSilence(it) })
            }
        }
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Box {
                TextButton(onClick = { sleepMenu = true }) {
                    Text("Sleep: " + when {
                        s.sleepEndOfChapter -> "end of chapter"
                        s.sleepEndsAt != null -> "${((s.sleepEndsAt!! - System.currentTimeMillis()) / 60_000 + 1)} min"
                        else -> "off"
                    })
                }
                DropdownMenu(sleepMenu, { sleepMenu = false }) {
                    DropdownMenuItem({ Text("Off") }, { c.spoken.setSleep(0); sleepMenu = false })
                    listOf(5, 10, 15, 30, 45, 60, 90).forEach { m -> DropdownMenuItem({ Text("$m min") }, { c.spoken.setSleep(m); sleepMenu = false }) }
                    if (chapters.size > 1) DropdownMenuItem({ Text("End of chapter") }, { c.spoken.setSleep(-1); sleepMenu = false })
                }
            }
            if (item.kind == "book") TextButton(onClick = {
                scope.launch { ui.toast = if (c.spoken.addBookmark(null)) "Bookmark added" else "Could not add bookmark" }
            }) { Icon(Icons.Default.Bookmark, null); Text("Bookmark") }
            Spacer(Modifier.weight(1f))
            if (item.kind != "station") TextButton(onClick = {
                c.spoken.markPlayed(item.kind, item.id, true); c.spoken.close(); ui.back()
            }) { Text("Mark played") }
        }
        if (chapters.size > 1) {
            Text("Chapters", style = MaterialTheme.typography.titleMedium, modifier = Modifier.fillMaxWidth().padding(top = 12.dp))
            chapters.forEachIndexed { i, ch ->
                Row(
                    Modifier.fillMaxWidth().clickable { c.spoken.seekTo(ch.start) }.padding(vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(ch.title.ifBlank { "Chapter ${i + 1}" }, Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis,
                        fontWeight = if (i == s.chapterIndex) FontWeight.SemiBold else FontWeight.Normal,
                        color = if (i == s.chapterIndex) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
                    Text(SpokenLogic.clock(ch.start), style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}
