package org.nasmusic.app.sync

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import java.io.File
import java.io.IOException
import java.time.Instant
import java.time.LocalTime
import org.nasmusic.app.container
import org.nasmusic.app.data.LibraryRepository
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.AudioFormat
import org.nasmusic.app.data.api.ManifestItem
import org.nasmusic.app.data.api.NasMusicApi
import org.nasmusic.app.data.api.SmartCandidateDto
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.data.db.DownloadDao
import org.nasmusic.app.data.db.DownloadEntity
import org.nasmusic.app.data.db.OfflinePinEntity
import org.nasmusic.app.data.prefs.AppSettings
import org.nasmusic.app.feedback.EventUploader
import org.nasmusic.app.lyrics.LyricsRepository
import org.nasmusic.app.net.RouteSelector

/**
 * Offline sync. Three kinds of downloads share the music folder:
 *  - protected: songs the user downloaded and playlists/albums kept offline. Never evicted.
 *  - smart (Smart downloads on): a rolling set chosen by [SmartPlanner] within the storage budget.
 *  - sync (Offline favourites on, smart off): the server's manifest (today's mixes + favourites).
 * Night runs need Wi-Fi + charging (WorkManager constraints) and happen inside the night window; all runs
 * are LAN only unless "allow tunnel sync" is on. [KEY_PROTECTED_ONLY] runs fetch just the protected ones.
 */
class SyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    private class Tally { var downloaded = 0; var failed = 0; var evicted = 0; var stoppedForSpace = false }

    override suspend fun doWork(): Result {
        val c = applicationContext.container
        val cfg = c.secureStore.config.value ?: return Result.success()
        if (!cfg.isPaired) return Result.success()
        val settings = c.settings.current()
        val manual = inputData.getBoolean(KEY_MANUAL, false)
        val protectedOnly = inputData.getBoolean(KEY_PROTECTED_ONLY, false)
        val dao = c.db.downloads()
        val pinsDao = c.db.pins()

        val auto = settings.smartDownloads || settings.offlineFavourites
        val hasProtected = pinsDao.all().isNotEmpty() || dao.all().any { it.isProtected }
        if (!auto && !hasProtected) return Result.success(workDataOf(KEY_STATUS to "Downloads are off"))
        if (protectedOnly && !hasProtected) return Result.success()

        if (!manual && !protectedOnly) {
            val now = LocalTime.now()
            if (!SyncScheduler.inWindow(now.hour, now.minute, settings.syncHour, AppSettings.SYNC_WINDOW_HOURS)) {
                return Result.success() // outside the night window; try again tomorrow
            }
        }

        val route = c.router.refresh()
        if (!RouteSelector.syncAllowed(route, settings.allowTunnelSync)) {
            setProgress(workDataOf(KEY_STATUS to "Waiting for home network"))
            return when {
                protectedOnly -> Result.retry()
                manual -> Result.failure(workDataOf(KEY_STATUS to "Not on the home network"))
                else -> Result.retry()
            }
        }
        val factory = c.callFactory.pinnedTo(route)
        val api = ApiFactory.create(factory)
        val downloader = Downloader(factory, c.musicDir)
        val lyrics = LyricsRepository(c.db.lyrics(), { api.lyrics(it) }, ApiFactory.json)
        val quality = DownloadQuality.from(settings.downloadQuality)
        val known = HashMap<String, Track>()
        val tally = Tally()

        suspend fun drop(id: String) {
            dao.get(id)?.path?.takeIf { it.isNotBlank() }?.let { File(it).delete() }
            downloader.deleteAll(id)
            dao.delete(id)
            lyrics.drop(id)
            c.lyrics.drop(id) // also clears the app-wide in-memory copy
            tally.evicted++
        }

        return try {
            if (!protectedOnly) EventUploader(c.db.events(), { api.events(it) }).uploadAll()

            // 1. Collections kept offline follow changes on the server; then fetch what's missing.
            refreshPins(api, pinsDao.all(), dao, known, auto, settings, ::drop)
            val wantedProtected = dao.all().filter { it.isProtected && !it.complete }
            for ((i, row) in wantedProtected.withIndex()) {
                if (isStopped || tally.stoppedForSpace) break
                setProgress(workDataOf(KEY_STATUS to "Downloading your downloads", KEY_DONE to i, KEY_TOTAL to wantedProtected.size))
                val track = known[row.trackId] ?: LibraryRepository.decodeTrackJson(row.trackJson)
                val est = SmartPlanner.estimateBytes(quality, track?.durationS, track?.flacBytes)
                fetch(downloader, dao, row.trackId, quality, est, tally) { f ->
                    // Re-read: the user may have removed the download while it was being fetched.
                    dao.get(row.trackId)?.takeIf { it.isProtected }?.copy(format = quality.format.wire, kbps = quality.kbps, path = f.absolutePath, bytes = f.length(),
                        complete = true, trackJson = track?.let(LibraryRepository::encodeTrack) ?: row.trackJson,
                        updatedAtMs = System.currentTimeMillis())
                }
            }

            // 2. The automatic set.
            var extra = ""
            if (!protectedOnly && !tally.stoppedForSpace) {
                extra = when {
                    settings.smartDownloads -> smartSync(api, dao, downloader, quality, settings, known, tally, ::drop)
                    settings.offlineFavourites -> manifestSync(api, dao, downloader, settings, known, tally, ::drop)
                    else -> ""
                }
            }

            // 3. Lyrics for everything held, so they work offline (404s are cached as "none").
            if (!protectedOnly) {
                setProgress(workDataOf(KEY_STATUS to "Fetching lyrics"))
                for (id in dao.all().filter { it.complete }.map { it.trackId }) {
                    if (isStopped) break
                    lyrics.prefetch(id)
                }
                c.db.lyrics().pruneStreamed(System.currentTimeMillis() - 30L * 24 * 3600 * 1000)
            }

            val summary = buildString {
                append("Downloaded ${tally.downloaded}")
                if (tally.evicted > 0) append(", removed ${tally.evicted}")
                if (tally.failed > 0) append(", ${tally.failed} failed")
                if (tally.stoppedForSpace) append(" · stopped: phone storage nearly full")
                if (extra.isNotBlank()) append(" · ").append(extra)
            }
            c.offline.saveStatus(SyncStatus(System.currentTimeMillis(), tally.failed == 0 && !tally.stoppedForSpace, summary,
                tally.downloaded, tally.failed, tally.evicted))
            if (tally.failed > 0 && !manual) Result.retry() else Result.success(workDataOf(KEY_STATUS to summary))
        } catch (e: Exception) {
            if (e is kotlinx.coroutines.CancellationException) throw e
            c.offline.saveStatus(SyncStatus(System.currentTimeMillis(), false, "Sync failed: ${e.message ?: e.javaClass.simpleName}",
                tally.downloaded, tally.failed, tally.evicted))
            if (manual) Result.failure(workDataOf(KEY_STATUS to "Sync failed: ${e.message}")) else Result.retry()
        }
    }

    /** Re-reads each kept collection and moves pin membership onto the matching rows. */
    private suspend fun refreshPins(
        api: NasMusicApi, pins: List<OfflinePinEntity>, dao: DownloadDao, known: MutableMap<String, Track>,
        auto: Boolean, settings: AppSettings, drop: suspend (String) -> Unit,
    ) {
        if (pins.isEmpty() && dao.all().none { it.pinKeys.isNotEmpty() }) return
        val fresh = pins.map { p ->
            val tracks = runCatching {
                when (p.type) {
                    "album" -> api.album(p.id).tracks
                    "playlist" -> api.playlist(p.id).tracks
                    "smart_playlist" -> api.smartPlaylist(p.id).tracks
                    else -> null
                }
            }.getOrNull()?.filterNot { it.missing }
            if (tracks == null) p // offline/failed: keep the last known list
            else {
                tracks.forEach { known[it.id] = it }
                p.copy(trackIds = tracks.map { it.id }.distinct().joinToString(","), refreshedAtMs = System.currentTimeMillis())
                    .also { applicationContext.container.db.pins().upsert(it) }
            }
        }
        val membership = OfflineSets.pinMembership(fresh)
        val rows = dao.all().associateBy { it.trackId }
        for ((id, keys) in membership) {
            val row = rows[id]
            if (row == null) {
                val t = known[id]
                dao.upsert(DownloadEntity(trackId = id, format = "opus", path = "", bytes = 0, why = "pinned", complete = false,
                    trackJson = t?.let(LibraryRepository::encodeTrack), origin = "manual", pins = keys.joinToString(",")))
            } else if (row.pinKeys.toSet() != keys.toSet()) {
                dao.upsert(row.copy(pins = keys.joinToString(",")))
            }
        }
        // Rows that dropped out of every collection: the automatic set may keep them; otherwise they go.
        for (row in rows.values) {
            if (row.pinKeys.isEmpty() || row.trackId in membership) continue
            val released = row.copy(pins = "")
            when {
                released.manual -> dao.upsert(released)
                !released.complete || !auto -> drop(row.trackId)
                else -> dao.upsert(released.copy(origin = if (settings.smartDownloads) "smart" else "sync"))
            }
        }
    }

    private suspend fun smartSync(
        api: NasMusicApi, dao: DownloadDao, downloader: Downloader, quality: DownloadQuality, settings: AppSettings,
        known: MutableMap<String, Track>, tally: Tally, drop: suspend (String) -> Unit,
    ): String {
        setProgress(workDataOf(KEY_STATUS to "Choosing what to keep"))
        val resp = api.smartCandidates()
        resp.items.forEach { it.track?.let { t -> known[t.id] = t } }
        // Also refresh the offline Home's cached mixes/favourites (best effort).
        runCatching { api.mixesToday() }.getOrNull()?.let { applicationContext.container.library.cacheMixes(it) }
        runCatching { api.favourites() }.getOrNull()?.let { applicationContext.container.library.cacheFavourites(it) }

        val rows = dao.all()
        val held = rows.filter { it.complete || it.isProtected }.map {
            HeldTrack(it.trackId, it.bytes, it.qualityKey, it.complete, it.isProtected)
        }
        val smartHeld = held.filter { !it.protected && it.complete }.sumOf { it.bytes }
        val budget = StorageBudget.parse(settings.smartBudget)
        val budgetBytes = budget.bytes(downloader.freeBytes(), smartHeld)
        val candidates = OfflineSets.candidates(resp.items, quality, System.currentTimeMillis())
        val plan = SmartPlanner.plan(candidates, held, quality, budgetBytes)
        val prio = candidates.associate { it.trackId to SmartPlanner.priority(it) }

        for (id in plan.evict) drop(id)
        // Leftover partials of tracks no longer wanted (and not protected).
        val wanted = (plan.keep + plan.download).map { it.trackId }.toSet()
        for (row in rows) if (!row.complete && !row.isProtected && row.trackId !in wanted) drop(row.trackId)

        for ((i, cand) in plan.download.withIndex()) {
            if (isStopped || tally.stoppedForSpace) break
            setProgress(workDataOf(KEY_STATUS to "Smart downloads", KEY_DONE to i, KEY_TOTAL to plan.download.size))
            val old = dao.get(cand.trackId)
            fetch(downloader, dao, cand.trackId, quality, cand.bytes, tally) { f ->
                if (old != null && old.path.isNotBlank() && old.path != f.absolutePath) File(old.path).delete()
                DownloadEntity(
                    trackId = cand.trackId, format = quality.format.wire, path = f.absolutePath, bytes = f.length(),
                    why = OfflineSets.primaryReason(cand.reasons), complete = true,
                    trackJson = known[cand.trackId]?.let(LibraryRepository::encodeTrack) ?: old?.trackJson,
                    origin = "smart", kbps = quality.kbps, reasons = cand.reasons.joinToString(","),
                    priority = prio[cand.trackId] ?: 0.0,
                )
            }
        }
        for (k in plan.keep) {
            dao.get(k.trackId)?.let { d ->
                dao.upsert(d.copy(origin = "smart", why = OfflineSets.primaryReason(k.reasons), reasons = k.reasons.joinToString(","),
                    priority = prio[k.trackId] ?: d.priority,
                    trackJson = known[k.trackId]?.let(LibraryRepository::encodeTrack) ?: d.trackJson))
            }
        }
        val gb = { b: Long -> "%.1f".format(b / StorageBudget.GB.toDouble()) }
        return "smart set ${plan.keep.size + plan.download.size} songs, ${gb(plan.plannedBytes)} of ${gb(budgetBytes)} GB" +
            if (plan.overBudget > 0) " (${plan.overBudget} didn't fit)" else ""
    }

    /** Offline favourites (smart off): the server's manifest, as before, but never touching protected rows. */
    private suspend fun manifestSync(
        api: NasMusicApi, dao: DownloadDao, downloader: Downloader, settings: AppSettings,
        known: MutableMap<String, Track>, tally: Tally, drop: suspend (String) -> Unit,
    ): String {
        setProgress(workDataOf(KEY_STATUS to "Fetching manifest"))
        val c = applicationContext.container
        val mixes = api.mixesToday()
        c.library.cacheMixes(mixes)
        val favourites = api.favourites()
        c.library.cacheFavourites(favourites)
        val manifest = api.manifest(settings.favouritesFormat.wire)
        mixes.forEach { m -> m.tracks.forEach { known[it.track.id] = it.track } }
        favourites.forEach { known[it.id] = it }

        val rows = dao.all()
        val protectedIds = rows.filter { it.isProtected }.map { it.trackId }.toSet()
        val local = rows.filter { !it.isProtected }
        val items = manifest.items.filter { it.trackId !in protectedIds }
        val plan = ManifestDiff.plan(items, local.map {
            LocalTrack(it.trackId, if (it.format == "flac") AudioFormat.FLAC else AudioFormat.OPUS, it.bytes, it.complete)
        })
        for (id in plan.toDelete) drop(id)

        val keptBytes = plan.keep.sumOf { k -> local.firstOrNull { it.trackId == k.trackId }?.bytes ?: 0L }
        val sized = plan.toDownload.map { item ->
            item to ManifestDiff.plannedBytes(item, downloader.partialBytes(item.trackId, item.format), settings.streamBitrate)
        }
        val chosen = ManifestDiff.fitToLimit(sized, keptBytes, settings.storageLimitMb.toLong() * 1024 * 1024)
        for ((i, item) in chosen.withIndex()) {
            if (isStopped || tally.stoppedForSpace) break
            setProgress(workDataOf(KEY_STATUS to "Downloading", KEY_DONE to i, KEY_TOTAL to chosen.size))
            // Format changed (e.g. favourites switched to FLAC): drop the old file first.
            local.firstOrNull { it.trackId == item.trackId && it.format != item.format.wire }?.let { old ->
                File(old.path).delete()
                dao.delete(old.trackId)
            }
            val est = sized.first { it.first === item }.second
            fetchManifestItem(downloader, dao, item, settings, est, known, tally)
        }
        for (k in plan.keep) {
            local.firstOrNull { it.trackId == k.trackId }?.let { d ->
                dao.upsert(d.copy(why = k.why, mixIds = k.mixIds.joinToString(","), origin = "sync",
                    trackJson = known[k.trackId]?.let(LibraryRepository::encodeTrack) ?: d.trackJson))
            }
        }
        val skipped = plan.toDownload.size - chosen.size
        return if (skipped > 0) "$skipped over storage limit" else ""
    }

    private suspend fun fetchManifestItem(
        downloader: Downloader, dao: DownloadDao, item: ManifestItem, settings: AppSettings, est: Long,
        known: Map<String, Track>, tally: Tally,
    ) {
        if (!hasRoom(downloader, est)) { tally.stoppedForSpace = true; return }
        try {
            val f = downloader.download(item.trackId, item.format, settings.streamBitrate)
            if (ManifestDiff.sizeMismatch(item, f.length())) {
                f.delete()
                throw IOException("Size mismatch for ${item.trackId}")
            }
            dao.upsert(
                DownloadEntity(
                    trackId = item.trackId, format = item.format.wire, path = f.absolutePath, bytes = f.length(),
                    why = item.why, mixIds = item.mixIds.joinToString(","), complete = true,
                    trackJson = known[item.trackId]?.let(LibraryRepository::encodeTrack), origin = "sync",
                    kbps = if (item.format == AudioFormat.OPUS) settings.streamBitrate else 0,
                ),
            )
            tally.downloaded++
        } catch (e: IOException) {
            tally.failed++
        }
    }

    private suspend fun fetch(
        downloader: Downloader, dao: DownloadDao, trackId: String, quality: DownloadQuality, est: Long, tally: Tally,
        row: suspend (File) -> DownloadEntity?,
    ) {
        if (!hasRoom(downloader, est)) { tally.stoppedForSpace = true; return }
        try {
            val f = downloader.download(trackId, quality.format, quality.kbps, kbpsInName = true)
            val r = row(f)
            if (r == null) { f.delete(); return }
            dao.upsert(r)
            tally.downloaded++
        } catch (e: IOException) {
            tally.failed++
        }
    }

    /** Leave the phone at least [RESERVE_BYTES] free whatever the budget says. */
    private fun hasRoom(downloader: Downloader, bytes: Long) = downloader.freeBytes() - bytes >= RESERVE_BYTES

    companion object {
        const val KEY_MANUAL = "manual"
        const val KEY_PROTECTED_ONLY = "protected_only"
        const val KEY_STATUS = "status"
        const val KEY_DONE = "done"
        const val KEY_TOTAL = "total"
        const val RESERVE_BYTES = 512L * 1024 * 1024
    }
}

/** Pure helpers for [SyncWorker], unit tested. */
object OfflineSets {
    /** trackId → keys of the kept collections that contain it. */
    fun pinMembership(pins: List<OfflinePinEntity>): Map<String, List<String>> {
        val out = LinkedHashMap<String, MutableList<String>>()
        for (p in pins) for (id in p.ids) out.getOrPut(id) { mutableListOf() }.let { if (p.key !in it) it += p.key }
        return out
    }

    /** Server candidates → planner candidates, sized for [quality] (full size: that's what the budget counts). */
    fun candidates(items: List<SmartCandidateDto>, quality: DownloadQuality, nowMs: Long): List<SmartCandidate> =
        items.map { it ->
            val dur = it.durationS ?: it.track?.durationS?.takeIf { d -> d > 0 }
            val size = SmartPlanner.estimateBytes(quality, dur, it.flacBytes ?: it.track?.flacBytes)
            val days = it.lastPlayedAt?.let { s -> runCatching { (nowMs - Instant.parse(s).toEpochMilli()) / 86_400_000.0 }.getOrNull() }
            SmartCandidate(it.trackId, it.reasons.toSet(), it.score, size, days)
        }

    private val ORDER = listOf("favourite", "mix", "recent", "week_mix", "release_radar", "foryou", "predicted")

    fun primaryReason(reasons: Set<String>): String = ORDER.firstOrNull { it in reasons } ?: reasons.firstOrNull() ?: "smart"
}
