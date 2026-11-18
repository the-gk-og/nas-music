package org.nasmusic.app.sync

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import java.time.LocalTime
import org.nasmusic.app.container
import org.nasmusic.app.data.LibraryRepository
import org.nasmusic.app.data.api.AudioFormat
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.data.db.DownloadEntity
import org.nasmusic.app.data.prefs.AppSettings
import org.nasmusic.app.feedback.EventUploader
import org.nasmusic.app.lyrics.LyricsRepository
import org.nasmusic.app.net.RouteSelector

/**
 * Night sync: upload feedback, fetch the manifest, delete what is no longer wanted, download what
 * is missing (resumably) within the storage limit. LAN only unless "allow tunnel sync" is on.
 */
class SyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    override suspend fun doWork(): Result {
        val c = applicationContext.container
        val cfg = c.secureStore.config.value ?: return Result.success()
        if (!cfg.isPaired) return Result.success()
        val settings = c.settings.settings.value
        val manual = inputData.getBoolean(KEY_MANUAL, false)

        if (!manual) {
            val now = LocalTime.now()
            if (!SyncScheduler.inWindow(now.hour, now.minute, settings.syncHour, AppSettings.SYNC_WINDOW_HOURS)) {
                return Result.success() // outside the night window; try again tomorrow
            }
        }

        val route = c.router.refresh()
        if (!RouteSelector.syncAllowed(route, settings.allowTunnelSync)) {
            setProgress(workDataOf(KEY_STATUS to "Waiting for home network"))
            return if (manual) Result.failure(workDataOf(KEY_STATUS to "Not on the home network")) else Result.retry()
        }
        val factory = c.callFactory.pinnedTo(route)
        val api = org.nasmusic.app.data.api.ApiFactory.create(factory)
        val downloader = Downloader(factory, c.musicDir)

        return try {
            EventUploader(c.db.events(), { api.events(it) }).uploadAll()

            setProgress(workDataOf(KEY_STATUS to "Fetching manifest"))
            val mixes = api.mixesToday()
            c.library.cacheMixes(mixes)
            val favourites = api.favourites()
            c.library.cacheFavourites(favourites)
            val manifest = api.manifest(settings.favouritesFormat.wire)

            val known = HashMap<String, Track>()
            mixes.forEach { m -> m.tracks.forEach { known[it.track.id] = it.track } }
            favourites.forEach { known[it.id] = it }

            val dao = c.db.downloads()
            val local = dao.all()
            val plan = ManifestDiff.plan(manifest.items, local.map {
                LocalTrack(it.trackId, if (it.format == "flac") AudioFormat.FLAC else AudioFormat.OPUS, it.bytes, it.complete)
            })

            val lyrics = LyricsRepository(c.db.lyrics(), { api.lyrics(it) }, org.nasmusic.app.data.api.ApiFactory.json)
            for (id in plan.toDelete) {
                downloader.deleteAll(id)
                dao.delete(id)
                lyrics.drop(id)
                c.lyrics.drop(id) // also clears the app-wide in-memory copy
            }

            val keptBytes = plan.keep.sumOf { k -> local.firstOrNull { it.trackId == k.trackId }?.bytes ?: 0L }
            val sized = plan.toDownload.map { item ->
                item to ManifestDiff.plannedBytes(item, downloader.partialBytes(item.trackId, item.format), settings.streamBitrate)
            }
            val limit = settings.storageLimitMb.toLong() * 1024 * 1024
            val chosen = ManifestDiff.fitToLimit(sized, keptBytes, limit)

            var done = 0
            var failed = 0
            for (item in chosen) {
                setProgress(workDataOf(KEY_STATUS to "Downloading", KEY_DONE to done, KEY_TOTAL to chosen.size))
                // Format changed (e.g. favourites switched to FLAC): drop the old file first.
                local.firstOrNull { it.trackId == item.trackId && it.format != item.format.wire }?.let { old ->
                    java.io.File(old.path).delete()
                    dao.delete(old.trackId)
                }
                try {
                    val f = downloader.download(item.trackId, item.format, settings.streamBitrate)
                    if (ManifestDiff.sizeMismatch(item, f.length())) {
                        f.delete()
                        throw java.io.IOException("Size mismatch for ${item.trackId}")
                    }
                    dao.upsert(
                        DownloadEntity(
                            trackId = item.trackId,
                            format = item.format.wire,
                            path = f.absolutePath,
                            bytes = f.length(),
                            why = item.why,
                            mixIds = item.mixIds.joinToString(","),
                            complete = true,
                            trackJson = known[item.trackId]?.let { LibraryRepository.encodeTrack(it) },
                        ),
                    )
                    done++
                } catch (e: java.io.IOException) {
                    failed++
                }
                if (isStopped) break
            }
            // Refresh metadata/why for kept tracks (e.g. a mix track that is now also a favourite).
            for (k in plan.keep) {
                local.firstOrNull { it.trackId == k.trackId }?.let { d ->
                    dao.upsert(d.copy(why = k.why, mixIds = k.mixIds.joinToString(","),
                        trackJson = known[k.trackId]?.let { LibraryRepository.encodeTrack(it) } ?: d.trackJson))
                }
            }
            // Lyrics for everything the phone should hold, so they work offline. 404s are stored as
            // "none" markers; already-cached tracks are not asked again.
            setProgress(workDataOf(KEY_STATUS to "Fetching lyrics"))
            for (id in manifest.items.map { it.trackId }.distinct()) {
                if (isStopped) break
                lyrics.prefetch(id)
            }
            c.db.lyrics().pruneStreamed(System.currentTimeMillis() - 30L * 24 * 3600 * 1000)

            val skipped = plan.toDownload.size - chosen.size
            val summary = "Synced $done, failed $failed" + if (skipped > 0) ", $skipped over storage limit" else ""
            if (failed > 0 && !manual) Result.retry() else Result.success(workDataOf(KEY_STATUS to summary))
        } catch (e: Exception) {
            if (manual) Result.failure(workDataOf(KEY_STATUS to "Sync failed: ${e.message}")) else Result.retry()
        }
    }

    companion object {
        const val KEY_MANUAL = "manual"
        const val KEY_STATUS = "status"
        const val KEY_DONE = "done"
        const val KEY_TOTAL = "total"
    }
}
