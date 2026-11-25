package org.homebeat.app.widget

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.ColorFilter
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.LocalSize
import androidx.glance.action.Action
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.ContentScale
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxHeight
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider
import kotlinx.coroutines.launch
import org.homebeat.app.MainActivity
import org.homebeat.app.R
import org.homebeat.app.container
import org.homebeat.app.data.api.ApiFactory
import org.homebeat.app.data.api.ShelfItem
import org.homebeat.app.ui.LaunchRequests

/**
 * Resizable home-screen player (Jetpack Glance). Small: cover + play/pause. Medium: + title/artist,
 * previous/next and a heart. Large: + "Play something for now" and three pinned/recent items.
 * Re-rendered by [WidgetHub] when what's playing changes; no periodic updates.
 */
class NowPlayingWidget : GlanceAppWidget() {
    override val sizeMode = SizeMode.Responsive(setOf(SMALL, MEDIUM, LARGE))

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val c = context.container
        WidgetHub.start(context, c)
        WidgetHub.refreshShortcuts(context.applicationContext, c)
        provideContent {
            val s by WidgetHub.state.collectAsState()
            val cover by WidgetHub.cover.collectAsState()
            val shortcuts by WidgetHub.shortcuts.collectAsState()
            val settings by c.settings.settings.collectAsState()
            val pal = palette(settings.accent)
            val size = LocalSize.current
            val art = cover?.takeIf { it.first == s.albumId }?.second
            when (WidgetStates.sizeFor(size.width.value, size.height.value)) {
                WidgetSize.SMALL -> Small(context, s, art, pal)
                WidgetSize.MEDIUM -> Medium(context, s, art, pal)
                WidgetSize.LARGE -> Large(context, s, art, shortcuts, pal)
            }
        }
    }

    companion object {
        val SMALL = DpSize(60.dp, 60.dp)
        val MEDIUM = DpSize(220.dp, 70.dp)
        val LARGE = DpSize(220.dp, 200.dp)
    }
}

class NowPlayingWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = NowPlayingWidget()
}

/** Runs a widget button. Work goes to the app scope so a slow server never times out the broadcast. */
class WidgetActionCallback : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val app = context.applicationContext
        app.container.scope.launch {
            when (parameters[KEY]) {
                PRIMARY -> PlaybackControls.primary(app)
                NEXT -> PlaybackControls.send(app, PlaybackControls.Command.NEXT)
                PREVIOUS -> PlaybackControls.send(app, PlaybackControls.Command.PREVIOUS)
                FAVOURITE -> PlaybackControls.toggleFavourite(app)
                RADIO -> PlaybackControls.playSomethingForNow(app)
            }
        }
    }

    companion object {
        val KEY = ActionParameters.Key<String>("cmd")
        const val PRIMARY = "primary"
        const val NEXT = "next"
        const val PREVIOUS = "prev"
        const val FAVOURITE = "fav"
        const val RADIO = "radio"
    }
}

private data class Palette(
    val bg: ColorProvider,
    val surface: ColorProvider,
    val text: ColorProvider,
    val dim: ColorProvider,
    val accent: ColorProvider,
    val onAccent: ColorProvider,
)

/** The app's true-black look; the accent is Material You's on Android 12+, otherwise the app's accent setting. */
private fun palette(accent: Long): Palette {
    val dynamic = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S
    return Palette(
        bg = ColorProvider(Color.Black),
        surface = ColorProvider(Color(0xFF1C1C1C)),
        text = ColorProvider(Color.White),
        dim = ColorProvider(Color(0xFFB8B8B8)),
        accent = if (dynamic) ColorProvider(android.R.color.system_accent1_200) else ColorProvider(Color(accent)),
        onAccent = if (dynamic) ColorProvider(android.R.color.system_accent1_900) else ColorProvider(Color(0xFF00210E)),
    )
}

private fun cmd(name: String): Action = actionRunCallback<WidgetActionCallback>(actionParametersOf(WidgetActionCallback.KEY to name))

private fun openNowPlaying(ctx: Context): Action = actionStartActivity(
    Intent(ctx, MainActivity::class.java)
        .setData(Uri.parse("homebeat://route/now"))
        .putExtra(LaunchRequests.EXTRA_ROUTE, "now")
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
)

private fun openItem(ctx: Context, item: ShelfItem): Action = actionStartActivity(
    Intent(ctx, MainActivity::class.java)
        .setData(Uri.parse("homebeat://item/${Uri.encode(item.type)}/${Uri.encode(item.id)}"))
        .putExtra(LaunchRequests.EXTRA_ITEM, ApiFactory.json.encodeToString(ShelfItem.serializer(), item))
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
)

@Composable
private fun Cover(ctx: Context, art: android.graphics.Bitmap?, pal: Palette, modifier: GlanceModifier, radius: Dp = 12.dp) {
    Box(modifier.cornerRadius(radius).background(pal.surface).clickable(openNowPlaying(ctx)), contentAlignment = Alignment.Center) {
        if (art != null) {
            Image(ImageProvider(art), "Open Now Playing", GlanceModifier.fillMaxSize(), contentScale = ContentScale.Crop)
        } else {
            Image(ImageProvider(R.drawable.ic_w_note), "Open Now Playing", GlanceModifier.size(32.dp), colorFilter = ColorFilter.tint(pal.dim))
        }
    }
}

@Composable
private fun IconBtn(res: Int, desc: String, tint: ColorProvider, action: Action, size: Dp = 40.dp, bg: ColorProvider? = null) {
    var m = GlanceModifier.size(size)
    if (bg != null) m = m.background(bg).cornerRadius(size / 2)
    Box(m.clickable(action), contentAlignment = Alignment.Center) {
        Image(ImageProvider(res), desc, GlanceModifier.size(size * 0.6f), colorFilter = ColorFilter.tint(tint))
    }
}

@Composable
private fun PlayButton(s: PlayingInfo, pal: Palette, size: Dp) = when (WidgetStates.primaryAction(s)) {
    PrimaryAction.PLAY_SOMETHING -> IconBtn(R.drawable.ic_w_sparkle, WidgetStates.PLAY_SOMETHING, pal.onAccent, cmd(WidgetActionCallback.PRIMARY), size, pal.accent)
    else -> if (s.isPlaying) IconBtn(R.drawable.ic_w_pause, "Pause", pal.onAccent, cmd(WidgetActionCallback.PRIMARY), size, pal.accent)
    else IconBtn(R.drawable.ic_w_play, "Play", pal.onAccent, cmd(WidgetActionCallback.PRIMARY), size, pal.accent)
}

@Composable
private fun Titles(s: PlayingInfo, pal: Palette, modifier: GlanceModifier = GlanceModifier) {
    Column(modifier) {
        Text(
            if (s.hasTrack) s.title.ifBlank { "Unknown song" } else WidgetStates.PLAY_SOMETHING,
            style = TextStyle(color = pal.text, fontSize = 15.sp, fontWeight = FontWeight.Bold), maxLines = 1,
        )
        Text(
            WidgetStates.subtitle(s),
            // "Playing on Kitchen" in the accent, so it's obvious the buttons control another device.
            style = TextStyle(color = if (s.deviceLabel != null) pal.accent else pal.dim, fontSize = 13.sp), maxLines = 1,
        )
    }
}

@Composable
private fun Transport(s: PlayingInfo, pal: Palette) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        IconBtn(R.drawable.ic_w_prev, "Previous", pal.text, cmd(WidgetActionCallback.PREVIOUS))
        Spacer(GlanceModifier.width(4.dp))
        PlayButton(s, pal, 44.dp)
        Spacer(GlanceModifier.width(4.dp))
        IconBtn(R.drawable.ic_w_next, "Next", pal.text, cmd(WidgetActionCallback.NEXT))
        if (s.hasTrack) {
            Spacer(GlanceModifier.width(4.dp))
            IconBtn(
                if (s.starred) R.drawable.ic_w_heart else R.drawable.ic_w_heart_outline,
                if (s.starred) "Remove from favourites" else "Favourite",
                if (s.starred) pal.accent else pal.text, cmd(WidgetActionCallback.FAVOURITE),
            )
        }
    }
}

@Composable
private fun Small(ctx: Context, s: PlayingInfo, art: android.graphics.Bitmap?, pal: Palette) {
    Box(GlanceModifier.fillMaxSize().background(pal.bg).cornerRadius(20.dp), contentAlignment = Alignment.BottomEnd) {
        Cover(ctx, art, pal, GlanceModifier.fillMaxSize(), 20.dp)
        Box(GlanceModifier.padding(6.dp)) { PlayButton(s, pal, 40.dp) }
    }
}

@Composable
private fun Medium(ctx: Context, s: PlayingInfo, art: android.graphics.Bitmap?, pal: Palette) {
    Row(GlanceModifier.fillMaxSize().background(pal.bg).cornerRadius(20.dp).padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
        val h = (LocalSize.current.height - 16.dp).coerceAtMost(120.dp)
        Cover(ctx, art, pal, GlanceModifier.size(h).fillMaxHeight())
        Spacer(GlanceModifier.width(10.dp))
        Column(GlanceModifier.defaultWeight().clickable(openNowPlaying(ctx))) {
            Titles(s, pal)
            Spacer(GlanceModifier.height(4.dp))
            Transport(s, pal)
        }
    }
}

@Composable
private fun Large(ctx: Context, s: PlayingInfo, art: android.graphics.Bitmap?, shortcuts: List<Shortcut>, pal: Palette) {
    Column(GlanceModifier.fillMaxSize().background(pal.bg).cornerRadius(20.dp).padding(12.dp)) {
        Row(GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Cover(ctx, art, pal, GlanceModifier.size(88.dp))
            Spacer(GlanceModifier.width(12.dp))
            Column(GlanceModifier.defaultWeight().clickable(openNowPlaying(ctx))) {
                Titles(s, pal)
                Spacer(GlanceModifier.height(4.dp))
                Transport(s, pal)
            }
        }
        Spacer(GlanceModifier.height(10.dp))
        Row(
            GlanceModifier.fillMaxWidth().height(40.dp).cornerRadius(20.dp).background(pal.surface).clickable(cmd(WidgetActionCallback.RADIO)),
            verticalAlignment = Alignment.CenterVertically, horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Image(ImageProvider(R.drawable.ic_w_sparkle), null, GlanceModifier.size(18.dp), colorFilter = ColorFilter.tint(pal.accent))
            Spacer(GlanceModifier.width(8.dp))
            Text(WidgetStates.PLAY_SOMETHING, style = TextStyle(color = pal.text, fontSize = 14.sp, fontWeight = FontWeight.Medium), maxLines = 1)
        }
        if (shortcuts.isNotEmpty()) {
            Spacer(GlanceModifier.height(10.dp))
            Row(GlanceModifier.fillMaxWidth()) {
                shortcuts.forEachIndexed { i, sc ->
                    if (i > 0) Spacer(GlanceModifier.width(8.dp))
                    Column(GlanceModifier.defaultWeight().clickable(openItem(ctx, sc.item)), horizontalAlignment = Alignment.CenterHorizontally) {
                        Box(GlanceModifier.size(56.dp).cornerRadius(10.dp).background(pal.surface), contentAlignment = Alignment.Center) {
                            if (sc.art != null) Image(ImageProvider(sc.art), sc.item.title, GlanceModifier.fillMaxSize(), contentScale = ContentScale.Crop)
                            else Image(ImageProvider(R.drawable.ic_w_note), sc.item.title, GlanceModifier.size(24.dp), colorFilter = ColorFilter.tint(pal.dim))
                        }
                        Text(sc.item.title, style = TextStyle(color = pal.dim, fontSize = 12.sp), maxLines = 1)
                    }
                }
            }
        }
    }
}
