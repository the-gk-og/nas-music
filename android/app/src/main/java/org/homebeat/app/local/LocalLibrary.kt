package org.homebeat.app.local

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.database.ContentObserver
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.util.Size
import androidx.core.content.ContextCompat
import java.io.ByteArrayOutputStream
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.homebeat.app.data.api.Track

/**
 * Music stored on the phone (Settings → "Music on this phone", off by default). Reads MediaStore, the same index
 * every music player uses, so nothing is copied and Homebeat's own downloads (app-private storage, never in
 * MediaStore) can't show up twice. Optionally limited to chosen folders. Rescans when MediaStore changes.
 */
class LocalLibrary(private val context: Context, private val scope: CoroutineScope) {
    private val prefs = context.getSharedPreferences("local_music", Context.MODE_PRIVATE)

    private val _enabled = MutableStateFlow(prefs.getBoolean(KEY_ON, false))
    /** The Settings switch. Songs only show while this is on AND the permission is granted. */
    val enabled: StateFlow<Boolean> = _enabled.asStateFlow()

    private val _folders = MutableStateFlow(prefs.getStringSet(KEY_FOLDERS, emptySet()).orEmpty().toSet())
    /** Folders to include; empty = all music on the phone. */
    val folders: StateFlow<Set<String>> = _folders.asStateFlow()

    private val _tracks = MutableStateFlow<List<Track>>(emptyList())
    /** Phone songs in the chosen folders; empty while switched off or without permission. */
    val tracks: StateFlow<List<Track>> = _tracks.asStateFlow()

    private val _allFolders = MutableStateFlow<List<LocalFolder>>(emptyList())
    /** Every folder with music (for the folder picker), regardless of the choice. */
    val allFolders: StateFlow<List<LocalFolder>> = _allFolders.asStateFlow()

    private val _scanning = MutableStateFlow(false)
    val scanning: StateFlow<Boolean> = _scanning.asStateFlow()

    private var observer: ContentObserver? = null
    private var pending: Job? = null

    init { if (_enabled.value) start() }

    fun hasPermission(): Boolean = ContextCompat.checkSelfPermission(context, PERMISSION) == PackageManager.PERMISSION_GRANTED

    fun setEnabled(on: Boolean) {
        prefs.edit().putBoolean(KEY_ON, on).apply()
        _enabled.value = on
        if (on) start() else stop()
    }

    fun setFolders(chosen: Set<String>) {
        prefs.edit().putStringSet(KEY_FOLDERS, chosen).apply()
        _folders.value = chosen
        rescan()
    }

    /** Re-reads MediaStore (after the permission is granted, or on pull to refresh). */
    fun rescan() {
        if (!_enabled.value) return
        pending?.cancel()
        pending = scope.launch { scan() }
    }

    private fun start() {
        if (observer == null) {
            val o = object : ContentObserver(Handler(Looper.getMainLooper())) {
                override fun onChange(selfChange: Boolean) {
                    // MediaStore sends bursts while a folder is copied in: wait for it to settle.
                    pending?.cancel()
                    pending = scope.launch { delay(2_000); scan() }
                }
            }
            runCatching { context.contentResolver.registerContentObserver(MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, true, o) }
                .onSuccess { observer = o }
        }
        rescan()
    }

    private fun stop() {
        observer?.let { runCatching { context.contentResolver.unregisterContentObserver(it) } }
        observer = null
        pending?.cancel()
        _tracks.value = emptyList()
        _allFolders.value = emptyList()
    }

    private suspend fun scan() = withContext(Dispatchers.IO) {
        if (!hasPermission()) { _tracks.value = emptyList(); _allFolders.value = emptyList(); return@withContext }
        _scanning.value = true
        try {
            val rows = runCatching { query() }.getOrElse {
                android.util.Log.w(org.homebeat.app.diag.CrashLog.TAG, "local music scan failed", it); emptyList()
            }
            _allFolders.value = LocalIds.folders(rows.map { it.second })
            val chosen = _folders.value
            if (_enabled.value) _tracks.value = rows.filter { LocalIds.inFolders(it.second, chosen) }.map { it.first }
        } finally {
            _scanning.value = false
        }
    }

    /** Every music file MediaStore knows, with its folder; Homebeat's own files left out. */
    private fun query(): List<Pair<Track, String>> {
        val cols = buildList {
            add(MediaStore.Audio.Media._ID); add(MediaStore.Audio.Media.TITLE); add(MediaStore.Audio.Media.DISPLAY_NAME)
            add(MediaStore.Audio.Media.ARTIST); add(MediaStore.Audio.Media.ARTIST_ID)
            add(MediaStore.Audio.Media.ALBUM); add(MediaStore.Audio.Media.ALBUM_ID)
            add(MediaStore.Audio.Media.DURATION); add(MediaStore.Audio.Media.TRACK); add(MediaStore.Audio.Media.YEAR)
            if (Build.VERSION.SDK_INT >= 29) add(MediaStore.Audio.Media.RELATIVE_PATH) else @Suppress("DEPRECATION") add(MediaStore.Audio.Media.DATA)
        }.toTypedArray()
        val out = ArrayList<Pair<Track, String>>()
        val pkg = context.packageName
        context.contentResolver.query(
            MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, cols,
            "${MediaStore.Audio.Media.IS_MUSIC} != 0 AND ${MediaStore.Audio.Media.DURATION} >= ?", arrayOf(MIN_DURATION_MS.toString()),
            null,
        )?.use { cur ->
            fun idx(n: String) = cur.getColumnIndexOrThrow(n)
            val iId = idx(MediaStore.Audio.Media._ID); val iTitle = idx(MediaStore.Audio.Media.TITLE)
            val iName = idx(MediaStore.Audio.Media.DISPLAY_NAME); val iArtist = idx(MediaStore.Audio.Media.ARTIST)
            val iArtistId = idx(MediaStore.Audio.Media.ARTIST_ID); val iAlbum = idx(MediaStore.Audio.Media.ALBUM)
            val iAlbumId = idx(MediaStore.Audio.Media.ALBUM_ID); val iDur = idx(MediaStore.Audio.Media.DURATION)
            val iTrack = idx(MediaStore.Audio.Media.TRACK); val iYear = idx(MediaStore.Audio.Media.YEAR)
            val iPath = if (Build.VERSION.SDK_INT >= 29) idx(MediaStore.Audio.Media.RELATIVE_PATH) else @Suppress("DEPRECATION") idx(MediaStore.Audio.Media.DATA)
            while (cur.moveToNext()) {
                val path = cur.getString(iPath)
                val folder = if (Build.VERSION.SDK_INT >= 29) LocalIds.folderOf(path, null) else LocalIds.folderOf(null, path)
                if (LocalIds.isOwnFile(folder, pkg)) continue
                val (trackNo, discNo) = LocalIds.splitTrackNo(cur.getInt(iTrack))
                val title = cur.getString(iTitle)?.takeIf { it.isNotBlank() }
                    ?: cur.getString(iName)?.substringBeforeLast('.') ?: "Unknown"
                val t = Track(
                    id = LocalIds.track(cur.getLong(iId)),
                    title = title,
                    artistId = LocalIds.artist(cur.getLong(iArtistId)),
                    artist = cur.getString(iArtist)?.takeUnless { it.isBlank() || it == MediaStore.UNKNOWN_STRING } ?: "Unknown artist",
                    albumId = LocalIds.album(cur.getLong(iAlbumId)),
                    album = cur.getString(iAlbum)?.takeIf { it.isNotBlank() } ?: folder.substringAfterLast('/').ifEmpty { "Unknown album" },
                    trackNo = trackNo,
                    discNo = discNo,
                    year = cur.getInt(iYear).takeIf { it > 0 },
                    durationS = cur.getLong(iDur) / 1000.0,
                )
                out += t to folder
            }
        }
        return out
    }

    companion object {
        private const val KEY_ON = "enabled"
        private const val KEY_FOLDERS = "folders"
        /** Shorter clips (notification sounds, voice notes saved as music) are left out. */
        const val MIN_DURATION_MS = 20_000L

        val PERMISSION: String =
            if (Build.VERSION.SDK_INT >= 33) Manifest.permission.READ_MEDIA_AUDIO else Manifest.permission.READ_EXTERNAL_STORAGE

        /** The file to play for a `local:` track id. */
        fun contentUri(trackId: String): Uri? =
            LocalIds.mediaStoreId(trackId)?.let { ContentUris.withAppendedId(MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, it) }

        /** Cover art for a `local:album:` id that Coil can load in-process (album thumbnail on Android 10+). */
        fun artUri(albumId: String): String? {
            val id = LocalIds.albumMediaStoreId(albumId) ?: return null
            return if (Build.VERSION.SDK_INT >= 29) "content://media/external/audio/albums/$id"
            else "content://media/external/audio/albumart/$id"
        }

        /** JPEG bytes of a local album's cover for [org.homebeat.app.playback.ArtProvider] (lock screen, Auto). */
        fun artBytes(context: Context, albumId: String, size: Int): ByteArray? {
            val id = LocalIds.albumMediaStoreId(albumId) ?: return null
            return runCatching {
                if (Build.VERSION.SDK_INT >= 29) {
                    val uri = ContentUris.withAppendedId(MediaStore.Audio.Albums.EXTERNAL_CONTENT_URI, id)
                    val bmp = context.contentResolver.loadThumbnail(uri, Size(size, size), null)
                    ByteArrayOutputStream().use { o -> bmp.compress(Bitmap.CompressFormat.JPEG, 90, o); o.toByteArray() }
                } else {
                    context.contentResolver.openInputStream(Uri.parse("content://media/external/audio/albumart/$id"))?.use { it.readBytes() }
                }
            }.getOrNull()?.takeIf { it.isNotEmpty() }
        }
    }
}
