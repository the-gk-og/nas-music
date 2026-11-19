package org.nasmusic.app.playback

import androidx.media3.common.PlaybackException

/**
 * What to do when ExoPlayer reports an error. Network trouble (e.g. walking out of Wi-Fi: the LAN
 * connection drops, the router switches to the tunnel) is retried with backoff at the same position;
 * a track the server can't serve or the phone can't decode is skipped.
 */
object PlaybackRecovery {
    const val MAX_RETRIES = 6

    sealed interface Action {
        data class Retry(val delayMs: Long) : Action
        data object Skip : Action
        data object Stop : Action
    }

    fun decide(errorCode: Int, retries: Int, hasNext: Boolean): Action = when {
        errorCode == PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS ||
            errorCode == PlaybackException.ERROR_CODE_IO_FILE_NOT_FOUND ||
            errorCode in 3000..4999 -> if (hasNext) Action.Skip else Action.Stop
        errorCode in 2000..2999 || errorCode == PlaybackException.ERROR_CODE_TIMEOUT ->
            if (retries < MAX_RETRIES) Action.Retry((1000L shl retries).coerceAtMost(30_000L)) else Action.Stop
        else -> Action.Stop
    }
}
