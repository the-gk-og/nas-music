package org.nasmusic.app.upload

import kotlinx.serialization.Serializable
import okhttp3.Call
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody
import retrofit2.Response
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.Path

// Uploads (api/openapi.yaml 0.7.0, schema v7): resumable chunk + offset uploads into the server's upload folder.

@Serializable
data class UploadedFile(
    val id: String,
    val userId: String? = null,
    val username: String? = null,
    val path: String,
    val trackId: String,
    val bytes: Long,
    val originalName: String = "",
    val format: String = "",
    val visibility: String = "everyone",
    val createdAt: String? = null,
    val indexed: Boolean = false,
    val title: String? = null,
    val artist: String? = null,
    val album: String? = null,
    val durationS: Double? = null,
)

@Serializable
data class UploadSession(
    val id: String,
    val filename: String,
    val bytes: Long,
    val offset: Long,
    val visibility: String = "everyone",
    val status: String,              // uploading | done | failed
    val error: String? = null,
    val upload: UploadedFile? = null,
)

@Serializable
data class UploadOverview(
    val canUpload: Boolean,
    val enabled: Boolean,
    val quotaBytes: Long? = null,
    val usedBytes: Long = 0,
    val maxFileBytes: Long = 0,
    val chunkBytes: Int = 4 * 1024 * 1024,
    val maxChunkBytes: Int = 16 * 1024 * 1024,
    val formats: List<String> = emptyList(),
    val files: List<UploadedFile> = emptyList(),
    val sessions: List<UploadSession> = emptyList(),
)

@Serializable
data class CreateUpload(val filename: String, val bytes: Long, val visibility: String)

@Serializable
data class VisibilityBody(val visibility: String)

@Serializable
data class OffsetError(val detail: String? = null, val offset: Long? = null)

interface UploadsApi {
    @GET("uploads")
    suspend fun overview(): UploadOverview

    @POST("uploads")
    suspend fun create(@Body body: CreateUpload): UploadSession

    @GET("uploads/{id}")
    suspend fun status(@Path("id") id: String): UploadSession

    /** Raw chunk at `Upload-Offset`; 409 carries the server's offset (see [OffsetError]). */
    @PATCH("uploads/{id}")
    suspend fun append(@Path("id") id: String, @Header("Upload-Offset") offset: Long, @Body chunk: RequestBody): Response<UploadSession>

    @DELETE("uploads/{id}")
    suspend fun cancel(@Path("id") id: String): Response<Unit>

    @PATCH("uploads/files/{id}")
    suspend fun setVisibility(@Path("id") id: String, @Body body: VisibilityBody): UploadedFile

    @DELETE("uploads/files/{id}")
    suspend fun delete(@Path("id") id: String): Response<Unit>

    companion object {
        /** Built here rather than in ApiFactory (which the Wear module shares). */
        fun create(callFactory: Call.Factory, baseUrl: String = org.nasmusic.app.net.RoutedCallFactory.VIRTUAL_BASE): UploadsApi =
            Retrofit.Builder()
                .baseUrl(baseUrl)
                .callFactory(callFactory)
                .addConverterFactory(org.nasmusic.app.data.api.ApiFactory.json.asConverterFactory("application/json".toMediaType()))
                .build()
                .create(UploadsApi::class.java)
    }
}
