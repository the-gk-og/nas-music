package org.homebeat.app.data.prefs

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

/**
 * Maps Appearance settings to the synced `/me/settings` keys (contract): `appearance.animations`,
 * `appearance.defaultView`, `appearance.albumPage`, `appearance.accent` (name or "#rrggbb"),
 * `home.shelves` (order) and `home.hidden`.
 */
object AppearanceSync {
    val ACCENT_NAMES: Map<String, Long> = linkedMapOf(
        "green" to 0xFF4ADE80, "blue" to 0xFF60A5FA, "purple" to 0xFFA78BFA, "pink" to 0xFFF472B6,
        "orange" to 0xFFFBBF24, "red" to 0xFFF87171, "teal" to 0xFF2DD4BF,
    )

    fun accentName(argb: Long): String =
        ACCENT_NAMES.entries.firstOrNull { it.value == argb }?.key ?: "#%06x".format(argb and 0xFFFFFF)

    fun accentFrom(value: String): Long? = ACCENT_NAMES[value.lowercase()]
        ?: value.removePrefix("#").takeIf { it.length == 6 }?.toLongOrNull(16)?.let { 0xFF000000 or it }

    fun toRemote(s: AppSettings): Map<String, JsonElement> = mapOf(
        "appearance.animations" to JsonPrimitive(s.animations),
        "appearance.defaultView" to JsonPrimitive(s.defaultView),
        "appearance.albumPage" to JsonPrimitive(s.pageStyle),
        "appearance.accent" to JsonPrimitive(accentName(s.accent)),
        "home.shelves" to JsonArray(s.homeShelves.map { JsonPrimitive(it.removePrefix("-")) }),
        "home.hidden" to JsonArray(s.homeShelves.filter { it.startsWith("-") }.map { JsonPrimitive(it.removePrefix("-")) }),
    )

    /** Remote values applied over [cur]; keys that are missing or invalid keep the local value. */
    fun fromRemote(remote: Map<String, JsonElement>, cur: AppSettings): AppSettings {
        fun str(k: String) = (remote[k] as? JsonPrimitive)?.contentOrNull
        fun list(k: String) = (remote[k] as? JsonArray)?.mapNotNull { runCatching { it.jsonPrimitive.content }.getOrNull() }
        val order = list("home.shelves")
        val hidden = list("home.hidden").orEmpty().toSet()
        return cur.copy(
            animations = str("appearance.animations")?.takeIf { it in setOf("off", "quick", "expressive") } ?: cur.animations,
            defaultView = str("appearance.defaultView")?.takeIf { it == "grid" || it == "list" } ?: cur.defaultView,
            pageStyle = str("appearance.albumPage")?.takeIf { it == "big" || it == "simple" } ?: cur.pageStyle,
            accent = str("appearance.accent")?.let(::accentFrom) ?: cur.accent,
            homeShelves = order?.map { if (it in hidden) "-$it" else it } ?: cur.homeShelves,
        )
    }
}
