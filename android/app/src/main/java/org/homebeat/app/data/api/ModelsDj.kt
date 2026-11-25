package org.homebeat.app.data.api

import kotlinx.serialization.Serializable

// ---------- api 0.8.2: radio DJ voice ----------

/** GET /dj/status. `state`: ready | downloading | unavailable | disabled | error. */
@Serializable
data class DjStatus(
    val state: String,
    val voice: String = "",
    val detail: String? = null,
    val enabled: Boolean = false,
    val every: Int = 3,
) {
    /** The server can (or soon can) talk: show the switch. */
    val available: Boolean get() = state != "unavailable" && state != "disabled"
}

/** POST /dj/intro body. */
@Serializable
data class DjIntroRequest(
    val trackId: String,
    val position: Int,
    val previousTrackId: String? = null,
    val queueId: String? = null,
    val force: Boolean = false,
)

/** POST /dj/intro answer (204 = the DJ is quiet before this song). `url` is relative to the API base. */
@Serializable
data class DjIntro(
    val clipId: String,
    val text: String,
    val url: String,
    val durationMs: Long,
    val voice: String = "",
)
