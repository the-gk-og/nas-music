package org.nasmusic.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Explore
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.LibraryMusic
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.Today
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
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
import kotlinx.coroutines.delay
import org.nasmusic.app.AppContainer
import org.nasmusic.app.ui.screens.AlbumScreen
import org.nasmusic.app.ui.screens.ArtistScreen
import org.nasmusic.app.ui.screens.DiscoverScreen
import org.nasmusic.app.ui.screens.FavouritesScreen
import org.nasmusic.app.ui.screens.LibraryScreen
import org.nasmusic.app.ui.screens.NowPlayingScreen
import org.nasmusic.app.ui.screens.PairingScreen
import org.nasmusic.app.ui.screens.SettingsScreen
import org.nasmusic.app.ui.screens.TodayScreen
import org.nasmusic.app.ui.theme.NasMusicTheme

private data class Tab(val route: String, val label: String, val icon: ImageVector)

private val tabs = listOf(
    Tab("today", "Today", Icons.Default.Today),
    Tab("favourites", "Favourites", Icons.Default.Favorite),
    Tab("library", "Library", Icons.Default.LibraryMusic),
    Tab("discover", "Discover", Icons.Default.Explore),
    Tab("settings", "Settings", Icons.Default.Settings),
)

@Composable
fun NasMusicRoot(c: AppContainer) {
    NasMusicTheme {
        val config by c.secureStore.config.collectAsStateWithLifecycle()
        if (config?.isPaired != true) {
            Surface { PairingScreen(c) }
        } else {
            MainScaffold(c)
        }
    }
}

@Composable
private fun MainScaffold(c: AppContainer) {
    val nav = rememberNavController()
    val entry by nav.currentBackStackEntryAsState()
    val current = entry?.destination?.route
    Scaffold(
        bottomBar = {
            Column {
                if (current != "now") MiniPlayer(c) { nav.navigate("now") }
                NavigationBar {
                    tabs.forEach { t ->
                        NavigationBarItem(
                            selected = current == t.route,
                            onClick = { nav.goTab(t.route) },
                            icon = { Icon(t.icon, null) },
                            label = { Text(t.label) },
                        )
                    }
                }
            }
        },
    ) { padding ->
        NavHost(nav, startDestination = "today", modifier = Modifier.padding(padding)) {
            composable("today") { TodayScreen(c, onOpenPlayer = { nav.navigate("now") }) }
            composable("favourites") { FavouritesScreen(c) }
            composable("library") {
                LibraryScreen(c, onArtist = { nav.navigate("artist/$it") }, onAlbum = { nav.navigate("album/$it") })
            }
            composable("artist/{id}") { e ->
                ArtistScreen(c, e.arguments?.getString("id").orEmpty(), onAlbum = { nav.navigate("album/$it") })
            }
            composable("album/{id}") { e -> AlbumScreen(c, e.arguments?.getString("id").orEmpty()) }
            composable("discover") { DiscoverScreen(c) }
            composable("settings") { SettingsScreen(c) }
            composable("now") { NowPlayingScreen(c, onClose = { nav.popBackStack() }) }
        }
    }
}

private fun NavHostController.goTab(route: String) = navigate(route) {
    popUpTo(graph.findStartDestination().id) { saveState = true }
    launchSingleTop = true
    restoreState = true
}

@Composable
private fun MiniPlayer(c: AppContainer, onOpen: () -> Unit) {
    val s by c.player.state.collectAsStateWithLifecycle()
    LaunchedEffect(s.isPlaying) {
        while (s.isPlaying) { c.player.refresh(); delay(1000) }
    }
    val item = s.item ?: return
    Surface(tonalElevation = 3.dp) {
        Column {
            if (s.durationMs > 0) {
                LinearProgressIndicator(
                    progress = { (s.positionMs.toFloat() / s.durationMs).coerceIn(0f, 1f) },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            Row(
                Modifier.fillMaxWidth().clickable(onClick = onOpen).padding(horizontal = 12.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                AlbumArt(item.mediaMetadata.extras?.getString("nas.albumId"), 40.dp)
                Column(Modifier.weight(1f).padding(horizontal = 12.dp)) {
                    Text(item.mediaMetadata.title?.toString().orEmpty(), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(
                        item.mediaMetadata.artist?.toString().orEmpty(),
                        style = MaterialTheme.typography.bodySmall,
                        maxLines = 1,
                    )
                }
                IconButton(onClick = c.player::togglePlay) {
                    Icon(if (s.isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow, "Play/pause")
                }
                IconButton(onClick = c.player::next, enabled = s.hasNext) { Icon(Icons.Default.SkipNext, "Next") }
            }
            HorizontalDivider()
        }
    }
}
