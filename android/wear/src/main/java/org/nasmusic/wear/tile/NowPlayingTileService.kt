package org.nasmusic.wear.tile

import androidx.wear.protolayout.ActionBuilders
import androidx.wear.protolayout.ColorBuilders.argb
import androidx.wear.protolayout.DimensionBuilders.dp
import androidx.wear.protolayout.DimensionBuilders.expand
import androidx.wear.protolayout.LayoutElementBuilders
import androidx.wear.protolayout.ModifiersBuilders
import androidx.wear.protolayout.ResourceBuilders
import androidx.wear.protolayout.TimelineBuilders
import androidx.wear.protolayout.material.Button
import androidx.wear.protolayout.material.ButtonColors
import androidx.wear.protolayout.material.ButtonDefaults
import androidx.wear.protolayout.material.Colors
import androidx.wear.protolayout.material.Text
import androidx.wear.protolayout.material.Typography
import androidx.wear.tiles.RequestBuilders
import androidx.wear.tiles.TileBuilders
import androidx.wear.tiles.TileService
import com.google.common.util.concurrent.ListenableFuture
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.guava.future
import org.nasmusic.wear.R
import org.nasmusic.wear.WearApp
import org.nasmusic.wear.nowplaying.NowPlaying
import org.nasmusic.wear.nowplaying.NowPlayingRefresher
import org.nasmusic.wear.ui.MainActivity

/**
 * Now playing / quick resume tile: cover, title, where it plays, and prev / play-pause / next.
 * Buttons go through [TileActionActivity] (tiles can only launch activities).
 */
class NowPlayingTileService : TileService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val c by lazy { (application as WearApp).container }
    private val refresher by lazy { NowPlayingRefresher(c) }

    override fun onTileRequest(requestParams: RequestBuilders.TileRequest): ListenableFuture<TileBuilders.Tile> =
        scope.future {
            val np = if (c.secureStore.config.value?.isPaired == true) refresher.refresh() else null
            val hasArt = np?.albumId?.let { refresher.art(it) } != null
            TileBuilders.Tile.Builder()
                .setResourcesVersion(resourcesVersion(np, hasArt))
                // Re-ask after a minute while something plays elsewhere (no push channel to tiles).
                .setFreshnessIntervalMillis(if (np?.playing == true) 60_000 else 15 * 60_000)
                .setTileTimeline(TimelineBuilders.Timeline.fromLayoutElement(layout(np, hasArt)))
                .build()
        }

    override fun onTileResourcesRequest(requestParams: RequestBuilders.ResourcesRequest): ListenableFuture<ResourceBuilders.Resources> =
        scope.future {
            val b = ResourceBuilders.Resources.Builder().setVersion(requestParams.version)
            for ((id, res) in ICONS) {
                b.addIdToImageMapping(
                    id,
                    ResourceBuilders.ImageResource.Builder()
                        .setAndroidResourceByResId(ResourceBuilders.AndroidImageResourceByResId.Builder().setResourceId(res).build())
                        .build(),
                )
            }
            val np = c.nowPlaying.state.value
            refresher.art(np.albumId)?.let { bytes ->
                b.addIdToImageMapping(
                    ART,
                    ResourceBuilders.ImageResource.Builder()
                        .setInlineResource(
                            ResourceBuilders.InlineImageResource.Builder()
                                .setData(bytes)
                                .setWidthPx(128).setHeightPx(128)
                                .setFormat(ResourceBuilders.IMAGE_FORMAT_UNDEFINED)
                                .build(),
                        )
                        .build(),
                )
            }
            b.build()
        }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun resourcesVersion(np: NowPlaying?, hasArt: Boolean) = "v1-${np?.albumId.orEmpty()}-$hasArt"

    private fun layout(np: NowPlaying?, hasArt: Boolean): LayoutElementBuilders.LayoutElement {
        val col = LayoutElementBuilders.Column.Builder()
            .setWidth(expand())
            .setHorizontalAlignment(LayoutElementBuilders.HORIZONTAL_ALIGN_CENTER)
        val open = launch(TileActionActivity.OPEN)
        if (np == null) {
            col.addContent(text("nas-music", Typography.TYPOGRAPHY_TITLE3))
            col.addContent(spacer(6))
            col.addContent(text("Open the app to sign in", Typography.TYPOGRAPHY_CAPTION2, dim = true))
            return box(col.build(), open)
        }
        if (hasArt) {
            col.addContent(
                LayoutElementBuilders.Image.Builder()
                    .setResourceId(ART)
                    .setWidth(dp(44f)).setHeight(dp(44f))
                    .setModifiers(
                        ModifiersBuilders.Modifiers.Builder()
                            .setClickable(open)
                            .setBackground(ModifiersBuilders.Background.Builder().setCorner(ModifiersBuilders.Corner.Builder().setRadius(dp(8f)).build()).build())
                            .build(),
                    )
                    .build(),
            )
            col.addContent(spacer(4))
        }
        val title = if (np.isEmpty) "Nothing playing" else np.title.ifBlank { "…" }
        val sub = when {
            np.isEmpty -> "Tap ▶ to resume"
            np.isLocal -> np.artist
            else -> "${np.artist} · on ${np.deviceName}"
        }
        col.addContent(clickableText(title, Typography.TYPOGRAPHY_TITLE3, open))
        col.addContent(text(sub, Typography.TYPOGRAPHY_CAPTION2, dim = true))
        col.addContent(spacer(8))
        col.addContent(
            LayoutElementBuilders.Row.Builder()
                .setVerticalAlignment(LayoutElementBuilders.VERTICAL_ALIGN_CENTER)
                .addContent(button(PREV, TileActionActivity.PREV, "Previous", dp(40f), primary = false))
                .addContent(spacerW(6))
                .addContent(
                    button(
                        if (np.playing) PAUSE else PLAY, TileActionActivity.PLAY_PAUSE,
                        if (np.playing) "Pause" else "Play", ButtonDefaults.DEFAULT_SIZE, primary = true,
                    ),
                )
                .addContent(spacerW(6))
                .addContent(button(NEXT, TileActionActivity.NEXT, "Next", dp(40f), primary = false))
                .build(),
        )
        return box(col.build(), null)
    }

    private fun box(content: LayoutElementBuilders.LayoutElement, click: ModifiersBuilders.Clickable?) =
        LayoutElementBuilders.Box.Builder()
            .setWidth(expand()).setHeight(expand())
            .setHorizontalAlignment(LayoutElementBuilders.HORIZONTAL_ALIGN_CENTER)
            .setVerticalAlignment(LayoutElementBuilders.VERTICAL_ALIGN_CENTER)
            .apply { click?.let { setModifiers(ModifiersBuilders.Modifiers.Builder().setClickable(it).build()) } }
            .addContent(content)
            .build()

    private fun text(s: String, typo: Int, dim: Boolean = false) =
        Text.Builder(this, s)
            .setTypography(typo)
            .setColor(argb(if (dim) 0xFFBDBDBD.toInt() else 0xFFFFFFFF.toInt()))
            .setMaxLines(1)
            .build()

    private fun clickableText(s: String, typo: Int, click: ModifiersBuilders.Clickable) =
        Text.Builder(this, s)
            .setTypography(typo)
            .setColor(argb(0xFFFFFFFF.toInt()))
            .setMaxLines(1)
            .setModifiers(ModifiersBuilders.Modifiers.Builder().setClickable(click).build())
            .build()

    private fun spacer(h: Int) = LayoutElementBuilders.Spacer.Builder().setHeight(dp(h.toFloat())).build()
    private fun spacerW(w: Int) = LayoutElementBuilders.Spacer.Builder().setWidth(dp(w.toFloat())).build()

    private fun button(icon: String, action: String, desc: String, size: androidx.wear.protolayout.DimensionBuilders.DpProp, primary: Boolean) =
        Button.Builder(this, launch(action))
            .setIconContent(icon)
            .setContentDescription(desc)
            .setSize(size)
            .setButtonColors(if (primary) ButtonColors.primaryButtonColors(COLORS) else ButtonColors.secondaryButtonColors(COLORS))
            .build()

    private fun launch(action: String): ModifiersBuilders.Clickable =
        ModifiersBuilders.Clickable.Builder()
            .setId(action)
            .setOnClick(
                ActionBuilders.LaunchAction.Builder()
                    .setAndroidActivity(
                        ActionBuilders.AndroidActivity.Builder()
                            .setPackageName(packageName)
                            .setClassName(
                                if (action == TileActionActivity.OPEN) MainActivity::class.java.name else TileActionActivity::class.java.name,
                            )
                            .addKeyToExtraMapping(TileActionActivity.EXTRA_ACTION, ActionBuilders.AndroidStringExtra.Builder().setValue(action).build())
                            .build(),
                    )
                    .build(),
            )
            .build()

    private companion object {
        const val ART = "art"
        const val PLAY = "play"
        const val PAUSE = "pause"
        const val NEXT = "next"
        const val PREV = "prev"
        val ICONS = mapOf(PLAY to R.drawable.ic_play, PAUSE to R.drawable.ic_pause, NEXT to R.drawable.ic_next, PREV to R.drawable.ic_prev)
        val COLORS = Colors(0xFF8AB4F8.toInt(), 0xFF202124.toInt(), 0xFF303134.toInt(), 0xFFFFFFFF.toInt())
    }
}
