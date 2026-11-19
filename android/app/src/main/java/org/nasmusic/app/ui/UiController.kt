package org.nasmusic.app.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.navigation.NavHostController
import org.nasmusic.app.data.api.Track

/** App-wide UI actions and sheets (song menu, song info, devices…), shared through [LocalUi]. */
class UiController(val nav: NavHostController) {
    var songMenu by mutableStateOf<SongMenuTarget?>(null)
    var songInfo by mutableStateOf<String?>(null)
    var addToPlaylist by mutableStateOf<List<Track>?>(null)
    var addToQueue by mutableStateOf<List<Track>?>(null)
    /** v5: admin tag editor. */
    var tagEdit by mutableStateOf<TagEditTarget?>(null)
    /** Bumped after a tag edit so open screens reload. */
    var tagsRev by mutableStateOf(0)
    var devicesOpen by mutableStateOf(false)
    var speedOpen by mutableStateOf(false)
    var abOpen by mutableStateOf(false)
    /** Queue popup on Now Playing. */
    var queueSheet by mutableStateOf(false)
    /** Now Playing should show the lyrics side (set by "Show lyrics"). */
    var lyricsRequested by mutableStateOf(false)
    var toast by mutableStateOf<String?>(null)
    /** null = closed; false = from Settings; true = offered after a crash. */
    var bugReport by mutableStateOf<Boolean?>(null)

    fun go(route: String) = nav.navigate(route) { launchSingleTop = true }
}

/** A song "…" menu opened for [track]; [isCurrent] adds now-playing-only items. */
data class SongMenuTarget(val track: Track, val isCurrent: Boolean = false, val queueIndex: Int? = null)

val LocalUi = staticCompositionLocalOf<UiController> { error("UiController not provided") }
