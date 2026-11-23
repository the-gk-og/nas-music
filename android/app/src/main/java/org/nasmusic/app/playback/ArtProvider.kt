package org.nasmusic.app.playback

import android.content.ContentProvider
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.database.Cursor
import android.net.Uri
import android.os.ParcelFileDescriptor
import java.io.File
import java.io.FileNotFoundException
import java.util.concurrent.TimeUnit
import okhttp3.Request
import org.nasmusic.app.NasMusicApp

/**
 * Album art as `content://<applicationId>.art/album/<albumId>?size=256`.
 *
 * Android Auto, System UI (lock screen / media controls) and watch bridges load cover art in their own
 * process, so they can't use the app's URLs: those need the pinned self-signed certificate and the
 * session headers. This provider fetches with the app's routed, authenticated client and keeps a small
 * disk cache. It isn't exported; [grant] gives a media browser read access to the URIs it was handed.
 */
class ArtProvider : ContentProvider() {
    override fun onCreate() = true

    override fun getType(uri: Uri) = "image/jpeg"

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor {
        if (mode != "r") throw SecurityException("read-only")
        val req = parse(uri) ?: throw FileNotFoundException(uri.toString())
        val ctx = context ?: throw FileNotFoundException("no context")
        val file = cached(ctx, req) ?: throw FileNotFoundException("no art for ${req.albumId}")
        return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
    }

    private fun cached(ctx: Context, req: ArtRequest): File? {
        val dir = File(ctx.cacheDir, "art").apply { mkdirs() }
        val file = File(dir, req.fileName())
        if (file.length() > 0) { file.setLastModified(System.currentTimeMillis()); return file }
        val app = ctx.applicationContext as? NasMusicApp ?: return null
        val call = app.container.callFactory.newCall(Request.Builder().url(MediaItems.artUrl(req.albumId, req.size)).build())
        call.timeout().timeout(15, TimeUnit.SECONDS)
        val bytes = runCatching {
            call.execute().use { r -> if (r.isSuccessful) r.body?.bytes() else null }
        }.getOrNull()?.takeIf { it.isNotEmpty() } ?: return null
        // Write-then-rename so a reader never sees half a file.
        val tmp = File.createTempFile(req.fileName(), ".part", dir)
        tmp.writeBytes(bytes)
        if (!tmp.renameTo(file)) { tmp.delete(); return file.takeIf { it.length() > 0 } }
        trim(dir)
        return file
    }

    override fun query(uri: Uri, projection: Array<out String>?, selection: String?, selectionArgs: Array<out String>?, sortOrder: String?): Cursor? = null
    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?) = 0
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?) = 0

    data class ArtRequest(val albumId: String, val size: Int) {
        fun fileName(): String = albumId.replace(Regex("[^A-Za-z0-9._-]"), "_") + "_" + size + ".img"
    }

    companion object {
        const val AUTHORITY_SUFFIX = ".art"
        const val MAX_CACHE_BYTES = 64L * 1024 * 1024
        val SIZES = intArrayOf(128, 256, 512)

        /**
         * Packages that read now-playing art from the platform session: Android Auto (phone projection),
         * Android Automotive's media centre, System UI (lock screen, quick settings player).
         */
        val SYSTEM_READERS = listOf(
            "com.google.android.projection.gearhead",
            "com.android.car.media",
            "com.android.systemui",
        )

        fun authority(packageName: String) = packageName + AUTHORITY_SUFFIX

        /** Rounds the size up to one the server caches, so the disk cache isn't split across many sizes. */
        fun bucket(size: Int): Int = SIZES.firstOrNull { size <= it } ?: SIZES.last()

        fun uri(authority: String, albumId: String, size: Int): Uri = Uri.Builder()
            .scheme("content").authority(authority)
            .appendPath("album").appendPath(albumId)
            .appendQueryParameter("size", bucket(size).toString())
            .build()

        fun parse(uri: Uri): ArtRequest? {
            if (uri.scheme != "content" || uri.authority?.endsWith(AUTHORITY_SUFFIX) != true) return null
            val seg = uri.pathSegments
            if (seg.size != 2 || seg[0] != "album" || seg[1].isBlank()) return null
            val size = uri.getQueryParameter("size")?.toIntOrNull() ?: 512
            return ArtRequest(seg[1], bucket(size))
        }

        fun isOurs(uri: Uri?, authority: String): Boolean = uri?.scheme == "content" && uri.authority == authority

        /** Let [pkg] read the art URIs it was given (no-op for other URIs; ignores unknown packages). */
        fun grant(ctx: Context, pkg: String, uris: Iterable<Uri?>) {
            if (pkg == ctx.packageName) return
            val auth = authority(ctx.packageName)
            for (u in uris) {
                if (u == null || !isOurs(u, auth)) continue
                runCatching { ctx.grantUriPermission(pkg, u, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
            }
        }

        /** Oldest-first eviction once the cache is over [MAX_CACHE_BYTES]. */
        internal fun trim(dir: File, max: Long = MAX_CACHE_BYTES) {
            val files = dir.listFiles()?.filter { it.isFile } ?: return
            var total = files.sumOf { it.length() }
            if (total <= max) return
            for (f in files.sortedBy { it.lastModified() }) {
                if (total <= max) break
                total -= f.length()
                f.delete()
            }
        }
    }
}
