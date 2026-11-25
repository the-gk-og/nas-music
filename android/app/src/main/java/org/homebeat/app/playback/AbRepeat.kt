package org.homebeat.app.playback

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** A-B repeat for the current track: once B is set, playback loops from A whenever it reaches B. */
class AbRepeat {
    data class Loop(val trackId: String, val aMs: Long, val bMs: Long?)

    private val _loop = MutableStateFlow<Loop?>(null)
    val loop: StateFlow<Loop?> = _loop.asStateFlow()

    fun setA(trackId: String, ms: Long) { _loop.value = Loop(trackId, ms, null) }

    fun setB(trackId: String, ms: Long) {
        val l = _loop.value ?: return
        if (l.trackId != trackId) return
        _loop.value = if (ms > l.aMs + 500) l.copy(bMs = ms) else l
    }

    fun clear() { _loop.value = null }

    companion object {
        /** Where to seek, or null to keep playing. */
        fun check(loop: Loop?, trackId: String?, positionMs: Long): Long? {
            val l = loop ?: return null
            val b = l.bMs ?: return null
            return if (l.trackId == trackId && positionMs >= b) l.aMs else null
        }
    }
}
