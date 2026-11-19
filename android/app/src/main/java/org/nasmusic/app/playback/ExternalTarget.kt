package org.nasmusic.app.playback

/**
 * Something other than the phone's own player that is currently playing the queue — the `full` flavour's
 * Google Cast session. While [active], [PlayerConnection] sends transport controls here instead of to the
 * (paused) local player, and shows this target's progress. The FOSS build never sets one.
 */
interface ExternalTarget {
    val active: Boolean
    /** The speaker's name, shown to other devices ("Casting from Pixel to <name>"). */
    val name: String?
    fun play()
    fun pause()
    fun next()
    fun previous()
    fun seekTo(ms: Long)
    /** Jump to this index of the phone's queue, if the target has it. */
    fun skipTo(index: Int)
    fun setVolume(v: Float)
    /** The phone's queue was replaced (e.g. another device said "play this"): send it to the target. */
    fun reload()
    /** What Now Playing should show, based on the local state (queue etc.); null = nothing to override. */
    fun overlay(local: NowPlaying): NowPlaying?
}
