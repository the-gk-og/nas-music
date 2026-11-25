package org.homebeat.app.sync

import java.time.LocalDateTime
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.homebeat.app.data.api.AudioFormat.FLAC
import org.homebeat.app.data.api.AudioFormat.OPUS
import org.homebeat.app.data.api.ManifestItem

class ManifestDiffTest {
    private fun mix(id: String) = ManifestItem(id, OPUS, "mix", listOf("m1"))
    private fun fav(id: String, f: org.homebeat.app.data.api.AudioFormat = OPUS) = ManifestItem(id, f, "favourite")

    @Test fun `downloads missing, deletes stale, keeps complete`() {
        val plan = ManifestDiff.plan(
            listOf(mix("a"), mix("b"), fav("c")),
            listOf(LocalTrack("a", OPUS, 100, true), LocalTrack("old", OPUS, 100, true)),
        )
        assertEquals(listOf("b", "c"), plan.toDownload.map { it.trackId })
        assertEquals(listOf("old"), plan.toDelete)
        assertEquals(listOf("a"), plan.keep.map { it.trackId })
    }

    @Test fun `partial downloads are resumed, not kept`() {
        val plan = ManifestDiff.plan(listOf(mix("a")), listOf(LocalTrack("a", OPUS, 50, complete = false)))
        assertEquals(listOf("a"), plan.toDownload.map { it.trackId })
        assertTrue(plan.toDelete.isEmpty())
    }

    @Test fun `format change re-downloads`() {
        val plan = ManifestDiff.plan(listOf(fav("a", FLAC)), listOf(LocalTrack("a", OPUS, 100, true)))
        assertEquals(FLAC, plan.toDownload.single().format)
        assertTrue(plan.keep.isEmpty())
    }

    @Test fun `mix tracks are downloaded before favourites`() {
        val plan = ManifestDiff.plan(listOf(fav("f1"), mix("m1"), fav("f2"), mix("m2")), emptyList())
        assertEquals(listOf("m1", "m2", "f1", "f2"), plan.toDownload.map { it.trackId })
    }

    @Test fun `duplicate entries collapse, FLAC wins`() {
        val plan = ManifestDiff.plan(listOf(mix("a"), fav("a", FLAC)), emptyList())
        assertEquals(1, plan.toDownload.size)
        assertEquals(FLAC, plan.toDownload.single().format)
        assertEquals("mix", plan.toDownload.single().why)
    }

    @Test fun `empty manifest deletes everything`() {
        val plan = ManifestDiff.plan(emptyList(), listOf(LocalTrack("x", OPUS, 1, true), LocalTrack("y", FLAC, 1, false)))
        assertEquals(listOf("x", "y"), plan.toDelete)
    }

    @Test fun `storage limit skips what does not fit but keeps trying smaller ones`() {
        val picked = ManifestDiff.fitToLimit(
            listOf(mix("a") to 40L, mix("b") to 50L, mix("c") to 10L),
            usedBytes = 40, limitBytes = 100,
        )
        assertEquals(listOf("a", "c"), picked.map { it.trackId })
    }

    @Test fun `size estimates`() {
        assertEquals(1234L, ManifestDiff.estimateBytes(FLAC, 200.0, 1234, 160))
        // 200 s at 160 kbps = 4,000,000 bytes, +3 %
        assertEquals(4_120_000L, ManifestDiff.estimateBytes(OPUS, 200.0, null, 160))
    }

    @Test fun `planning uses manifest sizes and subtracts partial downloads`() {
        val exact = ManifestItem("a", FLAC, "favourite", durationS = 200.0, bytes = 30_000_000, bytesExact = true)
        assertEquals(30_000_000L, ManifestDiff.plannedBytes(exact, 0, 160))
        assertEquals(29_000_000L, ManifestDiff.plannedBytes(exact, 1_000_000, 160))
        val noSize = ManifestItem("b", OPUS, "mix", durationS = 200.0)
        assertEquals(4_120_000L, ManifestDiff.plannedBytes(noSize, 0, 160)) // falls back to duration x bitrate
    }

    @Test fun `only exact sizes are used to verify a finished download`() {
        val exact = ManifestItem("a", FLAC, "favourite", bytes = 100, bytesExact = true)
        val estimate = ManifestItem("b", OPUS, "mix", bytes = 100, bytesExact = false)
        assertTrue(ManifestDiff.sizeMismatch(exact, 99))
        assertFalse(ManifestDiff.sizeMismatch(exact, 100))
        assertFalse(ManifestDiff.sizeMismatch(estimate, 57))
    }

    @Test fun `night window`() {
        assertTrue(SyncScheduler.inWindow(3, 0, 3, 2))
        assertTrue(SyncScheduler.inWindow(4, 59, 3, 2))
        assertFalse(SyncScheduler.inWindow(5, 0, 3, 2))
        assertFalse(SyncScheduler.inWindow(2, 59, 3, 2))
        assertTrue(SyncScheduler.inWindow(0, 30, 23, 2)) // window wraps midnight
    }

    @Test fun `delay until next window start`() {
        val now = LocalDateTime.of(2026, 11, 18, 22, 30)
        assertEquals(4 * 60 + 30L, SyncScheduler.delayUntil(now, 3).toMinutes())
        assertEquals(24 * 60L, SyncScheduler.delayUntil(LocalDateTime.of(2026, 11, 18, 3, 0), 3).toMinutes())
    }
}
