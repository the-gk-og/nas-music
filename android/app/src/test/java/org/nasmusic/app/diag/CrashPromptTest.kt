package org.nasmusic.app.diag

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CrashPromptTest {
    private val now = 1_800_000_000_000L
    private val hour = 3_600_000L

    @Test fun `no crash files - no prompt`() {
        assertFalse(CrashPrompt.shouldPrompt(emptyList(), 0, now))
    }

    @Test fun `non-fatal error files and folders never prompt`() {
        assertFalse(CrashPrompt.shouldPrompt(listOf("error-${now - hour}.txt", "sent", "crash-x.txt", "crash-1.txt.tmp"), 0, now))
    }

    @Test fun `a new crash prompts once`() {
        val names = listOf("crash-${now - hour}.txt", "error-${now - 2 * hour}.txt")
        assertTrue(CrashPrompt.shouldPrompt(names, 0, now))
        // The user dismissed it ("Not now") or sent it: seenUpTo becomes the newest crash time.
        val seen = CrashPrompt.newestCrashTime(names)!!
        assertEquals(now - hour, seen)
        assertFalse(CrashPrompt.shouldPrompt(names, seen, now))
        assertFalse(CrashPrompt.shouldPrompt(names, seen, now + 24 * hour)) // every later launch too
    }

    @Test fun `a newer crash after dismissing prompts again`() {
        val seen = now - 5 * hour
        assertTrue(CrashPrompt.shouldPrompt(listOf("crash-$seen.txt", "crash-${now - hour}.txt"), seen, now))
    }

    @Test fun `old crashes don't interrupt`() {
        val old = now - CrashPrompt.MAX_AGE_MS - 1
        assertFalse(CrashPrompt.shouldPrompt(listOf("crash-$old.txt"), 0, now))
    }

    @Test fun `crash time parsing`() {
        assertEquals(123L, CrashPrompt.crashTime("crash-123.txt"))
        assertNull(CrashPrompt.crashTime("error-123.txt"))
        assertNull(CrashPrompt.newestCrashTime(listOf("error-1.txt")))
    }
}
