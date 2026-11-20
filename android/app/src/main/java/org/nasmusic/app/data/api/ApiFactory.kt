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
        // Send defaults like deviceKind="android"; nulls stay out (explicitNulls = false).
        encodeDefaults = true
    }

    fun create(callFactory: Call.Factory, baseUrl: String = RoutedCallFactory.VIRTUAL_BASE): NasMusicApi =
        retrofit(callFactory, baseUrl).create(NasMusicApi::class.java)

    /** v5: Release Radar, Wrapped, Blend. */
    fun discovery(callFactory: Call.Factory, baseUrl: String = RoutedCallFactory.VIRTUAL_BASE): DiscoveryApi =
        retrofit(callFactory, baseUrl).create(DiscoveryApi::class.java)

    /** Jam (listen together), host side. */
    fun jam(callFactory: Call.Factory, baseUrl: String = RoutedCallFactory.VIRTUAL_BASE): JamApi =
        retrofit(callFactory, baseUrl).create(JamApi::class.java)

    /** 0.7.0: podcasts, audiobooks, internet radio. */
    fun spoken(callFactory: Call.Factory, baseUrl: String = RoutedCallFactory.VIRTUAL_BASE): SpokenApi =
        retrofit(callFactory, baseUrl).create(SpokenApi::class.java)

    private fun retrofit(callFactory: Call.Factory, baseUrl: String): Retrofit =
        Retrofit.Builder()
            .baseUrl(baseUrl)
            .callFactory(callFactory)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
}
