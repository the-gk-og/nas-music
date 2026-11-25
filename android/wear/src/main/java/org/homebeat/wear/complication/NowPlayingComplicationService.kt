package org.homebeat.wear.complication

import android.app.PendingIntent
import android.content.Intent
import android.graphics.drawable.Icon
import androidx.wear.watchface.complications.data.ComplicationData
import androidx.wear.watchface.complications.data.ComplicationType
import androidx.wear.watchface.complications.data.LongTextComplicationData
import androidx.wear.watchface.complications.data.MonochromaticImage
import androidx.wear.watchface.complications.data.PlainComplicationText
import androidx.wear.watchface.complications.data.ShortTextComplicationData
import androidx.wear.watchface.complications.datasource.ComplicationRequest
import androidx.wear.watchface.complications.datasource.SuspendingComplicationDataSourceService
import org.homebeat.wear.R
import org.homebeat.wear.WearApp
import org.homebeat.wear.nowplaying.NowPlaying
import org.homebeat.wear.ui.MainActivity

/**
 * Watch-face complication: a note icon plus the current title (short text) or title + artist
 * (long text). Tapping opens now playing. Reads only the local [NowPlaying] store (no network: the
 * system expects complication data quickly); it is pushed an update whenever that store changes.
 */
class NowPlayingComplicationService : SuspendingComplicationDataSourceService() {

    override suspend fun onComplicationRequest(request: ComplicationRequest): ComplicationData? {
        val np = (application as WearApp).container.nowPlaying.state.value
        return build(request.complicationType, np)
    }

    override fun getPreviewData(type: ComplicationType): ComplicationData? =
        build(type, NowPlaying(trackId = "preview", title = "Song title", artist = "Artist", playing = true))

    private fun build(type: ComplicationType, np: NowPlaying): ComplicationData? {
        val tap = PendingIntent.getActivity(
            this, 1, Intent(this, MainActivity::class.java).putExtra(MainActivity.EXTRA_OPEN, "now_playing"),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val icon = MonochromaticImage.Builder(
            Icon.createWithResource(this, if (np.playing) R.drawable.ic_play else R.drawable.ic_music),
        ).build()
        val title = if (np.isEmpty) "Music" else np.title.ifBlank { "Music" }
        val desc = PlainComplicationText.Builder(
            if (np.isEmpty) "Homebeat" else "${if (np.playing) "Playing" else "Paused"}: $title by ${np.artist}",
        ).build()
        return when (type) {
            ComplicationType.SHORT_TEXT -> ShortTextComplicationData.Builder(PlainComplicationText.Builder(title.take(7)).build(), desc)
                .setMonochromaticImage(icon)
                .setTapAction(tap)
                .build()
            ComplicationType.LONG_TEXT -> LongTextComplicationData.Builder(PlainComplicationText.Builder(title).build(), desc)
                .setTitle(PlainComplicationText.Builder(if (np.isEmpty) "Homebeat" else np.artist.ifBlank { " " }).build())
                .setMonochromaticImage(icon)
                .setTapAction(tap)
                .build()
            else -> null
        }
    }
}
