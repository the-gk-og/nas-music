package org.nasmusic.app.feedback

/** Event types from the API's Event schema. */
enum class EventType(val wire: String) {
    PLAY("play"), SKIP("skip"), STAR("star"), UNSTAR("unstar"), RATE("rate"),
    THUMB_UP("thumb_up"), THUMB_DOWN("thumb_down"), THUMB_CLEAR("thumb_clear"),
}

/** Why playback left a track. */
enum class LeaveReason {
    /** The track played to its end and the player moved on by itself. */
    ENDED,
    /** The user pressed next / picked another track / seeked to another item. */
    USER_SKIP,
    /** Queue replaced, player stopped, app killed… not a judgement on the track. */
    OTHER,
}

/**
 * Decides play vs skip. Rule (from the design doc): a track counts as played when it finishes or
 * passes 80 % of its length; leaving it earlier because the user skipped records a skip with the
 * position. Each track instance produces at most one play/skip.
 */
object PlaybackClassifier {
    const val PLAY_FRACTION = 0.8

    fun passedPlayThreshold(positionS: Double, durationS: Double): Boolean =
        durationS > 0 && positionS >= durationS * PLAY_FRACTION

    /** Called while playing; returns PLAY the first time the 80 % mark is passed. */
    fun onProgress(positionS: Double, durationS: Double, alreadyCounted: Boolean): EventType? =
        if (!alreadyCounted && passedPlayThreshold(positionS, durationS)) EventType.PLAY else null

    /** Called when playback leaves a track. */
    fun onLeave(positionS: Double, durationS: Double, alreadyCounted: Boolean, reason: LeaveReason): EventType? {
        if (alreadyCounted) return null
        return when (reason) {
            LeaveReason.ENDED -> EventType.PLAY
            LeaveReason.USER_SKIP -> if (passedPlayThreshold(positionS, durationS)) EventType.PLAY else EventType.SKIP
            LeaveReason.OTHER -> if (passedPlayThreshold(positionS, durationS)) EventType.PLAY else null
        }
    }
}
