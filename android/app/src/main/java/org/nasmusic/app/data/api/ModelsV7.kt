package org.nasmusic.app.data.api

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive

// ---------- api 0.7 (schema v7): smart playlists ----------

/**
 * One node of a smart playlist's rules: a rule (`field` + `op` + `value` [+ `unit`]) or a group (`match` +
 * `rules`). Nulls are never sent (ApiFactory.json has explicitNulls = false), so a rule has no `rules` key and
 * the server can tell the two apart.
 */
@Serializable
data class SmartNode(
    val field: String? = null,
    val op: String? = null,
    val value: JsonElement? = null,
    val unit: String? = null,
    val match: String? = null,
    val rules: List<SmartNode>? = null,
) {
    val isGroup get() = rules != null
    val num: Double? get() = (value as? JsonPrimitive)?.takeIf { !it.isString }?.doubleOrNull
    val pair: List<Double>? get() = runCatching { value?.jsonArray?.map { it.jsonPrimitive.doubleOrNull ?: Double.NaN } }.getOrNull()
    val text: String? get() = (value as? JsonPrimitive)?.takeIf { it.isString }?.content
    val bool: Boolean? get() = (value as? JsonPrimitive)?.content?.toBooleanStrictOrNull()
}

@Serializable
data class SmartLimit(val type: String = "none", val value: Int? = null)

@Serializable
data class SmartDefinition(
    val match: String = "all",
    val rules: List<SmartNode> = emptyList(),
    val sort: String = "random",
    val limit: SmartLimit = SmartLimit(),
)

@Serializable
data class SmartPlaylist(
    val id: String,
    val name: String,
    val definition: SmartDefinition = SmartDefinition(),
    val preset: String? = null,
    val refreshedAt: String? = null,
    val trackCount: Int = 0,
    val durationS: Double = 0.0,
    val rev: Int = 0,
    val tracks: List<Track> = emptyList(),
)

@Serializable
data class SmartFieldSpec(
    val id: String,
    val label: String,
    /** number | percent | text | date | bool | enum | playlist */
    val type: String,
    val ops: List<String>,
    val options: List<String> = emptyList(),
    val suggest: String? = null,
    val unit: String? = null,
)

@Serializable
data class SmartSort(val id: String, val label: String)

@Serializable
data class SmartPreset(val id: String, val name: String, val description: String? = null, val definition: SmartDefinition)

@Serializable
data class SmartFields(
    val fields: List<SmartFieldSpec> = emptyList(),
    val sorts: List<SmartSort> = emptyList(),
    val units: List<String> = listOf("days", "weeks", "months", "years"),
    val presets: List<SmartPreset> = emptyList(),
)

@Serializable
data class NewSmartPlaylist(val name: String, val definition: SmartDefinition, val preset: String? = null)

@Serializable
data class SmartPatch(val name: String? = null, val definition: SmartDefinition? = null)

@Serializable
data class SmartPreviewRequest(val definition: SmartDefinition, val smartPlaylistId: String? = null, val sample: Int = 10)

@Serializable
data class SmartPreview(val trackCount: Int = 0, val durationS: Double = 0.0, val tracks: List<Track> = emptyList())

// ---------- api 0.7: artist pages ----------

@Serializable
data class ArtistLink(val kind: String, val url: String)

@Serializable
data class YearsActive(val from: Int? = null, val to: Int? = null)

@Serializable
data class ArtistBio(val text: String, val source: String = "Wikipedia", val url: String = "", val license: String = "", val licenseUrl: String = "")

@Serializable
data class ArtistImageCredit(
    val source: String = "Wikimedia Commons",
    val author: String? = null,
    val license: String? = null,
    val licenseUrl: String? = null,
    val filePage: String? = null,
)

@Serializable
data class ArtistInfo(
    /** ok | not_found | error | pending | none */
    val status: String = "none",
    val type: String? = null,
    val country: String? = null,
    val area: String? = null,
    val yearsActive: YearsActive? = null,
    val genres: List<String> = emptyList(),
    val links: List<ArtistLink> = emptyList(),
    val bio: ArtistBio? = null,
    val image: ArtistImageCredit? = null,
    val photoUrl: String? = null,
)

@Serializable
data class ArtistStats(val trackCount: Int = 0, val durationS: Double = 0.0, val playCount: Int = 0, val albumCount: Int = 0, val singleCount: Int = 0)

@Serializable
data class SimilarArtist(val id: String, val name: String, val albumCount: Int = 0, val similarity: Double? = null, val artUrl: String? = null)

@Serializable
data class ArtistPage(
    val id: String,
    val name: String,
    val mbid: String? = null,
    val artUrl: String? = null,
    val photoUrl: String? = null,
    val info: ArtistInfo = ArtistInfo(),
    val stats: ArtistStats = ArtistStats(),
    val topTracks: List<Track> = emptyList(),
    val albums: List<Album> = emptyList(),
    val singles: List<Album> = emptyList(),
    val appearsOn: List<Album> = emptyList(),
    val similar: List<SimilarArtist> = emptyList(),
    val radioSeed: RadioSeed? = null,
)
