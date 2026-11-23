package org.nasmusic.app.data.api

import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * Parsing for server feeds where one malformed element must not take down the screen: every shelf
 * and item is decoded on its own, bad or unknown ones are dropped (contract: "Unknown kinds/types
 * must be skipped by clients").
 */
object LenientJson {
    val SHELF_KINDS = setOf(
        "start_radio", "devices", "recently_played", "continue_listening", "pinned", "mixes", "radio",
        "because_you_played", "release_radar", "suggestions", "to_get", "blends", "wrapped",
    )
    val ITEM_TYPES = setOf(
        "track", "album", "artist", "playlist", "mix", "radio", "queue", "device", "catalog", "suggestion",
        "toget", "smart_list", "smart_playlist", "genre", "folder", "blend", "wrapped",
        // api 0.8 Continue listening
        "episode", "book",
    )

    fun <T> list(e: JsonElement?, ser: KSerializer<T>, json: Json = ApiFactory.json): List<T> =
        (e as? JsonArray)?.mapNotNull { runCatching { json.decodeFromJsonElement(ser, it) }.getOrNull() }.orEmpty()

    private fun JsonObject.str(k: String): String? = (this[k] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.contentOrNull

    fun item(e: JsonElement, json: Json = ApiFactory.json): ShelfItem? {
        val o = e as? JsonObject ?: return null
        val type = o.str("type") ?: return null
        if (type !in ITEM_TYPES) return null
        val id = o.str("id")?.takeIf { it.isNotBlank() } ?: return null
        return runCatching { json.decodeFromJsonElement(ShelfItem.serializer(), o) }.getOrElse {
            // A nested object didn't match (e.g. a partial track): keep the card with what is safe.
            ShelfItem(type, id, o.str("title").orEmpty(), o.str("subtitle"), artUrl = o.str("artUrl"))
        }
    }

    fun shelf(e: JsonElement, json: Json = ApiFactory.json): Shelf? {
        val o = e as? JsonObject ?: return null
        val kind = o.str("kind") ?: o.str("id") ?: return null
        if (kind !in SHELF_KINDS) return null
        val items = (o["items"] as? JsonArray)?.mapNotNull { item(it, json) }.orEmpty()
        return Shelf(
            id = o.str("id") ?: kind,
            kind = kind,
            title = o.str("title").orEmpty(),
            subtitle = o.str("subtitle"),
            layout = o.str("layout"),
            items = items,
        )
    }

    /** `{shelves:[…]}` (contract) or a bare array. */
    fun feed(e: JsonElement?, json: Json = ApiFactory.json): ShelfFeed {
        val arr = (e as? JsonObject)?.get("shelves") as? JsonArray ?: e as? JsonArray ?: return ShelfFeed()
        return ShelfFeed(arr.mapNotNull { shelf(it, json) })
    }

    fun search(e: JsonElement?, json: Json = ApiFactory.json): SearchResults {
        val o = e as? JsonObject ?: return SearchResults()
        return SearchResults(
            query = o.str("query").orEmpty(),
            top = o["top"]?.let { item(it, json) },
            tracks = list(o["tracks"], Track.serializer(), json),
            albums = list(o["albums"], Album.serializer(), json),
            artists = list(o["artists"], Artist.serializer(), json),
            playlists = list(o["playlists"], Playlist.serializer(), json),
            queues = list(o["queues"], Queue.serializer(), json),
            catalog = list(o["catalog"], CatalogItem.serializer(), json),
            catalogError = o.str("catalogError"),
        )
    }
}
