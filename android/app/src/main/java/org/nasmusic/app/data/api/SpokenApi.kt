package org.nasmusic.app.data.api

import kotlinx.serialization.Serializable
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query

// api/openapi.yaml 0.7.0, tag "spoken": podcasts, audiobooks, internet radio. Not music: nothing here is
// recorded as a listening event, scrobbled or added to queues.

@Serializable
data class SpokenProgress(
    val kind: String,
    val id: String,
    val positionS: Double = 0.0,
    val durationS: Double? = null,
    val played: Boolean = false,
    val speed: Double? = null,
    val updatedAt: String = "",
)

@Serializable
data class Podcast(
    val id: String,
    val feedUrl: String = "",
    val title: String,
    val author: String? = null,
    val description: String? = null,
    val summary: String? = null,
    val explicit: Boolean = false,
    val hasImage: Boolean = false,
    val lastError: String? = null,
    val serverKeepLast: Int? = null,
    val subscribed: Boolean = false,
    val speed: Double = 1.0,
    val skipSilence: Boolean = false,
    val autoDownload: Boolean = false,
    val unplayed: Int? = null,
    val latestAt: String? = null,
)

@Serializable
data class PodcastList(val podcasts: List<Podcast> = emptyList())

@Serializable
data class PodcastSearchResult(
    val id: String,
    val title: String,
    val author: String? = null,
    val feedUrl: String,
    val imageUrl: String? = null,
    val explicit: Boolean = false,
    val genre: String? = null,
    val source: String = "",
    val subscribed: Boolean = false,
)

@Serializable
data class PodcastSearch(val results: List<PodcastSearchResult> = emptyList(), val errors: List<String> = emptyList())

@Serializable
data class SubscribeBody(val feedUrl: String)

@Serializable
data class PodcastPatch(val speed: Double? = null, val skipSilence: Boolean? = null, val autoDownload: Boolean? = null, val serverKeepLast: Int? = null)

@Serializable
data class SpokenChapter(val start: Double, val title: String = "", val url: String? = null)

@Serializable
data class Episode(
    val id: String,
    val podcastId: String,
    val podcastTitle: String? = null,
    val title: String,
    val summary: String? = null,
    val publishedAt: String? = null,
    val durationS: Double? = null,
    val explicit: Boolean = false,
    val contentType: String? = null,
    val sizeBytes: Long? = null,
    val onServer: Boolean = false,
    val hasChapters: Boolean = false,
    val progress: SpokenProgress? = null,
    // detail only
    val description: String? = null,
    val chapters: List<SpokenChapter> = emptyList(),
    val speed: Double = 1.0,
    val skipSilence: Boolean = false,
)

@Serializable
data class EpisodeList(val episodes: List<Episode> = emptyList())

@Serializable
data class Audiobook(
    val id: String,
    val title: String,
    val author: String? = null,
    val narrator: String? = null,
    val year: Int? = null,
    val durationS: Double = 0.0,
    val summary: String? = null,
    val progress: SpokenProgress? = null,
    // detail only
    val description: String? = null,
    val parts: List<BookPart> = emptyList(),
    val chapters: List<BookChapter> = emptyList(),
    val bookmarks: List<Bookmark> = emptyList(),
)

@Serializable
data class BookPart(val index: Int, val durationS: Double = 0.0, val startS: Double = 0.0, val contentType: String? = null, val name: String = "")

@Serializable
data class BookChapter(val index: Int, val title: String, val startS: Double)

@Serializable
data class Bookmark(val id: String, val positionS: Double, val note: String? = null, val createdAt: String = "")

@Serializable
data class BookmarkBody(val positionS: Double, val note: String? = null)

@Serializable
data class AudiobookList(val enabled: Boolean = false, val books: List<Audiobook> = emptyList())

@Serializable
data class Station(
    val id: String,
    val name: String,
    val streamUrl: String = "",
    val homepage: String? = null,
    val country: String? = null,
    val tags: List<String> = emptyList(),
    val codec: String? = null,
    val bitrate: Int? = null,
    val explicit: Boolean = false,
    val hasImage: Boolean = false,
    val favourite: Boolean = false,
)

@Serializable
data class StationList(val stations: List<Station> = emptyList())

@Serializable
data class StationSearchResult(
    val id: String,
    val name: String,
    val streamUrl: String,
    val homepage: String? = null,
    val imageUrl: String? = null,
    val country: String? = null,
    val tags: List<String> = emptyList(),
    val codec: String? = null,
    val bitrate: Int? = null,
    val explicit: Boolean = false,
    val radiobrowserUuid: String? = null,
    val favourite: Boolean = false,
)

@Serializable
data class StationSearch(val results: List<StationSearchResult> = emptyList())

@Serializable
data class StationBody(
    val streamUrl: String,
    val name: String? = null,
    val homepage: String? = null,
    val imageUrl: String? = null,
    val country: String? = null,
    val tags: List<String>? = null,
    val codec: String? = null,
    val bitrate: Int? = null,
    val explicit: Boolean? = null,
    val radiobrowserUuid: String? = null,
    val probe: Boolean = true,
)

@Serializable
data class NowPlaying(val stationId: String, val title: String? = null)

@Serializable
data class ProgressWrite(
    val kind: String,
    val id: String,
    val positionS: Double? = null,
    val durationS: Double? = null,
    val played: Boolean? = null,
    val speed: Double? = null,
    val updatedAt: String? = null,
)

@Serializable
data class ProgressBatch(val items: List<ProgressWrite>)

@Serializable
data class ProgressResult(val kind: String, val id: String, val applied: Boolean = false, val progress: SpokenProgress? = null)

@Serializable
data class ProgressResults(val items: List<ProgressResult> = emptyList())

@Serializable
data class ProgressList(val items: List<SpokenProgress> = emptyList())

@Serializable
data class ContinueItem(val kind: String, val episode: Episode? = null, val book: Audiobook? = null, val progress: SpokenProgress)

/** POST /cast/sign-spoken (Google Cast from the phone): signed URLs for an episode, a book's parts or a station. */
@Serializable
data class SpokenSignBody(val kind: String, val id: String, val part: Int? = null)

@Serializable
data class SpokenSigned(
    val url: String,
    val artUrl: String? = null,
    val contentType: String = "audio/mpeg",
    val title: String = "",
    val subtitle: String? = null,
    val durationS: Double? = null,
    val startS: Double? = null,
    val part: Int? = null,
    val live: Boolean = false,
)

@Serializable
data class SpokenSignList(val items: List<SpokenSigned> = emptyList())

@Serializable
data class ContinueList(val items: List<ContinueItem> = emptyList())

interface SpokenApi {
    @GET("podcasts") suspend fun podcasts(): PodcastList
    @POST("podcasts") suspend fun subscribe(@Body body: SubscribeBody): Podcast
    @GET("podcasts/search") suspend fun searchPodcasts(@Query("q") q: String): PodcastSearch
    @GET("podcasts/episodes") suspend fun inbox(@Query("filter") filter: String = "unplayed", @Query("limit") limit: Int = 50): EpisodeList
    @GET("podcasts/episodes/{id}") suspend fun episode(@Path("id") id: String): Episode
    @GET("podcasts/{id}") suspend fun podcast(@Path("id") id: String): Podcast
    @PATCH("podcasts/{id}") suspend fun patchPodcast(@Path("id") id: String, @Body body: PodcastPatch): Podcast
    @DELETE("podcasts/{id}") suspend fun unsubscribe(@Path("id") id: String): Response<Unit>
    @GET("podcasts/{id}/episodes") suspend fun episodes(
        @Path("id") id: String, @Query("filter") filter: String = "all", @Query("limit") limit: Int = 300,
    ): EpisodeList
    @POST("podcasts/{id}/refresh") suspend fun refresh(@Path("id") id: String): kotlinx.serialization.json.JsonObject
    @POST("podcasts/{id}/played") suspend fun markShowPlayed(@Path("id") id: String): kotlinx.serialization.json.JsonObject

    @GET("audiobooks") suspend fun books(@Query("sort") sort: String = "recent"): AudiobookList
    @GET("audiobooks/{id}") suspend fun book(@Path("id") id: String): Audiobook
    @POST("audiobooks/{id}/bookmarks") suspend fun addBookmark(@Path("id") id: String, @Body body: BookmarkBody): Bookmark
    @DELETE("audiobooks/{id}/bookmarks/{bid}") suspend fun deleteBookmark(@Path("id") id: String, @Path("bid") bid: String): Response<Unit>

    @GET("stations") suspend fun stations(): StationList
    @GET("stations/search") suspend fun searchStations(@Query("q") q: String): StationSearch
    @POST("stations") suspend fun addStation(@Body body: StationBody): Station
    @DELETE("stations/{id}") suspend fun removeStation(@Path("id") id: String): Response<Unit>
    @GET("stations/{id}/now-playing") suspend fun nowPlaying(@Path("id") id: String): NowPlaying

    @GET("spoken/progress") suspend fun progress(@Query("since") since: String? = null): ProgressList
    @PUT("spoken/progress") suspend fun putProgress(@Body body: ProgressBatch): ProgressResults
    @GET("spoken/continue") suspend fun continueListening(): ContinueList
    @POST("cast/sign-spoken") suspend fun signSpoken(@Body body: SpokenSignBody): SpokenSignList
}
