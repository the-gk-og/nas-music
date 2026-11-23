package org.nasmusic.app.playback

import java.lang.reflect.Proxy
import java.time.Instant
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.nasmusic.app.connect.ConnectMedia
import org.nasmusic.app.connect.PlaybackState
import org.nasmusic.app.data.api.NasMusicApi
import org.nasmusic.app.data.api.NewQueue
import org.nasmusic.app.data.api.Queue
import org.nasmusic.app.data.api.QueuePatch
import org.nasmusic.app.data.api.Track
import org.nasmusic.app.spoken.SpokenItem
import retrofit2.Response

/** "Resume exactly where you left off": what gets restored after the app is killed / the phone restarts. */
class ResumeTest {
    @get:Rule val tmp = TemporaryFolder()

    private val ids = listOf("a", "b", "c", "d")

    @Test fun restoresSavedTrackAndPosition() {
        assertEquals(2 to 61_000L, ResumeLogic.restorePoint(ids, ResumePosition(index = 2, positionMs = 61_000, itemId = "c")))
    }

    @Test fun neverRestartsFromTheTopWhenTheIndexMoved() {
        // The queue file was written after a reorder: find the saved song, keep its position.
        assertEquals(0 to 5_000L, ResumeLogic.restorePoint(listOf("c", "a", "b", "d"), ResumePosition(index = 2, positionMs = 5_000, itemId = "c")))
    }

    @Test fun unknownSongClampsTheIndexAndDropsThePosition() {
        assertEquals(3 to 0L, ResumeLogic.restorePoint(ids, ResumePosition(index = 9, positionMs = 5_000, itemId = "zzz")))
        assertEquals(1 to 7L, ResumeLogic.restorePoint(ids, ResumePosition(index = 1, positionMs = 7, itemId = null)))
    }

    @Test fun nothingToRestore() {
        assertNull(ResumeLogic.restorePoint(emptyList(), ResumePosition(index = 1)))
        assertEquals(0 to 0L, ResumeLogic.restorePoint(ids, null))
        assertEquals(1 to 0L, ResumeLogic.restorePoint(ids, ResumePosition(index = 1, positionMs = -5, itemId = "b")))
    }

    private fun state(atMs: Long, playing: Boolean = false, active: String? = "web", queue: String? = "q1", media: ConnectMedia? = null) =
        PlaybackState(activeDeviceId = active, queueId = queue, positionAt = Instant.ofEpochMilli(atMs).toString(), playing = playing, media = media)

    @Test fun picksUpWhatAnotherDevicePlayedMoreRecently() {
        val local = 1_000_000L
        assertTrue(ResumeLogic.preferServer(local, state(local + 60_000), "phone"))
        assertTrue(ResumeLogic.preferServer(null, state(local), "phone"))
        assertTrue(ResumeLogic.preferServer(local, state(local + 60_000, queue = null, media = ConnectMedia("episode", "e1")), "phone"))
        // Older than this phone's own snapshot, or within the slack: keep the local one.
        assertFalse(ResumeLogic.preferServer(local, state(local - 60_000), "phone"))
        assertFalse(ResumeLogic.preferServer(local, state(local + 2_000), "phone"))
        // Still playing over there: this phone is a remote, nothing to adopt.
        assertFalse(ResumeLogic.preferServer(local, state(local + 60_000, playing = true), "phone"))
        // It was this phone, or there was nothing loaded.
        assertFalse(ResumeLogic.preferServer(local, state(local + 60_000, active = "phone"), "phone"))
        assertFalse(ResumeLogic.preferServer(local, state(local + 60_000, queue = null), "phone"))
        assertFalse(ResumeLogic.preferServer(local, null, "phone"))
    }

    @Test fun theTakeoverAnsweringOurOwnPlayDoesNotReloadTheQueue() {
        assertTrue(ResumeLogic.ownClaim(claimedAtMs = 10_000, nowMs = 11_000, playing = true))
        assertFalse(ResumeLogic.ownClaim(10_000, 20_000, true)) // a later transfer from another device
        assertFalse(ResumeLogic.ownClaim(10_000, 11_000, false))
        assertFalse(ResumeLogic.ownClaim(0, 1_000, true))
    }

    @Test fun storeRoundTripsAQueueAndASpokenItem() {
        val store = ResumeStore(tmp.newFolder("resume"))
        assertNull(store.load())
        val tracks = listOf(Track("t1", title = "One", albumId = "al"), Track("t2", title = "Two", albumId = "al"))
        store.saveQueue(ResumeQueue(tracks.map { ResumeEntry(it, PlaySource.MIX.wire, "m1") }, queueId = "q1"))
        store.savePosition(ResumePosition(index = 1, positionMs = 42_000, itemId = "t2", shuffle = true, repeat = 2, savedAt = 5, queueId = "q1"))
        val (q, p) = store.load()!!
        assertEquals(listOf("t1", "t2"), q.entries.map { it.track.id })
        assertEquals("m1", q.entries[0].mixId)
        assertEquals(ResumePosition(1, 42_000, "t2", true, 2, 5, "q1"), p)
        assertEquals(5L, store.savedAt())

        val book = SpokenItem("book", "b1", "Book", "Author", "", 3600.0, partStarts = listOf(0.0, 1800.0), partDurations = listOf(1800.0, 1800.0), speed = 1.25f)
        store.saveQueue(ResumeQueue(spoken = book))
        assertEquals(book, store.load()!!.first.spoken)

        store.clear()
        assertNull(store.load())
    }

    @Test fun corruptFilesAreIgnored() {
        val dir = tmp.newFolder("bad")
        java.io.File(dir, "resume_queue.json").writeText("{not json")
        assertNull(ResumeStore(dir).load())
    }
}

/** The race that made a queue "resume" at the wrong song: a new list's position written onto the old queue. */
@OptIn(ExperimentalCoroutinesApi::class)
class QueueSyncResumeTest {
    private class FakeApi {
        val patches = mutableListOf<Pair<String, QueuePatch>>()
        var created = 0
        val api: NasMusicApi = Proxy.newProxyInstance(NasMusicApi::class.java.classLoader, arrayOf(NasMusicApi::class.java)) { _, m, args ->
            when (m.name) {
                "createQueue" -> { created++; Queue("new${created}", (args[0] as NewQueue).name) }
                "patchQueue" -> { patches += (args[0] as String) to (args[1] as QueuePatch); Response.success(Unit) }
                "queues" -> emptyList<Queue>()
                else -> throw UnsupportedOperationException(m.name)
            }
        } as NasMusicApi
    }

    @Test fun positionOfANewListNeverGoesToThePreviousQueue() = runTest {
        val fake = FakeApi()
        val scope = TestScope(StandardTestDispatcher(testScheduler))
        val sync = QueueSync({ fake.api }, scope)
        sync.setCurrent("old")
        sync.created("Album: New", listOf(Track("t1"), Track("t2")), 1)
        assertNull("no current queue until the server answers", sync.currentId.value)
        sync.progress(1, 30_000) // the service's tick fires before createQueue returns
        scope.advanceUntilIdle()
        assertEquals("new1", sync.currentId.value)
        assertTrue(fake.patches.none { it.first == "old" })
        assertEquals(listOf("new1" to QueuePatch(currentIndex = 1, positionMs = 30_000)), fake.patches)
    }

    @Test fun anOlderCreateDoesNotWinOverANewerChoice() = runTest {
        val fake = FakeApi()
        val scope = TestScope(StandardTestDispatcher(testScheduler))
        val sync = QueueSync({ fake.api }, scope)
        sync.created("Album: A", listOf(Track("t1")), 0)
        sync.setCurrent("radio-q") // e.g. a radio started before the create answered
        scope.advanceUntilIdle()
        assertEquals("radio-q", sync.currentId.value)
    }
}
