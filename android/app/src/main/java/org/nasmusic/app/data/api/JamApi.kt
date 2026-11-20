package org.nasmusic.app.data.api

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

// Jam (listen together), host side — api/openapi.yaml 0.6.0 tag "jam". Guests use the server's web page.

@Serializable
data class JamSettings(val approval: Boolean = false, val allowPlayNext: Boolean = false, val maxPerGuest: Int? = 5)

/**
 * Settings body for start / PATCH. Built as a JsonObject because ApiFactory.json drops null properties
 * (explicitNulls = false), and `maxPerGuest: null` (= no cap) must reach the server.
 */
data class JamSettingsBody(val approval: Boolean, val allowPlayNext: Boolean, val maxPerGuest: Int?) {
    fun json(): JsonObject = buildJsonObject {
        put("approval", approval)
        put("allowPlayNext", allowPlayNext)
        put("maxPerGuest", maxPerGuest?.let(::JsonPrimitive) ?: JsonNull)
    }
}

@Serializable
data class JamTrack(val id: String, val title: String = "", val artist: String = "", val album: String = "",
                    val albumId: String = "", val durationS: Double = 0.0)

@Serializable
data class JamAdder(val guestId: String? = null, val name: String = "", val isHost: Boolean = false)

@Serializable
data class JamVotes(val up: Int = 0, val down: Int = 0, val mine: Int = 0)

@Serializable
data class JamEntry(val position: Int, val track: JamTrack, val addedBy: JamAdder = JamAdder(), val votes: JamVotes = JamVotes())

@Serializable
data class JamNowPlaying(val position: Int = 0, val track: JamTrack, val addedBy: JamAdder = JamAdder(),
                         val positionMs: Long = 0, val playing: Boolean = false)

@Serializable
data class JamRequestBy(val guestId: String? = null, val name: String = "")

@Serializable
data class JamRequest(val id: String, val trackId: String, val track: JamTrack? = null, val playNext: Boolean = false,
                      val status: String = "pending", val by: JamRequestBy? = null)

@Serializable
data class JamGuest(val id: String, val name: String, val isMember: Boolean = false)

@Serializable
data class JamInfo(
    val id: String,
    val hostName: String = "",
    val settings: JamSettings = JamSettings(),
    val expiresAt: String? = null,
    val joinCode: String? = null,
    val joinUrl: String? = null,
    val lanJoinUrl: String? = null,
    val joinExpiresAt: String? = null,
)

@Serializable
data class JamSnapshot(
    val jam: JamInfo,
    val nowPlaying: JamNowPlaying? = null,
    val upcoming: List<JamEntry> = emptyList(),
    val pending: List<JamRequest> = emptyList(),
    val guests: List<JamGuest> = emptyList(),
)

@Serializable
data class JamLinkBody(val close: Boolean = false, val ttlMinutes: Int? = null)

@Serializable
data class JamDecision(val approve: Boolean)

interface JamApi {
    @POST("jams")
    suspend fun start(@Body body: JsonObject): JamSnapshot

    /** 204 (no body) when you're not hosting one. */
    @GET("jams/current")
    suspend fun current(): Response<JamSnapshot>

    @GET("jams/{id}")
    suspend fun get(@Path("id") id: String): JamSnapshot

    @PATCH("jams/{id}")
    suspend fun settings(@Path("id") id: String, @Body body: JsonObject): JamSnapshot

    @DELETE("jams/{id}")
    suspend fun end(@Path("id") id: String): Response<Unit>

    @POST("jams/{id}/link")
    suspend fun link(@Path("id") id: String, @Body body: JamLinkBody): JamSnapshot

    @POST("jams/{id}/requests/{rid}")
    suspend fun decide(@Path("id") id: String, @Path("rid") rid: String, @Body body: JamDecision): JamSnapshot

    @DELETE("jams/{id}/queue/{position}")
    suspend fun remove(@Path("id") id: String, @Path("position") position: Int, @Query("trackId") trackId: String?): JamSnapshot

    @DELETE("jams/{id}/guests/{gid}")
    suspend fun kick(@Path("id") id: String, @Path("gid") gid: String): JamSnapshot
}
