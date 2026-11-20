package org.nasmusic.app.ui

import android.content.res.Configuration
import androidx.compose.animation.EnterTransition
import androidx.compose.animation.ExitTransition
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.QueueMusic
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.LibraryMusic
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.VerticalDivider
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavGraphBuilder
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import kotlin.math.abs
import org.nasmusic.app.AppContainer
import org.nasmusic.app.ui.screens.AccountScreen
import org.nasmusic.app.ui.screens.AdminScreen
import org.nasmusic.app.ui.screens.AlbumScreen
import org.nasmusic.app.ui.screens.ArtistScreen
import org.nasmusic.app.ui.screens.ConnectScreen
import org.nasmusic.app.ui.screens.DataSettingsScreen
import org.nasmusic.app.ui.screens.DiscoverScreen
import org.nasmusic.app.ui.screens.DownloadsScreen
import org.nasmusic.app.ui.screens.EqualizerScreen
import org.nasmusic.app.ui.screens.FoldersScreen
import org.nasmusic.app.ui.screens.ForYouScreen
import org.nasmusic.app.ui.screens.GenreScreen
import org.nasmusic.app.ui.screens.HomeScreen
import org.nasmusic.app.ui.screens.LibraryScreen
import org.nasmusic.app.ui.screens.NowPlayingScreen
import org.nasmusic.app.ui.screens.PlaylistScreen
import org.nasmusic.app.ui.screens.SmartEditorScreen
import org.nasmusic.app.ui.screens.SmartPlaylistScreen
import org.nasmusic.app.ui.screens.QueuesScreen
import org.nasmusic.app.ui.screens.GlobalSearchScreen
import org.nasmusic.app.ui.screens.SettingsScreen
import org.nasmusic.app.ui.screens.SmartListScreen
import org.nasmusic.app.ui.screens.BlendScreen
import org.nasmusic.app.ui.screens.ReleaseRadarScreen
import org.nasmusic.app.ui.screens.WrappedScreen
import org.nasmusic.app.ui.screens.ToGetScreen
import org.nasmusic.app.ui.screens.TodayScreen
import org.nasmusic.app.ui.theme.NasMusicTheme

/**
 * Which shell to draw (docs/v3-ux.md §6). A phone on its side is wide but short: it gets the rail
 * and full-screen Now Playing, not the tablet side pane (which needs height too).
 */
enum class ShellLayout {
    PHONE, RAIL, TABLET_SIDE_PANE;

    companion object {
        fun of(widthDp: Int, heightDp: Int): ShellLayout = when {
            widthDp >= 840 && heightDp >= 600 -> TABLET_SIDE_PANE
            widthDp >= 600 -> RAIL
            else -> PHONE
        }
    }
}

private data class Dest(val route: String, val label: String, val icon: ImageVector)

/** docs/v3-ux.md §2: five destinations. */
private val dests = listOf(
    Dest("home", "Home", Icons.Default.Home),
    Dest("foryou", "For You", Icons.Default.AutoAwesome),
    Dest("search", "Search", Icons.Default.Search),
    Dest("library", "Library", Icons.Default.LibraryMusic),
    Dest("queue", "Queue", Icons.AutoMirrored.Filled.QueueMusic),
)

@Composable
fun NasMusicRoot(c: AppContainer) {
    val settings by c.settings.settings.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    val motion = remember(settings.animations) { MotionLevel.from(settings.animations, MotionLevel.osAnimatorScale(ctx)) }
    NasMusicTheme(accent = Color(settings.accent)) {
        CompositionLocalProvider(LocalMotion provides motion) {
            val config by c.secureStore.config.collectAsStateWithLifecycle()
            Surface(color = MaterialTheme.colorScheme.background) {
                if (config?.isPaired != true) ConnectScreen(c) else MainScaffold(c)
            }
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
    LaunchedEffect(Unit) { c.ui.messages.collect { ui.toast = it } }
    // Widgets and quick-settings tiles open Now Playing or a pinned/recent item.
    val launchScope = androidx.compose.runtime.rememberCoroutineScope()
    LaunchedEffect(Unit) {
        LaunchRequests.pending.collect { r ->
            if (r == null) return@collect
            LaunchRequests.pending.value = null
            when (r) {
                is LaunchRequests.Request.Route -> ui.go(r.route)
                is LaunchRequests.Request.Item -> openShelfItem(c, ui, launchScope, r.item)
            }
        }
    }
    LaunchedEffect(current) { org.nasmusic.app.diag.CrashLog.currentScreen = current ?: "?" }
    // Offer a bug report once after a crash.
    LaunchedEffect(Unit) { if (org.nasmusic.app.diag.CrashLog.hasUnsentCrash()) ui.bugReport = true }
    val conf = LocalConfiguration.current
    val layout = ShellLayout.of(conf.screenWidthDp, conf.screenHeightDp)
    val wide = layout != ShellLayout.PHONE
    val sidePane = layout == ShellLayout.TABLET_SIDE_PANE
    val fullScreenNow = current == "now" && !sidePane
    // With the side pane showing Now Playing, the main area shows the Queue instead (never twice).
    LaunchedEffect(sidePane, current) { if (sidePane && current == "now") nav.goTab("queue") }
    val motion = LocalMotion.current

    CompositionLocalProvider(LocalUi provides ui) {
        Scaffold(
            // safeDrawing = system bars + display cutout + keyboard: nothing under the camera notch in landscape.
            contentWindowInsets = WindowInsets.safeDrawing,
            snackbarHost = { SnackbarHost(snack) },
            bottomBar = {
                if (!wide && !fullScreenNow) {
                    Column(Modifier.background(MaterialTheme.colorScheme.background)) {
                        MiniPlayer(c)
                        NavigationBar(containerColor = MaterialTheme.colorScheme.background, windowInsets = WindowInsets.safeDrawing.only(WindowInsetsSides.Bottom + WindowInsetsSides.Horizontal)) {
                            dests.forEach { d ->
                                NavigationBarItem(
                                    selected = current == d.route,
                                    onClick = { nav.goTab(d.route) },
                                    icon = { Icon(d.icon, null) },
                                    label = { Text(d.label) },
                                )
                            }
                        }
                    }
                }
            },
        ) { padding ->
            Row(Modifier.fillMaxSize().padding(padding)) {
                if (wide) {
                    // The Scaffold already applied the insets to this Row.
                    NavigationRail(containerColor = MaterialTheme.colorScheme.background, windowInsets = WindowInsets(0)) {
                        dests.forEach { d ->
                            NavigationRailItem(
                                selected = current == d.route,
                                onClick = { nav.goTab(d.route) },
                                icon = { Icon(d.icon, d.label) },
                                // A phone on its side is ~360 dp tall: labelled items wouldn't all fit.
                                label = if (conf.screenHeightDp >= 480) ({ Text(d.label) }) else null,
                                alwaysShowLabel = conf.screenHeightDp >= 480,
                            )
                        }
                    }
                }
                Column(Modifier.weight(1f).fillMaxHeight()) {
                    NavHost(
                        nav, startDestination = "home", modifier = Modifier.weight(1f),
                        enterTransition = { if (motion.enabled) fadeIn(motion.spec()) else EnterTransition.None },
                        exitTransition = { if (motion.enabled) fadeOut(motion.spec()) else ExitTransition.None },
                    ) { routes(c, motion) }
                    if (wide && !sidePane && current != "now") MiniPlayer(c)
                }
                if (sidePane) {
                    VerticalDivider()
                    Box(Modifier.width((conf.screenWidthDp * 0.38f).dp).fillMaxHeight()) {
                        NowPlayingScreen(c, compact = true)
                    }
                }
            }
            GlobalSheets(c)
        }
    }
}

private fun NavGraphBuilder.routes(c: AppContainer, motion: MotionLevel) {
    composable("home") { HomeScreen(c) }
    composable("foryou") { ForYouScreen(c) }
    composable("search") { GlobalSearchScreen(c) }
    composable("library") { LibraryScreen(c) }
    composable("queue") { QueuesScreen(c) }
    composable(
        "now",
        enterTransition = { if (motion.enabled) slideInVertically(motion.spec(1.3f)) { it } else EnterTransition.None },
        exitTransition = { if (motion.enabled) slideOutVertically(motion.spec(1.3f)) { it } else ExitTransition.None },
    ) { NowPlayingScreen(c) }
    composable("folders") { FoldersScreen(c, "") }
    composable("folder/{path}") { e -> FoldersScreen(c, android.net.Uri.decode(e.arguments?.getString("path").orEmpty())) }
    composable("album/{id}") { e -> AlbumScreen(c, e.arguments?.getString("id").orEmpty()) }
    composable("artist/{id}") { e -> ArtistScreen(c, e.arguments?.getString("id").orEmpty()) }
    composable("genre/{name}") { e -> GenreScreen(c, android.net.Uri.decode(e.arguments?.getString("name").orEmpty())) }
    composable("playlist/{id}") { e -> PlaylistScreen(c, e.arguments?.getString("id").orEmpty()) }
    composable("smart/{id}") { e -> SmartPlaylistScreen(c, e.arguments?.getString("id").orEmpty()) }
    composable("smart-edit/{param}") { e -> SmartEditorScreen(c, android.net.Uri.decode(e.arguments?.getString("param").orEmpty())) }
    composable("list/{kind}") { e -> SmartListScreen(c, e.arguments?.getString("kind").orEmpty()) }
    composable("toget") { ToGetScreen(c) }
    composable("release-radar") { ReleaseRadarScreen(c) }
    composable("wrapped/{year}") { e -> WrappedScreen(c, e.arguments?.getString("year").orEmpty()) }
    composable("blend/{id}") { e -> BlendScreen(c, e.arguments?.getString("id").orEmpty()) }
    composable("settings") { SettingsScreen(c) }
    composable("equalizer") { EqualizerScreen(c) }
    composable("mixes") { TodayScreen(c) }
    composable("discover") { DiscoverScreen(c) }
    composable("account") { AccountScreen(c) }
    composable("admin") { AdminScreen(c) }
    composable("data") { DataSettingsScreen(c) }
    composable("downloads") { DownloadsScreen(c) }
    composable("uploads") { org.nasmusic.app.ui.screens.UploadScreen(c) }
}

private fun NavHostController.goTab(route: String) = navigate(route) {
    popUpTo(graph.findStartDestination().id) { saveState = true }
    launchSingleTop = true
    restoreState = true
}

/**
 * Mini player (docs/v3-ux.md §2): cover, title/artist, play/pause, next, progress line on top.
 * Swipe left/right to skip; tap or swipe up opens Now Playing. Reads "Playing on X" as a remote.
 */
@Composable
fun MiniPlayer(c: AppContainer) {
    val ui = LocalUi.current
    val s by c.player.state.collectAsStateWithLifecycle()
    LaunchedEffect(s.isPlaying) { while (s.isPlaying) { c.player.refresh(); kotlinx.coroutines.delay(1000) } }
    val item = s.item ?: return
    var dx by remember { mutableFloatStateOf(0f) }
    var dy by remember { mutableFloatStateOf(0f) }
    Column(
        Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .pointerInput(Unit) {
                detectDragGestures(
                    onDragStart = { dx = 0f; dy = 0f },
                    onDragEnd = {
                        when {
                            dy < -60 && abs(dy) > abs(dx) -> ui.go("now")
                            dx < -80 -> c.player.next()
                            dx > 80 -> c.player.previous()
                        }
                    },
                ) { change, amount -> change.consume(); dx += amount.x; dy += amount.y }
            }
            .clickable { ui.go("now") },
    ) {
        LinearProgressIndicator(
            progress = { if (s.durationMs > 0) (s.positionMs.toFloat() / s.durationMs).coerceIn(0f, 1f) else 0f },
            modifier = Modifier.fillMaxWidth(),
            trackColor = Color.Transparent,
        )
        Row(Modifier.padding(horizontal = 10.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            AlbumArt(item.mediaMetadata.extras?.getString("nas.albumId"), 44.dp)
            Column(Modifier.weight(1f).padding(horizontal = 12.dp)) {
                Text(item.mediaMetadata.title?.toString().orEmpty(), maxLines = 1, overflow = TextOverflow.Ellipsis)
                val remote = s.remote
                Text(
                    if (remote != null) remote.label else item.mediaMetadata.artist?.toString().orEmpty(),
                    style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    color = if (remote != null) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            IconButton(onClick = c.player::togglePlay) { Icon(if (s.isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow, "Play/pause") }
            IconButton(onClick = c.player::next) { Icon(Icons.Default.SkipNext, "Next") }
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
    }
}
