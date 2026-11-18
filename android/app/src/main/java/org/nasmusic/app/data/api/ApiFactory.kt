package org.nasmusic.app.data.api

import kotlinx.serialization.json.Json
import okhttp3.Call
import okhttp3.MediaType.Companion.toMediaType
import org.nasmusic.app.net.RoutedCallFactory
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

object ApiFactory {
    val json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        coerceInputValues = true
    }

    fun create(callFactory: Call.Factory, baseUrl: String = RoutedCallFactory.VIRTUAL_BASE): NasMusicApi =
        Retrofit.Builder()
            .baseUrl(baseUrl)
            .callFactory(callFactory)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(NasMusicApi::class.java)
}
