package org.homebeat.app.widget

import android.graphics.drawable.Icon
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import android.widget.Toast
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.homebeat.app.R
import org.homebeat.app.container
import org.homebeat.app.data.FamilyRepository
import org.homebeat.app.data.api.FamilyUpdate

/** Base: a coroutine scope that only lives while the Quick Settings panel shows the tile. */
abstract class ListeningTile : TileService() {
    private var listening: CoroutineScope? = null

    override fun onStartListening() {
        super.onStartListening()
        listening?.cancel()
        listening = CoroutineScope(SupervisorJob() + Dispatchers.Main).also { listen(it) }
    }

    override fun onStopListening() {
        listening?.cancel()
        listening = null
        super.onStopListening()
    }

    abstract fun listen(scope: CoroutineScope): Job

    protected fun Tile.subtitleCompat(text: String?) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) subtitle = text
    }
}

/**
 * Tap: play/pause where the music is (or "Play something for now" when nothing is queued). Long-press opens
 * Now Playing (MainActivity handles QS_TILE_PREFERENCES). The label is the current song.
 */
class PlaybackTileService : ListeningTile() {
    override fun listen(scope: CoroutineScope): Job {
        WidgetHub.start(this, container)
        return scope.launch { WidgetHub.state.collect { render(it) } }
    }

    private fun render(info: PlayingInfo) {
        val t = qsTile ?: return
        val ui = WidgetStates.tile(info)
        t.label = ui.label
        t.subtitleCompat(ui.subtitle)
        t.state = if (ui.active) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        t.icon = Icon.createWithResource(this, when (ui.icon) {
            TileIcon.PLAY -> R.drawable.ic_w_play
            TileIcon.PAUSE -> R.drawable.ic_w_pause
            TileIcon.SPARKLE -> R.drawable.ic_w_sparkle
        })
        t.contentDescription = listOfNotNull(ui.label, ui.subtitle).joinToString(", ")
        t.updateTile()
    }

    override fun onClick() {
        super.onClick()
        val app = applicationContext
        app.container.scope.launch { PlaybackControls.primary(app) }
    }
}

/** Family mode on/off. When an admin has locked it, the tile shows that and can't be toggled. */
class FamilyTileService : ListeningTile() {
    override fun listen(scope: CoroutineScope): Job {
        val c = container
        c.scope.launch { c.family.refresh() }
        return scope.launch { c.family.mode.collect { render(WidgetStates.familyTile(it)) } }
    }

    private fun render(ui: FamilyTileUi) {
        val t = qsTile ?: return
        t.label = getString(R.string.tile_family)
        t.subtitleCompat(ui.subtitle)
        t.state = when {
            ui.locked -> Tile.STATE_UNAVAILABLE
            ui.active -> Tile.STATE_ACTIVE
            else -> Tile.STATE_INACTIVE
        }
        t.icon = Icon.createWithResource(this, if (ui.locked) R.drawable.ic_w_lock else R.drawable.ic_w_family)
        t.contentDescription = "Family mode, ${ui.subtitle}"
        t.updateTile()
    }

    override fun onClick() {
        super.onClick()
        val c = container
        val m = c.family.mode.value
        if (m.locked) return
        c.scope.launch {
            val r = c.family.update(FamilyUpdate(enabled = !m.enabled))
            val msg = when (r) {
                is FamilyRepository.Result.Failed -> r.message
                FamilyRepository.Result.NeedsPin -> "Family mode is locked — change it in the app"
                FamilyRepository.Result.Ok -> null
            }
            if (msg != null) withContext(Dispatchers.Main) { Toast.makeText(applicationContext, msg, Toast.LENGTH_SHORT).show() }
        }
    }
}
