package org.nasmusic.app.playback

import android.net.Uri
import android.os.Bundle
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.net.RoutedCallFactory

/** Where a play came from; matches the Event.source enum in the API. */
enum class PlaySource(val wire: String) {
    MIX("mix"), LIBRARY("library"), FAVOURITES("favourites"), MORE_LIKE("more_like"), SEARCH("search"),
}

object MediaItems {
    const val EXTRA_SOURCE = "nas.source"
    const val EXTRA_MIX_ID = "nas.mixId"
    const val EXTRA_DURATION_S = "nas.durationS"
    const val EXTRA_REASON = "nas.reason"
    const val EXTRA_STARRED = "nas.starred"
    const val EXTRA_ALBUM_ID = "nas.albumId"
    const val EXTRA_ARTIST_ID = "nas.artistId"
    const val EXTRA_SEARCH_QUERY = "nas.searchQuery"

    fun artUrl(albumId: String, size: Int = 512) = RoutedCallFactory.virtual("art/$albumId?size=$size")

    fun streamUrl(trackId: String, kbps: Int) =
        RoutedCallFactory.virtual("stream/${Uri.encode(trackId)}?format=opus&bitrate=$kbps")

    /** A MediaItem without a URI: the service resolves it to a local file or a stream URL. */
    fun fromTrack(t: Track, source: PlaySource, mixId: String? = null, reason: String? = null, searchQuery: String? = null): MediaItem {
        val extras = Bundle().apply {
            putString(EXTRA_SOURCE, source.wire)
            mixId?.let { putString(EXTRA_MIX_ID, it) }
            putDouble(EXTRA_DURATION_S, t.durationS)
            reason?.let { putString(EXTRA_REASON, it) }
            putBoolean(EXTRA_STARRED, t.starred)
            putString(EXTRA_ALBUM_ID, t.albumId)
            putString(EXTRA_ARTIST_ID, t.artistId)
            searchQuery?.let { putString(EXTRA_SEARCH_QUERY, it) }
        }
        val md = MediaMetadata.Builder()
            .setTitle(t.title)
            .setArtist(t.artist)
            .setAlbumTitle(t.album)
            .setArtworkUri(Uri.parse(artUrl(t.albumId)))
            .setTrackNumber(t.trackNo)
            .setDurationMs((t.durationS * 1000).toLong())
            .setIsPlayable(true)
            .setIsBrowsable(false)
            .setExtras(extras)
            .build()
        return MediaItem.Builder().setMediaId(t.id).setMediaMetadata(md).build()
    }

    /** Rebuilds the Track a MediaItem was made from (for menus, song info, Connect). */
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
            trackNo = md.trackNumber,
            durationS = x?.getDouble(EXTRA_DURATION_S) ?: ((md.durationMs ?: 0L) / 1000.0),
            starred = x?.getBoolean(EXTRA_STARRED) ?: false,
        )
    }
}
