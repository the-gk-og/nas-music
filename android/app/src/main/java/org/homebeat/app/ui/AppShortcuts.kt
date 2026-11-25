package org.homebeat.app.ui

import android.content.Context
import android.content.Intent
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.drawable.IconCompat
import org.homebeat.app.MainActivity
import org.homebeat.app.R

/**
 * Long-press the app icon: Shuffle favourites, Mix for now, Search, Downloads.
 * Published at runtime (not res/xml) so they also work in the `.debug` build, whose package name differs.
 */
object AppShortcuts {
    private data class Def(val id: String, val label: String, val long: String, val icon: Int, val action: String?, val route: String?)

    private val DEFS = listOf(
        Def("shuffle_favourites", "Shuffle favourites", "Shuffle your favourites", R.drawable.ic_sc_shuffle, LaunchRequests.ACTION_SHUFFLE_FAVOURITES, null),
        Def("mix_now", "Mix for now", "Play a mix for this moment", R.drawable.ic_sc_sparkle, LaunchRequests.ACTION_MIX_NOW, null),
        Def("search", "Search", "Search your music", R.drawable.ic_sc_search, null, "search"),
        Def("downloads", "Downloads", "Downloaded music", R.drawable.ic_sc_download, null, "downloads"),
    )

    fun publish(ctx: Context) {
        runCatching {
            val max = ShortcutManagerCompat.getMaxShortcutCountPerActivity(ctx).coerceAtLeast(1)
            val list = DEFS.take(max).mapIndexed { i, d ->
                val intent = Intent(ctx, MainActivity::class.java).setAction(Intent.ACTION_VIEW)
                    .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
                d.action?.let { intent.putExtra(LaunchRequests.EXTRA_ACTION, it) }
                d.route?.let { intent.putExtra(LaunchRequests.EXTRA_ROUTE, it) }
                ShortcutInfoCompat.Builder(ctx, d.id)
                    .setShortLabel(d.label).setLongLabel(d.long)
                    .setIcon(IconCompat.createWithResource(ctx, d.icon))
                    .setRank(i)
                    .setIntent(intent)
                    .build()
            }
            ShortcutManagerCompat.setDynamicShortcuts(ctx, list)
        }
    }

    /** Lets the launcher rank shortcuts by use. */
    fun reportUsed(ctx: Context, id: String) {
        runCatching { ShortcutManagerCompat.reportShortcutUsed(ctx, id) }
    }
}
