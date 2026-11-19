package org.nasmusic.wear

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.nasmusic.app.data.api.Track
import org.nasmusic.wear.auth.WearAuth
import org.nasmusic.wear.offline.OfflinePlan

class OfflinePlanTest {
    private fun t(id: String, s: Double = 200.0) = Track(id = id, durationS = s)

    @Test fun estimateIsDurationTimesBitrate() {
        // 200 s × 96 kbps = 2.4 MB, +5 %
        assertEquals(2_520_000L, OfflinePlan.estimateBytes(t("a"), 96))
    }

    @Test fun takesPrefixUntilFullThenSkipsTheRest() {
        val each = OfflinePlan.estimateBytes(t("a"), 96)
        val p = OfflinePlan.plan(listOf(t("a"), t("b"), t("c"), t("d", 10.0)), emptyMap(), each * 2 + 1, 96)
        assertEquals(listOf("a", "b"), p.download.map { it.id })
        // "d" would fit on its own but comes after the first track that didn't: skipped too.
        assertEquals(2, p.skipped)
    }

    @Test fun existingFilesCountAtRealSizeAndAreKept() {
        val p = OfflinePlan.plan(listOf(t("a"), t("b")), mapOf("a" to 100L), 100L + OfflinePlan.estimateBytes(t("b"), 96), 96)
        assertEquals(setOf("a"), p.keep)
        assertEquals(listOf("b"), p.download.map { it.id })
        assertEquals(0, p.skipped)
    }

    @Test fun duplicatesAcrossListsCountOnce() {
        val p = OfflinePlan.plan(listOf(t("a"), t("a")), emptyMap(), Long.MAX_VALUE, 96)
        assertEquals(1, p.download.size)
    }

    @Test fun budgetRespectsReserveAndCap() {
        assertEquals(1000L, OfflinePlan.budget(1000, 0, 10_000, 500))
        assertEquals(700L, OfflinePlan.budget(10_000, 200, 1000, 500))
        assertEquals(0L, OfflinePlan.budget(10_000, 0, 100, 500))
    }

    @Test fun pairCodeIsNormalised() {
        assertEquals("ABCD2345", WearAuth.normalizeCode(" abcd-2345 "))
    }

    @Test fun shortFingerprintKeepsEnds() {
        val fp = (1..32).joinToString(":") { "%02X".format(it) }
        val s = WearAuth.shortFingerprint(fp)
        assertTrue(s.startsWith("01:02:03:04") && s.endsWith("1D:1E:1F:20"))
    }
}
