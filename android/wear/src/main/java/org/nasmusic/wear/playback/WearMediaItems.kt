package org.nasmusic.wear.playback

import android.net.Uri
import android.os.Bundle
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.net.RoutedCallFactory

object WearMediaItems {
    const val EXTRA_ALBUM_ID = "nas.albumId"
    const val EXTRA_ARTIST_ID = "nas.artistId"
    const val EXTRA_DURATION_S = "nas.durationS"
    /** Playlist metadata extra: the server queue (POST /queues) this playback belongs to. */
    const val EXTRA_QUEUE_ID = "nas.queueId"

    fun artUrl(albumId: String, size: Int = 300) = RoutedCallFactory.virtual("art/${Uri.encode(albumId)}?size=$size")

    fun streamUrl(trackId: String, kbps: Int) =
        RoutedCallFactory.virtual("stream/${Uri.encode(trackId)}?format=opus&bitrate=$kbps")

    /** No URI: the playback service resolves it to the offline file or a stream URL. */
    fun fromTrack(t: Track): MediaItem {
        val extras = Bundle().apply {
            putString(EXTRA_ALBUM_ID, t.albumId)
            putString(EXTRA_ARTIST_ID, t.artistId)
            putDouble(EXTRA_DURATION_S, t.durationS)
        }
        val md = MediaMetadata.Builder()
            .setTitle(t.title)
            .setArtist(t.artist)
            .setAlbumTitle(t.album)
            .apply { if (t.albumId.isNotBlank()) setArtworkUri(Uri.parse(artUrl(t.albumId))) }
            .setDurationMs((t.durationS * 1000).toLong())
            .setIsPlayable(true)
            .setIsBrowsable(false)
            .setExtras(extras)
            .build()
        return MediaItem.Builder().setMediaId(t.id).setMediaMetadata(md).build()
    }

    fun toTrack(item: MediaItem): Track {
        val md = item.mediaMetadata
        val x = md.extras
        return Track(
            id = item.mediaId,
            title = md.title?.toString().orEmpty(),
            artistId = x?.getString(EXTRA_ARTIST_ID).orEmpty(),
            artist = md.artist?.toString().orEmpty(),
            albumId = x?.getString(EXTRA_ALBUM_ID).orEmpty(),
            album = md.albumTitle?.toString().orEmpty(),
            durationS = x?.getDouble(EXTRA_DURATION_S) ?: ((md.durationMs ?: 0L) / 1000.0),
        )
    }

    fun queueMetadata(queueId: String?, name: String): MediaMetadata =
        MediaMetadata.Builder().setTitle(name).setExtras(Bundle().apply { queueId?.let { putString(EXTRA_QUEUE_ID, it) } }).build()
}
