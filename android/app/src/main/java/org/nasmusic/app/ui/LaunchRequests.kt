package org.nasmusic.app.ui

import android.content.ComponentName
import android.content.Intent
import android.os.Build
import android.service.quicksettings.TileService
import kotlinx.coroutines.flow.MutableStateFlow
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.ShelfItem
import org.nasmusic.app.widget.FamilyTileService

/** Where MainActivity was asked to go by a widget, a quick-settings tile long-press or an app-icon shortcut. */
object LaunchRequests {
    const val EXTRA_ROUTE = "org.nasmusic.route"
    const val EXTRA_ITEM = "org.nasmusic.item"
    /** App-icon shortcuts ([AppShortcuts]) that play something rather than open a page. */
    const val EXTRA_ACTION = "org.nasmusic.action"
    const val ACTION_SHUFFLE_FAVOURITES = "shuffle_favourites"
    const val ACTION_MIX_NOW = "mix_now"

    sealed interface Request {
        data class Route(val route: String) : Request
        data class Item(val item: ShelfItem) : Request
        data class Action(val action: String) : Request
    }

    /** Consumed by the main scaffold once it is showing. */
    val pending = MutableStateFlow<Request?>(null)

    fun handle(intent: Intent?) {
        intent ?: return
        val req = when {
            intent.action == TileService.ACTION_QS_TILE_PREFERENCES -> {
                @Suppress("DEPRECATION")
                val from = if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_COMPONENT_NAME, ComponentName::class.java)
                else intent.getParcelableExtra(Intent.EXTRA_COMPONENT_NAME)
                Request.Route(if (from?.className == FamilyTileService::class.java.name) "settings" else "now")
            }
            intent.hasExtra(EXTRA_ACTION) -> intent.getStringExtra(EXTRA_ACTION)?.let { Request.Action(it) }
            intent.hasExtra(EXTRA_ITEM) -> runCatching {
                Request.Item(ApiFactory.json.decodeFromString(ShelfItem.serializer(), intent.getStringExtra(EXTRA_ITEM)!!))
            }.getOrNull()
            intent.hasExtra(EXTRA_ROUTE) -> intent.getStringExtra(EXTRA_ROUTE)?.let { Request.Route(it) }
            else -> null
        } ?: return
        pending.value = req
    }
}
