package org.nasmusic.app.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.nasmusic.app.data.api.SmartCandidateDto
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.data.db.DownloadEntity
import org.nasmusic.app.data.db.OfflinePinEntity

class SmartPlannerTest {
    private val q = DownloadQuality.OPUS_128
    private fun cand(id: String, vararg reasons: String, score: Double = 0.0, bytes: Long = 10, days: Double? = null) =
        SmartCandidate(id, reasons.toSet(), score, bytes, days)
    private fun held(id: String, bytes: Long = 10, quality: String = "opus128", complete: Boolean = true, protected: Boolean = false) =
        HeldTrack(id, bytes, quality, complete, protected)

    @Test fun `favourites and mixes outrank predictions`() {
        val fav = SmartPlanner.priority(cand("a", "favourite"))
        val mix = SmartPlanner.priority(cand("b", "mix"))
        val pred = SmartPlanner.priority(cand("c", "predicted", score = 1.0))
        assertTrue(fav > mix && mix > pred)
        // The AI score orders songs with the same reason.
        assertTrue(SmartPlanner.priority(cand("d", "predicted", score = 0.9)) > SmartPlanner.priority(cand("e", "predicted", score = 0.2)))
        // Recently played fades over time but keeps at least half its weight.
        val fresh = SmartPlanner.priority(cand("f", "recent", days = 0.0))
        val old = SmartPlanner.priority(cand("g", "recent", days = 60.0))
        assertTrue(fresh > old && old >= 0.35)
    }

    @Test fun `fills best first within the budget and skips what does not fit`() {
        val plan = SmartPlanner.plan(
            listOf(cand("pred", "predicted", score = 0.5, bytes = 30), cand("fav", "favourite", bytes = 40),
                cand("mix", "mix", bytes = 50), cand("small", "predicted", score = 0.1, bytes = 5)),
            held = emptyList(), quality = q, budgetBytes = 100,
        )
        assertEquals(listOf("fav", "mix", "small"), plan.download.map { it.trackId })
        assertEquals(95, plan.plannedBytes)
        assertEquals(1, plan.overBudget)
        assertTrue(plan.evict.isEmpty())
    }

    @Test fun `evicts least likely first and keeps what is already held`() {
        val plan = SmartPlanner.plan(
            listOf(cand("fav", "favourite"), cand("mix", "mix"), cand("lowpred", "predicted", score = 0.1)),
            held = listOf(held("fav"), held("gone"), held("lowpred"), held("mix")),
            quality = q, budgetBytes = 20,
        )
        assertEquals(listOf("fav", "mix"), plan.keep.map { it.trackId })
        assertTrue(plan.download.isEmpty())
        // Not suggested any more (-1) goes before a low prediction that didn't fit.
        assertEquals(listOf("gone", "lowpred"), plan.evict)
    }

    @Test fun `manual and kept-collection tracks are never evicted and do not use the budget`() {
        val plan = SmartPlanner.plan(
            listOf(cand("fav", "favourite", bytes = 10), cand("man", "favourite", bytes = 10)),
            held = listOf(held("man", bytes = 1_000, protected = true), held("pinned", bytes = 1_000, protected = true),
                held("pendingManual", complete = false, protected = true)),
            quality = q, budgetBytes = 10,
        )
        assertEquals(listOf("fav"), plan.download.map { it.trackId })
        assertTrue(plan.evict.isEmpty())
        assertFalse((plan.keep + plan.download).any { it.trackId == "man" })
    }

    @Test fun `a quality change re-downloads wanted tracks instead of evicting them`() {
        val plan = SmartPlanner.plan(listOf(cand("fav", "favourite", bytes = 20)), listOf(held("fav", quality = "opus192")), q, 100)
        assertEquals(listOf("fav"), plan.download.map { it.trackId })
        assertTrue(plan.evict.isEmpty())
    }

    @Test fun `held tracks get a small bonus so the set does not churn`() {
        // Same reason and nearly the same score; only room for one: the held one stays.
        val plan = SmartPlanner.plan(
            listOf(cand("new", "predicted", score = 0.52), cand("old", "predicted", score = 0.5)),
            listOf(held("old")), q, budgetBytes = 10,
        )
        assertEquals(listOf("old"), plan.keep.map { it.trackId })
        assertTrue(plan.download.isEmpty())
        // A clearly better new song still wins.
        val plan2 = SmartPlanner.plan(
            listOf(cand("new", "favourite"), cand("old", "predicted", score = 0.5)), listOf(held("old")), q, budgetBytes = 10,
        )
        assertEquals(listOf("new"), plan2.download.map { it.trackId })
        assertEquals(listOf("old"), plan2.evict)
    }

    @Test fun `duplicate candidates merge their reasons`() {
        val plan = SmartPlanner.plan(listOf(cand("a", "predicted", score = 0.3), cand("a", "favourite")), emptyList(), q, 100)
        assertEquals(setOf("predicted", "favourite"), plan.download.single().reasons)
    }

    @Test fun `budget parsing and resolving`() {
        assertEquals(StorageBudget.Fixed(10), StorageBudget.parse("gb:10"))
        assertEquals(StorageBudget.PercentOfFree(25), StorageBudget.parse("pct:25"))
        assertEquals(StorageBudget.DEFAULT, StorageBudget.parse("junk"))
        assertEquals(StorageBudget.DEFAULT, StorageBudget.parse(null))
        assertEquals(StorageBudget.PercentOfFree(90), StorageBudget.parse("pct:150"))
        assertEquals(2L * StorageBudget.GB, StorageBudget.Fixed(2).bytes(0, 0))
        // What smart downloads already hold counts as free, so the budget is stable as it fills.
        assertEquals(StorageBudget.PercentOfFree(50).bytes(60, 40), StorageBudget.PercentOfFree(50).bytes(100, 0))
        assertEquals(50L, StorageBudget.PercentOfFree(50).bytes(100, 0))
        StorageBudget.CHOICES.forEach { assertEquals(it, StorageBudget.parse(it.key)) }
    }

    @Test fun `quality keys and size estimates`() {
        assertEquals(DownloadQuality.OPUS_128, DownloadQuality.from(null))
        assertEquals(DownloadQuality.FLAC, DownloadQuality.from("flac"))
        assertEquals("opus192", DownloadQuality.of("opus", 192))
        assertEquals(30_000_000L, SmartPlanner.estimateBytes(DownloadQuality.FLAC, 200.0, 30_000_000))
        val o128 = SmartPlanner.estimateBytes(DownloadQuality.OPUS_128, 200.0, 30_000_000)
        val o192 = SmartPlanner.estimateBytes(DownloadQuality.OPUS_192, 200.0, 30_000_000)
        assertTrue(o128 in 3_200_000..3_400_000 && o192 > o128)
        val legacy = DownloadEntity("t", "opus", "/x", 1, "mix", complete = true)
        assertEquals("opus160", legacy.qualityKey)
        assertEquals("flac", legacy.copy(format = "flac").qualityKey)
        assertFalse(legacy.isProtected)
        assertTrue(legacy.copy(pins = "album:1").isProtected && legacy.copy(manual = true).isProtected)
    }

    @Test fun `server candidates map to planner candidates`() {
        val now = java.time.Instant.parse("2026-11-20T00:00:00Z").toEpochMilli()
        val out = OfflineSets.candidates(listOf(
            SmartCandidateDto("a", listOf("recent"), 0.5, lastPlayedAt = "2026-11-18T00:00:00Z", durationS = 100.0),
            SmartCandidateDto("b", listOf("favourite"), track = Track("b", durationS = 200.0, flacBytes = 9_000_000)),
            SmartCandidateDto("c", listOf("predicted"), lastPlayedAt = "not a date"),
        ), DownloadQuality.FLAC, now)
        assertEquals(2.0, out[0].lastPlayedDaysAgo!!, 1e-9)
        assertEquals(9_000_000L, out[1].bytes)
        assertEquals(null, out[2].lastPlayedDaysAgo)
        assertTrue(out[2].bytes > 0) // unknown duration still gets an estimate
        assertEquals("favourite", OfflineSets.primaryReason(setOf("predicted", "favourite")))
    }

    @Test fun `pin membership maps tracks to every collection holding them`() {
        val m = OfflineSets.pinMembership(listOf(
            OfflinePinEntity("album:1", "album", "1", "A", "t1,t2"),
            OfflinePinEntity("playlist:p", "playlist", "p", "P", "t2,t3,t2"),
        ))
        assertEquals(listOf("album:1"), m["t1"])
        assertEquals(listOf("album:1", "playlist:p"), m["t2"])
        assertEquals(setOf("t1", "t2", "t3"), m.keys)
    }
}
