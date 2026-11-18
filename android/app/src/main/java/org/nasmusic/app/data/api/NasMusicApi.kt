package org.nasmusic.app.data.api

import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
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

    @GET("sync/manifest")
    suspend fun manifest(@Query("favouritesFormat") favouritesFormat: String): SyncManifest

    @POST("events")
    suspend fun events(@Body batch: EventBatch): EventsResponse

    @GET("suggestions")
    suspend fun suggestions(): List<Suggestion>

    @POST("suggestions/{id}")
    suspend fun setSuggestionStatus(@Path("id") id: String, @Body body: SuggestionStatus): Response<Unit>
}
