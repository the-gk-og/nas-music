package org.homebeat.wear.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.navigation.NavHostController
import androidx.wear.compose.navigation.SwipeDismissableNavHost
import androidx.wear.compose.navigation.composable
import androidx.wear.compose.navigation.rememberSwipeDismissableNavController
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch
import org.homebeat.wear.WearApp
import org.homebeat.wear.sleep.SleepWatch
import org.homebeat.wear.sleep.SleepWatchService
import org.homebeat.wear.ui.screens.AlbumsScreen
import org.homebeat.wear.ui.screens.DevicesScreen
import org.homebeat.wear.ui.screens.DownloadsScreen
import org.homebeat.wear.ui.screens.FeedScreen
import org.homebeat.wear.ui.screens.HomeMenu
import org.homebeat.wear.ui.screens.NowPlayingScreen
import org.homebeat.wear.ui.screens.PlaylistsScreen
import org.homebeat.wear.ui.screens.QueueScreen
import org.homebeat.wear.ui.screens.SettingsScreen
import org.homebeat.wear.ui.screens.SignInScreen
import org.homebeat.wear.ui.screens.TrackListScreen

class MainActivity : ComponentActivity() {
    private var nav: NavHostController? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val c = (application as WearApp).container
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                c.ui.messages.collect { Toast.makeText(this@MainActivity, it, Toast.LENGTH_SHORT).show() }
            }
        }
        // Sleep detection, "start by itself at night": only while the app is open, because Android won't let
        // a health foreground service start from the background.
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                combine(c.player.ui, c.connect.state, c.settings.state) { ui, s, p -> Triple(ui, s, p) }.collect { (ui, s, p) ->
                    if (!p.sleepEnabled || !p.sleepAutoAtNight || c.sleep.watching) return@collect
                    if (System.currentTimeMillis() < c.sleep.noAutoUntilMs || !SleepWatch.isNight(java.time.LocalTime.now())) return@collect
                    val kind = if (ui.isRemote) s?.media?.kind else null
                    if (SleepWatch.eligible(ui.playing, kind, p.sleepSpokenOnly)) SleepWatchService.start(this@MainActivity)
                }
            }
        }
        val startAt = intent?.getStringExtra(EXTRA_OPEN)
        setContent {
            NasTheme {
                val cfg by c.secureStore.config.collectAsState()
                if (cfg?.isPaired != true) {
                    SignInScreen(c)
                    return@NasTheme
                }
                // The Connect socket only runs while the app is on screen (or playback/tile need it).
                DisposableEffect(Unit) {
                    c.connect.acquire(HOLDER)
                    onDispose { c.connect.release(HOLDER) }
                }
                val navController = rememberSwipeDismissableNavController()
                nav = navController
                LaunchedEffect(startAt) { if (startAt == "now_playing") navController.navigate("now") }
                val openList: (String, String) -> Unit = { kind, id -> navController.navigate("list/$kind/$id") }
                val toNow: () -> Unit = { navController.navigate("now") { launchSingleTop = true } }
                SwipeDismissableNavHost(navController = navController, startDestination = "menu") {
                    composable("menu") { HomeMenu(c) { navController.navigate(it) } }
                    composable("now") {
                        NowPlayingScreen(c, openQueue = { navController.navigate("queue") }, openDevices = { navController.navigate("devices") })
                    }
                    composable("queue") { QueueScreen(c) { navController.popBackStack() } }
                    composable("devices") { DevicesScreen(c) { navController.popBackStack() } }
                    composable("feed") { FeedScreen(c, openList, toNow) }
                    composable("playlists") { PlaylistsScreen(c, openList) }
                    composable("albums") { AlbumsScreen(c, openList) }
                    composable("downloads") { DownloadsScreen(c, openList) }
                    composable("settings") { SettingsScreen(c) }
                    composable("list/{kind}/{id}") { entry ->
                        TrackListScreen(
                            c,
                            entry.arguments?.getString("kind").orEmpty(),
                            entry.arguments?.getString("id").orEmpty(),
                            toNow,
                        )
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.getStringExtra(EXTRA_OPEN) == "now_playing") nav?.navigate("now") { launchSingleTop = true }
    }

    companion object {
        const val EXTRA_OPEN = "open"
        private const val HOLDER = "ui"
    }
}
