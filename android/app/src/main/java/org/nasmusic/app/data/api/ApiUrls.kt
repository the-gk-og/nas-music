package org.nasmusic.app.data.api

import java.net.URLEncoder
import org.nasmusic.app.net.RoutedCallFactory

/** Virtual URLs (routed + authenticated by RoutedCallFactory) for endpoints loaded by Coil/ExoPlayer. */
object ApiUrls {
    private fun seg(s: String) = URLEncoder.encode(s, "UTF-8").replace("+", "%20")

    fun suggestionCover(id: String, size: Int = 256) = RoutedCallFactory.virtual("suggestions/${seg(id)}/cover?size=$size")
    fun suggestionPreview(id: String) = RoutedCallFactory.virtual("suggestions/${seg(id)}/preview")

    /** User-facing text for a failed POST /mixes/more-like, or null if it was not an HTTP error we know. */
    fun moreLikeError(httpCode: Int?): String = when (httpCode) {
        404 -> "This track hasn't been analysed yet — try again later."
        503 -> "The recommender is busy or offline right now — try again later."
        null -> "Couldn't reach the server."
        else -> "Couldn't build a mix right now (error $httpCode)."
    }
}
