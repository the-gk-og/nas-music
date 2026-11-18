package org.nasmusic.app.feedback

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PlaybackClassifierTest {
    private val d = 200.0 // 80 % = 160 s

    @Test fun `natural end is a play`() {
        assertEquals(EventType.PLAY, PlaybackClassifier.onLeave(200.0, d, false, LeaveReason.ENDED))
    }

    @Test fun `skip before 80 percent is a skip`() {
        assertEquals(EventType.SKIP, PlaybackClassifier.onLeave(30.0, d, false, LeaveReason.USER_SKIP))
        assertEquals(EventType.SKIP, PlaybackClassifier.onLeave(159.9, d, false, LeaveReason.USER_SKIP))
    }

    @Test fun `skip after 80 percent counts as a play`() {
        assertEquals(EventType.PLAY, PlaybackClassifier.onLeave(160.0, d, false, LeaveReason.USER_SKIP))
    }

    @Test fun `nothing more once the play was already counted`() {
        assertNull(PlaybackClassifier.onLeave(190.0, d, true, LeaveReason.USER_SKIP))
        assertNull(PlaybackClassifier.onLeave(200.0, d, true, LeaveReason.ENDED))
    }

    @Test fun `queue replaced early is neither play nor skip`() {
        assertNull(PlaybackClassifier.onLeave(20.0, d, false, LeaveReason.OTHER))
        assertEquals(EventType.PLAY, PlaybackClassifier.onLeave(170.0, d, false, LeaveReason.OTHER))
    }

    @Test fun `progress fires play exactly once at the 80 percent mark`() {
        assertNull(PlaybackClassifier.onProgress(159.0, d, false))
        assertEquals(EventType.PLAY, PlaybackClassifier.onProgress(161.0, d, false))
        assertNull(PlaybackClassifier.onProgress(180.0, d, true))
    }

    @Test fun `unknown duration never passes the threshold`() {
        assertNull(PlaybackClassifier.onProgress(500.0, 0.0, false))
        assertEquals(EventType.SKIP, PlaybackClassifier.onLeave(500.0, 0.0, false, LeaveReason.USER_SKIP))
    }
}
