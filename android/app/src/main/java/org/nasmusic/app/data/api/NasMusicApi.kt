package org.nasmusic.app.data.api

import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.PATCH
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query

/** Retrofit view of api/openapi.yaml. Paths are relative to `<base>/api/v1/`. */
interface NasMusicApi {
    @GET("health")
    suspend fun health(): Health

    @POST("pair")
    suspend fun pair(@Body body: PairRequest): PairResponse

    @GET("library/artists")
    suspend fun artists(@Query("offset") offset: Int = 0, @Query("limit") limit: Int = 1000): List<Artist>

    @GET("library/artists/{id}")
    suspend fun artist(@Path("id") id: String): ArtistDetail

    // ---- api 0.7: artist pages + smart playlists ----
    @GET("library/artists/{id}/page")
    suspend fun artistPage(@Path("id") id: String): ArtistPage

    @GET("library/artists/{id}/info")
    suspend fun artistInfo(@Path("id") id: String): ArtistInfo

    @GET("library/artists/{id}/tracks")
    suspend fun artistTracks(@Path("id") id: String): List<Track>

    @GET("smart-playlists")
    suspend fun smartPlaylists(): List<SmartPlaylist>

    @GET("smart-playlists/fields")
    suspend fun smartFields(): SmartFields

    @GET("smart-playlists/{id}")
    suspend fun smartPlaylist(@Path("id") id: String): SmartPlaylist

    @POST("smart-playlists")
    suspend fun createSmartPlaylist(@Body body: NewSmartPlaylist): SmartPlaylist

    @PATCH("smart-playlists/{id}")
    suspend fun updateSmartPlaylist(@Path("id") id: String, @Body body: SmartPatch): SmartPlaylist

    @DELETE("smart-playlists/{id}")
    suspend fun deleteSmartPlaylist(@Path("id") id: String): Response<Unit>

    @POST("smart-playlists/preview")
    suspend fun previewSmartPlaylist(@Body body: SmartPreviewRequest): SmartPreview

    @GET("library/albums")
    suspend fun albums(
        @Query("artistId") artistId: String? = null,
        @Query("sort") sort: String = "name",
        @Query("offset") offset: Int = 0,
        @Query("limit") limit: Int = 200,
    ): List<Album>

    @GET("library/albums/{id}")
    suspend fun album(@Path("id") id: String): AlbumDetail

    @GET("library/tracks/{id}")
    suspend fun track(@Path("id") id: String): Track

    @GET("library/search")
    suspend fun search(@Query("q") q: String, @Query("limit") limit: Int = 50): SearchResult

    @GET("favourites")
    suspend fun favourites(): List<Track>

    @PUT("favourites/{id}")
    suspend fun star(@Path("id") id: String): Response<Unit>

    @DELETE("favourites/{id}")
    suspend fun unstar(@Path("id") id: String): Response<Unit>

    @GET("mixes/today")
    suspend fun mixesToday(): List<Mix>

    @GET("mixes/{id}")
    suspend fun mix(@Path("id") id: String): Mix

    @POST("mixes/more-like/{id}")
    suspend fun moreLike(@Path("id") trackId: String, @Query("minutes") minutes: Int = 40): Mix

    @GET("tracks/{id}/why")
    suspend fun why(@Path("id") trackId: String): WhyResponse

    /** 404 = unknown track or no lyrics; returned as a Response so callers can tell it from a network error. */
    @GET("tracks/{id}/lyrics")
    suspend fun lyrics(@Path("id") trackId: String, @Query("source") source: String? = null): Response<Lyrics>

    @GET("sync/manifest")
    suspend fun manifest(@Query("favouritesFormat") favouritesFormat: String): SyncManifest

    @GET("sync/smart")
    suspend fun smartCandidates(@Query("limit") limit: Int = 1500): SmartCandidates

    @POST("events")
    suspend fun events(@Body batch: EventBatch): EventsResponse

    @GET("suggestions")
    suspend fun suggestions(): List<Suggestion>

    @POST("suggestions/{id}")
    suspend fun setSuggestionStatus(@Path("id") id: String, @Body body: SuggestionStatus): Response<Unit>
    // ---- v2 (docs/v2.md §4) ----
    @GET("server-info")
    suspend fun serverInfo(): ServerInfo

    @POST("auth/login")
    suspend fun login(@Body body: LoginRequest): Session

    @POST("auth/register")
    suspend fun register(@Body body: RegisterRequest): Session

    @POST("auth/logout")
    suspend fun logout(): Response<Unit>

    @GET("me")
    suspend fun me(): Me

    @PUT("me/password")
    suspend fun changePassword(@Body body: PasswordChange): Response<Unit>

    @GET("me/devices")
    suspend fun myDevices(): List<MyDevice>

    @DELETE("me/devices/{id}")
    suspend fun deleteDevice(@Path("id") id: String): Response<Unit>

    @POST("me/pair-code")
    suspend fun pairCode(): PairCode

    @GET("me/settings")
    suspend fun mySettings(): UserSettings

    @GET("me/family")
    suspend fun family(): FamilyMode

    @PUT("me/family")
    suspend fun putFamily(@Body body: FamilyUpdate): FamilyMode

    // v7 voice control (Home Assistant): voice tokens only work on POST /voice/command.
    @GET("me/voice/tokens")
    suspend fun voiceTokens(): VoiceTokens

    @POST("me/voice/tokens")
    suspend fun createVoiceToken(@Body body: NewVoiceToken): VoiceToken

    @DELETE("me/voice/tokens/{id}")
    suspend fun revokeVoiceToken(@Path("id") id: String): Response<Unit>

    @GET("me/voice/devices")
    suspend fun voiceDevices(): VoiceDevices

    @PUT("me/voice/devices/{id}")
    suspend fun putVoiceDevice(@Path("id") id: String, @Body body: VoiceDevicePrefs): VoiceDevicePrefs

    @PUT("me/settings")
    suspend fun putMySettings(@Body body: UserSettings): Response<Unit>

    // ---- v7 scrobbling ----
    @GET("me/scrobble")
    suspend fun scrobble(): ScrobbleStatus

    @PUT("me/scrobble/{service}")
    suspend fun putScrobble(@Path("service") service: String, @Body body: ScrobbleSettingsBody): ScrobbleStatus

    @DELETE("me/scrobble/{service}")
    suspend fun disconnectScrobble(@Path("service") service: String): ScrobbleStatus

    @POST("me/scrobble/listenbrainz/connect")
    suspend fun connectListenBrainz(@Body body: ListenBrainzConnect): ScrobbleStatus

    @POST("me/scrobble/lastfm/connect")
    suspend fun lastfmConnect(): LastfmAuth

    /** 409 until approved on last.fm, 410 when the request expired. */
    @POST("me/scrobble/lastfm/complete")
    suspend fun lastfmComplete(): ScrobbleStatus

    @POST("me/scrobble/retry")
    suspend fun retryScrobbles(): ScrobbleStatus

    @POST("me/scrobble/listenbrainz/import")
    suspend fun importListenBrainz(): ScrobbleStatus

    @DELETE("me/scrobble/listenbrainz/import")
    suspend fun removeImported(): ScrobbleStatus

    // ---- v7 notifications ----
    @GET("me/notifications")
    suspend fun notifications(@Query("since") since: String? = null, @Query("limit") limit: Int = 50): NotificationList

    @POST("me/notifications/read")
    suspend fun readNotifications(@Body body: ReadNotifications): ReadResult

    @GET("library/new")
    suspend fun newMusic(@Query("days") days: Int = 7): NewMusic

    @GET("me/data")
    suspend fun myData(): MyData

    /** signal=all also erases listening history and needs confirm=true. */
    @DELETE("me/data")
    suspend fun deleteMyData(@Query("signal") signal: String, @Query("confirm") confirm: Boolean? = null): Response<Unit>

    @GET("library/folders")
    suspend fun folders(@Query("path") path: String = ""): FolderListing

    @GET("library/genres")
    suspend fun genres(): List<Genre>

    @GET("library/genres/{genre}/tracks")
    suspend fun genreTracks(@Path("genre") genre: String, @Query("limit") limit: Int = 1000): List<Track>

    @GET("library/tracks")
    suspend fun allTracks(
        @Query("sort") sort: String = "title",
        @Query("offset") offset: Int = 0,
        @Query("limit") limit: Int = 1000,
    ): List<Track>

    @GET("library/tracks/{id}/info")
    suspend fun trackInfo(@Path("id") id: String): TrackInfo

    @GET("lists/{kind}")
    suspend fun smartList(@Path("kind") kind: String, @Query("offset") offset: Int = 0, @Query("limit") limit: Int = 500): List<Track>

    @GET("playlists")
    suspend fun playlists(): List<Playlist>

    @POST("playlists")
    suspend fun createPlaylist(@Body body: NameBody): Playlist

    @GET("playlists/{id}")
    suspend fun playlist(@Path("id") id: String): Playlist

    @PATCH("playlists/{id}")
    suspend fun renamePlaylist(@Path("id") id: String, @Body body: NameBody): Response<Unit>

    @DELETE("playlists/{id}")
    suspend fun deletePlaylist(@Path("id") id: String): Response<Unit>

    @POST("playlists/{id}/tracks")
    suspend fun addToPlaylist(@Path("id") id: String, @Body body: TrackIdsBody): Response<Unit>

    @DELETE("playlists/{id}/tracks/{position}")
    suspend fun removeFromPlaylist(@Path("id") id: String, @Path("position") position: Int): Response<Unit>

    @POST("playlists/{id}/move")
    suspend fun movePlaylistTrack(@Path("id") id: String, @Body body: MoveBody): Response<Unit>

    /** Signed, short-lived media URLs for casting natively (full flavour's Google Cast button). */
    @POST("cast/sign")
    suspend fun castSign(@Body body: CastSignRequest): CastSignResponse
    // ---- v5: collaborative playlists ----

    @POST("playlists/{id}/tracks")
    suspend fun addToPlaylistAfter(@Path("id") id: String, @Body body: AddEntriesBody): Playlist

    @DELETE("playlists/{id}/entries/{entryId}")
    suspend fun removePlaylistEntry(@Path("id") id: String, @Path("entryId") entryId: String): Playlist

    @POST("playlists/{id}/entries/{entryId}/move")
    suspend fun movePlaylistEntry(@Path("id") id: String, @Path("entryId") entryId: String, @Body body: MoveEntryBody): Playlist

    @GET("playlists/{id}/members")
    suspend fun playlistMembers(@Path("id") id: String): List<PlaylistMember>

    @POST("playlists/{id}/members")
    suspend fun addPlaylistMember(@Path("id") id: String, @Body body: NewMemberBody): List<PlaylistMember>

    @PATCH("playlists/{id}/members/{userId}")
    suspend fun setPlaylistMemberRole(@Path("id") id: String, @Path("userId") userId: String, @Body body: RoleBody): List<PlaylistMember>

    @DELETE("playlists/{id}/members/{userId}")
    suspend fun removePlaylistMember(@Path("id") id: String, @Path("userId") userId: String): Response<Unit>

    @GET("playlists/{id}/activity")
    suspend fun playlistActivity(@Path("id") id: String, @Query("limit") limit: Int = 50): List<PlaylistActivity>

    @GET("users")
    suspend fun userDirectory(): List<UserRef>

    // ---- v5: admin tag editing ----

    @GET("admin/tags/tracks/{id}")
    suspend fun trackTags(@Path("id") id: String): TrackTagDetail

    @POST("admin/tags/edit")
    suspend fun editTags(@Body body: TagEditBody): TagEditResult

    @GET("admin/tags/musicbrainz")
    suspend fun tagSuggestions(@Query("trackId") trackId: String): TagSuggestions

    @GET("queues")
    suspend fun queues(): List<Queue>

    @POST("queues")
    suspend fun createQueue(@Body body: NewQueue): Queue

    @GET("queues/{id}")
    suspend fun queue(@Path("id") id: String): Queue

    @PATCH("queues/{id}")
    suspend fun patchQueue(@Path("id") id: String, @Body body: QueuePatch): Response<Unit>

    @DELETE("queues/{id}")
    suspend fun deleteQueue(@Path("id") id: String): Response<Unit>

    /** position: an index, "next" or "end". */
    @POST("queues/{id}/tracks")
    suspend fun addToQueue(@Path("id") id: String, @Body body: TrackIdsBody): Response<Unit>

    @DELETE("queues/{id}/tracks/{position}")
    suspend fun removeFromQueue(@Path("id") id: String, @Path("position") position: Int): Response<Unit>

    @POST("queues/{id}/move")
    suspend fun moveQueueTrack(@Path("id") id: String, @Body body: MoveBody): Response<Unit>

    @POST("queues/{id}/save-as-playlist")
    suspend fun saveQueueAsPlaylist(@Path("id") id: String, @Body body: NameBody): Playlist

    @PUT("tracks/{id}/lyrics")
    suspend fun putLyrics(@Path("id") id: String, @Body body: LyricsEdit): Response<Unit>

    @DELETE("tracks/{id}/lyrics")
    suspend fun deleteLyrics(@Path("id") id: String): Response<Unit>

    @GET("admin/invites")
    suspend fun invites(): List<Invite>

    @POST("admin/invites")
    suspend fun createInvite(@Body body: NewInvite): Invite

    @DELETE("admin/invites/{code}")
    suspend fun deleteInvite(@Path("code") code: String): Response<Unit>

    @GET("admin/users")
    suspend fun users(): List<AdminUser>

    @PATCH("admin/users/{id}")
    suspend fun patchUser(@Path("id") id: String, @Body body: AdminUserPatch): Response<Unit>

    @POST("admin/users/{id}/reset-password")
    suspend fun resetPassword(@Path("id") id: String, @Body body: NewPassword): Response<Unit>
    // ---- v3 (docs/v3-ux.md) ----
    // Feeds come back as raw JSON and go through LenientJson so one bad shelf/item can't break the screen.
    @GET("home")
    suspend fun homeRaw(): kotlinx.serialization.json.JsonElement

    @GET("foryou")
    suspend fun forYouRaw(): kotlinx.serialization.json.JsonElement

    @GET("search")
    suspend fun globalSearchRaw(@Query("q") q: String, @Query("scope") scope: String = "all", @Query("limit") limit: Int = 20): kotlinx.serialization.json.JsonElement

    /** status: want (default) | got | dismissed | all */
    @GET("toget")
    suspend fun toGet(@Query("status") status: String = "want"): List<ToGetItem>

    @POST("toget")
    suspend fun addToGet(@Body body: NewToGet): ToGetItem

    @PATCH("toget/{id}")
    suspend fun patchToGet(@Path("id") id: String, @Body body: ToGetPatch): ToGetItem

    @DELETE("toget/{id}")
    suspend fun deleteToGet(@Path("id") id: String): Response<Unit>

    @GET("pins")
    suspend fun pinsRaw(): kotlinx.serialization.json.JsonElement

    /** Returns all pins after the change. */
    @POST("pins")
    suspend fun pin(@Body body: NewPin): kotlinx.serialization.json.JsonElement

    @DELETE("pins/{type}/{id}")
    suspend fun unpin(@Path("type") type: String, @Path("id") id: String): Response<Unit>

    /** In-app bug report (docs: Settings → Send bug report). */
    @POST("me/bug-report")
    suspend fun bugReport(@Body body: org.nasmusic.app.diag.BugReportBody): org.nasmusic.app.diag.BugReportResponse

    /** AutoEq headphone profiles, proxied and cached by the server (api 0.5). */
    @GET("eq/autoeq")
    suspend fun autoEqSearch(@Query("q") q: String, @Query("limit") limit: Int = 40): AutoEqSearch

    @GET("eq/autoeq/profile")
    suspend fun autoEqProfile(@Query("id") id: String): org.nasmusic.app.eq.HeadphoneProfile

    @POST("radio")
    suspend fun startRadio(@Body body: RadioRequest): Queue

    /** Returns the whole queue with the new tracks appended. */
    @POST("radio/{queueId}/more")
    suspend fun radioMore(@Path("queueId") queueId: String): kotlinx.serialization.json.JsonObject

    // ---- AI radio (api 0.6): describe-a-vibe, station, steering ----
    @POST("radio/vibe/parse")
    suspend fun parseVibe(@Body body: VibeParseRequest): VibeParse

    @GET("radio/{queueId}/station")
    suspend fun radioStation(@Path("queueId") queueId: String): RadioStation

    @POST("radio/{queueId}/steer")
    suspend fun steerRadio(@Path("queueId") queueId: String, @Body body: SteerRequest): SteerResponse
}

interface DiscoveryApi {
    @GET("release-radar")
    suspend fun releaseRadarRaw(): kotlinx.serialization.json.JsonObject

    @GET("wrapped")
    suspend fun wrapped(@Query("year") year: Int? = null): WrappedStats

    @GET("wrapped/years")
    suspend fun wrappedYears(): List<Int>

    @GET("admin/wrapped")
    suspend fun adminWrapped(@Query("year") year: Int? = null): WrappedStats

    @GET("blends")
    suspend fun blends(): List<Blend>

    @GET("blends/candidates")
    suspend fun blendCandidates(): List<BlendCandidate>

    @POST("blends")
    suspend fun createBlend(@Body body: NewBlend): Blend

    @GET("blends/{id}")
    suspend fun blend(@Path("id") id: String): Blend

    @PATCH("blends/{id}")
    suspend fun renameBlend(@Path("id") id: String, @Body body: BlendName): Blend

    @POST("blends/{id}/invite")
    suspend fun inviteToBlend(@Path("id") id: String, @Body body: BlendInvite): Blend

    @POST("blends/{id}/accept")
    suspend fun acceptBlend(@Path("id") id: String): Blend

    @POST("blends/{id}/decline")
    suspend fun declineBlend(@Path("id") id: String): Response<Unit>

    @POST("blends/{id}/leave")
    suspend fun leaveBlend(@Path("id") id: String): Response<Unit>

    @POST("blends/{id}/refresh")
    suspend fun refreshBlend(@Path("id") id: String): Blend
}

/** Release Radar; items parsed leniently like any feed. */
suspend fun DiscoveryApi.releaseRadar(): ReleaseRadar {
    val o = releaseRadarRaw()
    fun items(k: String) = (o[k] as? kotlinx.serialization.json.JsonArray)?.mapNotNull { LenientJson.item(it) }.orEmpty()
    val updated = (o["updatedAt"] as? kotlinx.serialization.json.JsonPrimitive)?.takeIf { it !is kotlinx.serialization.json.JsonNull }?.content
    return ReleaseRadar(updated, items("inLibrary"), items("notOwned"))
}

suspend fun NasMusicApi.home(): ShelfFeed = LenientJson.feed(homeRaw())
suspend fun NasMusicApi.forYou(): ShelfFeed = LenientJson.feed(forYouRaw())
suspend fun NasMusicApi.globalSearch(q: String, scope: String = "all", limit: Int = 20): SearchResults = LenientJson.search(globalSearchRaw(q, scope, limit))
suspend fun NasMusicApi.pins(): List<ShelfItem> = (pinsRaw() as? kotlinx.serialization.json.JsonArray)?.mapNotNull { LenientJson.item(it) }.orEmpty()
