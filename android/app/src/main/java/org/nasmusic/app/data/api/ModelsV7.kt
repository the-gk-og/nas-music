package org.nasmusic.app.data.api

import kotlinx.serialization.Serializable

// v7 (api/openapi.yaml 0.7.0): scrobbling and "new music added" notifications.

@Serializable
data class ScrobbleService(
    val service: String,
    val available: Boolean = true,
    val connected: Boolean = false,
    val enabled: Boolean = false,
    val username: String? = null,
    val nowPlaying: Boolean = true,
    val needsReauth: Boolean = false,
    val authPending: Boolean = false,
    val connectedAt: String? = null,
    val lastScrobbleAt: String? = null,
    val lastError: String? = null,
    val lastErrorAt: String? = null,
    val pending: Int = 0,
    val failed: Int = 0,
    val sent: Int = 0,
    val ignored: Int = 0,
)

@Serializable
data class ScrobbleImport(
    val state: String = "done",
    val startedAt: String? = null,
    val finishedAt: String? = null,
    val scanned: Int? = null,
    val matched: Int? = null,
    val imported: Int? = null,
    val alreadyKnown: Int? = null,
    val unmatched: Int? = null,
    val error: String? = null,
)

@Serializable
data class ScrobbleStatus(
    val services: List<ScrobbleService> = emptyList(),
    val import: ScrobbleImport? = null,
    val importedEvents: Int = 0,
    val removed: Int? = null,
)

@Serializable
data class ScrobbleSettingsBody(val enabled: Boolean? = null, val nowPlaying: Boolean? = null)

@Serializable
data class ListenBrainzConnect(val token: String)

@Serializable
data class LastfmAuth(val authUrl: String)

@Serializable
data class NotificationData(
    val tracks: Int? = null,
    val albums: Int? = null,
    val artists: List<String> = emptyList(),
    val albumIds: List<String> = emptyList(),
    val trackIds: List<String> = emptyList(),
)

@Serializable
data class AppNotification(
    val id: String,
    val kind: String = "",
    val title: String = "",
    val body: String = "",
    val data: NotificationData = NotificationData(),
    val createdAt: String = "",
    val read: Boolean = false,
)

@Serializable
data class NotificationList(val items: List<AppNotification> = emptyList(), val unread: Int = 0)

@Serializable
data class ReadNotifications(val ids: List<String>? = null)

@Serializable
data class ReadResult(val marked: Int = 0, val unread: Int = 0)

@Serializable
data class NewMusic(
    val since: String = "",
    val days: Int = 7,
    val albums: List<Album> = emptyList(),
    val tracks: List<Track> = emptyList(),
)
