package org.nasmusic.wear.ui

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
import kotlinx.coroutines.launch
import org.nasmusic.wear.WearApp
import org.nasmusic.wear.ui.screens.AlbumsScreen
import org.nasmusic.wear.ui.screens.DevicesScreen
import org.nasmusic.wear.ui.screens.DownloadsScreen
import org.nasmusic.wear.ui.screens.FeedScreen
import org.nasmusic.wear.ui.screens.HomeMenu
import org.nasmusic.wear.ui.screens.NowPlayingScreen
import org.nasmusic.wear.ui.screens.PlaylistsScreen
import org.nasmusic.wear.ui.screens.QueueScreen
import org.nasmusic.wear.ui.screens.SettingsScreen
import org.nasmusic.wear.ui.screens.SignInScreen
import org.nasmusic.wear.ui.screens.TrackListScreen

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
