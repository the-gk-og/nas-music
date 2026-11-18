package org.nasmusic.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.QueueMusic
import androidx.compose.material.icons.filled.Album
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.LibraryMusic
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.PlayCircle
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Sell
import androidx.compose.material.icons.filled.SpeakerGroup
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import org.nasmusic.app.AppContainer
import org.nasmusic.app.ui.screens.AccountScreen
import org.nasmusic.app.ui.screens.AdminScreen
import org.nasmusic.app.ui.screens.AlbumScreen
import org.nasmusic.app.ui.screens.AlbumsScreen
import org.nasmusic.app.ui.screens.ArtistScreen
import org.nasmusic.app.ui.screens.ArtistsScreen
import org.nasmusic.app.ui.screens.ConnectScreen
import org.nasmusic.app.ui.screens.DataSettingsScreen
import org.nasmusic.app.ui.screens.DiscoverScreen
import org.nasmusic.app.ui.screens.EqualizerScreen
import org.nasmusic.app.ui.screens.FoldersScreen
import org.nasmusic.app.ui.screens.GenreScreen
import org.nasmusic.app.ui.screens.GenresScreen
import org.nasmusic.app.ui.screens.NowPlayingScreen
import org.nasmusic.app.ui.screens.PlaylistScreen
import org.nasmusic.app.ui.screens.PlaylistsScreen
import org.nasmusic.app.ui.screens.QueuesScreen
import org.nasmusic.app.ui.screens.SearchScreen
import org.nasmusic.app.ui.screens.SettingsScreen
import org.nasmusic.app.ui.screens.SmartListScreen
import org.nasmusic.app.ui.screens.TodayScreen
import org.nasmusic.app.ui.theme.NasMusicTheme

private data class Tab(val route: String, val label: String, val icon: ImageVector)

/** docs/v2.md §7 tab order; the 9th slot is the overflow menu. */
private val tabs = listOf(
    Tab("queues", "Queues", Icons.AutoMirrored.Filled.QueueMusic),
    Tab("now", "Now playing", Icons.Default.PlayCircle),
    Tab("folders", "Folders", Icons.Default.Folder),
    Tab("albums", "Albums", Icons.Default.Album),
    Tab("artists", "Artists", Icons.Default.Person),
    Tab("genres", "Genres", Icons.Default.Sell),
    Tab("playlists", "Playlists", Icons.Default.LibraryMusic),
    Tab("search", "Search", Icons.Default.Search),
)

@Composable
fun NasMusicRoot(c: AppContainer) {
    NasMusicTheme {
        val config by c.secureStore.config.collectAsStateWithLifecycle()
        Surface(color = MaterialTheme.colorScheme.background) {
            if (config?.isPaired != true) ConnectScreen(c) else MainScaffold(c)
        }
    }
}

@Composable
private fun MainScaffold(c: AppContainer) {
    val nav = rememberNavController()
    val ui = remember(nav) { UiController(nav) }
    val entry by nav.currentBackStackEntryAsState()
    val current = entry?.destination?.route
    val snack = remember { SnackbarHostState() }
    LaunchedEffect(ui.toast) { ui.toast?.let { snack.showSnackbar(it); ui.toast = null } }

    CompositionLocalProvider(LocalUi provides ui) {
        Scaffold(
            snackbarHost = { SnackbarHost(snack) },
            bottomBar = {
                Column(Modifier.background(MaterialTheme.colorScheme.background).navigationBarsPadding()) {
                    if (current != "now") RemoteBar(c)
                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                    val cfg by c.secureStore.config.collectAsStateWithLifecycle()
                    TabBar(nav, current, cfg?.isAdmin == true)
                }
            },
        ) { padding ->
            NavHost(nav, startDestination = "now", modifier = Modifier.padding(padding)) {
                composable("queues") { QueuesScreen(c) }
                composable("now") { NowPlayingScreen(c) }
                composable("folders") { FoldersScreen(c, "") }
                composable("folder/{path}") { e -> FoldersScreen(c, android.net.Uri.decode(e.arguments?.getString("path").orEmpty())) }
                composable("albums") { AlbumsScreen(c) }
                composable("album/{id}") { e -> AlbumScreen(c, e.arguments?.getString("id").orEmpty()) }
                composable("artists") { ArtistsScreen(c) }
                composable("artist/{id}") { e -> ArtistScreen(c, e.arguments?.getString("id").orEmpty()) }
                composable("genres") { GenresScreen(c) }
                composable("genre/{name}") { e -> GenreScreen(c, android.net.Uri.decode(e.arguments?.getString("name").orEmpty())) }
                composable("playlists") { PlaylistsScreen(c) }
                composable("playlist/{id}") { e -> PlaylistScreen(c, e.arguments?.getString("id").orEmpty()) }
                composable("list/{kind}") { e -> SmartListScreen(c, e.arguments?.getString("kind").orEmpty()) }
                composable("search") { SearchScreen(c) }
                composable("settings") { SettingsScreen(c) }
                composable("equalizer") { EqualizerScreen(c) }
                composable("mixes") { TodayScreen(c) }
                composable("discover") { DiscoverScreen(c) }
                composable("account") { AccountScreen(c) }
                composable("admin") { AdminScreen(c) }
                composable("data") { DataSettingsScreen(c) }
            }
            GlobalSheets(c)
        }
    }
}

@Composable
private fun TabBar(nav: NavHostController, current: String?, isAdmin: Boolean) {
    val ui = LocalUi.current
    var overflow by remember { mutableStateOf(false) }
    Row(Modifier.fillMaxWidth().height(56.dp), verticalAlignment = Alignment.CenterVertically) {
        tabs.forEach { t ->
            val selected = current == t.route
            Box(Modifier.weight(1f).height(56.dp).clickable { nav.goTab(t.route) }, contentAlignment = Alignment.Center) {
                if (selected) Box(Modifier.align(Alignment.TopCenter).width(36.dp).height(3.dp).background(MaterialTheme.colorScheme.primary))
                Icon(t.icon, t.label, tint = if (selected) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f))
            }
        }
        Box(Modifier.weight(1f).height(56.dp), contentAlignment = Alignment.Center) {
            IconButton(onClick = { overflow = true }) { Icon(Icons.Default.MoreVert, "More") }
            DropdownMenu(overflow, { overflow = false }) {
                listOf(
                    "Settings" to "settings", "Equalizer" to "equalizer", "Devices" to null, "Mixes for you" to "mixes",
                    "Discover" to "discover", "Account" to "account",
                ).plus(if (isAdmin) listOf("Admin" to "admin") else emptyList()).forEach { (label, route) ->
                    DropdownMenuItem({ Text(label) }, {
                        overflow = false
                        if (route == null) ui.devicesOpen = true else ui.go(route)
                    })
                }
            }
        }
    }
}

private fun NavHostController.goTab(route: String) = navigate(route) {
    popUpTo(graph.findStartDestination().id) { saveState = true }
    launchSingleTop = true
    restoreState = true
}

/** "Playing on <device>" — this phone is a Connect remote. */
@Composable
private fun RemoteBar(c: AppContainer) {
    val ui = LocalUi.current
    val s by c.player.state.collectAsStateWithLifecycle()
    val remote = s.remote ?: return
    Row(
        Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerHigh).clickable { ui.go("now") }
            .padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        AlbumArt(s.item?.mediaMetadata?.extras?.getString("nas.albumId"), 40.dp)
        Column(Modifier.weight(1f).padding(horizontal = 12.dp)) {
            Text(s.item?.mediaMetadata?.title?.toString() ?: "…", maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text("Playing on ${remote.deviceName}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary, maxLines = 1)
        }
        IconButton(onClick = { ui.devicesOpen = true }) { Icon(Icons.Default.SpeakerGroup, "Devices", tint = MaterialTheme.colorScheme.primary) }
        IconButton(onClick = c.player::togglePlay) { Icon(if (s.isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow, "Play/pause") }
    }
}
